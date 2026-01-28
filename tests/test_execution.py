# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T00:45:00Z
# Purpose: Tests for Trading Safety Core v1 (execution layer)
# Security: No network calls, all mocked
# === END SIGNATURE ===
"""
Tests for Trading Safety Core v1.

Tests cover:
- Idempotency (deterministic clientOrderId)
- Contracts (OrderIntentV1, OrderAckV1, FillEventV1)
- Journal (atomic append-only writes)
- Outbox (prepare/commit/ack lifecycle)
- Reconciler (UNKNOWN handling)
- Fills Ledger (source of truth)
"""
import pytest
import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import Mock, MagicMock


class TestIdempotency:
    """Tests for idempotency module."""

    def test_generate_client_order_id_deterministic(self):
        """Same inputs produce same output."""
        from core.execution.idempotency import generate_client_order_id

        id1 = generate_client_order_id("BTCUSDT", "BUY", "MARKET", 0.001)
        id2 = generate_client_order_id("BTCUSDT", "BUY", "MARKET", 0.001)

        assert id1 == id2, "IDs should be deterministic"

    def test_client_order_id_max_length(self):
        """ClientOrderId must be <= 36 chars."""
        from core.execution.idempotency import generate_client_order_id

        coid = generate_client_order_id(
            "BTCUSDT", "BUY", "LIMIT", 1.0,
            price=50000.0,
            session_id="a" * 100,  # Long session ID
            nonce="12345678",
        )

        assert len(coid) == 36, f"Length should be 36, got {len(coid)}"
        assert coid.startswith("H"), "Should start with H prefix"

    def test_canonical_payload_sorted(self):
        """Canonical payload has sorted keys."""
        from core.execution.idempotency import canonical_payload

        p1 = canonical_payload("BTCUSDT", "BUY", "MARKET", 0.001)
        p2 = canonical_payload("BTCUSDT", "BUY", "MARKET", 0.001)

        assert p1 == p2
        # Keys should be in order: d, q, s, t
        data = json.loads(p1)
        assert list(data.keys()) == sorted(data.keys())

    def test_verify_idempotency_key(self):
        """Verify that generated ID matches expected."""
        from core.execution.idempotency import (
            generate_client_order_id,
            verify_idempotency_key,
        )

        coid = generate_client_order_id("ETHUSDT", "SELL", "LIMIT", 0.5, price=3000.0)

        assert verify_idempotency_key(
            coid, "ETHUSDT", "SELL", "LIMIT", 0.5, price=3000.0
        )
        assert not verify_idempotency_key(
            coid, "ETHUSDT", "BUY", "LIMIT", 0.5, price=3000.0  # Wrong side
        )

    def test_different_inputs_different_ids(self):
        """Different inputs produce different IDs."""
        from core.execution.idempotency import generate_client_order_id

        id1 = generate_client_order_id("BTCUSDT", "BUY", "MARKET", 0.001)
        id2 = generate_client_order_id("BTCUSDT", "SELL", "MARKET", 0.001)
        id3 = generate_client_order_id("ETHUSDT", "BUY", "MARKET", 0.001)

        assert id1 != id2
        assert id1 != id3
        assert id2 != id3


class TestContracts:
    """Tests for data contracts."""

    def test_order_intent_creation(self):
        """OrderIntentV1 can be created."""
        from core.execution.contracts import OrderIntentV1

        intent = OrderIntentV1(
            client_order_id="H" + "a" * 35,
            symbol="BTCUSDT",
            side="BUY",
            order_type="MARKET",
            quantity=0.001,
        )

        assert intent.symbol == "BTCUSDT"
        assert intent.side == "BUY"
        assert intent.quantity == 0.001

    def test_order_intent_validation_fails(self):
        """OrderIntentV1 validates on creation."""
        from core.execution.contracts import OrderIntentV1

        # Invalid side
        with pytest.raises(ValueError, match="Invalid side"):
            OrderIntentV1(
                client_order_id="H" + "a" * 35,
                symbol="BTCUSDT",
                side="INVALID",
                order_type="MARKET",
                quantity=0.001,
            )

        # Too long clientOrderId
        with pytest.raises(ValueError, match="exceeds 36 chars"):
            OrderIntentV1(
                client_order_id="H" + "a" * 40,  # 41 chars
                symbol="BTCUSDT",
                side="BUY",
                order_type="MARKET",
                quantity=0.001,
            )

    def test_order_ack_from_timeout(self):
        """OrderAckV1 can be created for timeout."""
        from core.execution.contracts import OrderAckV1, OrderStatus

        ack = OrderAckV1.from_timeout("H123", "connection timeout")

        assert ack.client_order_id == "H123"
        assert ack.status == OrderStatus.UNKNOWN
        assert ack.is_unknown
        assert not ack.is_success

    def test_fill_event_notional(self):
        """FillEventV1 calculates notional."""
        from core.execution.contracts import FillEventV1

        fill = FillEventV1(
            fill_id=12345,
            client_order_id="H123",
            exchange_order_id=99999,
            symbol="BTCUSDT",
            side="BUY",
            price=50000.0,
            quantity=0.1,
        )

        assert fill.notional == 5000.0  # 50000 * 0.1


