# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T23:30:00Z
# Purpose: Hyperparameter auto-tuning for trading strategies
# Security: Fail-closed, no external dependencies beyond numpy
# === END SIGNATURE ===
"""
Auto-Tuner for Strategy Hyperparameters.

Provides automated hyperparameter optimization for trading strategies using:
1. Grid Search - exhaustive search over parameter grid
2. Random Search - random sampling from parameter distributions
3. (Future) Bayesian Optimization via Optuna

Fail-closed: returns empty result on errors.
"""
from __future__ import annotations

import logging
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable, Tuple, Union
from pathlib import Path
import json

import numpy as np

from core.backtest.engine import BacktestEngine, BacktestConfig, BacktestResult
from core.backtest.data_loader import KlinesResult
from core.strategy.orchestrator import StrategyOrchestrator
from core.strategy.base import BaseStrategy

logger = logging.getLogger(__name__)


# ============================================================================
# Parameter Space Definitions
# ============================================================================

@dataclass
class ParameterRange:
    """Defines a range for a single parameter."""

    name: str
    param_type: str  # "int" | "float" | "bool" | "choice"
    low: Optional[float] = None
    high: Optional[float] = None
    step: Optional[float] = None
    choices: Optional[List[Any]] = None
    log_scale: bool = False  # Use log scale for sampling

    def validate(self) -> bool:
        """Validate parameter definition."""
        if self.param_type == "choice":
            return self.choices is not None and len(self.choices) > 0
        elif self.param_type in ("int", "float"):
            return self.low is not None and self.high is not None and self.low <= self.high
        elif self.param_type == "bool":
            return True
        return False

    def sample(self, rng: random.Random) -> Any:
        """Sample a random value from this parameter range."""
        if self.param_type == "bool":
            return rng.choice([True, False])

        if self.param_type == "choice":
            return rng.choice(self.choices)

        if self.log_scale and self.low > 0:
            log_low = np.log(self.low)
            log_high = np.log(self.high)
            value = np.exp(rng.uniform(log_low, log_high))
        else:
            value = rng.uniform(self.low, self.high)

        if self.param_type == "int":
            value = int(round(value))

        if self.step is not None:
            value = round(value / self.step) * self.step
            if self.param_type == "int":
                value = int(value)

        return value

    def grid_values(self, n_points: int = 5) -> List[Any]:
        """Generate grid values for this parameter."""
        if self.param_type == "bool":
            return [True, False]

        if self.param_type == "choice":
            return list(self.choices)

        if self.step is not None:
            # Use step-based grid
            values = []
            v = self.low
            while v <= self.high:
                if self.param_type == "int":
                    values.append(int(v))
                else:
                    values.append(v)
                v += self.step
            return values

        # Use n_points evenly spaced
        if self.log_scale and self.low > 0:
            values = np.exp(np.linspace(np.log(self.low), np.log(self.high), n_points))
        else:
            values = np.linspace(self.low, self.high, n_points)

        if self.param_type == "int":
            values = [int(round(v)) for v in values]
            values = list(dict.fromkeys(values))  # Remove duplicates
        else:
            values = [round(v, 6) for v in values]

        return values


@dataclass
class SearchSpace:
    """Complete search space for strategy optimization."""

    strategy_name: str
    parameters: List[ParameterRange] = field(default_factory=list)

    def add_param(self, param: ParameterRange) -> "SearchSpace":
        """Add parameter to search space (fluent API)."""
        if param.validate():
            self.parameters.append(param)
        else:
            logger.warning("Invalid parameter: %s", param.name)
        return self

    def sample_config(self, rng: random.Random) -> Dict[str, Any]:
        """Sample a random configuration."""
        return {p.name: p.sample(rng) for p in self.parameters}

    def grid_configs(self, n_points_per_param: int = 5) -> List[Dict[str, Any]]:
        """Generate all grid configurations."""
        from itertools import product

        if not self.parameters:
            return [{}]

        param_grids = [p.grid_values(n_points_per_param) for p in self.parameters]
        param_names = [p.name for p in self.parameters]

        configs = []
        for values in product(*param_grids):
            config = dict(zip(param_names, values))
            configs.append(config)

        return configs

    @property
    def n_params(self) -> int:
        """Number of parameters."""
        return len(self.parameters)


# ============================================================================
# Pre-defined Search Spaces for Strategies
# ============================================================================

