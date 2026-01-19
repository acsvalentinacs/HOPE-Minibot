#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Execution Layer v2 for HOPE Engine v5.

Задача:
- Принимать TradeSignal;
- Решать, можно ли открыть/закрыть позицию;
- В DRY-режиме вести локальный учёт позиций (exec_positions_v5.json);
- Давать агрегированный EngineStatus для health_v5.json.

На этом этапе БЕЗ реальных ордеров на бирже.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .models import (
    EngineMode,
    TradeSide,
    PositionState,
    TradeSignal,
    PositionInfo,
    ExecutionResult,
    EngineStatus,
)

from .risk_manager import RiskManager
from .storage import PositionStorage

log = logging.getLogger(__name__)


# ======================================================================
# Конфиг уровня исполнения
# ======================================================================

@dataclass
class ExecutionConfig:
    mode: EngineMode
    max_open_positions: int = 0
    max_portfolio_load_pct: float = 0.0
    max_risk_per_symbol_pct_of_daily_stop: float = 0.0
    max_spread_pct: float = 0.0
    max_slippage_pct: float = 0.0
    account_currency: str = "USDT"


# ======================================================================
# ExecutionEngine — ядро исполнителя (DRY-версия)
# ======================================================================

class ExecutionEngine:
    """
    DRY-реализация:
    - НЕ ходит на биржу;
    - Оперирует только локальными позициями и RiskManager.
    """

    def __init__(
        self,
        config: ExecutionConfig,
        risk: RiskManager,
        storage: PositionStorage,
    ) -> None:
        self.config = config
        self.risk = risk
        self.storage = storage

        self._positions: List[PositionInfo] = self.storage.load_positions()
        self._last_error: Optional[str] = None

        # Псевдо-equity для DRY: считаем, что у нас всегда 1000 USDT
        self._dry_equity_usd: float = 1000.0

        # Флаг паузы торговли (STOP.flag)
        self._trading_paused: bool = False

        log.info(
            "ExecutionEngine инициализирован (mode=%s, positions=%d)",
            self.config.mode.value,
            len(self._positions),
        )

    # ------------------------------------------------------------------
    # Вспомогательные свойства
    # ------------------------------------------------------------------
    @property
    def positions(self) -> List[PositionInfo]:
        return list(self._positions)

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def trading_paused(self) -> bool:
        return self._trading_paused

    def set_trading_paused(self, paused: bool) -> None:
        if paused != self._trading_paused:
            log.warning("Trading paused set to %s", paused)
        self._trading_paused = paused

    # ------------------------------------------------------------------
    # Основные операции
    # ------------------------------------------------------------------
    def open_position(self, signal: TradeSignal) -> ExecutionResult:
        """
        Открытие позиции на основе сигнала.
        В DRY-режиме — просто создаём PositionInfo и сохраняем в storage.
        """
        try:
            if self._trading_paused:
                msg = "Trading paused (STOP.flag active)"
                log.warning(
                    "%s — игнорируем сигнал на открытие %s %s",
                    msg,
                    signal.side.value,
                    signal.symbol,
                )
                return ExecutionResult(success=False, reason=msg)

            # Проверяем риск-лимиты
            ok, reason, allowed_usd = self.risk.can_open(
                signal=signal,
                current_positions=self._positions,
                equity_usd=self._dry_equity_usd,
            )
            if not ok:
                log.warning(
                    "Отказ в открытии позиции по %s: %s (allowed_usd=%.2f)",
                    signal.symbol,
                    reason,
                    allowed_usd,
                )
                return ExecutionResult(success=False, reason=reason)

            if allowed_usd <= 0:
                msg = "Allowed size is 0"
                log.warning("Отказ в открытии позиции по %s: %s", signal.symbol, msg)
                return ExecutionResult(success=False, reason=msg)

            price = signal.signal_price if signal.signal_price > 0 else 1.0
            qty = allowed_usd / price

            now_ts = time.time()
            pos = PositionInfo(
                symbol=signal.symbol,
                side=signal.side,
                qty=qty,
                avg_price=price,
                size_usd=allowed_usd,
                state=PositionState.OPEN,
                created_at=now_ts,
                updated_at=now_ts,
                tags={
                    "source": signal.source,
                    "signal_id": signal.signal_id,
                },
            )

            self._positions.append(pos)
            self.storage.save_positions(self._positions)

            log.info(
                "DRY OPEN %s %s qty=%.6f @ %.4f (size=%.2f USDT)",
                signal.side.value,
                signal.symbol,
                qty,
                price,
                allowed_usd,
            )

            return ExecutionResult(
                success=True,
                reason="DRY position opened",
                position=pos,
                order_ids=[],
            )
        except Exception as e:
            self._last_error = str(e)
            log.exception("Ошибка при open_position(%s): %s", signal.symbol, e)
            return ExecutionResult(success=False, reason=f"Exception: {e}")

    def close_position(
        self,
        symbol: str,
        reason: str = "",
        close_price: Optional[float] = None,
    ) -> ExecutionResult:
        """
        Закрытие позиции по символу.
        В DRY-режиме:
          - если передана close_price, считаем PnL;
          - если нет, считаем PnL = 0 (старое поведение).
        """
        try:
            idx = None
            for i, p in enumerate(self._positions):
                if p.symbol == symbol and p.state == PositionState.OPEN:
                    idx = i
                    break

            if idx is None:
                msg = f"No open position for {symbol}"
                log.warning(msg)
                return ExecutionResult(success=False, reason=msg)

            pos = self._positions[idx]

            # Цена закрытия: если не передали — берём цену входа (PnL=0)
            exit_price = close_price if (close_price is not None and close_price > 0) else pos.avg_price

            if pos.side == TradeSide.LONG:
                pnl_usd = (exit_price - pos.avg_price) * pos.qty
            elif pos.side == TradeSide.SHORT:
                pnl_usd = (pos.avg_price - exit_price) * pos.qty
            else:
                pnl_usd = 0.0

            self.risk.notify_trade_result(pnl_usd)

            pos.state = PositionState.CLOSED
            pos.updated_at = time.time()

            # Логируем сделку в trades_v5.jsonl
            trade_record = {
                "ts": pos.updated_at,
                "symbol": pos.symbol,
                "side": pos.side.value,
                "qty": pos.qty,
                "entry_price": pos.avg_price,
                "close_price": exit_price,
                "size_usd": pos.size_usd,
                "pnl_usd": pnl_usd,
                "reason": reason,
            }
            self.storage.append_trade_record(trade_record)

            # Для простоты — сразу удаляем закрытую позицию из списка
            del self._positions[idx]
            self.storage.save_positions(self._positions)

            log.info(
                "DRY CLOSE %s %s @ %.4f (reason=%s, pnl=%.2f USDT)",
                pos.side.value,
                pos.symbol,
                exit_price,
                reason,
                pnl_usd,
            )

            return ExecutionResult(
                success=True,
                reason="DRY position closed",
                position=pos,
                order_ids=[],
            )
        except Exception as e:
            self._last_error = str(e)
            log.exception("Ошибка при close_position(%s): %s", symbol, e)
            return ExecutionResult(success=False, reason=f"Exception: {e}")

    def close_all_positions(
        self,
        reason: str = "",
        close_price: Optional[float] = None,
    ) -> List[ExecutionResult]:
        results: List[ExecutionResult] = []
        symbols = [p.symbol for p in self._positions if p.state == PositionState.OPEN]
        for sym in symbols:
            res = self.close_position(sym, reason=reason, close_price=close_price)
            results.append(res)
        return results

    # ------------------------------------------------------------------
    # Статус для health_v5.json
    # ------------------------------------------------------------------
    def get_status(self, now_ts: float) -> EngineStatus:
        """
        Агрегированный статус для health_v5.json.
        """
        total_size = sum(p.size_usd for p in self._positions)
        equity = self._dry_equity_usd
        portfolio_load_pct = (total_size / equity * 100.0) if equity > 0 else 0.0

        status = EngineStatus(
            mode=self.config.mode,
            positions=self.positions,
            portfolio_load_pct=portfolio_load_pct,
            open_orders_count=0,
            daily_pnl_usd=self.risk.daily_pnl_usd,
            daily_stop_hit=self.risk.daily_stop_hit(),
            circuit_breaker_active=False,
            last_error=self._last_error,
            last_heartbeat_ts=now_ts,
            equity_usd=equity,
            balance_usd=equity,
            queue_size=None,  # заполнит оркестратор
        )
        return status

    # ------------------------------------------------------------------
    # Сервисные хуки (на будущее)
    # ------------------------------------------------------------------
    def sync_with_exchange(self) -> None:
        """
        На будущее: сверка с биржей.
        В DRY — no-op.
        """
        return

    def on_timer(self, now_ts: float) -> None:
        """
        Периодический тик (пока пустой).
        """
        return

    def reload_config(self, new_config: ExecutionConfig) -> None:
        self.config = new_config
        log.info("ExecutionConfig обновлён: %s", self.config)
