# minibot/trade_exec.py — пример безопасного места для исполнения ордеров
from __future__ import annotations
from typing import Optional, Dict, Any
from . import guards

def place_market_quote(exchange, symbol: str, quote_usdt: float, params: Optional[Dict[str,Any]]=None):
    """
    Безопасное размещение рыночного ордера на сумму quote_usdt (в USDT).
    Если LIVE запрещён — бросаем исключение до попытки обращения к бирже.
    """
    if not guards.can_execute_real_orders():
        raise RuntimeError("LIVE запрещён предохранителем (DRY-RUN/RUNSTOP/STRICT).")

    # здесь уже «по-настоящему»
    market = symbol if "/" in symbol else (symbol[:-4] + "/" + symbol[-4:])
    params = params or {}
    # пример CCXT-логики:
    # return exchange.create_order(market, type="market", side="buy", amount=None, params={"quoteOrderQty": quote_usdt, **params})
    return {"ok": True, "symbol": market, "quote": quote_usdt}