def momentum_search_space() -> SearchSpace:
    """Search space for MomentumStrategy."""
    space = SearchSpace(strategy_name="momentum")
    space.add_param(ParameterRange(
        name="rsi_oversold",
        param_type="float",
        low=20.0,
        high=40.0,
        step=5.0,
    ))
    space.add_param(ParameterRange(
        name="rsi_overbought",
        param_type="float",
        low=60.0,
        high=80.0,
        step=5.0,
    ))
    space.add_param(ParameterRange(
        name="macd_fast",
        param_type="int",
        low=8,
        high=15,
        step=1,
    ))
    space.add_param(ParameterRange(
        name="macd_slow",
        param_type="int",
        low=20,
        high=30,
        step=2,
    ))
    space.add_param(ParameterRange(
        name="min_volume_ratio",
        param_type="float",
        low=1.0,
        high=2.0,
        step=0.2,
    ))
    space.add_param(ParameterRange(
        name="require_volume_confirmation",
        param_type="bool",
    ))
    return space


def breakout_search_space() -> SearchSpace:
    """Search space for BreakoutStrategy."""
    space = SearchSpace(strategy_name="breakout")
    space.add_param(ParameterRange(
        name="lookback_period",
        param_type="int",
        low=10,
        high=30,
        step=5,
    ))
    space.add_param(ParameterRange(
        name="breakout_threshold",
        param_type="float",
        low=0.001,
        high=0.005,
        step=0.001,
    ))
    space.add_param(ParameterRange(
        name="min_volume_ratio",
        param_type="float",
        low=1.2,
        high=2.5,
        step=0.3,
    ))
    space.add_param(ParameterRange(
        name="min_atr_pct",
        param_type="float",
        low=0.005,
        high=0.02,
        step=0.005,
    ))
    space.add_param(ParameterRange(
        name="trailing_atr_mult",
        param_type="float",
        low=1.5,
        high=3.0,
        step=0.5,
    ))
    return space


def mean_reversion_search_space() -> SearchSpace:
    """Search space for MeanReversionStrategy."""
    space = SearchSpace(strategy_name="mean_reversion")
    space.add_param(ParameterRange(
        name="bb_period",
        param_type="int",
        low=15,
        high=30,
        step=5,
    ))
    space.add_param(ParameterRange(
        name="bb_std",
        param_type="float",
        low=1.5,
        high=2.5,
        step=0.25,
    ))
    space.add_param(ParameterRange(
        name="entry_lower_threshold",
        param_type="float",
        low=0.05,
        high=0.20,
        step=0.05,
    ))
    space.add_param(ParameterRange(
        name="exit_middle_threshold",
        param_type="float",
        low=0.40,
        high=0.55,
        step=0.05,
    ))
    space.add_param(ParameterRange(
        name="rsi_oversold",
        param_type="float",
        low=25.0,
        high=40.0,
        step=5.0,
    ))
    return space


# ============================================================================
# Tuning Results
# ============================================================================

@dataclass
class TuneResult:
    """Result of a single tuning trial."""

    config: Dict[str, Any]
    sharpe: float
    total_return: float
    max_drawdown: float
    win_rate: float
    total_trades: int
    profit_factor: float

    @property
    def score(self) -> float:
        """
        Composite score for ranking.

        Weights:
        - Sharpe: 40% (risk-adjusted returns)
        - Return: 20% (absolute performance)
        - Drawdown: 20% (risk control)
        - Win rate: 10% (consistency)
        - Profit factor: 10% (edge quality)
        """
        # Normalize metrics
        sharpe_norm = max(-2, min(3, self.sharpe)) / 3  # [-2, 3] -> [-0.67, 1]
        return_norm = max(-0.5, min(1.0, self.total_return))  # Clip extreme returns
        dd_score = 1.0 - min(1.0, self.max_drawdown * 2)  # Lower DD = higher score
        wr_norm = self.win_rate  # Already [0, 1]
        pf_norm = min(3.0, self.profit_factor) / 3  # Cap at 3

        score = (
            0.40 * sharpe_norm +
            0.20 * return_norm +
            0.20 * dd_score +
            0.10 * wr_norm +
            0.10 * pf_norm
        )

        # Penalize if too few trades (unreliable)
        if self.total_trades < 5:
            score *= 0.5
        elif self.total_trades < 10:
            score *= 0.8

        return score

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "config": self.config,
            "sharpe": self.sharpe,
            "total_return": self.total_return,
            "max_drawdown": self.max_drawdown,
            "win_rate": self.win_rate,
            "total_trades": self.total_trades,
            "profit_factor": self.profit_factor,
            "score": self.score,
        }


