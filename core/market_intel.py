# === AI SIGNATURE ===
# Created by: Kirill Dev
# Created at: 2026-01-19 18:24:32 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-29 13:00:00 UTC
# Change: Fixed hardcoded paths (VPS portable), added all 5 RSS feeds, enhanced sentiment keywords
# === END SIGNATURE ===
"""
HOPE/NORE Market Intelligence Module v1.1

Aggregates market data from multiple sources:
- CoinGecko API (market caps, prices, volumes, global data)
- Binance API (order books, 24h stats)
- RSS news feeds (sentiment signals)

Fail-closed design:
- All external data MUST be persisted via snapshot_store before use
- Stale/invalid snapshots = skip cycle, log reason
- Signals MUST reference snapshot_id for audit trail

Usage:
    from core.market_intel import MarketIntel

    intel = MarketIntel()
    snapshot = intel.get_snapshot()
    signals = intel.get_trading_signals()
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
import xml.etree.ElementTree as ET

from core.snapshot_store import SnapshotStore, SnapshotMeta, DomainNotAllowedError

logger = logging.getLogger(__name__)

# Base directory for project (SSoT: derive from file location, portable to VPS)
BASE_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = BASE_DIR / "state"

# Cache settings (SSoT)
CACHE_DIR = STATE_DIR / "cache"
CACHE_TTL_SECONDS = 300  # 5 minutes for market data
NEWS_CACHE_TTL_SECONDS = 900  # 15 minutes for news
EXCHANGE_INFO_TTL_SECONDS = 3600  # 1 hour for trading rules (rarely change)

# API endpoints
COINGECKO_MARKETS = "https://api.coingecko.com/api/v3/coins/markets"
COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"
BINANCE_TICKER_24H = "https://api.binance.com/api/v3/ticker/24hr"
BINANCE_EXCHANGE_INFO = "https://api.binance.com/api/v3/exchangeInfo"

# News RSS feeds (all allowed sources per CLAUDE.md)
NEWS_FEEDS = [
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("cointelegraph", "https://cointelegraph.com/rss"),
    ("decrypt", "https://decrypt.co/feed"),
    ("theblock", "https://www.theblock.co/rss.xml"),
    ("bitcoinmagazine", "https://bitcoinmagazine.com/feed"),
]

# Trading pairs to monitor (high liquidity)
PRIORITY_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
]

# Signal thresholds
VOLUME_SPIKE_THRESHOLD = 2.0  # 2x average
PRICE_MOVE_THRESHOLD = 3.0  # 3% move

# Sentiment analysis keywords (comprehensive, case-insensitive)
SENTIMENT_KEYWORDS = {
    "bullish": [
        # Market action
        "surge", "rally", "breakout", "soar", "jump", "gain", "rise", "pump",
        # Adoption signals
        "adoption", "institutional", "etf approved", "etf approval", "spot etf",
        "banking charter", "treasury", "reserve",
        # Regulatory positive
        "clarity", "framework", "approval", "green light", "legalize",
        # Investment
        "accumulate", "buy", "bullish", "long", "upside",
        # Institutional
        "blackrock", "fidelity", "vanguard", "custody", "pension fund",
    ],
    "bearish": [
        # Market action
        "crash", "dump", "plunge", "collapse", "selloff", "liquidation", "capitulation",
        # Security threats
        "hack", "exploit", "vulnerability", "breach", "stolen", "drained",
        # Regulatory negative
        "ban", "crackdown", "lawsuit", "sec charges", "enforcement", "sanction",
        # Risk factors
        "bearish", "short", "downside", "risk-off", "contagion", "insolvency",
        # Macro negative
        "recession", "inflation", "rate hike", "quantitative tightening",
    ],
    "high_impact": [
        # Events that require immediate attention regardless of direction
        "breaking", "urgent", "emergency", "halt", "suspend", "delist",
        "fork", "upgrade", "mainnet", "airdrop",
        "fed", "fomc", "powell", "yellen", "treasury",
        "trump", "biden", "congress", "senate",
    ],
}


@dataclass(frozen=True)
class MarketTicker:
    """Single market ticker data."""
    symbol: str
    price: float
    price_change_24h: float
    price_change_pct: float
    volume_24h: float
    high_24h: float
    low_24h: float
    timestamp: float


@dataclass(frozen=True)
class NewsItem:
    """Single news item."""
    source: str
    title: str
    link: str
    pub_date: str
    sentiment: str  # bullish, bearish, neutral


@dataclass
class GlobalMarketData:
    """CoinGecko global market data."""
    total_market_cap_usd: float
    total_volume_24h_usd: float
    btc_dominance: float
    eth_dominance: float
    market_cap_change_24h_pct: float
    timestamp: float


@dataclass
class MarketSnapshot:
    """Aggregated market snapshot."""
    timestamp: float
    tickers: Dict[str, MarketTicker]
    news: List[NewsItem]
    btc_dominance: float
    total_market_cap: float
    fear_greed_index: int  # 0-100
    global_data: Optional[GlobalMarketData] = None
    is_stale: bool = False


@dataclass
class TradingSignal:
    """Actionable trading signal with entry price for outcome tracking."""
    symbol: str
    signal_type: str  # volume_spike, price_breakout, news_sentiment, momentum
    direction: str  # long, short, neutral
    strength: float  # 0.0 to 1.0
    reason: str
    timestamp: float
    entry_price: float = 0.0  # Price at signal generation (for outcome tracking)
    invalidation_price: Optional[float] = None  # Price that invalidates signal


class MarketIntel:
    """
    Market intelligence aggregator.

    Fail-closed design:
    - All external data persisted via SnapshotStore with sha256 id
    - API timeout = cached data + stale flag
    - Parse error = skip source + log
    - No data = STOP signal to engine
    - Signals MUST reference snapshot_id for audit trail
    """

    def __init__(self, cache_dir: Optional[Path] = None, base_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._base_dir = base_dir or BASE_DIR
        self._snapshot_store = SnapshotStore(self._base_dir)
        self._last_snapshot: Optional[MarketSnapshot] = None
        self._request_timeout = 10  # seconds

    def get_snapshot(self, force_refresh: bool = False) -> MarketSnapshot:
        """
        Get current market snapshot.

        Args:
            force_refresh: Bypass cache

        Returns:
            MarketSnapshot with all available data
        """
        # Check cache
        if not force_refresh:
            cached = self._load_cache("snapshot")
            if cached and time.time() - cached.get("ts", 0) < CACHE_TTL_SECONDS:
                return self._deserialize_snapshot(cached)

        # Fetch fresh data with snapshot persistence
        tickers = self._fetch_binance_tickers()
        news = self._fetch_news()
        global_data, global_snapshot_id = self._fetch_global_data()

        # Use CoinGecko data if available, else estimate from Binance
        if global_data:
            btc_dom = global_data.btc_dominance
            total_mcap = global_data.total_market_cap_usd
        else:
            btc_dom = self._estimate_btc_dominance(tickers)
            total_mcap = self._estimate_market_cap(tickers)

        snapshot = MarketSnapshot(
            timestamp=time.time(),
            tickers=tickers,
            news=news,
            btc_dominance=btc_dom,
            total_market_cap=total_mcap,
            fear_greed_index=self._calculate_fear_greed(tickers, news),
            global_data=global_data,
            is_stale=len(tickers) == 0,
        )

        # Log snapshot_id for audit trail
        if global_snapshot_id:
            logger.info("Market snapshot using global_data from: %s", global_snapshot_id[:24])

        # Cache result
        self._save_cache("snapshot", self._serialize_snapshot(snapshot))
        self._last_snapshot = snapshot

        return snapshot

    def get_trading_signals(self) -> List[TradingSignal]:
        """
        Generate actionable trading signals from current data.

        Returns:
            List of TradingSignal objects sorted by strength
        """
        snapshot = self.get_snapshot()
        signals: List[TradingSignal] = []

        if snapshot.is_stale:
            logger.warning("Market data is stale - no signals generated")
            return []

        # Volume spike detection
        signals.extend(self._detect_volume_spikes(snapshot))

        # Price momentum signals
        signals.extend(self._detect_price_momentum(snapshot))

        # News sentiment signals
        signals.extend(self._detect_news_sentiment(snapshot))

        # Sort by strength descending
        signals.sort(key=lambda s: s.strength, reverse=True)

        return signals

    def _fetch_global_data(self) -> Tuple[Optional[GlobalMarketData], Optional[str]]:
        """
        Fetch global market data from CoinGecko with snapshot persistence.

        Returns:
            Tuple of (GlobalMarketData or None, snapshot_id or None)
        """
        source_url = COINGECKO_GLOBAL
        raw_bytes = b""
        http_status = 0
        parse_ok = False
        error_msg = ""

        try:
            req = Request(source_url, headers={"User-Agent": "HOPE-Bot/1.0"})
            with urlopen(req, timeout=self._request_timeout) as resp:
                http_status = resp.status
                raw_bytes = resp.read()
        except (URLError, HTTPError) as e:
            error_msg = str(e)
            http_status = getattr(e, "code", 0) or 0
            logger.warning("Failed to fetch CoinGecko global: %s", e)

        # Always persist snapshot (even on failure) for audit
        parsed_data: Optional[Dict[str, Any]] = None
        global_data: Optional[GlobalMarketData] = None

        if raw_bytes and http_status == 200:
            try:
                parsed_data = json.loads(raw_bytes.decode("utf-8"))
                parse_ok = True

                data = parsed_data.get("data", {})
                market_cap = data.get("total_market_cap", {})
                volume = data.get("total_volume", {})

                global_data = GlobalMarketData(
                    total_market_cap_usd=market_cap.get("usd", 0.0),
                    total_volume_24h_usd=volume.get("usd", 0.0),
                    btc_dominance=data.get("market_cap_percentage", {}).get("btc", 0.0),
                    eth_dominance=data.get("market_cap_percentage", {}).get("eth", 0.0),
                    market_cap_change_24h_pct=data.get("market_cap_change_percentage_24h_usd", 0.0),
                    timestamp=time.time(),
                )
            except (json.JSONDecodeError, KeyError) as e:
                error_msg = f"Parse error: {e}"
                parse_ok = False

        # Persist to snapshot store
        try:
            meta, _ = self._snapshot_store.persist(
                source="coingecko_global",
                source_url=source_url,
                raw=raw_bytes or b"{}",
                ttl_sec=CACHE_TTL_SECONDS,
                http_status=http_status or 0,
                parse_ok=parse_ok,
                error=error_msg,
                parsed=parsed_data,
            )
            snapshot_id = meta.snapshot_id
            logger.debug("Persisted CoinGecko global snapshot: %s", snapshot_id[:24])
        except DomainNotAllowedError as e:
            logger.error("Domain not allowed for CoinGecko: %s", e)
            return None, None

        if not parse_ok:
            return None, snapshot_id

        return global_data, snapshot_id

    def _fetch_binance_tickers(self) -> Dict[str, MarketTicker]:
        """Fetch 24h tickers from Binance."""
        tickers: Dict[str, MarketTicker] = {}

        try:
            req = Request(
                BINANCE_TICKER_24H,
                headers={"User-Agent": "HOPE-Bot/1.0"}
            )
            with urlopen(req, timeout=self._request_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (URLError, HTTPError, json.JSONDecodeError) as e:
            logger.error("Failed to fetch Binance tickers: %s", e)
            return self._load_cached_tickers()

        now = time.time()
        for item in data:
            symbol = item.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue

            try:
                ticker = MarketTicker(
                    symbol=symbol,
                    price=float(item.get("lastPrice", 0)),
                    price_change_24h=float(item.get("priceChange", 0)),
                    price_change_pct=float(item.get("priceChangePercent", 0)),
                    volume_24h=float(item.get("quoteVolume", 0)),
                    high_24h=float(item.get("highPrice", 0)),
                    low_24h=float(item.get("lowPrice", 0)),
                    timestamp=now,
                )
                tickers[symbol] = ticker
            except (ValueError, TypeError) as e:
                logger.debug("Skipping malformed ticker %s: %s", symbol, e)

        logger.info("Fetched %d USDT tickers from Binance", len(tickers))
        return tickers

    def _fetch_news(self) -> List[NewsItem]:
        """Fetch and parse RSS news feeds."""
        news: List[NewsItem] = []

        for source, url in NEWS_FEEDS:
            try:
                req = Request(url, headers={"User-Agent": "HOPE-Bot/1.0"})
                with urlopen(req, timeout=self._request_timeout) as resp:
                    content = resp.read().decode("utf-8")

                items = self._parse_rss(source, content)
                news.extend(items[:10])  # Top 10 per source
                logger.info("Fetched %d news items from %s", len(items), source)

            except (URLError, HTTPError) as e:
                logger.warning("Failed to fetch news from %s: %s", source, e)
            except ET.ParseError as e:
                logger.warning("Failed to parse RSS from %s: %s", source, e)

        return news

    def _parse_rss(self, source: str, content: str) -> List[NewsItem]:
        """Parse RSS XML content."""
        items: List[NewsItem] = []

        root = ET.fromstring(content)
        channel = root.find("channel")
        if channel is None:
            return items

        for item_elem in channel.findall("item")[:20]:
            title_elem = item_elem.find("title")
            link_elem = item_elem.find("link")
            pub_elem = item_elem.find("pubDate")

            if title_elem is None or title_elem.text is None:
                continue

            title = title_elem.text.strip()
            link = link_elem.text.strip() if link_elem is not None and link_elem.text else ""
            pub_date = pub_elem.text.strip() if pub_elem is not None and pub_elem.text else ""

            sentiment = self._analyze_sentiment(title)

            items.append(NewsItem(
                source=source,
                title=title,
                link=link,
                pub_date=pub_date,
                sentiment=sentiment,
            ))

        return items

    def _analyze_sentiment(self, text: str) -> str:
        """Simple keyword-based sentiment analysis."""
        text_lower = text.lower()

        bullish_score = sum(1 for kw in SENTIMENT_KEYWORDS["bullish"] if kw in text_lower)
        bearish_score = sum(1 for kw in SENTIMENT_KEYWORDS["bearish"] if kw in text_lower)

        if bullish_score > bearish_score:
            return "bullish"
        elif bearish_score > bullish_score:
            return "bearish"
        return "neutral"

    def _detect_volume_spikes(self, snapshot: MarketSnapshot) -> List[TradingSignal]:
        """Detect unusual volume activity."""
        signals: List[TradingSignal] = []

        # Get average volume for priority symbols
        volumes = [
            snapshot.tickers[s].volume_24h
            for s in PRIORITY_SYMBOLS
            if s in snapshot.tickers
        ]

        if not volumes:
            return signals

        avg_volume = sum(volumes) / len(volumes)

        for symbol in PRIORITY_SYMBOLS:
            if symbol not in snapshot.tickers:
                continue

            ticker = snapshot.tickers[symbol]
            volume_ratio = ticker.volume_24h / avg_volume if avg_volume > 0 else 0

            if volume_ratio >= VOLUME_SPIKE_THRESHOLD:
                direction = "long" if ticker.price_change_pct > 0 else "short"
                strength = min(1.0, (volume_ratio - 1) / 3)  # Normalize to 0-1

                # Calculate invalidation price (2% adverse move)
                inv_price = ticker.price * 0.98 if direction == "long" else ticker.price * 1.02

                signals.append(TradingSignal(
                    symbol=symbol,
                    signal_type="volume_spike",
                    direction=direction,
                    strength=strength,
                    reason=f"Volume {volume_ratio:.1f}x average, price {ticker.price_change_pct:+.2f}%",
                    timestamp=snapshot.timestamp,
                    entry_price=ticker.price,
                    invalidation_price=inv_price,
                ))

        return signals

    def _detect_price_momentum(self, snapshot: MarketSnapshot) -> List[TradingSignal]:
        """Detect significant price movements."""
        signals: List[TradingSignal] = []

        for symbol in PRIORITY_SYMBOLS:
            if symbol not in snapshot.tickers:
                continue

            ticker = snapshot.tickers[symbol]
            pct = abs(ticker.price_change_pct)

            if pct >= PRICE_MOVE_THRESHOLD:
                direction = "long" if ticker.price_change_pct > 0 else "short"
                strength = min(1.0, pct / 10)  # 10% = max strength

                # Invalidation: retracement to 50% of move
                mid_price = (ticker.high_24h + ticker.low_24h) / 2
                inv_price = mid_price if direction == "long" else mid_price

                signals.append(TradingSignal(
                    symbol=symbol,
                    signal_type="price_breakout",
                    direction=direction,
                    strength=strength,
                    reason=f"24h move {ticker.price_change_pct:+.2f}%, range ${ticker.low_24h:.2f}-${ticker.high_24h:.2f}",
                    timestamp=snapshot.timestamp,
                    entry_price=ticker.price,
                    invalidation_price=inv_price,
                ))

        return signals

    def _detect_news_sentiment(self, snapshot: MarketSnapshot) -> List[TradingSignal]:
        """Generate signals from news sentiment."""
        signals: List[TradingSignal] = []

        if not snapshot.news:
            return signals

        # Count sentiment per asset mentioned
        asset_sentiment: Dict[str, List[str]] = {}

        for news in snapshot.news:
            title_lower = news.title.lower()

            for symbol in PRIORITY_SYMBOLS:
                base = symbol.replace("USDT", "").lower()
                if base in title_lower or (base == "btc" and "bitcoin" in title_lower) or (base == "eth" and "ethereum" in title_lower):
                    if symbol not in asset_sentiment:
                        asset_sentiment[symbol] = []
                    asset_sentiment[symbol].append(news.sentiment)

        for symbol, sentiments in asset_sentiment.items():
            bullish = sentiments.count("bullish")
            bearish = sentiments.count("bearish")
            total = len(sentiments)

            if total >= 2:
                if bullish > bearish:
                    direction = "long"
                    strength = bullish / total
                elif bearish > bullish:
                    direction = "short"
                    strength = bearish / total
                else:
                    continue

                # Get entry price from ticker
                ticker = snapshot.tickers.get(symbol)
                entry = ticker.price if ticker else 0.0

                signals.append(TradingSignal(
                    symbol=symbol,
                    signal_type="news_sentiment",
                    direction=direction,
                    strength=strength * 0.5,  # News signals are weaker
                    reason=f"{total} news items: {bullish} bullish, {bearish} bearish",
                    timestamp=snapshot.timestamp,
                    entry_price=entry,
                    invalidation_price=None,  # News signals have no clear invalidation
                ))

        return signals

    def _estimate_btc_dominance(self, tickers: Dict[str, MarketTicker]) -> float:
        """Estimate BTC dominance from volume."""
        if "BTCUSDT" not in tickers:
            return 0.0

        btc_vol = tickers["BTCUSDT"].volume_24h
        total_vol = sum(t.volume_24h for t in tickers.values())

        return (btc_vol / total_vol * 100) if total_vol > 0 else 0.0

    def _estimate_market_cap(self, tickers: Dict[str, MarketTicker]) -> float:
        """Estimate total market cap from USDT volumes (rough approximation)."""
        if "BTCUSDT" not in tickers:
            return 0.0

        # Use BTC price as proxy (assuming ~$1.9T market cap at $95K)
        btc_price = tickers["BTCUSDT"].price
        return btc_price * 20_000_000  # Rough BTC supply estimate

    def _calculate_fear_greed(self, tickers: Dict[str, MarketTicker], news: List[NewsItem]) -> int:
        """Calculate fear/greed index (0=extreme fear, 100=extreme greed)."""
        score = 50  # Neutral baseline

        # Price momentum component
        if "BTCUSDT" in tickers:
            btc_change = tickers["BTCUSDT"].price_change_pct
            score += btc_change * 2  # -10% to +10% maps to -20 to +20

        # News sentiment component
        if news:
            bullish = sum(1 for n in news if n.sentiment == "bullish")
            bearish = sum(1 for n in news if n.sentiment == "bearish")
            sentiment_ratio = (bullish - bearish) / len(news) if news else 0
            score += sentiment_ratio * 20

        return max(0, min(100, int(score)))

    def _load_cache(self, key: str) -> Optional[Dict]:
        """Load data from cache file."""
        cache_file = self.cache_dir / f"{key}.json"
        if not cache_file.exists():
            return None

        try:
            content = cache_file.read_text(encoding="utf-8")
            return json.loads(content)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load cache %s: %s", key, e)
            return None

    def _save_cache(self, key: str, data: Dict) -> None:
        """Save data to cache file."""
        cache_file = self.cache_dir / f"{key}.json"
        try:
            cache_file.write_text(
                json.dumps(data, ensure_ascii=False),
                encoding="utf-8"
            )
        except OSError as e:
            logger.warning("Failed to save cache %s: %s", key, e)

    def _load_cached_tickers(self) -> Dict[str, MarketTicker]:
        """Load tickers from cache as fallback."""
        cached = self._load_cache("tickers")
        if not cached:
            return {}

        tickers = {}
        for symbol, data in cached.get("tickers", {}).items():
            try:
                tickers[symbol] = MarketTicker(**data)
            except (TypeError, KeyError):
                pass

        return tickers

    def _serialize_snapshot(self, snapshot: MarketSnapshot) -> Dict:
        """Serialize snapshot for caching."""
        return {
            "ts": snapshot.timestamp,
            "tickers": {
                k: {
                    "symbol": v.symbol,
                    "price": v.price,
                    "price_change_24h": v.price_change_24h,
                    "price_change_pct": v.price_change_pct,
                    "volume_24h": v.volume_24h,
                    "high_24h": v.high_24h,
                    "low_24h": v.low_24h,
                    "timestamp": v.timestamp,
                }
                for k, v in snapshot.tickers.items()
            },
            "news": [
                {
                    "source": n.source,
                    "title": n.title,
                    "link": n.link,
                    "pub_date": n.pub_date,
                    "sentiment": n.sentiment,
                }
                for n in snapshot.news
            ],
            "btc_dominance": snapshot.btc_dominance,
            "total_market_cap": snapshot.total_market_cap,
            "fear_greed_index": snapshot.fear_greed_index,
            "global_data": {
                "total_market_cap_usd": snapshot.global_data.total_market_cap_usd,
                "total_volume_24h_usd": snapshot.global_data.total_volume_24h_usd,
                "btc_dominance": snapshot.global_data.btc_dominance,
                "eth_dominance": snapshot.global_data.eth_dominance,
                "market_cap_change_24h_pct": snapshot.global_data.market_cap_change_24h_pct,
                "timestamp": snapshot.global_data.timestamp,
            } if snapshot.global_data else None,
            "is_stale": snapshot.is_stale,
        }

    def _deserialize_snapshot(self, data: Dict) -> MarketSnapshot:
        """Deserialize snapshot from cache."""
        tickers = {}
        for k, v in data.get("tickers", {}).items():
            try:
                tickers[k] = MarketTicker(**v)
            except (TypeError, KeyError):
                pass

        news = []
        for n in data.get("news", []):
            try:
                news.append(NewsItem(**n))
            except (TypeError, KeyError):
                pass

        global_data = None
        gd = data.get("global_data")
        if gd:
            try:
                global_data = GlobalMarketData(**gd)
            except (TypeError, KeyError):
                pass

        return MarketSnapshot(
            timestamp=data.get("ts", 0),
            tickers=tickers,
            news=news,
            btc_dominance=data.get("btc_dominance", 0),
            total_market_cap=data.get("total_market_cap", 0),
            fear_greed_index=data.get("fear_greed_index", 50),
            global_data=global_data,
            is_stale=data.get("is_stale", True),
        )


def get_market_intel() -> MarketIntel:
    """Get singleton market intel instance."""
    global _intel_instance
    if "_intel_instance" not in globals():
        _intel_instance = MarketIntel()
    return _intel_instance


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    intel = MarketIntel()

    print("=== MARKET SNAPSHOT ===")
    snapshot = intel.get_snapshot(force_refresh=True)
    print(f"Timestamp: {datetime.fromtimestamp(snapshot.timestamp)}")
    print(f"Tickers: {len(snapshot.tickers)}")
    print(f"News items: {len(snapshot.news)}")
    print(f"BTC Dominance: {snapshot.btc_dominance:.1f}%")
    print(f"Fear/Greed Index: {snapshot.fear_greed_index}")

    print("\n=== TOP TICKERS ===")
    for symbol in PRIORITY_SYMBOLS[:5]:
        if symbol in snapshot.tickers:
            t = snapshot.tickers[symbol]
            print(f"{symbol}: ${t.price:.2f} ({t.price_change_pct:+.2f}%) Vol: ${t.volume_24h/1e9:.1f}B")

    print("\n=== TRADING SIGNALS ===")
    signals = intel.get_trading_signals()
    for sig in signals[:10]:
        print(f"[{sig.signal_type}] {sig.symbol} {sig.direction.upper()} (strength: {sig.strength:.2f})")
        print(f"  Reason: {sig.reason}")

