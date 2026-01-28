# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T22:00:00Z
# Purpose: Backtest engine for strategy evaluation on historical data
# Security: Fail-closed design, Spot-only enforcement, no real trades
# === END SIGNATURE ===
"""
Backtest Engine.

Runs trading strategies on historical OHLCV data to evaluate performance.

Features:
- Uses existing StrategyOrchestrator pipeline
- Spot-only enforcement (no SHORT positions)
- Commission and slippage modeling
- Equity curve and drawdown tracking
- Comprehensive metrics calculation

Usage:
    from core.backtest import BacktestEngine, BacktestConfig
    from core.strategy.orchestrator import StrategyOrchestrator
    from core.strategy.momentum import MomentumStrategy

    orchestrator = StrategyOrchestrator([MomentumStrategy()])
    engine = BacktestEngine(orchestrator, BacktestConfig())

    klines = DataLoader().generate_synthetic(candle_count=500)
    result = engine.run(klines)

    print(f"Total trades: {result.total_trades}")
    print(f"Win rate: {result.win_rate:.1%}")
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

import numpy as np

from core.ai.signal_engine import MarketData, SignalDirection
from core.strategy.base import Position, PositionSide, TradeResult
from core.strategy.orchestrator import (
    StrategyOrchestrator,
    OrchestratorDecision,
    DecisionAction,
    OrchestratorConfig,
)
from core.market.klines_provider import KlinesResult
from .metrics import (
    calculate_drawdown,
    calculate_sharpe_ratio,
    calculate_trade_stats,
    calculate_returns,
    format_metrics_report,
    TradeStats,
    DrawdownInfo,
)
from .data_loader import validate_klines, DataValidation, DataLoader

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    """Configuration for backtest execution."""

    # Capital management
    initial_capital: float = 10000.0  # Starting capital in quote currency (USD)
    position_size_pct: float = 0.02   # Risk per trade as % of equity (2%)
    max_position_pct: float = 0.20    # Max position size as % of equity (20%)

    # Costs
    commission_pct: float = 0.001     # Commission per trade (0.1% for Binance)
    slippage_pct: float = 0.0005      # Slippage per trade (0.05%)

    # Position limits
    max_open_positions: int = 1       # Max concurrent positions (no pyramiding)
    spot_only: bool = True            # Only LONG positions (Spot mode)

    # Data requirements
    min_candles: int = 70             # Minimum candles for regime detection (need 50 ATR values + 15 warmup + 5 buffer)
    timeframe: str = "15m"            # Expected timeframe

    # Risk management
    use_stop_loss: bool = True        # Honor signal stop-loss
    use_take_profit: bool = True      # Honor signal take-profit
    trailing_stop_pct: float = 0.0    # Trailing stop (0 = disabled)

    # Execution
    fill_on_close: bool = True        # Fill at candle close (vs next open)


@dataclass
class BacktestResult:
    """Complete backtest results."""

    # Core results
    trades: List[TradeResult]
    equity_curve: List[float]
    decisions: List[OrchestratorDecision]

    # Configuration used
    config: BacktestConfig
    symbol: str
    timeframe: str
    candle_count: int

    # Time range
    start_timestamp: float
    end_timestamp: float

    # Validation info
    validation: DataValidation

    # Computed metrics (filled after calculation)
    trade_stats: Optional[TradeStats] = None
    drawdown: Optional[DrawdownInfo] = None
    sharpe_ratio: float = 0.0

    # Convenience properties
    @property
    def total_trades(self) -> int:
        return self.trade_stats.total_trades if self.trade_stats else len(self.trades)

    @property
    def win_rate(self) -> float:
        return self.trade_stats.win_rate if self.trade_stats else 0.0

    @property
    def profit_factor(self) -> float:
        return self.trade_stats.profit_factor if self.trade_stats else 0.0

    @property
    def total_pnl(self) -> float:
        return self.trade_stats.net_pnl if self.trade_stats else 0.0

    @property
    def max_drawdown(self) -> float:
        return self.drawdown.max_drawdown_pct if self.drawdown else 0.0

    @property
    def final_equity(self) -> float:
        return self.equity_curve[-1] if self.equity_curve else self.config.initial_capital

    @property
    def total_return_pct(self) -> float:
        if self.config.initial_capital <= 0:
            return 0.0
        return ((self.final_equity / self.config.initial_capital) - 1) * 100

    def format_report(self) -> str:
        """Format results as human-readable report."""
        if not self.trade_stats or not self.drawdown:
            return "No metrics calculated"
        return format_metrics_report(
            trade_stats=self.trade_stats,
            drawdown=self.drawdown,
            sharpe=self.sharpe_ratio,
            initial_capital=self.config.initial_capital,
            final_equity=self.final_equity,
        )


@dataclass
class _OpenPosition:
    """Internal tracking of open position during backtest."""
    position: Position
    entry_bar: int  # Bar index when opened
    entry_cost: float  # Total cost including commission


class BacktestEngine:
    """
    Backtest engine for evaluating trading strategies.

    Process:
    1. Validate input data
    2. Iterate through each bar (from min_candles to end)
    3. Check stop-loss/take-profit hits
    4. Call orchestrator.decide() for new signals
    5. Execute entries/exits with commission/slippage
    6. Track equity curve
    7. Calculate final metrics
    """

    def __init__(
        self,
        orchestrator: StrategyOrchestrator,
        config: Optional[BacktestConfig] = None,
    ):
        """
        Initialize backtest engine.

        Args:
            orchestrator: StrategyOrchestrator with configured strategies
            config: Backtest configuration
        """
        self._orchestrator = orchestrator
        self._config = config or BacktestConfig()

        # Runtime state (reset per run)
        self._equity: float = 0.0
        self._positions: List[_OpenPosition] = []
        self._trades: List[TradeResult] = []
        self._equity_curve: List[float] = []
        self._decisions: List[OrchestratorDecision] = []
        self._current_bar: int = 0

    def run(self, klines: KlinesResult) -> BacktestResult:
        """
        Run backtest on historical data.

        Args:
            klines: KlinesResult with OHLCV data

        Returns:
            BacktestResult with trades, equity curve, and metrics
        """
        # Reset state
        self._reset()

        # Validate data
        validation = validate_klines(
            klines,
            self._config.timeframe,
            self._config.min_candles,
        )

        if not validation.is_valid:
            logger.error("Data validation failed: %s", validation.errors)
            return self._create_empty_result(klines, validation)

        if validation.warnings:
            for warning in validation.warnings:
                logger.warning("Data warning: %s", warning)

        # Initialize equity
        self._equity = self._config.initial_capital
        self._equity_curve.append(self._equity)

        n = klines.candle_count
        start_bar = self._config.min_candles

        logger.info(
            "Starting backtest: %s %s, %d candles (starting at bar %d)",
            klines.symbol, klines.timeframe, n, start_bar
        )

        # Main loop
        for bar_idx in range(start_bar, n):
            self._current_bar = bar_idx
            timestamp = int(klines.candle_times[bar_idx])

            # Current bar OHLC
            bar_open = klines.opens[bar_idx]
            bar_high = klines.highs[bar_idx]
            bar_low = klines.lows[bar_idx]
            bar_close = klines.closes[bar_idx]

            # 1. Check stops on existing positions
            self._check_stops(bar_high, bar_low, bar_close, timestamp)

            # 2. Build MarketData slice (0 to current bar inclusive)
            market_data = self._build_market_data(klines, bar_idx)
            if market_data is None:
                continue

            # 3. Get orchestrator decision
            # Convert _OpenPosition to Position list for orchestrator
            positions = [op.position for op in self._positions]

            decision = self._orchestrator.decide(
                market_data=market_data,
                current_positions=positions,
                timeframe=self._config.timeframe,
            )
            self._decisions.append(decision)

            # 4. Execute decision
            if decision.action == DecisionAction.ENTER:
                self._execute_entry(decision, bar_close, timestamp, bar_idx)

            elif decision.action == DecisionAction.EXIT:
                self._execute_exit_all(bar_close, timestamp, "orchestrator_signal")

            # 5. Update equity curve
            self._update_equity(bar_close)

        # Close remaining positions at final bar
        if self._positions:
            final_price = klines.closes[-1]
            final_timestamp = int(klines.candle_times[-1])
            self._execute_exit_all(final_price, final_timestamp, "backtest_end")

        # Calculate metrics
        result = self._create_result(klines, validation)
        self._calculate_metrics(result)

        logger.info(
            "Backtest complete: %d trades, %.1f%% return, %.1f%% win rate",
            result.total_trades,
            result.total_return_pct,
            result.win_rate * 100,
        )

        return result

    def _reset(self) -> None:
        """Reset internal state for new run."""
        self._equity = 0.0
        self._positions = []
        self._trades = []
        self._equity_curve = []
        self._decisions = []
        self._current_bar = 0

    def _build_market_data(self, klines: KlinesResult, bar_idx: int) -> Optional[MarketData]:
        """Build MarketData slice for orchestrator."""
        try:
            # Slice arrays from start to current bar (inclusive)
            end = bar_idx + 1

            return MarketData(
                symbol=klines.symbol,
                timestamp=int(klines.candle_times[bar_idx]),
                opens=klines.opens[:end],
                highs=klines.highs[:end],
                lows=klines.lows[:end],
                closes=klines.closes[:end],
                volumes=klines.volumes[:end],
            )
        except (ValueError, IndexError) as e:
            logger.warning("Failed to build MarketData at bar %d: %s", bar_idx, e)
            return None

    def _check_stops(
        self,
        bar_high: float,
        bar_low: float,
        bar_close: float,
        timestamp: int,
    ) -> None:
        """Check stop-loss and take-profit for all positions."""
        positions_to_close = []

        for open_pos in self._positions:
            pos = open_pos.position

            # Check stop-loss (price went below SL)
            if self._config.use_stop_loss and pos.stop_loss > 0:
                if bar_low <= pos.stop_loss:
                    # Stop-loss hit
                    exit_price = pos.stop_loss  # Assume fill at SL
                    positions_to_close.append((open_pos, exit_price, "stop_loss"))
                    continue

            # Check take-profit (price reached TP)
            if self._config.use_take_profit and pos.take_profit > 0:
                if bar_high >= pos.take_profit:
                    # Take-profit hit
                    exit_price = pos.take_profit  # Assume fill at TP
                    positions_to_close.append((open_pos, exit_price, "take_profit"))
                    continue

        # Execute closes
        for open_pos, exit_price, reason in positions_to_close:
            self._close_position(open_pos, exit_price, timestamp, reason)

    def _execute_entry(
        self,
        decision: OrchestratorDecision,
        current_price: float,
        timestamp: int,
        bar_idx: int,
    ) -> None:
        """Execute entry based on orchestrator decision."""
        if decision.signal is None:
            return

        signal = decision.signal

        # Spot-only check
        if self._config.spot_only and signal.direction == SignalDirection.SHORT:
            logger.debug("Skipping SHORT signal (spot_only=True)")
            return

        # Max positions check
        if len(self._positions) >= self._config.max_open_positions:
            logger.debug("Max positions reached, skipping entry")
            return

        # Calculate position size
        risk_amount = self._equity * self._config.position_size_pct
        entry_price = current_price * (1 + self._config.slippage_pct)  # Slippage on entry

        # Size based on risk to stop-loss
        if signal.stop_loss > 0 and signal.stop_loss < entry_price:
            risk_per_unit = entry_price - signal.stop_loss
            size = risk_amount / risk_per_unit if risk_per_unit > 0 else 0
        else:
            # Fallback: use max_position_pct
            max_value = self._equity * self._config.max_position_pct
            size = max_value / entry_price if entry_price > 0 else 0

        # Ensure we can afford it
        total_cost = size * entry_price * (1 + self._config.commission_pct)
        if total_cost > self._equity:
            # Scale down
            size = (self._equity * 0.95) / (entry_price * (1 + self._config.commission_pct))

        if size <= 0:
            logger.debug("Cannot afford position, skipping entry")
            return

        # Create position
        position = Position(
            symbol=signal.symbol,
            side=PositionSide.LONG if signal.direction == SignalDirection.LONG else PositionSide.SHORT,
            entry_price=entry_price,
            size=size,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            signal_id=signal.signal_id,
            entry_time=timestamp,
        )

        # Deduct principal (position value) + commission from equity
        position_value = size * entry_price
        entry_commission = position_value * self._config.commission_pct
        self._equity -= (position_value + entry_commission)

        open_pos = _OpenPosition(
            position=position,
            entry_bar=bar_idx,
            entry_cost=entry_commission,
        )

        self._positions.append(open_pos)

        logger.debug(
            "Opened %s position: %.4f @ %.2f (SL=%.2f, TP=%.2f)",
            position.side.value,
            size,
            entry_price,
            position.stop_loss,
            position.take_profit,
        )

    def _execute_exit_all(
        self,
        current_price: float,
        timestamp: int,
        reason: str,
    ) -> None:
        """Exit all open positions."""
        for open_pos in list(self._positions):
            self._close_position(open_pos, current_price, timestamp, reason)

    def _close_position(
        self,
        open_pos: _OpenPosition,
        exit_price: float,
        timestamp: int,
        reason: str,
    ) -> None:
        """Close a single position and record trade."""
        pos = open_pos.position

        # Apply slippage on exit
        actual_exit = exit_price * (1 - self._config.slippage_pct)

        # Calculate P&L
        if pos.side == PositionSide.LONG:
            pnl = (actual_exit - pos.entry_price) * pos.size
        else:
            pnl = (pos.entry_price - actual_exit) * pos.size

        # Deduct exit commission
        exit_commission = pos.size * actual_exit * self._config.commission_pct
        pnl -= exit_commission

        # Update equity
        self._equity += pnl + (pos.size * pos.entry_price)  # Return principal + P&L

        # Calculate bars in trade
        bars_in_trade = self._current_bar - open_pos.entry_bar

        # Record trade
        trade = TradeResult(
            symbol=pos.symbol,
            signal_id=pos.signal_id,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=actual_exit,
            size=pos.size,
            pnl=pnl,
            pnl_percent=(pnl / (pos.entry_price * pos.size)) * 100 if pos.size > 0 else 0,
            entry_time=pos.entry_time,
            exit_time=timestamp,
            exit_reason=reason,
        )

        self._trades.append(trade)
        self._positions.remove(open_pos)

        logger.debug(
            "Closed position: %s %.2f (entry=%.2f, exit=%.2f, PnL=%.2f, reason=%s)",
            pos.symbol,
            pos.size,
            pos.entry_price,
            actual_exit,
            pnl,
            reason,
        )

    def _update_equity(self, current_price: float) -> None:
        """Update equity curve with current mark-to-market."""
        # Current cash + unrealized P&L
        unrealized = 0.0
        for open_pos in self._positions:
            pos = open_pos.position
            if pos.side == PositionSide.LONG:
                unrealized += (current_price - pos.entry_price) * pos.size
            else:
                unrealized += (pos.entry_price - current_price) * pos.size

        total_equity = self._equity + unrealized

        # Add position value (principal held in position)
        for open_pos in self._positions:
            total_equity += open_pos.position.entry_price * open_pos.position.size

        self._equity_curve.append(total_equity)

    def _create_empty_result(
        self,
        klines: KlinesResult,
        validation: DataValidation,
    ) -> BacktestResult:
        """Create empty result for failed validation."""
        return BacktestResult(
            trades=[],
            equity_curve=[self._config.initial_capital],
            decisions=[],
            config=self._config,
            symbol=klines.symbol,
            timeframe=klines.timeframe,
            candle_count=klines.candle_count,
            start_timestamp=klines.candle_times[0] if klines.candle_count > 0 else 0,
            end_timestamp=klines.candle_times[-1] if klines.candle_count > 0 else 0,
            validation=validation,
        )

    def _create_result(
        self,
        klines: KlinesResult,
        validation: DataValidation,
    ) -> BacktestResult:
        """Create result object."""
        return BacktestResult(
            trades=self._trades,
            equity_curve=self._equity_curve,
            decisions=self._decisions,
            config=self._config,
            symbol=klines.symbol,
            timeframe=klines.timeframe,
            candle_count=klines.candle_count,
            start_timestamp=klines.candle_times[0],
            end_timestamp=klines.candle_times[-1],
            validation=validation,
        )

    def _calculate_metrics(self, result: BacktestResult) -> None:
        """Calculate all metrics for result."""
        # Trade statistics
        pnls = [t.pnl for t in result.trades]
        bars_in_trades = [
            t.exit_time - t.entry_time  # Approximate bars
            for t in result.trades
        ]
        result.trade_stats = calculate_trade_stats(pnls, bars_in_trades)

        # Drawdown
        result.drawdown = calculate_drawdown(result.equity_curve)

        # Sharpe ratio
        returns = calculate_returns(result.equity_curve)
        result.sharpe_ratio = calculate_sharpe_ratio(returns)

    def load_data(
        self,
        source: str = "synthetic",
        symbol: str = "BTCUSDT",
        timeframe: str = "15m",
        **kwargs,
    ) -> Optional[KlinesResult]:
        """
        Load data for backtesting. TZ v1.0 compatibility method.

        Args:
            source: "synthetic" | "csv" | "api"
            symbol: Trading pair
            timeframe: Candle interval
            **kwargs: Source-specific arguments

        Returns:
            KlinesResult or None
        """
        loader = DataLoader()
        return loader.load(source, symbol, timeframe, **kwargs)


def run_backtest(
    klines: KlinesResult,
    strategies: Optional[List] = None,
    config: Optional[BacktestConfig] = None,
) -> BacktestResult:
    """
    Convenience function to run backtest.

    Args:
        klines: Historical OHLCV data
        strategies: List of strategy instances (default: MomentumStrategy)
        config: Backtest configuration

    Returns:
        BacktestResult
    """
    from core.strategy.momentum import MomentumStrategy

    if strategies is None:
        strategies = [MomentumStrategy()]

    orchestrator = StrategyOrchestrator(
        strategies=strategies,
        config=OrchestratorConfig(spot_only=True),
    )

    engine = BacktestEngine(orchestrator, config)
    return engine.run(klines)


def load_data(
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    source: str = "synthetic",
    **kwargs,
) -> Optional[KlinesResult]:
    """
    Load data for backtesting. Convenience function.

    Args:
        symbol: Trading pair
        timeframe: Candle interval
        source: "synthetic" | "csv" | "api"
        **kwargs: Additional args passed to loader

    Returns:
        KlinesResult or None
    """
    loader = DataLoader()

    if source == "synthetic":
        return loader.generate_synthetic(
            symbol=symbol,
            timeframe=timeframe,
            candle_count=kwargs.get("candle_count", 500),
            start_price=kwargs.get("start_price", 50000.0),
            volatility=kwargs.get("volatility", 0.02),
            trend=kwargs.get("trend", 0.0001),
            seed=kwargs.get("seed"),
        )
    elif source == "csv":
        path = kwargs.get("path")
        if not path:
            return None
        return loader.load_csv(path, symbol, timeframe)
    elif source == "api":
        return loader.fetch_recent(
            symbol=symbol,
            timeframe=timeframe,
            limit=kwargs.get("limit", 500),
        )
    return None
