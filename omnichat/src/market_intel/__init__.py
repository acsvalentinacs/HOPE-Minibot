# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T19:00:00Z
# Purpose: Market Intelligence Module - Real-time crypto data and news analysis
# === END SIGNATURE ===
"""
Market Intelligence Module for HOPE Trading System.

Provides:
- Real-time market data from Binance
- Global crypto metrics from CoinGecko
- News aggregation and impact scoring from RSS feeds
- Atomic persistence with sha256 verification

Usage:
    from omnichat.src.market_intel import MarketIntel, fetch_market_snapshot

    intel = MarketIntel()
    snapshot = await intel.get_snapshot()
"""

from .types import (
    MarketSnapshot,
    TickerData,
    GlobalMetrics,
    NewsItem,
    ImpactScore,
)
from .fetcher import MarketFetcher
from .analyzer import NewsAnalyzer, calculate_impact_score
from .intel import MarketIntel, fetch_market_snapshot

__all__ = [
    "MarketSnapshot",
    "TickerData",
    "GlobalMetrics",
    "NewsItem",
    "ImpactScore",
    "MarketFetcher",
    "NewsAnalyzer",
    "MarketIntel",
    "fetch_market_snapshot",
    "calculate_impact_score",
]
