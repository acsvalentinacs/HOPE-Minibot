# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T14:00:00Z
# Modified by: Claude (opus-4)
# Modified at: 2026-01-27T14:30:00Z
# Purpose: AI/ML trading modules package
# === END SIGNATURE ===
"""
HOPE AI Trading Modules.

Modules:
- technical_indicators: RSI, MACD, Bollinger Bands, ATR
- signal_engine: Main signal generation engine
- ml_predictor: LightGBM price predictor (Phase 2)
"""

from .technical_indicators import (
    TechnicalIndicators,
    MACDResult,
    BollingerBandsResult,
    VolumeProfile,
)
from .signal_engine import (
    SignalEngine,
    SignalEngineConfig,
    create_signal_engine,
)

__all__ = [
    # Technical Indicators
    "TechnicalIndicators",
    "MACDResult",
    "BollingerBandsResult",
    "VolumeProfile",
    # Signal Engine
    "SignalEngine",
    "SignalEngineConfig",
    "create_signal_engine",
]
