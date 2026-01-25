# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T16:25:00Z
# Modified by: Claude (opus-4)
# Modified at (UTC): 2026-01-25T17:40:00Z
# Purpose: Trading Order Router - единственная точка размещения ордеров (+delisting protection)
# === END SIGNATURE ===
"""
Trading Order Router - Fail-Closed Order Execution.

ЕДИНСТВЕННАЯ точка размещения ордеров.
Прямые сетевые вызовы ЗАПРЕЩЕНЫ - используем egress wrapper.

Flow (неизменяем):
1. audit.log_intent()
2. risk_engine.validate_order()
3. live_gate.check()
4. submit order (через egress wrapper)
5. audit.log_ack/fill

FAIL-CLOSED:
- Ошибка audit = STOP
- Ошибка risk = REJECT
- Ошибка gate = REJECT
- Ошибка submit = REJECT + audit ERROR

Режимы:
- DRY: только расчёты + audit, без реальных ордеров
- TESTNET: ордера на testnet.binance.vision
- MAINNET: ордера на api.binance.com (требует LIVE Gate)

Usage:
    from core.trade.order_router import TradingOrderRouter

    router = TradingOrderRouter(mode="TESTNET")

    result = router.execute_order(
        symbol="BTCUSDT",
        side="BUY",
        size_usd=100.0,
        portfolio=portfolio_snapshot,
    )

    if not result.success:
        log.error(f"Order failed: {result.reason}")
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("trade.order_router")

# SSoT paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATE_DIR = BASE_DIR / "state"


class TradingMode(str, Enum):
    """Trading mode."""
    DRY = "DRY"
    TESTNET = "TESTNET"
    MAINNET = "MAINNET"


class OrderResultStatus(str, Enum):
    """Order result status."""
    SUCCESS = "SUCCESS"
    REJECTED_AUDIT = "REJECTED_AUDIT"
    REJECTED_RISK = "REJECTED_RISK"
    REJECTED_GATE = "REJECTED_GATE"
    REJECTED_DELISTING = "REJECTED_DELISTING"  # Symbol blocked due to delisting
    REJECTED_EXCHANGE = "REJECTED_EXCHANGE"
    ERROR = "ERROR"


@dataclass
class TradingOrderResult:
    """Result of order execution."""
    success: bool
    status: OrderResultStatus
    reason: str
    order_id: str = ""
    client_order_id: str = ""
    symbol: str = ""
    side: str = ""
    requested_size_usd: float = 0.0
    allowed_size_usd: float = 0.0
    executed_qty: float = 0.0
    avg_price: float = 0.0
    executed_usd: float = 0.0
    mode: str = "DRY"
    dry_run: bool = True
    timestamp_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict."""
        return {
            "success": self.success,
            "status": self.status.value,
            "reason": self.reason,
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side,
            "requested_size_usd": self.requested_size_usd,
            "allowed_size_usd": self.allowed_size_usd,
            "executed_qty": self.executed_qty,
            "avg_price": self.avg_price,
            "executed_usd": self.executed_usd,
            "mode": self.mode,
            "dry_run": self.dry_run,
            "timestamp_utc": self.timestamp_utc,
        }


