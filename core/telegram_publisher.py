# === AI SIGNATURE ===
# Created by: Kirill Dev
# Created at: 2026-01-19 18:24:32 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-22 23:20:00 UTC
# === END SIGNATURE ===
"""
Telegram Publisher - Publish market intel to Telegram channel.

Implements fail-closed publication with IDEMPOTENCY:
- Only publishes if market_intel has valid snapshot_id
- Only publishes if not stale (TTL check)
- Only publishes if not partial (all critical sources succeeded)
- IDEMPOTENT: Tracks published snapshot_ids to prevent duplicates
- Supports dry-run mode for testing

Secrets are loaded from environment variables (not in git):
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID

DUAL-KEY LIVE (fail-safe):
- --live flag AND HOPE_TELEGRAM_LIVE=1 env required for real publish
- Without BOTH keys: only dry-run is allowed

Usage:
    # Dry-run (logs only, no actual send, default)
    python -m core.telegram_publisher --dry-run

    # Real publish (requires BOTH keys)
    HOPE_TELEGRAM_LIVE=1 python -m core.telegram_publisher --live
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

# SSoT: compute paths from __file__, not hardcoded
BASE_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = BASE_DIR / "state"
MARKET_INTEL_PATH = STATE_DIR / "market_intel.json"
PUBLISHED_LOG_PATH = STATE_DIR / "telegram" / "published.jsonl"
PUBLISHED_INDEX_PATH = STATE_DIR / "telegram" / "published_index.json"

TTL_MARKET_SEC = 300  # 5 min - market data TTL
TTL_NEWS_SEC = 900    # 15 min - news TTL

# Idempotency: maximum number of snapshot_ids to track
# Prevents unbounded growth while keeping enough history
MAX_PUBLISHED_HISTORY = 1000


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


# === IDEMPOTENCY FUNCTIONS (ATOMIC: check+append under one lock) ===

def _get_publisher_lock():
    """Get the inter-process lock for publisher operations."""
    from core.atomic_io import FileLock
    lock_path = STATE_DIR / "telegram" / ".publisher.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(lock_path)


def _load_published_index() -> Dict[str, Any]:
    """
    Load the published index (fast lookup cache).

    Index contains recent_ids set for O(1) lookup.
    Ledger (JSONL) is the SSoT, index is cache.
    """
    if not PUBLISHED_INDEX_PATH.exists():
        return {"recent_ids": [], "last_cleanup": 0}

    try:
        return json.loads(PUBLISHED_INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"recent_ids": [], "last_cleanup": 0}


def _save_published_index(index: Dict[str, Any]) -> None:
    """Atomically save the published index."""
    from core.atomic_io import atomic_write_json
    PUBLISHED_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(PUBLISHED_INDEX_PATH, index)


def _append_to_ledger(record: Dict[str, Any]) -> None:
    """Append a Canon B record to the ledger."""
    from core.contracts import wrap_sha256_prefix_line

    PUBLISHED_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = wrap_sha256_prefix_line(record) + "\n"

    with open(PUBLISHED_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def atomic_check_and_record_published(
    snapshot_id: str,
    intel: Dict[str, Any]
) -> tuple[bool, str]:
    """
    ATOMIC idempotency: check if published + record if not.

    All operations under ONE lock to prevent TOCTOU race.

    Returns:
        (was_already_published, status_message)
    """
    if not snapshot_id:
        return False, "no_snapshot_id"

    lock = _get_publisher_lock()

    with lock.acquire(timeout_sec=30.0):
        # Load index (fast cache)
        index = _load_published_index()
        recent_ids = set(index.get("recent_ids", []))

        # Check if already published
        if snapshot_id in recent_ids:
            return True, "already_published"

        # Not published - create record
        record = {
            "snapshot_id": snapshot_id,
            "published_at_utc": datetime.now(timezone.utc).isoformat(),
            "published_at_unix": time.time(),
            "btc_price": intel.get("btc_price", 0),
            "eth_price": intel.get("eth_price", 0),
        }

        # Append to ledger (Canon B JSONL)
        _append_to_ledger(record)

        # Update index
        recent_ids.add(snapshot_id)

        # Cleanup if needed (under same lock!)
        if len(recent_ids) > MAX_PUBLISHED_HISTORY:
            # Keep only recent ones - rebuild from ledger tail
            recent_ids = _rebuild_recent_from_ledger(MAX_PUBLISHED_HISTORY)

        index["recent_ids"] = list(recent_ids)
        index["last_update"] = time.time()
        _save_published_index(index)

        logger.info("Recorded published: %s", snapshot_id[:32])
        return False, "recorded"


def _rebuild_recent_from_ledger(max_entries: int) -> set:
    """Rebuild recent IDs set from ledger tail."""
    if not PUBLISHED_LOG_PATH.exists():
        return set()

    try:
        from core.contracts import parse_sha256_prefix_line, ContractViolation

        recent = []
        with open(PUBLISHED_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = parse_sha256_prefix_line(line)
                    sid = obj.get("snapshot_id", "")
                    if sid:
                        recent.append(sid)
                except ContractViolation:
                    continue

        # Keep only last max_entries
        return set(recent[-max_entries:])
    except Exception as e:
        logger.warning("Failed to rebuild from ledger: %s", e)
        return set()


def is_already_published(snapshot_id: str) -> bool:
    """
    Quick check if snapshot_id was already published.

    NOTE: For atomic check+record, use atomic_check_and_record_published().
    This function is for read-only checks (e.g., dry-run).
    """
    if not snapshot_id:
        return False

    index = _load_published_index()
    return snapshot_id in set(index.get("recent_ids", []))


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
    if config.dry_run:
        logger.info("[DRY-RUN] Would send message:\n%s", text)
        return True

    # Import aiohttp only when actually sending (not for dry-run)
    try:
        import aiohttp
    except ImportError:
        logger.error("FAIL-CLOSED: aiohttp not installed (pip install aiohttp)")
        return False

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


async def publish_intel(dry_run: bool = False, force: bool = False) -> bool:
    """
    Main publication flow with fail-closed semantics and IDEMPOTENCY.

    Args:
        dry_run: If True, only log what would be sent.
        force: If True, publish even if already published (skip idempotency check).

    Returns True if published successfully (or already published), False otherwise.
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

    snapshot_id = intel.get("market_snapshot_id", "")

    # Dry-run: just check and format, don't record
    if dry_run:
        if not force and is_already_published(snapshot_id):
            logger.info("IDEMPOTENT: snapshot %s already published (dry-run check)", snapshot_id[:32])
            return True

        message = format_message(intel)
        return await send_telegram_message(config, message)

    # LIVE mode: atomic check+record under one lock
    if not force:
        was_published, status = atomic_check_and_record_published(snapshot_id, intel)
        if was_published:
            logger.info("IDEMPOTENT: snapshot %s already published, skipping", snapshot_id[:32])
            return True  # Success - nothing to do

    # Format and send
    message = format_message(intel)
    success = await send_telegram_message(config, message)

    if success:
        logger.info("Published intel to Telegram")
        # Note: record was already done in atomic_check_and_record_published
        # If force was used, record now
        if force:
            _, _ = atomic_check_and_record_published(snapshot_id, intel)
    else:
        logger.error("Failed to publish intel to Telegram")
        # TODO: Consider removing from index on failure (but ledger is append-only)

    return success


