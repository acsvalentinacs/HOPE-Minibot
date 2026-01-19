"""
HOPE/NORE Daily Digest Generator v1.0

Generates "Итоги дня" (End of Day) summary.

Format (RU):
- Max 7 bullet points
- Each: [type] короткий тезис — источник (ссылка)
- Strictly: news + link

Scheduled: 21:30 UTC daily

Usage:
    from core.daily_digest import DailyDigestGenerator, get_digest_generator

    generator = get_digest_generator()
    digest = generator.generate_digest()
    print(digest.format_telegram_ru())
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

STATE_DIR = Path(r"C:\Users\kirillDev\Desktop\TradingBot\minibot\state")
NEWS_JSONL = STATE_DIR / "news_items.jsonl"
EVENTS_JSONL = STATE_DIR / "events.jsonl"

MAX_DIGEST_ITEMS = 7


@dataclass
class DigestItem:
    """Single digest item."""
    event_type: str
    title: str
    source: str
    link: str
    impact_score: float
    timestamp: float


@dataclass
class DailyDigest:
    """Complete daily digest."""
    date: str  # YYYY-MM-DD
    items: List[DigestItem]
    market_summary: Dict[str, Any]
    generated_at: float

    def format_telegram_ru(self) -> str:
        """Format digest for Telegram (Russian)."""
        lines = [
            f"📋 <b>ИТОГИ ДНЯ</b> | {self.date}",
            "",
        ]

        type_emoji = {
            "regulation": "⚖️",
            "institutional": "🏦",
            "exploit": "🚨",
            "macro": "🌍",
            "market": "📊",
            "listing": "📋",
            "signal": "⚡",
        }

        for item in self.items[:MAX_DIGEST_ITEMS]:
            emoji = type_emoji.get(item.event_type, "📰")
            title_short = item.title[:60] + "..." if len(item.title) > 60 else item.title
            lines.append(f"• {emoji} {title_short}")
            if item.link:
                lines.append(f"  └ <a href=\"{item.link}\">{item.source}</a>")
            else:
                lines.append(f"  └ {item.source}")
            lines.append("")

        if self.market_summary:
            lines.append("📈 <b>Рынок за день:</b>")
            if "btc_change" in self.market_summary:
                btc = self.market_summary["btc_change"]
                emoji = "🟢" if btc > 0 else "🔴"
                lines.append(f"  {emoji} BTC: {btc:+.2f}%")
            if "eth_change" in self.market_summary:
                eth = self.market_summary["eth_change"]
                emoji = "🟢" if eth > 0 else "🔴"
                lines.append(f"  {emoji} ETH: {eth:+.2f}%")
            lines.append("")

        lines.append("#HOPE #итогидня #crypto")

        return "\n".join(lines)

    def format_telegram_en(self) -> str:
        """Format digest for Telegram (English)."""
        lines = [
            f"📋 <b>DAILY DIGEST</b> | {self.date}",
            "",
        ]

        type_emoji = {
            "regulation": "⚖️",
            "institutional": "🏦",
            "exploit": "🚨",
            "macro": "🌍",
            "market": "📊",
            "listing": "📋",
            "signal": "⚡",
        }

        for item in self.items[:MAX_DIGEST_ITEMS]:
            emoji = type_emoji.get(item.event_type, "📰")
            title_short = item.title[:60] + "..." if len(item.title) > 60 else item.title
            lines.append(f"• {emoji} {title_short}")
            if item.link:
                lines.append(f"  └ <a href=\"{item.link}\">{item.source}</a>")
            else:
                lines.append(f"  └ {item.source}")
            lines.append("")

        if self.market_summary:
            lines.append("📈 <b>Market Today:</b>")
            if "btc_change" in self.market_summary:
                btc = self.market_summary["btc_change"]
                emoji = "🟢" if btc > 0 else "🔴"
                lines.append(f"  {emoji} BTC: {btc:+.2f}%")
            if "eth_change" in self.market_summary:
                eth = self.market_summary["eth_change"]
                emoji = "🟢" if eth > 0 else "🔴"
                lines.append(f"  {emoji} ETH: {eth:+.2f}%")
            lines.append("")

        lines.append("#HOPE #dailydigest #crypto")

        return "\n".join(lines)


class DailyDigestGenerator:
    """
    Generates daily digest from events and news.

    Selects top items by impact_score.
    """

    def __init__(
        self,
        events_path: Path = EVENTS_JSONL,
        news_path: Path = NEWS_JSONL,
    ):
        self._events_path = events_path
        self._news_path = news_path

    def _load_events_today(self) -> List[DigestItem]:
        """Load today's events from journal."""
        items: List[DigestItem] = []

        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff = today_start.timestamp()

        if not self._events_path.exists():
            return items

        try:
            with open(self._events_path, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split(":", 2)
                    if len(parts) != 3:
                        continue

                    obj = json.loads(parts[2])
                    ts = obj.get("timestamp_unix", 0)

                    if ts < cutoff:
                        continue

                    if obj.get("event_type") == "signal":
                        continue

                    items.append(DigestItem(
                        event_type=obj.get("event_type", "market"),
                        title=obj.get("title", ""),
                        source=obj.get("source", "unknown"),
                        link=obj.get("source_url", ""),
                        impact_score=obj.get("impact_score", 0.3),
                        timestamp=ts,
                    ))

        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to load events: %s", e)

        return items

    def _load_news_today(self) -> List[DigestItem]:
        """Load today's news from spider output."""
        items: List[DigestItem] = []

        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff = today_start.timestamp()

        if not self._news_path.exists():
            return items

        try:
            with open(self._news_path, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split(":", 2)
                    if len(parts) != 3:
                        continue

                    obj = json.loads(parts[2])
                    ts = obj.get("fetch_timestamp", 0)

                    if ts < cutoff:
                        continue

                    items.append(DigestItem(
                        event_type="news",
                        title=obj.get("title", ""),
                        source=obj.get("source", "unknown"),
                        link=obj.get("link", ""),
                        impact_score=0.5,
                        timestamp=ts,
                    ))

        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to load news: %s", e)

        return items

    def _deduplicate(self, items: List[DigestItem]) -> List[DigestItem]:
        """Remove duplicates by title similarity."""
        seen_titles: set = set()
        unique: List[DigestItem] = []

        for item in items:
            title_key = item.title[:50].lower()
            if title_key in seen_titles:
                continue
            seen_titles.add(title_key)
            unique.append(item)

        return unique

    def _get_market_summary(self) -> Dict[str, Any]:
        """Get market summary for today."""
        summary = {}

        try:
            cache_file = STATE_DIR / "cache" / "snapshot.json"
            if cache_file.exists():
                content = cache_file.read_text(encoding="utf-8")
                data = json.loads(content)
                tickers = data.get("tickers", {})

                if "BTCUSDT" in tickers:
                    summary["btc_change"] = tickers["BTCUSDT"].get("price_change_pct", 0)
                if "ETHUSDT" in tickers:
                    summary["eth_change"] = tickers["ETHUSDT"].get("price_change_pct", 0)

        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to load market summary: %s", e)

        return summary

    def generate_digest(self) -> DailyDigest:
        """Generate daily digest."""
        events = self._load_events_today()
        news = self._load_news_today()

        all_items = events + news
        all_items = self._deduplicate(all_items)
        all_items.sort(key=lambda x: x.impact_score, reverse=True)

        top_items = all_items[:MAX_DIGEST_ITEMS]

        market_summary = self._get_market_summary()

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        return DailyDigest(
            date=today,
            items=top_items,
            market_summary=market_summary,
            generated_at=time.time(),
        )


def get_digest_generator() -> DailyDigestGenerator:
    """Get singleton generator instance."""
    global _generator_instance
    if "_generator_instance" not in globals():
        _generator_instance = DailyDigestGenerator()
    return _generator_instance


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("=== DAILY DIGEST GENERATOR TEST ===\n")

    generator = DailyDigestGenerator()
    digest = generator.generate_digest()

    print(f"Date: {digest.date}")
    print(f"Items: {len(digest.items)}")
    print(f"Market summary: {digest.market_summary}")
    print()
    print("=== TELEGRAM FORMAT (RU) ===")
    print(digest.format_telegram_ru())
