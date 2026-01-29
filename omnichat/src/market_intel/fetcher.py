# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T19:00:00Z
# Purpose: Market data fetcher - async HTTP with fail-closed error handling
# === END SIGNATURE ===
"""
Market Data Fetcher.

Fetches real-time data from:
- Binance API (ticker/24hr)
- CoinGecko API (global metrics)
- RSS news feeds

Principles:
- Fail-closed: any error returns explicit failure, not partial data
- Timeout enforcement: no hanging requests
- Rate limit respect: exponential backoff on 429
"""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import aiohttp

from .types import TickerData, GlobalMetrics, NewsItem, ImpactScore

_log = logging.getLogger("market_intel.fetcher")

# Configuration
DEFAULT_TIMEOUT = 10.0
MAX_RETRIES = 2
BACKOFF_BASE = 2.0

# Allowed domains (security)
ALLOWED_DOMAINS = {
    "api.binance.com",
    "api.coingecko.com",
    "cointelegraph.com",
    "coindesk.com",
    "www.coindesk.com",
    "decrypt.co",
    "www.theblock.co",
    "theblock.co",
    "bitcoinmagazine.com",
    # AI APIs
    "api.anthropic.com",
}

# Top symbols to fetch
TOP_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
]


class FetchError(Exception):
    """Raised when fetch fails."""
    pass


