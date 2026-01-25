# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T18:45:00Z
# Purpose: One-time news digest publisher for @hope_vip_signals
# === END SIGNATURE ===
"""
News Digest Publisher - One-time publish of news history.

Reads news_items.jsonl and publishes a formatted digest to Telegram.
Ignores age filter to include all available news.

Usage:
    python tools/publish_news_digest.py --dry-run   # Preview only
    python tools/publish_news_digest.py --publish   # Actually send
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

STATE_DIR = PROJECT_ROOT / "state"
NEWS_ITEMS_PATH = STATE_DIR / "news_items.jsonl"

# Telegram config
CHANNEL_ID = "@hope_vip_signals"
MAX_NEWS_PER_MESSAGE = 10  # Telegram message size limit


def load_secrets() -> Dict[str, str]:
    """Load secrets from environment or .env file."""
    secrets = {}

    # Try environment first
    secrets["bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN", "")

    # Try .env file
    env_paths = [
        Path(r"C:\secrets\hope\.env"),
        Path(r"C:\secrets\hope.env"),
    ]

    for env_path in env_paths:
        if env_path.exists() and not secrets["bot_token"]:
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")
                        if key == "TELEGRAM_BOT_TOKEN":
                            secrets["bot_token"] = value
            except Exception:
                pass

    return secrets


def read_news_items(max_items: int = 100) -> List[Dict[str, Any]]:
    """Read news items from JSONL file."""
    if not NEWS_ITEMS_PATH.exists():
        print(f"ERROR: News file not found: {NEWS_ITEMS_PATH}")
        return []

    items = []
    try:
        with open(NEWS_ITEMS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                    items.append(item)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        print(f"ERROR: Failed to read news: {e}")
        return []

    # Sort by published date (newest first)
    items.sort(key=lambda x: x.get("published_utc", ""), reverse=True)

    return items[:max_items]


def categorize_news(items: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Categorize news by type."""
    categories = {
        "regulation": [],
        "listings": [],
        "market": [],
        "institutional": [],
        "tech": [],
        "other": [],
    }

    for item in items:
        title = item.get("title", "").lower()
        category = item.get("category", "").lower()
        source = item.get("source_id", "")

        # Determine category
        if any(kw in title for kw in ["sec", "regulation", "law", "bill", "policy", "ban", "fca", "occ"]):
            categories["regulation"].append(item)
        elif "binance" in source and "listing" in title.lower():
            categories["listings"].append(item)
        elif any(kw in title for kw in ["listing", "delist", "launch", "add"]) and "binance" in source:
            categories["listings"].append(item)
        elif any(kw in title for kw in ["etf", "institutional", "fund", "treasury", "ark", "grayscale"]):
            categories["institutional"].append(item)
        elif any(kw in title for kw in ["price", "market", "bull", "bear", "rally", "crash", "outflow"]):
            categories["market"].append(item)
        elif any(kw in title for kw in ["quantum", "security", "hack", "exploit", "upgrade"]):
            categories["tech"].append(item)
        else:
            categories["other"].append(item)

    return categories


