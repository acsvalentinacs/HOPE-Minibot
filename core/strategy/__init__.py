# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T14:00:00Z
# Modified by: Claude (opus-4)
# Modified at: 2026-01-27T19:30:00Z
# Purpose: Trading strategies package
# === END SIGNATURE ===
"""
HOPE Trading Strategies.

Strategies:
- MomentumStrategy: Trend following (RSI + MACD)
- MeanReversionStrategy: Counter-trend (Bollinger Bands)
- BreakoutStrategy: Volatility breakout

Orchestration:
- StrategyOrchestrator: Regime-based strategy selection
- Regime detection: TRENDING/RANGING/VOLATILE
"""

from .base import BaseStrategy, StrategyConfig, Position, PositionSide, TradeResult
from .momentum import MomentumStrategy, MomentumConfig
from .mean_reversion import MeanReversionStrategy, MeanReversionConfig
from .breakout import BreakoutStrategy, BreakoutConfig
from .regime import Regime, RegimeResult, RegimeConfig, detect_regime
from .orchestrator import (
    StrategyOrchestrator,
    OrchestratorConfig,
    OrchestratorDecision,
    DecisionAction,
    DenyReason,
)

__all__ = [
    # Base
    "BaseStrategy",
    "StrategyConfig",
    "Position",
    "PositionSide",
    "TradeResult",
    # Strategies
    "MomentumStrategy",
    "MomentumConfig",
    "MeanReversionStrategy",
    "MeanReversionConfig",
    "BreakoutStrategy",
    "BreakoutConfig",
    # Regime
    "Regime",
    "RegimeResult",
    "RegimeConfig",
    "detect_regime",
    # Orchestrator
    "StrategyOrchestrator",
    "OrchestratorConfig",
    "OrchestratorDecision",
    "DecisionAction",
    "DenyReason",
]
