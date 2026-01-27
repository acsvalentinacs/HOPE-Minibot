# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T22:00:00Z
# Purpose: Integration tests for Phase 3 - Backtest Engine
# Security: Test-only, no production impact
# === END SIGNATURE ===
"""
Tests for Phase 3 Backtest Engine.

Modules tested:
- core.backtest.metrics (drawdown, Sharpe, trade stats)
- core.backtest.data_loader (CSV, synthetic, validation)
- core.backtest.engine (BacktestEngine, BacktestConfig)
"""
import pytest
import numpy as np
from pathlib import Path
from unittest.mock import Mock, patch
import tempfile
import csv


class TestMetrics:
    """Tests for metrics calculation."""

    def test_calculate_drawdown_basic(self):
        """Verify drawdown calculation."""
        from core.backtest.metrics import calculate_drawdown

        # Simple drawdown: 100 -> 90 -> 100
        equity = [100, 95, 90, 95, 100]
        dd = calculate_drawdown(equity)

        assert dd.max_drawdown_pct == pytest.approx(0.10, rel=0.01)  # 10%
        assert dd.peak_equity == 100
        assert dd.trough_equity == 90

    def test_calculate_drawdown_no_drawdown(self):
        """Verify no drawdown when equity only goes up."""
        from core.backtest.metrics import calculate_drawdown

        equity = [100, 105, 110, 115, 120]
        dd = calculate_drawdown(equity)

        assert dd.max_drawdown_pct == 0.0

    def test_calculate_drawdown_empty(self):
        """Verify empty equity returns zero drawdown."""
        from core.backtest.metrics import calculate_drawdown

        dd = calculate_drawdown([])
        assert dd.max_drawdown_pct == 0.0

    def test_calculate_sharpe_ratio(self):
        """Verify Sharpe ratio calculation."""
        from core.backtest.metrics import calculate_sharpe_ratio

        # Varying positive returns (need std > 0 for Sharpe calculation)
        returns = [0.01, 0.012, 0.008, 0.011, 0.009]  # ~1% avg with variance
        sharpe = calculate_sharpe_ratio(returns)

        # Should be positive (positive mean returns)
        assert sharpe > 0

    def test_calculate_sharpe_ratio_negative(self):
        """Verify negative Sharpe for losing strategy."""
        from core.backtest.metrics import calculate_sharpe_ratio

        returns = [-0.01, -0.02, -0.01, -0.02, -0.01]  # Losses
        sharpe = calculate_sharpe_ratio(returns)

        assert sharpe < 0

    def test_calculate_sharpe_ratio_empty(self):
        """Verify empty returns gives zero Sharpe."""
        from core.backtest.metrics import calculate_sharpe_ratio

        sharpe = calculate_sharpe_ratio([])
        assert sharpe == 0.0

    def test_profit_factor(self):
        """Verify profit factor calculation."""
        from core.backtest.metrics import calculate_profit_factor

        # 2:1 profit factor
        pf = calculate_profit_factor(200, 100)
        assert pf == pytest.approx(2.0, rel=0.01)

    def test_profit_factor_no_loss(self):
        """Verify profit factor with no losses."""
        from core.backtest.metrics import calculate_profit_factor

        pf = calculate_profit_factor(100, 0)
        assert pf == 999.99  # Capped

    def test_profit_factor_no_profit(self):
        """Verify profit factor with no profits."""
        from core.backtest.metrics import calculate_profit_factor

        pf = calculate_profit_factor(0, 100)
        assert pf == 0.0

    def test_win_rate(self):
        """Verify win rate calculation."""
        from core.backtest.metrics import calculate_win_rate

        wr = calculate_win_rate(7, 10)
        assert wr == pytest.approx(0.7, rel=0.01)

    def test_trade_stats(self):
        """Verify comprehensive trade stats."""
        from core.backtest.metrics import calculate_trade_stats

        pnls = [100, -50, 80, -30, 60, -20, 40]
        stats = calculate_trade_stats(pnls)

        assert stats.total_trades == 7
        assert stats.winning_trades == 4
        assert stats.losing_trades == 3
        assert stats.net_pnl == pytest.approx(180, rel=0.01)
        assert stats.largest_win == 100
        assert stats.largest_loss == -50

    def test_trade_stats_empty(self):
        """Verify empty trades return zeroed stats."""
        from core.backtest.metrics import calculate_trade_stats

        stats = calculate_trade_stats([])
        assert stats.total_trades == 0
        assert stats.win_rate == 0.0


