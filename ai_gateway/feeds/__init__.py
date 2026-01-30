# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 09:15:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-29 21:30:00 UTC
# Purpose: Data feeds package (Binance WS, Enricher, PriceBridge, Realtime)
# === END SIGNATURE ===
"""
AI-Gateway Data Feeds.

Modules:
- binance_ws: Real-time price feed via WebSocket
- binance_realtime: Unified prices + trades + buys_per_sec
- trade_aggregator: Real-time buys_per_sec calculation
- binance_ws_enricher: Orderbook, spread, trade enrichment
- price_bridge: Bridge WS prices to OutcomeTracker
"""

from .binance_ws import BinancePriceFeed, get_price_feed, PriceUpdate
from .binance_realtime import BinanceRealtimeFeed, get_realtime_feed, RealtimeData
from .trade_aggregator import TradeAggregator, TradeStats, get_trade_aggregator
from .price_bridge import (
    PriceFeedBridge,
    get_price_bridge,
    start_bridge,
    stop_bridge,
)
from .binance_ws_enricher import (
    BinanceWSEnricher,
    get_enricher,
    enrich_signal,
    EnrichedSignal,
    EnrichedData,
    OrderBook,
)

__all__ = [
    # Binance WS
    "BinancePriceFeed",
    "get_price_feed",
    "PriceUpdate",
    # Binance Realtime (prices + trades)
    "BinanceRealtimeFeed",
    "get_realtime_feed",
    "RealtimeData",
    # Trade Aggregator
    "TradeAggregator",
    "TradeStats",
    "get_trade_aggregator",
    # Price Bridge
    "PriceFeedBridge",
    "get_price_bridge",
    "start_bridge",
    "stop_bridge",
    # Enricher
    "BinanceWSEnricher",
    "get_enricher",
    "enrich_signal",
    "EnrichedSignal",
    "EnrichedData",
    "OrderBook",
]
