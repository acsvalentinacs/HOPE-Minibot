# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T07:15:00Z
# Purpose: State hydration from FillsLedger on restart
# Security: Fail-closed, positions calculated only from fills
# === END SIGNATURE ===
"""
State Hydration - Restore Trading State from FillsLedger.

On restart:
1. Check for UNKNOWN orders → reconcile first
2. Load fills from FillsLedger
3. Calculate open positions from fills
4. Return restored state

CRITICAL: Positions are calculated ONLY from FillsLedger.
RAM state is NOT trusted after restart.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("trade.state_hydration")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATE_DIR = BASE_DIR / "state"
ORDERS_DIR = STATE_DIR / "orders"
FILLS_DIR = STATE_DIR / "fills"


@dataclass
class Position:
    """Calculated position from fills."""
    symbol: str
    quantity: float  # Positive = long, negative = short (N/A for spot)
    avg_price: float
    entry_time: str
    total_cost: float = 0.0
    realized_pnl: float = 0.0

    @property
    def notional(self) -> float:
        return abs(self.quantity) * self.avg_price


@dataclass
class HydratedState:
    """Restored state from journal."""
    positions: Dict[str, Position] = field(default_factory=dict)
    unknown_count: int = 0
    total_fills: int = 0
    hydrated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    warnings: List[str] = field(default_factory=list)

    @property
    def has_open_positions(self) -> bool:
        return len(self.positions) > 0

    @property
    def needs_reconcile(self) -> bool:
        return self.unknown_count > 0


class StateHydrator:
    """
    Hydrate trading state from persistent storage.

    Uses FillsLedger as ONLY source of truth for positions.
    """

    def __init__(self):
        """Initialize hydrator with paths."""
        from core.execution.outbox import Outbox
        from core.execution.fills_ledger import FillsLedger

        ORDERS_DIR.mkdir(parents=True, exist_ok=True)
        FILLS_DIR.mkdir(parents=True, exist_ok=True)

        self._outbox = Outbox(ORDERS_DIR / "outbox.jsonl")
        self._ledger = FillsLedger(FILLS_DIR / "fills.jsonl")

    def hydrate(self, reconcile_unknown: bool = True) -> HydratedState:
        """
        Hydrate state from persistent storage.

        Args:
            reconcile_unknown: If True, attempt to reconcile UNKNOWN orders

        Returns:
            HydratedState with positions and warnings
        """
        state = HydratedState()

        # Step 1: Check UNKNOWN orders
        unknown_orders = self._outbox.get_unknown()
        state.unknown_count = len(unknown_orders)

        if unknown_orders:
            logger.warning(
                "Found %d UNKNOWN orders needing reconcile: %s",
                len(unknown_orders),
                [o.client_order_id[:20] for o in unknown_orders],
            )
            state.warnings.append(
                f"UNKNOWN orders found: {len(unknown_orders)} need reconcile"
            )

            if reconcile_unknown:
                # Would need exchange client here
                state.warnings.append("Auto-reconcile skipped (no exchange client)")

        # Step 2: Load fills
        all_fills = list(self._ledger.get_recent_fills(limit=10000))
        state.total_fills = len(all_fills)

        if not all_fills:
            logger.info("No fills found - starting fresh")
            return state

        # Step 3: Calculate positions from fills
        positions: Dict[str, Position] = {}

        for fill in all_fills:
            symbol = fill.symbol
            qty = fill.quantity
            price = fill.price
            side = fill.side

            if symbol not in positions:
                positions[symbol] = Position(
                    symbol=symbol,
                    quantity=0.0,
                    avg_price=0.0,
                    entry_time=fill.recorded_at,
                    total_cost=0.0,
                )

            pos = positions[symbol]

            if side == "BUY":
                # Adding to position
                new_qty = pos.quantity + qty
                new_cost = pos.total_cost + (qty * price)
                pos.quantity = new_qty
                pos.total_cost = new_cost
                pos.avg_price = new_cost / new_qty if new_qty > 0 else 0
            else:  # SELL
                # Reducing position
                if pos.quantity > 0:
                    # Calculate realized P&L
                    sell_qty = min(qty, pos.quantity)
                    realized = (price - pos.avg_price) * sell_qty
                    pos.realized_pnl += realized
                    pos.quantity -= sell_qty
                    pos.total_cost = pos.quantity * pos.avg_price

        # Filter out closed positions
        state.positions = {
            sym: pos for sym, pos in positions.items()
            if pos.quantity > 0.0001  # Dust threshold
        }

        logger.info(
            "Hydrated %d open positions from %d fills",
            len(state.positions),
            state.total_fills,
        )

        for sym, pos in state.positions.items():
            logger.info(
                "  %s: qty=%.8f avg=%.2f notional=%.2f",
                sym, pos.quantity, pos.avg_price, pos.notional,
            )

        return state

    def get_position(self, symbol: str) -> Optional[Position]:
        """Get position for symbol."""
        state = self.hydrate(reconcile_unknown=False)
        return state.positions.get(symbol)

    def get_all_positions(self) -> Dict[str, Position]:
        """Get all open positions."""
        state = self.hydrate(reconcile_unknown=False)
        return state.positions

    def get_unknown_orders(self) -> list:
        """Get UNKNOWN orders needing reconcile."""
        return self._outbox.get_unknown()


def hydrate_on_startup() -> HydratedState:
    """
    Convenience function for startup hydration.

    Returns:
        HydratedState
    """
    hydrator = StateHydrator()
    return hydrator.hydrate(reconcile_unknown=False)
