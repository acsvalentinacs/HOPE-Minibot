# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T22:00:00Z
# Purpose: Historical data loader for backtesting (CSV + Binance API)
# Security: Fail-closed on invalid data, no side effects
# === END SIGNATURE ===
"""
Historical Data Loader for Backtesting.

Provides utilities to load OHLCV data from:
- CSV files (Binance export format)
- Binance API (via KlinesProvider)
- Synthetic data generation (for testing)

All loaders are fail-closed: return None on invalid/missing data.
"""
from __future__ import annotations

import csv
import logging
import time
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import Optional, List, Dict, Any, Union

import numpy as np

from core.market.klines_provider import KlinesResult, KlinesProvider, get_klines_provider

logger = logging.getLogger(__name__)

# Valid timeframes (Binance format)
VALID_TIMEFRAMES = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"}

# Timeframe to milliseconds
TIMEFRAME_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "3d": 259_200_000,
    "1w": 604_800_000,
    "1M": 2_592_000_000,  # Approximate
}


@dataclass
class DataValidation:
    """Validation result for klines data."""
    is_valid: bool
    candle_count: int
    missing_candles: int
    duplicate_candles: int
    warnings: List[str]
    errors: List[str]


def validate_klines(
    klines: KlinesResult,
    timeframe: str,
    min_candles: int = 50,
) -> DataValidation:
    """
    Validate klines data integrity.

    Checks:
    - Minimum candle count
    - Arrays same length
    - No NaN/Inf values
    - Monotonic timestamps
    - Expected time gaps (based on timeframe)
    - OHLC logic (high >= low, etc.)

    Args:
        klines: KlinesResult to validate
        timeframe: Expected timeframe (for gap detection)
        min_candles: Minimum required candles

    Returns:
        DataValidation with results and warnings
    """
    warnings = []
    errors = []

    # Basic checks
    if klines is None:
        return DataValidation(
            is_valid=False,
            candle_count=0,
            missing_candles=0,
            duplicate_candles=0,
            warnings=[],
            errors=["klines is None"],
        )

    n = klines.candle_count

    # Minimum candles
    if n < min_candles:
        errors.append(f"Insufficient candles: {n} < {min_candles}")
        return DataValidation(
            is_valid=False,
            candle_count=n,
            missing_candles=0,
            duplicate_candles=0,
            warnings=warnings,
            errors=errors,
        )

    # Array lengths
    arrays = [klines.opens, klines.highs, klines.lows, klines.closes, klines.volumes, klines.candle_times]
    lengths = [len(a) for a in arrays]
    if len(set(lengths)) != 1:
        errors.append(f"Array length mismatch: {lengths}")
        return DataValidation(
            is_valid=False,
            candle_count=n,
            missing_candles=0,
            duplicate_candles=0,
            warnings=warnings,
            errors=errors,
        )

    # Check for NaN/Inf
    for name, arr in [("opens", klines.opens), ("highs", klines.highs),
                      ("lows", klines.lows), ("closes", klines.closes),
                      ("volumes", klines.volumes)]:
        nan_count = np.sum(~np.isfinite(arr))
        if nan_count > 0:
            errors.append(f"{name} contains {nan_count} NaN/Inf values")

    # OHLC logic
    invalid_high = np.sum(klines.highs < klines.lows)
    if invalid_high > 0:
        errors.append(f"{invalid_high} candles with high < low")

    invalid_open_high = np.sum(klines.opens > klines.highs)
    invalid_open_low = np.sum(klines.opens < klines.lows)
    invalid_close_high = np.sum(klines.closes > klines.highs)
    invalid_close_low = np.sum(klines.closes < klines.lows)

    ohlc_errors = invalid_open_high + invalid_open_low + invalid_close_high + invalid_close_low
    if ohlc_errors > 0:
        warnings.append(f"{ohlc_errors} OHLC logic violations")

    # Timestamp analysis
    times = klines.candle_times
    diffs = np.diff(times)

    # Monotonic check
    non_monotonic = np.sum(diffs <= 0)
    if non_monotonic > 0:
        errors.append(f"{non_monotonic} non-monotonic timestamps")

    # Gap detection
    expected_gap = TIMEFRAME_MS.get(timeframe, 900_000) / 1000  # Convert to seconds
    tolerance = expected_gap * 0.1  # 10% tolerance

    missing = 0
    duplicates = 0

    for diff in diffs:
        if diff < expected_gap - tolerance:
            duplicates += 1
        elif diff > expected_gap + tolerance:
            # Estimate missing candles
            missing += int((diff / expected_gap) - 1)

    if missing > 0:
        warnings.append(f"Estimated {missing} missing candles (gaps detected)")
    if duplicates > 0:
        warnings.append(f"{duplicates} potential duplicate timestamps")

    is_valid = len(errors) == 0

    return DataValidation(
        is_valid=is_valid,
        candle_count=n,
        missing_candles=missing,
        duplicate_candles=duplicates,
        warnings=warnings,
        errors=errors,
    )


