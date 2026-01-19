from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, NamedTuple


@dataclass
class RiskConfigV5:
    """
    Базовый конфиг риск-слоя.
    Мы НЕ лезем внутрь run_live_v5, а даём единый контракт can_open().
    """

    # Максимальное количество ОТКРЫТЫХ позиций одновременно
    max_open_positions: int = 3

    # Максимальный риск на сделку, USDT
    max_risk_per_trade_usd: float = 20.0

    # Минимальный осмысленный размер позиции, USDT
    min_position_usd: float = 5.0

    # Дневной стоп (PnL за день не должен уходить ниже этого значения)
    daily_stop_usd: float = -50.0

    # Разрешать ли добавление к уже открытой позиции по символу
    allow_add_to_existing: bool = True

    # Максимум позиций по одному символу (обычно 1)
    max_positions_per_symbol: int = 1


class RiskDecision(NamedTuple):
    can_open: bool
    reason: str
    allowed_risk_usd: float


def _count_open_positions(positions: Iterable[object]) -> int:
    """
    Аккуратно считаем позиции через duck-typing.
    Ожидаем, что у позиции есть атрибут/ключ state == "OPEN".
    """
    count = 0
    for pos in positions:
        state = getattr(pos, "state", None)
        if state is None and isinstance(pos, dict):
            state = pos.get("state")
        if str(state).upper() == "OPEN":
            count += 1
    return count


def _has_open_in_symbol(positions: Iterable[object], symbol: str) -> int:
    """
    Количество открытых позиций по конкретному символу.
    """
    sym = symbol.upper()
    count = 0
    for pos in positions:
        s = getattr(pos, "symbol", None)
        if s is None and isinstance(pos, dict):
            s = pos.get("symbol")
        state = getattr(pos, "state", None)
        if state is None and isinstance(pos, dict):
            state = pos.get("state")
        if str(state).upper() == "OPEN" and str(s).upper() == sym:
            count += 1
    return count


class RiskLayerV5:
    """
    Простой риск-слой для ядра v5.

    decision = risk.can_open(
        symbol="BTCUSDT",
        current_positions=engine.positions,
        equity_usd=current_equity,
        daily_pnl_usd=daily_pnl,
        requested_risk_usd=nominal_risk,
    )
    """

    def __init__(self, config: RiskConfigV5 | None = None):
        self.config = config or RiskConfigV5()

    def can_open(
        self,
        *,
        symbol: str,
        current_positions: Iterable[object],
        equity_usd: float,
        daily_pnl_usd: float,
        requested_risk_usd: float,
    ) -> RiskDecision:
        cfg = self.config

        # 1) Дневной стоп
        if daily_pnl_usd <= cfg.daily_stop_usd:
            return RiskDecision(
                can_open=False,
                reason="RISK_DAILY_STOP",
                allowed_risk_usd=0.0,
            )

        # 2) Лимит количества открытых позиций
        open_count = _count_open_positions(current_positions)
        if open_count >= cfg.max_open_positions:
            return RiskDecision(
                can_open=False,
                reason="RISK_TOO_MANY_POS",
                allowed_risk_usd=0.0,
            )

        # 3) Лимит по символу
        sym_open = _has_open_in_symbol(current_positions, symbol)
        if sym_open >= cfg.max_positions_per_symbol and not cfg.allow_add_to_existing:
            return RiskDecision(
                can_open=False,
                reason="RISK_POSITION_EXISTS",
                allowed_risk_usd=0.0,
            )

        # 4) Нормируем риск по сделке
        risk = float(max(0.0, requested_risk_usd))
        if risk <= 0.0:
            return RiskDecision(
                can_open=False,
                reason="RISK_SIZE_TOO_SMALL",
                allowed_risk_usd=0.0,
            )

        if risk > cfg.max_risk_per_trade_usd:
            risk = cfg.max_risk_per_trade_usd

        if risk < cfg.min_position_usd:
            return RiskDecision(
                can_open=False,
                reason="RISK_SIZE_TOO_SMALL",
                allowed_risk_usd=0.0,
            )

        # 5) Sanity-check по equity
        if equity_usd <= 0:
            return RiskDecision(
                can_open=False,
                reason="RISK_LOW_EQUITY",
                allowed_risk_usd=0.0,
            )

        if risk > equity_usd:
            risk = max(cfg.min_position_usd, equity_usd * 0.25)
            if risk < cfg.min_position_usd:
                return RiskDecision(
                    can_open=False,
                    reason="RISK_LOW_EQUITY",
                    allowed_risk_usd=0.0,
                )

        # Если дошли сюда — сделку открыть можно
        return RiskDecision(
            can_open=True,
            reason="OK",
            allowed_risk_usd=risk,
        )
