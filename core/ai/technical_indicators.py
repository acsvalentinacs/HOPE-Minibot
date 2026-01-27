# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T18:30:00Z
# Purpose: Technical indicators for trading signals
# Security: No external data loading, pure calculations
# === END SIGNATURE ===
"""
Technical Indicators Module.

All indicators implemented in pure numpy without external dependencies.
Each function is a static method, stateless, thread-safe.
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class IndicatorResult:
    value: float
    signal: Literal["BUY", "SELL", "NEUTRAL"]
    strength: float
    description: str


@dataclass(frozen=True)
class MACDResult:
    macd_line: float
    signal_line: float
    histogram: float
    crossover: Literal["BULLISH", "BEARISH", "NONE"]
    trend_strength: float


@dataclass(frozen=True)
class BollingerResult:
    upper: float
    middle: float
    lower: float
    width: float
    position: float
    squeeze: bool


@dataclass(frozen=True)
class VolumeProfile:
    avg_volume: float
    current_ratio: float
    trend: Literal["INCREASING", "DECREASING", "STABLE"]
    spike: bool


class TechnicalIndicators:

    @staticmethod
    def rsi(closes: np.ndarray, period: int = 14) -> IndicatorResult:
        if len(closes) < period + 1:
            raise ValueError(f"RSI requires at least {period + 1} values")

        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            rsi_value = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi_value = 100 - (100 / (1 + rs))

        if rsi_value < 30:
            signal = "BUY"
            strength = (30 - rsi_value) / 30
            description = f"Oversold (RSI={rsi_value:.1f})"
        elif rsi_value > 70:
            signal = "SELL"
            strength = (rsi_value - 70) / 30
            description = f"Overbought (RSI={rsi_value:.1f})"
        else:
            signal = "NEUTRAL"
            strength = abs(rsi_value - 50) / 50
            description = f"Neutral zone (RSI={rsi_value:.1f})"

        return IndicatorResult(
            value=rsi_value,
            signal=signal,
            strength=min(1.0, strength),
            description=description,
        )

    @staticmethod
    def macd(closes: np.ndarray, fast: int = 12, slow: int = 26, signal_period: int = 9) -> MACDResult:
        min_required = slow + signal_period
        if len(closes) < min_required:
            raise ValueError(f"MACD requires at least {min_required} values")

        fast_ema = TechnicalIndicators._ema(closes, fast)
        slow_ema = TechnicalIndicators._ema(closes, slow)
        macd_line = fast_ema - slow_ema

        macd_series = []
        for i in range(slow - 1, len(closes)):
            fast_e = TechnicalIndicators._ema(closes[:i+1], fast)
            slow_e = TechnicalIndicators._ema(closes[:i+1], slow)
            macd_series.append(fast_e - slow_e)

        macd_array = np.array(macd_series)
        signal_line = TechnicalIndicators._ema(macd_array, signal_period)
        histogram = macd_line - signal_line

        if len(macd_array) >= 2:
            prev_macd = macd_array[-2]
            prev_signal = TechnicalIndicators._ema(macd_array[:-1], signal_period)
            if prev_macd <= prev_signal and macd_line > signal_line:
                crossover = "BULLISH"
            elif prev_macd >= prev_signal and macd_line < signal_line:
                crossover = "BEARISH"
            else:
                crossover = "NONE"
        else:
            crossover = "NONE"

        trend_strength = min(1.0, abs(histogram) / (abs(macd_line) + 0.0001))

        return MACDResult(macd_line=macd_line, signal_line=signal_line, histogram=histogram, crossover=crossover, trend_strength=trend_strength)

    @staticmethod
    def bollinger_bands(closes: np.ndarray, period: int = 20, std_dev: float = 2.0, squeeze_threshold: float = 0.02) -> BollingerResult:
        if len(closes) < period:
            raise ValueError(f"Bollinger requires at least {period} values")

        middle = np.mean(closes[-period:])
        std = np.std(closes[-period:])
        upper = middle + (std_dev * std)
        lower = middle - (std_dev * std)
        width = (upper - lower) / middle

        current_price = closes[-1]
        position = (current_price - lower) / (upper - lower) if upper != lower else 0.5
        position = max(0.0, min(1.0, position))
        squeeze = width < squeeze_threshold

        return BollingerResult(upper=upper, middle=middle, lower=lower, width=width, position=position, squeeze=squeeze)

    @staticmethod
    def atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
        if len(highs) < period + 1:
            raise ValueError(f"ATR requires at least {period + 1} values")

        tr_list = []
        for i in range(1, len(closes)):
            high_low = highs[i] - lows[i]
            high_close = abs(highs[i] - closes[i - 1])
            low_close = abs(lows[i] - closes[i - 1])
            tr_list.append(max(high_low, high_close, low_close))

        tr_array = np.array(tr_list)
        atr_value = np.mean(tr_array[:period])
        for i in range(period, len(tr_array)):
            atr_value = (atr_value * (period - 1) + tr_array[i]) / period

        return atr_value

    @staticmethod
    def volume_profile(volumes: np.ndarray, period: int = 20, spike_threshold: float = 2.0) -> VolumeProfile:
        if len(volumes) < period:
            raise ValueError(f"Volume profile requires at least {period} values")

        avg_volume = np.mean(volumes[-period:])
        current_volume = volumes[-1]
        current_ratio = current_volume / avg_volume if avg_volume > 0 else 0

        first_half_avg = np.mean(volumes[-period:-period//2])
        second_half_avg = np.mean(volumes[-period//2:])

        if second_half_avg > first_half_avg * 1.1:
            trend = "INCREASING"
        elif second_half_avg < first_half_avg * 0.9:
            trend = "DECREASING"
        else:
            trend = "STABLE"

        spike = current_ratio >= spike_threshold
        return VolumeProfile(avg_volume=avg_volume, current_ratio=current_ratio, trend=trend, spike=spike)

    @staticmethod
    def _ema(values: np.ndarray, period: int) -> float:
        if len(values) < period:
            return float(np.mean(values))
        multiplier = 2 / (period + 1)
        ema = float(np.mean(values[:period]))
        for value in values[period:]:
            ema = (value - ema) * multiplier + ema
        return ema

    @staticmethod
    def sma(closes: np.ndarray, period: int) -> float:
        if len(closes) < period:
            raise ValueError(f"SMA requires at least {period} values")
        return float(np.mean(closes[-period:]))

    @staticmethod
    def ema(closes: np.ndarray, period: int) -> float:
        return TechnicalIndicators._ema(closes, period)
