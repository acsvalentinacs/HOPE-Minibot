# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T00:30:00Z
# Purpose: Order outbox pattern - prepare/commit/ack lifecycle
# Security: Fail-closed, UNKNOWN detection, no lost orders
# === END SIGNATURE ===
"""
Order Outbox - Two-Phase Order Submission.

The outbox pattern ensures no order is lost:
1. PREPARE: Write intent to disk (outbox.jsonl)
2. COMMIT: Send to exchange
3. ACK: Write result to disk
4. UNKNOWN: If timeout/5xx, quarantine for reconcile

CRITICAL: NEVER retry after UNKNOWN. Always read-your-writes first.
"""
import os
import json
import hashlib
from pathlib import Path
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from enum import Enum

from core.execution.contracts import OrderIntentV1, OrderAckV1, IntentStatus, OrderStatus
from core.execution.journal import AtomicJournal, JournalEntry


@dataclass
class OutboxEntry:
    """
    Outbox entry tracking order lifecycle.

    Immutable once created. Status updates create new entries.
    """
    client_order_id: str
    intent: Dict[str, Any]  # OrderIntentV1 as dict
    status: IntentStatus
    ack: Optional[Dict[str, Any]] = None  # OrderAckV1 as dict
    error: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OutboxEntry":
        """Deserialize from dict."""
        data = dict(data)
        if "status" in data and isinstance(data["status"], str):
            data["status"] = IntentStatus(data["status"])
        return cls(**data)


