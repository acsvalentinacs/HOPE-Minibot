# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T00:30:00Z
# Purpose: Read-your-writes reconciliation for UNKNOWN orders
# Security: Never retry after UNKNOWN, always query first
# === END SIGNATURE ===
"""
Reconciler - Read-Your-Writes Protocol for UNKNOWN Orders.

When order submission results in timeout/5xx:
1. DO NOT retry the order
2. Query exchange for order status by clientOrderId
3. If found: update outbox with actual status
4. If not found: safe to retry (order never reached exchange)

CRITICAL: This is the ONLY safe way to handle UNKNOWN states.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Protocol, runtime_checkable
from enum import Enum

from core.execution.contracts import OrderAckV1, OrderStatus
from core.execution.outbox import Outbox, OutboxEntry, IntentStatus


class ReconcileAction(Enum):
    """What to do after reconcile."""
    CONFIRMED = "CONFIRMED"      # Order exists on exchange
    NOT_FOUND = "NOT_FOUND"      # Order never reached exchange
    STILL_UNKNOWN = "STILL_UNKNOWN"  # Reconcile also failed
    FILLED = "FILLED"            # Order was filled
    CANCELED = "CANCELED"        # Order was canceled


@dataclass
class ReconcileResult:
    """Result of reconciliation attempt."""
    client_order_id: str
    action: ReconcileAction
    ack: Optional[OrderAckV1] = None
    error: Optional[str] = None
    reconciled_at: str = ""

    def __post_init__(self):
        if not self.reconciled_at:
            self.reconciled_at = datetime.now(timezone.utc).isoformat()

    @property
    def can_retry(self) -> bool:
        """Check if safe to retry order."""
        return self.action == ReconcileAction.NOT_FOUND


@runtime_checkable
class ExchangeQueryProtocol(Protocol):
    """Protocol for querying exchange order status."""

    def query_order(
        self,
        symbol: str,
        client_order_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Query order by clientOrderId.

        Returns:
            Order dict if found, None if not found.

        Raises:
            Exception on network/API error.
        """
        ...


class Reconciler:
    """
    Order Reconciler for UNKNOWN states.

    Implements read-your-writes protocol:
    1. Query exchange for order status
    2. Update outbox with actual status
    3. Determine if retry is safe

    Usage:
        reconciler = Reconciler(outbox, exchange_client)

        # Reconcile single order
        result = reconciler.reconcile_one(client_order_id, symbol)
        if result.can_retry:
            # Safe to retry
            pass

        # Reconcile all UNKNOWN orders
        results = reconciler.reconcile_all()
    """

    def __init__(
        self,
        outbox: Outbox,
        exchange_query: ExchangeQueryProtocol,
    ):
        """
        Initialize reconciler.

        Args:
            outbox: Order outbox
            exchange_query: Exchange client for querying orders
        """
        self.outbox = outbox
        self.exchange = exchange_query

    def reconcile_one(
        self,
        client_order_id: str,
        symbol: str,
    ) -> ReconcileResult:
        """
        Reconcile single UNKNOWN order.

        Args:
            client_order_id: Order ID to reconcile
            symbol: Trading pair

        Returns:
            ReconcileResult with action and updated ack.
        """
        entry = self.outbox.get(client_order_id)
        if entry is None:
            return ReconcileResult(
                client_order_id=client_order_id,
                action=ReconcileAction.NOT_FOUND,
                error="Intent not in outbox",
            )

        if entry.status != IntentStatus.UNKNOWN:
            return ReconcileResult(
                client_order_id=client_order_id,
                action=ReconcileAction.CONFIRMED,
                error=f"Not UNKNOWN, status is {entry.status.value}",
            )

        try:
            # Query exchange
            order_data = self.exchange.query_order(symbol, client_order_id)

            if order_data is None:
                # Order never reached exchange - safe to retry
                return ReconcileResult(
                    client_order_id=client_order_id,
                    action=ReconcileAction.NOT_FOUND,
                )

            # Order exists - create ack from response
            ack = self._parse_order_response(client_order_id, order_data)

            # Update outbox
            self.outbox.reconciled(client_order_id, ack)

            # Determine action based on status
            if ack.status == OrderStatus.FILLED:
                action = ReconcileAction.FILLED
            elif ack.status == OrderStatus.CANCELED:
                action = ReconcileAction.CANCELED
            else:
                action = ReconcileAction.CONFIRMED

            return ReconcileResult(
                client_order_id=client_order_id,
                action=action,
                ack=ack,
            )

        except Exception as e:
            # Reconcile failed - still unknown
            return ReconcileResult(
                client_order_id=client_order_id,
                action=ReconcileAction.STILL_UNKNOWN,
                error=str(e),
            )

    def reconcile_all(self) -> List[ReconcileResult]:
        """
        Reconcile all UNKNOWN orders.

        Returns:
            List of ReconcileResult for each UNKNOWN order.
        """
        unknown_entries = self.outbox.get_unknown()
        results = []

        for entry in unknown_entries:
            symbol = entry.intent.get("symbol", "")
            if not symbol:
                results.append(ReconcileResult(
                    client_order_id=entry.client_order_id,
                    action=ReconcileAction.STILL_UNKNOWN,
                    error="Missing symbol in intent",
                ))
                continue

            result = self.reconcile_one(entry.client_order_id, symbol)
            results.append(result)

        return results

    def _parse_order_response(
        self,
        client_order_id: str,
        order_data: Dict[str, Any],
    ) -> OrderAckV1:
        """Parse exchange order response into OrderAckV1."""
        status_str = order_data.get("status", "UNKNOWN")
        try:
            status = OrderStatus(status_str)
        except ValueError:
            status = OrderStatus.UNKNOWN

        return OrderAckV1(
            client_order_id=client_order_id,
            exchange_order_id=order_data.get("orderId"),
            status=status,
            filled_qty=float(order_data.get("executedQty", 0)),
            avg_price=float(order_data.get("avgPrice", 0) or
                          order_data.get("price", 0)),
            raw_response=order_data,
        )

    def has_unknown(self) -> bool:
        """Check if there are UNKNOWN orders needing reconcile."""
        return len(self.outbox.get_unknown()) > 0

    def get_unknown_count(self) -> int:
        """Get count of UNKNOWN orders."""
        return len(self.outbox.get_unknown())
