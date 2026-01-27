# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T23:00:00Z
# Purpose: ML model wrapper for trading predictions
# Security: Fail-closed, heuristic fallback, no external model dependency
# === END SIGNATURE ===
"""
ML Predictor for Trading Signals.

Provides predictions for market direction using:
1. Heuristic model (default) - Rule-based using indicators
2. XGBoost model (optional) - If trained model available
3. LightGBM model (optional) - Alternative ML backend

Output range: [-1.0, +1.0]
- Positive = bullish (LONG signal)
- Negative = bearish (SHORT signal)
- Near zero = neutral

Fail-closed design:
- Returns 0.0 on any error
- Falls back to heuristic if ML model unavailable
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any, Callable

import numpy as np

from core.ai.features import FeatureSet

logger = logging.getLogger(__name__)


@dataclass
class MLConfig:
    """ML predictor configuration."""

    enabled: bool = True
    model_type: str = "heuristic"  # "heuristic" | "xgboost" | "lightgbm"
    model_path: Optional[Path] = None
    fallback_to_heuristic: bool = True
    min_confidence: float = 0.05  # Predictions below this return 0
    cache_predictions: bool = True


@dataclass
class PredictionResult:
    """Result of ML prediction."""

    score: float  # [-1, +1]
    confidence: float  # [0, 1]
    model_type: str
    probabilities: Optional[Dict[str, float]] = None  # {'long': 0.6, 'short': 0.3, 'hold': 0.1}

    @property
    def direction(self) -> str:
        """Get predicted direction."""
        if self.score > 0.1:
            return "LONG"
        elif self.score < -0.1:
            return "SHORT"
        return "NEUTRAL"


class HeuristicModel:
    """
    Rule-based prediction model using technical indicators.

    Uses RSI, MACD, Bollinger position, and trend indicators
    to generate a directional score.

    No external dependencies required.
    """

    # Feature indices (must match FeatureExtractor.FEATURE_NAMES)
    IDX_RSI = 5
    IDX_MACD_HIST = 6
    IDX_MACD_SIGNAL = 7
    IDX_BB_POSITION = 11
    IDX_VOLUME_TREND = 14
    IDX_VOLUME_SPIKE = 15
    IDX_EMA_SLOPE_10 = 16
    IDX_PRICE_VS_EMA = 18
    IDX_TREND_STRENGTH = 19

    def predict(self, features: np.ndarray) -> float:
        """
        Generate prediction from features.

        Args:
            features: Feature array from FeatureExtractor

        Returns:
            Score in [-1, +1] range
        """
        if len(features) < 20:
            return 0.0

        score = 0.0

        # RSI component (weight: 0.25)
        # RSI < 0.3 = oversold (bullish), RSI > 0.7 = overbought (bearish)
        rsi = features[self.IDX_RSI]
        if rsi < 0.30:
            score += 0.25 * (0.30 - rsi) / 0.30  # Bullish
        elif rsi > 0.70:
            score -= 0.25 * (rsi - 0.70) / 0.30  # Bearish

        # MACD component (weight: 0.25)
        macd_hist = features[self.IDX_MACD_HIST]
        macd_signal = features[self.IDX_MACD_SIGNAL]
        score += 0.15 * macd_hist  # Histogram direction
        score += 0.10 * macd_signal  # Crossover signal

        # Bollinger component (weight: 0.20)
        # Position < 0.2 = near lower band (bullish), > 0.8 = near upper (bearish)
        bb_pos = features[self.IDX_BB_POSITION]
        if bb_pos < 0.20:
            score += 0.20 * (0.20 - bb_pos) / 0.20
        elif bb_pos > 0.80:
            score -= 0.20 * (bb_pos - 0.80) / 0.20

        # Trend component (weight: 0.20)
        ema_slope = features[self.IDX_EMA_SLOPE_10]
        price_vs_ema = features[self.IDX_PRICE_VS_EMA]
        trend_strength = features[self.IDX_TREND_STRENGTH]

        # Strong trend alignment
        if ema_slope > 0.2 and price_vs_ema > 0.2:
            score += 0.20 * min(trend_strength, 1.0)
        elif ema_slope < -0.2 and price_vs_ema < -0.2:
            score -= 0.20 * min(trend_strength, 1.0)

        # Volume confirmation (weight: 0.10)
        volume_trend = features[self.IDX_VOLUME_TREND]
        volume_spike = features[self.IDX_VOLUME_SPIKE]

        # Volume confirms direction
        if score > 0 and (volume_trend > 0 or volume_spike > 0):
            score += 0.10 * volume_spike
        elif score < 0 and (volume_trend > 0 or volume_spike > 0):
            score -= 0.10 * volume_spike

        # Clamp to [-1, 1]
        return float(np.clip(score, -1.0, 1.0))

    def predict_proba(self, features: np.ndarray) -> Dict[str, float]:
        """Return pseudo-probabilities based on score."""
        score = self.predict(features)

        # Convert score to probabilities
        # score > 0 → higher long probability
        # score < 0 → higher short probability
        if score > 0:
            p_long = 0.33 + 0.5 * score
            p_short = 0.33 - 0.4 * score
        else:
            p_long = 0.33 + 0.4 * score
            p_short = 0.33 - 0.5 * score

        p_long = max(0.0, min(1.0, p_long))
        p_short = max(0.0, min(1.0, p_short))
        p_hold = 1.0 - p_long - p_short

        return {
            "long": p_long,
            "short": p_short,
            "hold": max(0.0, p_hold),
        }


class MLPredictor:
    """
    ML predictor wrapper for trading signals.

    Supports:
    - Heuristic model (default, no dependencies)
    - XGBoost (optional, requires xgboost package)
    - LightGBM (optional, requires lightgbm package)

    Fail-closed: returns 0.0 on any error.
    """

    def __init__(self, config: Optional[MLConfig] = None):
        """
        Initialize ML predictor.

        Args:
            config: ML configuration (uses defaults if None)
        """
        self.config = config or MLConfig()
        self._heuristic = HeuristicModel()
        self._ml_model: Optional[Any] = None
        self._model_loaded = False
        self._cache: Dict[str, float] = {}

        # Try to load ML model
        if self.config.enabled and self.config.model_type != "heuristic":
            self._try_load_model()

    def _try_load_model(self) -> None:
        """Attempt to load ML model."""
        if self.config.model_path and self.config.model_path.exists():
            try:
                if self.config.model_type == "xgboost":
                    self._load_xgboost(self.config.model_path)
                elif self.config.model_type == "lightgbm":
                    self._load_lightgbm(self.config.model_path)
                self._model_loaded = True
                logger.info("Loaded ML model: %s", self.config.model_path)
            except Exception as e:
                logger.warning("Failed to load ML model: %s", e)
                self._model_loaded = False
        else:
            logger.debug("No ML model path provided, using heuristic")

    def _load_xgboost(self, path: Path) -> None:
        """Load XGBoost model."""
        try:
            import xgboost as xgb
            self._ml_model = xgb.Booster()
            self._ml_model.load_model(str(path))
        except ImportError:
            logger.warning("xgboost not installed, using heuristic")
            raise

    def _load_lightgbm(self, path: Path) -> None:
        """Load LightGBM model."""
        try:
            import lightgbm as lgb
            self._ml_model = lgb.Booster(model_file=str(path))
        except ImportError:
            logger.warning("lightgbm not installed, using heuristic")
            raise

    def predict(self, features: FeatureSet) -> float:
        """
        Predict market direction.

        Args:
            features: FeatureSet from FeatureExtractor

        Returns:
            Score in [-1.0, +1.0]:
            - Positive = bullish (LONG)
            - Negative = bearish (SHORT)
            - Near zero = neutral
        """
        if not self.config.enabled:
            return 0.0

        if features is None or not features.is_valid():
            logger.debug("Invalid features, returning 0.0")
            return 0.0

        try:
            # Check cache
            if self.config.cache_predictions:
                cache_key = f"{features.symbol}:{features.timestamp}"
                if cache_key in self._cache:
                    return self._cache[cache_key]

            # Get prediction
            if self._model_loaded and self._ml_model is not None:
                score = self._predict_ml(features.features)
            else:
                score = self._heuristic.predict(features.features)

            # Apply minimum confidence threshold
            if abs(score) < self.config.min_confidence:
                score = 0.0

            # Cache result
            if self.config.cache_predictions:
                self._cache[cache_key] = score
                # Limit cache size
                if len(self._cache) > 1000:
                    # Remove oldest entries (simple approach)
                    keys = list(self._cache.keys())[:500]
                    for k in keys:
                        del self._cache[k]

            return score

        except Exception as e:
            logger.error("Prediction failed: %s", e)
            return 0.0  # Fail-closed

    def _predict_ml(self, features: np.ndarray) -> float:
        """Get prediction from ML model."""
        try:
            if self.config.model_type == "xgboost":
                import xgboost as xgb
                dmatrix = xgb.DMatrix(features.reshape(1, -1))
                proba = self._ml_model.predict(dmatrix)[0]
                # Assume binary classification: proba = P(LONG)
                return float(proba * 2 - 1)  # Convert [0,1] to [-1,1]

            elif self.config.model_type == "lightgbm":
                proba = self._ml_model.predict(features.reshape(1, -1))[0]
                return float(proba * 2 - 1)

            else:
                return self._heuristic.predict(features)

        except Exception as e:
            logger.warning("ML prediction failed, using heuristic: %s", e)
            if self.config.fallback_to_heuristic:
                return self._heuristic.predict(features)
            return 0.0

    def predict_full(self, features: FeatureSet) -> PredictionResult:
        """
        Get full prediction with probabilities.

        Args:
            features: FeatureSet from FeatureExtractor

        Returns:
            PredictionResult with score, confidence, and probabilities
        """
        score = self.predict(features)

        if self._model_loaded and self._ml_model is not None:
            model_type = self.config.model_type
            probabilities = None  # TODO: Extract from ML model
        else:
            model_type = "heuristic"
            probabilities = self._heuristic.predict_proba(features.features)

        return PredictionResult(
            score=score,
            confidence=abs(score),
            model_type=model_type,
            probabilities=probabilities,
        )

    def get_model_info(self) -> Dict[str, Any]:
        """Get information about loaded model."""
        return {
            "enabled": self.config.enabled,
            "model_type": self.config.model_type,
            "model_loaded": self._model_loaded,
            "model_path": str(self.config.model_path) if self.config.model_path else None,
            "fallback_enabled": self.config.fallback_to_heuristic,
            "cache_size": len(self._cache),
        }

    def clear_cache(self) -> None:
        """Clear prediction cache."""
        self._cache.clear()


# Singleton instance
_predictor_instance: Optional[MLPredictor] = None


def get_ml_predictor(config: Optional[MLConfig] = None) -> MLPredictor:
    """Get singleton MLPredictor instance."""
    global _predictor_instance
    if _predictor_instance is None:
        _predictor_instance = MLPredictor(config)
    return _predictor_instance
