# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 21:20:00 UTC
# Purpose: Unified Binance real-time feed (prices + trades + buys_per_sec)
# Contract: fail-closed, auto-reconnect, critical for SUPER_SCALP detection
# === END SIGNATURE ===
"""
Binance Real-Time Feed - Unified WebSocket Client.

Combines:
- miniTicker stream: Real-time prices
- aggTrade stream: Trade data for buys_per_sec calculation

Critical for SUPER_SCALP mode which requires:
- buys_per_sec > 100 for immediate entry
- Real-time price for accurate entry/exit

INVARIANTS:
- No hallucinated data
- Stale data (>60s) rejected
- All data has verifiable timestamp
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .trade_aggregator import TradeAggregator, TradeStats, get_trade_aggregator

logger = logging.getLogger(__name__)

# WebSocket endpoints
WS_URL_COMBINED = "wss://stream.binance.com:9443/stream?streams="
WS_URL_TESTNET = "wss://testnet.binance.vision/ws"

# Connection settings
MIN_RECONNECT_DELAY = 1.0
MAX_RECONNECT_DELAY = 60.0
HEARTBEAT_INTERVAL = 30.0
PRICE_STALE_SECONDS = 60.0
MAX_STREAMS_PER_CONNECTION = 100


@dataclass
class RealtimeData:
    """Combined real-time data for a symbol."""
    symbol: str
    price: float
    timestamp: float
    buys_per_sec: float
    sells_per_sec: float
    buy_sell_ratio: float
    volume_24h: float
    change_24h_pct: float
    source: str = "binance_realtime"

    def is_stale(self) -> bool:
        """Check if data is stale."""
        return (time.time() - self.timestamp) > PRICE_STALE_SECONDS

    def is_super_scalp_ready(self) -> bool:
        """Check if conditions met for SUPER_SCALP mode."""
        return self.buys_per_sec >= 100

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "timestamp": self.timestamp,
            "buys_per_sec": round(self.buys_per_sec, 2),
            "sells_per_sec": round(self.sells_per_sec, 2),
            "buy_sell_ratio": round(self.buy_sell_ratio, 3),
            "volume_24h": self.volume_24h,
            "change_24h_pct": self.change_24h_pct,
            "is_super_scalp_ready": self.is_super_scalp_ready(),
            "source": self.source,
        }


class BinanceRealtimeFeed:
    """
    Unified real-time feed combining prices and trades.

    Usage:
        feed = BinanceRealtimeFeed()

        # Subscribe to symbols
        await feed.subscribe(["BTCUSDT", "XVSUSDT", "SYNUSDT"])

        # Get real-time data (price + buys_per_sec)
        data = feed.get_data("XVSUSDT")
        print(f"Price: {data.price}, Buys/sec: {data.buys_per_sec}")

        # Check for SUPER_SCALP condition
        if data.is_super_scalp_ready():
            print("SUPER_SCALP triggered!")

        # Run feed (blocking)
        await feed.run()
    """

    def __init__(
        self,
        event_bus: Optional[Any] = None,
        on_data: Optional[Callable[[RealtimeData], None]] = None,
        use_testnet: bool = False,
        trade_window: float = 60.0,
    ):
        """
        Initialize real-time feed.

        Args:
            event_bus: Optional EventBus for publishing events
            on_data: Callback for data updates
            use_testnet: Use testnet WebSocket
            trade_window: Window size for trade aggregation (seconds)
        """
        self.event_bus = event_bus
        self.on_data = on_data
        self.use_testnet = use_testnet

        # Subscribed symbols
        self._symbols: Set[str] = set()

        # Price data by symbol
        self._prices: Dict[str, Dict[str, Any]] = {}

        # Trade aggregator
        self._trade_agg = TradeAggregator(window_size=trade_window)

        # Connection state
        self._connected = False
        self._reconnect_delay = MIN_RECONNECT_DELAY
        self._total_reconnects = 0
        self._last_message_time = 0.0

        # WebSocket client
        self._ws: Optional[Any] = None
        self._running = False
        self._tasks: List[asyncio.Task] = []

        logger.info(f"BinanceRealtimeFeed initialized (testnet={use_testnet})")

    async def subscribe(self, symbols: List[str]) -> None:
        """
        Subscribe to price and trade updates.

        Args:
            symbols: List of trading pairs
        """
        normalized = []
        for s in symbols:
            s = s.upper()
            if not s.endswith("USDT"):
                s = s + "USDT"
            normalized.append(s)

        new_symbols = set(normalized) - self._symbols
        if not new_symbols:
            return

        self._symbols.update(new_symbols)
        logger.info(f"Subscribed to {len(new_symbols)} symbols: {list(new_symbols)[:5]}...")

        # If connected, send dynamic subscription
        if self._ws is not None and self._connected:
            await self._send_subscribe(list(new_symbols))

    def get_data(self, symbol: str) -> Optional[RealtimeData]:
        """
        Get combined real-time data for a symbol.

        Returns None if data is stale or unavailable.
        FAIL-CLOSED: No data = None
        """
        symbol = symbol.upper()
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"

        price_data = self._prices.get(symbol)
        if price_data is None:
            return None

        # Check staleness
        age = time.time() - price_data.get("timestamp", 0)
        if age > PRICE_STALE_SECONDS:
            logger.warning(f"Stale data for {symbol}, age={age:.1f}s")
            return None

        # Get trade stats
        trade_stats = self._trade_agg.get_stats(symbol)

        return RealtimeData(
            symbol=symbol,
            price=price_data.get("price", 0.0),
            timestamp=price_data.get("timestamp", time.time()),
            buys_per_sec=trade_stats.buys_per_sec,
            sells_per_sec=trade_stats.sells_per_sec,
            buy_sell_ratio=trade_stats.buy_sell_ratio,
            volume_24h=price_data.get("volume_24h", 0.0),
            change_24h_pct=price_data.get("change_24h_pct", 0.0),
        )

    def get_price(self, symbol: str) -> Optional[float]:
        """Get current price for symbol."""
        data = self.get_data(symbol)
        return data.price if data else None

    def get_buys_per_sec(self, symbol: str) -> float:
        """Get buys per second for symbol."""
        return self._trade_agg.get_buys_per_sec(symbol)

    def get_all_data(self) -> Dict[str, RealtimeData]:
        """Get data for all tracked symbols."""
        result = {}
        for symbol in self._symbols:
            data = self.get_data(symbol)
            if data:
                result[symbol] = data
        return result

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._connected

    @property
    def symbols(self) -> Set[str]:
        """Get subscribed symbols."""
        return self._symbols.copy()

    async def run(self) -> None:
        """Connect and run the feed indefinitely."""
        self._running = True

        # Start trade aggregator
        await self._trade_agg.start()

        while self._running:
            try:
                await self._connect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                self._connected = False

                # Exponential backoff
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2,
                    MAX_RECONNECT_DELAY
                )

        await self.stop()

    async def stop(self) -> None:
        """Stop the feed."""
        self._running = False

        # Cancel tasks
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Close WebSocket
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass

        # Stop trade aggregator
        await self._trade_agg.stop()

        self._connected = False
        logger.info("BinanceRealtimeFeed stopped")

    async def _connect(self) -> None:
        """Establish WebSocket connection."""
        try:
            import websockets
        except ImportError:
            logger.error("websockets package not installed. Run: pip install websockets")
            raise

        if not self._symbols:
            logger.warning("No symbols subscribed, waiting...")
            await asyncio.sleep(5)
            return

        # Build combined stream URL
        # Include both miniTicker (price) and aggTrade (trades) streams
        streams = []
        for s in list(self._symbols)[:50]:  # Limit to 50 symbols
            s_lower = s.lower()
            streams.append(f"{s_lower}@miniTicker")
            streams.append(f"{s_lower}@aggTrade")

        url = WS_URL_COMBINED + "/".join(streams[:MAX_STREAMS_PER_CONNECTION])

        logger.info(f"Connecting with {len(streams)} streams ({len(self._symbols)} symbols)...")

        async with websockets.connect(url, ping_interval=HEARTBEAT_INTERVAL) as ws:
            self._ws = ws
            self._connected = True
            self._reconnect_delay = MIN_RECONNECT_DELAY
            self._total_reconnects += 1

            logger.info(f"WebSocket connected (reconnects: {self._total_reconnects})")

            # Start heartbeat
            heartbeat_task = asyncio.create_task(self._heartbeat())
            self._tasks.append(heartbeat_task)

            try:
                async for message in ws:
                    self._last_message_time = time.time()
                    await self._on_message(message)
            finally:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

    async def _send_subscribe(self, symbols: List[str]) -> None:
        """Send dynamic subscription for new symbols."""
        if self._ws is None:
            return

        streams = []
        for s in symbols:
            s_lower = s.lower()
            streams.append(f"{s_lower}@miniTicker")
            streams.append(f"{s_lower}@aggTrade")

        msg = {
            "method": "SUBSCRIBE",
            "params": streams,
            "id": int(time.time() * 1000),
        }

        try:
            await self._ws.send(json.dumps(msg))
            logger.debug(f"Sent subscribe for {len(symbols)} symbols")
        except Exception as e:
            logger.error(f"Subscribe failed: {e}")

    async def _on_message(self, message: str) -> None:
        """Process incoming WebSocket message."""
        try:
            data = json.loads(message)

            # Combined stream format
            if "stream" in data and "data" in data:
                stream = data["stream"]
                payload = data["data"]

                if "@miniTicker" in stream:
                    self._handle_ticker(payload)
                elif "@aggTrade" in stream:
                    self._handle_trade(payload)
            else:
                # Direct message format
                event_type = data.get("e")
                if event_type == "24hrMiniTicker":
                    self._handle_ticker(data)
                elif event_type == "aggTrade":
                    self._handle_trade(data)

        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON: {e}")
        except Exception as e:
            logger.error(f"Message error: {e}")

    def _handle_ticker(self, data: Dict[str, Any]) -> None:
        """Handle 24hr miniTicker event."""
        symbol = data.get("s", "")
        if not symbol:
            return

        price = float(data.get("c", 0))  # Close price
        open_price = float(data.get("o", price))
        volume = float(data.get("v", 0))

        change_pct = ((price - open_price) / open_price * 100) if open_price > 0 else 0.0

        self._prices[symbol] = {
            "price": price,
            "timestamp": time.time(),
            "volume_24h": volume * price,
            "change_24h_pct": round(change_pct, 2),
        }

        # Emit callback
        self._emit_data_update(symbol)

    def _handle_trade(self, data: Dict[str, Any]) -> None:
        """Handle aggTrade event."""
        symbol = data.get("s", "")
        if not symbol:
            return

        price = float(data.get("p", 0))
        quantity = float(data.get("q", 0))
        is_buyer_maker = data.get("m", False)
        trade_time = data.get("T", 0) / 1000  # Convert ms to seconds
        trade_id = data.get("a", 0)

        # Add to aggregator
        self._trade_agg.add_trade_raw(
            symbol=symbol,
            price=price,
            quantity=quantity,
            is_buyer_maker=is_buyer_maker,
            timestamp=trade_time or time.time(),
            trade_id=trade_id,
        )

    def _emit_data_update(self, symbol: str) -> None:
        """Emit data update callback."""
        data = self.get_data(symbol)
        if data is None:
            return

        # Callback
        if self.on_data:
            try:
                self.on_data(data)
            except Exception as e:
                logger.error(f"Data callback error: {e}")

        # Event bus
        if self.event_bus is not None:
            try:
                from ..core.event_bus import EventType
                self.event_bus.publish(
                    EventType.PRICE,
                    data.to_dict(),
                    source="binance_realtime"
                )
            except Exception as e:
                logger.error(f"EventBus error: {e}")

    async def _heartbeat(self) -> None:
        """Heartbeat and connection health check."""
        while self._running and self._connected:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)

                # Check for stale connection
                if self._last_message_time > 0:
                    silence = time.time() - self._last_message_time
                    if silence > HEARTBEAT_INTERVAL * 3:
                        logger.warning(f"No messages for {silence:.1f}s, reconnecting...")
                        if self._ws:
                            await self._ws.close()
                        break

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Get feed statistics."""
        return {
            "is_connected": self._connected,
            "symbols_count": len(self._symbols),
            "prices_count": len(self._prices),
            "total_reconnects": self._total_reconnects,
            "last_message_age": (
                time.time() - self._last_message_time
                if self._last_message_time > 0 else None
            ),
            "trade_aggregator": self._trade_agg.get_status(),
        }


# === Singleton Instance ===

_feed: Optional[BinanceRealtimeFeed] = None


def get_realtime_feed(
    event_bus: Optional[Any] = None,
    use_testnet: bool = False,
) -> BinanceRealtimeFeed:
    """Get or create singleton real-time feed."""
    global _feed

    if _feed is None:
        _feed = BinanceRealtimeFeed(
            event_bus=event_bus,
            use_testnet=use_testnet,
        )

    return _feed


# === Quick Test ===

async def test_feed():
    """Quick test of the real-time feed."""
    feed = BinanceRealtimeFeed()

    def on_data(data: RealtimeData):
        if data.buys_per_sec > 0:
            print(f"{data.symbol}: ${data.price:.4f} | "
                  f"Buys/s: {data.buys_per_sec:.1f} | "
                  f"B/S ratio: {data.buy_sell_ratio:.2f}")

    feed.on_data = on_data

    # Subscribe to test symbols
    await feed.subscribe(["BTCUSDT", "ETHUSDT", "XVSUSDT", "SYNUSDT"])

    print("Starting feed... (Ctrl+C to stop)")
    await feed.run()


if __name__ == "__main__":
    asyncio.run(test_feed())
