# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T07:00:00Z
# Purpose: Trading Order Router v2 - integrated with Trading Safety Core
# Security: Outbox pattern, UNKNOWN protocol, Fill ledger as SSoT
# === END SIGNATURE ===
"""
Trading Order Router v2 - Fail-Closed Order Execution.

INTEGRATED WITH TRADING SAFETY CORE:
- Outbox pattern (two-phase commit)
- UNKNOWN protocol (read-your-writes on timeout)
- FillsLedger as ONLY source of truth

Flow:
1. Generate idempotent clientOrderId
2. Check duplicate via Outbox.has_pending()
3. PREPARE: Write intent to Outbox
4. COMMIT: Send to exchange
5. ACK/UNKNOWN: Handle response or timeout
6. Record FillEvent to FillsLedger

FAIL-CLOSED:
- Any error before COMMIT = REJECTED (no side effects)
- Timeout/5xx = UNKNOWN (quarantine, reconcile)
- Only FillEvent records actual execution
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Protocol

logger = logging.getLogger("trade.order_router_v2")

# SSoT paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATE_DIR = BASE_DIR / "state"
ORDERS_DIR = STATE_DIR / "orders"
FILLS_DIR = STATE_DIR / "fills"


class ExecutionStatus(str, Enum):
    """Order execution result status."""
    SUCCESS = "SUCCESS"
    DUPLICATE = "DUPLICATE"
    RISK_BLOCKED = "RISK_BLOCKED"
    GATE_BLOCKED = "GATE_BLOCKED"
    EXCHANGE_REJECTED = "EXCHANGE_REJECTED"
    UNKNOWN = "UNKNOWN"  # Timeout/5xx - needs reconcile
    ERROR = "ERROR"


@dataclass
class ExecutionResult:
    """Result of order execution."""
    status: ExecutionStatus
    client_order_id: str = ""
    exchange_order_id: Optional[int] = None
    symbol: str = ""
    side: str = ""
    quantity: float = 0.0
    price: float = 0.0
    notional: float = 0.0
    message: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def success(self) -> bool:
        return self.status == ExecutionStatus.SUCCESS

    @property
    def needs_reconcile(self) -> bool:
        return self.status == ExecutionStatus.UNKNOWN


class ExchangeClientProtocol(Protocol):
    """Protocol for exchange client."""

    def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        client_order_id: str,
        price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Create order on exchange."""
        ...

    def get_order(
        self,
        symbol: str,
        client_order_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get order by clientOrderId."""
        ...

    def get_ticker_price(self, symbol: str) -> Optional[float]:
        """Get current price."""
        ...


class TradingOrderRouterV2:
    """
    Trading Order Router v2 - integrated with Trading Safety Core.

    Uses:
    - core/execution/outbox.py for order lifecycle
    - core/execution/fills_ledger.py for execution records
    - core/execution/idempotency.py for clientOrderId
    - core/execution/reconcile.py for UNKNOWN handling
    """

    def __init__(
        self,
        mode: str = "DRY",
        dry_run: bool = True,
    ):
        """
        Initialize Trading Order Router v2.

        Args:
            mode: Trading mode (DRY, TESTNET, MAINNET)
            dry_run: If True, no real orders
        """
        self.mode = mode.upper()
        self.dry_run = dry_run or self.mode == "DRY"

        # Initialize execution layer
        from core.execution.outbox import Outbox
        from core.execution.fills_ledger import FillsLedger
        from core.execution.idempotency import cmdline_sha256_id

        ORDERS_DIR.mkdir(parents=True, exist_ok=True)
        FILLS_DIR.mkdir(parents=True, exist_ok=True)

        self._outbox = Outbox(ORDERS_DIR / "outbox.jsonl")
        self._ledger = FillsLedger(FILLS_DIR / "fills.jsonl")
        self._session_id = cmdline_sha256_id()

        # Exchange client (lazy init)
        self._exchange_client = None

        # Import gates
        from .live_gate import LiveGate
        from .risk_engine import TradingRiskEngine

        self._live_gate = LiveGate()
        self._risk_engine = TradingRiskEngine()

        logger.info(
            "TradingOrderRouterV2 initialized: mode=%s, dry_run=%s, session=%s",
            self.mode, self.dry_run, self._session_id[:32],
        )

    def _get_exchange_client(self):
        """Get or create exchange client."""
        if self._exchange_client is not None:
            return self._exchange_client

        if self.mode == "TESTNET":
            from core.spot_testnet_client import SpotTestnetClient
            self._exchange_client = SpotTestnetClient()
        elif self.mode == "MAINNET":
            from core.spot_testnet_client import SpotTestnetClient

            # Load mainnet credentials
            secrets_path = Path(r"C:\secrets\hope.env")
            env = {}
            if secrets_path.exists():
                for line in secrets_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        env[k.strip()] = v.strip()

            self._exchange_client = SpotTestnetClient(
                api_key=env.get("BINANCE_API_KEY", ""),
                api_secret=env.get("BINANCE_API_SECRET", ""),
            )
            self._exchange_client.base_url = "https://api.binance.com/api"

        return self._exchange_client

    def execute_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: Optional[float] = None,
        order_type: str = "MARKET",
        signal_id: Optional[str] = None,
    ) -> ExecutionResult:
        """
        Execute order with Outbox pattern.

        Flow:
        1. Generate idempotent clientOrderId
        2. Check duplicate
        3. PREPARE (write to outbox)
        4. Validate (risk, gate)
        5. COMMIT (send to exchange)
        6. ACK/UNKNOWN
        7. Record fill if executed

        Args:
            symbol: Trading pair
            side: BUY or SELL
            quantity: Order quantity
            price: Limit price (None for MARKET)
            order_type: MARKET or LIMIT
            signal_id: Link to originating signal

        Returns:
            ExecutionResult
        """
        from core.execution.idempotency import generate_client_order_id
        from core.execution.contracts import (
            OrderIntentV1, OrderAckV1, FillEventV1, OrderStatus
        )

        # === STEP 1: Generate idempotent clientOrderId ===
        client_order_id = generate_client_order_id(
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            session_id=self._session_id,
            nonce=signal_id or "",
        )

        # === STEP 2: Check duplicate ===
        existing = self._outbox.get(client_order_id)
        if existing is not None:
            logger.warning("Duplicate order detected: %s", client_order_id)
            return ExecutionResult(
                status=ExecutionStatus.DUPLICATE,
                client_order_id=client_order_id,
                symbol=symbol,
                side=side,
                message=f"Duplicate: already {existing.status.value}",
            )

        # === STEP 3: PREPARE (write to outbox) ===
        intent = OrderIntentV1(
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            session_id=self._session_id,
            metadata={"signal_id": signal_id} if signal_id else {},
        )

        try:
            self._outbox.prepare(intent)
        except ValueError as e:
            # Duplicate detected during prepare
            return ExecutionResult(
                status=ExecutionStatus.DUPLICATE,
                client_order_id=client_order_id,
                symbol=symbol,
                side=side,
                message=str(e),
            )

        # === STEP 4: Validate (risk, gate) ===
        # Risk check
        from .risk_engine import OrderIntent as RiskOrderIntent, PortfolioSnapshot

        # Get current price for notional calculation
        current_price = price
        if current_price is None and not self.dry_run:
            client = self._get_exchange_client()
            if client:
                try:
                    ticker = client.get_ticker_price(symbol)
                    if ticker:
                        current_price = float(ticker[0]["price"]) if isinstance(ticker, list) else float(ticker.get("price", 0))
                except Exception as e:
                    logger.warning("Failed to get price: %s", e)

        # In DRY mode, use default prices if none provided
        if current_price is None or current_price == 0.0:
            if self.dry_run:
                # Default prices for DRY mode simulation
                dry_mode_prices = {
                    "BTCUSDT": 90000.0,
                    "ETHUSDT": 3000.0,
                    "BNBUSDT": 600.0,
                }
                current_price = dry_mode_prices.get(symbol, 100.0)
            else:
                current_price = 0.0

        notional = quantity * current_price

        # Calculate actual open positions from fills (not fill count!)
        open_positions_count = 0
        try:
            from core.trade.state_hydration import StateHydrator
            hydrator = StateHydrator()
            positions = hydrator.get_all_positions()
            open_positions_count = len(positions)
        except Exception as e:
            logger.warning("Failed to hydrate positions for risk check: %s", e)

        # Minimal portfolio for validation (should be replaced with real data)
        portfolio = PortfolioSnapshot(
            equity_usd=10000.0,  # TODO: Get real balance
            open_positions=open_positions_count,
            daily_pnl_usd=0.0,
            start_of_day_equity=10000.0,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            source="order_router_v2",
        )

        risk_intent = RiskOrderIntent(
            symbol=symbol,
            side=side,
            size_usd=notional,
            order_type=order_type,
            signal_id=signal_id,
        )

        risk_result = self._risk_engine.validate_order(risk_intent, portfolio)
        if not risk_result.allowed:
            self._outbox.unknown(client_order_id, f"RISK: {risk_result.reason}")
            return ExecutionResult(
                status=ExecutionStatus.RISK_BLOCKED,
                client_order_id=client_order_id,
                symbol=symbol,
                side=side,
                message=risk_result.reason,
            )

        # Gate check
        gate_result = self._live_gate.check(
            mode=self.mode,
            target_host="api.binance.com" if self.mode == "MAINNET" else "testnet.binance.vision",
            skip_evidence=(self.mode == "DRY"),
        )
        if not gate_result.allowed:
            self._outbox.unknown(client_order_id, f"GATE: {gate_result.reason}")
            return ExecutionResult(
                status=ExecutionStatus.GATE_BLOCKED,
                client_order_id=client_order_id,
                symbol=symbol,
                side=side,
                message=gate_result.reason,
            )

        # === STEP 5: COMMIT (mark as sent, execute) ===
        self._outbox.commit(client_order_id)

        if self.dry_run:
            # DRY mode: simulate success
            ack = OrderAckV1(
                client_order_id=client_order_id,
                exchange_order_id=int(time.time() * 1000),
                status=OrderStatus.FILLED,
                filled_qty=quantity,
                avg_price=current_price,
            )
            self._outbox.ack(client_order_id, ack)

            logger.info(
                "[DRY] Order simulated: %s %s %.8f @ %.2f",
                side, symbol, quantity, current_price,
            )

            return ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                client_order_id=client_order_id,
                exchange_order_id=ack.exchange_order_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=current_price,
                notional=notional,
                message="DRY: Order simulated",
            )

        # Real order
        try:
            client = self._get_exchange_client()
            if client is None:
                self._outbox.unknown(client_order_id, "Exchange client not available")
                return ExecutionResult(
                    status=ExecutionStatus.ERROR,
                    client_order_id=client_order_id,
                    symbol=symbol,
                    side=side,
                    message="Exchange client not available",
                )

            # Place order
            order_result = client.place_market_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                client_order_id=client_order_id,
            )

            if order_result.success:
                # === STEP 6: ACK ===
                ack = OrderAckV1(
                    client_order_id=client_order_id,
                    exchange_order_id=int(order_result.order_id) if order_result.order_id else None,
                    status=OrderStatus.FILLED if order_result.qty > 0 else OrderStatus.NEW,
                    filled_qty=order_result.qty,
                    avg_price=order_result.price,
                )
                self._outbox.ack(client_order_id, ack)

                # === STEP 7: Record fill ===
                if order_result.qty > 0:
                    fill = FillEventV1(
                        fill_id=int(time.time() * 1000000),  # Should come from exchange
                        client_order_id=client_order_id,
                        exchange_order_id=int(order_result.order_id) if order_result.order_id else 0,
                        symbol=symbol,
                        side=side,
                        price=order_result.price,
                        quantity=order_result.qty,
                        trade_time=int(time.time() * 1000),
                    )
                    self._ledger.record(fill)

                logger.info(
                    "Order executed: %s %s %.8f @ %.2f (id=%s)",
                    side, symbol, order_result.qty, order_result.price, order_result.order_id,
                )

                return ExecutionResult(
                    status=ExecutionStatus.SUCCESS,
                    client_order_id=client_order_id,
                    exchange_order_id=int(order_result.order_id) if order_result.order_id else None,
                    symbol=symbol,
                    side=side,
                    quantity=order_result.qty,
                    price=order_result.price,
                    notional=order_result.qty * order_result.price,
                    message="Order executed",
                )
            else:
                # Exchange rejected
                ack = OrderAckV1(
                    client_order_id=client_order_id,
                    status=OrderStatus.REJECTED,
                    error_msg=order_result.error,
                )
                self._outbox.ack(client_order_id, ack)

                return ExecutionResult(
                    status=ExecutionStatus.EXCHANGE_REJECTED,
                    client_order_id=client_order_id,
                    symbol=symbol,
                    side=side,
                    message=order_result.error or "Exchange rejected",
                )

        except TimeoutError:
            # === UNKNOWN PROTOCOL ===
            self._outbox.unknown(client_order_id, "Network timeout")
            logger.error("UNKNOWN: Timeout for %s - needs reconcile", client_order_id)

            return ExecutionResult(
                status=ExecutionStatus.UNKNOWN,
                client_order_id=client_order_id,
                symbol=symbol,
                side=side,
                message="TIMEOUT: Needs reconcile before retry",
            )

        except Exception as e:
            # Network error - treat as UNKNOWN
            error_msg = str(e)
            if "timeout" in error_msg.lower() or "connection" in error_msg.lower():
                self._outbox.unknown(client_order_id, error_msg)
                return ExecutionResult(
                    status=ExecutionStatus.UNKNOWN,
                    client_order_id=client_order_id,
                    symbol=symbol,
                    side=side,
                    message=f"UNKNOWN: {error_msg}",
                )

            # Other errors
            logger.error("Order error: %s", e)
            self._outbox.unknown(client_order_id, error_msg)
            return ExecutionResult(
                status=ExecutionStatus.ERROR,
                client_order_id=client_order_id,
                symbol=symbol,
                side=side,
                message=error_msg,
            )

    def reconcile_unknown(self, client_order_id: str, symbol: str) -> ExecutionResult:
        """
        Reconcile UNKNOWN order via read-your-writes.

        Queries exchange for order status and updates outbox.

        Args:
            client_order_id: Order to reconcile
            symbol: Trading pair

        Returns:
            ExecutionResult with actual status
        """
        from core.execution.contracts import OrderAckV1, FillEventV1, OrderStatus

        entry = self._outbox.get(client_order_id)
        if entry is None:
            return ExecutionResult(
                status=ExecutionStatus.ERROR,
                client_order_id=client_order_id,
                message="Order not in outbox",
            )

        client = self._get_exchange_client()
        if client is None:
            return ExecutionResult(
                status=ExecutionStatus.ERROR,
                client_order_id=client_order_id,
                message="Exchange client not available for reconcile",
            )

        try:
            # Query order by clientOrderId
            order_data = client.get_order(symbol=symbol, orig_client_order_id=client_order_id)

            if order_data is None:
                # Order never reached exchange - can retry
                logger.info("Reconcile: Order %s not found on exchange - safe to retry", client_order_id)
                return ExecutionResult(
                    status=ExecutionStatus.ERROR,
                    client_order_id=client_order_id,
                    message="Order not found on exchange - can retry with new ID",
                )

            # Order exists
            status_str = order_data.get("status", "UNKNOWN")
            try:
                status = OrderStatus(status_str)
            except ValueError:
                status = OrderStatus.UNKNOWN

            ack = OrderAckV1(
                client_order_id=client_order_id,
                exchange_order_id=order_data.get("orderId"),
                status=status,
                filled_qty=float(order_data.get("executedQty", 0)),
                avg_price=float(order_data.get("avgPrice", 0) or order_data.get("price", 0)),
                raw_response=order_data,
            )

            self._outbox.reconciled(client_order_id, ack)

            # Record fill if executed
            if status == OrderStatus.FILLED and ack.filled_qty > 0:
                fill = FillEventV1(
                    fill_id=int(order_data.get("orderId", time.time() * 1000000)),
                    client_order_id=client_order_id,
                    exchange_order_id=order_data.get("orderId", 0),
                    symbol=symbol,
                    side=entry.intent.get("side", "BUY"),
                    price=ack.avg_price,
                    quantity=ack.filled_qty,
                    trade_time=order_data.get("updateTime", int(time.time() * 1000)),
                )
                self._ledger.record(fill)

            logger.info(
                "Reconciled: %s status=%s filled=%.8f",
                client_order_id, status.value, ack.filled_qty,
            )

            return ExecutionResult(
                status=ExecutionStatus.SUCCESS if status == OrderStatus.FILLED else ExecutionStatus.ERROR,
                client_order_id=client_order_id,
                exchange_order_id=ack.exchange_order_id,
                symbol=symbol,
                side=entry.intent.get("side", ""),
                quantity=ack.filled_qty,
                price=ack.avg_price,
                message=f"Reconciled: {status.value}",
            )

        except Exception as e:
            logger.error("Reconcile failed: %s", e)
            return ExecutionResult(
                status=ExecutionStatus.UNKNOWN,
                client_order_id=client_order_id,
                message=f"Reconcile error: {e}",
            )

    def get_pending_unknown(self) -> list:
        """Get all UNKNOWN orders needing reconcile."""
        return self._outbox.get_unknown()

    def has_pending_unknown(self) -> bool:
        """Check if there are UNKNOWN orders."""
        return len(self._outbox.get_unknown()) > 0

    def get_fills_for_symbol(self, symbol: str) -> list:
        """Get all fills for a symbol from ledger."""
        return self._ledger.get_fills_for_symbol(symbol)

    def get_status(self) -> Dict[str, Any]:
        """Get router status."""
        return {
            "mode": self.mode,
            "dry_run": self.dry_run,
            "session_id": self._session_id[:32],
            "pending_unknown": len(self._outbox.get_unknown()),
            "total_fills": self._ledger.fill_count(),
        }
