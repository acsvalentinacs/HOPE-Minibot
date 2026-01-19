from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List

import ccxt

from .models import OrderSide, OrderStatus


@dataclass
class ExchangeOrder:
    order_id: str
    client_order_id: Optional[str]
    symbol: str
    side: str
    status: str
    price: float
    qty: float
    fills: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class BalanceSnapshot:
    total_usd: float
    free_usd: float
    raw: Dict[str, Any] = field(default_factory=dict)


class ExchangeClient:
    """
    Обёртка над ccxt.binance для DRY / TESTNET / LIVE режимов.

    DRY  -> полностью мок, без сети.
    TESTNET -> Binance Spot в sandbox-режиме.
    LIVE -> реальный Binance Spot.
    """

    def __init__(self, mode: str, secrets: Dict[str, str] | None = None) -> None:
        self.mode = (mode or "DRY").upper()
        self.secrets = secrets or {}
        self._exchange: Optional[ccxt.binance] = None
        self._order_counter = 0

    # ==========================
    #   ВНУТРЕННЕЕ
    # ==========================

    def _build_exchange(self) -> Optional[ccxt.binance]:
        """
        Инициализирует ccxt.binance при первом обращении.
        Для DRY вернёт None.
        """
        if self.mode == "DRY":
            return None

        api_key = (
            self.secrets.get("BINANCE_API_KEY") or self.secrets.get("API_KEY") or ""
        )
        api_secret = (
            self.secrets.get("BINANCE_API_SECRET")
            or self.secrets.get("API_SECRET")
            or ""
        )

        if not api_key or not api_secret:
            raise RuntimeError("Binance API keys not provided in secrets")

        exchange = ccxt.binance(
            {
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
                "options": {
                    "defaultType": "spot",
                },
            }
        )

        # Тестнет, если нужно
        if self.mode == "TESTNET":
            exchange.set_sandbox_mode(True)

        return exchange

    @property
    def exchange(self) -> Optional[ccxt.binance]:
        if self._exchange is None:
            self._exchange = self._build_exchange()
        return self._exchange

    # ==========================
    #   ПУБЛИЧНЫЕ МЕТОДЫ
    # ==========================

    def create_market_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        client_order_id: Optional[str] = None,
    ) -> ExchangeOrder:
        """
        Создаёт рыночный ордер.
        DRY -> мок без сети (цену берём условно 100.0).
        TESTNET/LIVE -> реальный ордер через ccxt.
        """
        side_upper = (side or "").upper()

        if self.mode == "DRY":
            self._order_counter += 1
            return ExchangeOrder(
                order_id=f"DRY-{int(time.time())}-{self._order_counter}",
                client_order_id=client_order_id,
                symbol=symbol,
                side=side_upper,
                status=OrderStatus.FILLED.value,
                price=100.0,
                qty=float(qty),
                fills=[],
            )

        ex = self.exchange
        if ex is None:
            raise RuntimeError(f"Exchange is not initialized for mode={self.mode}")

        # Приводим сторону к формату ccxt: 'buy' / 'sell'
        if side_upper in ("BUY", "LONG"):
            ccxt_side = "buy"
        elif side_upper in ("SELL", "SHORT", "CLOSE"):
            ccxt_side = "sell"
        else:
            raise ValueError(f"Unsupported side: {side}")

        # CCXT ожидает количество базовой валюты (BTC, ETH и т.п.)
        order = ex.create_order(
            symbol=symbol,
            type="market",
            side=ccxt_side,
            amount=float(qty),
        )

        fills = order.get("trades") or order.get("fills") or []
        price = float(order.get("average") or order.get("price") or 0.0)

        return ExchangeOrder(
            order_id=str(order.get("id")),
            client_order_id=client_order_id or order.get("clientOrderId"),
            symbol=symbol,
            side=side_upper,
            status=str(order.get("status") or OrderStatus.FILLED.value),
            price=price,
            qty=float(order.get("amount") or qty),
            fills=fills,
        )

    def fetch_balance(self) -> BalanceSnapshot:
        """
        Возвращает "эквивалент в USDT" как proxy для USD.
        DRY -> 1000 USDT.
        """
        if self.mode == "DRY":
            return BalanceSnapshot(
                total_usd=1000.0,
                free_usd=1000.0,
                raw={"mode": "DRY"},
            )

        ex = self.exchange
        if ex is None:
            raise RuntimeError(f"Exchange is not initialized for mode={self.mode}")

        bal = ex.fetch_balance()

        # Простейшая модель: смотрим на кошелёк USDT
        usdt = bal.get("USDT") or {}
        total = float(usdt.get("total") or 0.0)
        free = float(usdt.get("free") or 0.0)

        return BalanceSnapshot(
            total_usd=total,
            free_usd=free,
            raw=bal,
        )

    def fetch_order_book(self, symbol: str, limit: int = 5) -> Optional[Dict[str, Any]]:
        """
        Fetch orderbook for liquidity checks.

        Args:
            symbol: trading pair (e.g. "BTCUSDT")
            limit: depth levels to fetch (default 5)

        Returns:
            {"bids": [[price, qty], ...], "asks": [[price, qty], ...]}
            or None if unavailable

        DRY mode: returns mock orderbook with reasonable spread
        """
        if self.mode == "DRY":
            # Mock orderbook for testing (100 USDT mid-price, 0.1% spread)
            mid_price = 100.0
            spread_pct = 0.001  # 0.1%
            half_spread = mid_price * spread_pct / 2.0

            best_bid = mid_price - half_spread
            best_ask = mid_price + half_spread

            # Create 5 levels, each 0.05% away
            bids = []
            asks = []
            for i in range(limit):
                bid_price = best_bid - (i * 0.0005 * mid_price)
                ask_price = best_ask + (i * 0.0005 * mid_price)
                # Mock liquidity: $1000 per level
                qty_bid = 1000.0 / bid_price
                qty_ask = 1000.0 / ask_price
                bids.append([bid_price, qty_bid])
                asks.append([ask_price, qty_ask])

            return {
                "bids": bids,
                "asks": asks,
                "timestamp": int(time.time() * 1000),
            }

        ex = self.exchange
        if ex is None:
            return None

        try:
            orderbook = ex.fetch_order_book(symbol, limit=limit)
            return orderbook
        except Exception as e:
            # Non-fatal: liquidity guard will block if orderbook unavailable
            return None