@dataclass
class TuneReport:
    """Complete tuning report."""

    strategy_name: str
    search_method: str  # "grid" | "random"
    n_trials: int
    best_result: Optional[TuneResult] = None
    all_results: List[TuneResult] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def add_result(self, result: TuneResult) -> None:
        """Add a trial result."""
        self.all_results.append(result)
        if self.best_result is None or result.score > self.best_result.score:
            self.best_result = result

    def top_n(self, n: int = 5) -> List[TuneResult]:
        """Get top N results by score."""
        sorted_results = sorted(self.all_results, key=lambda r: r.score, reverse=True)
        return sorted_results[:n]

    def format_report(self) -> str:
        """Format human-readable report."""
        lines = [
            "=" * 60,
            f"AUTO-TUNE REPORT: {self.strategy_name}",
            "=" * 60,
            f"Search method: {self.search_method}",
            f"Trials: {self.n_trials}",
            f"Elapsed: {self.elapsed_seconds:.1f}s",
            "",
        ]

        if self.best_result:
            lines.extend([
                "BEST CONFIGURATION:",
                "-" * 40,
            ])
            for k, v in self.best_result.config.items():
                lines.append(f"  {k}: {v}")
            lines.extend([
                "",
                f"  Score:       {self.best_result.score:.4f}",
                f"  Sharpe:      {self.best_result.sharpe:.3f}",
                f"  Return:      {self.best_result.total_return:.2%}",
                f"  Max DD:      {self.best_result.max_drawdown:.2%}",
                f"  Win Rate:    {self.best_result.win_rate:.2%}",
                f"  Trades:      {self.best_result.total_trades}",
                f"  Profit F:    {self.best_result.profit_factor:.2f}",
                "",
            ])

        lines.extend([
            "TOP 5 CONFIGURATIONS:",
            "-" * 40,
        ])
        for i, result in enumerate(self.top_n(5), 1):
            lines.append(
                f"{i}. Score={result.score:.4f} Sharpe={result.sharpe:.2f} "
                f"Ret={result.total_return:.1%} DD={result.max_drawdown:.1%}"
            )

        lines.append("=" * 60)
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "strategy_name": self.strategy_name,
            "search_method": self.search_method,
            "n_trials": self.n_trials,
            "elapsed_seconds": self.elapsed_seconds,
            "best_result": self.best_result.to_dict() if self.best_result else None,
            "top_5": [r.to_dict() for r in self.top_n(5)],
            "all_results_count": len(self.all_results),
        }


# ============================================================================
# Auto-Tuner Engine
# ============================================================================

@dataclass
class AutoTuneConfig:
    """Configuration for auto-tuning."""

    # Search settings
    search_method: str = "random"  # "grid" | "random"
    n_trials: int = 50  # For random search
    n_grid_points: int = 5  # Points per param for grid search

    # Backtest settings
    initial_capital: float = 10000.0
    commission_pct: float = 0.001

    # Optimization target
    primary_metric: str = "score"  # "score" | "sharpe" | "return" | "drawdown"

    # Data settings
    train_ratio: float = 0.7  # Use 70% for training, 30% for validation

    # Random seed
    seed: Optional[int] = None

    # Output
    save_results: bool = True
    results_path: Optional[Path] = None


StrategyFactory = Callable[[Dict[str, Any]], BaseStrategy]


class AutoTuner:
    """
    Hyperparameter Auto-Tuner.

    Searches for optimal strategy parameters using backtesting.
    Supports grid search and random search.
    """

    def __init__(
        self,
        search_space: SearchSpace,
        strategy_factory: StrategyFactory,
        config: Optional[AutoTuneConfig] = None,
    ):
        """
        Initialize auto-tuner.

        Args:
            search_space: Parameter search space
            strategy_factory: Function that creates strategy from config dict
            config: Tuning configuration
        """
        self.search_space = search_space
        self.strategy_factory = strategy_factory
        self.config = config or AutoTuneConfig()
        self._rng = random.Random(self.config.seed)

    def tune(self, klines: KlinesResult) -> TuneReport:
        """
        Run hyperparameter tuning.

        Args:
            klines: Historical data for backtesting

        Returns:
            TuneReport with results
        """
        import time
        start_time = time.time()

        report = TuneReport(
            strategy_name=self.search_space.strategy_name,
            search_method=self.config.search_method,
            n_trials=0,
        )

        try:
            if self.config.search_method == "grid":
                configs = self.search_space.grid_configs(self.config.n_grid_points)
                logger.info("Grid search: %d configurations", len(configs))
            else:
                configs = [
                    self.search_space.sample_config(self._rng)
                    for _ in range(self.config.n_trials)
                ]
                logger.info("Random search: %d trials", len(configs))

            for i, param_config in enumerate(configs):
                try:
                    result = self._evaluate_config(param_config, klines)
                    if result is not None:
                        report.add_result(result)

                        if (i + 1) % 10 == 0:
                            logger.info(
                                "Trial %d/%d: score=%.4f",
                                i + 1, len(configs),
                                result.score
                            )
                except Exception as e:
                    logger.warning("Trial %d failed: %s", i, e)
                    continue

            report.n_trials = len(configs)
            report.elapsed_seconds = time.time() - start_time

            # Save results if configured
            if self.config.save_results and self.config.results_path:
                self._save_report(report)

            logger.info(
                "Tuning complete: %d trials, best score=%.4f",
                report.n_trials,
                report.best_result.score if report.best_result else 0.0
            )

        except Exception as e:
            logger.error("Tuning failed: %s", e)
            report.elapsed_seconds = time.time() - start_time

        return report

    def _evaluate_config(
        self,
        param_config: Dict[str, Any],
        klines: KlinesResult,
    ) -> Optional[TuneResult]:
        """Evaluate a single configuration."""
        try:
            # Create strategy with this config
            strategy = self.strategy_factory(param_config)

            # Setup backtest
            orchestrator = StrategyOrchestrator([strategy])
            bt_config = BacktestConfig(
                initial_capital=self.config.initial_capital,
                commission_pct=self.config.commission_pct,
                spot_only=True,
            )
            engine = BacktestEngine(orchestrator, bt_config)

            # Run backtest
            result = engine.run(klines)

            if not result.validation.is_valid:
                return None

            # Extract metrics
            return TuneResult(
                config=param_config,
                sharpe=result.sharpe_ratio,
                total_return=result.total_return_pct / 100,  # Convert to decimal
                max_drawdown=result.max_drawdown_pct,
                win_rate=result.win_rate,
                total_trades=result.total_trades,
                profit_factor=result.profit_factor,
            )

        except Exception as e:
            logger.debug("Config evaluation failed: %s", e)
            return None

    def _save_report(self, report: TuneReport) -> None:
        """Save report to file."""
        try:
            path = self.config.results_path
            path.parent.mkdir(parents=True, exist_ok=True)

            with open(path, "w", encoding="utf-8") as f:
                json.dump(report.to_dict(), f, indent=2)

            logger.info("Saved tuning results to %s", path)
        except Exception as e:
            logger.warning("Failed to save results: %s", e)


