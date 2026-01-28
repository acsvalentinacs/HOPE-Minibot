# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T18:35:00Z
# Purpose: Base strategy class for trading strategies
# Security: Abstract base, no direct execution
# === END SIGNATURE ===
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, List
from enum import Enum
import numpy as np
from core.ai.signal_engine import TradingSignal, MarketData, SignalDirection

class PositionSide(Enum):
    LONG = 'LONG'
    SHORT = 'SHORT'
    FLAT = 'FLAT'

@dataclass(frozen=True)
class Position:
    symbol: str
    side: PositionSide
    entry_price: float
    size: float
    stop_loss: float
    take_profit: float
    signal_id: str
    entry_time: int
    @property
    def is_open(self) -> bool:
        return self.side != PositionSide.FLAT

@dataclass(frozen=True)
class TradeResult:
    symbol: str
    signal_id: str
    side: PositionSide
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    pnl_percent: float
    entry_time: int
    exit_time: int
    exit_reason: str

@dataclass
class StrategyConfig:
    max_position_size: float = 0.1
    risk_per_trade: float = 0.02
    max_open_positions: int = 3
    min_confidence: float = 0.60
    use_trailing_stop: bool = False
    trailing_stop_percent: float = 0.02
    spot_only: bool = True  # CRITICAL: No SHORT positions on Spot

class BaseStrategy(ABC):
    def __init__(self, config: Optional[StrategyConfig] = None):
        self.config = config or StrategyConfig()
        self._positions: List[Position] = []
        self._trade_history: List[TradeResult] = []
        self._capital = 10000.0

    @property
    def name(self) -> str:
        """Strategy name for orchestrator registration (e.g., 'momentum' from 'MomentumStrategy')."""
        class_name = self.__class__.__name__.lower()
        return class_name.replace('strategy', '') if class_name.endswith('strategy') else class_name
    
    @property
    def positions(self) -> List[Position]:
        return [p for p in self._positions if p.is_open]
    
    @property
    def trade_history(self) -> List[TradeResult]:
        return self._trade_history.copy()
    
    def set_capital(self, capital: float) -> None:
        if capital <= 0:
            raise ValueError('Capital must be positive')
        self._capital = capital
    
    @abstractmethod
    def generate_signal(self, market_data: MarketData) -> Optional[TradingSignal]:
        pass

    @abstractmethod
    def should_exit(self, position: Position, market_data: MarketData) -> Optional[str]:
        pass

    def should_enter(self, market_data: MarketData) -> bool:
        """
        Check if strategy should enter a new position.

        This is a convenience wrapper around generate_signal().

        Args:
            market_data: Current market data

        Returns:
            True if a valid signal is generated, False otherwise
        """
        signal = self.generate_signal(market_data)
        return signal is not None

    def can_trade(self, market_data: MarketData) -> bool:
        """
        Check if strategy can trade (has capacity for new positions).

        Args:
            market_data: Current market data

        Returns:
            True if can open new positions, False otherwise
        """
        # Check max positions limit
        if len(self.positions) >= self.config.max_open_positions:
            return False
        # Check if already have position for this symbol
        if any(p.symbol == market_data.symbol for p in self.positions):
            return False
        return True
    
    def calculate_position_size(self, entry_price: float, stop_loss: float, signal: TradingSignal) -> float:
        if entry_price <= 0 or stop_loss <= 0:
            return 0.0
        risk_per_unit = abs(entry_price - stop_loss)
        if risk_per_unit == 0:
            return 0.0
        risk_amount = self._capital * self.config.risk_per_trade
        size_by_risk = risk_amount / risk_per_unit
        max_allocation = self._capital * self.config.max_position_size
        size_by_allocation = max_allocation / entry_price
        position_size = min(size_by_risk, size_by_allocation) * signal.confidence
        return max(0.0, position_size)
    
    def open_position(self, signal: TradingSignal, current_time: int) -> Optional[Position]:
        if len(self.positions) >= self.config.max_open_positions:
            return None
        if any(p.symbol == signal.symbol for p in self.positions):
            return None
        if signal.confidence < self.config.min_confidence:
            return None
        # SPOT-ONLY ENFORCEMENT: Block SHORT positions
        if self.config.spot_only and signal.direction == SignalDirection.SHORT:
            return None  # CRITICAL: No SHORT on Spot
        size = self.calculate_position_size(signal.entry_price, signal.stop_loss, signal)
        if size <= 0:
            return None
        side = PositionSide.LONG if signal.direction == SignalDirection.LONG else PositionSide.SHORT if signal.direction == SignalDirection.SHORT else None
        if side is None:
            return None
        position = Position(symbol=signal.symbol, side=side, entry_price=signal.entry_price, size=size, stop_loss=signal.stop_loss, take_profit=signal.take_profit, signal_id=signal.signal_id, entry_time=current_time)
        self._positions.append(position)
        return position
    
    def close_position(self, position: Position, exit_price: float, exit_time: int, exit_reason: str) -> TradeResult:
        if position.side == PositionSide.LONG:
            pnl = (exit_price - position.entry_price) * position.size
        else:
            pnl = (position.entry_price - exit_price) * position.size
        pnl_percent = pnl / (position.entry_price * position.size) * 100
        result = TradeResult(symbol=position.symbol, signal_id=position.signal_id, side=position.side, entry_price=position.entry_price, exit_price=exit_price, size=position.size, pnl=pnl, pnl_percent=pnl_percent, entry_time=position.entry_time, exit_time=exit_time, exit_reason=exit_reason)
        self._positions = [p for p in self._positions if p.signal_id != position.signal_id]
        self._trade_history.append(result)
        return result
    
    def check_stops(self, market_data: MarketData, current_time: int) -> List[TradeResult]:
        results = []
        current_price = float(market_data.closes[-1])
        for position in self.positions:
            if position.symbol != market_data.symbol:
                continue
            exit_reason = None
            if position.side == PositionSide.LONG:
                if current_price <= position.stop_loss:
                    exit_reason = 'stop_loss'
                elif current_price >= position.take_profit:
                    exit_reason = 'take_profit'
            else:
                if current_price >= position.stop_loss:
                    exit_reason = 'stop_loss'
                elif current_price <= position.take_profit:
                    exit_reason = 'take_profit'
            if exit_reason:
                result = self.close_position(position, current_price, current_time, exit_reason)
                results.append(result)
        return results
    
    def get_statistics(self) -> dict:
        if not self._trade_history:
            return {'total_trades': 0, 'win_rate': 0.0, 'avg_pnl': 0.0, 'total_pnl': 0.0, 'profit_factor': 0.0, 'max_drawdown': 0.0}
        wins = [t for t in self._trade_history if t.pnl > 0]
        losses = [t for t in self._trade_history if t.pnl <= 0]
        total_profit = sum(t.pnl for t in wins)
        total_loss = abs(sum(t.pnl for t in losses))
        return {'total_trades': len(self._trade_history), 'win_rate': len(wins) / len(self._trade_history) * 100, 'avg_pnl': sum(t.pnl for t in self._trade_history) / len(self._trade_history), 'total_pnl': sum(t.pnl for t in self._trade_history), 'profit_factor': total_profit / total_loss if total_loss > 0 else float('inf'), 'max_drawdown': self._calculate_max_drawdown()}
    
    def _calculate_max_drawdown(self) -> float:
        if not self._trade_history:
            return 0.0
        cumulative, peak, max_dd = 0.0, 0.0, 0.0
        for trade in self._trade_history:
            cumulative += trade.pnl
            peak = max(peak, cumulative)
            max_dd = max(max_dd, peak - cumulative)
        return max_dd
