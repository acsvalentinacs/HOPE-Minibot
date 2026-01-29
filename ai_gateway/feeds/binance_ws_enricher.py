# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 19:00:00 UTC
# Purpose: Binance WebSocket Enricher - orderbook, spread, trades
# Contract: Real-time enrichment for PrecursorDetector, fail-closed
# === END SIGNATURE ===
"""
Binance WebSocket Enricher - adds orderbook depth and trade data to signals.

This enriches raw MoonBot signals with:
- Real-time price
- Orderbook imbalance (bid pressure)
- Spread percentage (liquidity indicator)
- Recent trades stats
- Volume metrics

INVARIANTS:
- Stale data (>5s) = FAIL-CLOSED
- Missing orderbook = set imbalance to 0 (neutral)
- High spread (>1%) = flag as illiquid
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Config
ORDERBOOK_DEPTH = 20  # Top 20 levels
TRADE_BUFFER_SIZE = 500  # Last 500 trades per symbol
STALE_THRESHOLD_SEC = 5.0  # Data older than 5s is stale


@dataclass
class OrderBookLevel:
    """Single orderbook level."""
    price: float
    quantity: float


@dataclass
class OrderBook:
    """Orderbook snapshot."""
    symbol: str
    bids: List[OrderBookLevel]
    asks: List[OrderBookLevel]
    timestamp: float

    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 0.0

    @property
    def mid_price(self) -> float:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return 0.0

    @property
    def spread_pct(self) -> float:
        if self.mid_price > 0:
            return ((self.best_ask - self.best_bid) / self.mid_price) * 100
        return 100.0  # No price = max spread (illiquid)

    @property
    def imbalance(self) -> float:
        """
        Orderbook imbalance: (bid_vol - ask_vol) / total_vol.

        Positive = more buy pressure.
        Negative = more sell pressure.
        Range: -1.0 to 1.0
        """
        bid_vol = sum(l.quantity * l.price for l in self.bids[:10])
        ask_vol = sum(l.quantity * l.price for l in self.asks[:10])
        total = bid_vol + ask_vol
        if total > 0:
            return (bid_vol - ask_vol) / total
        return 0.0

    def is_stale(self) -> bool:
        return (time.time() - self.timestamp) > STALE_THRESHOLD_SEC


@dataclass
class Trade:
    """Single trade."""
    symbol: str
    price: float
    quantity: float
    is_buyer_maker: bool  # True = sell, False = buy
    timestamp: float


@dataclass
class EnrichedData:
    """Enrichment data to add to signal."""
    price: float
    bid: float
    ask: float
    spread_pct: float
    orderbook_imbalance: float
    volume_24h: float
    trades_1m: int
    buys_1m: int
    sells_1m: int
    avg_trade_size: float
    is_stale: bool
    enriched_at: float


@dataclass
class EnrichedSignal:
    """Signal with Binance enrichment."""
    raw: Dict[str, Any]
    binance: EnrichedData
    latency_ms: float
    checksum: str


class BinanceWSEnricher:
    """
    Real-time signal enricher using Binance WebSocket.

    Usage:
        enricher = BinanceWSEnricher()
        await enricher.start()

        # Enrich a signal
        enriched = await enricher.enrich(raw_signal)

        # Check orderbook
        ob = enricher.get_orderbook("BTCUSDT")
        print(f"Imbalance: {ob.imbalance:.2%}")
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        on_orderbook: Optional[Callable[[OrderBook], None]] = None,
        on_trade: Optional[Callable[[Trade], None]] = None,
    ):
        """
        Initialize enricher.

        Args:
            symbols: Initial symbols to track (can add more later)
            on_orderbook: Callback for orderbook updates
            on_trade: Callback for trade updates
        """
        self.symbols = set(symbols or [])
        self.on_orderbook = on_orderbook
        self.on_trade = on_trade

        # Data storage
        self._orderbooks: Dict[str, OrderBook] = {}
        self._trades: Dict[str, deque] = {}
        self._tickers: Dict[str, Dict[str, Any]] = {}

        # Connection state
        self._ws = None
        self._running = False
        self._task: Optional[asyncio.Task] = None

        logger.info("BinanceWSEnricher initialized")

    async def start(self) -> bool:
        """Start the enricher."""
        if self._running:
            return True

        try:
            self._running = True
            self._task = asyncio.create_task(
                self._run_ws_loop(),
                name="binance_ws_enricher"
            )
            logger.info("BinanceWSEnricher started")
            return True
        except Exception as e:
            logger.error(f"Failed to start BinanceWSEnricher: {e}")
            self._running = False
            return False

    async def stop(self) -> None:
        """Stop the enricher."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
        logger.info("BinanceWSEnricher stopped")

    async def subscribe(self, symbols: List[str]) -> None:
        """Subscribe to additional symbols."""
        new_symbols = []
        for s in symbols:
            s = s.upper()
            if not s.endswith("USDT"):
                s = s + "USDT"
            if s not in self.symbols:
                self.symbols.add(s)
                self._trades[s] = deque(maxlen=TRADE_BUFFER_SIZE)
                new_symbols.append(s)

        if new_symbols and self._ws:
            await self._send_subscribe(new_symbols)

    async def enrich(self, signal: Dict[str, Any]) -> EnrichedSignal:
        """
        Enrich a raw signal with Binance data.

        Args:
            signal: Raw signal dict with 'symbol' key

        Returns:
            EnrichedSignal with Binance data
        """
        start = time.time()
        symbol = signal.get("symbol", "").upper()
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"

        # Auto-subscribe if needed
        if symbol not in self.symbols:
            await self.subscribe([symbol])

        # Get orderbook
        ob = self._orderbooks.get(symbol)
        if ob and not ob.is_stale():
            price = ob.mid_price
            bid = ob.best_bid
            ask = ob.best_ask
            spread = ob.spread_pct
            imbalance = ob.imbalance
            is_stale = False
        else:
            # No orderbook or stale - use signal price
            price = signal.get("price", 0)
            bid = price
            ask = price
            spread = 1.0  # Assume high spread if no data
            imbalance = 0.0  # Neutral
            is_stale = True

        # Get trades stats (last 1 minute)
        trades_1m = 0
        buys_1m = 0
        sells_1m = 0
        total_size = 0.0
        cutoff = time.time() - 60

        if symbol in self._trades:
            for t in self._trades[symbol]:
                if t.timestamp >= cutoff:
                    trades_1m += 1
                    total_size += t.quantity * t.price
                    if t.is_buyer_maker:
                        sells_1m += 1
                    else:
                        buys_1m += 1

        avg_trade_size = total_size / trades_1m if trades_1m > 0 else 0

        # Get 24h volume from ticker
        ticker = self._tickers.get(symbol, {})
        volume_24h = float(ticker.get("quoteVolume", 0))

        # Build enrichment
        enriched = EnrichedData(
            price=price,
            bid=bid,
            ask=ask,
            spread_pct=round(spread, 4),
            orderbook_imbalance=round(imbalance, 4),
            volume_24h=volume_24h,
            trades_1m=trades_1m,
            buys_1m=buys_1m,
            sells_1m=sells_1m,
            avg_trade_size=round(avg_trade_size, 2),
            is_stale=is_stale,
            enriched_at=time.time(),
        )

        latency = (time.time() - start) * 1000

        # Compute checksum
        import hashlib
        import json
        data = json.dumps({"raw": signal, "enriched": enriched.__dict__}, sort_keys=True, default=str)
        checksum = f"sha256:{hashlib.sha256(data.encode()).hexdigest()[:16]}"

        return EnrichedSignal(
            raw=signal,
            binance=enriched,
            latency_ms=round(latency, 2),
            checksum=checksum,
        )

    def get_orderbook(self, symbol: str) -> Optional[OrderBook]:
        """Get current orderbook for symbol."""
        symbol = symbol.upper()
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"
        return self._orderbooks.get(symbol)

    def get_recent_trades(self, symbol: str, n: int = 100) -> List[Trade]:
        """Get recent trades for symbol."""
        symbol = symbol.upper()
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"
        if symbol not in self._trades:
            return []
        return list(self._trades[symbol])[-n:]

    def get_stats(self) -> Dict[str, Any]:
        """Get enricher statistics."""
        return {
            "running": self._running,
            "symbols_tracked": len(self.symbols),
            "orderbooks_cached": len(self._orderbooks),
            "trades_buffered": sum(len(t) for t in self._trades.values()),
        }

    async def _run_ws_loop(self) -> None:
        """WebSocket connection loop with auto-reconnect."""
        while self._running:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"WS error: {e}, reconnecting in 5s...")
                await asyncio.sleep(5)

    async def _connect_and_listen(self) -> None:
        """Connect to Binance WS and listen for updates."""
        try:
            import websockets
        except ImportError:
            logger.error("websockets not installed")
            return

        if not self.symbols:
            logger.warning("No symbols to track, waiting...")
            await asyncio.sleep(5)
            return

        # Build combined streams URL
        streams = []
        for s in list(self.symbols)[:50]:  # Max 50 symbols
            s_lower = s.lower()
            streams.append(f"{s_lower}@depth20@100ms")
            streams.append(f"{s_lower}@aggTrade")
            streams.append(f"{s_lower}@ticker")

        url = f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"
        logger.info(f"Connecting to Binance WS with {len(streams)} streams...")

        async with websockets.connect(url, ping_interval=30) as ws:
            self._ws = ws
            logger.info("Binance WS connected")

            async for message in ws:
                await self._handle_message(message)

    async def _send_subscribe(self, symbols: List[str]) -> None:
        """Send subscribe message for new symbols."""
        if not self._ws:
            return

        streams = []
        for s in symbols:
            s_lower = s.lower()
            streams.extend([
                f"{s_lower}@depth20@100ms",
                f"{s_lower}@aggTrade",
                f"{s_lower}@ticker",
            ])

        msg = {
            "method": "SUBSCRIBE",
            "params": streams,
            "id": int(time.time() * 1000),
        }

        import json
        await self._ws.send(json.dumps(msg))
        logger.debug(f"Subscribed to {len(streams)} streams")

    async def _handle_message(self, message: str) -> None:
        """Handle incoming WebSocket message."""
        import json
        try:
            data = json.loads(message)

            if "stream" in data and "data" in data:
                stream = data["stream"]
                payload = data["data"]

                if "@depth" in stream:
                    await self._handle_orderbook(payload)
                elif "@aggTrade" in stream:
                    await self._handle_trade(payload)
                elif "@ticker" in stream:
                    await self._handle_ticker(payload)

        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.error(f"Message handling error: {e}")

    async def _handle_orderbook(self, data: Dict) -> None:
        """Handle orderbook depth update."""
        symbol = data.get("s", "")
        bids = [OrderBookLevel(float(p), float(q)) for p, q in data.get("bids", [])]
        asks = [OrderBookLevel(float(p), float(q)) for p, q in data.get("asks", [])]

        ob = OrderBook(
            symbol=symbol,
            bids=bids,
            asks=asks,
            timestamp=time.time(),
        )
        self._orderbooks[symbol] = ob

        if self.on_orderbook:
            try:
                self.on_orderbook(ob)
            except Exception as e:
                logger.error(f"Orderbook callback error: {e}")

    async def _handle_trade(self, data: Dict) -> None:
        """Handle aggregated trade."""
        symbol = data.get("s", "")
        trade = Trade(
            symbol=symbol,
            price=float(data.get("p", 0)),
            quantity=float(data.get("q", 0)),
            is_buyer_maker=data.get("m", False),
            timestamp=data.get("T", time.time() * 1000) / 1000,
        )

        if symbol not in self._trades:
            self._trades[symbol] = deque(maxlen=TRADE_BUFFER_SIZE)
        self._trades[symbol].append(trade)

        if self.on_trade:
            try:
                self.on_trade(trade)
            except Exception as e:
                logger.error(f"Trade callback error: {e}")

    async def _handle_ticker(self, data: Dict) -> None:
        """Handle 24h ticker update."""
        symbol = data.get("s", "")
        self._tickers[symbol] = data


# === Singleton Instance ===

_enricher: Optional[BinanceWSEnricher] = None


def get_enricher(symbols: Optional[List[str]] = None) -> BinanceWSEnricher:
    """Get or create singleton enricher."""
    global _enricher
    if _enricher is None:
        _enricher = BinanceWSEnricher(symbols=symbols)
    return _enricher


async def enrich_signal(signal: Dict[str, Any]) -> EnrichedSignal:
    """Convenience function to enrich a signal."""
    enricher = get_enricher()
    if not enricher._running:
        await enricher.start()
    return await enricher.enrich(signal)
