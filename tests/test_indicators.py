# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T18:35:00Z
# Purpose: Unit tests for technical indicators
# Security: Test-only, no production impact
# === END SIGNATURE ===
"""
Unit Tests for Technical Indicators Module.

Tests cover:
- RSI calculation and signals
- MACD calculation and crossovers
- Bollinger Bands calculation
- ATR calculation
- Volume profile analysis
- Edge cases and error handling
"""

import pytest
import numpy as np

from core.ai.technical_indicators import (
    TechnicalIndicators,
    IndicatorResult,
    MACDResult,
    BollingerResult,
    VolumeProfile,
)


class TestRSI:
    """Tests for RSI indicator."""

    def test_rsi_basic(self):
        """Test basic RSI calculation."""
        # Uptrend data
        closes = np.array([100 + i * 0.5 for i in range(20)])
        result = TechnicalIndicators.rsi(closes)

        assert isinstance(result, IndicatorResult)
        assert 0 <= result.value <= 100
        assert result.signal in ["BUY", "SELL", "NEUTRAL"]
        assert 0 <= result.strength <= 1

    def test_rsi_oversold(self):
        """Test RSI in oversold zone."""
        # Strong downtrend
        closes = np.array([100 - i * 2 for i in range(20)])
        result = TechnicalIndicators.rsi(closes)

        assert result.value < 30
        assert result.signal == "BUY"

    def test_rsi_overbought(self):
        """Test RSI in overbought zone."""
        # Strong uptrend
        closes = np.array([100 + i * 2 for i in range(20)])
        result = TechnicalIndicators.rsi(closes)

        assert result.value > 70
        assert result.signal == "SELL"

    def test_rsi_neutral(self):
        """Test RSI in neutral zone."""
        # Sideways movement
        closes = np.array([100 + np.sin(i * 0.5) * 2 for i in range(20)])
        result = TechnicalIndicators.rsi(closes)

        assert 30 <= result.value <= 70
        assert result.signal == "NEUTRAL"

    def test_rsi_insufficient_data(self):
        """Test RSI with insufficient data raises error."""
        closes = np.array([100, 101, 102])

        with pytest.raises(ValueError, match="requires at least"):
            TechnicalIndicators.rsi(closes)

    def test_rsi_custom_period(self):
        """Test RSI with custom period."""
        closes = np.array([100 + i * 0.5 for i in range(30)])
        result = TechnicalIndicators.rsi(closes, period=21)

        assert isinstance(result, IndicatorResult)


class TestMACD:
    """Tests for MACD indicator."""

    def test_macd_basic(self):
        """Test basic MACD calculation."""
        closes = np.array([100 + i * 0.3 + np.sin(i * 0.2) * 2 for i in range(50)])
        result = TechnicalIndicators.macd(closes)

        assert isinstance(result, MACDResult)
        assert isinstance(result.macd_line, float)
        assert isinstance(result.signal_line, float)
        assert isinstance(result.histogram, float)
        assert result.crossover in ["BULLISH", "BEARISH", "NONE"]

    def test_macd_bullish_crossover(self):
        """Test MACD bullish crossover detection."""
        # Create data with recent upward momentum
        closes = np.concatenate([
            np.array([100 - i * 0.3 for i in range(30)]),  # Downtrend
            np.array([85 + i * 1.0 for i in range(20)]),   # Strong reversal up
        ])
        result = TechnicalIndicators.macd(closes)

        # Should detect bullish momentum
        assert result.histogram > 0 or result.crossover == "BULLISH"

    def test_macd_insufficient_data(self):
        """Test MACD with insufficient data raises error."""
        closes = np.array([100 + i for i in range(20)])

        with pytest.raises(ValueError, match="requires at least"):
            TechnicalIndicators.macd(closes)


class TestBollingerBands:
    """Tests for Bollinger Bands indicator."""

    def test_bollinger_basic(self):
        """Test basic Bollinger Bands calculation."""
        closes = np.array([100 + np.sin(i * 0.3) * 5 for i in range(30)])
        result = TechnicalIndicators.bollinger_bands(closes)

        assert isinstance(result, BollingerResult)
        assert result.upper > result.middle > result.lower
        assert 0 <= result.position <= 1
        assert result.width >= 0

    def test_bollinger_squeeze(self):
        """Test Bollinger squeeze detection."""
        # Low volatility data
        closes = np.array([100 + i * 0.01 for i in range(30)])
        result = TechnicalIndicators.bollinger_bands(closes, squeeze_threshold=0.05)

        # Very tight bands = squeeze
        assert result.squeeze == True

    def test_bollinger_position_bounds(self):
        """Test position is clamped to 0-1."""
        # Price at upper band
        closes = np.array([100 + i * 0.5 for i in range(30)])
        result = TechnicalIndicators.bollinger_bands(closes)

        assert 0 <= result.position <= 1

    def test_bollinger_insufficient_data(self):
        """Test Bollinger with insufficient data raises error."""
        closes = np.array([100, 101, 102])

        with pytest.raises(ValueError, match="requires at least"):
            TechnicalIndicators.bollinger_bands(closes)


