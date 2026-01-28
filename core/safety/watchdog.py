# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T11:05:00Z
# Purpose: Three automatic safety protections for HOPE trading
# Security: Fail-closed, all protections create STOP.flag on trigger
# === END SIGNATURE ===
"""
HOPE Safety Watchdog Module.

Three automatic protections:
1. WatchdogService - monitors health_v5.json freshness (stale = STOP)
2. CircuitBreaker - monitors daily PnL (loss > threshold = STOP)
3. OrderRateLimiter - limits order frequency (too many = STOP)

All protections are FAIL-CLOSED: any trigger creates STOP.flag.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("safety")

# Paths
ROOT = Path(__file__).resolve().parent.parent.parent
STATE_DIR = ROOT / "state"
HEALTH_FILE = STATE_DIR / "health_v5.json"
STOP_FLAG = ROOT / "STOP.flag"


def _atomic_write(path: Path, content: str) -> None:
    """Atomic write: temp -> fsync -> replace."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _create_stop_flag(reason: str) -> None:
    """Create STOP.flag with reason."""
    content = f"{reason}\nCreated: {datetime.now(timezone.utc).isoformat()}\n"
    _atomic_write(STOP_FLAG, content)
    logger.critical("STOP.flag created: %s", reason)


def _read_health() -> Optional[dict]:
    """Read health_v5.json, return None on any error."""
    try:
        if not HEALTH_FILE.exists():
            return None
        return json.loads(HEALTH_FILE.read_text("utf-8"))
    except Exception as e:
        logger.warning("Failed to read health: %s", e)
        return None


# =============================================================================
# 1. WATCHDOG SERVICE - monitors health_v5.json freshness
# =============================================================================

@dataclass
class WatchdogConfig:
    """Watchdog configuration."""
    max_age_sec: int = 120  # Health older than this = stale
    check_interval_sec: float = 10.0  # How often to check
    alert_callback: Optional[Callable[[str], None]] = None  # TG alert


class WatchdogService:
    """
    Monitors health_v5.json freshness.

    If heartbeat is older than max_age_sec:
    1. Creates STOP.flag
    2. Calls alert_callback (TG notification)
    3. Logs critical error
    """

    def __init__(self, config: Optional[WatchdogConfig] = None):
        self.config = config or WatchdogConfig()
        self._running = False
        self._last_alert_time = 0.0

    async def run(self) -> None:
        """Main watchdog loop (run as asyncio task)."""
        self._running = True
        logger.info("Watchdog started: max_age=%ds, interval=%.1fs",
                   self.config.max_age_sec, self.config.check_interval_sec)

        while self._running:
            try:
                self._check_health()
            except Exception as e:
                logger.error("Watchdog check error: %s", e)

            await asyncio.sleep(self.config.check_interval_sec)

    def stop(self) -> None:
        """Stop the watchdog."""
        self._running = False

    def _check_health(self) -> None:
        """Check health_v5.json freshness."""
        health = _read_health()

        if health is None:
            self._trigger_alert("WATCHDOG: health_v5.json missing or unreadable")
            return

        hb_ts = health.get("hb_ts")
        if not hb_ts:
            self._trigger_alert("WATCHDOG: health_v5.json missing hb_ts field")
            return

        # Parse timestamp
        try:
            if hb_ts.endswith("Z"):
                hb_dt = datetime.fromisoformat(hb_ts.replace("Z", "+00:00"))
            else:
                hb_dt = datetime.fromisoformat(hb_ts)
            hb_dt = hb_dt.astimezone(timezone.utc)
        except Exception as e:
            self._trigger_alert(f"WATCHDOG: invalid hb_ts format: {e}")
            return

        # Check age
        age_sec = (datetime.now(timezone.utc) - hb_dt).total_seconds()

        if age_sec > self.config.max_age_sec:
            self._trigger_alert(
                f"WATCHDOG: heartbeat stale (age={int(age_sec)}s > max={self.config.max_age_sec}s)"
            )
        elif age_sec < 0:
            self._trigger_alert(f"WATCHDOG: heartbeat in future (age={int(age_sec)}s)")

    def _trigger_alert(self, reason: str) -> None:
        """Trigger STOP.flag and alert."""
        now = time.time()

        # Rate limit alerts (max 1 per 60 sec)
        if now - self._last_alert_time < 60:
            return

        self._last_alert_time = now
        _create_stop_flag(reason)

        if self.config.alert_callback:
            try:
                self.config.alert_callback(f"🚨 {reason}")
            except Exception as e:
                logger.error("Alert callback failed: %s", e)