def load_csv(
    path: Union[str, Path],
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
) -> Optional[KlinesResult]:
    """
    Load OHLCV data from CSV file.

    Expected CSV format (Binance export):
    open_time,open,high,low,close,volume,close_time,quote_volume,...

    Args:
        path: Path to CSV file
        symbol: Symbol name (for result metadata)
        timeframe: Timeframe (for result metadata)

    Returns:
        KlinesResult or None if loading fails
    """
    path = Path(path)

    if not path.exists():
        logger.error("CSV file not found: %s", path)
        return None

    try:
        candle_times = []
        opens = []
        highs = []
        lows = []
        closes = []
        volumes = []

        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            # Check required columns
            required = {"open_time", "open", "high", "low", "close", "volume"}
            if reader.fieldnames is None:
                logger.error("CSV has no headers")
                return None

            headers = set(reader.fieldnames)
            missing = required - headers
            if missing:
                logger.error("CSV missing columns: %s", missing)
                return None

            for row in reader:
                try:
                    # open_time is in milliseconds
                    open_time_ms = int(row["open_time"])
                    candle_times.append(open_time_ms / 1000)  # Convert to seconds

                    opens.append(float(row["open"]))
                    highs.append(float(row["high"]))
                    lows.append(float(row["low"]))
                    closes.append(float(row["close"]))
                    volumes.append(float(row["volume"]))
                except (ValueError, KeyError) as e:
                    logger.warning("Skipping invalid row: %s", e)
                    continue

        if len(closes) == 0:
            logger.error("No valid data in CSV")
            return None

        return KlinesResult(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=time.time(),
            opens=np.array(opens, dtype=np.float64),
            highs=np.array(highs, dtype=np.float64),
            lows=np.array(lows, dtype=np.float64),
            closes=np.array(closes, dtype=np.float64),
            volumes=np.array(volumes, dtype=np.float64),
            candle_times=np.array(candle_times, dtype=np.float64),
            is_stale=False,
            from_cache=False,
        )

    except Exception as e:
        logger.error("Failed to load CSV: %s", e)
        return None


def generate_synthetic_klines(
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    candle_count: int = 500,
    start_price: float = 50000.0,
    volatility: float = 0.02,
    trend: float = 0.0001,
    seed: Optional[int] = None,
) -> KlinesResult:
    """
    Generate synthetic OHLCV data for testing.

    Args:
        symbol: Symbol name
        timeframe: Timeframe
        candle_count: Number of candles to generate
        start_price: Starting price
        volatility: Price volatility (std dev per candle)
        trend: Drift per candle (positive = uptrend)
        seed: Random seed for reproducibility

    Returns:
        KlinesResult with synthetic data
    """
    if seed is not None:
        np.random.seed(seed)

    n = candle_count

    # Generate returns with drift
    returns = np.random.randn(n) * volatility + trend

    # Cumulative price series
    log_prices = np.cumsum(returns)
    prices = start_price * np.exp(log_prices)

    # Generate OHLC from close prices
    closes = prices

    # Opens = previous close (shifted)
    opens = np.roll(closes, 1)
    opens[0] = start_price

    # High/Low with random wicks
    wick_up = np.abs(np.random.randn(n)) * volatility * start_price * 0.5
    wick_down = np.abs(np.random.randn(n)) * volatility * start_price * 0.5

    highs = np.maximum(opens, closes) + wick_up
    lows = np.minimum(opens, closes) - wick_down

    # Volume with some randomness
    base_volume = 1000.0
    volumes = base_volume * (1 + np.abs(np.random.randn(n)) * 0.5)

    # Timestamps
    interval_ms = TIMEFRAME_MS.get(timeframe, 900_000)
    start_time = time.time() - (n * interval_ms / 1000)
    candle_times = np.array([start_time + i * interval_ms / 1000 for i in range(n)])

    return KlinesResult(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=time.time(),
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
        candle_times=candle_times,
        is_stale=False,
        from_cache=False,
    )