class TestATR:
    """Tests for ATR indicator."""

    def test_atr_basic(self):
        """Test basic ATR calculation."""
        n = 20
        highs = np.array([102 + i * 0.1 for i in range(n)])
        lows = np.array([98 + i * 0.1 for i in range(n)])
        closes = np.array([100 + i * 0.1 for i in range(n)])

        atr = TechnicalIndicators.atr(highs, lows, closes)

        assert isinstance(atr, float)
        assert atr > 0

    def test_atr_volatile_market(self):
        """Test ATR in volatile market."""
        n = 20
        highs = np.array([100 + i * 0.5 + 5 for i in range(n)])
        lows = np.array([100 + i * 0.5 - 5 for i in range(n)])
        closes = np.array([100 + i * 0.5 for i in range(n)])

        atr = TechnicalIndicators.atr(highs, lows, closes)

        # Higher volatility = higher ATR
        assert atr >= 5

    def test_atr_insufficient_data(self):
        """Test ATR with insufficient data raises error."""
        highs = np.array([102, 103, 104])
        lows = np.array([98, 99, 100])
        closes = np.array([100, 101, 102])

        with pytest.raises(ValueError, match="requires at least"):
            TechnicalIndicators.atr(highs, lows, closes)


class TestVolumeProfile:
    """Tests for Volume Profile indicator."""

    def test_volume_profile_basic(self):
        """Test basic volume profile calculation."""
        volumes = np.array([1000 + i * 10 for i in range(25)])
        result = TechnicalIndicators.volume_profile(volumes)

        assert isinstance(result, VolumeProfile)
        assert result.avg_volume > 0
        assert result.current_ratio > 0
        assert result.trend in ["INCREASING", "DECREASING", "STABLE"]

    def test_volume_spike_detection(self):
        """Test volume spike detection."""
        # Normal volume then big spike
        volumes = np.array([1000] * 19 + [3000])
        result = TechnicalIndicators.volume_profile(volumes, spike_threshold=2.0)

        assert result.spike == True
        assert result.current_ratio >= 2.0

    def test_volume_trend_increasing(self):
        """Test increasing volume trend detection."""
        volumes = np.array([1000 + i * 100 for i in range(25)])
        result = TechnicalIndicators.volume_profile(volumes)

        assert result.trend == "INCREASING"

    def test_volume_trend_decreasing(self):
        """Test decreasing volume trend detection."""
        volumes = np.array([2500 - i * 100 for i in range(25)])
        result = TechnicalIndicators.volume_profile(volumes)

        assert result.trend == "DECREASING"

    def test_volume_insufficient_data(self):
        """Test volume profile with insufficient data raises error."""
        volumes = np.array([1000, 1100, 1200])

        with pytest.raises(ValueError, match="requires at least"):
            TechnicalIndicators.volume_profile(volumes)


class TestEMASMA:
    """Tests for EMA and SMA helpers."""

    def test_sma_basic(self):
        """Test SMA calculation."""
        closes = np.array([100, 102, 104, 106, 108, 110, 112, 114, 116, 118])
        sma = TechnicalIndicators.sma(closes, period=5)

        # SMA of last 5 values: (110+112+114+116+118)/5 = 114
        assert sma == 114.0

    def test_ema_basic(self):
        """Test EMA calculation."""
        closes = np.array([100 + i for i in range(15)])
        ema = TechnicalIndicators.ema(closes, period=10)

        assert isinstance(ema, float)
        # EMA should be close to SMA for linear data
        sma = TechnicalIndicators.sma(closes, period=10)
        assert abs(ema - sma) < 5

    def test_sma_insufficient_data(self):
        """Test SMA with insufficient data raises error."""
        closes = np.array([100, 101, 102])

        with pytest.raises(ValueError, match="requires at least"):
            TechnicalIndicators.sma(closes, period=10)


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_flat_prices(self):
        """Test indicators with flat prices."""
        closes = np.array([100.0] * 30)

        # RSI should be around 50 (neutral) or handle div-by-zero
        rsi = TechnicalIndicators.rsi(closes)
        assert 0 <= rsi.value <= 100

        # Bollinger bands should have zero width
        bb = TechnicalIndicators.bollinger_bands(closes)
        assert bb.width == 0 or abs(bb.width) < 0.001

    def test_negative_prices(self):
        """Test that indicators handle negative values gracefully."""
        # Some indicators may not make sense with negative prices
        # but should not crash
        closes = np.array([-10 + i * 0.5 for i in range(30)])

        # RSI should still calculate
        rsi = TechnicalIndicators.rsi(closes)
        assert 0 <= rsi.value <= 100

    def test_large_values(self):
        """Test indicators with large price values (like BTC)."""
        closes = np.array([50000 + i * 100 for i in range(30)])

        rsi = TechnicalIndicators.rsi(closes)
        assert 0 <= rsi.value <= 100

        bb = TechnicalIndicators.bollinger_bands(closes)
        assert bb.upper > bb.lower

    def test_small_values(self):
        """Test indicators with very small price values."""
        closes = np.array([0.00001 + i * 0.000001 for i in range(30)])

        rsi = TechnicalIndicators.rsi(closes)
        assert 0 <= rsi.value <= 100


class TestDataclasses:
    """Tests for dataclass properties."""

    def test_indicator_result_frozen(self):
        """Test IndicatorResult is immutable."""
        result = IndicatorResult(
            value=50.0,
            signal="NEUTRAL",
            strength=0.5,
            description="Test"
        )

        with pytest.raises(AttributeError):
            result.value = 60.0

    def test_macd_result_frozen(self):
        """Test MACDResult is immutable."""
        result = MACDResult(
            macd_line=0.5,
            signal_line=0.3,
            histogram=0.2,
            crossover="NONE",
            trend_strength=0.5
        )

        with pytest.raises(AttributeError):
            result.macd_line = 1.0

    def test_bollinger_result_frozen(self):
        """Test BollingerResult is immutable."""
        result = BollingerResult(
            upper=105.0,
            middle=100.0,
            lower=95.0,
            width=0.1,
            position=0.5,
            squeeze=False
        )

        with pytest.raises(AttributeError):
            result.upper = 110.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
