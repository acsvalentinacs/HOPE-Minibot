# -*- coding: utf-8 -*-
"""
P5: Roles Registry — Single Source of Truth for Role Definitions

Invariants:
- Every role has explicit command line (no guessing)
- Every role has explicit heartbeat path
- Every role has TTL and restart limits
- Fail-closed: unknown role = error, not fallback
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(frozen=True)
class RoleConfig:
    """Immutable role configuration."""
    name: str
    script: str  # Relative to root, e.g., "minibot/run_live_v5.py"
    args: List[str]  # Additional args, e.g., ["--mode", "DRY"]
    needle: str  # Process identification pattern in cmdline
    heartbeat_field: str  # Field in health_v5.json, e.g., "last_heartbeat_ts"
    ttl_sec: float = 120.0  # Heartbeat timeout
    grace_sec: float = 30.0  # Grace period after restart
    max_restarts_per_hour: int = 5
    backoff_base_sec: float = 10.0
    backoff_max_sec: float = 300.0

    def get_command(self, python_exe: Path, root: Path, mode: str = "DRY") -> List[str]:
        """Build full command line for subprocess."""
        script_path = root / self.script
        cmd = [str(python_exe), str(script_path)]
        for arg in self.args:
            if arg == "{MODE}":
                cmd.append(mode)
            else:
                cmd.append(arg)
        return cmd


# Registry: explicit, no discovery
ROLES: Dict[str, RoleConfig] = {
    "ENGINE": RoleConfig(
        name="ENGINE",
        script="minibot/run_live_v5.py",
        args=["--mode", "{MODE}"],
        needle="run_live_v5.py",
        heartbeat_field="last_heartbeat_ts",
        ttl_sec=120.0,
        grace_sec=30.0,
        max_restarts_per_hour=5,
    ),
    "LISTENER": RoleConfig(
        name="LISTENER",
        script="tools/hunters_listener_v1.py",
        args=["--mode", "{MODE}"],
        needle="hunters_listener_v1.py",
        heartbeat_field="listener_heartbeat_ts",  # Will add to health
        ttl_sec=180.0,
        grace_sec=30.0,
        max_restarts_per_hour=5,
    ),
    "TGBOT": RoleConfig(
        name="TGBOT",
        script="minibot/tg_bot_simple.py",
        args=[],
        needle="tg_bot_simple.py",
        heartbeat_field="tgbot_heartbeat_ts",  # Will add to health
        ttl_sec=300.0,  # Telegram bot can be slower
        grace_sec=60.0,
        max_restarts_per_hour=3,
    ),
}


def get_role(name: str) -> RoleConfig:
    """
    Get role config by name.

    Fail-closed: raises KeyError for unknown roles.
    """
    key = name.strip().upper()
    if key not in ROLES:
        raise KeyError(f"Unknown role: {name!r}. Valid roles: {list(ROLES.keys())}")
    return ROLES[key]


def get_all_roles() -> List[RoleConfig]:
    """Get all registered roles."""
    return list(ROLES.values())
