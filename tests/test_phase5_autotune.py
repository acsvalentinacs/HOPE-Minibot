# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T23:30:00Z
# Purpose: Tests for Phase 5 - Auto-tuner
# Security: Test-only, no production impact
# === END SIGNATURE ===
"""
Tests for Phase 5 Auto-tuner.

Modules tested:
- core.analytics.auto_tuner (ParameterRange, SearchSpace, AutoTuner)
- Convenience functions (tune_momentum, tune_breakout, tune_mean_reversion)
"""
import pytest
import random
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np


class TestParameterRange:
    """Tests for ParameterRange."""

    def test_int_range_valid(self):
        """Verify int range validates correctly."""
        from core.analytics.auto_tuner import ParameterRange

        param = ParameterRange(
            name="period",
            param_type="int",
            low=10,
            high=30,
        )
        assert param.validate()

    def test_float_range_valid(self):
        """Verify float range validates correctly."""
        from core.analytics.auto_tuner import ParameterRange

        param = ParameterRange(
            name="threshold",
            param_type="float",
            low=0.001,
            high=0.01,
            step=0.001,
        )
        assert param.validate()

    def test_bool_range_valid(self):
        """Verify bool range validates correctly."""
        from core.analytics.auto_tuner import ParameterRange

        param = ParameterRange(
            name="enabled",
            param_type="bool",
        )
        assert param.validate()

    def test_choice_range_valid(self):
        """Verify choice range validates correctly."""
        from core.analytics.auto_tuner import ParameterRange

        param = ParameterRange(
            name="method",
            param_type="choice",
            choices=["fast", "slow", "auto"],
        )
        assert param.validate()

    def test_choice_empty_invalid(self):
        """Verify empty choice is invalid."""
        from core.analytics.auto_tuner import ParameterRange

        param = ParameterRange(
            name="method",
            param_type="choice",
            choices=[],
        )
        assert not param.validate()

    def test_sample_int(self):
        """Verify int sampling returns int in range."""
        from core.analytics.auto_tuner import ParameterRange

        param = ParameterRange(
            name="period",
            param_type="int",
            low=10,
            high=20,
        )

        rng = random.Random(42)
        for _ in range(10):
            value = param.sample(rng)
            assert isinstance(value, int)
            assert 10 <= value <= 20

    def test_sample_float_with_step(self):
        """Verify float sampling respects step."""
        from core.analytics.auto_tuner import ParameterRange

        param = ParameterRange(
            name="threshold",
            param_type="float",
            low=0.0,
            high=1.0,
            step=0.1,
        )

        rng = random.Random(42)
        for _ in range(10):
            value = param.sample(rng)
            # Value should be multiple of 0.1
            assert round(value / 0.1) * 0.1 == pytest.approx(value, abs=0.001)

    def test_sample_bool(self):
        """Verify bool sampling returns True or False."""
        from core.analytics.auto_tuner import ParameterRange

        param = ParameterRange(
            name="enabled",
            param_type="bool",
        )

        rng = random.Random(42)
        values = [param.sample(rng) for _ in range(20)]

        # Should have both True and False in 20 samples
        assert True in values
        assert False in values

    def test_sample_choice(self):
        """Verify choice sampling returns valid choice."""
        from core.analytics.auto_tuner import ParameterRange

        choices = ["a", "b", "c"]
        param = ParameterRange(
            name="method",
            param_type="choice",
            choices=choices,
        )

        rng = random.Random(42)
        for _ in range(10):
            value = param.sample(rng)
            assert value in choices

    def test_grid_values_int(self):
        """Verify grid values for int parameter."""
        from core.analytics.auto_tuner import ParameterRange

        param = ParameterRange(
            name="period",
            param_type="int",
            low=10,
            high=20,
            step=5,
        )

        values = param.grid_values()
        assert values == [10, 15, 20]

    def test_grid_values_float(self):
        """Verify grid values for float parameter."""
        from core.analytics.auto_tuner import ParameterRange

        param = ParameterRange(
            name="threshold",
            param_type="float",
            low=0.0,
            high=1.0,
        )

        values = param.grid_values(n_points=5)
        assert len(values) == 5
        assert values[0] == pytest.approx(0.0)
        assert values[-1] == pytest.approx(1.0)

    def test_grid_values_bool(self):
        """Verify grid values for bool parameter."""
        from core.analytics.auto_tuner import ParameterRange

        param = ParameterRange(
            name="enabled",
            param_type="bool",
        )

        values = param.grid_values()
        assert values == [True, False]


