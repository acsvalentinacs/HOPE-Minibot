# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T19:30:00Z
# Purpose: Breakout trading strategy
# Security: Spot-only, no SHORT positions
# === END SIGNATURE ===
"""
Breakout Strategy Module.

Enters on price breakout above recent high with volume confirmation.
Optimal for TRENDING/VOLATILE markets. Spot-only: no SHORT positions.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import numpy as np
from core.ai.signal_engine import SignalEngine, SignalEngineConfig, TradingSignal, MarketData, SignalDirection
from core.ai.technical_indicators import TechnicalIndicators
from core.strategy.base import BaseStrategy, StrategyConfig, Position, PositionSide

@dataclass
class BreakoutConfig(StrategyConfig):
    """Configuration for Breakout Strategy."""
    # Breakout detection
    lookback_period: int = 20         # Bars to find high/low
    breakout_threshold: float = 0.002  # Min % above high to confirm breakout
    
    # Volume confirmation
    require_volume: bool = True
    min_volume_ratio: float = 1.5     # Volume must be 1.5x average
    
    # ATR filter (avoid breakouts in low volatility)
    min_atr_pct: float = 0.01         # ATR must be > 1% of price
    
    # Retest filter (optional: wait for pullback)
    wait_for_retest: bool = False
    retest_tolerance: float = 0.005   # How close to breakout level
    
    # Exit
    trailing_atr_mult: float = 2.0    # Trail stop at 2x ATR

class BreakoutStrategy(BaseStrategy):
    """
    Breakout Strategy (Spot-only).
    
    Entry: Price breaks above N-bar high with volume surge
    Exit: Trailing stop based on ATR
    
    SHORT signals (breakdown) are BLOCKED (Spot mode).
    """
    name = "breakout"
    
    def __init__(self, config: Optional[BreakoutConfig] = None):
        self.bo_config = config or BreakoutConfig()
        super().__init__(self.bo_config)
        self._signal_engine = SignalEngine(SignalEngineConfig(
            min_confidence=self.bo_config.min_confidence,
        ))
        self._last_breakout_level: dict = {}  # symbol -> price
    
    def generate_signal(self, market_data: MarketData) -> Optional[TradingSignal]:
        """Generate breakout signal. Spot-only: no SHORT."""
        try:
            return self._generate_signal_impl(market_data)
        except Exception:
            return None

    def should_enter(self, market_data: MarketData) -> bool:
        """Check if breakout conditions favor entry."""
        signal = self.generate_signal(market_data)
        return signal is not None and signal.confidence >= self.config.min_confidence
    
    def _generate_signal_impl(self, market_data: MarketData) -> Optional[TradingSignal]:
        n = len(market_data.closes)
        if n < self.bo_config.lookback_period + 1:
            return None
        
        # Calculate levels
        lookback = self.bo_config.lookback_period
        recent_high = float(np.max(market_data.highs[-lookback-1:-1]))
        recent_low = float(np.min(market_data.lows[-lookback-1:-1]))
        current_price = float(market_data.closes[-1])
        current_high = float(market_data.highs[-1])
        
        # ATR filter
        atr = TechnicalIndicators.atr(market_data.highs, market_data.lows, market_data.closes)
        atr_pct = atr / current_price
        if atr_pct < self.bo_config.min_atr_pct:
            return None  # Too low volatility
        
        # Volume filter
        if self.bo_config.require_volume:
            volume = TechnicalIndicators.volume_profile(market_data.volumes)
            if volume.current_ratio < self.bo_config.min_volume_ratio:
                return None  # Insufficient volume
        
        # LONG breakout: price breaks above recent high
        breakout_level = recent_high * (1 + self.bo_config.breakout_threshold)
        if current_high >= breakout_level:
            self._last_breakout_level[market_data.symbol] = recent_high
            return self._create_breakout_signal(market_data, SignalDirection.LONG, recent_high, atr)
        
        # SHORT breakdown would be here - BLOCKED for Spot
        # breakdown_level = recent_low * (1 - self.bo_config.breakout_threshold)
        # if current_low <= breakdown_level:
        #     BLOCKED: Spot mode does not support SHORT
        
        return None
    
    def _create_breakout_signal(self, market_data: MarketData, direction: SignalDirection, level: float, atr: float) -> TradingSignal:
        """Create breakout signal."""
        import hashlib
        
        current_price = float(market_data.closes[-1])
        
        # Only LONG allowed in Spot
        if direction != SignalDirection.LONG:
            return None
        
        stop_loss = level - atr  # Stop below breakout level
        take_profit = current_price + (atr * 3.0)  # 3:1 R:R
        
        content = f"{market_data.symbol}:{market_data.timestamp}:BO:{level:.2f}"
        signal_id = f"sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"
        
        # Calculate confidence based on volume and ATR
        volume = TechnicalIndicators.volume_profile(market_data.volumes)
        confidence = min(0.85, 0.6 + (volume.current_ratio - 1.0) * 0.1)
        
        rsi = TechnicalIndicators.rsi(market_data.closes)
        
        return TradingSignal(
            signal_id=signal_id,
            symbol=market_data.symbol,
            direction=direction,
            confidence=confidence,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            timestamp=market_data.timestamp,
            technical_score=0.7,
            ml_score=0.0,
            sentiment_score=0.0,
            volume_score=volume.current_ratio / 2.0,
            rsi=rsi.value,
            macd_histogram=0.0,
            bollinger_position=0.5,
            atr=atr,
            invalidation_price=stop_loss,
            notes=f"Breakout:LONG:level={level:.2f}"
        )
    
    def should_exit(self, position: Position, market_data: MarketData) -> Optional[str]:
        """Exit on trailing stop or breakdown."""
        try:
            return self._should_exit_impl(position, market_data)
        except Exception:
            return "error"
    
    def _should_exit_impl(self, position: Position, market_data: MarketData) -> Optional[str]:
        if position.symbol != market_data.symbol:
            return None
        
        current_price = float(market_data.closes[-1])
        
        # Check if price fell back below breakout level
        breakout_level = self._last_breakout_level.get(market_data.symbol)
        if breakout_level and position.side == PositionSide.LONG:
            if current_price < breakout_level * 0.995:  # 0.5% tolerance
                return "breakout_failed"
        
        return None