class TestJournal:
    """Tests for atomic journal."""

    def test_journal_append_and_read(self, tmp_path):
        """Journal can append and read entries."""
        from core.execution.journal import AtomicJournal

        journal_path = tmp_path / "test.jsonl"
        journal = AtomicJournal(journal_path)

        # Append entries
        e1 = journal.append("test", {"key": "value1"})
        e2 = journal.append("test", {"key": "value2"})

        assert e1.sequence == 1
        assert e2.sequence == 2

        # Read back
        entries = journal.read_all()
        assert len(entries) == 2
        assert entries[0].data["key"] == "value1"
        assert entries[1].data["key"] == "value2"

    def test_journal_persistence(self, tmp_path):
        """Journal survives restart."""
        from core.execution.journal import AtomicJournal

        journal_path = tmp_path / "test.jsonl"

        # Write with first instance
        j1 = AtomicJournal(journal_path)
        j1.append("test", {"n": 1})
        j1.append("test", {"n": 2})

        # Read with new instance
        j2 = AtomicJournal(journal_path)
        entries = j2.read_all()

        assert len(entries) == 2
        assert entries[0].data["n"] == 1

    def test_journal_entry_has_id(self, tmp_path):
        """Each entry gets unique ID."""
        from core.execution.journal import AtomicJournal

        journal = AtomicJournal(tmp_path / "test.jsonl")
        e1 = journal.append("test", {"x": 1})
        e2 = journal.append("test", {"x": 2})

        assert e1.entry_id != e2.entry_id
        assert len(e1.entry_id) == 16


class TestOutbox:
    """Tests for order outbox."""

    def test_outbox_lifecycle(self, tmp_path):
        """Outbox tracks order lifecycle."""
        from core.execution.outbox import Outbox, IntentStatus
        from core.execution.contracts import OrderIntentV1, OrderAckV1, OrderStatus

        outbox = Outbox(tmp_path / "outbox.jsonl")

        intent = OrderIntentV1(
            client_order_id="H" + "b" * 35,
            symbol="BTCUSDT",
            side="BUY",
            order_type="MARKET",
            quantity=0.001,
        )

        # Prepare
        entry = outbox.prepare(intent)
        assert entry.status == IntentStatus.PREPARED

        # Commit
        entry = outbox.commit(intent.client_order_id)
        assert entry.status == IntentStatus.COMMITTED

        # Ack
        ack = OrderAckV1(
            client_order_id=intent.client_order_id,
            exchange_order_id=12345,
            status=OrderStatus.FILLED,
        )
        entry = outbox.ack(intent.client_order_id, ack)
        assert entry.status == IntentStatus.FILLED

    def test_outbox_duplicate_detection(self, tmp_path):
        """Outbox detects duplicate intents."""
        from core.execution.outbox import Outbox
        from core.execution.contracts import OrderIntentV1

        outbox = Outbox(tmp_path / "outbox.jsonl")

        intent = OrderIntentV1(
            client_order_id="H" + "c" * 35,
            symbol="BTCUSDT",
            side="BUY",
            order_type="MARKET",
            quantity=0.001,
        )

        outbox.prepare(intent)

        with pytest.raises(ValueError, match="Duplicate intent"):
            outbox.prepare(intent)

    def test_outbox_unknown_tracking(self, tmp_path):
        """Outbox tracks UNKNOWN states."""
        from core.execution.outbox import Outbox, IntentStatus
        from core.execution.contracts import OrderIntentV1

        outbox = Outbox(tmp_path / "outbox.jsonl")

        intent = OrderIntentV1(
            client_order_id="H" + "d" * 35,
            symbol="BTCUSDT",
            side="BUY",
            order_type="MARKET",
            quantity=0.001,
        )

        outbox.prepare(intent)
        outbox.commit(intent.client_order_id)
        outbox.unknown(intent.client_order_id, "timeout after 30s")

        unknown_entries = outbox.get_unknown()
        assert len(unknown_entries) == 1
        assert unknown_entries[0].client_order_id == intent.client_order_id


