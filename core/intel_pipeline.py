"""
Intel Pipeline - Market data and news analysis with snapshot evidence.

Implements fetch -> snapshot -> analyze -> persist flow with fail-closed semantics.
All outputs reference snapshot_id for audit trail.

Data sources:
- Binance: ticker/24hr (market data, top movers)
- RSS: coindesk, cointelegraph, decrypt, theblock (news)
- Announcements: binance support (listings, delistings)

Event classification:
- market: price moves, volume spikes
- regulation: SEC, bans, approvals, legal
- listing: exchange listings/delistings
- exploit: hacks, vulnerabilities
- macro: Fed rates, inflation, geopolitical
- institutional: ETF, corporate treasury

Usage:
    pipeline = IntelPipeline(BASE_DIR)
    result = await pipeline.scan_all()
    print(result.market_summary)
    print(result.news_items)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.data_fetcher import fetch_bytes, fetch_json, FetchError
from core.snapshot_store import SnapshotStore, SnapshotMeta

logger = logging.getLogger("intel")

TTL_MARKET_SEC = 300      # 5 min for market data
TTL_NEWS_SEC = 900        # 15 min for news
MIN_VOLUME_USD = 1_000_000  # Filter low volume pairs

BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/24hr"

RSS_FEEDS = {
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "cointelegraph": "https://cointelegraph.com/rss",
    "decrypt": "https://decrypt.co/feed",
    "theblock": "https://www.theblock.co/rss.xml",
}

EVENT_KEYWORDS = {
    "regulation": ["sec", "regulation", "ban", "legal", "lawsuit", "court", "approve", "reject"],
    "listing": ["list", "delist", "trading pair", "new token", "launches"],
    "exploit": ["hack", "exploit", "vulnerability", "rug pull", "drain", "stolen"],
    "macro": ["fed", "rate", "inflation", "gdp", "employment", "treasury"],
    "institutional": ["etf", "grayscale", "blackrock", "fidelity", "microstrategy", "tesla"],
}


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


@dataclass
class MarketMover:
    symbol: str
    price_change_pct: float
    volume_usd: float
    last_price: float


@dataclass
class NewsItem:
    title: str
    link: str
    source: str
    pub_date: Optional[str]
    event_type: str
    impact_score: float
    snapshot_id: str


SCHEMA_VERSION = "1.0.0"


@dataclass
class ScanResult:
    timestamp: float
    market_snapshot_id: str
    news_snapshot_ids: Dict[str, str]
    top_gainers: List[MarketMover]
    top_losers: List[MarketMover]
    top_volume: List[MarketMover]
    news_items: List[NewsItem]
    errors: List[str] = field(default_factory=list)
    partial: bool = False  # True if some sources failed but market data is valid

    def is_publishable(self) -> bool:
        """Check if result is valid for Telegram publication (fail-closed)."""
        return bool(self.market_snapshot_id) and not self.partial

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "timestamp": self.timestamp,
            "timestamp_iso": datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat(),
            "market_snapshot_id": self.market_snapshot_id,
            "news_snapshot_ids": self.news_snapshot_ids,
            "partial": self.partial,
            "publishable": self.is_publishable(),
            "top_gainers": [
                {"symbol": m.symbol, "change_pct": m.price_change_pct, "volume_usd": m.volume_usd}
                for m in self.top_gainers
            ],
            "top_losers": [
                {"symbol": m.symbol, "change_pct": m.price_change_pct, "volume_usd": m.volume_usd}
                for m in self.top_losers
            ],
            "top_volume": [
                {"symbol": m.symbol, "volume_usd": m.volume_usd, "change_pct": m.price_change_pct}
                for m in self.top_volume
            ],
            "news_items": [
                {
                    "title": n.title,
                    "link": n.link,
                    "source": n.source,
                    "event_type": n.event_type,
                    "impact_score": n.impact_score,
                    "snapshot_id": n.snapshot_id,
                }
                for n in self.news_items
            ],
            "errors": self.errors,
        }


class IntelPipeline:
    """
    Market intelligence pipeline with snapshot evidence.

    All data is fetched, validated, snapshotted, then analyzed.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir
        self._store = SnapshotStore(base_dir)
        self._state_dir = base_dir / "state"
        self._state_dir.mkdir(parents=True, exist_ok=True)

    async def fetch_market_data(self) -> Optional[tuple[SnapshotMeta, List[Dict[str, Any]]]]:
        """Fetch Binance 24hr ticker and persist snapshot."""
        try:
            result = await fetch_bytes(BINANCE_TICKER_URL, timeout_sec=20.0)
            data = json.loads(result.body.decode("utf-8"))

            if not isinstance(data, list):
                logger.error("FAIL-CLOSED: unexpected ticker format")
                return None

            meta, _ = self._store.persist(
                source="binance_ticker",
                source_url=BINANCE_TICKER_URL,
                raw=result.body,
                ttl_sec=TTL_MARKET_SEC,
                parsed={"count": len(data)},
            )

            return meta, data

        except FetchError as e:
            logger.error("Market fetch failed: %s", e)
            return None
        except json.JSONDecodeError as e:
            logger.error("Market JSON parse failed: %s", e)
            return None

    def analyze_market(self, data: List[Dict[str, Any]], top_n: int = 10) -> tuple[List[MarketMover], List[MarketMover], List[MarketMover]]:
        """
        Analyze market data: extract top gainers, losers, volume leaders.

        Filters:
        - USDT pairs only
        - Volume > MIN_VOLUME_USD
        """
        usdt_pairs = []

        for item in data:
            symbol = item.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue

            try:
                volume_usd = float(item.get("quoteVolume", 0))
                if volume_usd < MIN_VOLUME_USD:
                    continue

                usdt_pairs.append(MarketMover(
                    symbol=symbol,
                    price_change_pct=float(item.get("priceChangePercent", 0)),
                    volume_usd=volume_usd,
                    last_price=float(item.get("lastPrice", 0)),
                ))
            except (ValueError, TypeError):
                continue

        gainers = sorted(usdt_pairs, key=lambda x: x.price_change_pct, reverse=True)[:top_n]
        losers = sorted(usdt_pairs, key=lambda x: x.price_change_pct)[:top_n]
        volume = sorted(usdt_pairs, key=lambda x: x.volume_usd, reverse=True)[:top_n]

        return gainers, losers, volume

    async def fetch_news_feed(self, source: str, url: str) -> Optional[tuple[SnapshotMeta, List[Dict[str, str]]]]:
        """Fetch RSS feed and persist snapshot."""
        try:
            result = await fetch_bytes(url, timeout_sec=15.0)

            # Parse RSS/Atom
            items = self._parse_rss(result.body.decode("utf-8", errors="replace"))

            meta, _ = self._store.persist(
                source=f"news_{source}",
                source_url=url,
                raw=result.body,
                ttl_sec=TTL_NEWS_SEC,
                parsed={"count": len(items)},
            )

            return meta, items

        except FetchError as e:
            logger.warning("News fetch failed (%s): %s", source, e)
            return None
        except Exception as e:
            logger.warning("News parse failed (%s): %s", source, e)
            return None

    def _parse_rss(self, xml_text: str) -> List[Dict[str, str]]:
        """Parse RSS/Atom feed into list of items."""
        items = []

        try:
            root = ET.fromstring(xml_text)

            # RSS 2.0
            for item in root.findall(".//item"):
                title = item.findtext("title", "")
                link = item.findtext("link", "")
                pub_date = item.findtext("pubDate", "")
                items.append({"title": title, "link": link, "pub_date": pub_date})

            # Atom
            for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry"):
                title = entry.findtext("{http://www.w3.org/2005/Atom}title", "")
                link_el = entry.find("{http://www.w3.org/2005/Atom}link")
                link = link_el.get("href", "") if link_el is not None else ""
                pub_date = entry.findtext("{http://www.w3.org/2005/Atom}published", "")
                items.append({"title": title, "link": link, "pub_date": pub_date})

        except ET.ParseError:
            pass

        return items

    def classify_news(self, title: str) -> tuple[str, float]:
        """
        Classify news item by event type and compute impact score.

        Returns:
            (event_type, impact_score)
        """
        title_lower = title.lower()

        for event_type, keywords in EVENT_KEYWORDS.items():
            matches = sum(1 for kw in keywords if kw in title_lower)
            if matches > 0:
                impact = min(1.0, 0.3 + matches * 0.2)
                return event_type, round(impact, 2)

        return "market", 0.3

    async def scan_all(self, top_n: int = 10) -> ScanResult:
        """
        Full scan: market data + news feeds.

        Returns ScanResult with all data and snapshot references.
        """
        errors = []
        ts = time.time()

        # Fetch market data
        market_snapshot_id = ""
        gainers, losers, volume = [], [], []

        market_result = await self.fetch_market_data()
        if market_result:
            meta, data = market_result
            market_snapshot_id = meta.snapshot_id
            gainers, losers, volume = self.analyze_market(data, top_n)
        else:
            errors.append("market_fetch_failed")

        # Fetch news feeds concurrently
        news_snapshot_ids: Dict[str, str] = {}
        news_items: List[NewsItem] = []

        news_tasks = {
            source: self.fetch_news_feed(source, url)
            for source, url in RSS_FEEDS.items()
        }

        results = await asyncio.gather(*news_tasks.values(), return_exceptions=True)

        for source, result in zip(news_tasks.keys(), results):
            if isinstance(result, Exception):
                errors.append(f"news_{source}_failed")
                continue
            if result is None:
                errors.append(f"news_{source}_empty")
                continue

            meta, items = result
            news_snapshot_ids[source] = meta.snapshot_id

            for item in items[:20]:  # Limit per source
                event_type, impact = self.classify_news(item["title"])
                if impact >= 0.4:  # Only notable news
                    news_items.append(NewsItem(
                        title=item["title"],
                        link=item["link"],
                        source=source,
                        pub_date=item.get("pub_date"),
                        event_type=event_type,
                        impact_score=impact,
                        snapshot_id=meta.snapshot_id,
                    ))

        # Sort news by impact
        news_items.sort(key=lambda x: x.impact_score, reverse=True)

        # Determine if result is partial (some sources failed but market is valid)
        is_partial = bool(errors) and bool(market_snapshot_id)

        result = ScanResult(
            timestamp=ts,
            market_snapshot_id=market_snapshot_id,
            news_snapshot_ids=news_snapshot_ids,
            top_gainers=gainers,
            top_losers=losers,
            top_volume=volume,
            news_items=news_items[:30],  # Limit total
            errors=errors,
            partial=is_partial,
        )

        # Persist result atomically (even if partial, for debugging)
        self._persist_result(result)

        return result

    def _persist_result(self, result: ScanResult) -> Path:
        """Persist scan result to state/market_intel.json."""
        out_path = self._state_dir / "market_intel.json"
        _atomic_write(out_path, json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        logger.info("Persisted intel to %s", out_path)
        return out_path

    def get_latest_result(self) -> Optional[Dict[str, Any]]:
        """Load latest scan result."""
        path = self._state_dir / "market_intel.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