class Outbox:
    """
    Order Outbox Manager.

    Manages the lifecycle of order intents:
    - prepare(): Write intent before sending
    - commit(): Mark as sent to exchange
    - ack(): Record exchange response
    - unknown(): Mark as needing reconcile
    - reconciled(): Mark as reconciled

    Usage:
        outbox = Outbox(Path("state/outbox.jsonl"))

        # Before sending
        intent = OrderIntentV1(...)
        outbox.prepare(intent)

        # After sending
        outbox.commit(intent.client_order_id)

        # After response
        outbox.ack(intent.client_order_id, ack)

        # OR on timeout
        outbox.unknown(intent.client_order_id, "timeout after 30s")
    """

    def __init__(self, path: Path):
        """
        Initialize outbox.

        Args:
            path: Path to outbox JSONL file
        """
        self.path = path
        self._journal = AtomicJournal(path)
        self._cache: Dict[str, OutboxEntry] = {}
        self._load_cache()

    def _load_cache(self):
        """Load current state from journal."""
        for entry in self._journal.iter_entries():
            if entry.entry_type == "outbox":
                outbox_entry = OutboxEntry.from_dict(entry.data)
                self._cache[outbox_entry.client_order_id] = outbox_entry

    def _append(self, outbox_entry: OutboxEntry):
        """Append entry to journal and update cache."""
        self._journal.append("outbox", outbox_entry.to_dict())
        self._cache[outbox_entry.client_order_id] = outbox_entry

    def prepare(self, intent: OrderIntentV1) -> OutboxEntry:
        """
        Prepare order intent (Phase 1).

        Writes intent to disk BEFORE sending to exchange.
        CRITICAL: Always call this before network call.

        Args:
            intent: Order intent to prepare

        Returns:
            OutboxEntry in PREPARED status.

        Raises:
            ValueError: If intent already exists (duplicate detection).
        """
        if intent.client_order_id in self._cache:
            existing = self._cache[intent.client_order_id]
            # Allow re-prepare only if previous was FAILED
            if existing.status not in (IntentStatus.FAILED,):
                raise ValueError(
                    f"Duplicate intent: {intent.client_order_id} "
                    f"already in {existing.status.value}"
                )

        entry = OutboxEntry(
            client_order_id=intent.client_order_id,
            intent=intent.to_dict(),
            status=IntentStatus.PREPARED,
        )
        self._append(entry)
        return entry

    def commit(self, client_order_id: str) -> OutboxEntry:
        """
        Mark intent as committed (Phase 2 - sent to exchange).

        Call this immediately after sending to exchange,
        BEFORE waiting for response.

        Args:
            client_order_id: The order ID

        Returns:
            Updated OutboxEntry.

        Raises:
            ValueError: If intent not found or not in PREPARED state.
        """
        if client_order_id not in self._cache:
            raise ValueError(f"Intent not found: {client_order_id}")

        existing = self._cache[client_order_id]
        if existing.status != IntentStatus.PREPARED:
            raise ValueError(
                f"Cannot commit from {existing.status.value}, "
                f"expected PREPARED"
            )

        entry = OutboxEntry(
            client_order_id=client_order_id,
            intent=existing.intent,
            status=IntentStatus.COMMITTED,
            created_at=existing.created_at,
        )
        self._append(entry)
        return entry

    def ack(
        self,
        client_order_id: str,
        ack: OrderAckV1,
    ) -> OutboxEntry:
        """
        Record exchange acknowledgment (Phase 3).

        Call this after receiving exchange response.

        Args:
            client_order_id: The order ID
            ack: Exchange response

        Returns:
            Updated OutboxEntry.
        """
        if client_order_id not in self._cache:
            raise ValueError(f"Intent not found: {client_order_id}")

        existing = self._cache[client_order_id]

        # Determine new status based on ack
        if ack.is_unknown:
            new_status = IntentStatus.UNKNOWN
        elif ack.status == OrderStatus.REJECTED:
            new_status = IntentStatus.FAILED
        elif ack.status == OrderStatus.FILLED:
            new_status = IntentStatus.FILLED
        else:
            new_status = IntentStatus.ACKED

        entry = OutboxEntry(
            client_order_id=client_order_id,
            intent=existing.intent,
            status=new_status,
            ack=ack.to_dict(),
            created_at=existing.created_at,
        )
        self._append(entry)
        return entry

    def unknown(
        self,
        client_order_id: str,
        reason: str,
    ) -> OutboxEntry:
        """
        Mark as UNKNOWN (timeout/5xx).

        CRITICAL: After calling this, DO NOT retry the order.
        Must reconcile first using read-your-writes.

        Args:
            client_order_id: The order ID
            reason: Why it's unknown (e.g., "timeout after 30s")

        Returns:
            Updated OutboxEntry.
        """
        if client_order_id not in self._cache:
            raise ValueError(f"Intent not found: {client_order_id}")

        existing = self._cache[client_order_id]

        entry = OutboxEntry(
            client_order_id=client_order_id,
            intent=existing.intent,
            status=IntentStatus.UNKNOWN,
            error=reason,
            created_at=existing.created_at,
        )
        self._append(entry)
        return entry

    def reconciled(
        self,
        client_order_id: str,
        ack: OrderAckV1,
    ) -> OutboxEntry:
        """
        Mark as reconciled after read-your-writes.

        Args:
            client_order_id: The order ID
            ack: Reconciled state from exchange

        Returns:
            Updated OutboxEntry.
        """
        if client_order_id not in self._cache:
            raise ValueError(f"Intent not found: {client_order_id}")

        existing = self._cache[client_order_id]

        # Determine final status
        if ack.status == OrderStatus.FILLED:
            new_status = IntentStatus.FILLED
        elif ack.is_terminal:
            new_status = IntentStatus.FAILED
        else:
            new_status = IntentStatus.RECONCILED

        entry = OutboxEntry(
            client_order_id=client_order_id,
            intent=existing.intent,
            status=new_status,
            ack=ack.to_dict(),
            created_at=existing.created_at,
        )
        self._append(entry)
        return entry

    def get(self, client_order_id: str) -> Optional[OutboxEntry]:
        """Get entry by client_order_id."""
        return self._cache.get(client_order_id)

    def get_by_status(self, status: IntentStatus) -> List[OutboxEntry]:
        """Get all entries with given status."""
        return [e for e in self._cache.values() if e.status == status]

    def get_unknown(self) -> List[OutboxEntry]:
        """Get all UNKNOWN entries needing reconcile."""
        return self.get_by_status(IntentStatus.UNKNOWN)

    def get_pending(self) -> List[OutboxEntry]:
        """Get all non-terminal entries."""
        return [
            e for e in self._cache.values()
            if e.status in (
                IntentStatus.PREPARED,
                IntentStatus.COMMITTED,
                IntentStatus.UNKNOWN,
            )
        ]

    def has_pending(self) -> bool:
        """Check if there are any pending orders."""
        return len(self.get_pending()) > 0

    def count_by_status(self) -> Dict[str, int]:
        """Get count of entries by status."""
        counts: Dict[str, int] = {}
        for entry in self._cache.values():
            status = entry.status.value
            counts[status] = counts.get(status, 0) + 1
        return counts
