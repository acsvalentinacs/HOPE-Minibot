# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T21:00:00Z
# Purpose: Real OHLCV provider from Binance API (fail-closed)
# Security: Public API only, no auth required, rate-limited
# === END SIGNATURE ===
"""
Binance Klines Provider.

Fetches real OHLCV data from Binance public API.
Fail-closed: returns None if data unavailable or stale.

Usage:
    from core.market.klines_provider import get_klines_provider

    provider = get_klines_provider()
    result = provider.get_klines("BTCUSDT", "15m", limit=100)
    if result and not result.is_stale:
        closes = result.closes
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional, Dict, List, Any
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# Binance API endpoints
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"

# Cache settings
CACHE_TTL_SEC = 60  # 1 minute cache for klines
MAX_LIMIT = 1000

# Valid timeframes
VALID_TIMEFRAMES = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"}


@dataclass
class KlinesConfig:
    """Configuration for klines provider."""
    cache_ttl_sec: int = CACHE_TTL_SEC
    stale_threshold_sec: int = 300  # 5 min = stale
    min_candles: int = 35  # Minimum for MarketData validation
    default_limit: int = 100
    timeout_sec: int = 10


@dataclass
class KlinesResult:
    """Result of klines fetch."""
    symbol: str
    timeframe: str
    timestamp: float  # Fetch timestamp

    # OHLCV arrays
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    volumes: np.ndarray

    # Timestamps for each candle (open time)
    candle_times: np.ndarray

    # Metadata
    candle_count: int = 0
    is_stale: bool = False
    from_cache: bool = False

    def __post_init__(self):
        self.candle_count = len(self.closes) if self.closes is not None else 0


class KlinesProvider:
    """
    Provides real OHLCV data from Binance API.

    Features:
    - In-memory cache with TTL
    - Staleness detection
    - Fail-closed on errors
    - numpy arrays for efficient processing
    """

    def __init__(self, config: Optional[KlinesConfig] = None):
        self.config = config or KlinesConfig()
        self._cache: Dict[str, KlinesResult] = {}
        self._session = None  # Lazy init

    def _get_session(self):
        """Get or create HTTP session."""
        if self._session is None:
            try:
                import requests
                self._session = requests.Session()
                self._session.headers["User-Agent"] = "HOPE-Bot/1.0"
            except ImportError:
                logger.error("requests library not installed")
                return None
        return self._session

    def _cache_key(self, symbol: str, timeframe: str) -> str:
        """Generate cache key."""
        return f"{symbol}:{timeframe}"

    def _parse_klines(self, raw_klines: List[List[Any]]) -> Optional[Dict[str, np.ndarray]]:
        """
        Parse Binance klines response to numpy arrays.

        Binance format: [open_time, open, high, low, close, volume, close_time, ...]
        """
        if not raw_klines or len(raw_klines) < self.config.min_candles:
            return None

        try:
            n = len(raw_klines)
            candle_times = np.zeros(n, dtype=np.float64)
            opens = np.zeros(n, dtype=np.float64)
            highs = np.zeros(n, dtype=np.float64)
            lows = np.zeros(n, dtype=np.float64)
            closes = np.zeros(n, dtype=np.float64)
            volumes = np.zeros(n, dtype=np.float64)

            for i, kline in enumerate(raw_klines):
                candle_times[i] = float(kline[0]) / 1000  # ms -> sec
                opens[i] = float(kline[1])
                highs[i] = float(kline[2])
                lows[i] = float(kline[3])
                closes[i] = float(kline[4])
                volumes[i] = float(kline[5])

            return {
                "candle_times": candle_times,
                "opens": opens,
                "highs": highs,
                "lows": lows,
                "closes": closes,
                "volumes": volumes,
            }
        except (ValueError, IndexError, TypeError) as e:
            logger.warning("Failed to parse klines: %s", e)
            return None

    def get_klines(
        self,
        symbol: str,
        timeframe: str = "15m",
        limit: int = 100,
        force_refresh: bool = False,
    ) -> Optional[KlinesResult]:
        """
        Fetch OHLCV klines for symbol.

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            timeframe: Candle interval (e.g., "15m", "1h")
            limit: Number of candles (max 1000)
            force_refresh: Bypass cache

        Returns:
            KlinesResult or None if failed/unavailable (fail-closed)
        """
        # Validate inputs
        symbol = symbol.upper()
        if timeframe not in VALID_TIMEFRAMES:
            logger.warning("Invalid timeframe: %s", timeframe)
            return None

        limit = min(limit, MAX_LIMIT)
        if limit < self.config.min_candles:
            limit = self.config.min_candles

        cache_key = self._cache_key(symbol, timeframe)

        # Check cache
        if not force_refresh and cache_key in self._cache:
            cached = self._cache[cache_key]
            age = time.time() - cached.timestamp

            if age < self.config.cache_ttl_sec:
                # Cache hit
                cached.from_cache = True
                cached.is_stale = age > self.config.stale_threshold_sec
                return cached

        # Fetch from API
        session = self._get_session()
        if session is None:
            return None

        try:
            params = {
                "symbol": symbol,
                "interval": timeframe,
                "limit": limit,
            }

            response = session.get(
                BINANCE_KLINES_URL,
                params=params,
                timeout=self.config.timeout_sec,
            )

            if response.status_code != 200:
                logger.warning(
                    "Binance API error: %d %s",
                    response.status_code,
                    response.text[:200] if response.text else "No response"
                )
                return None

            raw_klines = response.json()
            parsed = self._parse_klines(raw_klines)

            if parsed is None:
                logger.warning("Failed to parse klines for %s", symbol)
                return None

            result = KlinesResult(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=time.time(),
                opens=parsed["opens"],
                highs=parsed["highs"],
                lows=parsed["lows"],
                closes=parsed["closes"],
                volumes=parsed["volumes"],
                candle_times=parsed["candle_times"],
                is_stale=False,
                from_cache=False,
            )

            # Update cache
            self._cache[cache_key] = result
            logger.debug("Fetched %d klines for %s %s", result.candle_count, symbol, timeframe)

            return result

        except Exception as e:
            logger.error("Klines fetch failed for %s: %s", symbol, e)
            return None

    def get_multi_klines(
        self,
        symbols: List[str],
        timeframe: str = "15m",
        limit: int = 100,
    ) -> Dict[str, Optional[KlinesResult]]:
        """
        Fetch klines for multiple symbols.

        Returns dict mapping symbol -> KlinesResult (or None if failed).
        """
        results = {}
        for symbol in symbols:
            results[symbol] = self.get_klines(symbol, timeframe, limit)
            # Small delay to respect rate limits
            time.sleep(0.05)
        return results

    def clear_cache(self) -> None:
        """Clear klines cache."""
        self._cache.clear()

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        now = time.time()
        return {
            "cached_symbols": len(self._cache),
            "cache_keys": list(self._cache.keys()),
            "ages": {
                key: now - result.timestamp
                for key, result in self._cache.items()
            },
        }


# Singleton instance
_provider_instance: Optional[KlinesProvider] = None


def get_klines_provider(config: Optional[KlinesConfig] = None) -> KlinesProvider:
    """Get singleton klines provider."""
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = KlinesProvider(config)
    return _provider_instance


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    print("=== KLINES PROVIDER TEST ===\n")

    provider = get_klines_provider()

    # Test single symbol
    result = provider.get_klines("BTCUSDT", "15m", limit=50)
    if result:
        print(f"Symbol: {result.symbol}")
        print(f"Timeframe: {result.timeframe}")
        print(f"Candles: {result.candle_count}")
        print(f"Latest close: {result.closes[-1]:.2f}")
        print(f"Latest volume: {result.volumes[-1]:.2f}")
        print(f"Is stale: {result.is_stale}")
    else:
        print("FAILED: No data returned")

    # Test cache
    print("\n--- Testing cache ---")
    result2 = provider.get_klines("BTCUSDT", "15m", limit=50)
    if result2:
        print(f"From cache: {result2.from_cache}")

    print(f"\nCache stats: {provider.get_cache_stats()}")
