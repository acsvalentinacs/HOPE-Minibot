# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T00:30:00Z
# Purpose: Trading Safety Core v1 - Execution layer with fail-closed guarantees
# Security: Idempotency, Outbox pattern, UNKNOWN protocol, Fill ledger
# === END SIGNATURE ===
"""
Trading Safety Core v1 - Execution Layer.

This package provides P0 safety guarantees for order execution:
- Idempotency: Deterministic clientOrderId from payload hash
- Outbox: Append-only journal before network calls (prepare → commit)
- UNKNOWN Protocol: timeout/5xx → read-your-writes, not retry
- Fill Ledger: FillEvent as ONLY source of truth for executions

CRITICAL: All components are FAIL-CLOSED. Any uncertainty = STOP.
"""

from core.execution.contracts import (
    OrderIntentV1,
    OrderAckV1,
    FillEventV1,
    OrderStatus,
    IntentStatus,
)
from core.execution.idempotency import (
    generate_client_order_id,
    canonical_payload,
    verify_idempotency_key,
    cmdline_sha256_id,
    get_command_line_w_ssot,
)
from core.execution.journal import (
    AtomicJournal,
    JournalEntry,
)
from core.execution.outbox import (
    Outbox,
    OutboxEntry,
)
from core.execution.reconcile import (
    Reconciler,
    ReconcileResult,
)
from core.execution.fills_ledger import (
    FillsLedger,
    FillRecord,
)

__all__ = [
    # Contracts
    "OrderIntentV1",
    "OrderAckV1",
    "FillEventV1",
    "OrderStatus",
    "IntentStatus",
    # Idempotency
    "generate_client_order_id",
    "canonical_payload",
    "verify_idempotency_key",
    "cmdline_sha256_id",
    "get_command_line_w_ssot",
    # Journal
    "AtomicJournal",
    "JournalEntry",
    # Outbox
    "Outbox",
    "OutboxEntry",
    # Reconcile
    "Reconciler",
    "ReconcileResult",
    # Fills
    "FillsLedger",
    "FillRecord",
]
