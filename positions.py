from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

@dataclass
class Position:
    symbol: str
    qty: float
    entry_price: float
    entry_ts: datetime
    entry_atr: float | None = None
    tp_price: float | None = None
    sl_price: float | None = None

    def pnl(self, exit_price: float) -> tuple[float, float]:
        notional = self.qty * self.entry_price
        pnl_usd = (exit_price - self.entry_price) * self.qty
        pnl_pct = (pnl_usd / notional) * 100.0 if notional > 0 else 0.0
        return pnl_usd, pnl_pct

class PositionBook:
    def __init__(self):
        self._by_symbol: Dict[str, Position] = {}

    def is_open(self, symbol: str) -> bool:
        return symbol in self._by_symbol

    def open(self, symbol: str, qty: float, entry_price: float,
             entry_atr: float | None, tp: float | None, sl: float | None) -> Position:
        p = Position(
            symbol=symbol,
            qty=float(qty),
            entry_price=float(entry_price),
            entry_ts=datetime.now(timezone.utc),
            entry_atr=entry_atr,
            tp_price=tp,
            sl_price=sl,
        )
        self._by_symbol[symbol] = p
        return p

    def get(self, symbol: str) -> Optional[Position]:
        return self._by_symbol.get(symbol)

    def close(self, symbol: str, exit_price: float) -> Optional[Tuple[Position, float, float]]:
        p = self._by_symbol.pop(symbol, None)
        if not p:
            return None
        pnl_usd, pnl_pct = p.pnl(float(exit_price))
        return p, pnl_usd, pnl_pct

    def items(self):
        return list(self._by_symbol.items())

    def count(self) -> int:
        return len(self._by_symbol)
