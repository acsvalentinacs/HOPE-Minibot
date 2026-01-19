#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RiskManager v1 for HOPE v5.1

Работает с новым конфигом config/risk_v5.yaml:

version: 1

base:
  base_risk_usd: 15.0
  daily_loss_limit_usd: 50.0
  max_open_positions: 4
  cooldown_after_loss_sec: 600
  cooldown_after_big_loss_sec: 1800

profiles:
  SAFE:
    risk_multiplier: 0.5
  SCALP:
    risk_multiplier: 1.0
  HUNTERS_BOOST:
    risk_multiplier: 1.5

hunters:
  verdict_multipliers:
    IGNORE: 0.0
    WEAK: 0.5
    BORDERLINE: 0.75
    STRONG: 1.0

Интерфейс, который использует run_live_v5:

- свойства:
    * daily_pnl: float      — накопленный реализованный PnL за «сессию/день»
    * is_locked: bool       — блокировать ли новые входы (дневной лимит / кулдауны)
- методы:
    * can_open_position(curr_open_positions: int, equity: float) -> (allowed, reason)
    * get_risk_per_trade(verdict: Optional[str] = None) -> float
    * update_pnl(pnl: float) -> None

Дополнительно оставлен метод evaluate(...) для совместимости с будущими
использованиями (например, когда RiskManager будет стоять прямо на слое сигналов).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml


ROOT_DIR = Path(__file__).resolve().parents[2]
RISK_CONFIG_FILE_V5 = ROOT_DIR / "config" / "risk_v5.yaml"
RISK_CONFIG_FILE_V1 = ROOT_DIR / "config" / "risk_v1.yaml"  # запасной вариант


@dataclass
class RiskBaseConfig:
    base_risk_usd: float
    daily_loss_limit_usd: float
    max_open_positions: int
    cooldown_after_loss_sec: int
    cooldown_after_big_loss_sec: int


@dataclass
class RiskConfigV5:
    base: RiskBaseConfig
    profiles: Dict[str, float]          # name -> risk_multiplier
    verdict_multipliers: Dict[str, float]


