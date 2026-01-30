# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-01-30T02:15:00Z
# Purpose: Watchdog monitor for Production Engine
# Contract: Alert via Telegram if heartbeat stale
# === END SIGNATURE ===
"""
Engine Watchdog - Monitors Production Engine heartbeat.

Checks heartbeat file every 30 seconds.
If heartbeat is older than TIMEOUT, sends Telegram alert.

Usage:
    python engine_watchdog.py [--timeout 120] [--interval 30]
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("WATCHDOG")

# Paths
HEARTBEAT_FILE = Path("state/ai/production/heartbeat.json")
LAST_ALERT_FILE = Path("state/ai/production/watchdog_last_alert.txt")

# Default settings
DEFAULT_TIMEOUT_SEC = 120  # Alert if no heartbeat for 2 min
DEFAULT_INTERVAL_SEC = 30  # Check every 30 sec
ALERT_COOLDOWN_SEC = 300   # Don't re-alert within 5 min


def load_telegram_config():
    """Load Telegram config for alerts."""
    try:
        from dotenv import load_dotenv
        load_dotenv(Path("C:/secrets/hope.env"))
    except ImportError:
        pass

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    admin_id = os.getenv("HOPE_ADMIN_ID") or os.getenv("TG_ADMIN_ID")

    if not token or not admin_id:
        logger.warning("Telegram not configured - alerts disabled")
        return None, None

    return token, int(admin_id)


async def send_telegram_alert(token: str, chat_id: int, message: str):
    """Send alert via Telegram."""
    try:
        import aiohttp
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    logger.info("Telegram alert sent")
                else:
                    logger.error(f"Telegram alert failed: {resp.status}")
    except Exception as e:
        logger.error(f"Telegram alert error: {e}")


def check_heartbeat(timeout_sec: int) -> tuple:
    """
    Check heartbeat file.

    Returns (ok, info_dict)
    """
    if not HEARTBEAT_FILE.exists():
        return False, {"error": "heartbeat_file_missing"}

    try:
        data = json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
        ts = data.get("timestamp_unix", 0)
        age = time.time() - ts

        if age > timeout_sec:
            return False, {
                "error": "heartbeat_stale",
                "age_sec": age,
                "last_cycle": data.get("cycle", 0),
                "last_session": data.get("session", "?"),
                "positions": data.get("positions", 0),
            }

        return True, {
            "age_sec": age,
            "cycle": data.get("cycle", 0),
            "session": data.get("session", "?"),
            "positions": data.get("positions", 0),
        }

    except Exception as e:
        return False, {"error": str(e)}


def should_alert() -> bool:
    """Check if we should send alert (cooldown logic)."""
    if not LAST_ALERT_FILE.exists():
        return True

    try:
        last_alert = float(LAST_ALERT_FILE.read_text().strip())
        return (time.time() - last_alert) > ALERT_COOLDOWN_SEC
    except Exception:
        return True


def record_alert():
    """Record alert timestamp."""
    LAST_ALERT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_ALERT_FILE.write_text(str(time.time()), encoding="utf-8")


async def main():
    parser = argparse.ArgumentParser(description="Engine Watchdog")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC,
                       help=f"Alert if heartbeat older than N seconds (default: {DEFAULT_TIMEOUT_SEC})")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SEC,
                       help=f"Check interval in seconds (default: {DEFAULT_INTERVAL_SEC})")
    parser.add_argument("--once", action="store_true", help="Check once and exit")
    args = parser.parse_args()

    token, admin_id = load_telegram_config()

    logger.info(f"Watchdog started | timeout={args.timeout}s | interval={args.interval}s")
    logger.info(f"Telegram alerts: {'enabled' if token else 'disabled'}")

    consecutive_failures = 0

    while True:
        ok, info = check_heartbeat(args.timeout)

        if ok:
            consecutive_failures = 0
            logger.debug(f"Engine OK | cycle={info.get('cycle')} | age={info.get('age_sec'):.1f}s")
        else:
            consecutive_failures += 1
            logger.warning(f"Engine PROBLEM ({consecutive_failures}): {info}")

            # Alert on first failure or after cooldown
            if token and admin_id and should_alert():
                error = info.get("error", "unknown")
                if error == "heartbeat_file_missing":
                    msg = (
                        "🚨 <b>HOPE Engine DOWN</b>\n\n"
                        "Heartbeat file missing.\n"
                        "Engine may not be running."
                    )
                elif error == "heartbeat_stale":
                    msg = (
                        "🚨 <b>HOPE Engine STALE</b>\n\n"
                        f"Last heartbeat: {info.get('age_sec', 0):.0f}s ago\n"
                        f"Last cycle: {info.get('last_cycle')}\n"
                        f"Session: {info.get('last_session')}\n"
                        f"Open positions: {info.get('positions')}\n\n"
                        "Engine may be hung or crashed."
                    )
                else:
                    msg = f"🚨 <b>HOPE Engine ERROR</b>\n\n{error}"

                await send_telegram_alert(token, admin_id, msg)
                record_alert()

        if args.once:
            sys.exit(0 if ok else 1)

        await asyncio.sleep(args.interval)


if __name__ == "__main__":
    asyncio.run(main())