# =============================================================================
# 2. CIRCUIT BREAKER - monitors daily PnL
# =============================================================================

@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration."""
    max_daily_loss_usd: float = 50.0  # Max loss before trip
    check_interval_sec: float = 30.0  # How often to check
    alert_callback: Optional[Callable[[str], None]] = None


class CircuitBreaker:
    """
    Monitors daily PnL from health_v5.json.

    If daily_pnl_usd < -max_daily_loss_usd:
    1. Creates STOP.flag
    2. Sets daily_stop_hit=true in health
    3. Alerts via callback
    """

    def __init__(self, config: Optional[CircuitBreakerConfig] = None):
        self.config = config or CircuitBreakerConfig()
        self._running = False
        self._tripped = False

    async def run(self) -> None:
        """Main circuit breaker loop."""
        self._running = True
        logger.info("CircuitBreaker started: max_loss=$%.2f",
                   self.config.max_daily_loss_usd)

        while self._running:
            try:
                self._check_pnl()
            except Exception as e:
                logger.error("CircuitBreaker check error: %s", e)

            await asyncio.sleep(self.config.check_interval_sec)

    def stop(self) -> None:
        """Stop the circuit breaker."""
        self._running = False

    def _check_pnl(self) -> None:
        """Check daily PnL from health."""
        if self._tripped:
            return  # Already tripped, don't spam

        health = _read_health()
        if health is None:
            return

        daily_pnl = health.get("daily_pnl_usd", 0.0)

        if daily_pnl < -self.config.max_daily_loss_usd:
            self._trip(daily_pnl)

    def _trip(self, pnl: float) -> None:
        """Trip the circuit breaker."""
        self._tripped = True
        reason = f"CIRCUIT BREAKER: daily loss ${abs(pnl):.2f} > max ${self.config.max_daily_loss_usd:.2f}"

        _create_stop_flag(reason)
        logger.critical(reason)

        if self.config.alert_callback:
            try:
                self.config.alert_callback(f"🔴 {reason}")
            except Exception as e:
                logger.error("Alert callback failed: %s", e)

    def reset(self) -> None:
        """Reset the circuit breaker (new trading day)."""
        self._tripped = False
        logger.info("CircuitBreaker reset")


# =============================================================================
# 3. ORDER RATE LIMITER - limits order frequency
# =============================================================================

@dataclass
class RateLimiterConfig:
    """Rate limiter configuration."""
    max_orders_per_minute: int = 5
    max_orders_per_hour: int = 30


class OrderRateLimiter:
    """
    Limits order execution frequency.

    Call allow() before each order:
    - Returns True if order is allowed
    - Returns False and creates STOP.flag if rate exceeded
    """

    def __init__(self, config: Optional[RateLimiterConfig] = None):
        self.config = config or RateLimiterConfig()
        self._minute_window: deque = deque()  # timestamps
        self._hour_window: deque = deque()

    def allow(self) -> bool:
        """
        Check if order is allowed.

        Returns:
            True if order allowed, False if rate limit exceeded
        """
        now = time.time()

        # Clean old entries
        minute_ago = now - 60
        hour_ago = now - 3600

        while self._minute_window and self._minute_window[0] < minute_ago:
            self._minute_window.popleft()
        while self._hour_window and self._hour_window[0] < hour_ago:
            self._hour_window.popleft()

        # Check limits
        if len(self._minute_window) >= self.config.max_orders_per_minute:
            reason = f"RATE LIMIT: {len(self._minute_window)} orders/min >= max {self.config.max_orders_per_minute}"
            _create_stop_flag(reason)
            logger.critical(reason)
            return False

        if len(self._hour_window) >= self.config.max_orders_per_hour:
            reason = f"RATE LIMIT: {len(self._hour_window)} orders/hour >= max {self.config.max_orders_per_hour}"
            _create_stop_flag(reason)
            logger.critical(reason)
            return False

        # Record this order
        self._minute_window.append(now)
        self._hour_window.append(now)

        return True

    def get_stats(self) -> dict:
        """Get current rate limiter stats."""
        return {
            "orders_last_minute": len(self._minute_window),
            "orders_last_hour": len(self._hour_window),
            "max_per_minute": self.config.max_orders_per_minute,
            "max_per_hour": self.config.max_orders_per_hour,
        }
