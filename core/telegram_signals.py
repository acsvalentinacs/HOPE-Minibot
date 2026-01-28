"""
HOPE/NORE Telegram Signals Publisher v2.0

Publishes trading signals and market intelligence to Telegram channel.
Channel: https://t.me/hope_vip_signals

v2.0 Changes (per Opinion1):
- Integrated event classification with impact scoring
- High-impact news filter (impact >= 0.6)
- Publication journal to prevent duplicates
- Cursor/ack system for reliable delivery

Fail-closed design:
- API error = log + retry queue
- Invalid data = skip + log
- Rate limit = backoff

Usage:
    from core.telegram_signals import SignalPublisher

    publisher = SignalPublisher()
    publisher.publish_market_snapshot(snapshot)
    publisher.publish_signals(signals)
    publisher.publish_high_impact_news(news)  # NEW in v2.0
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

logger = logging.getLogger(__name__)

# Channel config
CHANNEL_ID = "@hope_vip_signals"
STATE_DIR = Path(r"C:\Users\kirillDev\Desktop\TradingBot\minibot\state")
JOURNAL_FILE = STATE_DIR / "publication_journal.json"

# Rate limiting
MIN_INTERVAL_SECONDS = 60  # Minimum 1 minute between messages
MAX_RETRIES = 3

# Impact filtering (per Opinion1)
HIGH_IMPACT_THRESHOLD = 0.6  # Only publish news with impact >= 0.6


@dataclass
class PublishResult:
    """Result of publish attempt."""
    success: bool
    message_id: Optional[int] = None
    error: Optional[str] = None
    timestamp: float = 0


class PublicationJournal:
    """
    Tracks published items to prevent duplicates (per Opinion1).

    Uses content hash as cursor/ack mechanism.
    Journal is persisted to disk and survives restarts.
    """

    def __init__(self, journal_path: Path = JOURNAL_FILE):
        self._journal_path = journal_path
        self._published_hashes: Set[str] = set()
        self._load()

    def _load(self) -> None:
        """Load journal from disk."""
        if not self._journal_path.exists():
            return

        try:
            content = self._journal_path.read_text(encoding="utf-8")
            data = json.loads(content)
            self._published_hashes = set(data.get("published", []))
            logger.info("Loaded %d entries from publication journal", len(self._published_hashes))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load journal: %s", e)
            self._published_hashes = set()

    def _save(self) -> None:
        """Save journal to disk."""
        try:
            self._journal_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "published": list(self._published_hashes),
                "last_update": datetime.now().isoformat(),
            }
            self._journal_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except OSError as e:
            logger.error("Failed to save journal: %s", e)

    def _compute_hash(self, content: str) -> str:
        """Compute SHA256 hash of content."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def is_published(self, content: str) -> bool:
        """Check if content was already published."""
        content_hash = self._compute_hash(content)
        return content_hash in self._published_hashes

    def mark_published(self, content: str) -> None:
        """Mark content as published."""
        content_hash = self._compute_hash(content)
        self._published_hashes.add(content_hash)
        self._save()

    def prune_old_entries(self, max_entries: int = 1000) -> None:
        """Keep only the most recent entries."""
        if len(self._published_hashes) > max_entries:
            # Keep last N entries (convert to list, slice, convert back)
            entries_list = list(self._published_hashes)
            self._published_hashes = set(entries_list[-max_entries:])
            self._save()
            logger.info("Pruned journal to %d entries", len(self._published_hashes))


