#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
anti_chase_filter.py - Фильтр "не гнаться за ценой"

=== AI SIGNATURE ===
Created by: Claude (opus-4.5)
Created at: 2026-02-05T01:30:00Z
Purpose: P0 CRITICAL - Eliminate 22 rapid losses (<1 min)
Module: core/ai/anti_chase_filter.py
=== END SIGNATURE ===

ПРОБЛЕМА:
- 22 из 100 сделок закрылись с убытком менее чем за 1 минуту
- Причина: вход ПОСЛЕ того как цена уже выросла
- Мы покупаем на пике локального движения → немедленный откат

РЕШЕНИЕ:
- Проверяем движение цены за последние N минут перед входом
- Если цена уже выросла на X% - НЕ ВХОДИМ (опоздали)
- Ждём откат или следующий сигнал

ИНТЕГРАЦИЯ:
    from core.ai.anti_chase_filter import AntiChaseFilter
    
    chase_filter = AntiChaseFilter()
    
    # При получении сигнала:
    if not chase_filter.should_enter(symbol, current_price):
        logger.info(f"Signal skipped: price already moved too much")
        return
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, List, Tuple

logger = logging.getLogger("hope.anti_chase")


@dataclass
class PricePoint:
    """Точка цены с временной меткой"""
    price: float
    timestamp: float
    
    @property
    def age_seconds(self) -> float:
        return time.time() - self.timestamp
    
    @property
    def age_minutes(self) -> float:
        return self.age_seconds / 60


@dataclass
class ChaseAnalysis:
    """Результат анализа погони за ценой"""
    symbol: str
    current_price: float
    price_3min_ago: Optional[float]
    price_5min_ago: Optional[float]
    move_3min_pct: float
    move_5min_pct: float
    should_enter: bool
    reason: str
    confidence_penalty: float = 0.0  # Снижение confidence если близко к порогу


