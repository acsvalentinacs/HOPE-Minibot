# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T14:00:00Z
# Modified by: Claude (opus-4)
# Modified at: 2026-01-28T00:00:00Z
# Purpose: AI/ML trading modules package
# === END SIGNATURE ===
"""
HOPE AI Trading Modules.

Modules:
- technical_indicators: RSI, MACD, Bollinger Bands, ATR
- signal_engine: Main signal generation engine
- features: ML feature extraction from MarketData (Phase 4)
- ml_predictor: ML model wrapper with heuristic fallback (Phase 4)
"""

from .technical_indicators import (
    TechnicalIndicators,
    IndicatorResult,
    MACDResult,
    BollingerResult,
    VolumeProfile,
)
from .signal_engine import (
    SignalEngine,
    SignalEngineConfig,
    SignalDirection,
    TradingSignal,
    MarketData,
)
from .features import (
    FeatureExtractor,
    FeatureSet,
    get_feature_extractor,
)
from .ml_predictor import (
    MLPredictor,
    MLConfig,
    PredictionResult,
    HeuristicModel,
    get_ml_predictor,
)

__all__ = [
    # Technical Indicators
    "TechnicalIndicators",
    "IndicatorResult",
    "MACDResult",
    "BollingerResult",
    "VolumeProfile",
    # Signal Engine
    "SignalEngine",
    "SignalEngineConfig",
    "SignalDirection",
    "TradingSignal",
    "MarketData",
    # Features (Phase 4)
    "FeatureExtractor",
    "FeatureSet",
    "get_feature_extractor",
    # ML Predictor (Phase 4)
    "MLPredictor",
    "MLConfig",
    "PredictionResult",
    "HeuristicModel",
    "get_ml_predictor",
]
