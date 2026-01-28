# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T02:00:00Z
# Modified by: Claude (opus-4)
# Modified at: 2026-01-29T00:20:00Z
# Purpose: Unified Live Trading Entrypoint - single path to production
# Security: Gatekeeper must pass, STOP.flag halts trading, health_v5.json updated
# Change: Added systemd watchdog integration (sd_notify)
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
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# State directory for health file
STATE_DIR = Path(__file__).resolve().parent.parent / "state"
HEALTH_FILE = STATE_DIR / "health_v5.json"
STOP_FLAG = Path(__file__).resolve().parent.parent / "STOP.flag"

# Systemd watchdog integration (fail-open: works without systemd)
try:
    from core.runtime.systemd_notify import sd_ready, sd_watchdog, sd_stopping, sd_status
    SYSTEMD_AVAILABLE = True
except ImportError:
    SYSTEMD_AVAILABLE = False
    def sd_ready() -> bool: return False
    def sd_watchdog() -> bool: return False
    def sd_stopping() -> bool: return False
    def sd_status(s: str) -> bool: return False

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


def _atomic_write(path: Path, content: str) -> None:
    """Atomic write: temp -> fsync -> replace."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def write_health_v5(
    mode: str,
    uptime_sec: int,
    open_positions: int,
    queue_size: int = 0,
    daily_pnl_usd: float = 0.0,
    daily_stop_hit: bool = False,
    last_error: Optional[str] = None,
) -> None:
    """
    Write health_v5.json for TG bot and health probes.

    Format matches tools/health_probe_v5.py requirements.
    """
    health = {
        "engine_version": "5.2.0",
        "mode": mode,
        "hb_ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "uptime_sec": uptime_sec,
        "open_positions": open_positions,
        "queue_size": queue_size,
        "daily_pnl_usd": daily_pnl_usd,
        "daily_stop_hit": daily_stop_hit,
        "last_error": last_error,
    }

    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _atomic_write(HEALTH_FILE, json.dumps(health, indent=2))
        logger.debug("health_v5.json updated: uptime=%ds, positions=%d", uptime_sec, open_positions)
    except Exception as e:
        logger.warning("Failed to write health_v5.json: %s", e)


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
        self._klines_provider = None
        self._signal_engine = None
        self._strategy_orchestrator = None

        # Trading state
        self._open_positions: List[Any] = []
        self._last_signal_id: Optional[str] = None
        self._start_time: float = time.time()
        self._daily_pnl_usd: float = 0.0
        self._daily_stop_hit: bool = False
        self._last_error: Optional[str] = None

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

            # 5. Market Data Provider
            self._init_klines_provider()

            # 6. Signal Engine
            self._init_signal_engine()

            # 7. Strategy Orchestrator
            self._init_strategy_orchestrator()

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

    def _init_klines_provider(self) -> None:
        """Initialize market data provider."""
        try:
            from core.market.klines_provider import get_klines_provider

            # KlinesProvider uses public API (no auth needed), works for all modes
            self._klines_provider = get_klines_provider()
            logger.info("KlinesProvider initialized")

        except ImportError as e:
            logger.warning("KlinesProvider not available: %s", e)

    def _init_signal_engine(self) -> None:
        """Initialize signal generation engine."""
        try:
            from core.ai.signal_engine import SignalEngine

            self._signal_engine = SignalEngine()
            logger.info("SignalEngine initialized")

        except ImportError as e:
            logger.warning("SignalEngine not available: %s", e)

    def _init_strategy_orchestrator(self) -> None:
        """Initialize strategy orchestrator."""
        try:
            from core.strategy.orchestrator import StrategyOrchestrator, OrchestratorConfig
            from core.strategy.momentum import MomentumStrategy
            from core.strategy.breakout import BreakoutStrategy
            from core.strategy.mean_reversion import MeanReversionStrategy

            # Create strategies
            strategies = [
                MomentumStrategy(),
                BreakoutStrategy(),
                MeanReversionStrategy(),
            ]

            config = OrchestratorConfig(
                spot_only=True,  # Only LONG positions on Spot
                dedup_ttl_seconds=300,  # 5 min dedup
            )

            self._strategy_orchestrator = StrategyOrchestrator(strategies, config)
            logger.info("StrategyOrchestrator initialized: %d strategies", len(strategies))

        except ImportError as e:
            logger.warning("StrategyOrchestrator not available: %s", e)

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

    def update_health(self, queue_size: int = 0) -> None:
        """Update health_v5.json with current state."""
        uptime = int(time.time() - self._start_time)
        write_health_v5(
            mode=self.mode,
            uptime_sec=uptime,
            open_positions=len(self._open_positions),
            queue_size=queue_size,
            daily_pnl_usd=self._daily_pnl_usd,
            daily_stop_hit=self._daily_stop_hit,
            last_error=self._last_error,
        )
        # Systemd watchdog keepalive
        sd_watchdog()
        sd_status(f"{self.mode} | pos={len(self._open_positions)} | pnl=${self._daily_pnl_usd:.2f}")

    def shutdown(self) -> None:
        """Clean shutdown of all components."""
        logger.info("Shutting down trading context...")
        sd_stopping()  # Notify systemd

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

                # Check STOP.flag (fail-closed)
                if STOP_FLAG.exists():
                    reason = STOP_FLAG.read_text(encoding="utf-8").strip().split("\n")[0]
                    logger.critical("STOP.flag detected: %s - halting trading", reason)
                    break

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
        """
        Execute one trading cycle.

        Flow:
        1. Fetch market data (KlinesProvider)
        2. Convert to MarketData
        3. Get ML prediction
        4. Run StrategyOrchestrator.decide()
        5. If ENTER: execute order
        6. If EXIT: close position
        7. Record results
        8. Update health_v5.json
        """
        # Update health every 10 cycles (10 seconds)
        if cycle % 10 == 0:
            self.context.update_health()

        if cycle % 60 == 0:  # Log every minute
            logger.info("Trading cycle %d (mode=%s, positions=%d)",
                       cycle, self.context.mode, len(self.context._open_positions))

        # In dry-run mode, just heartbeat
        if self.context.dry_run:
            return

        # === STEP 1: Fetch market data ===
        market_data = self._fetch_market_data()
        if market_data is None:
            logger.debug("No market data available, skipping cycle")
            return

        # === STEP 2: Get ML prediction ===
        ml_prediction = self.context.get_ml_prediction(market_data)

        # === STEP 3: Get signal from SignalEngine ===
        signal = None
        if self.context._signal_engine:
            signal = self.context._signal_engine.generate_signal(
                market_data=market_data,
                sentiment_score=None,  # TODO: integrate sentiment
                ml_prediction=ml_prediction,
            )

        # === STEP 4: Run Strategy Orchestrator ===
        decision = self._get_orchestrator_decision(market_data)
        if decision is None:
            return

        # === STEP 5: Execute decision ===
        if decision.action.value == "ENTER" and decision.signal:
            self._execute_entry(decision)
        elif decision.action.value == "EXIT":
            self._execute_exit(decision, market_data)

    def _fetch_market_data(self):
        """Fetch market data and convert to MarketData."""
        if not self.context._klines_provider:
            return None

        try:
            from core.ai.signal_engine import MarketData

            klines = self.context._klines_provider.get_klines(
                symbol=self.context.symbol,
                timeframe="15m",
                limit=100,
            )

            if klines is None or klines.candle_count < 35:
                logger.warning("Insufficient klines: %d",
                             klines.candle_count if klines else 0)
                return None

            if klines.is_stale:
                logger.warning("Stale klines data, skipping")
                return None

            return MarketData(
                symbol=self.context.symbol,
                timestamp=int(time.time()),
                opens=klines.opens,
                highs=klines.highs,
                lows=klines.lows,
                closes=klines.closes,
                volumes=klines.volumes,
            )

        except Exception as e:
            logger.error("Failed to fetch market data: %s", e)
            return None

    def _get_orchestrator_decision(self, market_data):
        """Get decision from strategy orchestrator."""
        if not self.context._strategy_orchestrator:
            return None

        try:
            from core.strategy.base import Position

            # Convert open positions to orchestrator format
            positions = []
            for pos in self.context._open_positions:
                if hasattr(pos, 'symbol'):
                    positions.append(pos)

            decision = self.context._strategy_orchestrator.decide(
                market_data=market_data,
                current_positions=positions,
                timeframe="15m",
            )

            if decision.is_actionable:
                logger.info("Orchestrator decision: %s (%s) confidence=%.2f",
                          decision.action.value, decision.strategy_name, decision.confidence)

            return decision

        except Exception as e:
            logger.error("Orchestrator error: %s", e)
            return None

    def _execute_entry(self, decision) -> None:
        """Execute entry order."""
        if not self.context._order_router:
            logger.warning("OrderRouter not available, skipping entry")
            return

        if not decision.signal:
            logger.warning("No signal in decision, skipping entry")
            return

        try:
            from core.trade.risk_engine import PortfolioSnapshot

            # Create portfolio snapshot for risk validation
            portfolio = PortfolioSnapshot(
                equity_usd=self.context.quote_amount * 10,  # Assume 10x of trade size
                open_positions=len(self.context._open_positions),
                daily_pnl_usd=0.0,  # TODO: track daily PnL
                start_of_day_equity=self.context.quote_amount * 10,
                timestamp_utc=datetime.now(timezone.utc).isoformat(),
                source="entrypoint",
            )

            # Determine side from signal direction
            side = "BUY" if decision.signal.direction.value == "LONG" else "SELL"

            # Execute order
            result = self.context._order_router.execute_order(
                symbol=self.context.symbol,
                side=side,
                size_usd=self.context.quote_amount,
                portfolio=portfolio,
                signal_id=decision.signal.signal_id,
            )

            if result.success:
                logger.info("ORDER EXECUTED: %s %s %.2f USD @ %.2f",
                          side, self.context.symbol, self.context.quote_amount,
                          result.avg_price or 0)

                # Track position
                self._track_new_position(decision, result)

                # Record trade
                self.context.record_trade({
                    "client_order_id": result.client_order_id,
                    "symbol": self.context.symbol,
                    "side": side,
                    "avg_price": result.avg_price,
                    "executed_qty": result.executed_qty,
                })
            else:
                logger.warning("Order failed: %s", result.reason)

        except Exception as e:
            logger.error("Entry execution error: %s", e)

    def _execute_exit(self, decision, market_data) -> None:
        """Execute exit for open positions."""
        if not self.context._order_router:
            return

        if not self.context._open_positions:
            return

        try:
            from core.trade.risk_engine import PortfolioSnapshot

            # Close first matching position
            for pos in self.context._open_positions[:]:
                if hasattr(pos, 'symbol') and pos.symbol == self.context.symbol:
                    # Create portfolio snapshot
                    portfolio = PortfolioSnapshot(
                        equity_usd=self.context.quote_amount * 10,
                        open_positions=len(self.context._open_positions),
                        daily_pnl_usd=0.0,
                        start_of_day_equity=self.context.quote_amount * 10,
                        timestamp_utc=datetime.now(timezone.utc).isoformat(),
                        source="entrypoint",
                    )

                    # Exit is opposite of position side
                    exit_side = "SELL" if getattr(pos, 'side', None) == "LONG" else "BUY"

                    result = self.context._order_router.execute_order(
                        symbol=self.context.symbol,
                        side=exit_side,
                        size_usd=self.context.quote_amount,
                        portfolio=portfolio,
                        signal_id=f"exit_{int(time.time())}",
                    )

                    if result.success:
                        logger.info("POSITION CLOSED: %s @ %.2f",
                                  self.context.symbol, result.avg_price or 0)

                        # Remove from tracking
                        self.context._open_positions.remove(pos)

                        # Record trade
                        self.context.record_trade({
                            "client_order_id": result.client_order_id,
                            "symbol": self.context.symbol,
                            "side": exit_side,
                            "avg_price": result.avg_price,
                            "executed_qty": result.executed_qty,
                            "pnl": self._calc_pnl(pos, result.avg_price),
                        })
                    break

        except Exception as e:
            logger.error("Exit execution error: %s", e)

    def _track_new_position(self, decision, result) -> None:
        """Track new position after entry."""
        try:
            from core.strategy.base import Position, PositionSide

            if decision.signal:
                pos = Position(
                    symbol=self.context.symbol,
                    side=PositionSide.LONG if decision.signal.direction.value == "LONG" else PositionSide.SHORT,
                    entry_price=result.avg_price or 0,
                    size=result.executed_qty or 0,
                    stop_loss=decision.signal.stop_loss,
                    take_profit=decision.signal.take_profit,
                    signal_id=decision.signal.signal_id,
                    entry_time=int(time.time()),
                )
                self.context._open_positions.append(pos)
                logger.debug("Position tracked: %s", pos)

        except Exception as e:
            logger.warning("Failed to track position: %s", e)

    def _calc_pnl(self, position, exit_price: float) -> float:
        """Calculate PnL for position."""
        try:
            entry = getattr(position, 'entry_price', 0)
            size = getattr(position, 'size', 0)
            side = getattr(position, 'side', None)

            if side and side.value == "LONG":
                return (exit_price - entry) * size
            else:
                return (entry - exit_price) * size
        except Exception:
            return 0.0

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

        # Write initial health_v5.json
        write_health_v5(
            mode=args.mode,
            uptime_sec=0,
            open_positions=0,
            queue_size=0,
            daily_pnl_usd=0.0,
            daily_stop_hit=False,
            last_error=None,
        )
        logger.info("health_v5.json created: %s", HEALTH_FILE)

        # Notify systemd we're ready
        if sd_ready():
            logger.info("Systemd READY notification sent")

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