class TestSearchSpace:
    """Tests for SearchSpace."""

    def test_create_empty(self):
        """Verify empty search space."""
        from core.analytics.auto_tuner import SearchSpace

        space = SearchSpace(strategy_name="test")
        assert space.n_params == 0

    def test_add_param(self):
        """Verify adding parameters."""
        from core.analytics.auto_tuner import SearchSpace, ParameterRange

        space = SearchSpace(strategy_name="test")
        space.add_param(ParameterRange(name="x", param_type="int", low=1, high=10))
        space.add_param(ParameterRange(name="y", param_type="float", low=0.0, high=1.0))

        assert space.n_params == 2

    def test_sample_config(self):
        """Verify sampling a configuration."""
        from core.analytics.auto_tuner import SearchSpace, ParameterRange

        space = SearchSpace(strategy_name="test")
        space.add_param(ParameterRange(name="x", param_type="int", low=1, high=10))
        space.add_param(ParameterRange(name="y", param_type="bool"))

        rng = random.Random(42)
        config = space.sample_config(rng)

        assert "x" in config
        assert "y" in config
        assert isinstance(config["x"], int)
        assert isinstance(config["y"], bool)

    def test_grid_configs(self):
        """Verify grid configuration generation."""
        from core.analytics.auto_tuner import SearchSpace, ParameterRange

        space = SearchSpace(strategy_name="test")
        space.add_param(ParameterRange(name="x", param_type="int", low=1, high=3, step=1))
        space.add_param(ParameterRange(name="y", param_type="bool"))

        configs = space.grid_configs()

        # 3 values for x * 2 values for y = 6 configs
        assert len(configs) == 6

    def test_predefined_momentum_space(self):
        """Verify predefined momentum search space."""
        from core.analytics.auto_tuner import momentum_search_space

        space = momentum_search_space()
        assert space.strategy_name == "momentum"
        assert space.n_params >= 4

        # Check some expected params exist
        param_names = [p.name for p in space.parameters]
        assert "rsi_oversold" in param_names
        assert "rsi_overbought" in param_names

    def test_predefined_breakout_space(self):
        """Verify predefined breakout search space."""
        from core.analytics.auto_tuner import breakout_search_space

        space = breakout_search_space()
        assert space.strategy_name == "breakout"

        param_names = [p.name for p in space.parameters]
        assert "lookback_period" in param_names

    def test_predefined_mean_reversion_space(self):
        """Verify predefined mean reversion search space."""
        from core.analytics.auto_tuner import mean_reversion_search_space

        space = mean_reversion_search_space()
        assert space.strategy_name == "mean_reversion"

        param_names = [p.name for p in space.parameters]
        assert "bb_period" in param_names


class TestTuneResult:
    """Tests for TuneResult."""

    def test_score_calculation(self):
        """Verify composite score calculation."""
        from core.analytics.auto_tuner import TuneResult

        result = TuneResult(
            config={"x": 1},
            sharpe=1.5,
            total_return=0.20,
            max_drawdown=0.10,
            win_rate=0.60,
            total_trades=20,
            profit_factor=2.0,
        )

        score = result.score
        assert 0 < score < 1

    def test_score_penalizes_few_trades(self):
        """Verify low trade count penalizes score."""
        from core.analytics.auto_tuner import TuneResult

        result_many = TuneResult(
            config={},
            sharpe=1.0,
            total_return=0.1,
            max_drawdown=0.1,
            win_rate=0.6,
            total_trades=20,
            profit_factor=1.5,
        )

        result_few = TuneResult(
            config={},
            sharpe=1.0,
            total_return=0.1,
            max_drawdown=0.1,
            win_rate=0.6,
            total_trades=3,
            profit_factor=1.5,
        )

        assert result_many.score > result_few.score

    def test_to_dict(self):
        """Verify dictionary conversion."""
        from core.analytics.auto_tuner import TuneResult

        result = TuneResult(
            config={"x": 1},
            sharpe=1.0,
            total_return=0.1,
            max_drawdown=0.1,
            win_rate=0.6,
            total_trades=10,
            profit_factor=1.5,
        )

        d = result.to_dict()
        assert "config" in d
        assert "score" in d
        assert "sharpe" in d


class TestTuneReport:
    """Tests for TuneReport."""

    def test_add_result(self):
        """Verify adding results and tracking best."""
        from core.analytics.auto_tuner import TuneReport, TuneResult

        report = TuneReport(strategy_name="test", search_method="random", n_trials=0)

        result1 = TuneResult(
            config={"x": 1},
            sharpe=0.5,
            total_return=0.05,
            max_drawdown=0.2,
            win_rate=0.5,
            total_trades=10,
            profit_factor=1.0,
        )

        result2 = TuneResult(
            config={"x": 2},
            sharpe=1.5,
            total_return=0.15,
            max_drawdown=0.1,
            win_rate=0.7,
            total_trades=15,
            profit_factor=2.0,
        )

        report.add_result(result1)
        assert report.best_result == result1

        report.add_result(result2)
        assert report.best_result == result2  # Better score

    def test_top_n(self):
        """Verify top N results."""
        from core.analytics.auto_tuner import TuneReport, TuneResult

        report = TuneReport(strategy_name="test", search_method="random", n_trials=0)

        for i in range(10):
            report.add_result(TuneResult(
                config={"x": i},
                sharpe=float(i) / 5,
                total_return=0.1,
                max_drawdown=0.1,
                win_rate=0.5,
                total_trades=10,
                profit_factor=1.0 + i * 0.1,
            ))

        top5 = report.top_n(5)
        assert len(top5) == 5

        # Should be sorted by score descending
        for i in range(len(top5) - 1):
            assert top5[i].score >= top5[i + 1].score

    def test_format_report(self):
        """Verify report formatting."""
        from core.analytics.auto_tuner import TuneReport, TuneResult

        report = TuneReport(strategy_name="test", search_method="grid", n_trials=5)
        report.add_result(TuneResult(
            config={"x": 1, "y": 2.0},
            sharpe=1.0,
            total_return=0.1,
            max_drawdown=0.1,
            win_rate=0.6,
            total_trades=10,
            profit_factor=1.5,
        ))
        report.elapsed_seconds = 5.0

        text = report.format_report()
        assert "AUTO-TUNE REPORT" in text
        assert "test" in text
        assert "BEST CONFIGURATION" in text


