# -*- coding: utf-8 -*-
"""
HOPE v4.0 LIVE TRADING PATCH
============================

Этот файл ПАТЧИТ pump_detector.py для LIVE торговли.
НЕ создаёт отдельный entrypoint (избегаем дублирования).

ПРИМЕНЕНИЕ:
1. В pump_detector.py добавить: from core.live_trading_patch import patch_for_live
2. В начале main(): patch_for_live()

АРХИТЕКТУРА:
  pump_detector (WebSocket) 
       ↓ REALTIME сигнал
  _handle_signal()
       ↓
  Trading Engine (Signal Gate → Adaptive TP → Binance OCO)
       ↓
  Trade Logger → state/trades/trades.jsonl
"""

import os
import sys
import logging
from typing import Dict, Any, Optional, Callable
from functools import wraps

log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# LIVE BARRIER (FAIL-CLOSED)
# ══════════════════════════════════════════════════════════════════════════════

class LiveBarrier:
    """
    Двойной барьер для LIVE режима.
    Без обоих флагов = DRY mode (никаких ордеров).
    """
    
    def __init__(self):
        self.mode = os.environ.get("HOPE_MODE", "DRY").upper()
        self.ack = os.environ.get("HOPE_LIVE_ACK", "").upper()
        self.testnet = os.environ.get("BINANCE_TESTNET", "1") == "1"
        
        self._validate()
    
    def _validate(self):
        """Проверка конфигурации."""
        if self.mode == "LIVE":
            if self.ack != "YES_I_UNDERSTAND":
                log.warning("⚠️ HOPE_MODE=LIVE but HOPE_LIVE_ACK not set! Falling back to DRY")
                self.mode = "DRY"
            elif self.testnet:
                log.warning("⚠️ HOPE_MODE=LIVE but BINANCE_TESTNET=1! Using TESTNET")
                self.mode = "TESTNET"
    
    @property
    def is_live(self) -> bool:
        return self.mode == "LIVE" and self.ack == "YES_I_UNDERSTAND" and not self.testnet
    
    @property
    def is_testnet(self) -> bool:
        return self.mode == "TESTNET" or (self.mode == "LIVE" and self.testnet)
    
    @property
    def is_dry(self) -> bool:
        return self.mode == "DRY" or (self.mode == "LIVE" and self.ack != "YES_I_UNDERSTAND")
    
    def get_status(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "ack": bool(self.ack),
            "testnet": self.testnet,
            "effective_mode": "LIVE" if self.is_live else ("TESTNET" if self.is_testnet else "DRY"),
            "orders_enabled": self.is_live or self.is_testnet,
        }
    
    def __str__(self):
        return f"LiveBarrier({self.get_status()['effective_mode']})"


# ══════════════════════════════════════════════════════════════════════════════
# RATE LIMITER (Binance API protection)
# ══════════════════════════════════════════════════════════════════════════════

import time
from collections import deque
from threading import Lock

class RateLimiter:
    """
    Защита от превышения лимитов Binance API.
    Default: 10 orders/second, 100 orders/minute
    """
    
    def __init__(self, per_second: int = 10, per_minute: int = 100):
        self.per_second = per_second
        self.per_minute = per_minute
        self._second_window: deque = deque()
        self._minute_window: deque = deque()
        self._lock = Lock()
    
    def acquire(self) -> bool:
        """
        Попытка получить слот для запроса.
        Returns: True если можно, False если нужно подождать.
        """
        now = time.time()
        
        with self._lock:
            # Clean old entries
            while self._second_window and self._second_window[0] < now - 1:
                self._second_window.popleft()
            while self._minute_window and self._minute_window[0] < now - 60:
                self._minute_window.popleft()
            
            # Check limits
            if len(self._second_window) >= self.per_second:
                return False
            if len(self._minute_window) >= self.per_minute:
                return False
            
            # Record request
            self._second_window.append(now)
            self._minute_window.append(now)
            return True
    
    def wait_and_acquire(self, timeout: float = 5.0) -> bool:
        """Подождать и получить слот."""
        start = time.time()
        while time.time() - start < timeout:
            if self.acquire():
                return True
            time.sleep(0.1)
        return False


# ══════════════════════════════════════════════════════════════════════════════
# CIRCUIT BREAKER (защита от серии убытков)
# ══════════════════════════════════════════════════════════════════════════════

