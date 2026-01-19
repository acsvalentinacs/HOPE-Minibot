"""
ReconciliationEngine for HOPE v5.

Задачи:
- сверять in-memory позиции двигателя с exec_positions_v5.json;
- при наличии exchange-клиента — сверять с реальными позициями на бирже;
- находить и логировать:
  * orphan позиции (на бирже есть, в движке/файле нет),
  * phantom позиции (в файле/движке есть, на бирже нет),
  * расхождения по количеству (qty).

Движок специально написан максимально "плоско": он ничего не знает
про конкретный ExchangeClient, а работает через простые протоколы.
"""

from __future__ import annotations

import logging
import math
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from minibot.core.types import PositionInfo, PositionState

logger = logging.getLogger(__name__)


class ReconciliationEngine:
    """
    exchange — любой объект, у которого реализован метод:

        get_open_positions() -> Iterable[PositionInfo]

    engine_positions_fn — функция без аргументов, возвращающая Iterable[PositionInfo]
                          (обычно HOPEEngineV5.positions).

    storage_load_fn — функция без аргументов, возвращающая список PositionInfo
                      из exec_positions_v5.json.

    storage_save_fn — функция, принимающая Iterable[PositionInfo] и перезаписывающая
                      exec_positions_v5.json.
    """

    def __init__(
        self,
        *,
        exchange: Optional[Any],
        engine_positions_fn: callable,
        storage_load_fn: callable,
        storage_save_fn: callable,
        qty_tolerance: float = 0.005,
        interval_sec: int = 3600,
    ) -> None:
        self.exchange = exchange
        self.engine_positions_fn = engine_positions_fn
        self.storage_load_fn = storage_load_fn
        self.storage_save_fn = storage_save_fn

        self.qty_tolerance = qty_tolerance
        self.interval_sec = interval_sec
        self._last_run_ts: float = 0.0

    # ------------------------------------------------------------- public API
    def should_run(self, now_ts: Optional[float] = None) -> bool:
        if now_ts is None:
            now_ts = time.time()
        return (now_ts - self._last_run_ts) >= self.interval_sec

    def run_if_due(self, now_ts: Optional[float] = None) -> None:
        if not self.should_run(now_ts):
            return
        self._last_run_ts = now_ts or time.time()
        self.reconcile()

    def reconcile(self) -> None:
        """
        Основная логика reconciliation.

        1) Локальный reconciliation: движок <-> exec_positions_v5.json.
        2) Биржевой reconciliation (если exchange предоставлен):
           exchange <-> локальное состояние.
        """
        try:
            self._reconcile_local()
        except Exception as exc:
            logger.error("ReconciliationEngine: ошибка локального reconciliation: %s", exc, exc_info=True)

        if self.exchange is not None:
            try:
                self._reconcile_exchange()
            except Exception as exc:
                logger.error("ReconciliationEngine: ошибка биржевого reconciliation: %s", exc, exc_info=True)

    # -------------------------------------------------------- internal logic
    def _reconcile_local(self) -> None:
        engine_positions = list(self.engine_positions_fn())
        file_positions = list(self.storage_load_fn())

        engine_by_key: Dict[str, PositionInfo] = {p.symbol: p for p in engine_positions}
        file_by_key: Dict[str, PositionInfo] = {p.symbol: p for p in file_positions}

        # Позиции, которых нет в файле, но есть в движке
        for symbol, pos in engine_by_key.items():
            if symbol not in file_by_key:
                logger.warning(
                    "ReconciliationEngine(local): PHANTOM in engine only: %s size=%.6f",
                    symbol,
                    pos.qty,
                )

        # Позиции, которые есть в файле, но нет в движке
        for symbol, pos in file_by_key.items():
            if symbol not in engine_by_key:
                logger.warning(
                    "ReconciliationEngine(local): PHANTOM in storage only: %s size=%.6f",
                    symbol,
                    pos.qty,
                )

        # Можно дополнительно привести файл к in-memory состоянию, если хотим
        # считать in-memory truth-source:
        if engine_positions:
            logger.info("ReconciliationEngine(local): синхронизирую exec_positions_v5.json по in-memory")
            self.storage_save_fn(engine_positions)

    def _reconcile_exchange(self) -> None:
        # Тип exchange намеренно не фиксирован — ожидаем минимальный протокол.
        exchange_positions: List[PositionInfo] = list(self.exchange.get_open_positions())  # type: ignore[attr-defined]
        engine_positions = list(self.engine_positions_fn())

        ex_by_sym: Dict[str, PositionInfo] = {p.symbol: p for p in exchange_positions}
        eng_by_sym: Dict[str, PositionInfo] = {p.symbol: p for p in engine_positions}

        # Orphan: на бирже есть, у нас нет
        for symbol, ex_pos in ex_by_sym.items():
            if symbol not in eng_by_sym:
                logger.error(
                    "ReconciliationEngine(exchange): ORPHAN position on exchange only: %s qty=%.6f, "
                    "size_usd=%.2f",
                    symbol,
                    ex_pos.qty,
                    ex_pos.size_usd,
                )

        # Phantom: у нас есть, на бирже нет
        for symbol, eng_pos in eng_by_sym.items():
            if symbol not in ex_by_sym:
                logger.error(
                    "ReconciliationEngine(exchange): PHANTOM position in engine only: %s qty=%.6f, "
                    "size_usd=%.2f — будет разблокирована.",
                    symbol,
                    eng_pos.qty,
                    eng_pos.size_usd,
                )
                # Тут можно дополнительно пометить позицию как закрытую локально
                eng_pos.state = PositionState.CLOSED

        # Qty mismatches
        for symbol, ex_pos in ex_by_sym.items():
            eng_pos = eng_by_sym.get(symbol)
            if not eng_pos:
                continue
            if ex_pos.qty <= 0 and eng_pos.qty <= 0:
                continue

            diff = abs(ex_pos.qty - eng_pos.qty)
            if diff <= self.qty_tolerance * max(abs(ex_pos.qty), abs(eng_pos.qty)):
                continue

            logger.error(
                "ReconciliationEngine(exchange): QTY mismatch for %s: engine=%.6f, exchange=%.6f",
                symbol,
                eng_pos.qty,
                ex_pos.qty,
            )


