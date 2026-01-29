# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 17:10:00 UTC
# Purpose: Circuit Breaker for loss protection (fail-closed)
# === END SIGNATURE ===
"""
HOPE AI - Circuit Breaker v1.0

Защита от каскадных потерь:
- Трекинг последовательных убытков
- Автоматическое отключение торговли при превышении лимитов
- Cooldown период перед возобновлением
- Half-open state для тестовых сделок

STATES:
    CLOSED - нормальная работа, торговля разрешена
    OPEN - торговля заблокирована из-за потерь
    HALF_OPEN - тестовый режим после cooldown

INVARIANT: fail-closed - при любом сомнении = OPEN
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    """Состояния circuit breaker."""
    CLOSED = "CLOSED"      # Нормальная работа
    OPEN = "OPEN"          # Торговля заблокирована
    HALF_OPEN = "HALF_OPEN"  # Тестовый режим


@dataclass
class CircuitConfig:
    """Конфигурация circuit breaker."""
    # Loss thresholds
    max_consecutive_losses: int = 3       # Макс последовательных убытков
    max_daily_losses: int = 5             # Макс убытков за день
    max_daily_loss_pct: float = 3.0       # Макс % потери за день

    # Cooldown
    cooldown_seconds: int = 300           # 5 минут cooldown
    half_open_max_trades: int = 2         # Макс сделок в half-open

    # Recovery
    recovery_wins_required: int = 2       # Побед для полного восстановления


@dataclass
class LossRecord:
    """Запись об убытке."""
    timestamp: str
    symbol: str
    loss_pct: float
    reason: str


@dataclass
class CircuitBreaker:
    """
    Circuit Breaker для защиты от каскадных потерь.

    Usage:
        breaker = CircuitBreaker()

        # Перед сделкой
        if not breaker.can_trade():
            return SKIP

        # После сделки
        breaker.record_outcome(win=True, loss_pct=0)
        # или
        breaker.record_outcome(win=False, loss_pct=1.5)
    """
    config: CircuitConfig = field(default_factory=CircuitConfig)

    # State
    state: CircuitState = CircuitState.CLOSED
    last_state_change: float = field(default_factory=time.time)

    # Counters
    consecutive_losses: int = 0
    daily_losses: int = 0
    daily_loss_pct: float = 0.0
    half_open_trades: int = 0
    recovery_wins: int = 0

    # History
    loss_history: List[LossRecord] = field(default_factory=list)

    # Date tracking
    _current_date: str = ""

    def __post_init__(self):
        self._current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _check_new_day(self) -> None:
        """Сбросить дневные счётчики если новый день."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._current_date:
            logger.info(f"New day detected ({today}), resetting daily counters")
            self._current_date = today
            self.daily_losses = 0
            self.daily_loss_pct = 0.0

    def can_trade(self) -> bool:
        """
        Проверить можно ли торговать.

        Returns:
            True если торговля разрешена, False если заблокирована
        """
        self._check_new_day()
        self._check_cooldown()

        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.HALF_OPEN:
            if self.half_open_trades < self.config.half_open_max_trades:
                return True
            logger.warning("Half-open trade limit reached")
            return False

        # OPEN state
        return False

    def _check_cooldown(self) -> None:
        """Проверить истёк ли cooldown."""
        if self.state != CircuitState.OPEN:
            return

        elapsed = time.time() - self.last_state_change
        if elapsed >= self.config.cooldown_seconds:
            self._transition_to(CircuitState.HALF_OPEN)

    def record_outcome(
        self,
        win: bool,
        loss_pct: float = 0.0,
        symbol: str = "",
        reason: str = ""
    ) -> None:
        """
        Записать результат сделки.

        Args:
            win: True если прибыль, False если убыток
            loss_pct: Процент убытка (положительное число)
            symbol: Символ торговой пары
            reason: Причина убытка (stop-loss, timeout, etc.)
        """
        self._check_new_day()

        if win:
            self._record_win()
        else:
            self._record_loss(loss_pct, symbol, reason)

    def _record_win(self) -> None:
        """Записать выигрыш."""
        self.consecutive_losses = 0

        if self.state == CircuitState.HALF_OPEN:
            self.recovery_wins += 1
            logger.info(f"Recovery win #{self.recovery_wins}")

            if self.recovery_wins >= self.config.recovery_wins_required:
                self._transition_to(CircuitState.CLOSED)
        elif self.state == CircuitState.CLOSED:
            # Продолжаем нормальную работу
            pass

    def _record_loss(
        self,
        loss_pct: float,
        symbol: str,
        reason: str
    ) -> None:
        """Записать убыток."""
        self.consecutive_losses += 1
        self.daily_losses += 1
        self.daily_loss_pct += loss_pct

        # Record in history
        record = LossRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            symbol=symbol,
            loss_pct=loss_pct,
            reason=reason,
        )
        self.loss_history.append(record)

        # Trim history to last 100
        if len(self.loss_history) > 100:
            self.loss_history = self.loss_history[-100:]

        logger.warning(
            f"Loss recorded: {symbol} -{loss_pct:.2f}% ({reason}) | "
            f"Consecutive: {self.consecutive_losses}, Daily: {self.daily_losses}"
        )

        if self.state == CircuitState.HALF_OPEN:
            self.half_open_trades += 1
            # Reset recovery progress on loss
            self.recovery_wins = 0

        # Check thresholds
        self._check_thresholds()

    def _check_thresholds(self) -> None:
        """Проверить превышение порогов."""
        should_open = False
        reason = ""

        if self.consecutive_losses >= self.config.max_consecutive_losses:
            should_open = True
            reason = f"consecutive_losses={self.consecutive_losses}"

        elif self.daily_losses >= self.config.max_daily_losses:
            should_open = True
            reason = f"daily_losses={self.daily_losses}"

        elif self.daily_loss_pct >= self.config.max_daily_loss_pct:
            should_open = True
            reason = f"daily_loss_pct={self.daily_loss_pct:.2f}%"

        if should_open and self.state != CircuitState.OPEN:
            logger.error(f"CIRCUIT BREAKER TRIGGERED: {reason}")
            self._transition_to(CircuitState.OPEN)

    def _transition_to(self, new_state: CircuitState) -> None:
        """Перейти в новое состояние."""
        old_state = self.state
        self.state = new_state
        self.last_state_change = time.time()

        if new_state == CircuitState.HALF_OPEN:
            self.half_open_trades = 0
            self.recovery_wins = 0

        elif new_state == CircuitState.CLOSED:
            self.consecutive_losses = 0

        logger.info(f"Circuit state: {old_state.value} -> {new_state.value}")

    def force_open(self, reason: str = "manual") -> None:
        """Принудительно открыть circuit breaker."""
        logger.warning(f"Circuit breaker FORCE OPEN: {reason}")
        self._transition_to(CircuitState.OPEN)

    def force_close(self, reason: str = "manual") -> None:
        """Принудительно закрыть circuit breaker (разрешить торговлю)."""
        logger.warning(f"Circuit breaker FORCE CLOSE: {reason}")
        self._transition_to(CircuitState.CLOSED)

    def get_status(self) -> Dict[str, Any]:
        """Получить текущий статус."""
        elapsed = time.time() - self.last_state_change
        remaining_cooldown = max(0, self.config.cooldown_seconds - elapsed)

        return {
            "state": self.state.value,
            "can_trade": self.can_trade(),
            "consecutive_losses": self.consecutive_losses,
            "daily_losses": self.daily_losses,
            "daily_loss_pct": round(self.daily_loss_pct, 2),
            "remaining_cooldown_sec": round(remaining_cooldown),
            "half_open_trades": self.half_open_trades,
            "recovery_wins": self.recovery_wins,
            "config": {
                "max_consecutive_losses": self.config.max_consecutive_losses,
                "max_daily_losses": self.config.max_daily_losses,
                "max_daily_loss_pct": self.config.max_daily_loss_pct,
                "cooldown_seconds": self.config.cooldown_seconds,
            },
        }


# === Singleton ===

_breaker: Optional[CircuitBreaker] = None


def get_circuit_breaker(config: Optional[CircuitConfig] = None) -> CircuitBreaker:
    """Get or create singleton circuit breaker."""
    global _breaker
    if _breaker is None:
        _breaker = CircuitBreaker(config=config or CircuitConfig())
    return _breaker


def can_trade() -> bool:
    """Convenience function to check if trading is allowed."""
    return get_circuit_breaker().can_trade()


def record_outcome(win: bool, loss_pct: float = 0.0, **kwargs) -> None:
    """Convenience function to record trade outcome."""
    get_circuit_breaker().record_outcome(win, loss_pct, **kwargs)