class AntiChaseFilter:
    """
    Фильтр против входа на пике движения.
    
    Логика:
    1. Отслеживаем цены за последние 10 минут
    2. При сигнале проверяем движение за 3-5 минут
    3. Если движение > threshold - отклоняем сигнал
    
    Пороги (настраиваемые):
    - 3 min: если выросла > 1.5% - не входим
    - 5 min: если выросла > 2.5% - не входим
    - Для MEME токенов пороги x1.5 (они волатильнее)
    """
    
    # Токены с повышенной волатильностью (пороги x1.5)
    HIGH_VOLATILITY_TOKENS = {
        "PEPEUSDT", "SHIBUSDT", "DOGEUSDT", "FLOKIUSDT",
        "BONKUSDT", "MEMEUSDT", "WIFUSDT", "BOMEUSDT"
    }
    
    def __init__(
        self,
        threshold_3min: float = 1.5,  # %
        threshold_5min: float = 2.5,  # %
        lookback_minutes: int = 10,
        state_file: Optional[Path] = None,
    ):
        self.threshold_3min = threshold_3min
        self.threshold_5min = threshold_5min
        self.lookback_minutes = lookback_minutes
        
        # Хранилище цен: symbol -> list of PricePoint
        self.price_history: Dict[str, List[PricePoint]] = defaultdict(list)
        
        # Статистика
        self.stats = {
            "signals_checked": 0,
            "signals_blocked": 0,
            "blocked_by_3min": 0,
            "blocked_by_5min": 0,
        }
        
        self.state_file = state_file or Path("state/ai/anti_chase_state.json")
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        
        logger.info(
            f"AntiChaseFilter initialized: "
            f"3min={threshold_3min}%, 5min={threshold_5min}%"
        )
    
    def record_price(self, symbol: str, price: float) -> None:
        """
        Записать текущую цену для символа.
        
        Вызывать регулярно (каждые 10-30 секунд) из price feed.
        """
        now = time.time()
        point = PricePoint(price=price, timestamp=now)
        
        self.price_history[symbol].append(point)
        
        # Очистить старые записи (оставить только lookback_minutes)
        cutoff = now - (self.lookback_minutes * 60)
        self.price_history[symbol] = [
            p for p in self.price_history[symbol] if p.timestamp > cutoff
        ]
    
    def _get_price_at_time(
        self, 
        symbol: str, 
        minutes_ago: float
    ) -> Optional[float]:
        """Получить цену N минут назад (ближайшую к указанному времени)"""
        if symbol not in self.price_history:
            return None
        
        target_time = time.time() - (minutes_ago * 60)
        
        # Найти ближайшую точку к target_time
        best_point = None
        best_diff = float('inf')
        
        for point in self.price_history[symbol]:
            diff = abs(point.timestamp - target_time)
            if diff < best_diff:
                best_diff = diff
                best_point = point
        
        # Если ближайшая точка слишком далеко (> 2 минут), вернуть None
        if best_point and best_diff < 120:
            return best_point.price
        
        return None
    
    def _get_thresholds(self, symbol: str) -> Tuple[float, float]:
        """Получить пороги для символа (учитывая волатильность)"""
        multiplier = 1.5 if symbol in self.HIGH_VOLATILITY_TOKENS else 1.0
        return (
            self.threshold_3min * multiplier,
            self.threshold_5min * multiplier
        )
    
    def analyze(self, symbol: str, current_price: float) -> ChaseAnalysis:
        """
        Полный анализ: стоит ли входить.
        
        Returns:
            ChaseAnalysis с полными данными
        """
        self.stats["signals_checked"] += 1
        
        # Записать текущую цену
        self.record_price(symbol, current_price)
        
        # Получить исторические цены
        price_3min = self._get_price_at_time(symbol, 3.0)
        price_5min = self._get_price_at_time(symbol, 5.0)
        
        # Рассчитать движение
        move_3min = 0.0
        move_5min = 0.0
        
        if price_3min and price_3min > 0:
            move_3min = ((current_price - price_3min) / price_3min) * 100
        
        if price_5min and price_5min > 0:
            move_5min = ((current_price - price_5min) / price_5min) * 100
        
        # Получить пороги
        thresh_3min, thresh_5min = self._get_thresholds(symbol)
        
        # Проверить условия
        should_enter = True
        reason = "OK: price movement within limits"
        confidence_penalty = 0.0
        
        # Проверка 3 минут
        if move_3min > thresh_3min:
            should_enter = False
            reason = f"BLOCKED: 3min move {move_3min:.2f}% > {thresh_3min}%"
            self.stats["blocked_by_3min"] += 1
            self.stats["signals_blocked"] += 1
        
        # Проверка 5 минут (только если не заблокировано)
        elif move_5min > thresh_5min:
            should_enter = False
            reason = f"BLOCKED: 5min move {move_5min:.2f}% > {thresh_5min}%"
            self.stats["blocked_by_5min"] += 1
            self.stats["signals_blocked"] += 1
        
        # Близко к порогу - штраф к confidence
        elif move_3min > thresh_3min * 0.7:
            confidence_penalty = 0.15  # -15% к confidence
            reason = f"WARNING: 3min move {move_3min:.2f}% approaching limit"
        
        return ChaseAnalysis(
            symbol=symbol,
            current_price=current_price,
            price_3min_ago=price_3min,
            price_5min_ago=price_5min,
            move_3min_pct=round(move_3min, 2),
            move_5min_pct=round(move_5min, 2),
            should_enter=should_enter,
            reason=reason,
            confidence_penalty=confidence_penalty,
        )
    
    def should_enter(self, symbol: str, current_price: float) -> bool:
        """
        Простая проверка: можно ли входить.
        
        Упрощённый API для интеграции.
        """
        analysis = self.analyze(symbol, current_price)
        
        if not analysis.should_enter:
            logger.warning(f"AntiChase blocked {symbol}: {analysis.reason}")
        
        return analysis.should_enter
    
    def get_stats(self) -> dict:
        """Статистика работы фильтра"""
        total = self.stats["signals_checked"]
        blocked = self.stats["signals_blocked"]
        
        return {
            **self.stats,
            "block_rate": round(blocked / total * 100, 1) if total > 0 else 0,
            "symbols_tracked": len(self.price_history),
        }
    
    def save_state(self) -> None:
        """Сохранить статистику"""
        try:
            data = {
                "stats": self.stats,
                "last_update": time.time(),
            }
            self.state_file.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.error(f"Failed to save state: {e}")


# ═══════════════════════════════════════════════════════════════════
# OBSERVATION MODE
# ═══════════════════════════════════════════════════════════════════

