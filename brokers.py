import os

import ccxt

from utils import log


# ---------------------------------------------------------------- tạo sàn


def make_exchange(cfg):
    futures = cfg["market"] == "futures"
    mode = cfg["mode"]
    params = {"enableRateLimit": True, "options": {"adjustForTimeDifference": True}}

    if mode == "testnet":
        params["apiKey"] = os.getenv("BINANCE_TESTNET_API_KEY")
        params["secret"] = os.getenv("BINANCE_TESTNET_API_SECRET")
    elif mode == "live":
        params["apiKey"] = os.getenv("BINANCE_API_KEY")
        params["secret"] = os.getenv("BINANCE_API_SECRET")
    if mode != "paper" and not params.get("apiKey"):
        raise SystemExit("Thiếu API key Binance trong .env cho mode này")

    ex = ccxt.binanceusdm(params) if futures else ccxt.binance(params)

    if mode == "testnet":
        try:
            ex.set_sandbox_mode(True)
        except Exception as e:  # noqa: BLE001
            # Binance đã đổi cơ chế testnet futures sang "demo trading" ở một số phiên bản ccxt
            if hasattr(ex, "enable_demo_trading"):
                log.warning("set_sandbox_mode lỗi (%s) -> thử enable_demo_trading", e)
                ex.enable_demo_trading(True)
            else:
                raise
    return ex


def setup_futures(ex, symbols, cfg):
    f = cfg["futures"]
    for s in symbols:
        try:
            ex.set_margin_mode(f["margin_mode"], s)
        except Exception as e:  # noqa: BLE001
            log.info("margin mode %s: %s (có thể đã đặt sẵn)", s, e)
        ex.set_leverage(f["leverage"], s)


# ---------------------------------------------------------------- base


class BaseBroker:
    def __init__(self, ex, cfg, state, symbols):
        self.ex, self.cfg, self.state, self.symbols = ex, cfg, state, symbols
        self.futures = cfg["market"] == "futures"
        self.lev = cfg["futures"]["leverage"] if self.futures else 1
        self.quote = cfg.get("quote", "USDT")

    def price(self, sym):
        return float(self.ex.fetch_ticker(sym)["last"])

    def to_amount(self, sym, x):
        try:
            return float(self.ex.amount_to_precision(sym, x))
        except Exception:  # noqa: BLE001
            return 0.0

    def limits(self, sym):
        m = self.ex.market(sym)["limits"]
        return (m.get("amount", {}).get("min") or 0.0, m.get("cost", {}).get("min") or 0.0)


# ---------------------------------------------------------------- paper


class PaperBroker(BaseBroker):
    """Giả lập: dùng giá thật của Binance, vốn ảo, không gửi lệnh."""

    def __init__(self, ex, cfg, state, symbols):
        super().__init__(ex, cfg, state, symbols)
        if state.data.get("paper") is None:
            state.data["paper"] = {"balance": float(cfg["paper_start_balance"]), "positions": {}}
            state.save()
        self.fee = cfg["risk"]["fee_pct"] / 100

    @property
    def p(self):
        return self.state.data["paper"]

    @staticmethod
    def _upnl(pos, px):
        sign = 1 if pos["side"] == "long" else -1
        return (px - pos["entry"]) * pos["amount"] * sign

    def equity(self):
        total = self.p["balance"]
        for sym, pos in self.p["positions"].items():
            total += self._upnl(pos, self.price(sym))
        return total

    def free(self):
        used = sum(x["entry"] * x["amount"] / self.lev for x in self.p["positions"].values())
        return max(self.p["balance"] - used, 0.0)

    def position(self, sym):
        pos = self.p["positions"].get(sym)
        return None if not pos else {k: pos[k] for k in ("side", "amount", "entry", "sl", "tp")}

    def open(self, sym, side, amount, sl, tp):
        px = self.price(sym)
        self.p["balance"] -= px * amount * self.fee
        self.p["positions"][sym] = {"side": side, "amount": amount, "entry": px, "sl": sl, "tp": tp}
        self.state.save()
        return f"[PAPER] mở {side} {sym} {amount} @ {px} SL={sl} TP={tp}"

    def close(self, sym, px=None, why="đóng tay"):
        pos = self.p["positions"].pop(sym, None)
        if not pos:
            return "không có vị thế"
        px = px or self.price(sym)
        pnl = self._upnl(pos, px) - px * pos["amount"] * self.fee
        self.p["balance"] += pnl
        self.state.save()
        return f"[PAPER] đóng {sym} ({why}) @ {px:.4f} PnL={pnl:+.2f} {self.quote}"

    def sync(self):
        events = []
        for sym, pos in list(self.p["positions"].items()):
            px = self.price(sym)
            long_ = pos["side"] == "long"
            if (long_ and px <= pos["sl"]) or (not long_ and px >= pos["sl"]):
                events.append(self.close(sym, pos["sl"], "chạm SL"))
            elif (long_ and px >= pos["tp"]) or (not long_ and px <= pos["tp"]):
                events.append(self.close(sym, pos["tp"], "chạm TP"))
        return events


# ---------------------------------------------------------------- sàn thật / testnet


