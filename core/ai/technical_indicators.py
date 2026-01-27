# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T14:00:00Z
# Purpose: Technical indicators for trading signals (RSI, MACD, BB, ATR)
# === END SIGNATURE ===
"""
Technical Indicators Module.

High-performance calculations using numpy.
All functions are pure and stateless.

Indicators:
- RSI (Relative Strength Index)
- MACD (Moving Average Convergence Divergence)
- Bollinger Bands
- ATR (Average True Range)
- EMA (Exponential Moving Average)
- SMA (Simple Moving Average)
- Volume Profile
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np


@dataclass(frozen=True)
class MACDResult:
    """MACD calculation result."""
    macd_line: float
    signal_line: float
    histogram: float


@dataclass(frozen=True)
class BollingerBandsResult:
    """Bollinger Bands result."""
    upper: float
    middle: float
    lower: float
    bandwidth: float  # (upper - lower) / middle
    percent_b: float  # (price - lower) / (upper - lower)


@dataclass(frozen=True)
class VolumeProfile:
    """Volume analysis result."""
    avg_volume: float
    current_ratio: float  # current / avg
    trend: Literal["increasing", "decreasing", "stable"]
    is_spike: bool  # > 2x average


class TechnicalIndicators:
    """
    Technical indicators calculator.

    All methods are static and pure - no side effects.
    Uses numpy for vectorized calculations.
    """

    # === RSI ===

    @staticmethod
    def rsi(closes: np.ndarray, period: int = 14) -> float:
        """
        Calculate Relative Strength Index.

        RSI = 100 - (100 / (1 + RS))
        RS = Average Gain / Average Loss

        Interpretation:
        - RSI > 70: Overbought (potential SHORT)
        - RSI < 30: Oversold (potential LONG)
        - RSI 40-60: Neutral zone

        Args:
            closes: Array of closing prices (oldest first)
            period: RSI period (default 14)

        Returns:
            RSI value 0-100, or NaN if insufficient data
        """
        if len(closes) < period + 1:
            return float('nan')

        # Calculate price changes
        deltas = np.diff(closes)

        # Separate gains and losses
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        # Use Wilder's smoothing (EMA with alpha = 1/period)
        # First average is SMA
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])

        # Continue with EMA for remaining values
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))

        return round(rsi, 2)

    # === MACD ===

    @staticmethod
    def ema(closes: np.ndarray, period: int) -> float:
        """
        Calculate Exponential Moving Average.

        EMA = Price * k + EMA_prev * (1 - k)
        k = 2 / (period + 1)

        Args:
            closes: Array of closing prices
            period: EMA period

        Returns:
            EMA value or NaN if insufficient data
        """
        if len(closes) < period:
            return float('nan')

        k = 2.0 / (period + 1)
        ema = closes[0]

        for price in closes[1:]:
            ema = price * k + ema * (1 - k)

        return round(ema, 8)

    @staticmethod
    def sma(closes: np.ndarray, period: int) -> float:
        """
        Calculate Simple Moving Average.

        Args:
            closes: Array of closing prices
            period: SMA period

        Returns:
            SMA value or NaN if insufficient data
        """
        if len(closes) < period:
            return float('nan')

        return round(np.mean(closes[-period:]), 8)

    @staticmethod
    def macd(
        closes: np.ndarray,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> MACDResult:
        """
        Calculate MACD (Moving Average Convergence Divergence).

        MACD Line = EMA(fast) - EMA(slow)
        Signal Line = EMA(MACD Line, signal period)
        Histogram = MACD Line - Signal Line

        Signals:
        - MACD crosses above signal: Bullish (LONG)
        - MACD crosses below signal: Bearish (SHORT)
        - Histogram increasing: Trend strengthening
        - Histogram decreasing: Trend weakening

        Args:
            closes: Array of closing prices
            fast: Fast EMA period (default 12)
            slow: Slow EMA period (default 26)
            signal: Signal EMA period (default 9)

        Returns:
            MACDResult with macd_line, signal_line, histogram
        """
        if len(closes) < slow + signal:
            return MACDResult(
                macd_line=float('nan'),
                signal_line=float('nan'),
                histogram=float('nan'),
            )

        # Calculate EMAs for full series
        k_fast = 2.0 / (fast + 1)
        k_slow = 2.0 / (slow + 1)
        k_signal = 2.0 / (signal + 1)

        ema_fast = closes[0]
        ema_slow = closes[0]

        macd_values = []

        for price in closes:
            ema_fast = price * k_fast + ema_fast * (1 - k_fast)
            ema_slow = price * k_slow + ema_slow * (1 - k_slow)
            macd_values.append(ema_fast - ema_slow)

        # Calculate signal line (EMA of MACD)
        signal_ema = macd_values[0]
        for val in macd_values:
            signal_ema = val * k_signal + signal_ema * (1 - k_signal)

        macd_line = macd_values[-1]
        histogram = macd_line - signal_ema

        return MACDResult(
            macd_line=round(macd_line, 8),
            signal_line=round(signal_ema, 8),
            histogram=round(histogram, 8),
        )

    # === BOLLINGER BANDS ===

    @staticmethod
    def bollinger_bands(
        closes: np.ndarray,
        period: int = 20,
        std_dev: float = 2.0,
        current_price: float | None = None,
    ) -> BollingerBandsResult:
        """
        Calculate Bollinger Bands.

        Middle Band = SMA(period)
        Upper Band = Middle + (std_dev * StdDev)
        Lower Band = Middle - (std_dev * StdDev)

        Signals:
        - Price near lower band: Potential LONG (mean reversion)
        - Price near upper band: Potential SHORT
        - Band squeeze (low bandwidth): Breakout incoming
        - High %B (> 1): Price above upper band
        - Low %B (< 0): Price below lower band

        Args:
            closes: Array of closing prices
            period: SMA period (default 20)
            std_dev: Standard deviation multiplier (default 2.0)
            current_price: Price for %B calculation (default: last close)

        Returns:
            BollingerBandsResult with upper, middle, lower, bandwidth, percent_b
        """
        if len(closes) < period:
            return BollingerBandsResult(
                upper=float('nan'),
                middle=float('nan'),
                lower=float('nan'),
                bandwidth=float('nan'),
                percent_b=float('nan'),
            )

        window = closes[-period:]
        middle = np.mean(window)
        std = np.std(window, ddof=1)  # Sample std dev

        upper = middle + (std_dev * std)
        lower = middle - (std_dev * std)

        # Bandwidth: measure of volatility
        bandwidth = (upper - lower) / middle if middle != 0 else 0

        # %B: position within bands
        price = current_price if current_price is not None else closes[-1]
        band_range = upper - lower
        percent_b = (price - lower) / band_range if band_range != 0 else 0.5

        return BollingerBandsResult(
            upper=round(upper, 8),
            middle=round(middle, 8),
            lower=round(lower, 8),
            bandwidth=round(bandwidth, 4),
            percent_b=round(percent_b, 4),
        )

    # === ATR ===

    @staticmethod
    def atr(
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        period: int = 14,
    ) -> float:
        """
        Calculate Average True Range.

        True Range = max(
            High - Low,
            abs(High - Previous Close),
            abs(Low - Previous Close)
        )
        ATR = EMA(True Range, period)

        Usage:
        - Stop Loss = Entry - (ATR * multiplier)
        - Position Size = Risk$ / (ATR * multiplier)
        - Volatility measure for position sizing

        Args:
            highs: Array of high prices
            lows: Array of low prices
            closes: Array of closing prices
            period: ATR period (default 14)

        Returns:
            ATR value (absolute, not percentage)
        """
        n = len(closes)
        if n < period + 1 or len(highs) != n or len(lows) != n:
            return float('nan')

        # Calculate True Range for each bar
        true_ranges = []

        for i in range(1, n):
            high_low = highs[i] - lows[i]
            high_close = abs(highs[i] - closes[i - 1])
            low_close = abs(lows[i] - closes[i - 1])
            tr = max(high_low, high_close, low_close)
            true_ranges.append(tr)

        # Wilder's smoothing (EMA with alpha = 1/period)
        atr = np.mean(true_ranges[:period])

        for tr in true_ranges[period:]:
            atr = (atr * (period - 1) + tr) / period

        return round(atr, 8)

    # === VOLUME ===

    @staticmethod
    def volume_profile(
        volumes: np.ndarray,
        period: int = 20,
    ) -> VolumeProfile:
        """
        Analyze volume profile.

        Args:
            volumes: Array of volume values
            period: Period for average calculation

        Returns:
            VolumeProfile with avg, ratio, trend, is_spike
        """
        if len(volumes) < period:
            return VolumeProfile(
                avg_volume=float('nan'),
                current_ratio=float('nan'),
                trend="stable",
                is_spike=False,
            )

        avg_volume = np.mean(volumes[-period:])
        current_volume = volumes[-1]
        current_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0

        # Determine trend (compare first half vs second half)
        half = period // 2
        first_half_avg = np.mean(volumes[-period:-half])
        second_half_avg = np.mean(volumes[-half:])

        change_pct = (second_half_avg - first_half_avg) / first_half_avg if first_half_avg > 0 else 0

        if change_pct > 0.1:
            trend = "increasing"
        elif change_pct < -0.1:
            trend = "decreasing"
        else:
            trend = "stable"

        is_spike = current_ratio > 2.0

        return VolumeProfile(
            avg_volume=round(avg_volume, 2),
            current_ratio=round(current_ratio, 2),
            trend=trend,
            is_spike=is_spike,
        )

    # === HELPER METHODS ===

    @staticmethod
    def is_overbought(rsi: float, threshold: float = 70.0) -> bool:
        """Check if RSI indicates overbought condition."""
        return not np.isnan(rsi) and rsi > threshold

    @staticmethod
    def is_oversold(rsi: float, threshold: float = 30.0) -> bool:
        """Check if RSI indicates oversold condition."""
        return not np.isnan(rsi) and rsi < threshold

    @staticmethod
    def macd_crossover(
        macd_current: MACDResult,
        macd_previous: MACDResult,
    ) -> Literal["bullish", "bearish", "none"]:
        """
        Detect MACD crossover.

        Returns:
            "bullish" if MACD crosses above signal
            "bearish" if MACD crosses below signal
            "none" if no crossover
        """
        if np.isnan(macd_current.histogram) or np.isnan(macd_previous.histogram):
            return "none"

        # Bullish: histogram goes from negative to positive
        if macd_previous.histogram < 0 and macd_current.histogram > 0:
            return "bullish"

        # Bearish: histogram goes from positive to negative
        if macd_previous.histogram > 0 and macd_current.histogram < 0:
            return "bearish"

        return "none"

    @staticmethod
    def bb_signal(
        percent_b: float,
        lower_threshold: float = 0.0,
        upper_threshold: float = 1.0,
    ) -> Literal["long", "short", "neutral"]:
        """
        Generate signal from Bollinger Bands %B.

        Args:
            percent_b: %B value from bollinger_bands()
            lower_threshold: Threshold for long signal (default 0.0)
            upper_threshold: Threshold for short signal (default 1.0)

        Returns:
            "long" if price at/below lower band
            "short" if price at/above upper band
            "neutral" otherwise
        """
        if np.isnan(percent_b):
            return "neutral"

        if percent_b <= lower_threshold:
            return "long"
        elif percent_b >= upper_threshold:
            return "short"

        return "neutral"
