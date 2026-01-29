# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 03:46:00 UTC
# Purpose: Market regime detection using rule-based indicators
# === END SIGNATURE ===
"""
Regime Detector: Market state classification.

Detects current market regime (trending, ranging, volatile) using
technical indicators. Purely rule-based - no AI dependencies.
Writes RegimeArtifact to state/ai/regime.jsonl.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ...contracts import (
    MarketRegime,
    RegimeArtifact,
    RegimeIndicator,
    create_artifact_id,
)
from ...jsonl_writer import write_artifact
from ...status_manager import get_status_manager

logger = logging.getLogger(__name__)


# Regime detection thresholds
TREND_STRENGTH_THRESHOLD = 0.4  # ADX-like threshold
VOLATILITY_HIGH_PERCENTILE = 75
VOLATILITY_LOW_PERCENTILE = 25
RANGE_ATR_RATIO = 0.02  # 2% ATR/price ratio for ranging


@dataclass
class OHLCV:
    """OHLCV candle data."""
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float


class RegimeDetector:
    """
    Market regime detector for HOPE AI-Gateway.

    Classifies market into:
    - TRENDING_UP: Strong upward trend
    - TRENDING_DOWN: Strong downward trend
    - RANGING: Sideways consolidation
    - HIGH_VOLATILITY: Large price swings
    - LOW_VOLATILITY: Tight range, low activity
    """

    def __init__(self, ttl_seconds: int = 300):
        self._ttl = ttl_seconds
        self._status = get_status_manager()

    def detect(
        self,
        symbol: str,
        candles: List[OHLCV],
        timeframe: str = "4h",
    ) -> RegimeArtifact:
        """
        Detect market regime from OHLCV data.

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            candles: List of OHLCV candles (most recent last)
            timeframe: Candle timeframe

        Returns:
            RegimeArtifact with detection results
        """
        try:
            if len(candles) < 20:
                raise ValueError(f"Need at least 20 candles, got {len(candles)}")

            indicators: List[RegimeIndicator] = []

            # 1. Trend strength (simplified ADX-like)
            trend_dir, trend_strength = self._calculate_trend(candles)
            indicators.append(RegimeIndicator(
                name="trend_strength",
                value=trend_strength,
                threshold_low=0.2,
                threshold_high=TREND_STRENGTH_THRESHOLD,
                signal="bullish" if trend_dir > 0 else "bearish" if trend_dir < 0 else "neutral",
            ))

            # 2. Volatility percentile
            volatility, vol_percentile = self._calculate_volatility(candles)
            indicators.append(RegimeIndicator(
                name="volatility",
                value=vol_percentile,
                threshold_low=VOLATILITY_LOW_PERCENTILE,
                threshold_high=VOLATILITY_HIGH_PERCENTILE,
                signal="high" if vol_percentile > VOLATILITY_HIGH_PERCENTILE else "low" if vol_percentile < VOLATILITY_LOW_PERCENTILE else "normal",
            ))

            # 3. Range detection (price range / ATR)
            range_ratio = self._calculate_range_ratio(candles)
            indicators.append(RegimeIndicator(
                name="range_ratio",
                value=range_ratio,
                threshold_low=0.5,
                threshold_high=1.5,
                signal="ranging" if range_ratio < 1.0 else "expanding",
            ))

            # 4. Volume trend
            vol_trend = self._calculate_volume_trend(candles)
            indicators.append(RegimeIndicator(
                name="volume_trend",
                value=vol_trend,
                threshold_low=-0.2,
                threshold_high=0.2,
                signal="increasing" if vol_trend > 0.2 else "decreasing" if vol_trend < -0.2 else "stable",
            ))

            # Determine regime
            regime = self._classify_regime(
                trend_strength=trend_strength,
                trend_dir=trend_dir,
                vol_percentile=vol_percentile,
                range_ratio=range_ratio,
            )

            # Calculate regime confidence
            confidence = self._calculate_confidence(regime, indicators)

            # Determine strategy recommendation
            strategy = self._recommend_strategy(regime, trend_dir)

            # Position size modifier
            size_modifier = self._calculate_size_modifier(regime, confidence)

            # Create artifact
            artifact = RegimeArtifact(
                artifact_id=create_artifact_id("regime", symbol),
                ttl_seconds=self._ttl,
                symbol=symbol,
                timeframe=timeframe,
                current_regime=regime,
                regime_confidence=confidence,
                trend_direction="up" if trend_dir > 0 else "down" if trend_dir < 0 else "neutral",
                trend_strength=trend_strength,
                volatility_percentile=vol_percentile,
                indicators=indicators,
                recommended_strategy=strategy,
                position_size_modifier=size_modifier,
            )

            # Write to JSONL
            if write_artifact(artifact.with_checksum()):
                self._status.mark_healthy("regime")
            else:
                self._status.mark_warning("regime", "Write failed")

            return artifact

        except Exception as e:
            logger.error(f"Regime detection failed: {e}")
            self._status.mark_error("regime", str(e))
            raise

    def _calculate_trend(self, candles: List[OHLCV]) -> Tuple[int, float]:
        """
        Calculate trend direction and strength.

        Returns:
            (direction: -1/0/1, strength: 0-1)
        """
        closes = [c.close for c in candles]

        # Simple moving averages
        sma_short = sum(closes[-10:]) / 10
        sma_long = sum(closes[-20:]) / 20

        # Direction: short MA above/below long MA
        diff = (sma_short - sma_long) / sma_long
        if diff > 0.01:
            direction = 1
        elif diff < -0.01:
            direction = -1
        else:
            direction = 0

        # Strength: consistency of direction
        up_moves = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i-1])
        consistency = abs(up_moves / (len(closes) - 1) - 0.5) * 2

        # Price change magnitude
        total_change = abs(closes[-1] - closes[0]) / closes[0]
        magnitude = min(1.0, total_change / 0.1)  # Normalize to 10%

        strength = (consistency + magnitude) / 2

        return direction, strength

    def _calculate_volatility(self, candles: List[OHLCV]) -> Tuple[float, float]:
        """
        Calculate volatility and its percentile.

        Returns:
            (raw_volatility, percentile)
        """
        # True Range for each candle
        true_ranges = []
        for i, c in enumerate(candles):
            if i == 0:
                tr = c.high - c.low
            else:
                prev_close = candles[i-1].close
                tr = max(
                    c.high - c.low,
                    abs(c.high - prev_close),
                    abs(c.low - prev_close)
                )
            true_ranges.append(tr / c.close)  # Normalize by price

        # ATR (average)
        atr = sum(true_ranges[-14:]) / 14

        # Historical volatility (all candles)
        sorted_tr = sorted(true_ranges)
        current_rank = sum(1 for tr in sorted_tr if tr <= atr)
        percentile = (current_rank / len(sorted_tr)) * 100

        return atr, percentile

    def _calculate_range_ratio(self, candles: List[OHLCV]) -> float:
        """
        Calculate range expansion ratio.

        Low ratio = ranging, High ratio = trending/expanding
        """
        recent = candles[-14:]

        # Price range
        highs = [c.high for c in recent]
        lows = [c.low for c in recent]
        price_range = max(highs) - min(lows)
        avg_price = sum(c.close for c in recent) / len(recent)

        # Expected range from individual candles
        candle_ranges = [c.high - c.low for c in recent]
        expected_range = sum(candle_ranges) * 0.5  # Overlap expected

        if expected_range == 0:
            return 1.0

        return price_range / expected_range

    def _calculate_volume_trend(self, candles: List[OHLCV]) -> float:
        """
        Calculate volume trend (increasing/decreasing).

        Returns:
            -1 to 1 (negative = decreasing, positive = increasing)
        """
        volumes = [c.volume for c in candles[-14:]]
        if len(volumes) < 2:
            return 0.0

        # Simple linear regression slope
        n = len(volumes)
        sum_x = sum(range(n))
        sum_y = sum(volumes)
        sum_xy = sum(i * v for i, v in enumerate(volumes))
        sum_xx = sum(i * i for i in range(n))

        denominator = n * sum_xx - sum_x * sum_x
        if denominator == 0:
            return 0.0

        slope = (n * sum_xy - sum_x * sum_y) / denominator

        # Normalize slope
        avg_volume = sum_y / n
        if avg_volume == 0:
            return 0.0

        normalized = slope / avg_volume * n
        return max(-1.0, min(1.0, normalized))

    def _classify_regime(
        self,
        trend_strength: float,
        trend_dir: int,
        vol_percentile: float,
        range_ratio: float,
    ) -> MarketRegime:
        """Classify market regime based on indicators."""

        # High volatility takes precedence
        if vol_percentile > VOLATILITY_HIGH_PERCENTILE:
            return MarketRegime.HIGH_VOLATILITY

        # Low volatility
        if vol_percentile < VOLATILITY_LOW_PERCENTILE:
            return MarketRegime.LOW_VOLATILITY

        # Trending
        if trend_strength > TREND_STRENGTH_THRESHOLD:
            if trend_dir > 0:
                return MarketRegime.TRENDING_UP
            elif trend_dir < 0:
                return MarketRegime.TRENDING_DOWN

        # Ranging
        if range_ratio < 1.0:
            return MarketRegime.RANGING

        # Default to ranging if no clear signal
        return MarketRegime.RANGING

    def _calculate_confidence(
        self,
        regime: MarketRegime,
        indicators: List[RegimeIndicator],
    ) -> float:
        """Calculate confidence in regime classification."""
        # Base confidence
        confidence = 0.5

        # Adjust based on indicator agreement
        for ind in indicators:
            if regime == MarketRegime.TRENDING_UP:
                if ind.name == "trend_strength" and ind.value > 0.4:
                    confidence += 0.15
                if ind.name == "volume_trend" and ind.signal == "increasing":
                    confidence += 0.1
            elif regime == MarketRegime.TRENDING_DOWN:
                if ind.name == "trend_strength" and ind.value > 0.4:
                    confidence += 0.15
                if ind.name == "volume_trend" and ind.signal == "increasing":
                    confidence += 0.1
            elif regime == MarketRegime.RANGING:
                if ind.name == "range_ratio" and ind.value < 1.0:
                    confidence += 0.15
                if ind.name == "trend_strength" and ind.value < 0.2:
                    confidence += 0.1
            elif regime == MarketRegime.HIGH_VOLATILITY:
                if ind.name == "volatility" and ind.value > 80:
                    confidence += 0.2
            elif regime == MarketRegime.LOW_VOLATILITY:
                if ind.name == "volatility" and ind.value < 20:
                    confidence += 0.2

        return min(0.95, confidence)

    def _recommend_strategy(self, regime: MarketRegime, trend_dir: int) -> str:
        """Recommend trading strategy based on regime."""
        if regime == MarketRegime.TRENDING_UP:
            return "trend_follow_long"
        elif regime == MarketRegime.TRENDING_DOWN:
            return "trend_follow_short"
        elif regime == MarketRegime.RANGING:
            return "mean_revert"
        elif regime == MarketRegime.HIGH_VOLATILITY:
            return "reduce_exposure"
        elif regime == MarketRegime.LOW_VOLATILITY:
            return "hold"
        else:
            return "hold"

    def _calculate_size_modifier(self, regime: MarketRegime, confidence: float) -> float:
        """Calculate position size modifier based on regime."""
        base_modifier = {
            MarketRegime.TRENDING_UP: 1.2,
            MarketRegime.TRENDING_DOWN: 1.2,
            MarketRegime.RANGING: 0.8,
            MarketRegime.HIGH_VOLATILITY: 0.5,
            MarketRegime.LOW_VOLATILITY: 0.7,
        }.get(regime, 1.0)

        # Adjust by confidence
        return base_modifier * (0.5 + 0.5 * confidence)


# === Convenience function ===

def detect_regime(
    symbol: str,
    candles: List[Dict[str, float]],
    timeframe: str = "4h",
) -> RegimeArtifact:
    """
    Quick regime detection.

    Args:
        symbol: Trading pair
        candles: List of dicts with keys: timestamp, open, high, low, close, volume
        timeframe: Candle timeframe
    """
    ohlcv_list = [
        OHLCV(
            timestamp=c["timestamp"],
            open=c["open"],
            high=c["high"],
            low=c["low"],
            close=c["close"],
            volume=c["volume"],
        )
        for c in candles
    ]

    detector = RegimeDetector()
    return detector.detect(symbol, ohlcv_list, timeframe)
