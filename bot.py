import os
import sys
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from brokers import CcxtBroker, PaperBroker, make_exchange, setup_futures
from risk import Risk
from strategy import decide, fetch_news, snapshot
from utils import State, load_config, log, log_decision, notify, setup_logging

KILL_FILE = Path("STOP")  # tạo file tên STOP trong thư mục chạy bot để dừng mở lệnh mới


def map_symbols(cfg):
    q = cfg.get("quote", "USDT")
    if cfg["market"] == "futures":
        return [f"{s}:{q}" if ":" not in s else s for s in cfg["symbols"]]
    return list(cfg["symbols"])


def process_symbol(sym, cfg, ex, broker, risk, client, state, news, all_syms, daily_loss_hit):
    futures = cfg["market"] == "futures"
    px = broker.price(sym)
    pos = broker.position(sym)
    snap = snapshot(ex, sym, cfg["timeframe"], futures)
    allowed = risk.allowed_actions(bool(pos))

    d = decide(client, cfg, snap, pos, news, allowed)

    min_amt, min_cost = broker.limits(sym)
    open_count = sum(1 for s in all_syms if broker.position(s))
    ctx = {
        "price": px,
        "equity": broker.equity(),
        "free": broker.free(),
        "position": pos,
        "open_count": open_count,
        "trades_today": state.data["day"].get("trades", 0),
        "daily_loss_hit": daily_loss_hit,
        "to_amount": lambda x: broker.to_amount(sym, x),
        "min_amount": min_amt,
        "min_cost": min_cost,
        "leverage": broker.lev,
    }
    v = risk.evaluate(d, ctx)
    log_decision({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "symbol": sym, "price": px,
                  "llm": d, "risk": {k: v[k] for k in v if k != "to_amount"}})

    head = f"{sym} @ {px}: LLM={d.get('action')} ({d.get('confidence')}) | Risk: {v['msg']}"
    if not v["ok"]:
        log.info(head + f" | {d.get('reason', '')}")
        return

    try:
        if v["action"] == "close":
            msg = broker.close(sym)
        else:
            msg = broker.open(sym, v["action"], v["amount"], v["sl"], v["tp"])
            state.data["day"]["trades"] = state.data["day"].get("trades", 0) + 1
            state.save()
        notify(cfg, f"{msg}\nLý do: {d.get('reason', '')}")
    except Exception as e:  # noqa: BLE001
        notify(cfg, f"LỖI thực thi {sym}: {e}")


def main():
    load_dotenv()
    setup_logging()
    cfg = load_config()

    if cfg["mode"] == "live" and os.getenv("I_ACCEPT_LIVE_TRADING") != "YES":
        sys.exit("mode=live dùng TIỀN THẬT. Đặt biến môi trường I_ACCEPT_LIVE_TRADING=YES nếu bạn chắc chắn.")

    ex = make_exchange(cfg)
    ex.load_markets()
    symbols = map_symbols(cfg)
    state = State()
    broker = (PaperBroker if cfg["mode"] == "paper" else CcxtBroker)(ex, cfg, state, symbols)
    if cfg["market"] == "futures" and cfg["mode"] != "paper":
        setup_futures(ex, symbols, cfg)

    client = anthropic.Anthropic()  # đọc ANTHROPIC_API_KEY từ môi trường
    risk = Risk(cfg)
    log.info("Khởi động: market=%s mode=%s symbols=%s", cfg["market"], cfg["mode"], symbols)

    last_decision = 0.0
    loss_notified = False
    while True:
        try:
            for e in broker.sync():
                notify(cfg, e)

            equity = broker.equity()
            day = state.roll_day(equity)
            pnl_pct = (equity / day["start_equity"] - 1) * 100 if day["start_equity"] else 0
            loss_hit = pnl_pct <= -cfg["risk"]["max_daily_loss_pct"]
            if loss_hit and not loss_notified:
                notify(cfg, f"Chạm ngưỡng lỗ ngày ({pnl_pct:.2f}%). Chặn mở lệnh mới đến hết ngày UTC.")
                loss_notified = True
            if not loss_hit:
                loss_notified = False

            now = time.time()
            if KILL_FILE.exists():
                log.warning("Có file STOP: không ra quyết định mới.")
            elif now - last_decision >= cfg["decision_every_seconds"]:
                last_decision = now
                news = fetch_news(cfg)
                for sym in symbols:
                    try:
                        process_symbol(sym, cfg, ex, broker, risk, client, state, news, symbols, loss_hit)
                    except Exception as e:  # noqa: BLE001
                        log.exception("Lỗi xử lý %s: %s", sym, e)
                log.info("Equity=%.2f %s | PnL ngày=%.2f%%", equity, cfg.get("quote", "USDT"), pnl_pct)
        except KeyboardInterrupt:
            log.info("Dừng bot.")
            break
        except Exception as e:  # noqa: BLE001
            log.exception("Lỗi vòng lặp: %s", e)
        time.sleep(cfg["exit_check_every_seconds"])


if __name__ == "__main__":
    main()
