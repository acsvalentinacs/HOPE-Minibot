#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
risk_manager_v1.py — HOPE v5.x

RiskManagerV1:
- следит за дневным PnL и дневным стопом (глобально для ядра);
- ограничивает число одновременно открытых позиций;
- считает blocked_count — сколько раз дал отказ на открытие позиции;
- хранит состояние в state/risk_state_v1.json;
- читает конфиг из config/risk_v5.yaml (utf-8-sig).

Дополнительно (HUNTERS):
- читает verdict_multipliers (IGNORE/WEAK/BORDERLINE/STRONG → множитель риска);
- читает секцию hunters (base_risk_usd, profiles.*.risk_mult);
- предоставляет метод compute_hunters_risk(profile, verdict, equity_usd=None),
  который возвращает риск в USDT для HUNTERS-сделки с учётом:
    * hunters.base_risk_usd,
    * hunters.profiles[PROFILE].risk_mult,
    * verdict_multipliers[VERDICT],
    * base.max_risk_equity_pct (ограничение по equity, если передано).
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, Tuple

import yaml

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT_DIR / "state"
CONFIG_DIR = ROOT_DIR / "config"

RISK_CONFIG_FILE = CONFIG_DIR / "risk_v5.yaml"
RISK_STATE_FILE = STATE_DIR / "risk_state_v1.json"

JSON_ENCODING = "utf-8-sig"