class SignalPublisher:
    """
    Telegram channel publisher for HOPE signals.

    Requires TELEGRAM_BOT_TOKEN in secrets.

    v2.0: Includes publication journal and high-impact news filtering.
    """

    def __init__(self):
        self._last_publish_time: float = 0
        self._retry_queue: List[str] = []
        self._bot_token: Optional[str] = None
        self._journal = PublicationJournal()

    def _get_bot_token(self) -> str:
        """Load bot token from secrets (lazy)."""
        if self._bot_token:
            return self._bot_token

        try:
            from core.secrets_loader import SecretsLoader
            secrets = SecretsLoader.load()
            self._bot_token = secrets.get_required("TELEGRAM_BOT_TOKEN")
            return self._bot_token
        except Exception as e:
            logger.error("Failed to load TELEGRAM_BOT_TOKEN: %s", e)
            raise

    def _send_message(self, text: str, parse_mode: str = "HTML") -> PublishResult:
        """Send message to Telegram channel."""
        # Rate limiting
        now = time.time()
        elapsed = now - self._last_publish_time
        if elapsed < MIN_INTERVAL_SECONDS:
            wait = MIN_INTERVAL_SECONDS - elapsed
            logger.info("Rate limit: waiting %.1f seconds", wait)
            time.sleep(wait)

        try:
            token = self._get_bot_token()
        except Exception as e:
            return PublishResult(success=False, error=str(e), timestamp=now)

        url = f"https://api.telegram.org/bot{token}/sendMessage"

        payload = {
            "chat_id": CHANNEL_ID,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }

        for attempt in range(MAX_RETRIES):
            try:
                req = Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "HOPE-Bot/1.0",
                    },
                    method="POST",
                )

                with urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read().decode("utf-8"))

                if result.get("ok"):
                    message_id = result.get("result", {}).get("message_id")
                    self._last_publish_time = time.time()
                    logger.info("Published message %s to %s", message_id, CHANNEL_ID)
                    return PublishResult(
                        success=True,
                        message_id=message_id,
                        timestamp=time.time(),
                    )
                else:
                    error = result.get("description", "Unknown error")
                    logger.warning("Telegram API error: %s", error)
                    return PublishResult(success=False, error=error, timestamp=time.time())

            except (URLError, HTTPError) as e:
                logger.warning("Attempt %d failed: %s", attempt + 1, e)
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
                else:
                    return PublishResult(success=False, error=str(e), timestamp=time.time())

        return PublishResult(success=False, error="Max retries exceeded", timestamp=time.time())

    def format_market_snapshot(
        self,
        tickers: Dict[str, Any],
        fear_greed: int,
        news: List[Any],
    ) -> str:
        """Format market snapshot for Telegram."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Header
        lines = [
            f"🔔 <b>HOPE SIGNAL</b> | {now}",
            "",
            "📊 <b>MARKET SNAPSHOT</b>",
        ]

        # BTC and ETH
        if "BTCUSDT" in tickers:
            btc = tickers["BTCUSDT"]
            emoji = "🟢" if btc.price_change_pct > 0 else "🔴"
            lines.append(f"{emoji} BTC: ${btc.price:,.0f} ({btc.price_change_pct:+.2f}%)")

        if "ETHUSDT" in tickers:
            eth = tickers["ETHUSDT"]
            emoji = "🟢" if eth.price_change_pct > 0 else "🔴"
            lines.append(f"{emoji} ETH: ${eth.price:,.0f} ({eth.price_change_pct:+.2f}%)")

        # Fear/Greed
        if fear_greed < 25:
            fg_text = "Extreme Fear 😱"
        elif fear_greed < 45:
            fg_text = "Fear 😰"
        elif fear_greed < 55:
            fg_text = "Neutral 😐"
        elif fear_greed < 75:
            fg_text = "Greed 😀"
        else:
            fg_text = "Extreme Greed 🤑"

        lines.append(f"📈 Fear/Greed: {fear_greed} ({fg_text})")

        # Top movers
        lines.append("")
        lines.append("📈 <b>TOP MOVERS</b>")

        sorted_tickers = sorted(
            [t for k, t in tickers.items() if k.endswith("USDT") and t.volume_24h > 1e8],
            key=lambda x: abs(x.price_change_pct),
            reverse=True,
        )[:5]

        for t in sorted_tickers:
            symbol = t.symbol.replace("USDT", "")
            emoji = "🟢" if t.price_change_pct > 0 else "🔴"
            lines.append(f"{emoji} {symbol}: {t.price_change_pct:+.2f}%")

        # News highlights
        if news:
            lines.append("")
            lines.append("📰 <b>KEY NEWS</b>")
            for n in news[:3]:
                sentiment_emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(n.sentiment, "⚪")
                title = n.title[:60] + "..." if len(n.title) > 60 else n.title
                lines.append(f"{sentiment_emoji} {title}")

        # Footer
        lines.append("")
        lines.append("#HOPE #crypto #signals")

        return "\n".join(lines)

    def format_trading_signals(self, signals: List[Any]) -> str:
        """Format trading signals for Telegram."""
        if not signals:
            return ""

        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        lines = [
            f"⚡ <b>HOPE TRADING SIGNALS</b> | {now}",
            "",
        ]

        for sig in signals[:5]:
            direction_emoji = "🟢 LONG" if sig.direction == "long" else "🔴 SHORT"
            strength_bar = "█" * int(sig.strength * 5) + "░" * (5 - int(sig.strength * 5))

            symbol = sig.symbol.replace("USDT", "")
            lines.append(f"<b>{symbol}</b> {direction_emoji}")
            lines.append(f"  Strength: [{strength_bar}] {sig.strength:.0%}")
            lines.append(f"  Type: {sig.signal_type}")
            lines.append(f"  {sig.reason}")
            lines.append("")

        lines.append("⚠️ <i>Not financial advice. DYOR.</i>")
        lines.append("#HOPE #signals #trading")

        return "\n".join(lines)

    def publish_market_snapshot(
        self,
        tickers: Dict[str, Any],
        fear_greed: int,
        news: List[Any],
    ) -> PublishResult:
        """Publish market snapshot to channel."""
        text = self.format_market_snapshot(tickers, fear_greed, news)
        return self._send_message(text)

    def publish_signals(self, signals: List[Any]) -> PublishResult:
        """Publish trading signals to channel."""
        if not signals:
            return PublishResult(success=True, error="No signals to publish")

        text = self.format_trading_signals(signals)
        return self._send_message(text)

    def publish_alert(self, title: str, message: str, level: str = "info") -> PublishResult:
        """Publish custom alert."""
        emoji_map = {
            "info": "ℹ️",
            "warning": "⚠️",
            "error": "🚨",
            "success": "✅",
        }
        emoji = emoji_map.get(level, "ℹ️")

        text = f"{emoji} <b>{title}</b>\n\n{message}\n\n#HOPE #alert"
        return self._send_message(text)

    def format_high_impact_news(self, events: List[Any]) -> str:
        """
        Format high-impact news for Telegram (v2.0).

        Only includes events with impact_score >= HIGH_IMPACT_THRESHOLD.
        Skips already published items via journal.
        """
        if not events:
            return ""

        # Filter by impact threshold
        high_impact = [e for e in events if getattr(e, 'impact_score', 0) >= HIGH_IMPACT_THRESHOLD]

        if not high_impact:
            return ""

        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        lines = [
            f"🔥 <b>HIGH IMPACT NEWS</b> | {now}",
            "",
        ]

        # Type emoji mapping
        type_emoji = {
            "regulation": "⚖️",
            "listing": "📋",
            "exploit": "🚨",
            "macro": "🌍",
            "institutional": "🏦",
            "market": "📊",
        }

        # Sentiment emoji
        sentiment_emoji = {
            "bullish": "🟢",
            "bearish": "🔴",
            "neutral": "⚪",
        }

        for event in high_impact[:5]:  # Max 5 items
            # Check if already published
            if self._journal.is_published(event.title):
                continue

            e_type = getattr(event, 'event_type', 'market')
            e_sentiment = getattr(event, 'sentiment', 'neutral')
            e_impact = getattr(event, 'impact_score', 0.5)
            e_assets = getattr(event, 'affected_assets', [])

            type_icon = type_emoji.get(e_type, "📰")
            sent_icon = sentiment_emoji.get(e_sentiment, "⚪")

            # Impact bar visualization
            impact_filled = int(e_impact * 5)
            impact_bar = "█" * impact_filled + "░" * (5 - impact_filled)

            lines.append(f"{type_icon} <b>{event.title[:80]}</b>")
            lines.append(f"  {sent_icon} Impact: [{impact_bar}] {e_impact:.0%}")

            if e_assets:
                lines.append(f"  📌 {', '.join(e_assets)}")

            lines.append("")

        if len(lines) <= 2:  # Only header, no news passed filter
            return ""

        lines.append("⚠️ <i>DYOR. Not financial advice.</i>")
        lines.append("#HOPE #news #highimpact")

        return "\n".join(lines)

    def publish_high_impact_news(self, events: List[Any]) -> PublishResult:
        """
        Publish high-impact news to channel (v2.0).

        Only publishes events with impact >= 0.6.
        Tracks published items to prevent duplicates.
        """
        text = self.format_high_impact_news(events)

        if not text:
            return PublishResult(success=True, error="No high-impact news to publish")

        result = self._send_message(text)

        # Mark all included events as published
        if result.success:
            for event in events:
                if getattr(event, 'impact_score', 0) >= HIGH_IMPACT_THRESHOLD:
                    self._journal.mark_published(event.title)

        return result

    def publish_combined_update(
        self,
        tickers: Dict[str, Any],
        fear_greed: int,
        signals: List[Any],
        events: List[Any],
    ) -> List[PublishResult]:
        """
        Publish combined market update (snapshot + signals + high-impact news).

        This is the recommended method for regular updates.
        Sends 1-2 messages depending on content.
        """
        results: List[PublishResult] = []

        # 1. Market snapshot + signals (always publish)
        snapshot_text = self.format_market_snapshot(tickers, fear_greed, [])
        signals_text = self.format_trading_signals(signals)

        combined = snapshot_text
        if signals_text:
            combined += "\n\n" + "─" * 20 + "\n\n" + signals_text

        if combined and not self._journal.is_published(combined):
            result = self._send_message(combined)
            results.append(result)
            if result.success:
                self._journal.mark_published(combined)

        # 2. High-impact news (separate message if any)
        news_text = self.format_high_impact_news(events)
        if news_text:
            # Add small delay between messages
            time.sleep(2)
            result = self._send_message(news_text)
            results.append(result)

        # Prune journal periodically
        self._journal.prune_old_entries()

        return results


def format_weekly_report(stats: Any) -> str:
    """
    Format weekly statistics report for Telegram.

    Args:
        stats: WeeklyStats from OutcomeTracker
    """
    from datetime import datetime

    week_start = datetime.fromtimestamp(stats.week_start).strftime("%Y-%m-%d")
    week_end = datetime.fromtimestamp(stats.week_end).strftime("%Y-%m-%d")

    lines = [
        f"📊 <b>HOPE WEEKLY REPORT</b>",
        f"📅 {week_start} - {week_end}",
        "",
        "📈 <b>SIGNAL STATISTICS</b>",
        f"  Total Signals: {stats.total_signals}",
        f"  Completed Outcomes: {stats.completed_outcomes}",
        f"  Invalidated: {stats.invalidated_count}",
        "",
    ]

    def fmt_pct(val: float | None) -> str:
        return f"{val:+.2f}%" if val is not None else "N/A"

    def fmt_win(val: float | None) -> str:
        return f"{val:.1f}%" if val is not None else "N/A"

    lines.append("⏱️ <b>1H PERFORMANCE</b>")
    lines.append(f"  MFE: {fmt_pct(stats.avg_mfe_1h)} | MAE: {fmt_pct(stats.avg_mae_1h)}")
    lines.append(f"  Win Rate: {fmt_win(stats.win_rate_1h)}")
    lines.append("")

    lines.append("⏱️ <b>4H PERFORMANCE</b>")
    lines.append(f"  MFE: {fmt_pct(stats.avg_mfe_4h)} | MAE: {fmt_pct(stats.avg_mae_4h)}")
    lines.append(f"  Win Rate: {fmt_win(stats.win_rate_4h)}")
    lines.append("")

    lines.append("⏱️ <b>24H PERFORMANCE</b>")
    lines.append(f"  MFE: {fmt_pct(stats.avg_mfe_24h)} | MAE: {fmt_pct(stats.avg_mae_24h)}")
    lines.append(f"  Win Rate: {fmt_win(stats.win_rate_24h)}")
    lines.append("")

    lines.append("ℹ️ <i>MFE = Max Favorable Excursion (best move in signal direction)</i>")
    lines.append("ℹ️ <i>MAE = Max Adverse Excursion (worst move against signal)</i>")
    lines.append("")
    lines.append("#HOPE #weekly #performance")

    return "\n".join(lines)


def get_signal_publisher() -> SignalPublisher:
    """Get singleton publisher instance."""
    global _publisher_instance
    if "_publisher_instance" not in globals():
        _publisher_instance = SignalPublisher()
    return _publisher_instance


# Alias for TZ v1.0 compatibility
TelegramSignals = SignalPublisher


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test formatting (without actual publishing)
    from core.market_intel import MarketIntel

    intel = MarketIntel()
    snapshot = intel.get_snapshot()
    signals = intel.get_trading_signals()

    publisher = SignalPublisher()

    print("=== MARKET SNAPSHOT MESSAGE ===")
    print(publisher.format_market_snapshot(
        snapshot.tickers,
        snapshot.fear_greed_index,
        snapshot.news,
    ))

    print("\n=== TRADING SIGNALS MESSAGE ===")
    print(publisher.format_trading_signals(signals))
