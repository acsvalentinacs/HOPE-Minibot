# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T19:00:00Z
# Purpose: Market Intelligence main module - atomic persistence, fail-closed
# === END SIGNATURE ===
"""
Market Intelligence Main Module.

Orchestrates data fetching, analysis, and persistence.
Follows HOPE protocols:
- Atomic writes (temp → fsync → replace)
- sha256 content verification
- Fail-closed error handling
- JSONL audit trail
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .types import MarketSnapshot, TickerData, GlobalMetrics, NewsItem, MarketAlert
from .fetcher import MarketFetcher, FetchError
from .analyzer import NewsAnalyzer

_log = logging.getLogger("market_intel.intel")

# Paths
STATE_DIR = Path(__file__).parent.parent.parent.parent / "state"
SNAPSHOT_PATH = STATE_DIR / "market_intel.json"
HISTORY_PATH = STATE_DIR / "market_intel_history.jsonl"


def _atomic_write(path: Path, content: str) -> None:
    """Atomic write: temp → fsync → replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")

    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp, path)


def _append_jsonl(path: Path, record: dict) -> None:
    """Append record to JSONL file (atomic append)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"

    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


class MarketIntel:
    """
    Market Intelligence Engine.

    Provides real-time market data, news, and analysis.

    Usage:
        intel = MarketIntel()
        snapshot = await intel.get_snapshot()

        # Or with caching
        snapshot = await intel.get_snapshot(max_age_seconds=60)
    """

    def __init__(
        self,
        cache_path: Optional[Path] = None,
        history_path: Optional[Path] = None,
    ):
        self.cache_path = cache_path or SNAPSHOT_PATH
        self.history_path = history_path or HISTORY_PATH
        self.analyzer = NewsAnalyzer()
        self._last_snapshot: Optional[MarketSnapshot] = None

    async def get_snapshot(
        self,
        max_age_seconds: int = 300,
        force_refresh: bool = False,
    ) -> MarketSnapshot:
        """
        Get market snapshot, using cache if fresh.

        Args:
            max_age_seconds: Max cache age (default 5 min)
            force_refresh: Ignore cache and fetch fresh data

        Returns:
            MarketSnapshot with current data

        Raises:
            FetchError: If data fetch fails (fail-closed)
        """
        # Check cache
        if not force_refresh:
            cached = self._load_cache()
            if cached:
                age = (datetime.utcnow() - cached.timestamp).total_seconds()
                if age < max_age_seconds:
                    _log.info(f"Using cached snapshot (age={age:.0f}s)")
                    return cached

        # Fetch fresh data
        return await self._fetch_snapshot()

    async def _fetch_snapshot(self) -> MarketSnapshot:
        """Fetch complete market snapshot."""
        start_time = time.time()
        errors = []
        source_urls = []

        tickers: dict[str, TickerData] = {}
        metrics: Optional[GlobalMetrics] = None
        news: list[NewsItem] = []

        async with MarketFetcher() as fetcher:
            # Fetch tickers
            try:
                tickers = await fetcher.fetch_tickers()
                source_urls.append("https://api.binance.com/api/v3/ticker/24hr")
            except FetchError as e:
                errors.append(f"Tickers: {e}")
                _log.error(f"Failed to fetch tickers: {e}")

            # Fetch global metrics
            try:
                metrics = await fetcher.fetch_global_metrics()
                source_urls.append("https://api.coingecko.com/api/v3/global")
            except FetchError as e:
                errors.append(f"Global metrics: {e}")
                _log.warning(f"Failed to fetch global metrics: {e}")

            # Fetch news
            try:
                news = await fetcher.fetch_news(max_items=10)
                source_urls.extend([
                    "https://cointelegraph.com/rss",
                    "https://www.coindesk.com/arc/outboundfeeds/rss/",
                ])
            except FetchError as e:
                errors.append(f"News: {e}")
                _log.warning(f"Failed to fetch news: {e}")

        # Fail-closed: must have at least tickers
        if not tickers:
            raise FetchError("Critical: No ticker data available")

        fetch_duration = int((time.time() - start_time) * 1000)
        timestamp = datetime.utcnow()

        # Build snapshot data for ID computation
        snapshot_data = {
            "timestamp": timestamp.isoformat(),
            "tickers": {k: v.to_dict() for k, v in tickers.items()},
            "metrics": metrics.to_dict() if metrics else None,
            "news_count": len(news),
        }
        snapshot_id = MarketSnapshot.compute_id(snapshot_data)

        snapshot = MarketSnapshot(
            snapshot_id=snapshot_id,
            timestamp=timestamp,
            tickers=tickers,
            global_metrics=metrics,
            news=news,
            source_urls=source_urls,
            fetch_duration_ms=fetch_duration,
            errors=errors,
        )

        # Save to cache
        self._save_cache(snapshot)

        # Append to history
        self._append_history(snapshot)

        _log.info(
            f"Snapshot {snapshot_id}: "
            f"{len(tickers)} tickers, {len(news)} news, "
            f"{fetch_duration}ms"
        )

        self._last_snapshot = snapshot
        return snapshot

    def _load_cache(self) -> Optional[MarketSnapshot]:
        """Load snapshot from cache file."""
        if not self.cache_path.exists():
            return None

        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            return self._dict_to_snapshot(data)

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            _log.warning(f"Cache corrupted, ignoring: {e}")
            return None

    def _save_cache(self, snapshot: MarketSnapshot) -> None:
        """Save snapshot to cache file atomically."""
        content = snapshot.to_json()
        _atomic_write(self.cache_path, content)

    def _append_history(self, snapshot: MarketSnapshot) -> None:
        """Append snapshot summary to history JSONL."""
        record = {
            "snapshot_id": snapshot.snapshot_id,
            "timestamp": snapshot.timestamp.isoformat(),
            "btc_price": snapshot.btc_price,
            "eth_price": snapshot.eth_price,
            "market_cap": snapshot.global_metrics.total_market_cap_usd if snapshot.global_metrics else 0,
            "news_count": len(snapshot.news),
            "errors": len(snapshot.errors),
        }
        _append_jsonl(self.history_path, record)

    def _dict_to_snapshot(self, data: dict) -> MarketSnapshot:
        """Convert dict to MarketSnapshot."""
        tickers = {}
        for symbol, t in data.get("tickers", {}).items():
            tickers[symbol] = TickerData(
                symbol=t["symbol"],
                price=t["price"],
                price_change_pct=t["price_change_pct"],
                volume=t["volume"],
                quote_volume=t["quote_volume"],
                high_24h=t["high_24h"],
                low_24h=t["low_24h"],
                timestamp=datetime.fromisoformat(t["timestamp"]),
            )

        metrics = None
        if data.get("global_metrics"):
            m = data["global_metrics"]
            metrics = GlobalMetrics(
                total_market_cap_usd=m["total_market_cap_usd"],
                total_volume_24h_usd=m["total_volume_24h_usd"],
                btc_dominance_pct=m["btc_dominance_pct"],
                eth_dominance_pct=m["eth_dominance_pct"],
                market_cap_change_24h_pct=m["market_cap_change_24h_pct"],
                active_cryptocurrencies=m["active_cryptocurrencies"],
                timestamp=datetime.fromisoformat(m["timestamp"]),
            )

        news = []
        for n in data.get("news", []):
            from .types import ImpactScore
            news.append(NewsItem(
                title=n["title"],
                source=n["source"],
                url=n["url"],
                published_at=datetime.fromisoformat(n["published_at"]),
                summary=n["summary"],
                impact=ImpactScore[n["impact"]],
                keywords=tuple(n.get("keywords", [])),
                sentiment_score=n.get("sentiment_score", 0.0),
            ))

        return MarketSnapshot(
            snapshot_id=data["snapshot_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            tickers=tickers,
            global_metrics=metrics,
            news=news,
            source_urls=data.get("source_urls", []),
            fetch_duration_ms=data.get("fetch_duration_ms", 0),
            errors=data.get("errors", []),
        )

    def get_alerts(self, snapshot: MarketSnapshot) -> list[MarketAlert]:
        """Generate alerts from snapshot."""
        alerts = []

        # News alerts
        news_alerts = self.analyzer.analyze_news(
            snapshot.news,
            snapshot.tickers,
            snapshot.global_metrics,
        )
        alerts.extend(news_alerts)

        # Market alerts
        market_alerts = self.analyzer.generate_market_alerts(
            snapshot.tickers,
            snapshot.global_metrics,
        )
        alerts.extend(market_alerts)

        return alerts

    def get_summary(self, snapshot: MarketSnapshot) -> dict:
        """Get market summary from snapshot."""
        return self.analyzer.summarize_sentiment(
            snapshot.news,
            snapshot.tickers,
            snapshot.global_metrics,
        )

    def format_snapshot(self, snapshot: MarketSnapshot) -> str:
        """Format snapshot for display."""
        lines = []
        lines.append("=" * 50)
        lines.append(f"📊 MARKET INTEL - {snapshot.timestamp.strftime('%Y-%m-%d %H:%M')} UTC")
        lines.append(f"ID: {snapshot.snapshot_id}")
        lines.append("=" * 50)

        # Prices
        lines.append("\n💰 TOP ASSETS:")
        for symbol in ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]:
            ticker = snapshot.tickers.get(symbol)
            if ticker:
                arrow = "🟢" if ticker.is_bullish else "🔴"
                lines.append(
                    f"  {arrow} {symbol}: ${ticker.price:,.2f} "
                    f"({ticker.price_change_pct:+.2f}%)"
                )

        # Global metrics
        if snapshot.global_metrics:
            m = snapshot.global_metrics
            lines.append(f"\n🌍 GLOBAL:")
            lines.append(f"  Market Cap: ${m.total_market_cap_usd/1e12:.2f}T")
            lines.append(f"  24h Volume: ${m.total_volume_24h_usd/1e9:.1f}B")
            lines.append(f"  BTC Dom: {m.btc_dominance_pct:.1f}%")
            lines.append(f"  Change 24h: {m.market_cap_change_24h_pct:+.2f}%")
            lines.append(f"  Sentiment: {m.sentiment.value}")

        # News
        if snapshot.news:
            lines.append(f"\n📰 TOP NEWS ({len(snapshot.news)} items):")
            for i, news in enumerate(snapshot.news[:5]):
                impact = "🔴" if news.is_market_moving else "🔵"
                lines.append(f"  {impact} [{news.source}] {news.title[:60]}...")

        # Errors
        if snapshot.errors:
            lines.append(f"\n⚠️ WARNINGS: {len(snapshot.errors)}")
            for err in snapshot.errors[:3]:
                lines.append(f"  - {err}")

        lines.append("\n" + "=" * 50)
        return "\n".join(lines)


async def fetch_market_snapshot(
    max_age_seconds: int = 300,
) -> MarketSnapshot:
    """
    Convenience function to fetch market snapshot.

    Usage:
        snapshot = await fetch_market_snapshot()
    """
    intel = MarketIntel()
    return await intel.get_snapshot(max_age_seconds=max_age_seconds)