class RiskManagerV1:
    """
    Лёгкий stateful-слой риска для HOPE v5.1.

    Не лезет в детали стратегии:
    - знает только текущий PnL (update_pnl),
    - сколько уже открыто позиций (can_open_position),
    - и возвращает допустимый риск на сделку (get_risk_per_trade).
    """

    def __init__(self, config_path: Optional[Path] = None, profile: Optional[str] = None) -> None:
        self.config = self._load_config(config_path)

        # Активный профиль риска
        env_profile = os.getenv("HOPE_RISK_PROFILE", "SCALP").upper()
        self.profile_name: str = (profile or env_profile).upper()
        if self.profile_name not in self.config.profiles:
            self.profile_name = "SCALP"

        # Текущее состояние
        self.daily_pnl: float = 0.0
        self._locked: bool = False
        self._last_loss_ts: Optional[float] = None
        self._last_big_loss_ts: Optional[float] = None

    # ------------------------------------------------------------------ #
    # Загрузка конфига
    # ------------------------------------------------------------------ #
    def _load_config(self, path: Optional[Path]) -> RiskConfigV5:
        # Приоритет: явно переданный → risk_v5.yaml → (fallback) risk_v1.yaml c конвертацией
        cfg_path: Path
        if path is not None:
            cfg_path = Path(path)
        elif RISK_CONFIG_FILE_V5.exists():
            cfg_path = RISK_CONFIG_FILE_V5
        elif RISK_CONFIG_FILE_V1.exists():
            cfg_path = RISK_CONFIG_FILE_V1
        else:
            raise FileNotFoundError(
                f"Не найден конфиг риска ни {RISK_CONFIG_FILE_V5}, ни {RISK_CONFIG_FILE_V1}"
            )

        with cfg_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        # Если это старый формат risk_v1.yaml — аккуратно адаптируем его к v5
        if "base" not in raw and "profiles" not in raw:
            base = RiskBaseConfig(
                base_risk_usd=float(raw.get("max_risk_per_trade_usd", 15.0)),
                daily_loss_limit_usd=float(raw.get("max_daily_loss_usd", 50.0)),
                max_open_positions=int(raw.get("max_concurrent_positions_total", 4)),
                cooldown_after_loss_sec=int(raw.get("cooldown_after_loss_sec", 600)),
                cooldown_after_big_loss_sec=int(
                    raw.get("cooldown_after_big_loss_sec", 1800)
                ),
            )
            profiles = {
                "SAFE": 0.5,
                "SCALP": 1.0,
                "HUNTERS_BOOST": 1.5,
            }
            verdict_multipliers = {
                "IGNORE": 0.0,
                "WEAK": 0.5,
                "BORDERLINE": 0.75,
                "STRONG": 1.0,
            }
        else:
            base_raw = raw.get("base", {}) or {}
            base = RiskBaseConfig(
                base_risk_usd=float(base_raw.get("base_risk_usd", 15.0)),
                daily_loss_limit_usd=float(base_raw.get("daily_loss_limit_usd", 50.0)),
                max_open_positions=int(base_raw.get("max_open_positions", 4)),
                cooldown_after_loss_sec=int(base_raw.get("cooldown_after_loss_sec", 600)),
                cooldown_after_big_loss_sec=int(
                    base_raw.get("cooldown_after_big_loss_sec", 1800)
                ),
            )

            profiles_raw = raw.get("profiles", {}) or {}
            profiles = {}
            for name, cfg in profiles_raw.items():
                try:
                    profiles[name.upper()] = float(cfg.get("risk_multiplier", 1.0))
                except Exception:
                    continue
            if not profiles:
                profiles = {"SCALP": 1.0}

            hunters_raw = raw.get("hunters", {}) or {}
            verdict_multipliers = {
                k.upper(): float(v)
                for k, v in (hunters_raw.get("verdict_multipliers") or {}).items()
            }

        return RiskConfigV5(
            base=base,
            profiles=profiles,
            verdict_multipliers=verdict_multipliers,
        )

    # ------------------------------------------------------------------ #
    # Свойства
    # ------------------------------------------------------------------ #
    @property
    def is_locked(self) -> bool:
        """
        True — если:
        - дневной PnL пробил лимит по убытку
        - либо активен кулдаун после потерь
        """
        if self._locked:
            return True

        now = time.time()

        # Дневной лимит убытка
        if self.daily_pnl <= -self.config.base.daily_loss_limit_usd:
            return True

        # Обычный кулдаун после убыточной сделки
        if (
            self._last_loss_ts is not None
            and now - self._last_loss_ts < self.config.base.cooldown_after_loss_sec
        ):
            return True

        # Длинный кулдаун после крупной просадки
        if (
            self._last_big_loss_ts is not None
            and now - self._last_big_loss_ts < self.config.base.cooldown_after_big_loss_sec
        ):
            return True

        return False

    # ------------------------------------------------------------------ #
    # Публичный API для run_live_v5
    # ------------------------------------------------------------------ #
    def can_open_position(self, curr_open_positions: int, equity: float) -> Tuple[bool, str]:
        """
        Проверяем, можно ли открывать новую позицию.

        curr_open_positions — сколько уже открыто (по всем символам).
        equity              — текущий эквити в USDT.
        """
        if self.is_locked:
            return False, "RiskManager locked (daily loss limit / cooldown)"

        if curr_open_positions >= self.config.base.max_open_positions:
            return (
                False,
                f"Превышен лимит позиций: {curr_open_positions} >= "
                f"{self.config.base.max_open_positions}",
            )

        # Простая sanity-проверка: риск на сделку не должен быть больше 20% от equity
        risk = self.get_risk_per_trade()
        if equity > 0 and risk > 0.2 * equity:
            return False, f"Слишком большой риск на сделку: {risk:.2f} > 20% от equity"

        return True, "OK"

    def get_risk_per_trade(self, verdict: Optional[str] = None) -> float:
        """
        Возвращает риск на сделку с учётом профиля и (опционально) verdict от HUNTERS.
        """
        base_risk = self.config.base.base_risk_usd
        profile_mult = self.config.profiles.get(self.profile_name, 1.0)

        verdict_mult = 1.0
        if verdict:
            verdict_mult = self.config.verdict_multipliers.get(verdict.upper(), 1.0)

        risk = max(0.0, base_risk * profile_mult * verdict_mult)
        return risk

    def update_pnl(self, pnl: float) -> None:
        """
        Обновляет дневной PnL и внутренние таймстемпы потерь.
        """
        self.daily_pnl += float(pnl)

        if pnl < 0:
            now = time.time()
            self._last_loss_ts = now

            # «Большой» убыток — эвристика: хуже, чем 2R
            if pnl <= -2.0 * self.config.base.base_risk_usd:
                self._last_big_loss_ts = now

    # ------------------------------------------------------------------ #
    # Доп. API «в стиле evaluate» — для будущей интеграции на слое сигналов
    # ------------------------------------------------------------------ #
    def evaluate(
        self,
        signal: Dict[str, Any],
        ctx: "RiskContext",
    ) -> Tuple[bool, Dict[str, Any], str]:
        """
        Совместимый с ранней версией API вариант.

        Сейчас оборачивает:
        - can_open_position(...)
        - get_risk_per_trade(...)
        """
        sig = dict(signal)
        side = str(sig.get("side", "")).upper()

        # CLOSE никогда не блокируем
        if side == "CLOSE":
            return True, sig, "CLOSE всегда разрешён"

        if side not in ("LONG", "SHORT"):
            return True, sig, f"Неизвестный side={side}, пропускаю"

        # Обновляем внутренний PnL на основе контекста (на будущее)
        self.daily_pnl = float(ctx.daily_realized_pnl_usd)

        allowed, reason = self.can_open_position(
            curr_open_positions=len(ctx.open_positions),
            equity=float(ctx.equity),
        )
        if not allowed:
            return False, sig, reason

        # Можем использовать sig.get("verdict") от HUNTERS
        verdict = sig.get("verdict")
        sig_risk = float(sig.get("risk_usd") or 0.0)
        rm_risk = self.get_risk_per_trade(verdict=verdict)

        if rm_risk <= 0:
            return False, sig, "risk_usd=0 после применения профиля/вердикта"

        # Если сигнал просит больший риск — режем его до лимита
        if sig_risk <= 0 or sig_risk > rm_risk:
            sig["risk_usd"] = rm_risk
            return True, sig, f"risk_usd установлен RiskManager: {rm_risk:.2f}"

        return True, sig, "Сигнал прошёл RiskManager без изменений"


@dataclass
class RiskContext:
    """
    Контекст для метода evaluate(...).

    equity                — текущий баланс/эквити (USDT)
    daily_realized_pnl_usd — реализованный PnL за день (USDT, может быть отриц.)
    open_positions        — список открытых позиций (dict, минимум symbol/state)
    """

    equity: float
    daily_realized_pnl_usd: float
    open_positions: List[Dict[str, Any]]


__all__ = ["RiskManagerV1", "RiskContext", "RiskConfigV5", "RiskBaseConfig"]
