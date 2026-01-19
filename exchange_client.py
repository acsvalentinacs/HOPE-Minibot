#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
minibot.exchange_client
Обёртка над ccxt для ExecutionEngine v5.

Задачи:
- DRY / TESTNET / LIVE режимы
- проверка min_notional и precision
- логирование запросов/ответов в logs/exchange_v5.log
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Tuple

try:
    import ccxt  # type: ignore
except Exception:  # pragma: no cover
    ccxt = None  # DRY mode может жить и без ccxt


EXCHANGE_LOG_PATH = os.path.join("logs", "exchange_v5.log")
SECURITY_LOG_PATH = os.path.join("logs", "security_v5.jsonl")


class ExchangeMode(str, Enum):
    DRY = "DRY"
    TESTNET = "TESTNET"
    LIVE = "LIVE"


@dataclass
class SymbolLimits:
    min_notional: float
    min_qty: float
    step_size: float
    price_precision: int
    qty_precision: int


class ExchangeClient:
    def __init__(
        self,
        mode: ExchangeMode,
        *,
        api_key: Optional[str],
        api_secret: Optional[str],
        testnet_flag: bool = False,
        log_exchange_calls: bool = True,
    ) -> None:
        self.mode = mode
        self.log_exchange_calls = log_exchange_calls
        self._markets: Dict[str, Any] = {}

        os.makedirs("logs", exist_ok=True)

        if self.mode == ExchangeMode.DRY:
            self._exchange = None
            return

        if ccxt is None:
            raise RuntimeError("ccxt не установлен, а режим не DRY")

        self._exchange = ccxt.binance(
            {
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
                "options": {"defaultType": "spot"},
            }
        )

        # sandbox, если TESTNET или явно задан testnet_flag
        if self.mode == ExchangeMode.TESTNET or testnet_flag:
            self._exchange.set_sandbox_mode(True)

        self._markets = self._exchange.load_markets()

        self._log_event(
            "init",
            {
                "mode": self.mode.value,
                "sandbox": self._exchange.options.get("sandboxMode", False),
            },
        )

    # ---------- LOGGING ----------

    def _log_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        if not self.log_exchange_calls:
            return
        rec = {
            "ts": time.time(),
            "type": event_type,
            **self._sanitize(payload),
        }
        with open(EXCHANGE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _log_security(self, msg: str, extra: Optional[Dict[str, Any]] = None) -> None:
        rec = {"ts": time.time(), "msg": msg, "extra": extra or {}}
        with open(SECURITY_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    @staticmethod
    def _sanitize(data: Dict[str, Any]) -> Dict[str, Any]:
        d = dict(data)
        for key in list(d.keys()):
            lk = key.lower()
            if any(
                bad in lk
                for bad in ["key", "secret", "token", "password", "authorization", "apikey"]
            ):
                d[key] = "***REDACTED***"
        return d

    # ---------- PUBLIC API ----------

    def fetch_price(self, symbol: str, *, fallback_price: Optional[float] = None) -> float:
        """Текущая цена. В DRY режиме может использоваться fallback_price."""
        if self.mode == ExchangeMode.DRY:
            if fallback_price is None:
                raise RuntimeError("DRY mode: price required (fallback_price is None)")
            return float(fallback_price)

        self._log_event("request", {"method": "fetch_ticker", "symbol": symbol})
        ticker = self._exchange.fetch_ticker(symbol)
        self._log_event("response", {"method": "fetch_ticker", "symbol": symbol})
        return float(ticker["last"])

    def get_symbol_limits(self, symbol: str) -> SymbolLimits:
        """min_notional, min_qty, step_size, precision по символу."""
        sym = symbol.upper()
        m = self._markets.get(sym)
        if not m:
            if self._exchange:
                self._markets = self._exchange.load_markets()
                m = self._markets.get(sym)
        if not m:
            # Очень консервативные значения по умолчанию
            return SymbolLimits(
                min_notional=10.0,
                min_qty=1e-5,
                step_size=1e-5,
                price_precision=8,
                qty_precision=8,
            )

        limits = m.get("limits", {})
        amount = limits.get("amount", {}) if limits else {}
        cost = limits.get("cost", {}) if limits else {}

        min_qty = float(amount.get("min", 0.0) or 0.0)
        min_notional = float(cost.get("min", 0.0) or 0.0)

        precision = m.get("precision", {})
        price_prec = int(precision.get("price", 8))
        qty_prec = int(precision.get("amount", 8))

        # Из шага размера (filters)
        step_size = min_qty or math.pow(10, -qty_prec)

        return SymbolLimits(
            min_notional=min_notional or 0.0,
            min_qty=min_qty or 0.0,
            step_size=step_size,
            price_precision=price_prec,
            qty_precision=qty_prec,
        )

    def create_market_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        *,
        price_hint: Optional[float] = None,
        max_retries: int = 3,
    ) -> Tuple[float, float]:
        """
        Создаёт рыночный ордер.

        Возвращает (exec_price, filled_qty).
        В DRY режиме просто возвращает (price_hint, qty).
        """
        side = side.upper()
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side must be BUY/SELL, got {side}")

        if self.mode == ExchangeMode.DRY:
            if price_hint is None:
                raise RuntimeError("DRY mode: price_hint required for create_market_order")
            return float(price_hint), float(qty)

        last_err: Optional[str] = None
        for attempt in range(1, max_retries + 1):
            try:
                self._log_event(
                    "request",
                    {"method": "create_order", "symbol": symbol, "side": side, "amount": qty},
                )
                order = self._exchange.create_market_order(symbol, side, qty)
                self._log_event(
                    "response",
                    {
                        "method": "create_order",
                        "symbol": symbol,
                        "side": side,
                        "amount": qty,
                        "status": order.get("status"),
                    },
                )
                filled = float(order.get("filled") or order.get("amount") or qty)
                exec_price = float(
                    order.get("average")
                    or order.get("price")
                    or price_hint
                    or self.fetch_price(symbol, fallback_price=price_hint)
                )
                return exec_price, filled
            except Exception as e:  # pragma: no cover
                last_err = str(e)
                self._log_event(
                    "error",
                    {"method": "create_order", "symbol": symbol, "side": side, "error": last_err},
                )
                time.sleep(1.0)

        msg = f"Failed to create market order {symbol} {side} qty={qty}: {last_err}"
        self._log_security(msg, {"symbol": symbol, "side": side, "qty": qty})
        raise RuntimeError(msg)
