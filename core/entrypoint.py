# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T02:00:00Z
# Purpose: Unified Live Trading Entrypoint - single path to production
# Security: Gatekeeper must pass before ANY trading code executes
# === END SIGNATURE ===
"""
HOPE Live Trading Entrypoint.

THE ONLY authorized path to live trading.
No trading code executes until Gatekeeper passes.

Flow:
1. Parse arguments (SSoT from cmdline)
2. Run Gatekeeper (all gates must pass)
3. Initialize trading infrastructure (CircuitBreaker, etc.)
4. Start trading loop

Exit Codes:
- 0: Clean shutdown
- 1: Internal error
- 2: Gate blocked (expected)
- 3: Trading error

Usage:
    # TESTNET (safe)
    python -m core.entrypoint --mode TESTNET --symbol BTCUSDT --amount 11

    # MAINNET (requires explicit ACK)
    python -m core.entrypoint --mode MAINNET --symbol BTCUSDT --amount 11 \
        --live-enable --live-ack I_KNOW_WHAT_I_AM_DOING

    # Dry-run (no real orders)
    python -m core.entrypoint --mode TESTNET --dry-run
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Configure logging FIRST (before any imports that might log)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger("entrypoint")


class ExitCode:
    """Exit codes for entrypoint."""
    SUCCESS = 0
    INTERNAL_ERROR = 1
    GATE_BLOCKED = 2
    TRADING_ERROR = 3


class TradingContext:
    """
    Trading context with all initialized components.

    Created ONLY after Gatekeeper passes.
    """

    def __init__(
        self,
        mode: str,
        symbol: str,
        quote_amount: float,
        dry_run: bool,
    ):
        self.mode = mode
        self.symbol = symbol
        self.quote_amount = quote_amount
        self.dry_run = dry_run

        # Components (initialized lazily)
        self._circuit_breaker = None
        self._performance_tracker = None
        self._order_router = None
        self._ml_predictor = None
        self._feature_extractor = None

        logger.info("TradingContext created: mode=%s, symbol=%s", mode, symbol)

    def initialize(self) -> bool:
        """
        Initialize all trading components.

        Returns:
            True if all components initialized successfully
        """
        try:
            # 1. Circuit Breaker
            self._init_circuit_breaker()

            # 2. Performance Tracker
            self._init_performance_tracker()

            # 3. ML Components (optional)
            self._init_ml_components()

            # 4. Order Router
            self._init_order_router()

            logger.info("All trading components initialized")
            return True

        except Exception as e:
            logger.exception("Failed to initialize trading components: %s", e)
            return False

    def _init_circuit_breaker(self) -> None:
        """Initialize circuit breaker for API calls."""
        try:
            from core.runtime.circuit_breaker import get_binance_circuit

            self._circuit_breaker = get_binance_circuit()
            logger.info("CircuitBreaker initialized: %s", self._circuit_breaker.name)

        except ImportError:
            logger.warning("CircuitBreaker not available")

    def _init_performance_tracker(self) -> None:
        """Initialize performance tracker."""
        try:
            from core.analytics.performance import get_performance_tracker

            self._performance_tracker = get_performance_tracker()
            logger.info("PerformanceTracker initialized")

        except ImportError:
            logger.warning("PerformanceTracker not available")

    def _init_ml_components(self) -> None:
        """Initialize ML predictor and feature extractor."""
        try:
            from core.ai.features import get_feature_extractor
            from core.ai.ml_predictor import get_ml_predictor

            self._feature_extractor = get_feature_extractor()
            self._ml_predictor = get_ml_predictor()
            logger.info("ML components initialized (features: %d)",
                       len(self._feature_extractor.get_feature_names()))

        except ImportError:
            logger.warning("ML components not available")

    def _init_order_router(self) -> None:
        """Initialize order router with circuit breaker integration."""
        try:
            from core.trade.order_router import TradingOrderRouter

            self._order_router = TradingOrderRouter(
                mode=self.mode,
                dry_run=self.dry_run,
            )
            logger.info("OrderRouter initialized: mode=%s, dry_run=%s",
                       self.mode, self.dry_run)

        except ImportError as e:
            logger.warning("OrderRouter not available: %s", e)

    def get_ml_prediction(self, market_data) -> Optional[float]:
        """
        Get ML prediction for market data.

        Returns:
            ML score [-1, +1] or None if not available
        """
        if not self._feature_extractor or not self._ml_predictor:
            return None

        try:
            features = self._feature_extractor.extract(market_data)
            if features is None:
                return None

            return self._ml_predictor.predict(features)

        except Exception as e:
            logger.warning("ML prediction failed: %s", e)
            return None

    def record_trade(self, trade_result) -> None:
        """Record completed trade in performance tracker."""
        if not self._performance_tracker:
            return

        try:
            from core.analytics.performance import CompletedTrade

            # Convert trade result to CompletedTrade format
            completed = CompletedTrade(
                trade_id=trade_result.get("client_order_id", "unknown"),
                symbol=trade_result.get("symbol", self.symbol),
                side=trade_result.get("side", "BUY"),
                entry_price=trade_result.get("avg_price", 0),
                exit_price=trade_result.get("avg_price", 0),  # For single fills
                quantity=trade_result.get("executed_qty", 0),
                pnl=trade_result.get("pnl", 0),
                pnl_pct=trade_result.get("pnl_pct", 0),
                entry_time=datetime.now(timezone.utc),
                exit_time=datetime.now(timezone.utc),
                strategy_name="live",
            )

            self._performance_tracker.record_trade(completed)
            logger.debug("Trade recorded in PerformanceTracker")

        except Exception as e:
            logger.warning("Failed to record trade: %s", e)

    def shutdown(self) -> None:
        """Clean shutdown of all components."""
        logger.info("Shutting down trading context...")

        # Save performance snapshot
        if self._performance_tracker:
            try:
                snapshot = self._performance_tracker.get_snapshot()
                logger.info("Final PnL: %.2f%% (DD: %.2f%%)",
                          snapshot.return_24h_pct, snapshot.current_drawdown_pct)
            except Exception as e:
                logger.warning("Failed to get performance snapshot: %s", e)


class LiveTradingRunner:
    """
    Main trading runner.

    Manages the trading loop with proper error handling.
    """

    def __init__(self, context: TradingContext):
        self.context = context
        self._running = False
        self._shutdown_requested = False

    def run(self) -> int:
        """
        Run the main trading loop.

        Returns:
            Exit code
        """
        self._running = True

        try:
            logger.info("Starting trading loop: mode=%s, symbol=%s",
                       self.context.mode, self.context.symbol)

            cycle_count = 0
            while self._running and not self._shutdown_requested:
                cycle_count += 1

                try:
                    self._trading_cycle(cycle_count)

                except KeyboardInterrupt:
                    logger.info("Keyboard interrupt received")
                    break

                except Exception as e:
                    logger.error("Trading cycle error: %s", e)
                    # Continue running unless fatal

                # Sleep between cycles (configurable)
                time.sleep(1.0)

            logger.info("Trading loop ended after %d cycles", cycle_count)
            return ExitCode.SUCCESS

        except Exception as e:
            logger.exception("Fatal trading error: %s", e)
            return ExitCode.TRADING_ERROR

        finally:
            self._running = False

    def _trading_cycle(self, cycle: int) -> None:
        """Execute one trading cycle."""
        if cycle % 60 == 0:  # Log every minute
            logger.info("Trading cycle %d (mode=%s)", cycle, self.context.mode)

        # In dry-run mode, just heartbeat
        if self.context.dry_run:
            return

        # TODO: Implement actual trading logic
        # 1. Fetch market data
        # 2. Get ML prediction: ml_score = context.get_ml_prediction(market_data)
        # 3. Run strategy
        # 4. Execute order if signal
        # 5. Record trade: context.record_trade(result)

    def request_shutdown(self) -> None:
        """Request graceful shutdown."""
        logger.info("Shutdown requested")
        self._shutdown_requested = True


def setup_signal_handlers(runner: LiveTradingRunner, gatekeeper) -> None:
    """Setup signal handlers for graceful shutdown."""

    def signal_handler(signum, frame):
        logger.info("Signal %d received, initiating shutdown", signum)
        runner.request_shutdown()
        gatekeeper.release()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="HOPE Live Trading Entrypoint",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # TESTNET (safe for testing)
  python -m core.entrypoint --mode TESTNET --symbol BTCUSDT --amount 11

  # MAINNET (requires explicit acknowledgment)
  python -m core.entrypoint --mode MAINNET --symbol BTCUSDT --amount 11 \\
      --live-enable --live-ack I_KNOW_WHAT_I_AM_DOING

  # Dry-run (no real orders, simulation only)
  python -m core.entrypoint --mode TESTNET --dry-run

  # Skip certain gates (for development)
  python -m core.entrypoint --mode DRY --skip-changelog --skip-reconcile
        """
    )

    # Mode and trading params
    parser.add_argument(
        "--mode", "-m",
        default="TESTNET",
        choices=["DRY", "TESTNET", "MAINNET"],
        help="Trading mode (default: TESTNET)"
    )
    parser.add_argument(
        "--symbol", "-s",
        default="BTCUSDT",
        help="Trading symbol (default: BTCUSDT)"
    )
    parser.add_argument(
        "--amount", "-a",
        type=float,
        default=11.0,
        help="Quote amount in USDT (default: 11.0)"
    )

    # MAINNET safeguards
    parser.add_argument(
        "--live-enable",
        action="store_true",
        help="Enable live trading (required for MAINNET)"
    )
    parser.add_argument(
        "--live-ack",
        default="",
        help="Acknowledgment string for MAINNET (must be: I_KNOW_WHAT_I_AM_DOING)"
    )

    # Dry-run
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate trading without real orders"
    )

    # Development options
    parser.add_argument(
        "--skip-changelog",
        action="store_true",
        help="Skip changelog check (development only)"
    )
    parser.add_argument(
        "--skip-reconcile",
        action="store_true",
        help="Skip state reconciliation (development only)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )

    return parser.parse_args()


def main() -> int:
    """
    Main entrypoint.

    Returns:
        Exit code (0=success, 1=error, 2=blocked, 3=trading error)
    """
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("="*60)
    logger.info("HOPE Live Trading Entrypoint")
    logger.info("="*60)
    logger.info("Mode: %s | Symbol: %s | Amount: %.2f",
               args.mode, args.symbol, args.amount)

    # ========================================
    # PHASE 1: GATEKEEPER (MUST PASS)
    # ========================================
    logger.info("Phase 1: Running Gatekeeper...")

    try:
        from core.runtime.gatekeeper import Gatekeeper

        gatekeeper = Gatekeeper(
            mode=args.mode,
            live_enable=args.live_enable,
            live_ack=args.live_ack,
            symbol=args.symbol,
            quote_amount=args.amount,
            dry_run=args.dry_run or args.mode == "DRY",
            skip_changelog=args.skip_changelog,
            skip_reconcile=args.skip_reconcile,
        )

        gate_result = gatekeeper.run()

        if not gate_result.ok:
            logger.error("GATEKEEPER BLOCKED: %s", gate_result.block_reason)
            logger.error("Evidence: %s", gate_result.evidence_path)
            return gate_result.exit_code.value

        logger.info("Gatekeeper PASSED (%.1fms)", gate_result.total_duration_ms)

    except Exception as e:
        logger.exception("Gatekeeper failed with exception: %s", e)
        return ExitCode.INTERNAL_ERROR

    # ========================================
    # PHASE 2: INITIALIZE TRADING CONTEXT
    # ========================================
    logger.info("Phase 2: Initializing trading context...")

    try:
        context = TradingContext(
            mode=args.mode,
            symbol=args.symbol,
            quote_amount=args.amount,
            dry_run=args.dry_run or args.mode == "DRY",
        )

        if not context.initialize():
            logger.error("Failed to initialize trading context")
            gatekeeper.release()
            return ExitCode.INTERNAL_ERROR

    except Exception as e:
        logger.exception("Context initialization failed: %s", e)
        gatekeeper.release()
        return ExitCode.INTERNAL_ERROR

    # ========================================
    # PHASE 3: START TRADING
    # ========================================
    logger.info("Phase 3: Starting trading...")

    try:
        runner = LiveTradingRunner(context)
        setup_signal_handlers(runner, gatekeeper)

        exit_code = runner.run()

    except Exception as e:
        logger.exception("Trading loop failed: %s", e)
        exit_code = ExitCode.TRADING_ERROR

    finally:
        # Always cleanup
        context.shutdown()
        gatekeeper.release()

    logger.info("="*60)
    logger.info("Entrypoint finished with exit code: %d", exit_code)
    logger.info("="*60)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
