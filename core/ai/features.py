# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T23:00:00Z
# Purpose: ML feature extraction from MarketData
# Security: Fail-closed on invalid data, pure computation
# === END SIGNATURE ===
"""
ML Feature Extractor.

Extracts normalized features from MarketData for ML prediction.

Features include:
- Price-based (returns, momentum)
- Volatility (ATR, Bollinger)
- Oscillators (RSI, MACD)
- Volume profile
- Trend indicators

All features are normalized to comparable ranges for ML models.
Fail-closed: returns None if data insufficient or invalid.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

import numpy as np

from core.ai.signal_engine import MarketData
from core.ai.technical_indicators import TechnicalIndicators

logger = logging.getLogger(__name__)

# Minimum candles required for feature extraction
MIN_CANDLES_FOR_FEATURES = 50


@dataclass
class FeatureSet:
    """ML feature vector extracted from MarketData."""

    features: np.ndarray  # Feature values (1D array)
    names: List[str]      # Feature names (same length as features)
    timestamp: int        # Extraction timestamp
    symbol: str           # Symbol name

    @property
    def num_features(self) -> int:
        """Number of features."""
        return len(self.features)

    def to_dict(self) -> Dict[str, float]:
        """Convert to name -> value dictionary."""
        return dict(zip(self.names, self.features.tolist()))

    def is_valid(self) -> bool:
        """Check if all features are finite."""
        return np.all(np.isfinite(self.features))


class FeatureExtractor:
    """
    Extract ML features from MarketData.

    Features are designed for:
    - Price prediction (next candle direction)
    - Signal strength estimation
    - Risk assessment

    All features normalized to approximately [-1, 1] or [0, 1] range.
    """

    # Feature names (order matters for consistency)
    FEATURE_NAMES = [
        # Price-based (5)
        "returns_1",         # 1-bar return
        "returns_5",         # 5-bar return
        "returns_20",        # 20-bar return
        "high_low_ratio",    # Bar range / price
        "close_position",    # Close position in bar range [0,1]

        # Momentum (4)
        "rsi_14",            # RSI normalized [0,1]
        "macd_hist_norm",    # MACD histogram / ATR
        "macd_signal",       # Crossover signal [-1,0,1]
        "momentum_10",       # 10-bar momentum

        # Volatility (4)
        "atr_pct",           # ATR / price
        "bb_width",          # Bollinger width / price
        "bb_position",       # Position in BB [0,1]
        "volatility_20",     # 20-bar std of returns

        # Volume (3)
        "volume_ratio",      # Volume / avg volume
        "volume_trend",      # Volume trend [-1,0,1]
        "volume_spike",      # Spike indicator [0,1]

        # Trend (4)
        "ema_slope_10",      # EMA(10) slope normalized
        "ema_slope_50",      # EMA(50) slope normalized
        "price_vs_ema20",    # Price distance from EMA(20) / ATR
        "trend_strength",    # Trend strength indicator
    ]

    def __init__(self, min_candles: int = MIN_CANDLES_FOR_FEATURES):
        """
        Initialize feature extractor.

        Args:
            min_candles: Minimum required candles for extraction
        """
        self.min_candles = min_candles

    def get_feature_names(self) -> List[str]:
        """Get list of feature names."""
        return self.FEATURE_NAMES.copy()

    def extract(self, market_data: MarketData) -> Optional[FeatureSet]:
        """
        Extract features from MarketData.

        Args:
            market_data: OHLCV data with at least min_candles bars

        Returns:
            FeatureSet or None if extraction fails
        """
        try:
            # Validate input
            if market_data is None:
                logger.warning("MarketData is None")
                return None

            n = len(market_data.closes)
            if n < self.min_candles:
                logger.warning("Insufficient candles: %d < %d", n, self.min_candles)
                return None

            # Extract OHLCV arrays
            opens = market_data.opens
            highs = market_data.highs
            lows = market_data.lows
            closes = market_data.closes
            volumes = market_data.volumes

            # Current values
            current_close = closes[-1]
            current_high = highs[-1]
            current_low = lows[-1]

            # Calculate indicators
            rsi_result = TechnicalIndicators.rsi(closes)
            macd_result = TechnicalIndicators.macd(closes)
            bb_result = TechnicalIndicators.bollinger_bands(closes)
            atr = TechnicalIndicators.atr(highs, lows, closes)
            volume_result = TechnicalIndicators.volume_profile(volumes)

            # Protect against division by zero
            safe_close = max(current_close, 0.0001)
            safe_atr = max(atr, 0.0001)

            # === PRICE-BASED FEATURES ===
            returns_1 = (closes[-1] / closes[-2] - 1) if n > 1 else 0.0
            returns_5 = (closes[-1] / closes[-5] - 1) if n > 5 else 0.0
            returns_20 = (closes[-1] / closes[-20] - 1) if n > 20 else 0.0

            bar_range = current_high - current_low
            high_low_ratio = bar_range / safe_close

            close_position = 0.5
            if bar_range > 0:
                close_position = (current_close - current_low) / bar_range

            # === MOMENTUM FEATURES ===
            rsi_14 = rsi_result.value / 100.0  # Normalize to [0, 1]

            macd_hist_norm = macd_result.histogram / safe_atr  # Normalize by ATR
            macd_hist_norm = np.clip(macd_hist_norm, -3, 3) / 3  # Clip to [-1, 1]

            macd_signal = 0.0
            if macd_result.crossover == "BULLISH":
                macd_signal = 1.0
            elif macd_result.crossover == "BEARISH":
                macd_signal = -1.0

            momentum_10 = (closes[-1] / closes[-10] - 1) if n > 10 else 0.0

            # === VOLATILITY FEATURES ===
            atr_pct = atr / safe_close

            bb_width = bb_result.width / safe_close

            bb_position = bb_result.position  # Already [0, 1]

            # Volatility from returns
            if n >= 20:
                returns = np.diff(closes[-21:]) / closes[-21:-1]
                volatility_20 = float(np.std(returns))
            else:
                volatility_20 = 0.02  # Default 2%

            # === VOLUME FEATURES ===
            volume_ratio = volume_result.current_ratio
            volume_ratio = min(volume_ratio, 5.0) / 5.0  # Normalize to [0, 1]

            volume_trend = 0.0
            if volume_result.trend == "increasing":
                volume_trend = 1.0
            elif volume_result.trend == "decreasing":
                volume_trend = -1.0

            volume_spike = 1.0 if volume_result.spike else 0.0

            # === TREND FEATURES ===
            ema10 = TechnicalIndicators.ema(closes, 10)
            ema20 = TechnicalIndicators.ema(closes, 20)
            ema50 = TechnicalIndicators.ema(closes, 50) if n >= 50 else ema20

            # EMA slopes (normalized by price)
            if n >= 12:
                ema10_prev = TechnicalIndicators.ema(closes[:-2], 10)
                ema_slope_10 = (ema10 - ema10_prev) / safe_close / 2  # Per-bar slope
            else:
                ema_slope_10 = 0.0

            if n >= 52:
                ema50_prev = TechnicalIndicators.ema(closes[:-2], 50)
                ema_slope_50 = (ema50 - ema50_prev) / safe_close / 2
            else:
                ema_slope_50 = 0.0

            # Clip slopes
            ema_slope_10 = np.clip(ema_slope_10, -0.1, 0.1) * 10  # Scale to [-1, 1]
            ema_slope_50 = np.clip(ema_slope_50, -0.1, 0.1) * 10

            # Price vs EMA
            price_vs_ema20 = (current_close - ema20) / safe_atr
            price_vs_ema20 = np.clip(price_vs_ema20, -3, 3) / 3  # Scale to [-1, 1]

            # Trend strength
            trend_strength = abs(ema10 - ema50) / safe_atr
            trend_strength = min(trend_strength, 3.0) / 3.0  # Scale to [0, 1]

            # === BUILD FEATURE VECTOR ===
            features = np.array([
                # Price-based
                returns_1,
                returns_5,
                returns_20,
                high_low_ratio,
                close_position,
                # Momentum
                rsi_14,
                macd_hist_norm,
                macd_signal,
                momentum_10,
                # Volatility
                atr_pct,
                bb_width,
                bb_position,
                volatility_20,
                # Volume
                volume_ratio,
                volume_trend,
                volume_spike,
                # Trend
                ema_slope_10,
                ema_slope_50,
                price_vs_ema20,
                trend_strength,
            ], dtype=np.float64)

            # Validate features
            if not np.all(np.isfinite(features)):
                logger.warning("Features contain NaN/Inf, replacing with 0")
                features = np.nan_to_num(features, nan=0.0, posinf=1.0, neginf=-1.0)

            return FeatureSet(
                features=features,
                names=self.FEATURE_NAMES,
                timestamp=market_data.timestamp,
                symbol=market_data.symbol,
            )

        except Exception as e:
            logger.error("Feature extraction failed: %s", e)
            return None


# Singleton instance
_extractor_instance: Optional[FeatureExtractor] = None


def get_feature_extractor() -> FeatureExtractor:
    """Get singleton FeatureExtractor instance."""
    global _extractor_instance
    if _extractor_instance is None:
        _extractor_instance = FeatureExtractor()
    return _extractor_instance