def format_digest_message(items: List[Dict[str, Any]], page: int = 1, total_pages: int = 1) -> str:
    """Format news items as Telegram message."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        f"<b>[HOPE NEWS DIGEST]</b> {now}",
        f"Page {page}/{total_pages}",
        "",
    ]

    categories = categorize_news(items)

    # Regulation news (high priority)
    if categories["regulation"]:
        lines.append("<b>[LAW] REGULATION</b>")
        for item in categories["regulation"][:3]:
            title = item.get("title", "")[:65]
            if len(item.get("title", "")) > 65:
                title += "..."
            source = item.get("source_id", "unknown").replace("_rss", "")
            lines.append(f"  - {title}")
            lines.append(f"    <i>{source}</i>")
        lines.append("")

    # Listings
    if categories["listings"]:
        lines.append("<b>[LIST] BINANCE UPDATES</b>")
        for item in categories["listings"][:4]:
            title = item.get("title", "")[:60]
            if len(item.get("title", "")) > 60:
                title += "..."
            lines.append(f"  - {title}")
        lines.append("")

    # Institutional
    if categories["institutional"]:
        lines.append("<b>[INST] INSTITUTIONAL</b>")
        for item in categories["institutional"][:3]:
            title = item.get("title", "")[:65]
            if len(item.get("title", "")) > 65:
                title += "..."
            source = item.get("source_id", "unknown").replace("_rss", "")
            lines.append(f"  - {title}")
            lines.append(f"    <i>{source}</i>")
        lines.append("")

    # Market
    if categories["market"]:
        lines.append("<b>[MKT] MARKET NEWS</b>")
        for item in categories["market"][:3]:
            title = item.get("title", "")[:65]
            if len(item.get("title", "")) > 65:
                title += "..."
            source = item.get("source_id", "unknown").replace("_rss", "")
            lines.append(f"  - {title}")
            lines.append(f"    <i>{source}</i>")
        lines.append("")

    # Tech
    if categories["tech"]:
        lines.append("<b>[TECH] TECHNOLOGY</b>")
        for item in categories["tech"][:2]:
            title = item.get("title", "")[:65]
            if len(item.get("title", "")) > 65:
                title += "..."
            source = item.get("source_id", "unknown").replace("_rss", "")
            lines.append(f"  - {title}")
            lines.append(f"    <i>{source}</i>")
        lines.append("")

    lines.append("#HOPE #spider #news #digest")

    return "\n".join(lines)


def send_telegram_message(bot_token: str, message: str, dry_run: bool = True) -> bool:
    """Send message to Telegram channel."""
    if dry_run:
        print("\n=== DRY-RUN MESSAGE ===")
        print(message)
        print("=== END MESSAGE ===\n")
        return True

    if not bot_token:
        print("ERROR: No bot token available")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({
        "chat_id": CHANNEL_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")

    try:
        req = Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("ok"):
                print(f"Message sent! ID: {result.get('result', {}).get('message_id')}")
                return True
            else:
                print(f"Telegram error: {result}")
                return False
    except HTTPError as e:
        print(f"HTTP error: {e.code} - {e.read().decode()}")
        return False
    except URLError as e:
        print(f"URL error: {e.reason}")
        return False
    except Exception as e:
        print(f"Error: {e}")
        return False


def main() -> int:
    """Main entrypoint."""
    import argparse

    parser = argparse.ArgumentParser(description="News Digest Publisher")
    parser.add_argument("--dry-run", "-d", action="store_true", help="Preview only")
    parser.add_argument("--publish", "-p", action="store_true", help="Actually send")
    parser.add_argument("--max", "-m", type=int, default=50, help="Max items to include")

    args = parser.parse_args()

    dry_run = not args.publish

    print("=== NEWS DIGEST PUBLISHER ===")
    print(f"Mode: {'DRY-RUN' if dry_run else 'LIVE PUBLISH'}")
    print(f"Channel: {CHANNEL_ID}")
    print()

    # Load news
    items = read_news_items(max_items=args.max)

    if not items:
        print("No news items found")
        return 1

    print(f"Loaded {len(items)} news items")

    # Load secrets
    secrets = load_secrets()
    if not dry_run and not secrets.get("bot_token"):
        print("ERROR: TELEGRAM_BOT_TOKEN not found")
        return 1

    # Format and send
    message = format_digest_message(items)

    success = send_telegram_message(
        bot_token=secrets.get("bot_token", ""),
        message=message,
        dry_run=dry_run,
    )

    if success:
        print("PASS: News digest published" if not dry_run else "PASS: Dry-run complete")
        return 0
    else:
        print("FAIL: Failed to publish")
        return 1


if __name__ == "__main__":
    sys.exit(main())
