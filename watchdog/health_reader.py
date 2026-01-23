# -*- coding: utf-8 -*-
"""
P5: Health Reader — Read heartbeat timestamps from health files

Invariants:
- Fixed paths only (no discovery)
- Fail-closed: missing/corrupt file = stale
- Returns None for missing data, never guesses
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class HeartbeatStatus:
    """Heartbeat check result."""
    role: str
    alive: bool
    last_ts: Optional[float]
    age_sec: float
    stale: bool
    reason: str


def read_health_json(path: Path) -> Optional[Dict[str, Any]]:
    """
    Read health JSON file.

    Returns None on any error (fail-closed).
    """
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text)
    except Exception:
        return None


def check_heartbeat(
    role: str,
    health_path: Path,
    heartbeat_field: str,
    ttl_sec: float,
) -> HeartbeatStatus:
    """
    Check if role's heartbeat is fresh.

    Returns HeartbeatStatus with stale=True if:
    - Health file missing
    - Heartbeat field missing
    - Heartbeat older than TTL
    """
    now = time.time()

    health = read_health_json(health_path)
    if health is None:
        return HeartbeatStatus(
            role=role,
            alive=False,
            last_ts=None,
            age_sec=float("inf"),
            stale=True,
            reason="health_file_missing",
        )

    last_ts = health.get(heartbeat_field)
    if last_ts is None:
        # Try fallback to generic last_heartbeat_ts
        last_ts = health.get("last_heartbeat_ts")

    if not isinstance(last_ts, (int, float)):
        return HeartbeatStatus(
            role=role,
            alive=False,
            last_ts=None,
            age_sec=float("inf"),
            stale=True,
            reason=f"heartbeat_field_missing: {heartbeat_field}",
        )

    age_sec = now - float(last_ts)
    stale = age_sec > ttl_sec

    return HeartbeatStatus(
        role=role,
        alive=not stale,
        last_ts=float(last_ts),
        age_sec=age_sec,
        stale=stale,
        reason="ok" if not stale else f"stale ({age_sec:.1f}s > {ttl_sec}s)",
    )


def read_pid_file(pid_path: Path) -> Optional[int]:
    """
    Read PID from file.

    Returns None on any error (fail-closed).
    """
    if not pid_path.exists():
        return None
    try:
        text = pid_path.read_text(encoding="utf-8").strip()
        # Extract first integer (tolerant parsing like launcher)
        import re
        m = re.search(r"(\d{1,8})", text)
        if m:
            return int(m.group(1))
        return None
    except Exception:
        return None
