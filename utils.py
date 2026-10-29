import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

log = logging.getLogger("agent")


def load_config(path="config.yaml"):
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    assert cfg["market"] in ("spot", "futures"), "market phải là spot hoặc futures"
    assert cfg["mode"] in ("paper", "testnet", "live"), "mode phải là paper/testnet/live"
    return cfg


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("agent.log", encoding="utf-8")],
    )


class State:
    """Lưu trạng thái ra state.json để restart bot không mất kế hoạch SL/TP."""

    def __init__(self, path="state.json"):
        self.path = Path(path)
        self.data = {"plans": {}, "day": {}, "paper": None}
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    @property
    def plans(self):
        return self.data["plans"]

    def roll_day(self, equity):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.data["day"].get("date") != today:
            self.data["day"] = {"date": today, "start_equity": equity, "trades": 0}
            self.save()
        return self.data["day"]


def notify(cfg, text):
    log.info(text)
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if cfg.get("notify", {}).get("telegram") and token and chat:
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": text[:4000]},
                timeout=10,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("Telegram lỗi: %s", e)


def log_decision(entry):
    with open("decisions.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