class TestReconciler:
    """Tests for reconciler."""

    def test_reconcile_not_found(self, tmp_path):
        """Reconciler handles order not found."""
        from core.execution.outbox import Outbox, IntentStatus
        from core.execution.reconcile import Reconciler, ReconcileAction
        from core.execution.contracts import OrderIntentV1

        outbox = Outbox(tmp_path / "outbox.jsonl")

        intent = OrderIntentV1(
            client_order_id="H" + "e" * 35,
            symbol="BTCUSDT",
            side="BUY",
            order_type="MARKET",
            quantity=0.001,
        )

        outbox.prepare(intent)
        outbox.commit(intent.client_order_id)
        outbox.unknown(intent.client_order_id, "timeout")

        # Mock exchange that returns None (not found)
        mock_exchange = Mock()
        mock_exchange.query_order.return_value = None

        reconciler = Reconciler(outbox, mock_exchange)
        result = reconciler.reconcile_one(intent.client_order_id, "BTCUSDT")

        assert result.action == ReconcileAction.NOT_FOUND
        assert result.can_retry is True

    def test_reconcile_found_filled(self, tmp_path):
        """Reconciler handles order found and filled."""
        from core.execution.outbox import Outbox
        from core.execution.reconcile import Reconciler, ReconcileAction
        from core.execution.contracts import OrderIntentV1

        outbox = Outbox(tmp_path / "outbox.jsonl")

        intent = OrderIntentV1(
            client_order_id="H" + "f" * 35,
            symbol="BTCUSDT",
            side="BUY",
            order_type="MARKET",
            quantity=0.001,
        )

        outbox.prepare(intent)
        outbox.commit(intent.client_order_id)
        outbox.unknown(intent.client_order_id, "timeout")

        # Mock exchange that returns filled order
        mock_exchange = Mock()
        mock_exchange.query_order.return_value = {
            "orderId": 12345,
            "status": "FILLED",
            "executedQty": "0.001",
            "price": "50000.0",
        }

        reconciler = Reconciler(outbox, mock_exchange)
        result = reconciler.reconcile_one(intent.client_order_id, "BTCUSDT")

        assert result.action == ReconcileAction.FILLED
        assert result.can_retry is False


class TestFillsLedger:
    """Tests for fills ledger."""

    def test_ledger_record_fill(self, tmp_path):
        """Ledger records fills."""
        from core.execution.fills_ledger import FillsLedger
        from core.execution.contracts import FillEventV1

        ledger = FillsLedger(tmp_path / "fills.jsonl")

        fill = FillEventV1(
            fill_id=1001,
            client_order_id="H123",
            exchange_order_id=99999,
            symbol="BTCUSDT",
            side="BUY",
            price=50000.0,
            quantity=0.1,
        )

        record = ledger.record(fill)
        assert record is not None
        assert record.ledger_sequence == 1

    def test_ledger_deduplication(self, tmp_path):
        """Ledger deduplicates by fill_id."""
        from core.execution.fills_ledger import FillsLedger
        from core.execution.contracts import FillEventV1

        ledger = FillsLedger(tmp_path / "fills.jsonl")

        fill = FillEventV1(
            fill_id=1001,
            client_order_id="H123",
            exchange_order_id=99999,
            symbol="BTCUSDT",
            side="BUY",
            price=50000.0,
            quantity=0.1,
        )

        r1 = ledger.record(fill)
        r2 = ledger.record(fill)  # Duplicate

        assert r1 is not None
        assert r2 is None  # Deduplicated

    def test_ledger_aggregation(self, tmp_path):
        """Ledger aggregates fills for order."""
        from core.execution.fills_ledger import FillsLedger
        from core.execution.contracts import FillEventV1

        ledger = FillsLedger(tmp_path / "fills.jsonl")

        # Two partial fills for same order
        fill1 = FillEventV1(
            fill_id=1001,
            client_order_id="HORDER1",
            exchange_order_id=99999,
            symbol="BTCUSDT",
            side="BUY",
            price=50000.0,
            quantity=0.05,
        )
        fill2 = FillEventV1(
            fill_id=1002,
            client_order_id="HORDER1",
            exchange_order_id=99999,
            symbol="BTCUSDT",
            side="BUY",
            price=50100.0,
            quantity=0.05,
        )

        ledger.record(fill1)
        ledger.record(fill2)

        total_qty = ledger.total_filled_qty("HORDER1")
        assert total_qty == 0.1

        avg_price = ledger.avg_fill_price("HORDER1")
        assert avg_price is not None
        assert abs(avg_price - 50050.0) < 0.01  # VWAP

    def test_ledger_pnl_calculation(self, tmp_path):
        """Ledger computes P&L between entry and exit."""
        from core.execution.fills_ledger import FillsLedger
        from core.execution.contracts import FillEventV1

        ledger = FillsLedger(tmp_path / "fills.jsonl")

        # Entry: BUY 0.1 BTC @ 50000
        entry_fill = FillEventV1(
            fill_id=2001,
            client_order_id="HENTRY",
            exchange_order_id=11111,
            symbol="BTCUSDT",
            side="BUY",
            price=50000.0,
            quantity=0.1,
        )

        # Exit: SELL 0.1 BTC @ 51000
        exit_fill = FillEventV1(
            fill_id=2002,
            client_order_id="HEXIT",
            exchange_order_id=22222,
            symbol="BTCUSDT",
            side="SELL",
            price=51000.0,
            quantity=0.1,
        )

        ledger.record(entry_fill)
        ledger.record(exit_fill)

        pnl = ledger.compute_pnl("HENTRY", "HEXIT")
        assert pnl == 100.0  # 5100 - 5000 = 100 USDT profit


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
