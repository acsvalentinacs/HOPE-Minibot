# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T23:30:00Z
# Purpose: Analytics package exports
# === END SIGNATURE ===
"""
HOPE Analytics Module.

Provides tools for strategy analysis and optimization:
- auto_tuner: Hyperparameter optimization for trading strategies

Quick Start:
    from core.analytics import tune_momentum
    from core.backtest import DataLoader

    klines = DataLoader().generate_synthetic(candle_count=500, seed=42)
    report = tune_momentum(klines, n_trials=30, seed=42)
    print(report.format_report())
"""

from .auto_tuner import (
    # Classes
    AutoTuner,
    AutoTuneConfig,
    SearchSpace,
    ParameterRange,
    TuneResult,
    TuneReport,
    # Search spaces
    momentum_search_space,
    breakout_search_space,
    mean_reversion_search_space,
    # Convenience functions
    tune_momentum,
    tune_breakout,
    tune_mean_reversion,
    get_auto_tuner,
)

__all__ = [
    # Classes
    "AutoTuner",
    "AutoTuneConfig",
    "SearchSpace",
    "ParameterRange",
    "TuneResult",
    "TuneReport",
    # Search spaces
    "momentum_search_space",
    "breakout_search_space",
    "mean_reversion_search_space",
    # Convenience functions
    "tune_momentum",
    "tune_breakout",
    "tune_mean_reversion",
    "get_auto_tuner",
]
