# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T22:00:00Z
# Purpose: Backtest package exports
# === END SIGNATURE ===
"""
HOPE Backtest Engine.

Provides tools for backtesting trading strategies on historical data.

Modules:
- engine: Main backtest engine (BacktestEngine, BacktestConfig, BacktestResult)
- data_loader: Historical data loading (CSV, API, synthetic)
- metrics: Performance metrics (drawdown, Sharpe, win rate)

Quick Start:
    from core.backtest import BacktestEngine, BacktestConfig, DataLoader
    from core.strategy.orchestrator import StrategyOrchestrator
    from core.strategy.momentum import MomentumStrategy

    # Setup
    orchestrator = StrategyOrchestrator([MomentumStrategy()])
    engine = BacktestEngine(orchestrator, BacktestConfig())

    # Load data
    loader = DataLoader()
    klines = loader.generate_synthetic(candle_count=500, seed=42)

    # Run backtest
    result = engine.run(klines)
    print(result.format_report())
"""

from .engine import (
    BacktestEngine,
    BacktestConfig,
    BacktestResult,
    run_backtest,
)
from .data_loader import (
    DataLoader,
    load_csv,
    generate_synthetic_klines,
    validate_klines,
    DataValidation,
    get_data_loader,
)
from .metrics import (
    calculate_drawdown,
    calculate_sharpe_ratio,
    calculate_profit_factor,
    calculate_win_rate,
    calculate_trade_stats,
    calculate_returns,
    format_metrics_report,
    TradeStats,
    DrawdownInfo,
)

__all__ = [
    # Engine
    "BacktestEngine",
    "BacktestConfig",
    "BacktestResult",
    "run_backtest",
    # Data
    "DataLoader",
    "load_csv",
    "generate_synthetic_klines",
    "validate_klines",
    "DataValidation",
    "get_data_loader",
    # Metrics
    "calculate_drawdown",
    "calculate_sharpe_ratio",
    "calculate_profit_factor",
    "calculate_win_rate",
    "calculate_trade_stats",
    "calculate_returns",
    "format_metrics_report",
    "TradeStats",
    "DrawdownInfo",
]
