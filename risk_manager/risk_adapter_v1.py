#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
risk_adapter_v1.py — тонкий адаптер RiskManagerV1 для ядра HOPE v5.

Идея:
- Никаких файлов, никакого знания о внутренностях run_live_v5 / ExecutionEngine.
- Вся информация подаётся извне: signal, equity, daily_pnl, open_positions.
- Возвращаем (allowed, new_signal, reason) для дальнейшей обработки в run_live_v5.

Пример использования в run_live_v5:

    from minibot.risk_manager.risk_adapter_v1 import apply_risk_manager

    ...

    for signal in queue.iter_signals():
        equity = equity_tracker.current_equity_usd()
        daily_pnl = daily_pnl_tracker.current_realized_pnl_usd()
        open_positions = engine.get_open_positions()  # или из state/exec_positions_v5.json

        allowed, sig2, reason = apply_risk_manager(
            signal=signal,
            equity=equity,
            daily_realized_pnl_usd=daily_pnl,
            open_positions=open_positions,
        )

        if not allowed:
            logger.info("RiskManagerV1 отклонил сигнал: %s (%s)", signal, reason)
            continue

        if sig2 is not signal:
            logger.info("RiskManagerV1 модифицировал сигнал: %s", reason)

        engine.handle_signal(sig2)

"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .risk_manager_v1 import RiskManagerV1, RiskContext


# Ленивая инициализация единственного инстанса RiskManagerV1
_rm_singleton: RiskManagerV1 | None = None


def get_risk_manager() -> RiskManagerV1:
    """
    Возвращает singleton-инстанс RiskManagerV1.

    Можно вызывать сколько угодно раз — конфиг risk_v1.yaml читается только один раз.
    """
    global _rm_singleton
    if _rm_singleton is None:
        _rm_singleton = RiskManagerV1()
    return _rm_singleton


def apply_risk_manager(
    signal: Dict[str, Any],
    equity: float,
    daily_realized_pnl_usd: float,
    open_positions: List[Dict[str, Any]],
) -> Tuple[bool, Dict[str, Any], str]:
    """
    Основная функция адаптера.

    Parameters
    ----------
    signal : dict
        Сырой торговый сигнал (как после SmartSignalQueueV2 / чтения signals_v5.jsonl).
        Ожидается формат:
            - symbol: str
            - side: "LONG" / "SHORT" / "CLOSE"
            - risk_usd: float (может отсутствовать — тогда 0)
            - прочие поля (price, ts, source, reason, signal_id, confidence, ...)

    equity : float
        Текущее эквити аккаунта (USDT). Можно подать как "total equity" или "free equity" —
        зависит от того, как ты считаешь риск. Для v1 не критично, но полезно на будущее.

    daily_realized_pnl_usd : float
        Реализованный PnL за текущий торговый день (USDT).
        Отрицательный при убытке, положительный при прибыли.
        Используется для контроля max_daily_loss_usd.

    open_positions : list[dict]
        Текущие открытые позиции. Минимальные ожидаемые поля:
            - symbol: str
            - state: "OPEN" / "CLOSED"
        Всё остальное (entry_price, size, ... ) RiskManager v1 не трогает.

    Returns
    -------
    allowed : bool
        Можно ли отправлять сигнал дальше в ExecutionEngine.
        False → сигнал отбрасывается (но CLOSE никогда не блокируется).

    new_signal : dict
        Возможно модифицированный сигнал:
            - например, если risk_usd > max_risk_per_trade_usd, он будет урезан.

    reason : str
        Человеко-читаемое объяснение для логов:
            - почему отклонили
            - или чем модифицировали
            - или что "сигнал прошёл все проверки"
    """
    rm = get_risk_manager()

    ctx = RiskContext(
        equity=float(equity),
        daily_realized_pnl_usd=float(daily_realized_pnl_usd),
        open_positions=list(open_positions) if open_positions else [],
    )

    allowed, new_signal, reason = rm.evaluate(signal, ctx)
    return allowed, new_signal, reason


__all__ = ["apply_risk_manager", "get_risk_manager"]
