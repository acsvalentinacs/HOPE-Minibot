#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
execution_layer.py — каркас исполнения ордеров для HOPE minibot.

Этап 0: безопасная обёртка над ccxt:
- единое место, где создаются/мониторятся ордера;
- мягкая проверка minNotional и нормализация количества;
- структура данных для последующего усложнения (partial fill, chase, DRY/TESTNET и т.п.).
"""

from __future__ import annotations

import logging
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, Optional, Any

import ccxt  # предполагается, что уже установлен


# Базовые пути (на будущее, если захотим складывать свои логи исполнения)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


class OrderState(str, Enum):
    SIGNAL = "signal"
    PENDING_ENTRY = "pending_entry"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    MONITORING = "monitoring"
    PENDING_EXIT = "pending_exit"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    ERROR = "error"


class TradeMode(str, Enum):
    DRY = "dry"
    TESTNET = "testnet"
    LIVE_SAFE = "live_safe"
    LIVE_FULL = "live_full"


class ApiStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    RATE_LIMIT = "rate_limit"
    UNKNOWN = "unknown"


class CircuitStatus(str, Enum):
    NORMAL = "normal"
    PAUSED = "paused"
    HALTED = "halted"


@dataclass
class ExecutionOrder:
    """Снимок состояния конкретного ордера на бирже."""
    client_order_id: str
    symbol: str
    side: str           # "buy" / "sell"
    type: str           # "limit" / "market"

    state: OrderState

    requested_qty: float
    requested_price: Optional[float]

    exchange_order_id: Optional[str] = None
    filled_qty: float = 0.0
    avg_price: float = 0.0

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    last_error: Optional[str] = None
    raw_last_response: Optional[Dict[str, Any]] = None

    def mark_error(self, msg: str, response: Optional[Dict[str, Any]] = None) -> None:
        self.state = OrderState.ERROR
        self.last_error = msg
        self.raw_last_response = response
        self.updated_at = datetime.now(timezone.utc)


@dataclass
class ExecutionContext:
    """Контекст исполнения: биржа, режим, кэш рынков, счетчики ошибок."""
    exchange: ccxt.Exchange
    mode: TradeMode
    testnet: bool
    market_cache: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    error_count: int = 0
    last_error: Optional[str] = None
    last_error_at: Optional[datetime] = None
    circuit_status: CircuitStatus = CircuitStatus.NORMAL

    api_status: ApiStatus = ApiStatus.UNKNOWN

    def register_error(self, msg: str) -> None:
        self.error_count += 1
        self.last_error = msg
        self.last_error_at = datetime.now(timezone.utc)
        self.api_status = ApiStatus.ERROR


class ExecutionEngine:
    """
    Мягкий каркас над ccxt. На этом этапе:
    - не меняем общую бизнес-логику DRY/LIVE;
    - аккуратно добавляем:
        * кэш markets;
        * нормализацию количества;
        * проверку minNotional;
        * генерацию clientOrderId.
    """

    def __init__(self, ctx: ExecutionContext, logger: Optional[logging.Logger] = None) -> None:
        self.ctx = ctx
        self.log = logger or logging.getLogger("HOPE.ExecutionEngine")
        if not self.log.handlers:
            # На всякий случай, чтобы логи не терялись, если не настроен root-логгер
            handler = logging.FileHandler(LOG_DIR / "execution_engine.log", encoding="utf-8")
            formatter = logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            handler.setFormatter(formatter)
            self.log.addHandler(handler)
            self.log.setLevel(logging.INFO)

    # ------------------------------------------------------------------
    # Внутренние утилиты
    # ------------------------------------------------------------------
    def _ensure_market_cache_loaded(self) -> None:
        """Ленивая загрузка markets в кэш."""
        if self.ctx.market_cache:
            return

        try:
            self.log.info("ExecutionEngine: загрузка markets в кэш…")
            markets = self.ctx.exchange.load_markets()
            self.ctx.market_cache = markets or {}
            self.ctx.api_status = ApiStatus.OK
            self.log.info("ExecutionEngine: markets загружены (%d символов).", len(self.ctx.market_cache))
        except Exception as e:
            msg = f"Не удалось загрузить markets: {e}"
            self.log.error(msg, exc_info=True)
            self.ctx.register_error(msg)

    def _normalize_qty(self, symbol: str, qty: float) -> float:
        """Нормализуем количество согласно lot_step/precision."""
        try:
            return float(self.ctx.exchange.amount_to_precision(symbol, qty))
        except Exception:
            # В крайнем случае возвращаем как есть (не падаем).
            return float(qty)

    def _get_min_notional(self, symbol: str) -> Optional[float]:
        """Пытаемся вытащить minNotional из markets."""
        market = self.ctx.market_cache.get(symbol) or {}
        # Структура зависит от конкретной биржи, для Binance чаще всего:
        # market["limits"]["cost"]["min"] или market["info"]["minNotional"]
        limits = market.get("limits") or {}
        cost_limits = limits.get("cost") or {}
        min_cost = cost_limits.get("min")
        if min_cost is not None:
            return float(min_cost)

        info = market.get("info") or {}
        if "minNotional" in info:
            try:
                return float(info["minNotional"])
            except (TypeError, ValueError):
                return None

        return None

    def _check_min_notional(self, symbol: str, qty: float, price: float) -> bool:
        """Мягкая проверка minNotional (стоимость позиции)."""
        min_notional = self._get_min_notional(symbol)
        if min_notional is None:
            # Не смогли определить — НЕ блокируем, только логируем.
            self.log.debug(
                "ExecutionEngine: minNotional неизвестен для %s, пропускаем проверку (qty=%f, price=%f).",
                symbol, qty, price,
            )
            return True

        notional = abs(qty * price)
        if notional < min_notional:
            self.log.warning(
                "ExecutionEngine: ордер слишком маленький по notional (%s: %.8f < %.8f).",
                symbol, notional, min_notional,
            )
            return False
        return True

    def _generate_client_order_id(self, symbol: str, side: str) -> str:
        """
        Генерируем clientOrderId. Пока без строгой идемпотентности по сигналу,
        но уже стабильный формат.
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        raw = f"HOPE_{symbol}_{side}_{now_ms}"
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        # Binance ограничивает длину clientOrderId, поэтому режем:
        return digest[:20]

    # ------------------------------------------------------------------
    # Публичные методы: open / close / poll
    # ------------------------------------------------------------------
    def open_position(
        self,
        symbol: str,
        side: str,
        order_type: str,
        qty: float,
        price: Optional[float] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> ExecutionOrder:
        """
        Создаём ордер на вход в позицию.

        ВАЖНО: на этом этапе мы:
        - НЕ меняем существующую логику DRY/LIVE (она должна остаться в вызывающем коде);
        - просто оборачиваем create_order в единый слой;
        - добавляем:
            * market cache;
            * normalize qty;
            * проверку minNotional;
            * clientOrderId.
        """
        self._ensure_market_cache_loaded()

        params = dict(params or {})
        client_order_id = params.get("newClientOrderId") or self._generate_client_order_id(symbol, side)
        # Для Binance newClientOrderId, для других бирж может отличаться — но лишний параметр не ломает.
        params.setdefault("newClientOrderId", client_order_id)

        order = ExecutionOrder(
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            type=order_type,
            state=OrderState.PENDING_ENTRY,
            requested_qty=float(qty),
            requested_price=float(price) if price is not None else None,
        )

        try:
            if price is None and order_type.lower() == "limit":
                raise ValueError("Для limit-ордера требуется цена.")

            # Нормализуем количество
            adj_qty = self._normalize_qty(symbol, qty)

            # Если есть цена, проверяем minNotional
            eff_price = float(price) if price is not None else float(
                self.ctx.exchange.fetch_ticker(symbol)["last"]
            )

            if not self._check_min_notional(symbol, adj_qty, eff_price):
                msg = f"Notional меньше минимального, ордер не отправлен (symbol={symbol})."
                self.log.warning("ExecutionEngine: %s", msg)
                order.mark_error(msg)
                self.ctx.register_error(msg)
                return order

            self.log.info(
                "ExecutionEngine: создаём ордер %s %s %s qty=%.8f price=%s clientOrderId=%s",
                order_type, symbol, side, adj_qty, price, client_order_id,
            )

            # КРИТИЧЕСКИЙ МОМЕНТ: здесь мы НЕ трогаем логику DRY/LIVE.
            # Предполагаем, что вызывающий код уже решил, можно ли реально торговать.
            ccxt_order = self.ctx.exchange.create_order(
                symbol=symbol,
                type=order_type,
                side=side,
                amount=adj_qty,
                price=price,
                params=params,
            )

            order.exchange_order_id = ccxt_order.get("id")
            order.raw_last_response = ccxt_order
            order.updated_at = datetime.now(timezone.utc)

            filled = float(ccxt_order.get("filled") or 0.0)
            avg_price = float(
                ccxt_order.get("average")
                or ccxt_order.get("price")
                or (price if price is not None else eff_price)
            )

            order.filled_qty = filled
            order.avg_price = avg_price

            status = (ccxt_order.get("status") or "").lower()
            if status in ("closed", "filled"):
                order.state = OrderState.FILLED
            elif filled > 0:
                order.state = OrderState.PARTIALLY_FILLED
            else:
                order.state = OrderState.PENDING_ENTRY

            self.ctx.api_status = ApiStatus.OK
            return order

        except Exception as e:
            msg = f"Ошибка при создании ордера: {e}"
            self.log.error("ExecutionEngine: %s", msg, exc_info=True)
            order.mark_error(msg)
            self.ctx.register_error(msg)
            return order

    def close_position(
        self,
        symbol: str,
        side: str,
        order_type: str,
        qty: float,
        price: Optional[float] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> ExecutionOrder:
        """
        Ордер на выход из позиции (зеркально open_position).
        На этом этапе логика такая же, как в open_position.
        """
        # Для простоты сейчас используем ту же логику open_position,
        # в будущем можно разделить на вход/выход с разной политикой.
        opposite_state = OrderState.PENDING_EXIT

        order = self.open_position(
            symbol=symbol,
            side=side,
            order_type=order_type,
            qty=qty,
            price=price,
            params=params,
        )
        # Если ордер не в ошибке, переименуем state в PENDING_EXIT / CLOSED/...
        if order.state not in (OrderState.ERROR,):
            if order.state == OrderState.FILLED:
                order.state = OrderState.CLOSED
            else:
                order.state = opposite_state
        return order

    def poll_order(self, symbol: str, order: ExecutionOrder) -> ExecutionOrder:
        """
        Обновляем состояние ордера по данным биржи (fetch_order).
        """
        if not order.exchange_order_id:
            # Нечего опрашивать
            return order

        try:
            self._ensure_market_cache_loaded()
            ccxt_order = self.ctx.exchange.fetch_order(order.exchange_order_id, symbol)
            order.raw_last_response = ccxt_order
            order.updated_at = datetime.now(timezone.utc)

            filled = float(ccxt_order.get("filled") or 0.0)
            avg_price = float(
                ccxt_order.get("average")
                or ccxt_order.get("price")
                or (order.avg_price or order.requested_price or 0.0)
            )

            order.filled_qty = filled
            order.avg_price = avg_price

            status = (ccxt_order.get("status") or "").lower()
            if status in ("closed", "filled"):
                if order.state in (OrderState.PENDING_EXIT, OrderState.MONITORING):
                    order.state = OrderState.CLOSED
                else:
                    order.state = OrderState.FILLED
            elif status in ("canceled", "cancelled"):
                if filled > 0:
                    # Частично исполнен и отменён — на будущее тут можно
                    # докрутить особую логику.
                    order.state = OrderState.PARTIALLY_FILLED
                else:
                    order.state = OrderState.CANCELLED
            else:
                if filled > 0 and order.state == OrderState.PENDING_ENTRY:
                    order.state = OrderState.PARTIALLY_FILLED

            self.ctx.api_status = ApiStatus.OK
            return order

        except Exception as e:
            msg = f"Ошибка при обновлении ордера {order.exchange_order_id}: {e}"
            self.log.error("ExecutionEngine: %s", msg, exc_info=True)
            order.mark_error(msg)
            self.ctx.register_error(msg)
            return order
