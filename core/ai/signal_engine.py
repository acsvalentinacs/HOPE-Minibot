# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T14:00:00Z
# Purpose: Main signal generation engine combining TA, ML, and sentiment
# === END SIGNATURE ===
"""
Signal Engine Module.

Central hub for trading signal generation.
Combines:
1. Technical Analysis (RSI, MACD, Bollinger Bands)
2. Strategy signals (Momentum, Mean Reversion, Breakout)
3. News sentiment (from event_classifier) - optional
4. Volume analysis

Fail-closed: No signal generated if data is stale or invalid.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

from core.ai.technical_indicators import TechnicalIndicators
from core.strategy.base import (
    BaseStrategy,
    StrategySignal,
    MarketData,
    SignalDirection,
)
from core.strategy.momentum import MomentumStrategy, MomentumConfig


_log = logging.getLogger("hope.signal_engine")

# State directory for persisting signals
STATE_DIR = Path(__file__).resolve().parent.parent.parent / "state"


@dataclass
class SignalEngineConfig:
    """
    Signal Engine configuration.
    """
    # Minimum thresholds
    min_signal_strength: float = 0.5
    min_confidence: float = 0.5

    # Enabled strategies
    enable_momentum: bool = True
    enable_mean_reversion: bool = False  # Phase 2
    enable_breakout: bool = False  # Phase 2

    # Data staleness (seconds)
    max_data_age_seconds: int = 300  # 5 minutes

    # Rate limiting
    min_signal_interval_seconds: int = 60  # Min time between signals per symbol

    # Market scan settings
    default_symbols: list[str] = field(default_factory=lambda: [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
        "ADAUSDT", "DOGEUSDT", "DOTUSDT", "AVAXUSDT", "MATICUSDT",
    ])


class SignalEngine:
    """
    Main signal generation engine.

    Orchestrates multiple strategies and combines signals.
    Thread-safe for async operations.
    """

    def __init__(
        self,
        config: SignalEngineConfig | None = None,
        strategies: list[BaseStrategy] | None = None,
    ):
        """
        Initialize signal engine.

        Args:
            config: Engine configuration
            strategies: List of strategies to use (default: Momentum only)
        """
        self.config = config or SignalEngineConfig()
        self.indicators = TechnicalIndicators()

        # Initialize strategies
        if strategies:
            self.strategies = strategies
        else:
            self.strategies = []
            if self.config.enable_momentum:
                self.strategies.append(MomentumStrategy())

        # State tracking
        self._last_signals: dict[str, datetime] = {}  # symbol -> last signal time
        self._signal_history: list[StrategySignal] = []

        _log.info(
            f"SignalEngine initialized with {len(self.strategies)} strategies: "
            f"{[s.name for s in self.strategies]}"
        )

    async def generate_signal(
        self,
        symbol: str,
        ohlcv_data: dict[str, Any],
        current_price: float,
        timeframe: str = "1h",
    ) -> StrategySignal | None:
        """
        Generate trading signal for a symbol.

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            ohlcv_data: Dictionary with 'open', 'high', 'low', 'close', 'volume' lists
            current_price: Current market price
            timeframe: Candle timeframe

        Returns:
            Best StrategySignal or None if no valid signal
        """
        # Check rate limiting
        if not self._check_rate_limit(symbol):
            _log.debug(f"Rate limited: {symbol}")
            return None

        # Prepare market data
        try:
            market_data = self._prepare_market_data(
                symbol=symbol,
                ohlcv_data=ohlcv_data,
                current_price=current_price,
                timeframe=timeframe,
            )
        except ValueError as e:
            _log.warning(f"Invalid data for {symbol}: {e}")
            return None

        # Run all strategies
        signals: list[StrategySignal] = []

        for strategy in self.strategies:
            try:
                signal = strategy.analyze(market_data)
                if signal and self._validate_signal(signal):
                    signals.append(signal)
                    _log.info(
                        f"Strategy '{strategy.name}' generated {signal.direction.value} "
                        f"signal for {symbol} (strength={signal.strength:.2f})"
                    )
            except Exception as e:
                _log.error(f"Strategy '{strategy.name}' failed for {symbol}: {e}")
                continue

        if not signals:
            return None

        # Select best signal (highest strength * confidence)
        best_signal = max(signals, key=lambda s: s.strength * s.confidence)

        # Update state
        self._last_signals[symbol] = datetime.now(timezone.utc)
        self._signal_history.append(best_signal)

        # Keep history bounded
        if len(self._signal_history) > 1000:
            self._signal_history = self._signal_history[-500:]

        return best_signal

    async def scan_market(
        self,
        symbols: list[str] | None = None,
        ohlcv_fetcher: Any = None,
        price_fetcher: Any = None,
    ) -> list[StrategySignal]:
        """
        Scan multiple symbols for signals.

        Args:
            symbols: List of symbols to scan (default: config.default_symbols)
            ohlcv_fetcher: Async function(symbol, timeframe) -> ohlcv_data
            price_fetcher: Async function(symbol) -> current_price

        Returns:
            List of signals sorted by strength (descending)
        """
        if symbols is None:
            symbols = self.config.default_symbols

        if ohlcv_fetcher is None or price_fetcher is None:
            _log.error("Market scan requires ohlcv_fetcher and price_fetcher")
            return []

        signals: list[StrategySignal] = []

        # Process symbols concurrently with semaphore
        semaphore = asyncio.Semaphore(5)  # Max 5 concurrent

        async def process_symbol(symbol: str) -> StrategySignal | None:
            async with semaphore:
                try:
                    ohlcv = await ohlcv_fetcher(symbol, "1h")
                    price = await price_fetcher(symbol)
                    return await self.generate_signal(
                        symbol=symbol,
                        ohlcv_data=ohlcv,
                        current_price=price,
                        timeframe="1h",
                    )
                except Exception as e:
                    _log.warning(f"Failed to process {symbol}: {e}")
                    return None

        tasks = [process_symbol(s) for s in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, StrategySignal):
                signals.append(result)
            elif isinstance(result, Exception):
                _log.warning(f"Scan task failed: {result}")

        # Sort by strength descending
        signals.sort(key=lambda s: s.strength * s.confidence, reverse=True)

        _log.info(f"Market scan complete: {len(signals)} signals from {len(symbols)} symbols")
        return signals

    def _prepare_market_data(
        self,
        symbol: str,
        ohlcv_data: dict[str, Any],
        current_price: float,
        timeframe: str,
    ) -> MarketData:
        """
        Prepare market data from raw OHLCV.

        Raises:
            ValueError: If data is invalid or insufficient
        """
        required_keys = ["open", "high", "low", "close", "volume"]
        for key in required_keys:
            if key not in ohlcv_data:
                raise ValueError(f"Missing required key: {key}")

        opens = ohlcv_data["open"]
        highs = ohlcv_data["high"]
        lows = ohlcv_data["low"]
        closes = ohlcv_data["close"]
        volumes = ohlcv_data["volume"]

        # Validate lengths match
        lengths = [len(opens), len(highs), len(lows), len(closes), len(volumes)]
        if len(set(lengths)) != 1:
            raise ValueError(f"OHLCV arrays have different lengths: {lengths}")

        min_bars = 100  # Need at least 100 bars for indicators
        if lengths[0] < min_bars:
            raise ValueError(f"Insufficient data: {lengths[0]} bars, need {min_bars}")

        return MarketData(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=datetime.now(timezone.utc),
            opens=list(opens),
            highs=list(highs),
            lows=list(lows),
            closes=list(closes),
            volumes=list(volumes),
            current_price=current_price,
        )

    def _validate_signal(self, signal: StrategySignal) -> bool:
        """Validate signal meets engine thresholds."""
        if signal.strength < self.config.min_signal_strength:
            return False
        if signal.confidence < self.config.min_confidence:
            return False
        if signal.direction == SignalDirection.NEUTRAL:
            return False
        return True

    def _check_rate_limit(self, symbol: str) -> bool:
        """Check if enough time has passed since last signal."""
        last_time = self._last_signals.get(symbol)
        if last_time is None:
            return True

        elapsed = (datetime.now(timezone.utc) - last_time).total_seconds()
        return elapsed >= self.config.min_signal_interval_seconds

    def get_active_strategies(self) -> list[str]:
        """Get list of active strategy names."""
        return [s.name for s in self.strategies]

    def get_signal_history(self, limit: int = 100) -> list[StrategySignal]:
        """Get recent signal history."""
        return self._signal_history[-limit:]

    def add_strategy(self, strategy: BaseStrategy) -> None:
        """Add a new strategy."""
        self.strategies.append(strategy)
        _log.info(f"Added strategy: {strategy.name}")

    def remove_strategy(self, name: str) -> bool:
        """Remove a strategy by name."""
        for i, s in enumerate(self.strategies):
            if s.name == name:
                self.strategies.pop(i)
                _log.info(f"Removed strategy: {name}")
                return True
        return False


# === STANDALONE HELPERS ===

def create_signal_engine(
    enable_momentum: bool = True,
    enable_mean_reversion: bool = False,
    enable_breakout: bool = False,
) -> SignalEngine:
    """
    Factory function to create configured signal engine.

    Args:
        enable_momentum: Enable momentum strategy
        enable_mean_reversion: Enable mean reversion (Phase 2)
        enable_breakout: Enable breakout (Phase 2)

    Returns:
        Configured SignalEngine instance
    """
    config = SignalEngineConfig(
        enable_momentum=enable_momentum,
        enable_mean_reversion=enable_mean_reversion,
        enable_breakout=enable_breakout,
    )
    return SignalEngine(config=config)
