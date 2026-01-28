# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T19:30:00Z
# Purpose: Market regime detection for strategy selection
# Security: Pure calculations, no side effects
# === END SIGNATURE ===
"""
Regime Detection Module.

Detects market regime (TRENDING, RANGING, VOLATILE) to select optimal strategy.
Uses ATR percentage and EMA slope for classification.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Sequence, Union
import numpy as np

class Regime(str, Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"
    UNKNOWN = "UNKNOWN"

@dataclass(frozen=True)
class RegimeConfig:
    """Configuration for regime detection."""
    atr_pct_volatile: float = 0.03      # ATR > 3% of price = volatile
    atr_pct_low: float = 0.01           # ATR < 1% = low volatility (ranging)
    slope_trending: float = 0.005       # EMA slope > 0.5% = trending
    lookback_slope: int = 20            # Bars for slope calculation
    min_bars: int = 50                  # Minimum data required

@dataclass(frozen=True)
class RegimeResult:
    """Result of regime detection with metadata."""
    regime: Regime
    atr_pct: float
    slope: float
    confidence: float
    reason: str

def _to_float_array(arr: Union[Sequence[float], np.ndarray]) -> np.ndarray:
    """Convert sequence to numpy array, handling edge cases."""
    if isinstance(arr, np.ndarray):
        return arr.astype(float)
    return np.array(arr, dtype=float)

def detect_regime(
    closes: Union[Sequence[float], np.ndarray],
    atr_values: Union[Sequence[float], np.ndarray],
    ema_values: Union[Sequence[float], np.ndarray],
    cfg: RegimeConfig = RegimeConfig(),
) -> RegimeResult:
    """
    Detect market regime based on volatility and trend.
    
    Args:
        closes: Close prices
        atr_values: ATR values (same length as closes)
        ema_values: EMA values for slope calculation
        cfg: Configuration parameters
    
    Returns:
        RegimeResult with regime classification and metadata
    """
    closes_arr = _to_float_array(closes)
    atr_arr = _to_float_array(atr_values)
    ema_arr = _to_float_array(ema_values)
    
    n = min(len(closes_arr), len(atr_arr), len(ema_arr))
    
    # Insufficient data
    if n < cfg.min_bars:
        return RegimeResult(
            regime=Regime.UNKNOWN,
            atr_pct=0.0,
            slope=0.0,
            confidence=0.0,
            reason=f"INSUFFICIENT_DATA({n}<{cfg.min_bars})"
        )
    
    # Get latest values
    close = closes_arr[-1]
    atr = atr_arr[-1]
    
    # Validate
    if close <= 0 or not np.isfinite(close) or not np.isfinite(atr):
        return RegimeResult(
            regime=Regime.UNKNOWN,
            atr_pct=0.0,
            slope=0.0,
            confidence=0.0,
            reason="INVALID_DATA"
        )
    
    # Calculate ATR percentage
    atr_pct = atr / close
    
    # Calculate EMA slope
    k = min(cfg.lookback_slope, n - 1)
    ema_start = ema_arr[-1 - k]
    ema_end = ema_arr[-1]
    
    if ema_start <= 0 or not np.isfinite(ema_start) or not np.isfinite(ema_end):
        slope = 0.0
    else:
        slope = (ema_end - ema_start) / ema_start
    
    # Classify regime
    if atr_pct >= cfg.atr_pct_volatile:
        regime = Regime.VOLATILE
        confidence = min(1.0, atr_pct / cfg.atr_pct_volatile)
        reason = f"HIGH_ATR({atr_pct:.4f})"
    elif abs(slope) >= cfg.slope_trending:
        if slope > 0:
            regime = Regime.TRENDING_UP
        else:
            regime = Regime.TRENDING_DOWN
        confidence = min(1.0, abs(slope) / cfg.slope_trending)
        reason = f"SLOPE({slope:.4f})"
    elif atr_pct <= cfg.atr_pct_low:
        regime = Regime.RANGING
        confidence = 1.0 - (atr_pct / cfg.atr_pct_low)
        reason = f"LOW_ATR({atr_pct:.4f})"
    else:
        regime = Regime.RANGING
        confidence = 0.5
        reason = "DEFAULT_RANGING"
    
    return RegimeResult(
        regime=regime,
        atr_pct=atr_pct,
        slope=slope,
        confidence=confidence,
        reason=reason
    )


class MarketRegimeDetector:
    """
    Market regime detector class wrapper for TZ v1.0 compatibility.

    Provides object-oriented interface to regime detection.
    """

    def __init__(self, config: RegimeConfig = RegimeConfig()):
        """
        Initialize detector with configuration.

        Args:
            config: RegimeConfig with detection thresholds
        """
        self._config = config

    def detect(
        self,
        closes: Union[Sequence[float], np.ndarray],
        atr_values: Union[Sequence[float], np.ndarray],
        ema_values: Union[Sequence[float], np.ndarray],
    ) -> RegimeResult:
        """
        Detect market regime from price data.

        Args:
            closes: Close prices
            atr_values: ATR values
            ema_values: EMA values for slope calculation

        Returns:
            RegimeResult with regime classification
        """
        return detect_regime(closes, atr_values, ema_values, self._config)

    @property
    def config(self) -> RegimeConfig:
        """Get current configuration."""
        return self._config
