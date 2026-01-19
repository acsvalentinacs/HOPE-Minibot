from __future__ import annotations

"""
signal_validator_v5.py

Единая точка валидации сигналов для HOPE v5.

Задачи:
- проверить, что сигнал корректный по структуре;
- отфильтровать явно странные значения (NaN, бесконечности, отрицательная цена и т.п.);
- ограничить диапазон риска;
- нормализовать сторону (LONG/SHORT/CLOSE);
- подготовить понятное текстовое объяснение, почему сигнал отклонён.

Использование:
    is_ok, reason = validate_signal(signal_dict, current_price=price_from_market)
"""

import logging
import math
from typing import Any, Dict, Tuple, Optional

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ("symbol", "side", "price", "risk_usd", "source")
VALID_SIDES = {"LONG", "SHORT", "CLOSE"}


def _is_number(value: Any) -> bool:
    """Проверяет, является ли значение конечным числом."""
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def validate_signal(
    signal: Dict[str, Any],
    *,
    current_price: Optional[float] = None,
    min_price: float = 1e-8,
    max_price: float = 1_000_000.0,
    min_risk: float = 1.0,
    max_risk: float = 5_000.0,
    max_price_deviation: float = 0.05,
) -> Tuple[bool, str]:
    """
    Валидация входного сигнала.

    Возвращает (ok: bool, reason: str).
    reason = "OK" только если сигнал полностью принят.
    """

    # 0) Тип
    if not isinstance(signal, dict):
        logger.warning("❌ Signal rejected: not a dict (%r)", type(signal))
        return False, "Signal is not a dict"

    # 1) Обязательные поля
    for field in REQUIRED_FIELDS:
        if field not in signal:
            logger.warning("❌ Signal rejected: missing field %s (%r)", field, signal)
            return False, f"Missing required field: {field}"

    # 2) Символ и источник
    symbol = str(signal.get("symbol", "")).strip().upper()
    if not symbol:
        logger.warning("❌ Signal rejected: empty symbol (%r)", signal)
        return False, "Empty symbol"

    source = str(signal.get("source", "")).strip()
    if not source:
        logger.warning("❌ Signal rejected: empty source (%r)", signal)
        return False, "Empty source"

    # 3) Сторона
    side_raw = str(signal.get("side", "")).strip().upper()
    if side_raw not in VALID_SIDES:
        logger.warning("❌ Signal rejected: invalid side=%r (%r)", side_raw, signal)
        return False, f"Invalid side: {side_raw}"

    # 4) Цена
    price = signal.get("price")
    if not _is_number(price):
        logger.warning("❌ Signal rejected: invalid price=%r (%r)", price, signal)
        return False, f"Invalid price: {price!r}"

    price_f = float(price)
    if not (min_price <= price_f <= max_price):
        logger.warning(
            "❌ Signal rejected: price out of range %.8f not in [%.8f, %.8f] (%r)",
            price_f,
            min_price,
            max_price,
            signal,
        )
        return False, f"Price out of allowed range: {price_f}"

    # 5) Отклонение от текущей рыночной цены (если передана)
    if current_price is not None and _is_number(current_price) and current_price > 0:
        cp = float(current_price)
        deviation = abs(price_f - cp) / cp
        if deviation > max_price_deviation:
            logger.warning(
                "❌ Signal rejected: price deviation %.4f > max %.4f (price=%.8f, current=%.8f, symbol=%s)",
                deviation,
                max_price_deviation,
                price_f,
                cp,
                symbol,
            )
            return False, f"Price deviates too much from market ({deviation:.2%})"

    # 6) Риск в USD
    risk_usd = signal.get("risk_usd")
    if not _is_number(risk_usd):
        logger.warning("❌ Signal rejected: invalid risk_usd=%r (%r)", risk_usd, signal)
        return False, f"Invalid risk_usd: {risk_usd!r}"

    risk_f = float(risk_usd)
    if risk_f < min_risk:
        logger.warning(
            "❌ Signal rejected: risk %.2f < min %.2f (%r)",
            risk_f,
            min_risk,
            signal,
        )
        return False, f"Risk too small: {risk_f} < {min_risk}"

    if risk_f > max_risk:
        logger.warning(
            "❌ Signal rejected: risk %.2f > max %.2f (%r)",
            risk_f,
            max_risk,
            signal,
        )
        return False, f"Risk too large: {risk_f} > {max_risk}"

    # 7) Доп. поля (ts, signal_id) — не обязательны, но полезны для трассировки
    ts = signal.get("ts")
    if ts is not None and not _is_number(ts):
        logger.warning("⚠️ Signal ts is not numeric: %r (ignored)", ts)

    signal_id = signal.get("signal_id") or ""
    signal_id = str(signal_id).strip()
    # Не валидируем жёстко, просто нормализуем
    if signal_id:
        # Можно добавить ограничение длины/символов при необходимости
        pass

    # 8) Всё ок
    logger.debug(
        "✅ Signal validated: symbol=%s side=%s price=%.8f risk=%.2f source=%s id=%s",
        symbol,
        side_raw,
        price_f,
        risk_f,
        source,
        signal_id or "-",
    )
    return True, "OK"