class CcxtBroker(BaseBroker):
    def equity(self):
        bal = self.ex.fetch_balance()
        total = float(bal["total"].get(self.quote, 0) or 0)
        if not self.futures:  # spot: cộng giá trị coin whitelist đang giữ
            for s in self.symbols:
                amt = float(bal["total"].get(s.split("/")[0], 0) or 0)
                if amt:
                    total += amt * self.price(s)
        return total

    def free(self):
        return float(self.ex.fetch_balance()["free"].get(self.quote, 0) or 0)

    def position(self, sym):
        if self.futures:
            for p in self.ex.fetch_positions([sym]):
                c = float(p.get("contracts") or 0)
                if c > 0:
                    plan = self.state.plans.get(sym, {})
                    return {"side": p["side"], "amount": c, "entry": float(p["entryPrice"]),
                            "sl": plan.get("sl"), "tp": plan.get("tp")}
            return None
        plan = self.state.plans.get(sym)
        return dict(plan) if plan else None

    # ---- mở lệnh
    def open(self, sym, side, amount, sl, tp):
        ex = self.ex
        sl_p = float(ex.price_to_precision(sym, sl))
        tp_p = float(ex.price_to_precision(sym, tp))

        if self.futures:
            ex.create_order(sym, "market", "buy" if side == "long" else "sell", amount)
            pos = self.position(sym)
            amt = pos["amount"] if pos else amount
            entry = pos["entry"] if pos else self.price(sym)
            exit_side = "sell" if side == "long" else "buy"
            try:  # SL là bắt buộc: lỗi thì đóng vị thế ngay
                ex.create_order(sym, "market", exit_side, amt, None,
                                {"stopLossPrice": sl_p, "reduceOnly": True})
            except Exception as e:  # noqa: BLE001
                self.close(sym)
                raise RuntimeError(f"Đặt SL lỗi, đã đóng khẩn cấp vị thế: {e}") from e
            try:
                ex.create_order(sym, "market", exit_side, amt, None,
                                {"takeProfitPrice": tp_p, "reduceOnly": True})
            except Exception as e:  # noqa: BLE001
                log.warning("Đặt TP lỗi (vẫn còn SL): %s", e)
        else:
            order = ex.create_order(sym, "market", "buy", amount)
            base = sym.split("/")[0]
            free_base = float(ex.fetch_balance()["free"].get(base, 0) or 0)
            amt = float(ex.amount_to_precision(sym, min(free_base, amount)))
            entry = float(order.get("average") or self.price(sym))
            try:
                ex.create_order(sym, "STOP_LOSS_LIMIT", "sell", amt,
                                float(ex.price_to_precision(sym, sl * 0.995)),
                                {"stopPrice": sl_p, "timeInForce": "GTC"})
            except Exception as e:  # noqa: BLE001
                self.close(sym)
                raise RuntimeError(f"Đặt SL lỗi, đã bán khẩn cấp: {e}") from e

        # spot: TP do bot theo dõi (sàn không cho 2 lệnh bán cùng số dư, trừ OCO)
        self.state.plans[sym] = {"side": side, "amount": amt, "entry": entry, "sl": sl_p, "tp": tp_p}
        self.state.save()
        return f"[{self.cfg['mode'].upper()}] mở {side} {sym} {amt} @ ~{entry} SL={sl_p} TP={tp_p}"

    # ---- đóng lệnh
    def close(self, sym):
        ex = self.ex
        try:
            ex.cancel_all_orders(sym)
        except Exception as e:  # noqa: BLE001
            log.warning("cancel_all_orders: %s", e)
        if self.futures:
            pos = self.position(sym)
            if pos:
                ex.create_order(sym, "market", "sell" if pos["side"] == "long" else "buy",
                                pos["amount"], None, {"reduceOnly": True})
        else:
            base = sym.split("/")[0]
            free_base = float(ex.fetch_balance()["free"].get(base, 0) or 0)
            plan = self.state.plans.get(sym)
            amt = float(ex.amount_to_precision(sym, min(free_base, plan["amount"]) if plan else free_base))
            min_amt, min_cost = self.limits(sym)
            if amt > 0 and amt >= min_amt and amt * self.price(sym) >= min_cost:
                ex.create_order(sym, "market", "sell", amt)
        self.state.plans.pop(sym, None)
        self.state.save()
        return f"[{self.cfg['mode'].upper()}] đã đóng {sym}"

    # ---- đồng bộ SL/TP
    def sync(self):
        events = []
        if not self.state.plans:
            return events
        bal = None if self.futures else self.ex.fetch_balance()
        for sym, plan in list(self.state.plans.items()):
            if self.futures:
                if not self.position(sym):
                    try:
                        self.ex.cancel_all_orders(sym)  # dọn lệnh SL/TP còn lại
                    except Exception as e:  # noqa: BLE001
                        log.warning("dọn lệnh: %s", e)
                    self.state.plans.pop(sym, None)
                    events.append(f"{sym}: vị thế đã đóng (SL/TP khớp trên sàn)")
            else:
                base = sym.split("/")[0]
                have = float(bal["total"].get(base, 0) or 0)
                if have < plan["amount"] * 0.5:
                    self.state.plans.pop(sym, None)
                    events.append(f"{sym}: SL đã khớp trên sàn")
                elif self.price(sym) >= plan["tp"]:
                    events.append(self.close(sym) + " (chạm TP)")
        self.state.save()
        return events
