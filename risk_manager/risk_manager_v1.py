#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RiskManager v1 — независимый слой риск-менеджмента для HOPE v5.

Идея:
- Это «чёрный ящик», который стоит между источником сигналов (SmartTrend, телеграм и т.п.)
  и ExecutionEngine / ядром.
- Не знает ничего про реализацию HOPE, работает только с:
    * сигналом (dict)
    * контекстом риска (equity, дневной PnL, открытые позиции)
    * конфигом risk_v1.yaml

Принципы:
- Сигналы LONG/SHORT могут быть:
    * разрешены как есть
    * модифицированы (уменьшён risk_usd)
    * отклонены (blocked)
- Сигналы CLOSE:
    * НИКОГДА не блокируются, даже при превышении лимитов — их задача закрывать риск.

Интеграция (Шаг 3):
- где-то в run_live_v5:
    rm = RiskManagerV1()
    ctx = RiskContext(
        equity=current_equity,
        daily_realized_pnl_usd=daily_pnl,
        open_positions=current_positions,
    )
    allowed, new_signal, reason = rm.evaluate(signal, ctx)
    if not allowed:
        логируем reason и не передаём сигнал дальше.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml


ROOT_DIR = Path(__file__).resolve().parents[2]
RISK_CONFIG_FILE = ROOT_DIR / "config" / "risk_v1.yaml"


@dataclass
class RiskConfig:
    max_daily_loss_usd: float
    max_daily_loss_hard_stop_usd: float
    max_concurrent_positions_total: int
    max_concurrent_positions_per_symbol: int
    max_risk_per_trade_usd: float
    allow_new_trades: bool
    blocked_symbols: List[str]


@dataclass
class RiskContext:
    """
    Контекст для принятия решения по сигналу.

    equity                — текущий баланс/эквити (USDT)
    daily_realized_pnl_usd — реализованный PnL за день (USDT, может быть отрицательным)
    open_positions        — список открытых позиций (как минимум поля: symbol, state=OPEN)
    """

    equity: float
    daily_realized_pnl_usd: float
    open_positions: List[Dict[str, Any]]


class RiskManagerV1:
    def __init__(self, config_path: Optional[Path] = None) -> None:
        self.config = self._load_config(config_path)

    # ------------------------------------------------------------------ #
    # Конфиг
    # ------------------------------------------------------------------ #

    def _load_config(self, path: Optional[Path]) -> RiskConfig:
        cfg_path = path or RISK_CONFIG_FILE
        if not cfg_path.exists():
            raise FileNotFoundError(f"Не найден конфиг риска {cfg_path}")

        with cfg_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        return RiskConfig(
            max_daily_loss_usd=float(raw.get("max_daily_loss_usd", 50.0)),
            max_daily_loss_hard_stop_usd=float(
                raw.get("max_daily_loss_hard_stop_usd", 80.0)
            ),
            max_concurrent_positions_total=int(
                raw.get("max_concurrent_positions_total", 3)
            ),
            max_concurrent_positions_per_symbol=int(
                raw.get("max_concurrent_positions_per_symbol", 1)
            ),
            max_risk_per_trade_usd=float(raw.get("max_risk_per_trade_usd", 20.0)),
            allow_new_trades=bool(raw.get("allow_new_trades", True)),
            blocked_symbols=[s.upper() for s in raw.get("blocked_symbols", [])],
        )

    # ------------------------------------------------------------------ #
    # Публичный API
    # ------------------------------------------------------------------ #

    def evaluate(
        self,
        signal: Dict[str, Any],
        ctx: RiskContext,
    ) -> Tuple[bool, Dict[str, Any], str]:
        """
        Возвращает: (allowed, new_signal, reason)

        allowed    — можно ли отправлять сигнал дальше.
        new_signal — сигнал (возможно модифицированный, например с урезанным risk_usd).
        reason     — текстовое пояснение (для логов).
        """
        # Клонируем сигнал, чтобы не портить оригинал
        sig = dict(signal)
        side = str(sig.get("side", "")).upper()
        symbol = str(sig.get("symbol", "")).upper()

        # CLOSE никогда не блокируем — пусть ядро закрывает риск
        if side == "CLOSE":
            return True, sig, "CLOSE всегда разрешён (RiskManager v1)"

        # Далее проверяем только входные сигналы (LONG / SHORT)
        if side not in ("LONG", "SHORT"):
            # неизвестный тип сигнала — пропускаем как есть, но с пометкой
            return True, sig, f"Неизвестный side={side}, пропускаю без изменений"

        # Базовые проверки (глобальный флаг + блок-лист символов)
        if not self.config.allow_new_trades:
            return False, sig, "Новые сделки запрещены (allow_new_trades = false)"

        if symbol in self.config.blocked_symbols:
            return False, sig, f"Символ {symbol} заблокирован (blocked_symbols)"

        # Лимиты по дневному убытку
        if ctx.daily_realized_pnl_usd <= -self.config.max_daily_loss_usd:
            return (
                False,
                sig,
                f"Достигнут дневной лимит убытка "
                f"{ctx.daily_realized_pnl_usd:.2f} <= -{self.config.max_daily_loss_usd:.2f}",
            )

        # Лимиты по количеству открытых позиций
        total_open = sum(1 for p in ctx.open_positions if p.get("state") == "OPEN")
        if total_open >= self.config.max_concurrent_positions_total:
            return (
                False,
                sig,
                f"Превышен лимит позиций: {total_open} >= "
                f"{self.config.max_concurrent_positions_total}",
            )

        symbol_open = sum(
            1
            for p in ctx.open_positions
            if p.get("state") == "OPEN"
            and str(p.get("symbol", "")).upper() == symbol
        )
        if symbol_open >= self.config.max_concurrent_positions_per_symbol:
            return (
                False,
                sig,
                f"Уже есть {symbol_open} открытых позиций по {symbol}, "
                f"лимит = {self.config.max_concurrent_positions_per_symbol}",
            )

        # Лимит риска на сделку: если сигнал просит больше — урежем
        risk_usd = float(sig.get("risk_usd", 0.0))
        if risk_usd > self.config.max_risk_per_trade_usd > 0:
            sig["risk_usd"] = float(self.config.max_risk_per_trade_usd)
            return (
                True,
                sig,
                f"risk_usd урезан с {risk_usd:.2f} до "
                f"{self.config.max_risk_per_trade_usd:.2f} (лимит на сделку)",
            )

        # Если до сюда дошли — всё ок, пропускаем без изменений
        return True, sig, "Сигнал прошёл все проверки RiskManager v1"


__all__ = ["RiskManagerV1", "RiskContext", "RiskConfig"]