async def fetch_historical_klines(
    symbol: str,
    timeframe: str,
    start_date: date,
    end_date: date,
    provider: Optional[KlinesProvider] = None,
) -> Optional[KlinesResult]:
    """
    Fetch historical klines from Binance API.

    Note: This uses the existing KlinesProvider which fetches
    recent data. For true historical data, multiple requests
    may be needed with pagination.

    Args:
        symbol: Trading pair (e.g., "BTCUSDT")
        timeframe: Candle interval (e.g., "15m")
        start_date: Start date
        end_date: End date
        provider: Optional KlinesProvider instance

    Returns:
        KlinesResult or None if fetch fails
    """
    if provider is None:
        provider = get_klines_provider()

    # Calculate required candles
    interval_ms = TIMEFRAME_MS.get(timeframe, 900_000)
    start_ts = datetime.combine(start_date, datetime.min.time()).timestamp()
    end_ts = datetime.combine(end_date, datetime.max.time()).timestamp()

    duration_ms = (end_ts - start_ts) * 1000
    required_candles = int(duration_ms / interval_ms)

    # Binance limit is 1000 per request
    if required_candles > 1000:
        logger.warning(
            "Requested %d candles, but API limit is 1000. "
            "Only fetching most recent 1000.",
            required_candles
        )
        required_candles = 1000

    # Fetch via provider
    result = provider.get_klines(
        symbol=symbol,
        timeframe=timeframe,
        limit=required_candles,
        force_refresh=True,
    )

    if result is None:
        logger.error("Failed to fetch klines for %s", symbol)
        return None

    return result


class DataLoader:
    """
    Unified data loader for backtesting.

    Supports:
    - CSV files
    - Synthetic data generation
    - Binance API (via KlinesProvider)
    """

    def __init__(self, klines_provider: Optional[KlinesProvider] = None):
        """
        Initialize DataLoader.

        Args:
            klines_provider: Optional KlinesProvider for API access
        """
        self._provider = klines_provider or get_klines_provider()

    def load_csv(
        self,
        path: Union[str, Path],
        symbol: str = "BTCUSDT",
        timeframe: str = "15m",
    ) -> Optional[KlinesResult]:
        """Load from CSV file."""
        return load_csv(path, symbol, timeframe)

    def generate_synthetic(
        self,
        symbol: str = "BTCUSDT",
        timeframe: str = "15m",
        candle_count: int = 500,
        start_price: float = 50000.0,
        volatility: float = 0.02,
        trend: float = 0.0001,
        seed: Optional[int] = None,
    ) -> KlinesResult:
        """Generate synthetic data for testing."""
        return generate_synthetic_klines(
            symbol=symbol,
            timeframe=timeframe,
            candle_count=candle_count,
            start_price=start_price,
            volatility=volatility,
            trend=trend,
            seed=seed,
        )

    def fetch_recent(
        self,
        symbol: str,
        timeframe: str = "15m",
        limit: int = 500,
    ) -> Optional[KlinesResult]:
        """Fetch recent klines from API."""
        return self._provider.get_klines(
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
        )

    def validate(
        self,
        klines: KlinesResult,
        timeframe: str = "15m",
        min_candles: int = 50,
    ) -> DataValidation:
        """Validate klines data."""
        return validate_klines(klines, timeframe, min_candles)


# Convenience instances
_default_loader: Optional[DataLoader] = None


def get_data_loader() -> DataLoader:
    """Get singleton DataLoader instance."""
    global _default_loader
    if _default_loader is None:
        _default_loader = DataLoader()
    return _default_loader
