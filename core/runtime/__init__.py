# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T23:45:00Z
# Modified at: 2026-01-27T23:55:00Z
# Purpose: Runtime module exports
# === END SIGNATURE ===
"""
HOPE Runtime Module.

Provides core runtime infrastructure:
- lockfile: Process locking with cmdline SHA256 binding
- circuit_breaker: Fail-closed cascade protection

Usage:
    from core.runtime import RuntimeLockfile, acquire_runtime_lock
    from core.runtime import CircuitBreaker, get_circuit

    with RuntimeLockfile() as lock:
        # Protected code
        pass

    breaker = get_circuit("api_calls")

    @breaker.protect
    def call_api():
        return requests.get(...)
"""

from .lockfile import (
    RuntimeLockfile,
    LockfileData,
    LockAcquireResult,
    acquire_runtime_lock,
    check_runtime_lock,
    LOCKFILE_SCHEMA,
)

from .circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpen,
    CircuitState,
    CircuitStats,
    get_circuit,
    get_all_circuits,
    reset_all_circuits,
    get_binance_circuit,
    get_news_circuit,
    get_trade_circuit,
)

__all__ = [
    # Lockfile
    "RuntimeLockfile",
    "LockfileData",
    "LockAcquireResult",
    "acquire_runtime_lock",
    "check_runtime_lock",
    "LOCKFILE_SCHEMA",
    # Circuit Breaker
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitBreakerOpen",
    "CircuitState",
    "CircuitStats",
    "get_circuit",
    "get_all_circuits",
    "reset_all_circuits",
    "get_binance_circuit",
    "get_news_circuit",
    "get_trade_circuit",
]
