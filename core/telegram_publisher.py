"""
Telegram Publisher - Publish market intel to Telegram channel.

Implements fail-closed publication:
- Only publishes if market_intel has valid snapshot_id
- Only publishes if not stale (TTL check)
- Only publishes if not partial (all critical sources succeeded)
- Supports dry-run mode for testing

Secrets are loaded from environment variables (not in git):
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID

Usage:
    # Dry-run (logs only, no actual send)
    python -m core.telegram_publisher --dry-run

    # Real publish
    python -m core.telegram_publisher
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("telegram")

BASE_DIR = Path(r"C:\Users\kirillDev\Desktop\TradingBot\minibot")
STATE_DIR = BASE_DIR / "state"
MARKET_INTEL_PATH = STATE_DIR / "market_intel.json"

TTL_MARKET_SEC = 300  # 5 min - market data TTL
TTL_NEWS_SEC = 900    # 15 min - news TTL


@dataclass
class TelegramConfig:
    bot_token: str
    chat_id: str
    dry_run: bool = False

    @classmethod
    def from_env(cls, dry_run: bool = False) -> "TelegramConfig":
        """Load config from environment variables."""
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

        if not dry_run and (not token or not chat_id):
            raise ValueError("FAIL-CLOSED: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")

        return cls(bot_token=token, chat_id=chat_id, dry_run=dry_run)


def load_market_intel() -> Optional[Dict[str, Any]]:
    """Load latest market_intel.json."""
    if not MARKET_INTEL_PATH.exists():
        logger.error("FAIL-CLOSED: market_intel.json not found")
        return None

    try:
        return json.loads(MARKET_INTEL_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.error("FAIL-CLOSED: market_intel.json parse error: %s", e)
        return None


def validate_intel(intel: Dict[str, Any]) -> tuple[bool, str]:
    """
    Validate market intel for publication.

    Returns (is_valid, reason).
    """
    # Check schema version
    if intel.get("schema_version") != "1.0.0":
        return False, f"unknown_schema_version:{intel.get('schema_version')}"

    # Check if partial
    if intel.get("partial", False):
        return False, "partial_data"

    # Check if publishable flag
    if not intel.get("publishable", False):
        return False, "not_publishable"

    # Check market snapshot exists
    if not intel.get("market_snapshot_id"):
        return False, "no_market_snapshot"

    # Check staleness
    ts = intel.get("timestamp", 0)
    age = time.time() - ts
    if age > TTL_MARKET_SEC:
        return False, f"stale_data:age={age:.0f}s"

    return True, "ok"


def format_message(intel: Dict[str, Any]) -> str:
    """Format market intel as Telegram message."""
    lines = []

    ts = intel.get("timestamp", 0)
    ts_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines.append(f"HOPE Market Intel | {ts_str}")
    lines.append("")

    # Top gainers
    gainers = intel.get("top_gainers", [])[:5]
    if gainers:
        lines.append("TOP GAINERS:")
        for g in gainers:
            lines.append(f"  {g['symbol']}: +{g['change_pct']:.1f}%")
        lines.append("")

    # Top losers
    losers = intel.get("top_losers", [])[:5]
    if losers:
        lines.append("TOP LOSERS:")
        for l in losers:
            lines.append(f"  {l['symbol']}: {l['change_pct']:.1f}%")
        lines.append("")

    # High impact news
    news = [n for n in intel.get("news_items", []) if n.get("impact_score", 0) >= 0.6][:5]
    if news:
        lines.append("HIGH IMPACT NEWS:")
        for n in news:
            lines.append(f"  [{n['event_type']}] {n['title'][:60]}...")
        lines.append("")

    # Footer - clean for users, snapshot logged internally
    lines.append("#HOPE #crypto #signals")

    return "\n".join(lines)


async def send_telegram_message(config: TelegramConfig, text: str) -> bool:
    """Send message to Telegram channel."""
    import aiohttp

    if config.dry_run:
        logger.info("[DRY-RUN] Would send message:\n%s", text)
        return True

    url = f"https://api.telegram.org/bot{config.bot_token}/sendMessage"
    payload = {
        "chat_id": config.chat_id,
        "text": text,
        "parse_mode": "HTML",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error("Telegram API error: %s %s", resp.status, body)
                    return False
                return True
    except Exception as e:
        logger.error("Telegram send failed: %s", e)
        return False


async def publish_intel(dry_run: bool = False) -> bool:
    """
    Main publication flow with fail-closed semantics.

    Returns True if published successfully, False otherwise.
    """
    # Load config
    try:
        config = TelegramConfig.from_env(dry_run=dry_run)
    except ValueError as e:
        logger.error(str(e))
        return False

    # Load intel
    intel = load_market_intel()
    if intel is None:
        return False

    # Validate
    is_valid, reason = validate_intel(intel)
    if not is_valid:
        logger.error("FAIL-CLOSED: intel validation failed: %s", reason)
        return False

    # Format message
    message = format_message(intel)

    # Send
    success = await send_telegram_message(config, message)

    if success:
        logger.info("Published intel to Telegram (dry_run=%s)", dry_run)
    else:
        logger.error("Failed to publish intel to Telegram")

    return success


def main() -> int:
    """CLI entrypoint."""
    import asyncio
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    dry_run = "--dry-run" in sys.argv or "--dry_run" in sys.argv

    success = asyncio.run(publish_intel(dry_run=dry_run))
    return 0 if success else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
