# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T14:00:00Z
# Purpose: Trading strategies package
# === END SIGNATURE ===
"""
HOPE Trading Strategies.

Strategies:
- MomentumStrategy: Trend following (RSI + MACD)
- MeanReversionStrategy: Counter-trend (Bollinger Bands) - Phase 2
- BreakoutStrategy: Volatility breakout (ATR) - Phase 2
"""

from .base import BaseStrategy, StrategySignal, StrategyConfig
from .momentum import MomentumStrategy

__all__ = [
    "BaseStrategy",
    "StrategySignal",
    "StrategyConfig",
    "MomentumStrategy",
]