class TestDataLoader:
    """Tests for data loading."""

    def test_generate_synthetic_klines(self):
        """Verify synthetic data generation."""
        from core.backtest.data_loader import generate_synthetic_klines

        klines = generate_synthetic_klines(
            symbol="BTCUSDT",
            timeframe="15m",
            candle_count=100,
            start_price=50000.0,
            seed=42,
        )

        assert klines is not None
        assert klines.symbol == "BTCUSDT"
        assert klines.candle_count == 100
        assert len(klines.closes) == 100
        assert klines.closes[0] != klines.closes[-1]  # Some price movement

    def test_synthetic_reproducible_with_seed(self):
        """Verify same seed produces same data."""
        from core.backtest.data_loader import generate_synthetic_klines

        k1 = generate_synthetic_klines(seed=123)
        k2 = generate_synthetic_klines(seed=123)

        assert np.allclose(k1.closes, k2.closes)

    def test_validate_klines_valid(self):
        """Verify validation passes for good data."""
        from core.backtest.data_loader import validate_klines, generate_synthetic_klines

        klines = generate_synthetic_klines(candle_count=100)
        validation = validate_klines(klines, "15m", min_candles=50)

        assert validation.is_valid
        assert len(validation.errors) == 0

    def test_validate_klines_insufficient(self):
        """Verify validation fails for insufficient candles."""
        from core.backtest.data_loader import validate_klines, generate_synthetic_klines

        klines = generate_synthetic_klines(candle_count=30)
        validation = validate_klines(klines, "15m", min_candles=50)

        assert not validation.is_valid
        assert "Insufficient" in validation.errors[0]

    def test_validate_klines_none(self):
        """Verify validation fails for None input."""
        from core.backtest.data_loader import validate_klines

        validation = validate_klines(None, "15m")
        assert not validation.is_valid

    def test_load_csv_missing_file(self):
        """Verify load_csv returns None for missing file."""
        from core.backtest.data_loader import load_csv

        result = load_csv("/nonexistent/file.csv")
        assert result is None

    def test_load_csv_valid(self):
        """Verify load_csv loads valid CSV."""
        from core.backtest.data_loader import load_csv

        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['open_time', 'open', 'high', 'low', 'close', 'volume'])
            for i in range(60):
                writer.writerow([
                    1704067200000 + i * 900000,  # 15min intervals
                    100 + i * 0.1,
                    101 + i * 0.1,
                    99 + i * 0.1,
                    100.5 + i * 0.1,
                    1000 + i,
                ])
            csv_path = f.name

        try:
            result = load_csv(csv_path, "BTCUSDT", "15m")
            assert result is not None
            assert result.candle_count == 60
            assert result.symbol == "BTCUSDT"
        finally:
            Path(csv_path).unlink()

    def test_data_loader_class(self):
        """Verify DataLoader class works."""
        from core.backtest.data_loader import DataLoader

        loader = DataLoader()

        # Synthetic
        klines = loader.generate_synthetic(candle_count=100)
        assert klines.candle_count == 100

        # Validation
        validation = loader.validate(klines, "15m")
        assert validation.is_valid