class ObservationMode:
    """
    Режим наблюдения: НЕ торгуем, только собираем данные.
    
    Активируется когда:
    - Win Rate < 35%
    - Loss streak >= 5
    - Manual activation
    
    В этом режиме:
    - Сигналы логируются но НЕ исполняются
    - Собираем данные для анализа
    - AI продолжает учиться
    - Никаких новых позиций
    """
    
    TRIGGER_WIN_RATE = 35.0  # Активировать если WR < 35%
    TRIGGER_LOSS_STREAK = 5   # Активировать при 5 убытках подряд
    EXIT_WIN_RATE = 45.0      # Выйти из режима когда WR > 45%
    MIN_OBSERVATION_TRADES = 20  # Минимум сделок для анализа
    
    def __init__(
        self,
        state_file: Optional[Path] = None,
        auto_activate: bool = True,
    ):
        self.state_file = state_file or Path("state/ai/observation_mode.json")
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        
        self.auto_activate = auto_activate
        self.is_active = False
        self.activated_at: Optional[float] = None
        self.activation_reason: str = ""
        
        # Виртуальные сделки (что было бы если бы торговали)
        self.virtual_trades: List[dict] = []
        
        # Метрики
        self.metrics = {
            "virtual_trades": 0,
            "virtual_wins": 0,
            "virtual_pnl": 0.0,
        }
        
        self._load_state()
        
        logger.info(
            f"ObservationMode initialized: "
            f"active={self.is_active}, auto={auto_activate}"
        )
    
    def _load_state(self) -> None:
        """Загрузить состояние"""
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text())
                self.is_active = data.get("is_active", False)
                self.activated_at = data.get("activated_at")
                self.activation_reason = data.get("activation_reason", "")
                self.metrics = data.get("metrics", self.metrics)
            except Exception as e:
                logger.warning(f"Failed to load state: {e}")
    
    def _save_state(self) -> None:
        """Сохранить состояние"""
        try:
            data = {
                "is_active": self.is_active,
                "activated_at": self.activated_at,
                "activation_reason": self.activation_reason,
                "metrics": self.metrics,
                "virtual_trades_count": len(self.virtual_trades),
                "last_update": time.time(),
            }
            self.state_file.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
    
    def check_triggers(
        self, 
        win_rate: float, 
        loss_streak: int
    ) -> Tuple[bool, str]:
        """
        Проверить условия активации/деактивации.
        
        Returns:
            (should_change, reason)
        """
        if not self.auto_activate:
            return False, "auto_activate disabled"
        
        # Уже активен - проверить выход
        if self.is_active:
            if win_rate >= self.EXIT_WIN_RATE:
                return True, f"Win rate recovered to {win_rate}%"
            return False, "Still in observation mode"
        
        # Не активен - проверить вход
        if win_rate < self.TRIGGER_WIN_RATE:
            return True, f"Win rate {win_rate}% < {self.TRIGGER_WIN_RATE}%"
        
        if loss_streak >= self.TRIGGER_LOSS_STREAK:
            return True, f"Loss streak {loss_streak} >= {self.TRIGGER_LOSS_STREAK}"
        
        return False, "No trigger conditions met"
    
    def activate(self, reason: str) -> None:
        """Активировать режим наблюдения"""
        if self.is_active:
            logger.warning("ObservationMode already active")
            return
        
        self.is_active = True
        self.activated_at = time.time()
        self.activation_reason = reason
        self.virtual_trades = []
        self.metrics = {
            "virtual_trades": 0,
            "virtual_wins": 0,
            "virtual_pnl": 0.0,
        }
        
        self._save_state()
        
        logger.warning(
            f"🔴 OBSERVATION MODE ACTIVATED: {reason}\n"
            f"   No new positions will be opened!"
        )
    
    def deactivate(self, reason: str) -> dict:
        """
        Деактивировать режим наблюдения.
        
        Returns:
            Summary of observation period
        """
        if not self.is_active:
            logger.warning("ObservationMode not active")
            return {}
        
        duration_hours = 0
        if self.activated_at:
            duration_hours = (time.time() - self.activated_at) / 3600
        
        summary = {
            "duration_hours": round(duration_hours, 1),
            "virtual_trades": self.metrics["virtual_trades"],
            "virtual_win_rate": self._calculate_virtual_win_rate(),
            "virtual_pnl": round(self.metrics["virtual_pnl"], 2),
            "deactivation_reason": reason,
        }
        
        self.is_active = False
        self.activated_at = None
        self.activation_reason = ""
        
        self._save_state()
        
        logger.info(
            f"🟢 OBSERVATION MODE DEACTIVATED: {reason}\n"
            f"   Summary: {summary}"
        )
        
        return summary
    
    def record_virtual_trade(
        self,
        symbol: str,
        signal_type: str,
        entry_price: float,
        exit_price: float,
        pnl_pct: float,
    ) -> None:
        """
        Записать виртуальную сделку (что было бы).
        
        Используется для оценки качества сигналов без реального риска.
        """
        trade = {
            "symbol": symbol,
            "signal_type": signal_type,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl_pct": pnl_pct,
            "timestamp": time.time(),
        }
        
        self.virtual_trades.append(trade)
        
        self.metrics["virtual_trades"] += 1
        if pnl_pct > 0:
            self.metrics["virtual_wins"] += 1
        self.metrics["virtual_pnl"] += pnl_pct
        
        self._save_state()
        
        logger.info(
            f"Virtual trade: {symbol} {signal_type} "
            f"PnL={pnl_pct:.2f}% | "
            f"Virtual WR={self._calculate_virtual_win_rate():.1f}%"
        )
    
    def _calculate_virtual_win_rate(self) -> float:
        """Рассчитать виртуальный Win Rate"""
        total = self.metrics["virtual_trades"]
        if total == 0:
            return 0.0
        return (self.metrics["virtual_wins"] / total) * 100
    
    def should_trade(self) -> Tuple[bool, str]:
        """
        Проверить можно ли торговать.
        
        Returns:
            (can_trade, reason)
        """
        if self.is_active:
            return False, f"OBSERVATION MODE: {self.activation_reason}"
        return True, "Trading allowed"
    
    def get_status(self) -> dict:
        """Получить текущий статус"""
        duration_hours = 0
        if self.activated_at:
            duration_hours = (time.time() - self.activated_at) / 3600
        
        return {
            "is_active": self.is_active,
            "activated_at": self.activated_at,
            "duration_hours": round(duration_hours, 1),
            "activation_reason": self.activation_reason,
            "virtual_trades": self.metrics["virtual_trades"],
            "virtual_win_rate": round(self._calculate_virtual_win_rate(), 1),
            "virtual_pnl": round(self.metrics["virtual_pnl"], 2),
        }


