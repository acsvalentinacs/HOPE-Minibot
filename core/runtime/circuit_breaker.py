# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T23:55:00Z
# Purpose: Circuit breaker for cascade failure protection
# Security: Fail-closed, atomic state, automatic recovery
# === END SIGNATURE ===
"""
Circuit Breaker for Cascade Failure Protection.

Prevents system from repeatedly attempting operations that are likely to fail.
After N consecutive failures, circuit "opens" and rejects all attempts
until recovery timeout expires or manual reset.

States:
- CLOSED: Normal operation, requests pass through
- OPEN: Failure threshold reached, all requests rejected
- HALF_OPEN: Testing if system recovered (after timeout)

Fail-closed: When circuit is OPEN, ALL operations are rejected.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, Callable, TypeVar, Any, Dict

logger = logging.getLogger("circuit_breaker")

T = TypeVar("T")


class CircuitState(str, Enum):
    """Circuit breaker states."""
    CLOSED = "CLOSED"      # Normal operation
    OPEN = "OPEN"          # Rejecting all requests
    HALF_OPEN = "HALF_OPEN"  # Testing recovery


@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration."""
    failure_threshold: int = 3       # Failures before opening
    success_threshold: int = 2       # Successes to close from half-open
    recovery_timeout_sec: float = 60.0  # Time before half-open attempt
    half_open_max_calls: int = 3     # Max calls in half-open state


@dataclass
class CircuitStats:
    """Circuit breaker statistics."""
    state: CircuitState
    consecutive_failures: int
    consecutive_successes: int
    total_failures: int
    total_successes: int
    total_rejections: int
    last_failure_time: Optional[float]
    last_success_time: Optional[float]
    state_changed_at: float
    opened_count: int  # Times circuit has opened

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d


class CircuitBreakerOpen(Exception):
    """Raised when circuit is open and rejecting requests."""

    def __init__(self, name: str, time_until_retry: float):
        self.name = name
        self.time_until_retry = time_until_retry
        super().__init__(
            f"Circuit '{name}' is OPEN. Retry in {time_until_retry:.1f}s"
        )


