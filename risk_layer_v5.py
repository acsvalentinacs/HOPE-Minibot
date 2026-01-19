#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
risk_layer_v5.py — простой слой контроля рисков для HOPE v5.

Задачи:
- ограничить риск на сделку (min/max);
- ограничить количество одновременно открытых позиций;
- ограничить дневной убыток;
- вести внутренний счётчик дневного PnL.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from datetime import date, datetime
from typing import Dict, Tuple

logger = logging.getLogger(__name__)


@dataclass
class RiskConfigV5:
    """Настройки лимитов риска."""
    max_open_positions: int = 5
    max_risk_per_trade_usd: float = 20.0
    min_risk_per_trade_usd: float = 5.0
    max_daily_loss_usd: float = 50.0  # при -50$ бот останавливает открытия


class RiskLayerV5:
    """
    Лёгкий класс-обёртка над лимитами.

    Не знает ничего о Binance/ордерах — только числа:
    - сколько уже позиций открыто;
    - какой риск на новую сделку;
    - какой текущий дневной PnL.
    """

    def __init__(self, config: RiskConfigV5 | None = None) -> None:
        self.config = config or RiskConfigV5()
        self._daily_pnl: float = 0.0
        self._current_date: date | None = None

    # ───────────────────────── ВНУТРЕННИЕ МЕТОДЫ ───────────────────────── #

    def _ensure_current_day(self) -> None:
        """Сброс дневного счётчика при смене UTC-дня."""
        today = datetime.utcnow().date()
        if self._current_date != today:
            self._current_date = today
            self._daily_pnl = 0.0
            logger.info(
                "RiskLayerV5: новый день %s, дневной PnL обнулён",
                today.isoformat(),
            )

    # ───────────────────────── ПУБЛИЧНЫЕ МЕТОДЫ ───────────────────────── #

    def can_open(
        self,
        *,
        symbol: str,
        side: str,
        risk_usd: float,
        open_positions: int,
    ) -> Tuple[bool, str]:
        """
        Проверка возможности открыть новую позицию.

        Возвращает (ok, reason):
        - ok = True  → можно открывать;
        - ok = False → reason содержит причину отказа.
        """
        self._ensure_current_day()

        # 1) Минимальный риск
        if risk_usd < self.config.min_risk_per_trade_usd:
            msg = (
                f"Риск {risk_usd:.2f} USDT ниже минимального "
                f"{self.config.min_risk_per_trade_usd:.2f} USDT"
            )
            logger.warning("RiskLayerV5: %s", msg)
            return False, msg

        # 2) Максимальный риск на сделку
        if risk_usd > self.config.max_risk_per_trade_usd:
            msg = (
                f"Риск {risk_usd:.2f} USDT превышает лимит "
                f"{self.config.max_risk_per_trade_usd:.2f} USDT"
            )
            logger.warning("RiskLayerV5: %s", msg)
            return False, msg

        # 3) Лимит по количеству открытых позиций
        if open_positions >= self.config.max_open_positions:
            msg = (
                "Достигнут лимит открытых позиций "
                f"{open_positions}/{self.config.max_open_positions}"
            )
            logger.warning("RiskLayerV5: %s", msg)
            return False, msg

        # 4) Лимит по дневному убытку
        if self._daily_pnl <= -self.config.max_daily_loss_usd:
            msg = (
                f"Дневной убыток {self._daily_pnl:.2f} USDT "
                f"превысил лимит {self.config.max_daily_loss_usd:.2f} USDT"
            )
            logger.error("RiskLayerV5: %s", msg)
            return False, msg

        logger.info(
            "RiskLayerV5: разрешено открыть %s %s, риск %.2f USDT "
            "(открыто: %s/%s, дневной PnL=%.2f)",
            symbol,
            side,
            risk_usd,
            open_positions,
            self.config.max_open_positions,
            self._daily_pnl,
        )
        return True, "OK"

    def register_closed_trade(self, *, pnl: float) -> None:
        """
        Сообщить слою о закрытой сделке.

        PnL (плюс или минус) суммируется в дневной счётчик.
        """
        self._ensure_current_day()
        self._daily_pnl += pnl
        logger.info(
            "RiskLayerV5: закрыта сделка, PnL=%.2f USDT, дневной=%.2f USDT",
            pnl,
            self._daily_pnl,
        )

    def status(self) -> Dict[str, object]:
        """Краткий статус для /diag или логов."""
        self._ensure_current_day()
        return {
            "date": self._current_date.isoformat() if self._current_date else None,
            "daily_pnl": round(self._daily_pnl, 2),
            "limits": asdict(self.config),
            "can_trade": self._daily_pnl > -self.config.max_daily_loss_usd,
        }