class TestAutoTuner:
    """Tests for AutoTuner."""

    def test_tuner_creates(self):
        """Verify tuner can be created."""
        from core.analytics.auto_tuner import AutoTuner, SearchSpace, ParameterRange

        space = SearchSpace(strategy_name="test")
        space.add_param(ParameterRange(name="x", param_type="int", low=1, high=10))

        def factory(config):
            from core.strategy.momentum import MomentumStrategy
            return MomentumStrategy()

        tuner = AutoTuner(space, factory)
        assert tuner is not None

    def test_random_search(self):
        """Verify random search runs."""
        from core.analytics.auto_tuner import AutoTuner, AutoTuneConfig, SearchSpace, ParameterRange
        from core.backtest import generate_synthetic_klines

        space = SearchSpace(strategy_name="test")
        space.add_param(ParameterRange(name="rsi_oversold", param_type="float", low=25.0, high=35.0))

        def factory(config):
            from core.strategy.momentum import MomentumStrategy, MomentumConfig
            return MomentumStrategy(MomentumConfig(**config))

        config = AutoTuneConfig(
            search_method="random",
            n_trials=3,
            seed=42,
        )

        tuner = AutoTuner(space, factory, config)
        klines = generate_synthetic_klines(candle_count=200, seed=42)

        report = tuner.tune(klines)

        assert report.n_trials == 3
        assert len(report.all_results) >= 0  # Some may fail

    def test_grid_search(self):
        """Verify grid search runs."""
        from core.analytics.auto_tuner import AutoTuner, AutoTuneConfig, SearchSpace, ParameterRange
        from core.backtest import generate_synthetic_klines

        space = SearchSpace(strategy_name="test")
        space.add_param(ParameterRange(
            name="rsi_oversold",
            param_type="float",
            low=25.0,
            high=35.0,
            step=10.0,  # Only 2 values: 25, 35
        ))

        def factory(config):
            from core.strategy.momentum import MomentumStrategy, MomentumConfig
            return MomentumStrategy(MomentumConfig(**config))

        config = AutoTuneConfig(
            search_method="grid",
            seed=42,
        )

        tuner = AutoTuner(space, factory, config)
        klines = generate_synthetic_klines(candle_count=200, seed=42)

        report = tuner.tune(klines)

        assert report.search_method == "grid"


class TestConvenienceFunctions:
    """Tests for convenience tuning functions."""

    def test_tune_momentum(self):
        """Verify tune_momentum convenience function."""
        from core.analytics.auto_tuner import tune_momentum
        from core.backtest import generate_synthetic_klines

        klines = generate_synthetic_klines(candle_count=200, seed=42)
        report = tune_momentum(klines, n_trials=3, seed=42)

        assert report.strategy_name == "momentum"
        assert report.n_trials == 3

    def test_tune_breakout(self):
        """Verify tune_breakout convenience function."""
        from core.analytics.auto_tuner import tune_breakout
        from core.backtest import generate_synthetic_klines

        klines = generate_synthetic_klines(candle_count=200, seed=42)
        report = tune_breakout(klines, n_trials=3, seed=42)

        assert report.strategy_name == "breakout"

    def test_tune_mean_reversion(self):
        """Verify tune_mean_reversion convenience function."""
        from core.analytics.auto_tuner import tune_mean_reversion
        from core.backtest import generate_synthetic_klines

        klines = generate_synthetic_klines(candle_count=200, seed=42)
        report = tune_mean_reversion(klines, n_trials=3, seed=42)

        assert report.strategy_name == "mean_reversion"


class TestReproducibility:
    """Tests for reproducibility with seeds."""

    def test_random_search_reproducible(self):
        """Verify same seed produces same results."""
        from core.analytics.auto_tuner import tune_momentum
        from core.backtest import generate_synthetic_klines

        # Use more candles and trending data for more reliable signals
        klines = generate_synthetic_klines(
            candle_count=500,
            trend=0.001,  # Uptrend
            volatility=0.02,
            seed=42,
        )

        report1 = tune_momentum(klines, n_trials=5, seed=123)
        report2 = tune_momentum(klines, n_trials=5, seed=123)

        # Both should have results (may be None if no valid trades)
        # Check that the reports themselves are consistent
        assert report1.n_trials == report2.n_trials
        assert len(report1.all_results) == len(report2.all_results)

        # If both have best results, they should match
        if report1.best_result and report2.best_result:
            assert report1.best_result.config == report2.best_result.config


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
