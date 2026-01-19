# minibot/state_hooks.py
from __future__ import annotations
import asyncio, time
from typing import Callable, Awaitable, Dict, Any, List, Optional

from .state_writer import write_positions, write_balance, append_order, now_ts

FetchPositions = Callable[[], Awaitable[List[Dict[str, Any]]]] | Callable[[], List[Dict[str, Any]]]
FetchBalance   = Callable[[], Awaitable[Dict[str, Any]]]        | Callable[[], Dict[str, Any]]

class StatePump:
    def __init__(self, fetch_positions: FetchPositions, fetch_balance: FetchBalance,
                 interval_sec: int = 10):
        self.fetch_positions = fetch_positions
        self.fetch_balance   = fetch_balance
        self.interval_sec    = interval_sec
        self._task: Optional[asyncio.Task] = None
        self._stopped = False

    async def _maybe_await(self, fn):
        v = fn()
        if asyncio.iscoroutine(v):
            return await v
        return v

    async def _loop(self):
        while not self._stopped:
            try:
                pos = await self._maybe_await(self.fetch_positions)
                if not isinstance(pos, list):
                    pos = []
                # нормализуем минимально ожидаемые ключи, но ничего не ломаем
                norm = []
                for r in pos:
                    if not isinstance(r, dict):
                        continue
                    norm.append({
                        "symbol":          r.get("symbol") or r.get("sym") or "—",
                        "side":            (r.get("side") or r.get("direction") or "").upper(),
                        "qty":             r.get("qty") or r.get("quantity") or r.get("size") or 0,
                        "entry_price":     r.get("entry_price") or r.get("avg_price") or r.get("entry") or 0,
                        "unrealized_pnl":  r.get("u_pnl") or r.get("unrealized_pnl") or r.get("upnl") or 0,
                        **r,  # сохраним остальное как есть
                    })
                write_positions(norm)
            except Exception:
                # тихо, снапшоты побочные
                pass

            try:
                bal = await self._maybe_await(self.fetch_balance)
                if not isinstance(bal, dict):
                    bal = {}
                write_balance(bal)
            except Exception:
                pass

            await asyncio.sleep(self.interval_sec)

    def start(self):
        if self._task is None:
            self._stopped = False
            self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._stopped = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass
            self._task = None


def log_order(symbol: str, side: str, ord_type: str, qty, price, status: str, **extra):
    """
    Вставляй вызов в момент ИЗМЕНЕНИЯ статуса ордера (new/filled/partial/canceled/rejected).
    Все значения пишем «как знаем» — tg-бот уже умеет читать ровно такие поля.
    """
    payload = {
        "ts": now_ts(),
        "symbol": symbol,
        "side":   (side or "").upper(),
        "type":   ord_type,
        "qty":    qty,
        "price":  price,
        "status": status,
    }
    payload.update(extra or {})
    append_order(payload)