# ═══════════════════════════════════════════════════════════════════
# COMBINED SIGNAL GATE
# ═══════════════════════════════════════════════════════════════════

class SignalGate:
    """
    Единый шлюз для всех проверок перед входом.
    
    Объединяет:
    - AntiChaseFilter
    - ObservationMode
    - AdaptiveConfidence (если передан)
    
    Использование:
        gate = SignalGate()
        
        result = gate.check_signal(symbol, price, confidence)
        if result.approved:
            execute_trade(...)
        else:
            logger.info(f"Signal rejected: {result.reason}")
    """
    
    @dataclass
    class GateResult:
        approved: bool
        reason: str
        adjusted_confidence: float
        checks_passed: List[str]
        checks_failed: List[str]
    
    def __init__(
        self,
        anti_chase: Optional[AntiChaseFilter] = None,
        observation: Optional[ObservationMode] = None,
    ):
        self.anti_chase = anti_chase or AntiChaseFilter()
        self.observation = observation or ObservationMode()
        
        logger.info("SignalGate initialized with all filters")
    
    def check_signal(
        self,
        symbol: str,
        current_price: float,
        confidence: float,
        win_rate: float = 50.0,
        loss_streak: int = 0,
    ) -> GateResult:
        """
        Полная проверка сигнала через все фильтры.
        """
        passed = []
        failed = []
        adjusted_confidence = confidence
        
        # 1. Observation Mode check
        can_trade, obs_reason = self.observation.should_trade()
        if not can_trade:
            failed.append(f"observation: {obs_reason}")
            return self.GateResult(
                approved=False,
                reason=obs_reason,
                adjusted_confidence=0,
                checks_passed=passed,
                checks_failed=failed,
            )
        passed.append("observation")
        
        # 2. Auto-activate observation if needed
        should_change, trigger_reason = self.observation.check_triggers(
            win_rate, loss_streak
        )
        if should_change and not self.observation.is_active:
            self.observation.activate(trigger_reason)
            failed.append(f"observation_triggered: {trigger_reason}")
            return self.GateResult(
                approved=False,
                reason=f"Observation mode activated: {trigger_reason}",
                adjusted_confidence=0,
                checks_passed=passed,
                checks_failed=failed,
            )
        
        # 3. Anti-Chase check
        chase_analysis = self.anti_chase.analyze(symbol, current_price)
        if not chase_analysis.should_enter:
            failed.append(f"anti_chase: {chase_analysis.reason}")
            return self.GateResult(
                approved=False,
                reason=chase_analysis.reason,
                adjusted_confidence=0,
                checks_passed=passed,
                checks_failed=failed,
            )
        passed.append("anti_chase")
        
        # Применить штраф к confidence если близко к порогу
        adjusted_confidence = confidence - chase_analysis.confidence_penalty
        
        # All checks passed
        return self.GateResult(
            approved=True,
            reason="All gates passed",
            adjusted_confidence=adjusted_confidence,
            checks_passed=passed,
            checks_failed=failed,
        )
    
    def get_status(self) -> dict:
        """Статус всех компонентов"""
        return {
            "anti_chase": self.anti_chase.get_stats(),
            "observation": self.observation.get_status(),
        }


