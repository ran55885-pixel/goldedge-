"""Telegram alerts (optional). Token and chat id come from environment variables only."""
from __future__ import annotations

import os

import httpx


class Notifier:
    def __init__(self, enabled: bool):
        self.token = os.environ.get("MEMESCAN_TG_TOKEN")
        self.chat = os.environ.get("MEMESCAN_TG_CHAT_ID")
        self.enabled = enabled and bool(self.token and self.chat)

    def send(self, text: str):
        if not self.enabled:
            return
        try:
            httpx.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                       json={"chat_id": self.chat, "text": text, "disable_web_page_preview": True},
                       timeout=10)
        except httpx.HTTPError:
            pass
