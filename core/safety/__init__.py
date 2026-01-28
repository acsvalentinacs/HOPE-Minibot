# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T11:05:00Z
# Purpose: Safety module - Watchdog, CircuitBreaker, RateLimiter
# === END SIGNATURE ===
"""
HOPE Safety Module.

Provides three automatic protections:
- WatchdogService: Monitors health_v5.json freshness
- CircuitBreaker: Monitors daily PnL limit
- OrderRateLimiter: Limits order frequency
"""

from .watchdog import (
    WatchdogService,
    CircuitBreaker,
    OrderRateLimiter,
    STOP_FLAG,
)

__all__ = [
    "WatchdogService",
    "CircuitBreaker",
    "OrderRateLimiter",
    "STOP_FLAG",
]