class CircuitBreaker:
    """
    Circuit breaker for protecting against cascade failures.

    Usage:
        breaker = CircuitBreaker("binance_api")

        @breaker.protect
        def call_binance():
            return requests.get("...")

        # Or manual:
        try:
            breaker.check()
            result = call_binance()
            breaker.record_success()
        except Exception:
            breaker.record_failure()
            raise
    """

    def __init__(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None,
        state_path: Optional[Path] = None,
    ):
        """
        Initialize circuit breaker.

        Args:
            name: Unique name for this circuit
            config: Circuit configuration
            state_path: Path for state persistence
        """
        self.name = name
        self.config = config or CircuitBreakerConfig()

        if state_path is None:
            state_dir = Path(__file__).resolve().parent.parent.parent / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            state_path = state_dir / f"circuit_{name}.json"
        self.state_path = state_path

        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._total_failures = 0
        self._total_successes = 0
        self._total_rejections = 0
        self._last_failure_time: Optional[float] = None
        self._last_success_time: Optional[float] = None
        self._state_changed_at = time.time()
        self._opened_count = 0
        self._half_open_calls = 0

        self._load_state()

    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        with self._lock:
            self._check_recovery()
            return self._state

    @property
    def is_open(self) -> bool:
        """Check if circuit is open (rejecting requests)."""
        return self.state == CircuitState.OPEN

    @property
    def is_closed(self) -> bool:
        """Check if circuit is closed (normal operation)."""
        return self.state == CircuitState.CLOSED

    def check(self) -> None:
        """
        Check if request is allowed.

        Raises:
            CircuitBreakerOpen: If circuit is open
        """
        with self._lock:
            self._check_recovery()

            if self._state == CircuitState.OPEN:
                time_since_open = time.time() - self._state_changed_at
                time_until_retry = self.config.recovery_timeout_sec - time_since_open
                self._total_rejections += 1
                raise CircuitBreakerOpen(self.name, max(0, time_until_retry))

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.config.half_open_max_calls:
                    # Too many half-open calls, reject
                    self._total_rejections += 1
                    raise CircuitBreakerOpen(self.name, 1.0)
                self._half_open_calls += 1

    def record_success(self) -> None:
        """Record a successful operation."""
        with self._lock:
            now = time.time()
            self._consecutive_failures = 0
            self._consecutive_successes += 1
            self._total_successes += 1
            self._last_success_time = now

            if self._state == CircuitState.HALF_OPEN:
                if self._consecutive_successes >= self.config.success_threshold:
                    self._transition_to(CircuitState.CLOSED)
                    logger.info("Circuit '%s' CLOSED after recovery", self.name)

            self._persist_state()

    def record_failure(self, error: Optional[str] = None) -> None:
        """
        Record a failed operation.

        Args:
            error: Optional error description for logging
        """
        with self._lock:
            now = time.time()
            self._consecutive_successes = 0
            self._consecutive_failures += 1
            self._total_failures += 1
            self._last_failure_time = now

            if error:
                logger.warning("Circuit '%s' failure: %s", self.name, error)

            if self._state == CircuitState.HALF_OPEN:
                # Single failure in half-open reopens circuit
                self._transition_to(CircuitState.OPEN)
                logger.warning("Circuit '%s' REOPENED during recovery", self.name)

            elif self._state == CircuitState.CLOSED:
                if self._consecutive_failures >= self.config.failure_threshold:
                    self._transition_to(CircuitState.OPEN)
                    self._opened_count += 1
                    logger.error(
                        "Circuit '%s' OPENED after %d consecutive failures",
                        self.name, self._consecutive_failures
                    )

            self._persist_state()

    def reset(self) -> None:
        """Manually reset circuit to closed state."""
        with self._lock:
            self._transition_to(CircuitState.CLOSED)
            self._consecutive_failures = 0
            self._consecutive_successes = 0
            logger.info("Circuit '%s' manually RESET", self.name)
            self._persist_state()

    def force_open(self, reason: str = "manual") -> None:
        """Manually open circuit."""
        with self._lock:
            self._transition_to(CircuitState.OPEN)
            self._opened_count += 1
            logger.warning("Circuit '%s' manually OPENED: %s", self.name, reason)
            self._persist_state()

    def get_stats(self) -> CircuitStats:
        """Get circuit statistics."""
        with self._lock:
            self._check_recovery()
            return CircuitStats(
                state=self._state,
                consecutive_failures=self._consecutive_failures,
                consecutive_successes=self._consecutive_successes,
                total_failures=self._total_failures,
                total_successes=self._total_successes,
                total_rejections=self._total_rejections,
                last_failure_time=self._last_failure_time,
                last_success_time=self._last_success_time,
                state_changed_at=self._state_changed_at,
                opened_count=self._opened_count,
            )

    def protect(self, func: Callable[..., T]) -> Callable[..., T]:
        """
        Decorator to protect a function with circuit breaker.

        Usage:
            @breaker.protect
            def call_api():
                return requests.get(...)
        """
        def wrapper(*args, **kwargs) -> T:
            self.check()
            try:
                result = func(*args, **kwargs)
                self.record_success()
                return result
            except Exception as e:
                self.record_failure(str(e))
                raise

        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper

    def _check_recovery(self) -> None:
        """Check if circuit should transition to half-open."""
        if self._state == CircuitState.OPEN:
            time_since_open = time.time() - self._state_changed_at
            if time_since_open >= self.config.recovery_timeout_sec:
                self._transition_to(CircuitState.HALF_OPEN)
                logger.info("Circuit '%s' entering HALF_OPEN state", self.name)

    def _transition_to(self, new_state: CircuitState) -> None:
        """Transition to new state."""
        self._state = new_state
        self._state_changed_at = time.time()
        if new_state == CircuitState.HALF_OPEN:
            self._half_open_calls = 0

    def _persist_state(self) -> None:
        """Atomically persist state to disk."""
        try:
            state = {
                "name": self.name,
                "state": self._state.value,
                "consecutive_failures": self._consecutive_failures,
                "consecutive_successes": self._consecutive_successes,
                "total_failures": self._total_failures,
                "total_successes": self._total_successes,
                "total_rejections": self._total_rejections,
                "last_failure_time": self._last_failure_time,
                "last_success_time": self._last_success_time,
                "state_changed_at": self._state_changed_at,
                "opened_count": self._opened_count,
                "updated_at": time.time(),
            }

            content = json.dumps(state, indent=2)
            tmp_path = self.state_path.with_suffix(".json.tmp")

            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_path, self.state_path)

        except Exception as e:
            logger.warning("Failed to persist circuit state: %s", e)

    def _load_state(self) -> None:
        """Load state from disk."""
        if not self.state_path.exists():
            return

        try:
            content = self.state_path.read_text(encoding="utf-8")
            state = json.loads(content)

            self._state = CircuitState(state.get("state", "CLOSED"))
            self._consecutive_failures = state.get("consecutive_failures", 0)
            self._consecutive_successes = state.get("consecutive_successes", 0)
            self._total_failures = state.get("total_failures", 0)
            self._total_successes = state.get("total_successes", 0)
            self._total_rejections = state.get("total_rejections", 0)
            self._last_failure_time = state.get("last_failure_time")
            self._last_success_time = state.get("last_success_time")
            self._state_changed_at = state.get("state_changed_at", time.time())
            self._opened_count = state.get("opened_count", 0)

            logger.debug("Loaded circuit state: %s = %s", self.name, self._state)

        except Exception as e:
            logger.warning("Failed to load circuit state, resetting: %s", e)