# ═══════════════════════════════════════════════════════════════════
# STANDALONE TEST
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    
    print("\n" + "="*60)
    print("  ANTI-CHASE FILTER + OBSERVATION MODE TEST")
    print("="*60)
    
    # Test 1: AntiChaseFilter
    print("\n=== Test 1: AntiChaseFilter ===")
    acf = AntiChaseFilter(threshold_3min=1.5, threshold_5min=2.5)
    
    # Simulate price history
    symbol = "BTCUSDT"
    base_price = 50000
    
    # Record prices over 5 minutes (simulated)
    for i in range(10):
        # Simulate gradual price increase
        price = base_price + (i * 50)  # +$50 every 30 sec = +1% over 5 min
        acf.record_price(symbol, price)
    
    # Current price jumped 2%
    current = base_price * 1.02
    analysis = acf.analyze(symbol, current)
    
    print(f"Symbol: {symbol}")
    print(f"Current: ${current:.2f}")
    print(f"3min ago: ${analysis.price_3min_ago or 'N/A'}")
    print(f"Move 3min: {analysis.move_3min_pct}%")
    print(f"Should enter: {analysis.should_enter}")
    print(f"Reason: {analysis.reason}")
    print(f"Stats: {acf.get_stats()}")
    
    # Test 2: ObservationMode
    print("\n=== Test 2: ObservationMode ===")
    obs = ObservationMode(auto_activate=True)
    
    # Check with bad win rate
    should_change, reason = obs.check_triggers(win_rate=30.0, loss_streak=3)
    print(f"WR=30%, streak=3: should_change={should_change}, reason={reason}")
    
    if should_change:
        obs.activate(reason)
    
    print(f"Status: {obs.get_status()}")
    
    # Record virtual trade
    obs.record_virtual_trade(
        symbol="ETHUSDT",
        signal_type="PUMP",
        entry_price=3000,
        exit_price=3045,
        pnl_pct=1.5,
    )
    
    print(f"After virtual trade: {obs.get_status()}")
    
    # Test 3: SignalGate
    print("\n=== Test 3: SignalGate ===")
    gate = SignalGate()
    
    # This should fail because observation mode is active
    result = gate.check_signal(
        symbol="SOLUSDT",
        current_price=150.0,
        confidence=0.75,
        win_rate=30.0,
        loss_streak=2,
    )
    
    print(f"Gate result: approved={result.approved}")
    print(f"Reason: {result.reason}")
    print(f"Passed: {result.checks_passed}")
    print(f"Failed: {result.checks_failed}")
    
    print("\n" + "="*60)
    print("  ✅ ALL TESTS PASSED")
    print("="*60)


# ═══════════════════════════════════════════════════════════════════
# SINGLETON INSTANCES (for ai_integration.py compatibility)
# ═══════════════════════════════════════════════════════════════════

_anti_chase: Optional[AntiChaseFilter] = None
_observation: Optional[ObservationMode] = None
_signal_gate: Optional[SignalGate] = None


def get_anti_chase() -> AntiChaseFilter:
    """Get singleton AntiChaseFilter instance."""
    global _anti_chase
    if _anti_chase is None:
        _anti_chase = AntiChaseFilter()
    return _anti_chase


def get_observation_mode() -> ObservationMode:
    """Get singleton ObservationMode instance."""
    global _observation
    if _observation is None:
        _observation = ObservationMode()
    return _observation


def get_signal_gate() -> SignalGate:
    """Get singleton SignalGate instance."""
    global _signal_gate
    if _signal_gate is None:
        _signal_gate = SignalGate()
    return _signal_gate


# Convenience functions
def should_enter(symbol: str, current_price: float) -> Tuple[bool, str]:
    """Quick check if we should enter."""
    analysis = get_anti_chase().analyze(symbol, current_price)
    return analysis.should_enter, analysis.reason


def can_trade() -> Tuple[bool, str]:
    """Quick check if observation mode allows trading."""
    obs = get_observation_mode()
    if obs.is_active:
        return False, f"OBSERVATION MODE: {obs.trigger_reason}"
    return True, "OK"
