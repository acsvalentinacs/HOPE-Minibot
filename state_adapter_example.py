# minibot/state_adapter_example.py
"""
Пример адаптации под твой minibot.

Подключение:
    from minibot.state_hooks import StatePump, log_order

    pump = StatePump(fetch_positions, fetch_balance, interval_sec=10)
    pump.start()   # после инициализации бота/биржи

    # В местах создания/смены статуса ордера:
    log_order(symbol, side, ord_type, qty, price, status, order_id=..., client_id=..., fees=..., avgPrice=...)

Здесь приведены «болванки» fetch_* — замени телом получения реальных данных.
"""

from __future__ import annotations
import asyncio
from typing import Dict, Any, List

# ==== твои реальные импорты здесь ====
# from .exchange import get_positions, get_balance, last_fills, ...

async def fetch_positions() -> List[Dict[str, Any]]:
    # TODO: верни список твоих позиций
    # Пример структуры:
    return [{
        "symbol": "BTCUSDT",
        "side": "LONG",
        "qty": 0.0123,
        "entry_price": 98765.4,
        "unrealized_pnl": 3.21,
    }]

async def fetch_balance() -> Dict[str, Any]:
    # Вариант 1: totals/free
    return {
        "total": {"USDT": 1234.56, "BTC": 0.02},
        "free":  {"USDT": 250.00,  "BTC": 0.01},
    }
    # Вариант 2: список assets — тоже ок:
    # return {"assets":[{"asset":"USDT","free":250.0,"total":1234.56}]}

async def main_demo():
    from .state_hooks import StatePump, log_order
    pump = StatePump(fetch_positions, fetch_balance, interval_sec=5)
    pump.start()
    print("[adapter-demo] StatePump started; writing demo order in 3s...")
    await asyncio.sleep(3)
    log_order("BTCUSDT", "BUY", "MARKET", 25.0, 100_000.0, "filled", demo=True)
    await asyncio.sleep(15)

if __name__ == "__main__":
    asyncio.run(main_demo())
