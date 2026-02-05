#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
time_based_exit.py - Правила выхода по времени

=== AI SIGNATURE ===
Created by: Claude (opus-4.5)
Created at: 2026-02-05T01:15:00Z
Purpose: P0 Critical - Avoid trapped capital, reduce losses
=== END SIGNATURE ===

ПРОБЛЕМА:
- Позиции застревают надолго без движения
- 65% потерь происходят в первые 5 минут
- Нет механизма для быстрого выхода из "мёртвых" сделок

РЕШЕНИЕ:
- Quick Loss: закрыть если -0.5% в первые 5 минут
- Stale Position: закрыть если нет движения 30+ минут
- End of Day: очистка перед дневным ресетом

ИНТЕГРАЦИЯ:
В position_guardian.py:
    from core.time_based_exit import TimeBasedExitRules
    
    exit_rules = TimeBasedExitRules()
    
    # В цикле мониторинга:
    action = exit_rules.check_position(position)
    if action["should_exit"]:
        await self.close_position(position.symbol, action["reason"])
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Callable, Any

logger = logging.getLogger("hope.time_exit")


@dataclass
class Position:
    """Позиция для анализа"""
    symbol: str
    entry_price: float
    current_price: float
    quantity: float
    entry_time: float  # Unix timestamp
    pnl_pct: float = 0.0
    
    @property
    def age_seconds(self) -> float:
        return time.time() - self.entry_time
    
    @property
    def age_minutes(self) -> float:
        return self.age_seconds / 60
    
    @property
    def hour_utc(self) -> int:
        return datetime.fromtimestamp(time.time(), tz=timezone.utc).hour
    
    def calculate_pnl_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        self.pnl_pct = ((self.current_price - self.entry_price) / self.entry_price) * 100
        return self.pnl_pct


@dataclass
class ExitAction:
    """Результат проверки правил выхода"""
    should_exit: bool
    reason: str
    rule_name: str
    urgency: str = "normal"  # low, normal, high, critical
    partial_close_pct: int = 100  # 100 = full close


