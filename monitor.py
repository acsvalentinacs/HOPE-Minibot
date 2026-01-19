from __future__ import annotations
import os
import logging
import requests
try:
    from dotenv import load_dotenv, find_dotenv
    # Избегаем бага при stdin: ищем .env от текущей папки
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path=dotenv_path)
except Exception:
    pass

logging.basicConfig(level=logging.INFO)

class Monitor:
    def __init__(self, token: str | None = None, chat_id: str | None = None):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        if not self.token or not self.chat_id:
            logging.warning("Monitor: TELEGRAM_BOT_TOKEN/CHAT_ID not set. Messages will be logged only.")

    def _send_tg(self, text: str):
        if not self.token or not self.chat_id:
            return
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            requests.post(url, data={"chat_id": self.chat_id, "text": text}, timeout=5)
        except Exception as e:
            logging.warning(f"Monitor: Telegram send failed: {e}")

    def notify(self, text: str):
        logging.info(text)
        self._send_tg(f"ℹ️ {text}")

    def alert(self, text: str):
        logging.error(text)
        self._send_tg(f"🚨 {text}")