class CircuitBreaker:
    """
    Автоматическое отключение торговли при серии убытков.
    """
    
    def __init__(
        self,
        max_consecutive_losses: int = 5,
        max_daily_loss_pct: float = 10.0,
        cooldown_minutes: int = 30
    ):
        self.max_consecutive_losses = max_consecutive_losses
        self.max_daily_loss_pct = max_daily_loss_pct
        self.cooldown_minutes = cooldown_minutes
        
        self._consecutive_losses = 0
        self._daily_pnl_pct = 0.0
        self._tripped_at: Optional[float] = None
        self._trip_reason: Optional[str] = None
    
    def record_trade(self, pnl_pct: float):
        """Записать результат сделки."""
        self._daily_pnl_pct += pnl_pct
        
        if pnl_pct < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0
        
        # Check trip conditions
        if self._consecutive_losses >= self.max_consecutive_losses:
            self._trip(f"consecutive_losses={self._consecutive_losses}")
        elif self._daily_pnl_pct <= -self.max_daily_loss_pct:
            self._trip(f"daily_loss={self._daily_pnl_pct:.1f}%")
    
    def _trip(self, reason: str):
        """Сработал circuit breaker."""
        self._tripped_at = time.time()
        self._trip_reason = reason
        log.critical(f"🛑 CIRCUIT BREAKER TRIPPED: {reason}")
    
    def is_open(self) -> bool:
        """Проверить можно ли торговать."""
        if self._tripped_at is None:
            return False
        
        # Auto-reset after cooldown
        if time.time() - self._tripped_at > self.cooldown_minutes * 60:
            self.reset()
            return False
        
        return True
    
    def reset(self):
        """Сбросить circuit breaker."""
        self._tripped_at = None
        self._trip_reason = None
        self._consecutive_losses = 0
        log.info("Circuit breaker reset")
    
    def reset_daily(self):
        """Сброс дневной статистики (вызывать в 00:00 UTC)."""
        self._daily_pnl_pct = 0.0
        self._consecutive_losses = 0


# ══════════════════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ══════════════════════════════════════════════════════════════════════════════

class HealthMonitor:
    """
    Мониторинг здоровья системы.
    Если нет heartbeat > 60 сек — система считается мёртвой.
    """
    
    def __init__(self, timeout_sec: int = 60):
        self.timeout_sec = timeout_sec
        self._last_heartbeat = time.time()
        self._signals_processed = 0
        self._trades_executed = 0
        self._errors = 0
    
    def heartbeat(self):
        """Обновить heartbeat."""
        self._last_heartbeat = time.time()
    
    def record_signal(self):
        self._signals_processed += 1
        self.heartbeat()
    
    def record_trade(self):
        self._trades_executed += 1
        self.heartbeat()
    
    def record_error(self):
        self._errors += 1
    
    def is_healthy(self) -> bool:
        return time.time() - self._last_heartbeat < self.timeout_sec
    
    def get_status(self) -> Dict[str, Any]:
        return {
            "healthy": self.is_healthy(),
            "last_heartbeat": self._last_heartbeat,
            "seconds_since_heartbeat": time.time() - self._last_heartbeat,
            "signals_processed": self._signals_processed,
            "trades_executed": self._trades_executed,
            "errors": self._errors,
        }


# ══════════════════════════════════════════════════════════════════════════════
# GLOBAL INSTANCES
# ══════════════════════════════════════════════════════════════════════════════

_barrier: Optional[LiveBarrier] = None
_rate_limiter: Optional[RateLimiter] = None
_circuit_breaker: Optional[CircuitBreaker] = None
_health_monitor: Optional[HealthMonitor] = None

def get_live_barrier() -> LiveBarrier:
    global _barrier
    if _barrier is None:
        _barrier = LiveBarrier()
    return _barrier

def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter

def get_circuit_breaker() -> CircuitBreaker:
    global _circuit_breaker
    if _circuit_breaker is None:
        _circuit_breaker = CircuitBreaker()
    return _circuit_breaker

def get_health_monitor() -> HealthMonitor:
    global _health_monitor
    if _health_monitor is None:
        _health_monitor = HealthMonitor()
    return _health_monitor


# ══════════════════════════════════════════════════════════════════════════════
# PATCH FUNCTION
# ══════════════════════════════════════════════════════════════════════════════

def patch_for_live():
    """
    Вызвать в начале main() для активации LIVE режима.
    """
    barrier = get_live_barrier()
    
    mode = barrier.get_status()['effective_mode']
    print(f"""
╔═══════════════════════════════════════════════════════════════════╗
║  HOPE AI Trading System v4.0                                      ║
║  Mode: {mode:8}                                                   ║
║  Orders: {'ENABLED ' if barrier.is_live or barrier.is_testnet else 'DISABLED'}                                                  ║
╚═══════════════════════════════════════════════════════════════════╝
""")
    
    if barrier.is_live:
        log.warning("🔴 LIVE MODE ACTIVE - Real money at risk!")
    elif barrier.is_testnet:
        log.info("🟡 TESTNET MODE - Using Binance testnet")
    else:
        log.info("🟢 DRY MODE - No real orders")
    
    return barrier


# ══════════════════════════════════════════════════════════════════════════════
# TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    print("=" * 60)
    print("LIVE TRADING PATCH TEST")
    print("=" * 60)
    
    # Test barrier
    barrier = patch_for_live()
    print(f"\nBarrier status: {barrier.get_status()}")
    
    # Test rate limiter
    limiter = get_rate_limiter()
    for i in range(15):
        ok = limiter.acquire()
        print(f"Request {i+1}: {'OK' if ok else 'BLOCKED'}")
    
    # Test circuit breaker
    cb = get_circuit_breaker()
    for i in range(6):
        cb.record_trade(-1.0)  # Simulate loss
        print(f"Loss {i+1}: circuit_open={cb.is_open()}")
    
    # Test health
    health = get_health_monitor()
    print(f"\nHealth: {health.get_status()}")
    
    print("\n[PASS] All components tested")
