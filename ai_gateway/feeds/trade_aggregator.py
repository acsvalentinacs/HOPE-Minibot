# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 21:15:00 UTC
# Purpose: Real-time trade aggregator for buys_per_sec calculation
# Contract: fail-closed, sliding window, no hallucinated data
# === END SIGNATURE ===
"""
Trade Aggregator for Real-Time Buys/Sec Calculation.

Aggregates trade data from Binance WebSocket to calculate:
- buys_per_sec: Number of buy trades per second
- sell_per_sec: Number of sell trades per second
- volume_per_sec: Volume in USDT per second
- avg_trade_size: Average trade size in USDT

Uses sliding window approach for accurate real-time calculations.

INVARIANTS:
- Data older than window_size is discarded
- Zero values returned when no data (fail-closed)
- All calculations are from live data only
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Default settings
DEFAULT_WINDOW_SIZE = 60  # 60 second sliding window
CLEANUP_INTERVAL = 5.0     # Cleanup old data every 5 seconds


@dataclass
class TradeEvent:
    """Single trade event from WebSocket."""
    symbol: str
    price: float
    quantity: float
    is_buyer_maker: bool  # True = sell, False = buy
    timestamp: float      # Unix timestamp
    trade_id: int

    @property
    def is_buy(self) -> bool:
        """Check if this is a buy trade (taker is buyer)."""
        return not self.is_buyer_maker

    @property
    def usdt_value(self) -> float:
        """Trade value in USDT."""
        return self.price * self.quantity


@dataclass
class TradeStats:
    """Aggregated trade statistics for a symbol."""
    symbol: str
    window_start: float
    window_end: float
    buy_count: int = 0
    sell_count: int = 0
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    total_trades: int = 0
    avg_trade_size: float = 0.0

    @property
    def buys_per_sec(self) -> float:
        """Calculate buys per second."""
        window = self.window_end - self.window_start
        if window <= 0:
            return 0.0
        return self.buy_count / window

    @property
    def sells_per_sec(self) -> float:
        """Calculate sells per second."""
        window = self.window_end - self.window_start
        if window <= 0:
            return 0.0
        return self.sell_count / window

    @property
    def volume_per_sec(self) -> float:
        """Calculate volume per second in USDT."""
        window = self.window_end - self.window_start
        if window <= 0:
            return 0.0
        return (self.buy_volume + self.sell_volume) / window

    @property
    def buy_sell_ratio(self) -> float:
        """Ratio of buys to sells (>1 = more buys)."""
        if self.sell_count == 0:
            return float(self.buy_count) if self.buy_count > 0 else 1.0
        return self.buy_count / self.sell_count

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "buys_per_sec": round(self.buys_per_sec, 2),
            "sells_per_sec": round(self.sells_per_sec, 2),
            "volume_per_sec": round(self.volume_per_sec, 2),
            "buy_sell_ratio": round(self.buy_sell_ratio, 3),
            "buy_count": self.buy_count,
            "sell_count": self.sell_count,
            "avg_trade_size": round(self.avg_trade_size, 2),
            "window_seconds": round(self.window_end - self.window_start, 1),
        }


class TradeAggregator:
    """
    Real-time trade aggregator with sliding window.

    Usage:
        agg = TradeAggregator(window_size=60)

        # Add trade events (from WebSocket)
        agg.add_trade(trade_event)

        # Get current stats
        stats = agg.get_stats("BTCUSDT")
        print(f"Buys/sec: {stats.buys_per_sec}")

        # Get buys_per_sec directly
        bps = agg.get_buys_per_sec("XVSUSDT")
    """

    def __init__(
        self,
        window_size: float = DEFAULT_WINDOW_SIZE,
        on_stats: Optional[Callable[[TradeStats], None]] = None,
    ):
        """
        Initialize trade aggregator.

        Args:
            window_size: Sliding window size in seconds
            on_stats: Optional callback when stats are updated
        """
        self.window_size = window_size
        self.on_stats = on_stats

        # Trade buffers by symbol (deque for efficient sliding window)
        self._trades: Dict[str, deque] = {}

        # Cached stats by symbol
        self._stats_cache: Dict[str, TradeStats] = {}
        self._cache_time: Dict[str, float] = {}
        self._cache_ttl = 1.0  # Cache TTL in seconds

        # Cleanup task
        self._running = False
        self._cleanup_task: Optional[asyncio.Task] = None

        logger.info(f"TradeAggregator initialized (window={window_size}s)")

    def add_trade(self, trade: TradeEvent) -> None:
        """
        Add a trade event to the aggregator.

        Args:
            trade: TradeEvent from WebSocket
        """
        symbol = trade.symbol.upper()

        # Initialize deque if needed
        if symbol not in self._trades:
            self._trades[symbol] = deque(maxlen=10000)

        self._trades[symbol].append(trade)

        # Invalidate cache
        if symbol in self._cache_time:
            del self._cache_time[symbol]

    def add_trade_raw(
        self,
        symbol: str,
        price: float,
        quantity: float,
        is_buyer_maker: bool,
        timestamp: Optional[float] = None,
        trade_id: int = 0,
    ) -> None:
        """
        Add trade from raw data.

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            price: Trade price
            quantity: Trade quantity
            is_buyer_maker: True if buyer is maker (= sell trade)
            timestamp: Unix timestamp (defaults to now)
            trade_id: Optional trade ID
        """
        trade = TradeEvent(
            symbol=symbol.upper(),
            price=price,
            quantity=quantity,
            is_buyer_maker=is_buyer_maker,
            timestamp=timestamp or time.time(),
            trade_id=trade_id,
        )
        self.add_trade(trade)

    def get_stats(self, symbol: str) -> TradeStats:
        """
        Get aggregated trade statistics for a symbol.

        Args:
            symbol: Trading pair

        Returns:
            TradeStats with current statistics
        """
        symbol = symbol.upper()
        now = time.time()

        # Check cache
        if symbol in self._cache_time:
            if (now - self._cache_time[symbol]) < self._cache_ttl:
                return self._stats_cache[symbol]

        # Calculate fresh stats
        stats = self._calculate_stats(symbol, now)

        # Update cache
        self._stats_cache[symbol] = stats
        self._cache_time[symbol] = now

        return stats

    def get_buys_per_sec(self, symbol: str) -> float:
        """
        Get buys per second for a symbol.

        Args:
            symbol: Trading pair

        Returns:
            Buys per second (0.0 if no data)
        """
        return self.get_stats(symbol).buys_per_sec

    def get_sells_per_sec(self, symbol: str) -> float:
        """Get sells per second for a symbol."""
        return self.get_stats(symbol).sells_per_sec

    def get_buy_sell_ratio(self, symbol: str) -> float:
        """Get buy/sell ratio for a symbol."""
        return self.get_stats(symbol).buy_sell_ratio

    def get_volume_per_sec(self, symbol: str) -> float:
        """Get volume per second in USDT."""
        return self.get_stats(symbol).volume_per_sec

    def get_all_stats(self) -> Dict[str, TradeStats]:
        """Get stats for all tracked symbols."""
        return {symbol: self.get_stats(symbol) for symbol in self._trades.keys()}

    def _calculate_stats(self, symbol: str, now: float) -> TradeStats:
        """Calculate statistics from trade buffer."""
        window_start = now - self.window_size

        stats = TradeStats(
            symbol=symbol,
            window_start=window_start,
            window_end=now,
        )

        if symbol not in self._trades:
            return stats

        trades = self._trades[symbol]
        total_value = 0.0
        valid_count = 0

        for trade in trades:
            # Skip old trades
            if trade.timestamp < window_start:
                continue

            valid_count += 1
            stats.total_trades += 1
            total_value += trade.usdt_value

            if trade.is_buy:
                stats.buy_count += 1
                stats.buy_volume += trade.usdt_value
            else:
                stats.sell_count += 1
                stats.sell_volume += trade.usdt_value

        # Calculate average trade size
        if valid_count > 0:
            stats.avg_trade_size = total_value / valid_count

        # Callback
        if self.on_stats and stats.total_trades > 0:
            try:
                self.on_stats(stats)
            except Exception as e:
                logger.error(f"Stats callback error: {e}")

        return stats

    def _cleanup_old_trades(self) -> None:
        """Remove trades older than window from all buffers."""
        now = time.time()
        cutoff = now - self.window_size - 10  # Extra 10s buffer

        for symbol in list(self._trades.keys()):
            trades = self._trades[symbol]

            # Remove old trades from front
            while trades and trades[0].timestamp < cutoff:
                trades.popleft()

            # Remove empty buffers
            if not trades:
                del self._trades[symbol]

    async def start(self) -> None:
        """Start background cleanup task."""
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("TradeAggregator started")

    async def stop(self) -> None:
        """Stop background cleanup task."""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        logger.info("TradeAggregator stopped")

    async def _cleanup_loop(self) -> None:
        """Background cleanup loop."""
        while self._running:
            try:
                await asyncio.sleep(CLEANUP_INTERVAL)
                self._cleanup_old_trades()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cleanup error: {e}")

    def get_status(self) -> Dict[str, Any]:
        """Get aggregator status."""
        total_trades = sum(len(t) for t in self._trades.values())
        return {
            "symbols_tracked": len(self._trades),
            "total_trades_buffered": total_trades,
            "window_size": self.window_size,
            "cache_size": len(self._stats_cache),
        }


# === Singleton Instance ===

_aggregator: Optional[TradeAggregator] = None


def get_trade_aggregator(window_size: float = DEFAULT_WINDOW_SIZE) -> TradeAggregator:
    """Get or create singleton trade aggregator."""
    global _aggregator

    if _aggregator is None:
        _aggregator = TradeAggregator(window_size=window_size)

    return _aggregator
