# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T22:50:00Z
# Purpose: Micro Trade Executor v1.0 - $10 spot trade state machine (fail-closed)
# === END SIGNATURE ===
"""
Micro Trade Executor v1.0.

State machine for executing $10 USDT spot trades with:
- Market buy → Take-profit limit sell → Timeout market sell

States:
- INIT: Initial state, validate config and balance
- BUY_PENDING: Market buy order submitted
- BUY_FILLED: Buy executed, preparing TP order
- TP_SELL_PLACED: Take-profit limit order active
- TP_FILLED: Take-profit executed (success)
- TIMEOUT_CANCEL: Timeout reached, canceling TP
- TIMEOUT_SELL: Executing market sell (exit)
- DONE_PROFIT: Completed with profit (TP hit)
- DONE_TIMEOUT_EXIT: Completed with timeout exit
- FAILED: Unrecoverable error

Features:
- Idempotent on restart (resumes from persisted state)
- Append-only audit trail
- Fail-closed on all errors
- DRY → TESTNET → MAINNET progression

State file: state/trade/micro_trade_{job_id}.json
Audit file: state/trade/micro_trade_audit.jsonl
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, Any, Optional, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class TradeState(Enum):
    """Trade state machine states."""
    INIT = "INIT"
    BUY_PENDING = "BUY_PENDING"
    BUY_FILLED = "BUY_FILLED"
    TP_SELL_PLACED = "TP_SELL_PLACED"
    TP_FILLED = "TP_FILLED"
    TIMEOUT_CANCEL = "TIMEOUT_CANCEL"
    TIMEOUT_SELL = "TIMEOUT_SELL"
    DONE_PROFIT = "DONE_PROFIT"
    DONE_TIMEOUT_EXIT = "DONE_TIMEOUT_EXIT"
    FAILED = "FAILED"


class TradeMode(Enum):
    """Trade execution mode."""
    DRY = "DRY"
    TESTNET = "TESTNET"
    MAINNET = "MAINNET"


@dataclass
class TradeConfig:
    """Trade configuration."""
    symbol: str  # e.g., "BTCUSDT"
    quote_amount: float  # e.g., 10.0 USDT
    tp_percent: float  # Take-profit percentage (e.g., 0.5 for 0.5%)
    timeout_minutes: int  # Timeout before market exit (e.g., 30)
    mode: TradeMode
    job_id: Optional[str] = None

    def __post_init__(self):
        if self.job_id is None:
            # Generate deterministic job_id
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            self.job_id = f"micro_{self.symbol}_{ts}"


@dataclass
class TradeStateData:
    """Persisted trade state."""
    job_id: str
    config: Dict[str, Any]
    state: str  # TradeState value
    created_at: str
    updated_at: str

    # Order tracking
    buy_order_id: Optional[int] = None
    buy_price: Optional[float] = None
    buy_qty: Optional[float] = None
    buy_filled_at: Optional[str] = None

    tp_order_id: Optional[int] = None
    tp_price: Optional[float] = None
    tp_filled_at: Optional[str] = None

    exit_order_id: Optional[int] = None
    exit_price: Optional[float] = None
    exit_filled_at: Optional[str] = None

    # Outcome
    pnl_usdt: Optional[float] = None
    outcome: Optional[str] = None  # "profit", "timeout_exit", "failed"
    error: Optional[str] = None

    # Metadata
    transitions: List[Dict[str, Any]] = field(default_factory=list)


class MicroTradeExecutor:
    """
    Micro Trade State Machine.

    Executes small spot trades with state persistence.
    """

    STATE_DIR = PROJECT_ROOT / "state" / "trade"
    AUDIT_FILE = STATE_DIR / "micro_trade_audit.jsonl"

    def __init__(
        self,
        config: TradeConfig,
        client=None,  # BinanceSpotClient or None for DRY
    ):
        """
        Initialize executor.

        Args:
            config: Trade configuration
            client: Binance client (None for DRY mode)
        """
        self.config = config
        self.client = client
        self.state_file = self.STATE_DIR / f"micro_trade_{config.job_id}.json"

        # Ensure directories exist
        self.STATE_DIR.mkdir(parents=True, exist_ok=True)

        # Load or create state
        self.state_data = self._load_or_create_state()

    def _load_or_create_state(self) -> TradeStateData:
        """Load existing state or create new."""
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                return TradeStateData(**data)
            except Exception as e:
                raise ValueError(f"FAIL-CLOSED: Cannot load state file: {e}")

        # Create new state
        now = datetime.now(timezone.utc).isoformat()
        # Convert config to dict with enum as string
        config_dict = asdict(self.config)
        config_dict["mode"] = self.config.mode.value  # Convert enum to string
        return TradeStateData(
            job_id=self.config.job_id,
            config=config_dict,
            state=TradeState.INIT.value,
            created_at=now,
            updated_at=now,
            transitions=[],
        )

    def _save_state(self) -> None:
        """Save state atomically."""
        self.state_data.updated_at = datetime.now(timezone.utc).isoformat()

        tmp_path = self.state_file.with_suffix(".json.tmp")
        try:
            content = json.dumps(asdict(self.state_data), indent=2, ensure_ascii=False)
            with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.state_file)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise

    def _audit(self, event: str, data: Dict[str, Any]) -> None:
        """Append to audit trail."""
        record = {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "job_id": self.config.job_id,
            "event": event,
            "state": self.state_data.state,
            "mode": self.config.mode.value,
            **data,
        }

        # Atomic append
        tmp_path = self.AUDIT_FILE.with_suffix(".jsonl.tmp")
        try:
            line = json.dumps(record, ensure_ascii=False) + "\n"

            # Read existing content
            existing = ""
            if self.AUDIT_FILE.exists():
                existing = self.AUDIT_FILE.read_text(encoding="utf-8")

            # Write atomically
            with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(existing)
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.AUDIT_FILE)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            # Don't fail trade for audit failure
            pass

    def _transition(self, new_state: TradeState, reason: str = "") -> None:
        """Transition to new state."""
        old_state = self.state_data.state
        self.state_data.state = new_state.value

        transition = {
            "from": old_state,
            "to": new_state.value,
            "at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
        }
        self.state_data.transitions.append(transition)

        self._save_state()
        self._audit("state_transition", transition)

    def get_current_state(self) -> TradeState:
        """Get current state."""
        return TradeState(self.state_data.state)

    def run(self) -> TradeStateData:
        """
        Execute state machine until terminal state.

        Returns:
            Final state data
        """
        max_iterations = 100  # Safety limit

        for _ in range(max_iterations):
            state = self.get_current_state()

            if state in (TradeState.DONE_PROFIT, TradeState.DONE_TIMEOUT_EXIT, TradeState.FAILED):
                return self.state_data

            try:
                self._execute_step()
            except Exception as e:
                self._handle_error(str(e))
                return self.state_data

        self._handle_error("Max iterations exceeded")
        return self.state_data

    def _execute_step(self) -> None:
        """Execute one state machine step."""
        state = self.get_current_state()

        if state == TradeState.INIT:
            self._step_init()
        elif state == TradeState.BUY_PENDING:
            self._step_buy_pending()
        elif state == TradeState.BUY_FILLED:
            self._step_buy_filled()
        elif state == TradeState.TP_SELL_PLACED:
            self._step_tp_placed()
        elif state == TradeState.TP_FILLED:
            self._step_tp_filled()
        elif state == TradeState.TIMEOUT_CANCEL:
            self._step_timeout_cancel()
        elif state == TradeState.TIMEOUT_SELL:
            self._step_timeout_sell()

    def _step_init(self) -> None:
        """INIT → BUY_PENDING."""
        self._audit("step_init", {
            "symbol": self.config.symbol,
            "quote_amount": self.config.quote_amount,
            "mode": self.config.mode.value,
        })

        # Validate balance (skip in DRY mode)
        if self.config.mode != TradeMode.DRY:
            balance = self.client.get_balance("USDT")
            free = float(balance["free"])
            if free < self.config.quote_amount:
                raise ValueError(f"Insufficient balance: {free} USDT < {self.config.quote_amount} USDT")

        # Execute market buy
        if self.config.mode == TradeMode.DRY:
            # Simulate buy
            self.state_data.buy_order_id = 12345
            self.state_data.buy_price = 100000.0  # Simulated
            self.state_data.buy_qty = self.config.quote_amount / self.state_data.buy_price
            self.state_data.buy_filled_at = datetime.now(timezone.utc).isoformat()
            self._transition(TradeState.BUY_FILLED, "DRY mode buy simulated")
        else:
            result = self.client.market_buy(
                self.config.symbol,
                self.config.quote_amount,
            )
            self.state_data.buy_order_id = result.order_id
            self._transition(TradeState.BUY_PENDING, f"Buy order submitted: {result.order_id}")

    def _step_buy_pending(self) -> None:
        """BUY_PENDING → BUY_FILLED."""
        if self.config.mode == TradeMode.DRY:
            self._transition(TradeState.BUY_FILLED, "DRY mode")
            return

        order = self.client.get_order(self.config.symbol, self.state_data.buy_order_id)

        if order["status"] == "FILLED":
            self.state_data.buy_price = float(order.get("price") or order.get("avgPrice", "0"))
            self.state_data.buy_qty = float(order["executedQty"])
            self.state_data.buy_filled_at = datetime.now(timezone.utc).isoformat()

            # Calculate average price from fills if available
            if "fills" in order and order["fills"]:
                total_cost = sum(float(f["price"]) * float(f["qty"]) for f in order["fills"])
                total_qty = sum(float(f["qty"]) for f in order["fills"])
                self.state_data.buy_price = total_cost / total_qty if total_qty > 0 else 0

            self._transition(TradeState.BUY_FILLED, f"Buy filled at {self.state_data.buy_price}")
        elif order["status"] in ("CANCELED", "REJECTED", "EXPIRED"):
            raise ValueError(f"Buy order failed: {order['status']}")
        # else: still pending, wait

    def _step_buy_filled(self) -> None:
        """BUY_FILLED → TP_SELL_PLACED."""
        # Calculate TP price
        tp_price = self.state_data.buy_price * (1 + self.config.tp_percent / 100)
        self.state_data.tp_price = tp_price

        if self.config.mode == TradeMode.DRY:
            self.state_data.tp_order_id = 12346
            self._transition(TradeState.TP_SELL_PLACED, f"DRY mode TP placed at {tp_price:.8f}")
            return

        result = self.client.limit_sell(
            self.config.symbol,
            self.state_data.buy_qty,
            tp_price,
            "GTC",
        )
        self.state_data.tp_order_id = result.order_id
        self._transition(TradeState.TP_SELL_PLACED, f"TP order placed: {result.order_id} at {tp_price:.8f}")

    def _step_tp_placed(self) -> None:
        """TP_SELL_PLACED → TP_FILLED or TIMEOUT_CANCEL."""
        # Check timeout
        buy_time = datetime.fromisoformat(self.state_data.buy_filled_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        elapsed_minutes = (now - buy_time).total_seconds() / 60

        if elapsed_minutes >= self.config.timeout_minutes:
            self._transition(TradeState.TIMEOUT_CANCEL, f"Timeout after {elapsed_minutes:.1f} minutes")
            return

        if self.config.mode == TradeMode.DRY:
            # Simulate: 50% chance of TP hit before timeout
            import random
            if random.random() < 0.5:
                self.state_data.tp_filled_at = datetime.now(timezone.utc).isoformat()
                self._transition(TradeState.TP_FILLED, "DRY mode TP simulated hit")
            else:
                # Sleep a bit to simulate time passing
                time.sleep(1)
            return

        order = self.client.get_order(self.config.symbol, self.state_data.tp_order_id)

        if order["status"] == "FILLED":
            self.state_data.tp_filled_at = datetime.now(timezone.utc).isoformat()
            self._transition(TradeState.TP_FILLED, "TP order filled")
        elif order["status"] in ("CANCELED", "REJECTED", "EXPIRED"):
            raise ValueError(f"TP order failed unexpectedly: {order['status']}")
        # else: still open, continue waiting

    def _step_tp_filled(self) -> None:
        """TP_FILLED → DONE_PROFIT."""
        # Calculate PnL
        buy_cost = self.state_data.buy_qty * self.state_data.buy_price
        sell_revenue = self.state_data.buy_qty * self.state_data.tp_price
        self.state_data.pnl_usdt = sell_revenue - buy_cost
        self.state_data.outcome = "profit"

        self._audit("trade_complete", {
            "outcome": "profit",
            "pnl_usdt": self.state_data.pnl_usdt,
            "buy_price": self.state_data.buy_price,
            "sell_price": self.state_data.tp_price,
        })

        self._transition(TradeState.DONE_PROFIT, f"Profit: {self.state_data.pnl_usdt:.4f} USDT")

    def _step_timeout_cancel(self) -> None:
        """TIMEOUT_CANCEL → TIMEOUT_SELL."""
        if self.config.mode == TradeMode.DRY:
            self._transition(TradeState.TIMEOUT_SELL, "DRY mode TP cancelled")
            return

        try:
            self.client.cancel_order(self.config.symbol, self.state_data.tp_order_id)
        except ValueError as e:
            if "Unknown order" in str(e) or "UNKNOWN_ORDER" in str(e):
                # Order might have been filled between check and cancel
                order = self.client.get_order(self.config.symbol, self.state_data.tp_order_id)
                if order["status"] == "FILLED":
                    self.state_data.tp_filled_at = datetime.now(timezone.utc).isoformat()
                    self._transition(TradeState.TP_FILLED, "TP filled during cancel attempt")
                    return
            raise

        self._transition(TradeState.TIMEOUT_SELL, "TP order cancelled, executing market sell")

    def _step_timeout_sell(self) -> None:
        """TIMEOUT_SELL → DONE_TIMEOUT_EXIT."""
        if self.config.mode == TradeMode.DRY:
            self.state_data.exit_order_id = 12347
            self.state_data.exit_price = self.state_data.buy_price * 0.998  # Simulated small loss
            self.state_data.exit_filled_at = datetime.now(timezone.utc).isoformat()
        else:
            result = self.client.market_sell(
                self.config.symbol,
                self.state_data.buy_qty,
            )
            self.state_data.exit_order_id = result.order_id
            self.state_data.exit_price = float(result.price) if result.price != "0" else self.state_data.buy_price
            self.state_data.exit_filled_at = datetime.now(timezone.utc).isoformat()

            # Get actual fill price
            order = self.client.get_order(self.config.symbol, result.order_id)
            if order.get("fills"):
                total_revenue = sum(float(f["price"]) * float(f["qty"]) for f in order["fills"])
                total_qty = sum(float(f["qty"]) for f in order["fills"])
                self.state_data.exit_price = total_revenue / total_qty if total_qty > 0 else self.state_data.exit_price

        # Calculate PnL
        buy_cost = self.state_data.buy_qty * self.state_data.buy_price
        sell_revenue = self.state_data.buy_qty * self.state_data.exit_price
        self.state_data.pnl_usdt = sell_revenue - buy_cost
        self.state_data.outcome = "timeout_exit"

        self._audit("trade_complete", {
            "outcome": "timeout_exit",
            "pnl_usdt": self.state_data.pnl_usdt,
            "buy_price": self.state_data.buy_price,
            "sell_price": self.state_data.exit_price,
        })

        self._transition(TradeState.DONE_TIMEOUT_EXIT, f"Timeout exit PnL: {self.state_data.pnl_usdt:.4f} USDT")

    def _handle_error(self, error: str) -> None:
        """Handle unrecoverable error."""
        self.state_data.error = error
        self.state_data.outcome = "failed"

        self._audit("trade_error", {"error": error})
        self._transition(TradeState.FAILED, error)


def load_config_from_file(config_path: Path) -> TradeConfig:
    """
    Load trade config from JSON file.

    Args:
        config_path: Path to config file

    Returns:
        TradeConfig

    Raises:
        ValueError: On invalid config
    """
    if not config_path.exists():
        raise ValueError(f"Config file not found: {config_path}")

    data = json.loads(config_path.read_text(encoding="utf-8"))

    mode_str = data.get("mode", "DRY").upper()
    try:
        mode = TradeMode[mode_str]
    except KeyError:
        raise ValueError(f"Invalid mode: {mode_str}")

    return TradeConfig(
        symbol=data["symbol"],
        quote_amount=float(data["quote_amount"]),
        tp_percent=float(data["tp_percent"]),
        timeout_minutes=int(data["timeout_minutes"]),
        mode=mode,
        job_id=data.get("job_id"),
    )
