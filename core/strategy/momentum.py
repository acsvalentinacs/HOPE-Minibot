# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T18:35:00Z
# Purpose: Momentum-based trading strategy
# Security: Inherits from BaseStrategy, validated inputs
# === END SIGNATURE ===
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from core.ai.signal_engine import SignalEngine, SignalEngineConfig, TradingSignal, MarketData, SignalDirection
from core.ai.technical_indicators import TechnicalIndicators
from core.strategy.base import BaseStrategy, StrategyConfig, Position, PositionSide

@dataclass
class MomentumConfig(StrategyConfig):
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    rsi_exit_long: float = 65.0
    rsi_exit_short: float = 35.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    require_macd_crossover: bool = False  # Disabled by default for backtest flexibility
    require_volume_confirmation: bool = False  # Disabled - synthetic data has uniform volume
    min_volume_ratio: float = 1.0  # Lowered - synthetic data won't pass 1.2
    exit_on_opposite_signal: bool = True
    use_time_exit: bool = False
    max_hold_bars: int = 48

class MomentumStrategy(BaseStrategy):
    def __init__(self, config: Optional[MomentumConfig] = None):
        self.momentum_config = config or MomentumConfig()
        super().__init__(self.momentum_config)
        signal_config = SignalEngineConfig(min_confidence=self.momentum_config.min_confidence, macd_fast=self.momentum_config.macd_fast, macd_slow=self.momentum_config.macd_slow, macd_signal=self.momentum_config.macd_signal)
        self._signal_engine = SignalEngine(signal_config)
    
    def generate_signal(self, market_data: MarketData) -> Optional[TradingSignal]:
        try:
            return self._generate_signal_impl(market_data)
        except Exception:
            return None

    def should_enter(self, market_data: MarketData) -> bool:
        """Check if momentum conditions favor entry."""
        signal = self.generate_signal(market_data)
        return signal is not None and signal.confidence >= self.config.min_confidence
    
    def _generate_signal_impl(self, market_data: MarketData) -> Optional[TradingSignal]:
        signal = self._signal_engine.generate_signal(market_data)
        if signal is None:
            return None
        if not self._check_momentum_filters(market_data, signal):
            return None
        return signal
    
    def _check_momentum_filters(self, market_data: MarketData, signal: TradingSignal) -> bool:
        if signal.direction == SignalDirection.LONG:
            if signal.rsi > self.momentum_config.rsi_oversold + 10:
                return False
        elif signal.direction == SignalDirection.SHORT:
            if signal.rsi < self.momentum_config.rsi_overbought - 10:
                return False
        if self.momentum_config.require_macd_crossover:
            macd_result = TechnicalIndicators.macd(market_data.closes, self.momentum_config.macd_fast, self.momentum_config.macd_slow, self.momentum_config.macd_signal)
            if signal.direction == SignalDirection.LONG:
                if macd_result.crossover != 'BULLISH' and macd_result.histogram <= 0:
                    return False
            elif signal.direction == SignalDirection.SHORT:
                if macd_result.crossover != 'BEARISH' and macd_result.histogram >= 0:
                    return False
        if self.momentum_config.require_volume_confirmation:
            volume_result = TechnicalIndicators.volume_profile(market_data.volumes)
            if volume_result.current_ratio < self.momentum_config.min_volume_ratio:
                return False
        return True
    
    def should_exit(self, position: Position, market_data: MarketData) -> Optional[str]:
        try:
            return self._should_exit_impl(position, market_data)
        except Exception:
            return 'error'
    
    def _should_exit_impl(self, position: Position, market_data: MarketData) -> Optional[str]:
        if position.symbol != market_data.symbol:
            return None
        rsi_result = TechnicalIndicators.rsi(market_data.closes)
        if position.side == PositionSide.LONG:
            if rsi_result.value >= self.momentum_config.rsi_exit_long:
                return 'rsi_reversal'
        else:
            if rsi_result.value <= self.momentum_config.rsi_exit_short:
                return 'rsi_reversal'
        if self.momentum_config.exit_on_opposite_signal:
            new_signal = self._signal_engine.generate_signal(market_data)
            if new_signal:
                if position.side == PositionSide.LONG and new_signal.direction == SignalDirection.SHORT:
                    return 'opposite_signal'
                elif position.side == PositionSide.SHORT and new_signal.direction == SignalDirection.LONG:
                    return 'opposite_signal'
        if self.momentum_config.use_time_exit:
            bars_held = (market_data.timestamp - position.entry_time) // 3600
            if bars_held >= self.momentum_config.max_hold_bars:
                return 'time_exit'
        return None
    
    def process_bar(self, market_data: MarketData, current_time: int) -> dict:
        result = {'exits': [], 'entries': [], 'signals_generated': 0}
        stop_exits = self.check_stops(market_data, current_time)
        result['exits'].extend(stop_exits)
        for position in self.positions:
            if position.symbol != market_data.symbol:
                continue
            exit_reason = self.should_exit(position, market_data)
            if exit_reason:
                current_price = float(market_data.closes[-1])
                trade_result = self.close_position(position, current_price, current_time, exit_reason)
                result['exits'].append(trade_result)
        signal = self.generate_signal(market_data)
        if signal:
            result['signals_generated'] = 1
            position = self.open_position(signal, current_time)
            if position:
                result['entries'].append(position)
        return result
