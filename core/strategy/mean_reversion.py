# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T19:30:00Z
# Purpose: Mean reversion trading strategy
# Security: Spot-only, no SHORT positions
# === END SIGNATURE ===
"""
Mean Reversion Strategy Module.

Enters when price deviates from mean (Bollinger Bands), exits on return.
Optimal for RANGING markets. Spot-only: no SHORT positions.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from core.ai.signal_engine import SignalEngine, SignalEngineConfig, TradingSignal, MarketData, SignalDirection
from core.ai.technical_indicators import TechnicalIndicators
from core.strategy.base import BaseStrategy, StrategyConfig, Position, PositionSide

@dataclass
class MeanReversionConfig(StrategyConfig):
    """Configuration for Mean Reversion Strategy."""
    # Bollinger settings
    bb_period: int = 20
    bb_std: float = 2.0
    
    # Entry: position in BB (0=lower, 1=upper)
    entry_lower_threshold: float = 0.15   # Enter LONG when below 15%
    entry_upper_threshold: float = 0.85   # Would be SHORT (disabled in Spot)
    
    # Exit: return to mean
    exit_middle_threshold: float = 0.45   # Exit when above 45%
    
    # RSI confirmation
    use_rsi_filter: bool = True
    rsi_oversold: float = 35.0
    rsi_overbought: float = 65.0
    
    # Squeeze filter (avoid low volatility traps)
    avoid_squeeze: bool = True
    squeeze_threshold: float = 0.02

class MeanReversionStrategy(BaseStrategy):
    """
    Mean Reversion Strategy (Spot-only).
    
    Entry: Price near lower Bollinger Band + RSI oversold
    Exit: Price returns to middle band
    
    SHORT signals are BLOCKED (Spot mode).
    """
    name = "mean_reversion"
    
    def __init__(self, config: Optional[MeanReversionConfig] = None):
        self.mr_config = config or MeanReversionConfig()
        super().__init__(self.mr_config)
        self._signal_engine = SignalEngine(SignalEngineConfig(
            min_confidence=self.mr_config.min_confidence,
            bb_period=self.mr_config.bb_period,
            bb_std=self.mr_config.bb_std,
        ))
    
    def generate_signal(self, market_data: MarketData) -> Optional[TradingSignal]:
        """Generate mean reversion signal. Spot-only: no SHORT."""
        try:
            return self._generate_signal_impl(market_data)
        except Exception:
            return None
    
    def _generate_signal_impl(self, market_data: MarketData) -> Optional[TradingSignal]:
        # Calculate Bollinger Bands
        bb = TechnicalIndicators.bollinger_bands(
            market_data.closes,
            self.mr_config.bb_period,
            self.mr_config.bb_std,
            self.mr_config.squeeze_threshold
        )
        
        # Avoid squeeze (low volatility)
        if self.mr_config.avoid_squeeze and bb.squeeze:
            return None
        
        # Check RSI
        rsi = TechnicalIndicators.rsi(market_data.closes)
        
        # LONG entry: price near lower band + RSI oversold
        if bb.position <= self.mr_config.entry_lower_threshold:
            if self.mr_config.use_rsi_filter and rsi.value > self.mr_config.rsi_oversold:
                return None  # RSI not oversold enough
            
            signal = self._signal_engine.generate_signal(market_data)
            if signal and signal.direction == SignalDirection.LONG:
                return signal
            
            # Force LONG signal if BB position is extreme
            if bb.position <= 0.10:
                return self._create_manual_signal(market_data, SignalDirection.LONG, bb.position)
        
        # SHORT entry would be here - BLOCKED for Spot
        # if bb.position >= self.mr_config.entry_upper_threshold:
        #     BLOCKED: Spot mode does not support SHORT
        
        return None
    
    def _create_manual_signal(self, market_data: MarketData, direction: SignalDirection, position: float) -> Optional[TradingSignal]:
        """Create signal manually when engine returns None but conditions are met."""
        import hashlib
        from core.ai.technical_indicators import TechnicalIndicators
        
        atr = TechnicalIndicators.atr(market_data.highs, market_data.lows, market_data.closes)
        current_price = float(market_data.closes[-1])
        
        # Only LONG allowed in Spot
        if direction != SignalDirection.LONG:
            return None
        
        stop_loss = current_price - (atr * 1.5)
        take_profit = current_price + (atr * 2.5)
        
        content = f"{market_data.symbol}:{market_data.timestamp}:MR:{position:.4f}"
        signal_id = f"sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"
        
        return TradingSignal(
            signal_id=signal_id,
            symbol=market_data.symbol,
            direction=direction,
            confidence=0.65,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            timestamp=market_data.timestamp,
            technical_score=0.6,
            ml_score=0.0,
            sentiment_score=0.0,
            volume_score=0.3,
            rsi=TechnicalIndicators.rsi(market_data.closes).value,
            macd_histogram=0.0,
            bollinger_position=position,
            atr=atr,
            invalidation_price=stop_loss,
            notes="MeanReversion:LONG"
        )
    
    def should_exit(self, position: Position, market_data: MarketData) -> Optional[str]:
        """Exit when price returns to mean."""
        try:
            return self._should_exit_impl(position, market_data)
        except Exception:
            return "error"
    
    def _should_exit_impl(self, position: Position, market_data: MarketData) -> Optional[str]:
        if position.symbol != market_data.symbol:
            return None
        
        bb = TechnicalIndicators.bollinger_bands(
            market_data.closes,
            self.mr_config.bb_period,
            self.mr_config.bb_std
        )
        
        # LONG exit: price returned to middle
        if position.side == PositionSide.LONG:
            if bb.position >= self.mr_config.exit_middle_threshold:
                return "mean_reversion_target"
        
        # SHORT exit would be here - but we dont open SHORT in Spot
        
        return None
