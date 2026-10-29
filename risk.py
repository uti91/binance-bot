class Risk:
    """Lớp kiểm soát rủi ro bằng code cứng. LLM chỉ đề xuất, lớp này quyết định cuối cùng."""

    def __init__(self, cfg):
        self.r = cfg["risk"]
        self.market = cfg["market"]
        self.f = cfg.get("futures", {})

    def allowed_actions(self, has_position):
        if has_position:
            return ["close", "hold"]
        acts = ["long", "hold"]
        if self.market == "futures" and self.f.get("allow_short", False):
            acts.append("short")
        return acts

    @staticmethod
    def _no(msg):
        return {"ok": False, "msg": msg}

    def evaluate(self, d, c):
        """
        d: quyết định của LLM
        c: ngữ cảnh {price, equity, free, position, open_count, trades_today,
                     daily_loss_hit, to_amount, min_amount, min_cost, leverage}
        """
        r = self.r
        a = d.get("action", "hold")

        if a == "hold":
            return self._no("hold")
        if a == "close":
            if c["position"]:
                return {"ok": True, "action": "close", "msg": "đóng vị thế"}
            return self._no("không có vị thế để đóng")
        if a not in ("long", "short"):
            return self._no(f"action lạ: {a}")

        if a == "short" and (self.market == "spot" or not self.f.get("allow_short", False)):
            return self._no("không cho phép short")
        if c["position"]:
            return self._no("đã có vị thế, không nhồi thêm")
        if c["daily_loss_hit"]:
            return self._no("đã chạm ngưỡng lỗ trong ngày")
        if c["trades_today"] >= r["max_trades_per_day"]:
            return self._no("hết lượt giao dịch trong ngày")
        if c["open_count"] >= r["max_open_positions"]:
            return self._no("đã đủ số vị thế tối đa")
        if float(d.get("confidence", 0)) < r["min_confidence"]:
            return self._no("độ tự tin thấp")

        try:
            sl, tp = float(d["stop_loss"]), float(d["take_profit"])
        except (KeyError, TypeError, ValueError):
            return self._no("thiếu stop_loss/take_profit")

        px = c["price"]
        ok_side = (sl < px < tp) if a == "long" else (tp < px < sl)
        if not ok_side:
            return self._no("SL/TP sai phía so với giá hiện tại")

        sl_pct = abs(px - sl) / px * 100
        if not (r["min_sl_pct"] <= sl_pct <= r["max_sl_pct"]):
            return self._no(f"SL cách giá {sl_pct:.2f}% ngoài khoảng cho phép")
        rr = abs(tp - px) / abs(px - sl)
        if rr < r["min_rr"]:
            return self._no(f"RR {rr:.2f} < {r['min_rr']}")

        lev = c["leverage"]
        if self.market == "futures" and sl_pct >= 60 / lev:
            return self._no("SL quá gần mức thanh lý với đòn bẩy này")

        # ---- tính khối lượng: rủi ro cố định theo % vốn
        qty = (c["equity"] * r["risk_per_trade_pct"] / 100) / abs(px - sl)
        qty = min(qty, c["equity"] * r["max_position_pct"] / 100 * lev / px)  # trần giá trị vị thế
        qty = min(qty, c["free"] * 0.95 * lev / px)  # không vượt số dư khả dụng
        qty = c["to_amount"](qty)
        if qty <= 0 or qty < c["min_amount"] or qty * px < c["min_cost"]:
            return self._no("khối lượng tính ra nhỏ hơn mức tối thiểu của sàn (vốn quá nhỏ)")

        return {
            "ok": True,
            "action": a,
            "amount": qty,
            "sl": sl,
            "tp": tp,
            "msg": f"OK size={qty} rủi ro~{qty * abs(px - sl):.2f} {''} RR={rr:.2f}",
        }
