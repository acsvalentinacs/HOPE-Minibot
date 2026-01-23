# === AI SIGNATURE ===
# Created by: Kirill Dev
# Created at: 2026-01-19 18:24:32 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-22 23:00:00 UTC
# === END SIGNATURE ===
"""
Morning Scan - Daily 10:00 AM routine.

Scans market, publishes intel to Telegram channel.
Designed to run via Task Scheduler.

SSoT: Paths computed from __file__, not hardcoded.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# SSoT: Compute paths from __file__ (not hardcoded)
BASE_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = BASE_DIR / "state"
SECRETS_DIR = Path(os.environ.get("HOPE_SECRETS_DIR", r"C:\secrets\hope"))

# Load .env
def load_env():
    env_file = SECRETS_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

load_env()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(STATE_DIR / "morning_scan.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("morning_scan")


def run_scan() -> dict:
    """Run market intel scan."""
    sys.path.insert(0, str(BASE_DIR))
    from core.market_scanner import run_scan as market_scan

    return market_scan(trigger="morning_scan", top=5)


async def publish_to_channel() -> bool:
    """Publish intel to Telegram channel."""
    from core.telegram_publisher import publish_intel
    return await publish_intel(dry_run=False)


def main() -> int:
    logger.info("=" * 50)
    logger.info("MORNING SCAN START: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # Step 1: Run scan
    logger.info("Running market scan...")
    try:
        result = run_scan()
        if result.get("partial"):
            logger.warning("Scan partial - some sources failed")
        if not result.get("publishable"):
            logger.error("FAIL-CLOSED: Data not publishable")
            return 1
        logger.info("Scan complete: publishable=%s", result.get("publishable"))
    except Exception as e:
        logger.error("Scan failed: %s", e)
        return 1

    # Step 2: Publish to channel
    logger.info("Publishing to Telegram channel...")
    try:
        success = asyncio.run(publish_to_channel())
        if success:
            logger.info("Published successfully!")
        else:
            logger.error("Publish failed")
            return 1
    except Exception as e:
        logger.error("Publish error: %s", e)
        return 1

    # Step 3: Compute signal outcomes
    logger.info("Computing signal outcomes...")
    try:
        from core.outcome_tracker import compute_outcomes, get_symbols_to_track
        symbols = get_symbols_to_track()
        if symbols:
            logger.info("Tracking %d symbols: %s", len(symbols), symbols[:5])
        outcomes = compute_outcomes()
        logger.info("Computed %d outcomes", outcomes)
    except Exception as e:
        logger.warning("Outcome tracking skipped: %s", e)

    logger.info("MORNING SCAN COMPLETE")
    logger.info("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())