# ============================================================================
# Convenience Functions
# ============================================================================

def tune_momentum(
    klines: KlinesResult,
    n_trials: int = 50,
    seed: Optional[int] = None,
) -> TuneReport:
    """
    Tune MomentumStrategy hyperparameters.

    Args:
        klines: Historical data
        n_trials: Number of random trials
        seed: Random seed for reproducibility

    Returns:
        TuneReport with best configuration
    """
    from core.strategy.momentum import MomentumStrategy, MomentumConfig

    def factory(config: Dict[str, Any]) -> BaseStrategy:
        return MomentumStrategy(MomentumConfig(**config))

    space = momentum_search_space()
    tuner_config = AutoTuneConfig(
        search_method="random",
        n_trials=n_trials,
        seed=seed,
    )

    tuner = AutoTuner(space, factory, tuner_config)
    return tuner.tune(klines)


def tune_breakout(
    klines: KlinesResult,
    n_trials: int = 50,
    seed: Optional[int] = None,
) -> TuneReport:
    """Tune BreakoutStrategy hyperparameters."""
    from core.strategy.breakout import BreakoutStrategy, BreakoutConfig

    def factory(config: Dict[str, Any]) -> BaseStrategy:
        return BreakoutStrategy(BreakoutConfig(**config))

    space = breakout_search_space()
    tuner_config = AutoTuneConfig(
        search_method="random",
        n_trials=n_trials,
        seed=seed,
    )

    tuner = AutoTuner(space, factory, tuner_config)
    return tuner.tune(klines)


def tune_mean_reversion(
    klines: KlinesResult,
    n_trials: int = 50,
    seed: Optional[int] = None,
) -> TuneReport:
    """Tune MeanReversionStrategy hyperparameters."""
    from core.strategy.mean_reversion import MeanReversionStrategy, MeanReversionConfig

    def factory(config: Dict[str, Any]) -> BaseStrategy:
        return MeanReversionStrategy(MeanReversionConfig(**config))

    space = mean_reversion_search_space()
    tuner_config = AutoTuneConfig(
        search_method="random",
        n_trials=n_trials,
        seed=seed,
    )

    tuner = AutoTuner(space, factory, tuner_config)
    return tuner.tune(klines)


# ============================================================================
# Singleton
# ============================================================================

_tuner_instance: Optional[AutoTuner] = None


def get_auto_tuner(
    search_space: Optional[SearchSpace] = None,
    strategy_factory: Optional[StrategyFactory] = None,
    config: Optional[AutoTuneConfig] = None,
) -> AutoTuner:
    """Get or create AutoTuner instance."""
    global _tuner_instance

    if _tuner_instance is None:
        if search_space is None or strategy_factory is None:
            raise ValueError("Must provide search_space and strategy_factory on first call")
        _tuner_instance = AutoTuner(search_space, strategy_factory, config)

    return _tuner_instance
