# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T00:00:00Z
# Purpose: Backtest CLI for command-line strategy evaluation
# Security: Fail-closed, deterministic execution
# === END SIGNATURE ===
"""
Backtest CLI.

Run backtests from command line with reproducible results.

Usage:
    # Synthetic data
    python -m core.backtest.cli --strategy momentum --candles 500 --seed 42

    # CSV data
    python -m core.backtest.cli --strategy momentum --csv data/BTCUSDT-15m.csv

    # All strategies
    python -m core.backtest.cli --all --candles 500

Output: Backtest report with metrics (Sharpe, MDD, PF, win rate).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional, List

from core.backtest.engine import BacktestEngine, BacktestConfig, BacktestResult
from core.backtest.data_loader import (
    DataLoader,
    load_csv,
    generate_synthetic_klines,
    KlinesResult,
)
from core.strategy.orchestrator import StrategyOrchestrator
from core.strategy.momentum import MomentumStrategy, MomentumConfig
from core.strategy.breakout import BreakoutStrategy, BreakoutConfig
from core.strategy.mean_reversion import MeanReversionStrategy, MeanReversionConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("backtest.cli")


# === Strategy Registry ===
# Note: min_confidence=0.02 for backtest with synthetic data (signals are weak)

STRATEGIES = {
    "momentum": lambda: MomentumStrategy(MomentumConfig(min_confidence=0.02)),
    "breakout": lambda: BreakoutStrategy(BreakoutConfig(min_confidence=0.02)),
    "mean_reversion": lambda: MeanReversionStrategy(MeanReversionConfig(min_confidence=0.02)),
}


def create_strategy(name: str):
    """Create strategy instance by name."""
    if name not in STRATEGIES:
        raise ValueError(f"Unknown strategy: {name}. Available: {list(STRATEGIES.keys())}")
    return STRATEGIES[name]()


def load_data(args: argparse.Namespace) -> Optional[KlinesResult]:
    """Load data based on CLI arguments."""
    if args.csv:
        csv_path = Path(args.csv)
        if not csv_path.exists():
            logger.error("CSV file not found: %s", csv_path)
            return None

        logger.info("Loading CSV: %s", csv_path)
        klines = load_csv(
            csv_path,
            symbol=args.symbol,
            timeframe=args.timeframe,
        )

        if klines is None:
            logger.error("Failed to load CSV")
            return None

        logger.info("Loaded %d candles from CSV", klines.candle_count)
        return klines

    else:
        logger.info("Generating synthetic data: %d candles, seed=%s",
                    args.candles, args.seed)

        klines = generate_synthetic_klines(
            symbol=args.symbol,
            timeframe=args.timeframe,
            candle_count=args.candles,
            start_price=args.start_price,
            trend=args.trend,
            volatility=args.volatility,
            seed=args.seed,
        )

        return klines


def run_backtest(
    strategy_name: str,
    klines: KlinesResult,
    config: BacktestConfig,
) -> BacktestResult:
    """Run backtest for a single strategy."""
    strategy = create_strategy(strategy_name)
    # Use backtest-appropriate config: lower min_confidence for synthetic data
    from core.strategy.orchestrator import OrchestratorConfig
    orch_config = OrchestratorConfig(min_confidence=0.02)  # Low for backtest
    orchestrator = StrategyOrchestrator([strategy], orch_config)
    engine = BacktestEngine(orchestrator, config)

    start_time = time.time()
    result = engine.run(klines)
    elapsed = time.time() - start_time

    logger.info("Backtest complete in %.2fs", elapsed)
    return result


def run_all_strategies(
    klines: KlinesResult,
    config: BacktestConfig,
) -> List[tuple[str, BacktestResult]]:
    """Run backtest for all strategies."""
    results = []

    for name in STRATEGIES:
        logger.info("Running strategy: %s", name)
        result = run_backtest(name, klines, config)
        results.append((name, result))

    return results


def format_comparison_table(results: List[tuple[str, BacktestResult]]) -> str:
    """Format comparison table for multiple strategies."""
    lines = [
        "=" * 80,
        "STRATEGY COMPARISON",
        "=" * 80,
        f"{'Strategy':<20} {'Return':>10} {'Sharpe':>10} {'MaxDD':>10} {'WinRate':>10} {'Trades':>8}",
        "-" * 80,
    ]

    for name, result in sorted(results, key=lambda x: x[1].sharpe_ratio, reverse=True):
        lines.append(
            f"{name:<20} "
            f"{result.total_return_pct:>9.2f}% "
            f"{result.sharpe_ratio:>10.3f} "
            f"{result.max_drawdown:>9.2%} "
            f"{result.win_rate:>9.2%} "
            f"{result.total_trades:>8}"
        )

    lines.append("=" * 80)
    return "\n".join(lines)


def main() -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="HOPE Backtest CLI - Strategy Evaluation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Synthetic data with momentum strategy
  python -m core.backtest.cli --strategy momentum --candles 500 --seed 42

  # CSV data
  python -m core.backtest.cli --strategy momentum --csv data/BTCUSDT-15m.csv

  # All strategies comparison
  python -m core.backtest.cli --all --candles 500

  # Trending market simulation
  python -m core.backtest.cli --strategy breakout --trend 0.001 --volatility 0.02
        """
    )

    # Strategy selection
    strategy_group = parser.add_mutually_exclusive_group(required=True)
    strategy_group.add_argument(
        "--strategy", "-s",
        choices=list(STRATEGIES.keys()),
        help="Strategy to backtest"
    )
    strategy_group.add_argument(
        "--all", "-a",
        action="store_true",
        help="Run all strategies and compare"
    )

    # Data source
    data_group = parser.add_mutually_exclusive_group()
    data_group.add_argument(
        "--csv",
        type=str,
        help="Path to CSV file with OHLCV data"
    )
    data_group.add_argument(
        "--candles", "-n",
        type=int,
        default=500,
        help="Number of candles for synthetic data (default: 500)"
    )

    # Symbol and timeframe
    parser.add_argument(
        "--symbol",
        type=str,
        default="BTCUSDT",
        help="Trading symbol (default: BTCUSDT)"
    )
    parser.add_argument(
        "--timeframe",
        type=str,
        default="15m",
        help="Timeframe (default: 15m)"
    )

    # Synthetic data parameters
    parser.add_argument(
        "--start-price",
        type=float,
        default=50000.0,
        help="Starting price for synthetic data (default: 50000)"
    )
    parser.add_argument(
        "--trend",
        type=float,
        default=0.0,
        help="Trend factor for synthetic data (default: 0.0)"
    )
    parser.add_argument(
        "--volatility",
        type=float,
        default=0.02,
        help="Volatility for synthetic data (default: 0.02)"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducibility"
    )

    # Backtest configuration
    parser.add_argument(
        "--capital",
        type=float,
        default=10000.0,
        help="Initial capital (default: 10000)"
    )
    parser.add_argument(
        "--commission",
        type=float,
        default=0.001,
        help="Commission percentage (default: 0.001 = 0.1%%)"
    )
    parser.add_argument(
        "--slippage",
        type=float,
        default=0.0005,
        help="Slippage percentage (default: 0.0005 = 0.05%%)"
    )

    # Output options
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON"
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        help="Save results to file"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output"
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load data
    klines = load_data(args)
    if klines is None:
        return 1

    # Create backtest config
    bt_config = BacktestConfig(
        initial_capital=args.capital,
        commission_pct=args.commission,
        slippage_pct=args.slippage,
        spot_only=True,
    )

    # Run backtest(s)
    if args.all:
        results = run_all_strategies(klines, bt_config)

        if args.json:
            output = {
                "comparison": [
                    {"strategy": name, **result.to_dict()}
                    for name, result in results
                ]
            }
            print(json.dumps(output, indent=2, default=str))
        else:
            print(format_comparison_table(results))

        # Also print best strategy details
        if results:
            best_name, best_result = max(results, key=lambda x: x[1].sharpe_ratio)
            print(f"\nBest strategy: {best_name}")
            print(best_result.format_report())

    else:
        result = run_backtest(args.strategy, klines, bt_config)

        if args.json:
            print(json.dumps(result.to_dict(), indent=2, default=str))
        else:
            print(result.format_report())

    # Save to file if requested
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if args.all:
            data = {
                "comparison": [
                    {"strategy": name, **result.to_dict()}
                    for name, result in results
                ]
            }
        else:
            data = result.to_dict()

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

        logger.info("Results saved to: %s", output_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