def main() -> int:
    """CLI entrypoint with DUAL-KEY LIVE protection."""
    import asyncio
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Parse flags
    explicit_dry_run = "--dry-run" in sys.argv or "--dry_run" in sys.argv
    live_flag = "--live" in sys.argv
    force = "--force" in sys.argv

    # DUAL-KEY LIVE: requires BOTH --live flag AND env var
    live_env = os.environ.get("HOPE_TELEGRAM_LIVE", "0") == "1"

    # Determine effective mode
    if explicit_dry_run:
        # Explicit dry-run always wins
        dry_run = True
        logger.info("Mode: DRY-RUN (explicit --dry-run)")
    elif live_flag and live_env:
        # Both keys present = LIVE mode
        dry_run = False
        logger.warning("Mode: LIVE (dual-key activated: --live + HOPE_TELEGRAM_LIVE=1)")
    elif live_flag and not live_env:
        # Missing env key = FAIL-CLOSED
        logger.error("FAIL-CLOSED: --live flag requires HOPE_TELEGRAM_LIVE=1 env var")
        logger.error("Set: HOPE_TELEGRAM_LIVE=1 to confirm live publish")
        return 1
    else:
        # Default: dry-run for safety
        dry_run = True
        logger.info("Mode: DRY-RUN (default, use --live + HOPE_TELEGRAM_LIVE=1 for real)")

    if force and not dry_run:
        logger.warning("--force flag: bypassing idempotency check")

    success = asyncio.run(publish_intel(dry_run=dry_run, force=force))
    return 0 if success else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())