class TradingOrderRouter:
    """
    Trading Order Router - fail-closed order execution.

    ЗАПРЕЩЕНО: прямые сетевые вызовы.
    Используем существующий клиент проекта.
    """

    # Allowed hosts per mode
    ALLOWED_HOSTS = {
        TradingMode.TESTNET: "testnet.binance.vision",
        TradingMode.MAINNET: "api.binance.com",
    }

    def __init__(
        self,
        mode: str = "DRY",
        dry_run: bool = True,
    ):
        """
        Initialize Trading Order Router.

        Args:
            mode: Trading mode (DRY, TESTNET, MAINNET)
            dry_run: If True, no real orders (overrides mode)
        """
        self.mode = TradingMode(mode.upper())
        self.dry_run = dry_run or self.mode == TradingMode.DRY

        # Import dependencies
        from .live_gate import LiveGate
        from .risk_engine import TradingRiskEngine, OrderIntent, PortfolioSnapshot
        from .order_audit import OrderAudit
        from .delisting_detector import DelistingDetector

        self.live_gate = LiveGate()
        self.risk_engine = TradingRiskEngine()
        self.audit = OrderAudit()
        self.delisting_detector = DelistingDetector()

        # Generate run_id for this session
        self._run_id = self._generate_run_id()
        self._cmdline_sha256 = self._get_cmdline_sha256()

        # Set audit context
        self.audit.set_context(
            run_id=self._run_id,
            cmdline_sha256=self._cmdline_sha256,
            mode=self.mode.value,
            dry_run=self.dry_run,
        )

        # Exchange client (lazy init)
        self._exchange_client = None

        logger.info(
            "TradingOrderRouter initialized: mode=%s, dry_run=%s, run_id=%s",
            self.mode.value,
            self.dry_run,
            self._run_id[:40],
        )

    def _generate_run_id(self) -> str:
        """Generate unique run ID."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        pid = os.getpid()
        nonce = hashlib.sha256(f"{ts}{pid}{time.time_ns()}".encode()).hexdigest()[:32]
        cmd_prefix = self._get_cmdline_sha256()[:8]
        return f"trade_v1__ts={ts}__pid={pid}__nonce={nonce}__cmd={cmd_prefix}"

    def _get_cmdline_sha256(self) -> str:
        """Get cmdline SHA256 (SSoT)."""
        try:
            from core.truth.cmdline_ssot import get_cmdline_sha256
            return get_cmdline_sha256()
        except ImportError:
            # Fallback
            import sys
            cmdline = " ".join(sys.argv)
            return hashlib.sha256(cmdline.encode()).hexdigest()

    def _generate_client_order_id(self, symbol: str, side: str) -> str:
        """Generate deterministic client order ID."""
        ts = int(time.time() * 1000)
        data = f"{self._run_id}{symbol}{side}{ts}"
        nonce = hashlib.sha256(data.encode()).hexdigest()[:8]
        return f"HOPE_{symbol}_{ts}_{nonce}"

    def _get_exchange_client(self):
        """Get or create exchange client."""
        if self._exchange_client is not None:
            return self._exchange_client

        if self.mode == TradingMode.TESTNET:
            from core.spot_testnet_client import SpotTestnetClient
            self._exchange_client = SpotTestnetClient()
        elif self.mode == TradingMode.MAINNET:
            # For MAINNET, we need a mainnet client
            # This should use egress wrapper
            from core.spot_testnet_client import SpotTestnetClient

            # Load mainnet credentials
            secrets_path = Path(r"C:\secrets\hope\.env")
            env = {}
            if secrets_path.exists():
                for line in secrets_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        env[k.strip()] = v.strip()

            # Create client with mainnet URL
            self._exchange_client = SpotTestnetClient(
                api_key=env.get("BINANCE_MAINNET_API_KEY", ""),
                api_secret=env.get("BINANCE_MAINNET_API_SECRET", ""),
            )
            # Override base URL for mainnet
            self._exchange_client.base_url = "https://api.binance.com/api"

        return self._exchange_client

    def execute_order(
        self,
        symbol: str,
        side: str,
        size_usd: float,
        portfolio: "PortfolioSnapshot",
        order_type: str = "MARKET",
        signal_id: Optional[str] = None,
    ) -> TradingOrderResult:
        """
        Execute order with full validation.

        FLOW (неизменяем):
        1. audit.log_intent
        2. risk_engine.validate_order
        3. live_gate.check
        4. submit order
        5. audit.log_ack/fill

        Args:
            symbol: Trading pair
            side: BUY or SELL
            size_usd: Order size in USD
            portfolio: Current portfolio snapshot
            order_type: MARKET or LIMIT
            signal_id: Link to originating signal

        Returns:
            TradingOrderResult
        """
        from .risk_engine import OrderIntent

        client_order_id = self._generate_client_order_id(symbol, side)

        # === STEP 1: Audit INTENT (FAIL-CLOSED) ===
        if not self.audit.log_intent(symbol, side, size_usd, signal_id):
            return TradingOrderResult(
                success=False,
                status=OrderResultStatus.REJECTED_AUDIT,
                reason="STOP: Audit write failed",
                symbol=symbol,
                side=side,
                requested_size_usd=size_usd,
                mode=self.mode.value,
                dry_run=self.dry_run,
            )

        # === STEP 1.5: Delisting Check (FAIL-CLOSED) ===
        if self.delisting_detector.is_symbol_blocked(symbol):
            reason = self.delisting_detector.get_blocked_reason(symbol) or "Symbol blocked"
            self.audit.log_reject(
                symbol=symbol,
                side=side,
                reason_code="DELISTING_BLOCKED",
                reason=reason,
                size_usd=size_usd,
            )
            return TradingOrderResult(
                success=False,
                status=OrderResultStatus.REJECTED_DELISTING,
                reason=f"DELISTING PROTECTION: {reason}",
                symbol=symbol,
                side=side,
                requested_size_usd=size_usd,
                mode=self.mode.value,
                dry_run=self.dry_run,
            )

        # === STEP 2: Risk Engine Validation ===
        intent = OrderIntent(
            symbol=symbol,
            side=side,
            size_usd=size_usd,
            order_type=order_type,
            signal_id=signal_id,
        )

        risk_result = self.risk_engine.validate_order(intent, portfolio)

        self.audit.log_risk_check(
            symbol=symbol,
            side=side,
            decision="ALLOWED" if risk_result.allowed else "REJECTED",
            reason_code=risk_result.reason_code.value,
            allowed_size=risk_result.allowed_size_usd,
        )

        if not risk_result.allowed:
            self.audit.log_reject(
                symbol=symbol,
                side=side,
                reason_code=risk_result.reason_code.value,
                reason=risk_result.reason,
                size_usd=size_usd,
            )
            return TradingOrderResult(
                success=False,
                status=OrderResultStatus.REJECTED_RISK,
                reason=f"Risk blocked: {risk_result.reason}",
                symbol=symbol,
                side=side,
                requested_size_usd=size_usd,
                mode=self.mode.value,
                dry_run=self.dry_run,
            )

        allowed_size = risk_result.allowed_size_usd

        # === STEP 3: LIVE Gate Check ===
        target_host = self.ALLOWED_HOSTS.get(self.mode, "")

        gate_result = self.live_gate.check(
            mode=self.mode.value,
            target_host=target_host,
            skip_evidence=(self.mode == TradingMode.DRY),
        )

        if not gate_result.allowed:
            self.audit.log_reject(
                symbol=symbol,
                side=side,
                reason_code=gate_result.decision.value,
                reason=gate_result.reason,
                size_usd=allowed_size,
            )
            return TradingOrderResult(
                success=False,
                status=OrderResultStatus.REJECTED_GATE,
                reason=f"Gate blocked: {gate_result.reason}",
                symbol=symbol,
                side=side,
                requested_size_usd=size_usd,
                allowed_size_usd=allowed_size,
                mode=self.mode.value,
                dry_run=self.dry_run,
            )

        # === STEP 4: Execute Order ===
        if self.dry_run:
            # DRY mode: simulate
            self.risk_engine.record_order()

            result = TradingOrderResult(
                success=True,
                status=OrderResultStatus.SUCCESS,
                reason="DRY: Order simulated",
                order_id=f"DRY_{int(time.time() * 1000)}",
                client_order_id=client_order_id,
                symbol=symbol,
                side=side,
                requested_size_usd=size_usd,
                allowed_size_usd=allowed_size,
                executed_qty=0.0,
                avg_price=0.0,
                executed_usd=0.0,
                mode=self.mode.value,
                dry_run=True,
            )

            self.audit.log_submit(symbol, side, client_order_id, allowed_size)

            logger.info(
                "[DRY] Order simulated: %s %s %.2f USD",
                side, symbol, allowed_size,
            )

            return result

        # Real order
        try:
            self.audit.log_submit(symbol, side, client_order_id, allowed_size)

            client = self._get_exchange_client()
            if client is None:
                self.audit.log_error(symbol, side, "Exchange client not available", client_order_id)
                return TradingOrderResult(
                    success=False,
                    status=OrderResultStatus.ERROR,
                    reason="Exchange client not available",
                    client_order_id=client_order_id,
                    symbol=symbol,
                    side=side,
                    requested_size_usd=size_usd,
                    allowed_size_usd=allowed_size,
                    mode=self.mode.value,
                    dry_run=self.dry_run,
                )

            # Get price
            tickers = client.get_ticker_price(symbol)
            if not tickers:
                self.audit.log_error(symbol, side, "Price not available", client_order_id)
                return TradingOrderResult(
                    success=False,
                    status=OrderResultStatus.ERROR,
                    reason="Price not available",
                    client_order_id=client_order_id,
                    symbol=symbol,
                    side=side,
                    requested_size_usd=size_usd,
                    allowed_size_usd=allowed_size,
                    mode=self.mode.value,
                    dry_run=self.dry_run,
                )

            price = float(tickers[0]["price"])
            qty = allowed_size / price

            # Get symbol info for lot size
            info = client.get_exchange_info(symbol)
            if info and "symbols" in info:
                for s in info["symbols"]:
                    if s["symbol"] == symbol:
                        for f in s.get("filters", []):
                            if f["filterType"] == "LOT_SIZE":
                                step = float(f.get("stepSize", 0.00000001))
                                min_qty = float(f.get("minQty", 0))
                                if qty < min_qty:
                                    self.audit.log_error(symbol, side, f"Qty {qty} < min {min_qty}", client_order_id)
                                    return TradingOrderResult(
                                        success=False,
                                        status=OrderResultStatus.REJECTED_EXCHANGE,
                                        reason=f"Qty below minimum: {qty} < {min_qty}",
                                        client_order_id=client_order_id,
                                        symbol=symbol,
                                        side=side,
                                        requested_size_usd=size_usd,
                                        allowed_size_usd=allowed_size,
                                        mode=self.mode.value,
                                        dry_run=self.dry_run,
                                    )
                                # Round to step size
                                qty = qty - (qty % step)
                                break
                        break

            # Place order
            order_result = client.place_market_order(
                symbol=symbol,
                side=side,
                quantity=qty,
                client_order_id=client_order_id,
            )

            if order_result.success:
                self.risk_engine.record_order()

                self.audit.log_ack(
                    symbol=symbol,
                    side=side,
                    client_order_id=client_order_id,
                    order_id=order_result.order_id or "",
                    status=order_result.status,
                )

                if order_result.qty > 0:
                    self.audit.log_fill(
                        symbol=symbol,
                        side=side,
                        order_id=order_result.order_id or "",
                        qty=order_result.qty,
                        price=order_result.price,
                    )

                logger.info(
                    "Order executed: %s %s %.8f @ %.2f = %.2f USD (order_id=%s)",
                    side, symbol, order_result.qty, order_result.price,
                    order_result.qty * order_result.price, order_result.order_id,
                )

                return TradingOrderResult(
                    success=True,
                    status=OrderResultStatus.SUCCESS,
                    reason="Order executed",
                    order_id=order_result.order_id or "",
                    client_order_id=client_order_id,
                    symbol=symbol,
                    side=side,
                    requested_size_usd=size_usd,
                    allowed_size_usd=allowed_size,
                    executed_qty=order_result.qty,
                    avg_price=order_result.price,
                    executed_usd=order_result.qty * order_result.price,
                    mode=self.mode.value,
                    dry_run=False,
                )
            else:
                self.audit.log_error(symbol, side, order_result.error or "Unknown", client_order_id)
                return TradingOrderResult(
                    success=False,
                    status=OrderResultStatus.REJECTED_EXCHANGE,
                    reason=f"Exchange rejected: {order_result.error}",
                    client_order_id=client_order_id,
                    symbol=symbol,
                    side=side,
                    requested_size_usd=size_usd,
                    allowed_size_usd=allowed_size,
                    mode=self.mode.value,
                    dry_run=self.dry_run,
                )

        except Exception as e:
            logger.error("Order execution error: %s", e)
            self.audit.log_error(symbol, side, str(e), client_order_id)
            return TradingOrderResult(
                success=False,
                status=OrderResultStatus.ERROR,
                reason=f"Execution error: {e}",
                client_order_id=client_order_id,
                symbol=symbol,
                side=side,
                requested_size_usd=size_usd,
                allowed_size_usd=allowed_size,
                mode=self.mode.value,
                dry_run=self.dry_run,
            )

    def get_status(self) -> Dict[str, Any]:
        """Get router status."""
        return {
            "mode": self.mode.value,
            "dry_run": self.dry_run,
            "run_id": self._run_id,
            "live_gate": self.live_gate.get_status(),
            "risk_engine": self.risk_engine.get_status(),
        }


# === CLI Interface ===
def main() -> int:
    """CLI entrypoint."""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if len(sys.argv) < 2:
        print("Usage: python -m core.trade.order_router <command>")
        print("Commands:")
        print("  status                    - Show router status")
        print("  test-dry <symbol> <size>  - Test DRY order")
        return 1

    command = sys.argv[1]

    if command == "status":
        router = TradingOrderRouter(mode="DRY", dry_run=True)
        status = router.get_status()
        print(json.dumps(status, indent=2))
        return 0

    elif command == "test-dry":
        from .risk_engine import PortfolioSnapshot

        symbol = sys.argv[2] if len(sys.argv) > 2 else "BTCUSDT"
        size = float(sys.argv[3]) if len(sys.argv) > 3 else 100.0

        router = TradingOrderRouter(mode="DRY", dry_run=True)

        portfolio = PortfolioSnapshot(
            equity_usd=1000.0,
            open_positions=0,
            daily_pnl_usd=0.0,
            start_of_day_equity=1000.0,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            source="test",
        )

        result = router.execute_order(
            symbol=symbol,
            side="BUY",
            size_usd=size,
            portfolio=portfolio,
        )

        print(json.dumps(result.to_dict(), indent=2))
        return 0 if result.success else 1

    else:
        print(f"Unknown command: {command}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
