from __future__ import annotations
from dataclasses import dataclass
from typing import Tuple
import logging

from minibot.market_ccxt import CcxtBinanceSource

@dataclass
class OrderResult:
    ok: bool
    reason: str
    qty_used: float
    price_used: float

class OrderExecutor:
    """
    Исполнение: округления, min_amount/min_cost (через sandbox), DRY vs LIVE.
    Теперь с проверкой свободного баланса перед LIVE-ордером.
    """
    def __init__(self, ds: CcxtBinanceSource, dry_run: bool, logger: logging.Logger):
        self.ds = ds
        self.dry_run = dry_run
        self.log = logger

    # ---------- «песочница» под лимитный BUY (используем и как калькулятор)
    def prepare_limit_buy(self, symbol: str, expected_price: float, notional_usd: float) -> Tuple[bool, str, float, float]:
        if expected_price <= 0 or notional_usd <= 0:
            return False, "bad expected_price/notional", 0.0, 0.0
        qty_raw = notional_usd / expected_price
        ok, reason = self.ds.sandbox_check_order(symbol, "buy", qty_raw, expected_price)
        if not ok:
            return False, reason, 0.0, 0.0
        qty_q = self.ds.amount_to_precision(symbol, qty_raw)
        price_q = self.ds.price_to_precision(symbol, expected_price)
        return True, "ok", float(qty_q), float(price_q)

    # ---------- LIVE: MARKET BUY по quoteOrderQty (в USDT), с проверкой баланса quote
    def market_buy_quote(self, symbol: str, quote_usd: float) -> Tuple[bool, str, float, float]:
        """
        Returns (ok, reason, filled_qty, avg_price). Перед отправкой — проверяем свободный quote.
        """
        if quote_usd <= 0:
            return False, "bad quote_usd", 0.0, 0.0

        # баланс quote (например, USDT)
        free_quote = self.ds.free_quote_for_symbol(symbol)
        # маленький запас под комиссию/округления
        need_quote = quote_usd
        if free_quote + 1e-9 < need_quote:
            return False, f"insufficient quote: have={free_quote:.8f} need={need_quote:.8f}", 0.0, 0.0

        sym = self.ds._normalize(symbol)
        try:
            order = self.ds.ex.create_order(sym, type="market", side="buy",
                                            amount=None, price=None,
                                            params={"quoteOrderQty": str(quote_usd)})
        except Exception as e:
            return False, f"create_order buy failed: {e!r}", 0.0, 0.0

        # короткий лог info для пост-аналитики (без чувствительных данных)
        try:
            info = order.get("info")
            if info:
                self.log.info("BUY info: %s", str(info)[:300])
        except Exception:
            pass

        try:
            filled = float(order.get("filled") or 0.0)
            average = float(order.get("average") or 0.0)
        except Exception:
            filled, average = 0.0, 0.0

        if average <= 0.0:
            px = self.ds.get_price(symbol)
            if px: average = float(px)
        if filled <= 0.0 or average <= 0.0:
            return False, f"no fill info (filled={filled}, avg={average})", filled, average
        return True, "ok", filled, average

    # ---------- LIVE: MARKET SELL по qty, с проверкой баланса base
    def market_sell(self, symbol: str, qty: float) -> Tuple[bool, str, float, float]:
        """
        Returns (ok, reason, used_qty, avg_price). Проверяем свободный base перед отправкой.
        """
        if qty <= 0:
            return False, "bad qty", 0.0, 0.0

        free_base = self.ds.free_base_for_symbol(symbol)
        if free_base + 1e-12 < qty:
            return False, f"insufficient base: have={free_base:.12f} need={qty:.12f}", 0.0, 0.0

        sym = self.ds._normalize(symbol)
        qty_q = self.ds.amount_to_precision(sym, qty)
        if qty_q <= 0:
            return False, "qty rounds to zero", 0.0, 0.0
        try:
            order = self.ds.ex.create_order(sym, type="market", side="sell", amount=qty_q)
        except Exception as e:
            return False, f"create_order sell failed: {e!r}", 0.0, 0.0

        try:
            info = order.get("info")
            if info:
                self.log.info("SELL info: %s", str(info)[:300])
        except Exception:
            pass

        try:
            filled = float(order.get("filled") or 0.0)
            average = float(order.get("average") or 0.0)
        except Exception:
            filled, average = 0.0, 0.0

        if average <= 0.0:
            px = self.ds.get_price(symbol)
            if px: average = float(px)
        if filled <= 0.0 or average <= 0.0:
            return False, f"no fill info (filled={filled}, avg={average})", filled, average
        return True, "ok", filled, average

    # ---------- DRY-RUN универсалка (оставлена для совместимости)
    def place_or_paper(self, symbol: str, qty: float, price: float) -> OrderResult:
        if self.dry_run:
            self.log.info("🧪 DRY-RUN BUY %s qty=%.12f price=%.12f", symbol, qty, price)
            return OrderResult(ok=True, reason="dry-run", qty_used=qty, price_used=price)
        self.log.warning("REAL execution not implemented here (use market_buy_quote/market_sell)")
        return OrderResult(ok=False, reason="use market_* methods", qty_used=0.0, price_used=0.0)