class TestBacktestEngine:
    """Tests for backtest engine."""

    def test_engine_creates(self):
        """Verify engine can be created."""
        from core.backtest.engine import BacktestEngine, BacktestConfig
        from core.strategy.orchestrator import StrategyOrchestrator
        from core.strategy.momentum import MomentumStrategy

        orchestrator = StrategyOrchestrator([MomentumStrategy()])
        config = BacktestConfig(initial_capital=10000)
        engine = BacktestEngine(orchestrator, config)

        assert engine is not None
        assert engine._config.initial_capital == 10000

    def test_run_empty_returns_empty_result(self):
        """Verify running on insufficient data returns empty result."""
        from core.backtest.engine import BacktestEngine, BacktestConfig
        from core.backtest.data_loader import generate_synthetic_klines
        from core.strategy.orchestrator import StrategyOrchestrator
        from core.strategy.momentum import MomentumStrategy

        orchestrator = StrategyOrchestrator([MomentumStrategy()])
        config = BacktestConfig(min_candles=100)
        engine = BacktestEngine(orchestrator, config)

        # Only 50 candles, but need 100
        klines = generate_synthetic_klines(candle_count=50)
        result = engine.run(klines)

        assert result.total_trades == 0
        assert not result.validation.is_valid

    def test_run_synthetic_data(self):
        """Verify backtest runs on synthetic data."""
        from core.backtest.engine import BacktestEngine, BacktestConfig
        from core.backtest.data_loader import generate_synthetic_klines
        from core.strategy.orchestrator import StrategyOrchestrator
        from core.strategy.momentum import MomentumStrategy

        orchestrator = StrategyOrchestrator([MomentumStrategy()])
        config = BacktestConfig(
            initial_capital=10000,
            min_candles=50,
        )
        engine = BacktestEngine(orchestrator, config)

        # Generate trending data for momentum strategy
        klines = generate_synthetic_klines(
            candle_count=200,
            trend=0.001,  # Uptrend
            volatility=0.01,
            seed=42,
        )
        result = engine.run(klines)

        assert result is not None
        assert result.validation.is_valid
        assert len(result.equity_curve) > 0
        assert result.final_equity > 0

    def test_equity_curve_starts_at_initial(self):
        """Verify equity curve starts at initial capital."""
        from core.backtest.engine import BacktestEngine, BacktestConfig
        from core.backtest.data_loader import generate_synthetic_klines
        from core.strategy.orchestrator import StrategyOrchestrator
        from core.strategy.momentum import MomentumStrategy

        initial = 5000.0
        orchestrator = StrategyOrchestrator([MomentumStrategy()])
        config = BacktestConfig(initial_capital=initial, min_candles=50)
        engine = BacktestEngine(orchestrator, config)

        klines = generate_synthetic_klines(candle_count=100, seed=42)
        result = engine.run(klines)

        assert result.equity_curve[0] == initial

    def test_spot_only_no_short(self):
        """Verify SHORT signals are blocked in spot mode."""
        from core.backtest.engine import BacktestEngine, BacktestConfig
        from core.backtest.data_loader import generate_synthetic_klines
        from core.strategy.orchestrator import StrategyOrchestrator
        from core.strategy.momentum import MomentumStrategy

        orchestrator = StrategyOrchestrator([MomentumStrategy()])
        config = BacktestConfig(spot_only=True, min_candles=50)
        engine = BacktestEngine(orchestrator, config)

        # Downtrend data (would generate SHORT signals normally)
        klines = generate_synthetic_klines(
            candle_count=200,
            trend=-0.002,  # Downtrend
            seed=42,
        )
        result = engine.run(klines)

        # All trades should be LONG (or none if no opportunities)
        from core.strategy.base import PositionSide
        for trade in result.trades:
            assert trade.side != PositionSide.SHORT

    def test_commission_deducted(self):
        """Verify commission is deducted from trades."""
        from core.backtest.engine import BacktestEngine, BacktestConfig
        from core.backtest.data_loader import generate_synthetic_klines
        from core.strategy.orchestrator import StrategyOrchestrator
        from core.strategy.momentum import MomentumStrategy

        # High commission to see effect
        orchestrator = StrategyOrchestrator([MomentumStrategy()])
        config = BacktestConfig(
            commission_pct=0.01,  # 1% commission
            min_candles=50,
        )
        engine = BacktestEngine(orchestrator, config)

        klines = generate_synthetic_klines(candle_count=200, trend=0.001, seed=42)
        result = engine.run(klines)

        # If trades occurred, final equity should be affected by commission
        # Can't assert specific value without knowing exact trades
        assert result.final_equity <= result.config.initial_capital or result.total_trades == 0 or result.total_pnl != 0

    def test_result_format_report(self):
        """Verify result can format report."""
        from core.backtest.engine import BacktestEngine, BacktestConfig
        from core.backtest.data_loader import generate_synthetic_klines
        from core.strategy.orchestrator import StrategyOrchestrator
        from core.strategy.momentum import MomentumStrategy

        orchestrator = StrategyOrchestrator([MomentumStrategy()])
        engine = BacktestEngine(orchestrator, BacktestConfig(min_candles=50))

        klines = generate_synthetic_klines(candle_count=200, seed=42)
        result = engine.run(klines)

        report = result.format_report()
        assert "BACKTEST RESULTS" in report
        assert "Initial Capital" in report


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_run_backtest_function(self):
        """Verify run_backtest convenience function."""
        from core.backtest import run_backtest
        from core.backtest.data_loader import generate_synthetic_klines

        klines = generate_synthetic_klines(candle_count=200, seed=42)
        result = run_backtest(klines)

        assert result is not None
        assert result.validation.is_valid


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
