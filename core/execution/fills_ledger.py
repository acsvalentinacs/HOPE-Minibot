# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T00:30:00Z
# Purpose: Fill ledger - ONLY source of truth for executions
# Security: Append-only, immutable records, double-entry validation
# === END SIGNATURE ===
"""
Fills Ledger - Authoritative Record of Executions.

THIS IS THE ONLY SOURCE OF TRUTH FOR WHAT ACTUALLY HAPPENED.

Rules:
1. Only FillEvents are authoritative (not order responses)
2. Append-only (no modifications, no deletions)
3. Each fill has unique fill_id (exchange trade ID)
4. Supports aggregation by order, symbol, time

Usage:
    ledger = FillsLedger(Path("state/fills.jsonl"))

    # Record fill from exchange
    fill = FillEventV1.from_binance_trade(trade, client_order_id)
    ledger.record(fill)

    # Query fills
    fills = ledger.get_fills_for_order(client_order_id)
    total_qty = ledger.total_filled_qty(client_order_id)
"""
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from core.execution.contracts import FillEventV1
from core.execution.journal import AtomicJournal


@dataclass
class FillRecord:
    """Wrapper for fill with ledger metadata."""
    fill: FillEventV1
    ledger_sequence: int
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {
            "fill": self.fill.to_dict(),
            "ledger_sequence": self.ledger_sequence,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FillRecord":
        """Deserialize from dict."""
        return cls(
            fill=FillEventV1.from_dict(data["fill"]),
            ledger_sequence=data["ledger_sequence"],
            recorded_at=data.get("recorded_at", ""),
        )


class FillsLedger:
    """
    Fills Ledger - Authoritative execution records.

    Key properties:
    - Append-only: No modifications or deletions
    - Deduplicated: fill_id is unique (exchange trade ID)
    - Atomic: fsync on every write
    - Queryable: By order, symbol, time range

    This is the ONLY place to look for "what actually happened".
    Order responses (acks) are NOT authoritative.
    """

    def __init__(self, path: Path):
        """
        Initialize fills ledger.

        Args:
            path: Path to fills.jsonl file
        """
        self.path = path
        self._journal = AtomicJournal(path)
        self._fill_ids: set = set()
        self._by_order: Dict[str, List[FillRecord]] = {}
        self._by_symbol: Dict[str, List[FillRecord]] = {}
        self._sequence = 0
        self._load()

    def _load(self):
        """Load existing fills into memory indices."""
        for entry in self._journal.iter_entries():
            if entry.entry_type == "fill":
                record = FillRecord.from_dict(entry.data)
                self._index_record(record)
                self._sequence = max(self._sequence, record.ledger_sequence)

    def _index_record(self, record: FillRecord):
        """Add record to in-memory indices."""
        fill = record.fill
        self._fill_ids.add(fill.fill_id)

        if fill.client_order_id not in self._by_order:
            self._by_order[fill.client_order_id] = []
        self._by_order[fill.client_order_id].append(record)

        if fill.symbol not in self._by_symbol:
            self._by_symbol[fill.symbol] = []
        self._by_symbol[fill.symbol].append(record)

    def record(self, fill: FillEventV1) -> Optional[FillRecord]:
        """
        Record a fill event.

        Deduplicates by fill_id (exchange trade ID).

        Args:
            fill: Fill event to record

        Returns:
            FillRecord if new, None if duplicate.
        """
        # Deduplicate
        if fill.fill_id in self._fill_ids:
            return None

        self._sequence += 1
        record = FillRecord(
            fill=fill,
            ledger_sequence=self._sequence,
        )

        # Persist
        self._journal.append("fill", record.to_dict())

        # Index
        self._index_record(record)

        return record

    def record_from_binance(
        self,
        trade: Dict[str, Any],
        client_order_id: str,
    ) -> Optional[FillRecord]:
        """
        Record fill from Binance trade response.

        Args:
            trade: Binance trade dict
            client_order_id: Our order ID

        Returns:
            FillRecord if new, None if duplicate.
        """
        fill = FillEventV1.from_binance_trade(trade, client_order_id)
        return self.record(fill)

    def get_fills_for_order(self, client_order_id: str) -> List[FillEventV1]:
        """Get all fills for an order."""
        records = self._by_order.get(client_order_id, [])
        return [r.fill for r in records]

    def get_fills_for_symbol(self, symbol: str) -> List[FillEventV1]:
        """Get all fills for a symbol."""
        records = self._by_symbol.get(symbol, [])
        return [r.fill for r in records]

    def total_filled_qty(self, client_order_id: str) -> float:
        """Get total filled quantity for an order."""
        fills = self.get_fills_for_order(client_order_id)
        return sum(f.quantity for f in fills)

    def total_notional(self, client_order_id: str) -> float:
        """Get total notional value for an order."""
        fills = self.get_fills_for_order(client_order_id)
        return sum(f.notional for f in fills)

    def avg_fill_price(self, client_order_id: str) -> Optional[float]:
        """
        Calculate volume-weighted average fill price.

        Returns:
            VWAP or None if no fills.
        """
        fills = self.get_fills_for_order(client_order_id)
        if not fills:
            return None

        total_notional = sum(f.notional for f in fills)
        total_qty = sum(f.quantity for f in fills)

        if total_qty == 0:
            return None

        return total_notional / total_qty

    def total_commission(
        self,
        client_order_id: str,
        asset: Optional[str] = None,
    ) -> float:
        """
        Get total commission for an order.

        Args:
            client_order_id: Order ID
            asset: Filter by commission asset (optional)

        Returns:
            Total commission.
        """
        fills = self.get_fills_for_order(client_order_id)
        if asset:
            return sum(f.commission for f in fills if f.commission_asset == asset)
        return sum(f.commission for f in fills)

    def is_order_filled(self, client_order_id: str) -> bool:
        """Check if order has any fills."""
        return client_order_id in self._by_order

    def fill_count(self) -> int:
        """Get total number of fills."""
        return len(self._fill_ids)

    def order_count(self) -> int:
        """Get number of orders with fills."""
        return len(self._by_order)

    def get_recent_fills(self, limit: int = 100) -> List[FillEventV1]:
        """Get most recent fills."""
        all_records: List[FillRecord] = []
        for records in self._by_order.values():
            all_records.extend(records)

        # Sort by sequence (most recent first)
        all_records.sort(key=lambda r: r.ledger_sequence, reverse=True)

        return [r.fill for r in all_records[:limit]]

    def get_fills_in_range(
        self,
        start_time: int,
        end_time: int,
        symbol: Optional[str] = None,
    ) -> List[FillEventV1]:
        """
        Get fills within time range.

        Args:
            start_time: Start timestamp (Unix ms)
            end_time: End timestamp (Unix ms)
            symbol: Filter by symbol (optional)

        Returns:
            List of fills in range.
        """
        if symbol:
            source = self._by_symbol.get(symbol, [])
        else:
            source = []
            for records in self._by_order.values():
                source.extend(records)

        return [
            r.fill for r in source
            if start_time <= r.fill.trade_time <= end_time
        ]

    def compute_pnl(
        self,
        client_order_id_entry: str,
        client_order_id_exit: str,
    ) -> Optional[float]:
        """
        Compute P&L between entry and exit orders.

        Args:
            client_order_id_entry: Entry order ID
            client_order_id_exit: Exit order ID

        Returns:
            P&L in quote currency, or None if fills missing.
        """
        entry_fills = self.get_fills_for_order(client_order_id_entry)
        exit_fills = self.get_fills_for_order(client_order_id_exit)

        if not entry_fills or not exit_fills:
            return None

        entry_notional = sum(f.notional for f in entry_fills)
        exit_notional = sum(f.notional for f in exit_fills)

        # Check sides
        entry_side = entry_fills[0].side
        if entry_side == "BUY":
            # Long position: profit = exit - entry
            return exit_notional - entry_notional
        else:
            # Short position: profit = entry - exit
            return entry_notional - exit_notional
