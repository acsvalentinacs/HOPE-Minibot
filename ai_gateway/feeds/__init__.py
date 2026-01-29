# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 09:15:00 UTC
# Purpose: Data feeds package (Binance WS, News, CoinGecko)
# === END SIGNATURE ===
"""
AI-Gateway Data Feeds.

Modules:
- binance_ws: Real-time price feed via WebSocket
- news_aggregator: RSS news aggregation
- coingecko: Global market metrics
"""

from .binance_ws import BinancePriceFeed, get_price_feed

__all__ = ["BinancePriceFeed", "get_price_feed"]
