from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import List, Tuple
from urllib import parse, request

ROOT_DIR = Path(__file__).resolve().parents[1]
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

NOTIFICATIONS_FILE = LOGS_DIR / "notifications_v5.jsonl"

logger = logging.getLogger("notifications_worker_v5")
logging.basicConfig(
    level=logging.INFO,
    format="[NOTIF] %(asctime)s %(levelname)s: %(message)s",
)


def _load_token_and_chats() -> Tuple[str | None, List[str]]:
    """
    Читаем TELEGRAM_TOKEN_MINI / TELEGRAM_TOKEN и TELEGRAM_ALERT_CHAT_IDS / TELEGRAM_ALLOWED из окружения.
    """
    token = os.getenv("TELEGRAM_TOKEN_MINI") or os.getenv("TELEGRAM_TOKEN")
    chats_raw = os.getenv("TELEGRAM_ALERT_CHAT_IDS") or os.getenv("TELEGRAM_ALLOWED")

    chat_ids: List[str] = []
    if chats_raw:
        for part in chats_raw.split(","):
            part = part.strip()
            if part:
                chat_ids.append(part)

    return token, chat_ids


def _send_telegram_message(token: str, chat_id: str, text: str) -> None:
    if not text:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    encoded = parse.urlencode(data).encode("utf-8")
    req = request.Request(url, data=encoded)

    try:
        with request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                logger.warning("Telegram response status=%s", resp.status)
    except Exception as e:
        logger.error("Telegram send error: %s", e)


def _follow_file(path: Path):
    """
    Простое tail -f: читаем новые строки по мере появления.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)

    with path.open("r", encoding="utf-8") as f:
        f.seek(0, os.SEEK_END)  # начинаем с конца
        while True:
            pos = f.tell()
            line = f.readline()
            if not line:
                time.sleep(1.0)
                f.seek(pos)
                continue
            yield line


def main() -> None:
    token, chat_ids = _load_token_and_chats()
    if not token or not chat_ids:
        logger.error(
            "Нет TELEGRAM_TOKEN_MINI/TELEGRAM_TOKEN или TELEGRAM_ALERT_CHAT_IDS/TELEGRAM_ALLOWED в окружении."
        )
        logger.error("Воркер запущен, но не может отправлять сообщения. Исправь .env и перезапусти.")
        # Не выходим насмерть, просто ждём, чтобы логи были видны.
        while True:
            time.sleep(60)

    logger.info("notifications_worker_v5 стартовал. chat_ids=%s", chat_ids)
    logger.info("Слежу за файлом: %s", NOTIFICATIONS_FILE)

    for raw in _follow_file(NOTIFICATIONS_FILE):
        raw = raw.strip()
        if not raw:
            continue

        try:
            notif = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Некорректный JSON в notifications_v5.jsonl: %r", raw)
            continue

        text = notif.get("text") or notif.get("message") or ""
        if not text.strip():
            continue

        for chat_id in chat_ids:
            _send_telegram_message(token, chat_id, text)


if __name__ == "__main__":
    main()
