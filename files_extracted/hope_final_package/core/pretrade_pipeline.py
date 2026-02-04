# -*- coding: utf-8 -*-
"""
HOPE v4.0 FINAL — UNIFIED PRETRADE PIPELINE
============================================

Объединяет ВСЕ guards в один поток:
1. Signal Schema + TTL validation
2. Live Barrier (двойной барьер LIVE)
3. Liquidity Guard
4. Price Feed validation
5. Signal Gate (delta/symbol filters)
6. Circuit Breaker check
7. Rate Limiter check

Fail-closed: любое сомнение = SKIP

ИСПОЛЬЗОВАНИЕ:
    from core.pretrade_pipeline import pretrade_check
    
    ok, reason, data = pretrade_check(signal)
    if not ok:
        log.info(f"SKIP: {reason}")
        return
    # Proceed with trading...
"""

import os
import time
import json
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Tuple, List
from pathlib import Path
from collections import deque
from threading import Lock
from enum import Enum

log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PipelineConfig:
    """Конфигурация всего pipeline."""
    
    # Signal validation
    signal_ttl_sec: int = 30  # Max age of signal
    
    # Liquidity
    min_quote_volume_24h: float = 5_000_000  # $5M minimum
    
    # Price feed
    price_max_age_sec: int = 10  # Max age of price
    pricefeed_path: Path = field(default_factory=lambda: Path("state/ai/pricefeed.json"))
    
    # Signal Gate
    telegram_min_delta_pct: float = 10.0
    trade_min_delta_pct: float = 2.0
    blocked_types: List[str] = field(default_factory=lambda: ["MICRO", "TEST_ACTIVITY", "SCALP"])
    heavy_coins: List[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"])
    stablecoins: List[str] = field(default_factory=lambda: ["USDTUSDT", "USDCUSDT", "BUSDUSDT", "DAIUSDT", "FDUSDUSDT"])
    
    # Circuit Breaker
    max_consecutive_losses: int = 5
    max_daily_loss_pct: float = 5.0  # User specified 5%, not 10%
    cooldown_minutes: int = 30
    
    # Rate Limiter
    requests_per_second: int = 10
    requests_per_minute: int = 100
    
    # Live Barrier
    mode: str = field(default_factory=lambda: os.environ.get("HOPE_MODE", "DRY").upper())
    ack: str = field(default_factory=lambda: os.environ.get("HOPE_LIVE_ACK", "").upper())
    testnet: bool = field(default_factory=lambda: os.environ.get("BINANCE_TESTNET", "1") == "1")


# ══════════════════════════════════════════════════════════════════════════════
# 1. SIGNAL SCHEMA + TTL VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

class SignalValidationError(Exception):
    pass


def validate_signal_schema(signal: Dict[str, Any], config: PipelineConfig) -> Tuple[bool, str]:
    """
    Validate signal has required fields and is not expired.
    Fail-closed: any doubt = False
    """
    now = time.time()
    
    # Required fields
    symbol = signal.get("symbol")
    if not isinstance(symbol, str) or not symbol.endswith("USDT"):
        return False, "INVALID_SYMBOL"
    
    delta_pct = signal.get("delta_pct")
    if not isinstance(delta_pct, (int, float)):
        return False, "INVALID_DELTA"
    
    signal_type = signal.get("type", "UNKNOWN")
    
    # TTL check
    ts = signal.get("ts_unix") or signal.get("timestamp")
    if ts:
        if isinstance(ts, str):
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                ts = dt.timestamp()
            except:
                ts = now  # Fallback
        
        age = now - float(ts)
        if age > config.signal_ttl_sec:
            return False, f"EXPIRED_TTL:{age:.1f}s>{config.signal_ttl_sec}s"
        if age < -5:
            return False, "FUTURE_TIMESTAMP"
    
    return True, "OK"


# ══════════════════════════════════════════════════════════════════════════════
# 2. LIVE BARRIER
# ══════════════════════════════════════════════════════════════════════════════

class ExecutionMode(Enum):
    DRY = "DRY"
    TESTNET = "TESTNET"
    LIVE = "LIVE"


class LiveBarrier:
    """
    Двойной барьер для LIVE режима.
    Без обоих флагов = DRY mode (никаких ордеров).
    """
    _instance = None
    
    def __new__(cls, config: Optional[PipelineConfig] = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, config: Optional[PipelineConfig] = None):
        if self._initialized:
            return
        
        self.config = config or PipelineConfig()
        self._validate()
        self._initialized = True
    
    def _validate(self):
        mode = self.config.mode
        ack = self.config.ack
        testnet = self.config.testnet
        
        if mode == "LIVE":
            if ack != "YES_I_UNDERSTAND":
                log.warning("⚠️ HOPE_MODE=LIVE but HOPE_LIVE_ACK not set! Falling back to DRY")
                self._effective_mode = ExecutionMode.DRY
            elif testnet:
                log.warning("⚠️ HOPE_MODE=LIVE but BINANCE_TESTNET=1! Using TESTNET")
                self._effective_mode = ExecutionMode.TESTNET
            else:
                self._effective_mode = ExecutionMode.LIVE
                log.warning("🔴 LIVE MODE ACTIVE - Real money at risk!")
        elif mode == "TESTNET":
            self._effective_mode = ExecutionMode.TESTNET
        else:
            self._effective_mode = ExecutionMode.DRY
    
    @property
    def effective_mode(self) -> ExecutionMode:
        return self._effective_mode
    
    @property
    def orders_enabled(self) -> bool:
        return self._effective_mode in (ExecutionMode.LIVE, ExecutionMode.TESTNET)
    
    def check(self) -> Tuple[bool, str]:
        """Check if trading is allowed."""
        return True, f"MODE:{self._effective_mode.value}"


# ══════════════════════════════════════════════════════════════════════════════
# 3. LIQUIDITY GUARD
# ══════════════════════════════════════════════════════════════════════════════

def check_liquidity(signal: Dict[str, Any], config: PipelineConfig) -> Tuple[bool, str]:
    """
    Check if symbol has sufficient liquidity.
    Fail-closed: unknown/low = False
    """
    vol = signal.get("quote_volume_24h") or signal.get("daily_volume") or signal.get("daily_volume_m")
    
    if vol is None:
        # Try to get from payload
        vol = signal.get("payload", {}).get("quote_volume_24h")
    
    if not isinstance(vol, (int, float)):
        return False, "LIQUIDITY_UNKNOWN"
    
    # Handle "daily_volume_m" which is in millions
    if "daily_volume_m" in signal:
        vol = float(vol) * 1_000_000
    
    if float(vol) < config.min_quote_volume_24h:
        return False, f"LIQUIDITY_LOW:{vol:.0f}<{config.min_quote_volume_24h:.0f}"
    
    return True, "OK"


# ══════════════════════════════════════════════════════════════════════════════
# 4. PRICE FEED VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PricePoint:
    symbol: str
    price: float
    ts_unix: float


def get_price_from_feed(symbol: str, config: PipelineConfig) -> Optional[PricePoint]:
    """
    Get price from price feed file.
    Fail-closed: missing/stale = None
    """
    path = config.pricefeed_path
    
    if not path.exists():
        return None
    
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except:
        return None
    
    prices = data.get("prices", {})
    rec = prices.get(symbol)
    
    if not isinstance(rec, dict):
        return None
    
    price = rec.get("price")
    ts = rec.get("ts_unix", time.time())
    
    if not isinstance(price, (int, float)) or price <= 0:
        return None
    
    age = time.time() - float(ts)
    if age > config.price_max_age_sec:
        return None
    
    return PricePoint(symbol=symbol, price=float(price), ts_unix=float(ts))


def check_price_feed(signal: Dict[str, Any], config: PipelineConfig) -> Tuple[bool, str, Optional[float]]:
    """
    Validate price from feed.
    Returns: (ok, reason, price)
    """
    symbol = signal.get("symbol", "")
    
    # First try signal's own price
    signal_price = signal.get("price")
    if isinstance(signal_price, (int, float)) and signal_price > 0:
        return True, "PRICE_FROM_SIGNAL", float(signal_price)
    
    # Try price feed
    pp = get_price_from_feed(symbol, config)
    if pp:
        return True, "PRICE_FROM_FEED", pp.price
    
    return False, "PRICE_MISSING_OR_STALE", None


# ══════════════════════════════════════════════════════════════════════════════
# 5. SIGNAL GATE (Delta/Symbol filters)
# ══════════════════════════════════════════════════════════════════════════════

class GateDecision(Enum):
    PASS_TELEGRAM_AND_TRADE = "pass_tg_trade"
    PASS_TRADE_ONLY = "pass_trade_only"
    PASS_LOG_ONLY = "pass_log_only"
    BLOCK = "block"


def check_signal_gate(signal: Dict[str, Any], config: PipelineConfig) -> Tuple[GateDecision, str]:
    """
    Signal Gate: filter by delta, type, symbol.
    """
    symbol = signal.get("symbol", "")
    delta = float(signal.get("delta_pct", 0))
    sig_type = signal.get("type", "UNKNOWN")
    
    # Block heavy coins
    if symbol in config.heavy_coins:
        return GateDecision.PASS_LOG_ONLY, "HEAVY_COIN"
    
    # Block stablecoins
    if symbol in config.stablecoins:
        return GateDecision.BLOCK, "STABLECOIN"
    
    # Block unwanted types
    if sig_type in config.blocked_types:
        return GateDecision.PASS_LOG_ONLY, "BLOCKED_TYPE"
    
    # Check delta thresholds
    if delta >= config.telegram_min_delta_pct:
        return GateDecision.PASS_TELEGRAM_AND_TRADE, "STRONG_SIGNAL"
    elif delta >= config.trade_min_delta_pct:
        return GateDecision.PASS_TRADE_ONLY, "TRADE_ONLY"
    else:
        return GateDecision.PASS_LOG_ONLY, "LOW_DELTA"


# ══════════════════════════════════════════════════════════════════════════════
# 6. CIRCUIT BREAKER
# ══════════════════════════════════════════════════════════════════════════════

class CircuitBreaker:
    """
    Автоматическое отключение торговли при серии убытков.
    Singleton pattern.
    """
    _instance = None
    
    def __new__(cls, config: Optional[PipelineConfig] = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, config: Optional[PipelineConfig] = None):
        if self._initialized:
            return
        
        self.config = config or PipelineConfig()
        self._consecutive_losses = 0
        self._daily_pnl_pct = 0.0
        self._tripped_at: Optional[float] = None
        self._trip_reason: Optional[str] = None
        self._initialized = True
    
    def record_trade(self, pnl_pct: float):
        """Записать результат сделки."""
        self._daily_pnl_pct += pnl_pct
        
        if pnl_pct < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0
        
        # Check trip conditions
        if self._consecutive_losses >= self.config.max_consecutive_losses:
            self._trip(f"CONSECUTIVE_LOSSES:{self._consecutive_losses}")
        elif self._daily_pnl_pct <= -self.config.max_daily_loss_pct:
            self._trip(f"DAILY_LOSS:{self._daily_pnl_pct:.1f}%")
    
    def _trip(self, reason: str):
        """Сработал circuit breaker."""
        self._tripped_at = time.time()
        self._trip_reason = reason
        log.critical(f"🛑 CIRCUIT BREAKER TRIPPED: {reason}")
    
    def is_open(self) -> bool:
        """Check if circuit breaker is tripped."""
        if self._tripped_at is None:
            return False
        
        # Auto-reset after cooldown
        if time.time() - self._tripped_at > self.config.cooldown_minutes * 60:
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
    
    def check(self) -> Tuple[bool, str]:
        """Check if trading is allowed."""
        if self.is_open():
            return False, f"CIRCUIT_OPEN:{self._trip_reason}"
        return True, "OK"


# ══════════════════════════════════════════════════════════════════════════════
# 7. RATE LIMITER
# ══════════════════════════════════════════════════════════════════════════════

class RateLimiter:
    """
    Защита от превышения лимитов Binance API.
    Singleton pattern.
    """
    _instance = None
    
    def __new__(cls, config: Optional[PipelineConfig] = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, config: Optional[PipelineConfig] = None):
        if self._initialized:
            return
        
        self.config = config or PipelineConfig()
        self._second_window: deque = deque()
        self._minute_window: deque = deque()
        self._lock = Lock()
        self._initialized = True
    
    def check(self) -> Tuple[bool, str]:
        """Check if request is allowed."""
        now = time.time()
        
        with self._lock:
            # Clean old entries
            while self._second_window and self._second_window[0] < now - 1:
                self._second_window.popleft()
            while self._minute_window and self._minute_window[0] < now - 60:
                self._minute_window.popleft()
            
            # Check limits
            if len(self._second_window) >= self.config.requests_per_second:
                return False, "RATE_LIMIT_SEC"
            if len(self._minute_window) >= self.config.requests_per_minute:
                return False, "RATE_LIMIT_MIN"
            
            return True, "OK"
    
    def acquire(self) -> bool:
        """Try to acquire a slot."""
        ok, _ = self.check()
        if ok:
            now = time.time()
            with self._lock:
                self._second_window.append(now)
                self._minute_window.append(now)
        return ok


# ══════════════════════════════════════════════════════════════════════════════
# HEALTH MONITOR
# ══════════════════════════════════════════════════════════════════════════════

class HealthMonitor:
    """Мониторинг здоровья системы."""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._last_heartbeat = time.time()
        self._signals_processed = 0
        self._trades_executed = 0
        self._errors = 0
        self._skips_by_reason: Dict[str, int] = {}
        self._initialized = True
    
    def heartbeat(self):
        self._last_heartbeat = time.time()
    
    def record_signal(self):
        self._signals_processed += 1
        self.heartbeat()
    
    def record_trade(self):
        self._trades_executed += 1
        self.heartbeat()
    
    def record_skip(self, reason: str):
        self._skips_by_reason[reason] = self._skips_by_reason.get(reason, 0) + 1
    
    def record_error(self):
        self._errors += 1
    
    def get_status(self) -> Dict[str, Any]:
        return {
            "healthy": time.time() - self._last_heartbeat < 60,
            "last_heartbeat": self._last_heartbeat,
            "signals_processed": self._signals_processed,
            "trades_executed": self._trades_executed,
            "errors": self._errors,
            "skips_by_reason": dict(self._skips_by_reason),
        }


# ══════════════════════════════════════════════════════════════════════════════
# UNIFIED PRETRADE CHECK
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PretradeResult:
    ok: bool
    reason: str
    decision: GateDecision
    price: Optional[float] = None
    signal: Dict[str, Any] = field(default_factory=dict)
    
    def __bool__(self):
        return self.ok


def pretrade_check(
    signal: Dict[str, Any],
    config: Optional[PipelineConfig] = None
) -> PretradeResult:
    """
    UNIFIED PRETRADE PIPELINE
    
    Checks in order:
    1. Signal schema + TTL
    2. Live barrier
    3. Liquidity
    4. Price feed
    5. Signal gate
    6. Circuit breaker
    7. Rate limiter
    
    Fail-closed: any failure = PretradeResult(ok=False)
    """
    config = config or PipelineConfig()
    health = HealthMonitor()
    health.record_signal()
    
    # 1. Signal schema + TTL
    ok, reason = validate_signal_schema(signal, config)
    if not ok:
        health.record_skip(reason)
        return PretradeResult(ok=False, reason=reason, decision=GateDecision.BLOCK, signal=signal)
    
    # 2. Live barrier
    barrier = LiveBarrier(config)
    ok, reason = barrier.check()
    # Barrier always passes, but we record mode
    
    # 3. Liquidity
    ok, reason = check_liquidity(signal, config)
    if not ok:
        health.record_skip(reason)
        return PretradeResult(ok=False, reason=reason, decision=GateDecision.BLOCK, signal=signal)
    
    # 4. Price feed
    ok, price_reason, price = check_price_feed(signal, config)
    if not ok:
        health.record_skip(price_reason)
        return PretradeResult(ok=False, reason=price_reason, decision=GateDecision.BLOCK, signal=signal)
    
    # 5. Signal gate
    decision, gate_reason = check_signal_gate(signal, config)
    if decision == GateDecision.BLOCK:
        health.record_skip(gate_reason)
        return PretradeResult(ok=False, reason=gate_reason, decision=decision, signal=signal)
    if decision == GateDecision.PASS_LOG_ONLY:
        health.record_skip(gate_reason)
        return PretradeResult(ok=False, reason=f"LOG_ONLY:{gate_reason}", decision=decision, price=price, signal=signal)
    
    # 6. Circuit breaker
    cb = CircuitBreaker(config)
    ok, cb_reason = cb.check()
    if not ok:
        health.record_skip(cb_reason)
        return PretradeResult(ok=False, reason=cb_reason, decision=GateDecision.BLOCK, signal=signal)
    
    # 7. Rate limiter
    rl = RateLimiter(config)
    ok, rl_reason = rl.check()
    if not ok:
        health.record_skip(rl_reason)
        return PretradeResult(ok=False, reason=rl_reason, decision=GateDecision.BLOCK, signal=signal)
    
    # ALL CHECKS PASSED
    return PretradeResult(
        ok=True,
        reason=f"PASS:{gate_reason}",
        decision=decision,
        price=price,
        signal=signal
    )


# ══════════════════════════════════════════════════════════════════════════════
# TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    
    print("=" * 70)
    print("PRETRADE PIPELINE TEST")
    print("=" * 70)
    
    config = PipelineConfig()
    print(f"\nConfig:")
    print(f"  Max consecutive losses: {config.max_consecutive_losses}")
    print(f"  Max daily loss: {config.max_daily_loss_pct}%")
    print(f"  Min liquidity: ${config.min_quote_volume_24h:,.0f}")
    print(f"  Telegram delta: >= {config.telegram_min_delta_pct}%")
    
    # Test signals
    signals = [
        {"symbol": "PEPEUSDT", "delta_pct": 15.0, "type": "EXPLOSION", "daily_volume_m": 50, "price": 0.00001},
        {"symbol": "BTCUSDT", "delta_pct": 15.0, "type": "PUMP", "daily_volume_m": 1000, "price": 84000},
        {"symbol": "ADAUSDT", "delta_pct": 0.5, "type": "MICRO", "daily_volume_m": 100, "price": 0.32},
        {"symbol": "XYZUSDT", "delta_pct": 12.0, "type": "PUMP", "daily_volume_m": 0.1, "price": 1.0},  # Low liq
        {"symbol": "ENSOUSDT", "delta_pct": 28.0, "type": "MOONSHOT", "daily_volume_m": 30, "price": 0.5},
    ]
    
    print("\nResults:")
    for sig in signals:
        result = pretrade_check(sig, config)
        status = "✅" if result.ok else "❌"
        print(f"  {status} {sig['symbol']:12} delta={sig['delta_pct']:5.1f}% -> {result.reason}")
    
    # Test circuit breaker
    print("\n--- Circuit Breaker Test ---")
    cb = CircuitBreaker(config)
    for i in range(6):
        cb.record_trade(-1.0)
        print(f"  Loss {i+1}: open={cb.is_open()}")
    
    # Check after trip
    result = pretrade_check(signals[0], config)
    print(f"  After trip: {result.reason}")
    
    print("\n[PASS] Pipeline test complete")