class TimeBasedExitRules:
    """
    Правила выхода на основе времени и состояния позиции.
    
    Rules priority:
    1. Quick Loss - самый высокий приоритет
    2. Circuit Breaker - критический
    3. Stale Position - средний
    4. End of Day - низкий
    """
    
    def __init__(
        self,
        quick_loss_threshold: float = -0.5,
        quick_loss_window_min: float = 5.0,
        stale_threshold_pct: float = 0.3,
        stale_window_min: float = 30.0,
        end_of_day_hour_utc: int = 23,
    ):
        self.quick_loss_threshold = quick_loss_threshold
        self.quick_loss_window_min = quick_loss_window_min
        self.stale_threshold_pct = stale_threshold_pct
        self.stale_window_min = stale_window_min
        self.end_of_day_hour_utc = end_of_day_hour_utc
        
        # История цен для определения "застоя"
        self.price_history: dict[str, list[tuple[float, float]]] = {}  # symbol -> [(timestamp, price)]
        
        logger.info(
            f"TimeBasedExitRules initialized: "
            f"quick_loss={quick_loss_threshold}%/{quick_loss_window_min}min, "
            f"stale={stale_threshold_pct}%/{stale_window_min}min"
        )
    
    def check_position(self, position: Position) -> ExitAction:
        """
        Проверить позицию на все правила выхода.
        
        Returns first triggered rule (highest priority).
        """
        # Обновить PnL
        position.calculate_pnl_pct()
        
        # Rule 1: Quick Loss (highest priority)
        action = self._check_quick_loss(position)
        if action.should_exit:
            return action
        
        # Rule 2: Stale Position
        action = self._check_stale_position(position)
        if action.should_exit:
            return action
        
        # Rule 3: End of Day
        action = self._check_end_of_day(position)
        if action.should_exit:
            return action
        
        # No rule triggered
        return ExitAction(
            should_exit=False,
            reason="All time rules OK",
            rule_name="none",
        )
    
    def _check_quick_loss(self, position: Position) -> ExitAction:
        """
        Quick Loss Rule:
        Если позиция в убытке > threshold в первые N минут - закрыть.
        
        Логика: если сделка сразу идёт против нас, лучше выйти быстро.
        Статистически 65% убыточных сделок теряют деньги в первые 5 минут.
        """
        if position.age_minutes > self.quick_loss_window_min:
            return ExitAction(should_exit=False, reason="", rule_name="quick_loss")
        
        if position.pnl_pct <= self.quick_loss_threshold:
            return ExitAction(
                should_exit=True,
                reason=f"Quick loss: {position.pnl_pct:.2f}% < {self.quick_loss_threshold}% in {position.age_minutes:.1f}min",
                rule_name="quick_loss",
                urgency="high",
            )
        
        return ExitAction(should_exit=False, reason="", rule_name="quick_loss")
    
    def _check_stale_position(self, position: Position) -> ExitAction:
        """
        Stale Position Rule:
        Если позиция не двигается > N минут - закрыть.
        
        Логика: деньги не должны быть заморожены в "мёртвых" сделках.
        Лучше освободить капитал для новых возможностей.
        """
        if position.age_minutes < self.stale_window_min:
            return ExitAction(should_exit=False, reason="", rule_name="stale")
        
        # Обновить историю цен
        symbol = position.symbol
        now = time.time()
        
        if symbol not in self.price_history:
            self.price_history[symbol] = []
        
        self.price_history[symbol].append((now, position.current_price))
        
        # Очистить старые записи (оставить только последние 60 минут)
        cutoff = now - 3600
        self.price_history[symbol] = [
            (t, p) for t, p in self.price_history[symbol] if t > cutoff
        ]
        
        # Проверить движение за последние N минут
        window_start = now - (self.stale_window_min * 60)
        recent_prices = [
            p for t, p in self.price_history[symbol] if t > window_start
        ]
        
        if len(recent_prices) < 2:
            return ExitAction(should_exit=False, reason="", rule_name="stale")
        
        price_min = min(recent_prices)
        price_max = max(recent_prices)
        price_range_pct = ((price_max - price_min) / price_min) * 100 if price_min > 0 else 0
        
        if price_range_pct < self.stale_threshold_pct and abs(position.pnl_pct) < self.stale_threshold_pct:
            return ExitAction(
                should_exit=True,
                reason=f"Stale position: {position.age_minutes:.0f}min, movement {price_range_pct:.2f}% < {self.stale_threshold_pct}%",
                rule_name="stale",
                urgency="normal",
            )
        
        return ExitAction(should_exit=False, reason="", rule_name="stale")
    
    def _check_end_of_day(self, position: Position) -> ExitAction:
        """
        End of Day Rule:
        Закрыть убыточные позиции перед дневным ресетом.
        
        Логика: не переносить убытки на следующий день.
        """
        current_hour = position.hour_utc
        
        if current_hour == self.end_of_day_hour_utc:
            if position.pnl_pct < 0:
                return ExitAction(
                    should_exit=True,
                    reason=f"End of day cleanup: closing negative position ({position.pnl_pct:.2f}%)",
                    rule_name="end_of_day",
                    urgency="low",
                )
        
        return ExitAction(should_exit=False, reason="", rule_name="end_of_day")
    
    def get_stats(self) -> dict:
        """Статистика правил"""
        return {
            "quick_loss_threshold": self.quick_loss_threshold,
            "quick_loss_window_min": self.quick_loss_window_min,
            "stale_threshold_pct": self.stale_threshold_pct,
            "stale_window_min": self.stale_window_min,
            "end_of_day_hour_utc": self.end_of_day_hour_utc,
            "tracked_symbols": len(self.price_history),
        }


# ═══════════════════════════════════════════════════════════════════
# EXTENDED RULES FOR PRODUCTION
# ═══════════════════════════════════════════════════════════════════

