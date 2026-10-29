"""Test offline (không cần mạng/API key): chỉ báo, lớp risk, paper broker."""
import sys, types
for m in ("ccxt", "feedparser"):
    if m not in sys.modules:
        try: __import__(m)
        except ImportError: sys.modules[m] = types.ModuleType(m)
import numpy as np, pandas as pd
from utils import State, load_config
from risk import Risk
from brokers import PaperBroker
import strategy

class FakeEx:
    px = 100.0
    def fetch_ticker(self, s): return {"last": self.px}
    def amount_to_precision(self, s, x): return f"{int(x*1000)/1000}"
    def market(self, s): return {"limits": {"amount": {"min": 0.001}, "cost": {"min": 5}}}
    def fetch_ohlcv(self, s, tf, limit=260):
        r = np.random.default_rng(1); c = 100 + np.cumsum(r.normal(0, 1, limit))
        return [[i, c[i]-.2, c[i]+1, c[i]-1, c[i], 10] for i in range(limit)]

for market in ("spot", "futures"):
    cfg = load_config(); cfg["market"] = market
    ex = FakeEx(); st = State("/tmp/state_%s.json" % market); st.data["paper"] = None
    b = PaperBroker(ex, cfg, st, ["BTC/USDT"]); rk = Risk(cfg)
    snap = strategy.snapshot(ex, "BTC/USDT", "1h", False); assert snap["rsi14"] > 0
    ctx = dict(price=100, equity=b.equity(), free=b.free(), position=None, open_count=0,
               trades_today=0, daily_loss_hit=False, to_amount=lambda x: b.to_amount("BTC/USDT", x),
               min_amount=0.001, min_cost=5, leverage=b.lev)
    v = rk.evaluate({"action": "long", "confidence": .8, "stop_loss": 98, "take_profit": 104}, ctx)
    assert v["ok"], v
    print(market, "size:", v["amount"], v["msg"])
    assert not rk.evaluate({"action": "long", "confidence": .8, "stop_loss": 98, "take_profit": 101}, ctx)["ok"]  # RR thấp
    assert not rk.evaluate({"action": "long", "confidence": .8, "stop_loss": 102, "take_profit": 104}, ctx)["ok"]  # SL sai phía
    short = rk.evaluate({"action": "short", "confidence": .8, "stop_loss": 102, "take_profit": 96}, ctx)
    assert short["ok"] == (market == "futures"), short
    print(b.open("BTC/USDT", "long", v["amount"], 98, 104)); ex.px = 104.5
    for e in b.sync(): print(e)
    assert b.position("BTC/USDT") is None and b.equity() > 1000
print("TẤT CẢ TEST OK")