# === Circuit Breaker Registry ===

_circuits: Dict[str, CircuitBreaker] = {}
_registry_lock = threading.Lock()


def get_circuit(
    name: str,
    config: Optional[CircuitBreakerConfig] = None,
) -> CircuitBreaker:
    """
    Get or create a circuit breaker by name.

    Args:
        name: Circuit name (e.g., "binance_api", "news_spider")
        config: Circuit configuration (only used on first call)

    Returns:
        CircuitBreaker instance
    """
    with _registry_lock:
        if name not in _circuits:
            _circuits[name] = CircuitBreaker(name, config)
        return _circuits[name]


def get_all_circuits() -> Dict[str, CircuitStats]:
    """Get stats for all circuits."""
    with _registry_lock:
        return {name: circuit.get_stats() for name, circuit in _circuits.items()}


def reset_all_circuits() -> None:
    """Reset all circuits to closed state."""
    with _registry_lock:
        for circuit in _circuits.values():
            circuit.reset()


# === Common Circuit Breakers (Pre-configured) ===

def get_binance_circuit() -> CircuitBreaker:
    """Get circuit breaker for Binance API."""
    return get_circuit("binance_api", CircuitBreakerConfig(
        failure_threshold=3,
        success_threshold=2,
        recovery_timeout_sec=60.0,
    ))


def get_news_circuit() -> CircuitBreaker:
    """Get circuit breaker for news/RSS fetching."""
    return get_circuit("news_spider", CircuitBreakerConfig(
        failure_threshold=5,
        success_threshold=3,
        recovery_timeout_sec=300.0,  # 5 minutes
    ))


def get_trade_circuit() -> CircuitBreaker:
    """Get circuit breaker for trade execution."""
    return get_circuit("trade_execution", CircuitBreakerConfig(
        failure_threshold=2,  # More sensitive
        success_threshold=3,
        recovery_timeout_sec=120.0,
    ))