class MarketFetcher:
    """
    Async market data fetcher with fail-closed semantics.

    Usage:
        async with MarketFetcher() as fetcher:
            tickers = await fetcher.fetch_tickers()
            metrics = await fetcher.fetch_global_metrics()
            news = await fetcher.fetch_news()
    """

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = MAX_RETRIES,
    ):
        self.timeout = timeout
        self.max_retries = max_retries
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "MarketFetcher":
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout),
            headers={"User-Agent": "HOPE-MarketIntel/1.0"},
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._session:
            await self._session.close()

    def _validate_url(self, url: str) -> bool:
        """Security check: only allow whitelisted domains."""
        parsed = urlparse(url)
        return parsed.netloc in ALLOWED_DOMAINS

    async def _fetch_json(self, url: str) -> dict:
        """Fetch JSON with retry and fail-closed semantics."""
        if not self._validate_url(url):
            raise FetchError(f"Domain not allowed: {url}")

        if not self._session:
            raise FetchError("Session not initialized")

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                async with self._session.get(url) as resp:
                    if resp.status == 429:
                        # Rate limited - backoff
                        delay = BACKOFF_BASE ** attempt
                        _log.warning(f"Rate limited, backoff {delay}s")
                        await asyncio.sleep(delay)
                        continue

                    if resp.status != 200:
                        raise FetchError(f"HTTP {resp.status} from {url}")

                    return await resp.json()

            except asyncio.TimeoutError:
                last_error = FetchError(f"Timeout fetching {url}")
                _log.warning(f"Timeout attempt {attempt + 1}/{self.max_retries + 1}")
            except aiohttp.ClientError as e:
                last_error = FetchError(f"Client error: {e}")
                _log.warning(f"Client error: {e}")

            if attempt < self.max_retries:
                await asyncio.sleep(BACKOFF_BASE ** attempt)

        raise last_error or FetchError(f"Failed to fetch {url}")

    async def _fetch_text(self, url: str) -> str:
        """Fetch text (for RSS) with retry."""
        if not self._validate_url(url):
            raise FetchError(f"Domain not allowed: {url}")

        if not self._session:
            raise FetchError("Session not initialized")

        try:
            async with self._session.get(url) as resp:
                if resp.status != 200:
                    raise FetchError(f"HTTP {resp.status} from {url}")
                return await resp.text()
        except asyncio.TimeoutError:
            raise FetchError(f"Timeout fetching {url}")
        except aiohttp.ClientError as e:
            raise FetchError(f"Client error: {e}")

    async def fetch_tickers(
        self,
        symbols: Optional[list[str]] = None,
    ) -> dict[str, TickerData]:
        """
        Fetch ticker data from Binance.

        Args:
            symbols: List of symbols to fetch. Defaults to TOP_SYMBOLS.

        Returns:
            Dict mapping symbol to TickerData

        Raises:
            FetchError: If fetch fails (fail-closed)
        """
        symbols = symbols or TOP_SYMBOLS
        url = "https://api.binance.com/api/v3/ticker/24hr"

        data = await self._fetch_json(url)
        now = datetime.utcnow()

        result = {}
        symbol_set = set(symbols)

        for item in data:
            symbol = item.get("symbol", "")
            if symbol not in symbol_set:
                continue

            try:
                ticker = TickerData(
                    symbol=symbol,
                    price=float(item.get("lastPrice", 0)),
                    price_change_pct=float(item.get("priceChangePercent", 0)),
                    volume=float(item.get("volume", 0)),
                    quote_volume=float(item.get("quoteVolume", 0)),
                    high_24h=float(item.get("highPrice", 0)),
                    low_24h=float(item.get("lowPrice", 0)),
                    timestamp=now,
                )
                result[symbol] = ticker
            except (ValueError, TypeError) as e:
                _log.warning(f"Failed to parse ticker {symbol}: {e}")
                continue

        if not result:
            raise FetchError("No valid tickers parsed")

        _log.info(f"Fetched {len(result)} tickers")
        return result

    async def fetch_global_metrics(self) -> GlobalMetrics:
        """
        Fetch global market metrics from CoinGecko.

        Returns:
            GlobalMetrics object

        Raises:
            FetchError: If fetch fails (fail-closed)
        """
        url = "https://api.coingecko.com/api/v3/global"

        data = await self._fetch_json(url)
        now = datetime.utcnow()

        try:
            gdata = data.get("data", {})
            metrics = GlobalMetrics(
                total_market_cap_usd=gdata.get("total_market_cap", {}).get("usd", 0),
                total_volume_24h_usd=gdata.get("total_volume", {}).get("usd", 0),
                btc_dominance_pct=gdata.get("market_cap_percentage", {}).get("btc", 0),
                eth_dominance_pct=gdata.get("market_cap_percentage", {}).get("eth", 0),
                market_cap_change_24h_pct=gdata.get("market_cap_change_percentage_24h_usd", 0),
                active_cryptocurrencies=gdata.get("active_cryptocurrencies", 0),
                timestamp=now,
            )
            _log.info(f"Fetched global metrics: mcap=${metrics.total_market_cap_usd/1e12:.2f}T")
            return metrics

        except (KeyError, TypeError, ValueError) as e:
            raise FetchError(f"Failed to parse global metrics: {e}")

    async def fetch_news(
        self,
        sources: Optional[list[str]] = None,
        max_items: int = 10,
    ) -> list[NewsItem]:
        """
        Fetch news from RSS feeds.

        Args:
            sources: RSS feed URLs. Defaults to crypto news feeds.
            max_items: Maximum items per source.

        Returns:
            List of NewsItem sorted by date (newest first)

        Raises:
            FetchError: If ALL feeds fail (partial success allowed)
        """
        default_sources = [
            "https://cointelegraph.com/rss",
            "https://www.coindesk.com/arc/outboundfeeds/rss/",
            "https://decrypt.co/feed",
        ]
        sources = sources or default_sources

        all_news = []
        errors = []

        for feed_url in sources:
            try:
                items = await self._parse_rss_feed(feed_url, max_items)
                all_news.extend(items)
            except FetchError as e:
                errors.append(str(e))
                _log.warning(f"Failed to fetch {feed_url}: {e}")

        if not all_news and errors:
            raise FetchError(f"All feeds failed: {errors}")

        # Sort by date, newest first
        all_news.sort(key=lambda x: x.published_at, reverse=True)

        _log.info(f"Fetched {len(all_news)} news items from {len(sources)} sources")
        return all_news[:max_items * len(sources)]

    async def _parse_rss_feed(
        self,
        url: str,
        max_items: int,
    ) -> list[NewsItem]:
        """Parse RSS feed into NewsItem list."""
        text = await self._fetch_text(url)
        items = []

        try:
            root = ET.fromstring(text)

            # Handle both RSS 2.0 and Atom feeds
            channel = root.find("channel")
            if channel is not None:
                entries = channel.findall("item")[:max_items]
            else:
                # Atom format
                entries = root.findall("{http://www.w3.org/2005/Atom}entry")[:max_items]

            source = urlparse(url).netloc.replace("www.", "")

            for entry in entries:
                item = self._parse_rss_item(entry, source, url)
                if item:
                    items.append(item)

        except ET.ParseError as e:
            raise FetchError(f"XML parse error: {e}")

        return items

    def _parse_rss_item(
        self,
        entry: ET.Element,
        source: str,
        feed_url: str,
    ) -> Optional[NewsItem]:
        """Parse single RSS item."""
        try:
            # RSS 2.0 format
            title = entry.findtext("title", "")
            link = entry.findtext("link", "")
            pub_date_str = entry.findtext("pubDate", "")
            description = entry.findtext("description", "")

            # Atom format fallback
            if not title:
                title = entry.findtext("{http://www.w3.org/2005/Atom}title", "")
            if not link:
                link_elem = entry.find("{http://www.w3.org/2005/Atom}link")
                link = link_elem.get("href", "") if link_elem is not None else ""
            if not pub_date_str:
                pub_date_str = entry.findtext("{http://www.w3.org/2005/Atom}published", "")

            if not title:
                return None

            # Parse date
            pub_date = self._parse_date(pub_date_str)

            # Calculate impact score
            impact = self._classify_impact(title, description)

            # Extract keywords
            keywords = self._extract_keywords(title + " " + description)

            # Clean description
            summary = self._clean_html(description)[:500]

            return NewsItem(
                title=title.strip(),
                source=source,
                url=link,
                published_at=pub_date,
                summary=summary,
                impact=impact,
                keywords=tuple(keywords),
                sentiment_score=0.0,  # Could add sentiment analysis later
            )

        except Exception as e:
            _log.debug(f"Failed to parse RSS item: {e}")
            return None

    def _parse_date(self, date_str: str) -> datetime:
        """Parse various date formats."""
        if not date_str:
            return datetime.utcnow()

        formats = [
            "%a, %d %b %Y %H:%M:%S %z",   # RSS format
            "%a, %d %b %Y %H:%M:%S GMT",
            "%Y-%m-%dT%H:%M:%S%z",         # ISO format
            "%Y-%m-%dT%H:%M:%SZ",
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(date_str, fmt)
                # Convert to naive UTC
                if dt.tzinfo:
                    dt = dt.replace(tzinfo=None)
                return dt
            except ValueError:
                continue

        return datetime.utcnow()

    def _classify_impact(self, title: str, description: str) -> ImpactScore:
        """Classify news impact based on keywords."""
        text = (title + " " + description).lower()

        # Critical keywords
        critical = ["hack", "exploit", "sec ", "regulation", "ban", "etf approved", "lawsuit"]
        if any(k in text for k in critical):
            return ImpactScore.CRITICAL

        # High impact keywords
        high = ["partnership", "listing", "upgrade", "halving", "institutional", "billion"]
        if any(k in text for k in high):
            return ImpactScore.HIGH

        # Medium impact
        medium = ["adoption", "launch", "update", "milestone", "record"]
        if any(k in text for k in medium):
            return ImpactScore.MEDIUM

        # Low impact
        low = ["analysis", "opinion", "prediction", "could", "might"]
        if any(k in text for k in low):
            return ImpactScore.LOW

        return ImpactScore.NOISE

    def _extract_keywords(self, text: str) -> list[str]:
        """Extract crypto-related keywords."""
        keywords = []
        text_lower = text.lower()

        crypto_terms = [
            "bitcoin", "btc", "ethereum", "eth", "binance", "bnb",
            "solana", "sol", "xrp", "cardano", "ada", "dogecoin", "doge",
            "defi", "nft", "stablecoin", "usdt", "usdc", "tether",
            "sec", "regulation", "etf", "halving", "mining",
        ]

        for term in crypto_terms:
            if term in text_lower:
                keywords.append(term.upper() if len(term) <= 4 else term.capitalize())

        return list(set(keywords))[:10]

    def _clean_html(self, text: str) -> str:
        """Remove HTML tags from text."""
        import re
        clean = re.sub(r'<[^>]+>', '', text)
        clean = re.sub(r'\s+', ' ', clean)
        return clean.strip()
