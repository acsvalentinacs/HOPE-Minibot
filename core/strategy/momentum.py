# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T14:00:00Z
# Purpose: Momentum trading strategy (RSI + MACD)
# === END SIGNATURE ===
"""
Momentum Strategy Module.

Trend-following strategy combining:
- RSI for overbought/oversold conditions
- MACD for momentum and trend direction
- Volume confirmation

Entry signals:
- LONG: RSI < 30 + MACD bullish crossover + Volume spike
- SHORT: RSI > 70 + MACD bearish crossover + Volume spike

Exit via ATR-based stop loss and take profit.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from core.ai.technical_indicators import (
    TechnicalIndicators,
    MACDResult,
    VolumeProfile,
)
from .base import (
    BaseStrategy,
    StrategySignal,
    StrategyConfig,
    MarketData,
    SignalDirection,
)


@dataclass(frozen=True)
class MomentumConfig(StrategyConfig):
    """
    Momentum strategy specific configuration.
    """
    # RSI parameters
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    rsi_weight: float = 0.35

    # MACD parameters
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    macd_weight: float = 0.40

    # Volume parameters
    volume_period: int = 20
    volume_spike_mult: float = 1.5  # Require 1.5x avg volume
    volume_weight: float = 0.15

    # Trend filter (EMA)
    trend_ema_period: int = 50
    trend_weight: float = 0.10

    # Signal validity
    signal_validity_hours: int = 4


class MomentumStrategy(BaseStrategy):
    """
    Momentum trading strategy.

    Combines RSI, MACD, and volume for trend-following signals.
    Works best in trending markets.

    Weights:
    - MACD: 40% (primary trend indicator)
    - RSI: 35% (overbought/oversold filter)
    - Volume: 15% (confirmation)
    - Trend: 10% (direction filter)
    """

    name = "momentum"
    description = "Trend-following strategy using RSI + MACD"

    def __init__(self, config: MomentumConfig | None = None):
        """Initialize momentum strategy."""
        super().__init__(config or MomentumConfig())
        self.indicators = TechnicalIndicators()
        self._config: MomentumConfig = self.config  # Type hint

    def analyze(self, data: MarketData) -> StrategySignal | None:
        """
        Analyze market data and generate momentum signal.

        Args:
            data: Market data with OHLCV

        Returns:
            StrategySignal if momentum conditions met, None otherwise
        """
        # Convert to numpy for calculations
        closes = np.array(data.closes)
        highs = np.array(data.highs)
        lows = np.array(data.lows)
        volumes = np.array(data.volumes)

        # Minimum data required
        min_bars = max(
            self._config.rsi_period + 1,
            self._config.macd_slow + self._config.macd_signal,
            self._config.volume_period,
            self._config.trend_ema_period,
        )

        if len(closes) < min_bars:
            return None

        # Calculate indicators
        rsi = self.indicators.rsi(closes, self._config.rsi_period)
        macd = self.indicators.macd(
            closes,
            self._config.macd_fast,
            self._config.macd_slow,
            self._config.macd_signal,
        )
        atr = self.indicators.atr(highs, lows, closes)
        volume_profile = self.indicators.volume_profile(volumes, self._config.volume_period)
        trend_ema = self.indicators.ema(closes, self._config.trend_ema_period)

        # Check for NaN values
        if np.isnan(rsi) or np.isnan(macd.histogram) or np.isnan(atr):
            return None

        # Calculate previous MACD for crossover detection
        macd_prev = self.indicators.macd(
            closes[:-1],
            self._config.macd_fast,
            self._config.macd_slow,
            self._config.macd_signal,
        )

        # Determine direction and scores
        direction, scores = self._calculate_signal_scores(
            rsi=rsi,
            macd=macd,
            macd_prev=macd_prev,
            volume_profile=volume_profile,
            current_price=data.current_price,
            trend_ema=trend_ema,
        )

        # Calculate combined strength
        strength = self._calculate_strength(scores)
        confidence = self._calculate_confidence(scores, direction)

        # No signal if neutral
        if direction == SignalDirection.NEUTRAL:
            return None

        # Calculate entry/exit levels
        entry_price, stop_loss, take_profit, rr_ratio = self.calculate_entry_exit(
            direction=direction,
            current_price=data.current_price,
            atr=atr,
        )

        # Build reasoning
        reasoning = self._build_reasoning(direction, scores, rsi, macd)

        # Generate signal
        now = datetime.now(timezone.utc)
        signal = StrategySignal(
            signal_id=StrategySignal.generate_id(
                data.symbol, direction, now, self.name
            ),
            timestamp=now,
            strategy_name=self.name,
            symbol=data.symbol,
            direction=direction,
            strength=strength,
            confidence=confidence,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward_ratio=rr_ratio,
            timeframe=data.timeframe,
            indicators={
                "rsi": round(rsi, 2),
                "macd_line": macd.macd_line,
                "macd_signal": macd.signal_line,
                "macd_histogram": macd.histogram,
                "atr": round(atr, 8),
                "trend_ema": round(trend_ema, 8),
                "volume_ratio": volume_profile.current_ratio,
            },
            reasoning=reasoning,
            expires_at=now + timedelta(hours=self._config.signal_validity_hours),
            raw_scores=scores,
        )

        # Validate
        if not self.validate_signal(signal):
            return None

        self._last_signal = signal
        return signal

    def _calculate_signal_scores(
        self,
        rsi: float,
        macd: MACDResult,
        macd_prev: MACDResult,
        volume_profile: VolumeProfile,
        current_price: float,
        trend_ema: float,
    ) -> tuple[SignalDirection, dict[str, float]]:
        """
        Calculate individual indicator scores.

        Returns:
            (direction, scores_dict)
        """
        scores = {
            "rsi_score": 0.0,
            "macd_score": 0.0,
            "volume_score": 0.0,
            "trend_score": 0.0,
        }

        long_score = 0.0
        short_score = 0.0

        # RSI Score (-1 to +1)
        if rsi < self._config.rsi_oversold:
            # Oversold = potential LONG
            rsi_strength = (self._config.rsi_oversold - rsi) / self._config.rsi_oversold
            scores["rsi_score"] = rsi_strength
            long_score += rsi_strength * self._config.rsi_weight
        elif rsi > self._config.rsi_overbought:
            # Overbought = potential SHORT
            rsi_strength = (rsi - self._config.rsi_overbought) / (100 - self._config.rsi_overbought)
            scores["rsi_score"] = -rsi_strength
            short_score += rsi_strength * self._config.rsi_weight

        # MACD Score (-1 to +1)
        crossover = self.indicators.macd_crossover(macd, macd_prev)
        if crossover == "bullish":
            macd_strength = min(abs(macd.histogram) / abs(macd.signal_line), 1.0) if macd.signal_line != 0 else 0.5
            scores["macd_score"] = macd_strength
            long_score += macd_strength * self._config.macd_weight
        elif crossover == "bearish":
            macd_strength = min(abs(macd.histogram) / abs(macd.signal_line), 1.0) if macd.signal_line != 0 else 0.5
            scores["macd_score"] = -macd_strength
            short_score += macd_strength * self._config.macd_weight
        else:
            # No crossover - use histogram direction
            if macd.histogram > 0:
                scores["macd_score"] = 0.3
                long_score += 0.3 * self._config.macd_weight
            elif macd.histogram < 0:
                scores["macd_score"] = -0.3
                short_score += 0.3 * self._config.macd_weight

        # Volume Score (0 to 1)
        if volume_profile.current_ratio >= self._config.volume_spike_mult:
            vol_score = min(volume_profile.current_ratio / 3.0, 1.0)
            scores["volume_score"] = vol_score
            # Volume confirms both directions equally
            long_score += vol_score * self._config.volume_weight * 0.5
            short_score += vol_score * self._config.volume_weight * 0.5

        # Trend Score (filter)
        price_vs_ema = (current_price - trend_ema) / trend_ema if trend_ema != 0 else 0
        if price_vs_ema > 0.01:  # Price > EMA (uptrend)
            scores["trend_score"] = min(price_vs_ema * 10, 1.0)
            long_score += scores["trend_score"] * self._config.trend_weight
        elif price_vs_ema < -0.01:  # Price < EMA (downtrend)
            scores["trend_score"] = max(price_vs_ema * 10, -1.0)
            short_score += abs(scores["trend_score"]) * self._config.trend_weight

        # Determine direction
        if long_score > short_score and long_score > 0.3:
            return SignalDirection.LONG, scores
        elif short_score > long_score and short_score > 0.3:
            return SignalDirection.SHORT, scores
        else:
            return SignalDirection.NEUTRAL, scores

    def _calculate_strength(self, scores: dict[str, float]) -> float:
        """
        Calculate overall signal strength.

        Returns:
            Strength 0.0-1.0
        """
        # Weighted average of absolute scores
        total = (
            abs(scores["rsi_score"]) * self._config.rsi_weight +
            abs(scores["macd_score"]) * self._config.macd_weight +
            abs(scores["volume_score"]) * self._config.volume_weight +
            abs(scores["trend_score"]) * self._config.trend_weight
        )

        # Normalize to 0-1
        max_possible = (
            self._config.rsi_weight +
            self._config.macd_weight +
            self._config.volume_weight +
            self._config.trend_weight
        )

        strength = total / max_possible if max_possible > 0 else 0
        return round(min(strength, 1.0), 3)

    def _calculate_confidence(
        self,
        scores: dict[str, float],
        direction: SignalDirection,
    ) -> float:
        """
        Calculate signal confidence based on agreement.

        Returns:
            Confidence 0.0-1.0
        """
        if direction == SignalDirection.NEUTRAL:
            return 0.0

        # Count agreeing indicators
        is_long = direction == SignalDirection.LONG
        agreement = 0
        total = 0

        if scores["rsi_score"] != 0:
            total += 1
            if (scores["rsi_score"] > 0) == is_long:
                agreement += 1

        if scores["macd_score"] != 0:
            total += 1
            if (scores["macd_score"] > 0) == is_long:
                agreement += 1

        if scores["trend_score"] != 0:
            total += 1
            if (scores["trend_score"] > 0) == is_long:
                agreement += 1

        if scores["volume_score"] > 0:
            total += 1
            agreement += 0.5  # Volume is neutral confirmation

        confidence = agreement / total if total > 0 else 0.5
        return round(confidence, 3)

    def _build_reasoning(
        self,
        direction: SignalDirection,
        scores: dict[str, float],
        rsi: float,
        macd: MACDResult,
    ) -> str:
        """Build human-readable reasoning."""
        reasons = []

        if direction == SignalDirection.LONG:
            if scores["rsi_score"] > 0:
                reasons.append(f"RSI oversold ({rsi:.1f})")
            if scores["macd_score"] > 0:
                reasons.append("MACD bullish crossover")
            if scores["trend_score"] > 0:
                reasons.append("Price above trend EMA")
            if scores["volume_score"] > 0:
                reasons.append("Volume spike confirms")

        elif direction == SignalDirection.SHORT:
            if scores["rsi_score"] < 0:
                reasons.append(f"RSI overbought ({rsi:.1f})")
            if scores["macd_score"] < 0:
                reasons.append("MACD bearish crossover")
            if scores["trend_score"] < 0:
                reasons.append("Price below trend EMA")
            if scores["volume_score"] > 0:
                reasons.append("Volume spike confirms")

        return "; ".join(reasons) if reasons else "Signal generated"
