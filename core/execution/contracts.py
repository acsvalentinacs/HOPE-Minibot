# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T00:30:00Z
# Purpose: Data contracts for order execution (immutable, versioned)
# Security: Type-safe, serializable, fail-closed validation
# === END SIGNATURE ===
"""
Order Execution Contracts v1.

These dataclasses define the canonical format for:
- OrderIntentV1: What we WANT to do (before network call)
- OrderAckV1: What exchange SAID (after network call)
- FillEventV1: What ACTUALLY happened (only source of truth)

CRITICAL: These are immutable. Any mutation = new version.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Dict, Any, List
import json


class OrderStatus(Enum):
    """Exchange order status (from Binance API)."""
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    PENDING_CANCEL = "PENDING_CANCEL"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    EXPIRED_IN_MATCH = "EXPIRED_IN_MATCH"
    # Internal statuses
    UNKNOWN = "UNKNOWN"  # timeout/5xx - MUST reconcile


class IntentStatus(Enum):
    """Intent lifecycle status (local tracking)."""
    PREPARED = "PREPARED"      # Written to outbox, not sent
    COMMITTED = "COMMITTED"    # Sent to exchange
    ACKED = "ACKED"            # Got response (success or error)
    UNKNOWN = "UNKNOWN"        # timeout/5xx - quarantine
    RECONCILED = "RECONCILED"  # Read-your-writes completed
    FAILED = "FAILED"          # Terminal failure (rejected, etc)
    FILLED = "FILLED"          # Execution confirmed via FillEvent


@dataclass(frozen=True)
class OrderIntentV1:
    """
    Order intent - what we WANT to do.

    Written to outbox BEFORE network call.
    Immutable after creation.

    Attributes:
        client_order_id: Deterministic ID from payload hash (max 36 chars)
        symbol: Trading pair (e.g., "BTCUSDT")
        side: "BUY" or "SELL"
        order_type: "MARKET", "LIMIT", etc.
        quantity: Amount to trade
        price: Limit price (None for MARKET)
        time_in_force: "GTC", "IOC", "FOK"
        session_id: cmdline_sha256 for this session
        created_at: UTC timestamp
        metadata: Additional context (strategy, signal_id, etc)
    """
    client_order_id: str
    symbol: str
    side: str  # "BUY" | "SELL"
    order_type: str  # "MARKET" | "LIMIT" | etc
    quantity: float
    price: Optional[float] = None
    time_in_force: str = "GTC"
    session_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = "intent.v1"

    def __post_init__(self):
        """Validate on creation (fail-closed)."""
        if len(self.client_order_id) > 36:
            raise ValueError(f"client_order_id exceeds 36 chars: {len(self.client_order_id)}")
        if self.side not in ("BUY", "SELL"):
            raise ValueError(f"Invalid side: {self.side}")
        if self.quantity <= 0:
            raise ValueError(f"Invalid quantity: {self.quantity}")
        if self.order_type == "LIMIT" and self.price is None:
            raise ValueError("LIMIT order requires price")

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return asdict(self)

    def to_json(self) -> str:
        """Serialize to JSON (canonical, sorted keys)."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OrderIntentV1":
        """Deserialize from dict."""
        # Remove schema_version if it doesn't match constructor
        data = dict(data)
        data.pop("schema_version", None)
        return cls(**data, schema_version="intent.v1")


@dataclass(frozen=True)
class OrderAckV1:
    """
    Order acknowledgment - what exchange SAID.

    This is NOT the source of truth for execution.
    Only FillEventV1 is authoritative.

    Attributes:
        client_order_id: Our deterministic ID
        exchange_order_id: Exchange's order ID (orderId)
        status: Exchange order status
        filled_qty: How much was filled (may be partial)
        avg_price: Average fill price
        response_at: When we got the response
        raw_response: Full exchange response (for debugging)
    """
    client_order_id: str
    exchange_order_id: Optional[int] = None
    status: OrderStatus = OrderStatus.UNKNOWN
    filled_qty: float = 0.0
    avg_price: float = 0.0
    response_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    raw_response: Dict[str, Any] = field(default_factory=dict)
    error_code: Optional[int] = None
    error_msg: Optional[str] = None
    schema_version: str = "ack.v1"

    @property
    def is_success(self) -> bool:
        """Check if order was accepted."""
        return self.status in (
            OrderStatus.NEW,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
        )

    @property
    def is_terminal(self) -> bool:
        """Check if order reached terminal state."""
        return self.status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
            OrderStatus.EXPIRED_IN_MATCH,
        )

    @property
    def is_unknown(self) -> bool:
        """Check if state is unknown (needs reconcile)."""
        return self.status == OrderStatus.UNKNOWN

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OrderAckV1":
        """Deserialize from dict."""
        data = dict(data)
        if "status" in data and isinstance(data["status"], str):
            data["status"] = OrderStatus(data["status"])
        data.pop("schema_version", None)
        return cls(**data, schema_version="ack.v1")

    @classmethod
    def from_timeout(cls, client_order_id: str, error_msg: str = "timeout") -> "OrderAckV1":
        """Create UNKNOWN ack for timeout/network error."""
        return cls(
            client_order_id=client_order_id,
            status=OrderStatus.UNKNOWN,
            error_msg=error_msg,
        )


@dataclass(frozen=True)
class FillEventV1:
    """
    Fill event - what ACTUALLY happened.

    THIS IS THE ONLY SOURCE OF TRUTH FOR EXECUTION.
    Only create from exchange fills (trades), never from order responses.

    Attributes:
        fill_id: Unique fill ID (trade ID from exchange)
        client_order_id: Our deterministic ID
        exchange_order_id: Exchange's order ID
        symbol: Trading pair
        side: "BUY" or "SELL"
        price: Actual fill price
        quantity: Actual fill quantity
        commission: Fee paid
        commission_asset: Fee asset (e.g., "BNB", "USDT")
        trade_time: Exchange trade timestamp
        is_maker: True if we were maker
    """
    fill_id: int  # Exchange trade ID
    client_order_id: str
    exchange_order_id: int
    symbol: str
    side: str
    price: float
    quantity: float
    commission: float = 0.0
    commission_asset: str = ""
    trade_time: int = 0  # Unix ms from exchange
    is_maker: bool = False
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    schema_version: str = "fill.v1"

    @property
    def notional(self) -> float:
        """Calculate notional value (price * quantity)."""
        return self.price * self.quantity

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FillEventV1":
        """Deserialize from dict."""
        data = dict(data)
        data.pop("schema_version", None)
        return cls(**data, schema_version="fill.v1")

    @classmethod
    def from_binance_trade(
        cls,
        trade: Dict[str, Any],
        client_order_id: str,
    ) -> "FillEventV1":
        """Create from Binance trade response."""
        return cls(
            fill_id=trade["tradeId"],
            client_order_id=client_order_id,
            exchange_order_id=trade["orderId"],
            symbol=trade["symbol"],
            side=trade["side"],
            price=float(trade["price"]),
            quantity=float(trade["qty"]),
            commission=float(trade.get("commission", 0)),
            commission_asset=trade.get("commissionAsset", ""),
            trade_time=trade.get("time", 0),
            is_maker=trade.get("isMaker", False),
        )