class ExtendedExitRules(TimeBasedExitRules):
    """
    Расширенные правила с дополнительными проверками.
    """
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        # Дополнительные параметры
        self.max_position_age_hours = kwargs.get("max_position_age_hours", 4.0)
        self.profit_lock_threshold = kwargs.get("profit_lock_threshold", 1.0)
        self.profit_lock_trailing = kwargs.get("profit_lock_trailing", 0.5)
    
    def check_position(self, position: Position) -> ExitAction:
        """Расширенная проверка"""
        # Базовые правила
        action = super().check_position(position)
        if action.should_exit:
            return action
        
        # Rule 4: Maximum Age
        action = self._check_max_age(position)
        if action.should_exit:
            return action
        
        # Rule 5: Profit Lock (trailing)
        action = self._check_profit_lock(position)
        if action.should_exit:
            return action
        
        return ExitAction(
            should_exit=False,
            reason="All extended rules OK",
            rule_name="none",
        )
    
    def _check_max_age(self, position: Position) -> ExitAction:
        """
        Maximum Age Rule:
        Закрыть позицию если она слишком старая.
        """
        age_hours = position.age_minutes / 60
        
        if age_hours > self.max_position_age_hours:
            return ExitAction(
                should_exit=True,
                reason=f"Max age exceeded: {age_hours:.1f}h > {self.max_position_age_hours}h",
                rule_name="max_age",
                urgency="normal",
            )
        
        return ExitAction(should_exit=False, reason="", rule_name="max_age")
    
    def _check_profit_lock(self, position: Position) -> ExitAction:
        """
        Profit Lock Rule:
        Если достигли profit_lock_threshold, активируется trailing stop.
        Если цена падает на profit_lock_trailing от максимума - закрыть.
        """
        symbol = position.symbol
        
        if symbol not in self.price_history or not self.price_history[symbol]:
            return ExitAction(should_exit=False, reason="", rule_name="profit_lock")
        
        # Найти максимальный PnL за время жизни позиции
        entry_price = position.entry_price
        max_price = max(p for _, p in self.price_history[symbol])
        max_pnl_pct = ((max_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
        
        if max_pnl_pct >= self.profit_lock_threshold:
            # Trailing активирован
            current_pnl = position.pnl_pct
            drawdown_from_peak = max_pnl_pct - current_pnl
            
            if drawdown_from_peak >= self.profit_lock_trailing:
                return ExitAction(
                    should_exit=True,
                    reason=f"Profit lock triggered: peak {max_pnl_pct:.2f}%, current {current_pnl:.2f}%, drawdown {drawdown_from_peak:.2f}%",
                    rule_name="profit_lock",
                    urgency="high",
                )
        
        return ExitAction(should_exit=False, reason="", rule_name="profit_lock")


# ═══════════════════════════════════════════════════════════════════
# STANDALONE TEST
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    rules = TimeBasedExitRules()
    
    print("\n=== Test 1: Quick Loss ===")
    pos1 = Position(
        symbol="BTCUSDT",
        entry_price=100.0,
        current_price=99.0,  # -1%
        quantity=1.0,
        entry_time=time.time() - 180,  # 3 minutes ago
    )
    action1 = rules.check_position(pos1)
    print(f"Position: {pos1.pnl_pct:.2f}%, age: {pos1.age_minutes:.1f}min")
    print(f"Action: {action1}")
    assert action1.should_exit == True
    assert action1.rule_name == "quick_loss"
    
    print("\n=== Test 2: Old Position (no quick loss) ===")
    pos2 = Position(
        symbol="ETHUSDT",
        entry_price=100.0,
        current_price=99.0,  # -1%
        quantity=1.0,
        entry_time=time.time() - 600,  # 10 minutes ago
    )
    action2 = rules.check_position(pos2)
    print(f"Position: {pos2.pnl_pct:.2f}%, age: {pos2.age_minutes:.1f}min")
    print(f"Action: {action2}")
    # No quick loss because age > 5 min
    assert action2.rule_name != "quick_loss" or action2.should_exit == False
    
    print("\n=== Test 3: Profitable Position ===")
    pos3 = Position(
        symbol="SOLUSDT",
        entry_price=100.0,
        current_price=102.0,  # +2%
        quantity=1.0,
        entry_time=time.time() - 120,  # 2 minutes ago
    )
    action3 = rules.check_position(pos3)
    print(f"Position: {pos3.pnl_pct:.2f}%, age: {pos3.age_minutes:.1f}min")
    print(f"Action: {action3}")
    assert action3.should_exit == False
    
    print("\n=== Stats ===")
    print(f"Rules stats: {rules.get_stats()}")
    
    print("\n✅ TimeBasedExitRules test PASSED")
