# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T21:00:00Z
# Purpose: Market data providers package
# === END SIGNATURE ===
"""
HOPE Market Data Providers.

Modules:
- klines_provider: Real OHLCV data from Binance API
"""

from .klines_provider import (
    KlinesProvider,
    KlinesConfig,
    KlinesResult,
    get_klines_provider,
)

__all__ = [
    "KlinesProvider",
    "KlinesConfig",
    "KlinesResult",
    "get_klines_provider",
]