class RiskManagerV1:
    def __init__(
        self,
        config_path: Path | str = RISK_CONFIG_FILE,
        state_path: Path | str = RISK_STATE_FILE,
    ) -> None:
        self.config_path = Path(config_path)
        self.state_path = Path(state_path)

        self.cfg: Dict[str, Any] = {}
        self.daily_pnl: float = 0.0
        self.is_locked: bool = False
        self.blocked_count: int = 0
        self._last_reset_date: str = ""

        self._load_config()
        self._load_state()
        self._ensure_today()

    # --------------------------------------------------------------------- #
    # ВНУТРЕННИЕ ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ
    # --------------------------------------------------------------------- #
    def _load_config(self) -> None:
        try:
            if self.config_path.exists():
                raw = self.config_path.read_text(encoding=JSON_ENCODING) or ""
                self.cfg = yaml.safe_load(raw) or {}
            else:
                self.cfg = {}
        except Exception as e:
            logger.error("RiskManager config load error: %s", e)
            self.cfg = {}

        base = self.cfg.setdefault("base", {})
        base.setdefault("daily_loss_limit_usd", 50.0)
        base.setdefault("base_risk_usd", 15.0)
        base.setdefault("max_open_positions", 3)
        # max_risk_equity_pct может отсутствовать в старых конфигах — не навязываем,
        # но если указали, используем в compute_hunters_risk.
        base.setdefault("max_risk_equity_pct", 0.0)

    def _load_state(self) -> None:
        try:
            if self.state_path.exists():
                raw = self.state_path.read_text(encoding=JSON_ENCODING) or "{}"
                data = json.loads(raw)
            else:
                data = {}
        except Exception as e:
            logger.error("RiskManager state load error: %s", e)
            data = {}

        self.daily_pnl = float(data.get("daily_pnl_usd", 0.0))
        self.is_locked = bool(data.get("is_locked", False))
        self.blocked_count = int(data.get("blocked_count", 0))
        self._last_reset_date = str(data.get("reset_date") or "")

    def _save_state(self) -> None:
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "daily_pnl_usd": self.daily_pnl,
                "is_locked": self.is_locked,
                "blocked_count": self.blocked_count,
                "reset_date": self._last_reset_date or date.today().isoformat(),
            }
            self.state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error("RiskManager state save error: %s", e)

    def _ensure_today(self) -> None:
        today = date.today().isoformat()
        if self._last_reset_date != today:
            self.daily_pnl = 0.0
            self.is_locked = False
            self.blocked_count = 0
            self._last_reset_date = today
            self._save_state()

    def _get_base_cfg(self) -> Dict[str, Any]:
        return self.cfg.get("base", {}) or {}

    def _get_daily_limit(self) -> float:
        base = self._get_base_cfg()
        return float(base.get("daily_loss_limit_usd", 50.0))

    # HUNTERS-вспомогательные конфиги
    def _get_hunters_cfg(self) -> Dict[str, Any]:
        return self.cfg.get("hunters", {}) or {}

    def _get_verdict_cfg(self) -> Dict[str, float]:
        # verdict_multipliers лежат на верхнем уровне конфига
        raw = self.cfg.get("verdict_multipliers", {}) or {}
        # Приводим значения к float, но без фанатизма — ошибки не валим
        cleaned: Dict[str, float] = {}
        for k, v in raw.items():
            try:
                cleaned[str(k).strip().upper()] = float(v)
            except (TypeError, ValueError):
                continue
        return cleaned

    # --------------------------------------------------------------------- #
    # БАЗОВЫЙ РИСК ПО ПРОФИЛЮ (ГЛОБАЛЬНЫЙ)
    # --------------------------------------------------------------------- #
    def get_risk_for_profile(self, profile: str | None) -> float:
        """
        Возвращает базовый риск (в USDT) для профиля ядра, без учёта HUNTERS.
        Использует:
        - base.base_risk_usd,
        - profiles[PROFILE].risk_multiplier,
        - авто-мэппинг HUNTERS_*, если профиль без префикса.
        """
        self._ensure_today()
        base = self._get_base_cfg()
        base_risk = float(base.get("base_risk_usd", 15.0))

        profiles_cfg: Dict[str, Any] = self.cfg.get("profiles", {}) or {}
        key = (profile or "").strip().upper()
        data = None

        if key:
            data = profiles_cfg.get(key)
            if data is None and not key.startswith("HUNTERS_"):
                data = profiles_cfg.get("HUNTERS_" + key)

        if data is None:
            data = profiles_cfg.get("HUNTERS_STANDARD") or profiles_cfg.get("STANDARD") or {}

        mult = 1.0
        if isinstance(data, dict):
            try:
                mult = float(data.get("risk_multiplier", 1.0))
            except (TypeError, ValueError):
                mult = 1.0

        return max(0.0, base_risk * mult)

    # --------------------------------------------------------------------- #
    # ГЛОБАЛЬНЫЙ КОНТРОЛЬ ОТКРЫТИЯ ПОЗИЦИЙ
    # --------------------------------------------------------------------- #
    def can_open_position(self, current_open_positions: int, equity_usd: float) -> Tuple[bool, str]:
        """
        Проверяет, можно ли открыть НОВУЮ позицию:
        - дневной стоп по PnL;
        - лимит по количеству одновременно открытых позиций.

        Возвращает:
            (allowed: bool, reason: str)
        """
        self._ensure_today()
        base = self._get_base_cfg()
        daily_limit = self._get_daily_limit()

        # 1. Дневной стоп
        if self.daily_pnl <= -daily_limit:
            if not self.is_locked:
                logger.warning("Daily stop hit: PnL=%.2f <= -%.2f", self.daily_pnl, daily_limit)
            self.is_locked = True
            self.blocked_count += 1
            self._save_state()
            return False, "DAILY_STOP_HIT"

        if self.is_locked:
            self.blocked_count += 1
            self._save_state()
            return False, "DAILY_STOP_HIT"

        # 2. Лимит по количеству позиций
        max_pos = int(base.get("max_open_positions", 3))
        if current_open_positions >= max_pos:
            self.blocked_count += 1
            self._save_state()
            return False, "MAX_OPEN_POSITIONS"

        self._save_state()
        return True, "OK"

    def update_pnl(self, trade_pnl_usd: float) -> None:
        """
        Обновляет глобальный дневной PnL.
        Если дневной лимит пробит — включает global lock (is_locked=True).
        """
        self._ensure_today()
        self.daily_pnl += float(trade_pnl_usd or 0.0)
        daily_limit = self._get_daily_limit()
        if self.daily_pnl <= -daily_limit:
            self.is_locked = True
        self._save_state()

    # --------------------------------------------------------------------- #
    # HUNTERS: ПРОФИЛИ, VERDICT И ВЫЧИСЛЕНИЕ РИСКА
    # --------------------------------------------------------------------- #
    def get_verdict_multiplier(self, verdict: str | None) -> float:
        """
        Возвращает множитель по verdict из verdict_multipliers.
        IGNORE / WEAK / BORDERLINE / STRONG.

        Если verdict неизвестен — используем STRONG как «по умолчанию».
        """
        cfg = self._get_verdict_cfg()
        if not cfg:
            return 1.0

        key = (verdict or "").strip().upper()
        if not key:
            key = "STRONG"

        if key not in cfg and "STRONG" in cfg:
            key = "STRONG"

        return float(cfg.get(key, 1.0))

    def get_hunters_profile_multiplier(self, profile: str | None) -> float:
        """
        Возвращает множитель профиля из секции hunters.profiles:
        - SAFE / SCALP / BOOST (и любые другие, если добавишь).
        """
        hunters_cfg = self._get_hunters_cfg()
        profiles_cfg: Dict[str, Any] = hunters_cfg.get("profiles", {}) or {}

        key = (profile or "").strip().upper()
        if not key:
            key = "SCALP"

        data = profiles_cfg.get(key)
        if data is None:
            # Фоллбек — SCALP, если есть, иначе единица
            data = profiles_cfg.get("SCALP") or {}

        mult = 1.0
        if isinstance(data, dict):
            try:
                mult = float(data.get("risk_mult", 1.0))
            except (TypeError, ValueError):
                mult = 1.0

        return mult

    def compute_hunters_risk(
        self,
        profile: str | None,
        verdict: str | None,
        equity_usd: float | None = None,
    ) -> float:
        """
        Считает риск (в USDT) для HUNTERS-сделки:

        risk_usd =
            hunters.base_risk_usd
            * hunters.profiles[profile].risk_mult
            * verdict_multipliers[verdict]
            с учётом base.max_risk_equity_pct (если передан equity_usd).

        ПРИМЕР:
            base_risk_usd = 3.0
            profile = "BOOST" → risk_mult = 1.5
            verdict = "STRONG" → 1.0
            → raw_risk = 4.5 USDT

        Если max_risk_equity_pct > 0 и передан equity_usd,
        итог ограничивается equity_usd * max_risk_equity_pct.
        """
        self._ensure_today()

        base_cfg = self._get_base_cfg()
        hunters_cfg = self._get_hunters_cfg()

        # 1. Базовый риск: hunters.base_risk_usd или глобальный base_risk_usd
        base_risk = float(
            hunters_cfg.get(
                "base_risk_usd",
                base_cfg.get("base_risk_usd", 15.0),
            )
        )

        # 2. Множитель по профилю (SAFE / SCALP / BOOST и т.п.)
        profile_mult = self.get_hunters_profile_multiplier(profile)

        # 3. Множитель по verdict (IGNORE/WEAK/BORDERLINE/STRONG)
        verdict_mult = self.get_verdict_multiplier(verdict)

        raw_risk = max(0.0, base_risk * profile_mult * verdict_mult)

        # 4. Ограничение по equity, если задан max_risk_equity_pct
        max_equity_pct = 0.0
        try:
            max_equity_pct = float(base_cfg.get("max_risk_equity_pct", 0.0) or 0.0)
        except (TypeError, ValueError):
            max_equity_pct = 0.0

        risk_usd = raw_risk
        if equity_usd is not None and max_equity_pct > 0.0 and equity_usd > 0.0:
            cap = float(equity_usd) * max_equity_pct
            risk_usd = min(raw_risk, cap)

        risk_usd = max(0.0, float(risk_usd))

        logger.debug(
            "HUNTERS risk computed: base_risk=%.2f, profile=%s (x%.3f), "
            "verdict=%s (x%.3f), equity=%.2f, result=%.3f",
            base_risk,
            (profile or "").upper(),
            profile_mult,
            (verdict or "").upper(),
            verdict_mult,
            float(equity_usd) if equity_usd is not None else -1.0,
            risk_usd,
        )
        return risk_usd
