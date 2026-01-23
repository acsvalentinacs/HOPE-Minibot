# -*- coding: utf-8 -*-
"""
P5: Watchdog Supervisor v1

Single-role supervisor with fail-closed restart logic.

Invariants:
- Lock-truth verified before restart
- Backoff with jitter to prevent restart storms
- Circuit breaker after max_restarts_per_hour
- All state changes logged to JSONL audit trail
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from minibot.io_jsonl import append_jsonl
from minibot.lock_truth_v2 import atomic_write_json
from minibot.watchdog.roles_registry import RoleConfig, get_role
from minibot.watchdog.health_reader import check_heartbeat, read_pid_file
from minibot.watchdog.process_manager import (
    pid_is_alive,
    kill_pid,
    start_process,
    write_pid_file,
    remove_pid_file,
)

WATCHDOG_STATE_SCHEMA = "hope.watchdog.state.v1"
WATCHDOG_AUDIT_SCHEMA = "hope.watchdog.audit.v1"


@dataclass
class RestartRecord:
    """Single restart event for rate limiting."""
    ts: float
    reason: str


@dataclass
class RoleState:
    """Runtime state for a single role."""
    role: str
    pid: Optional[int] = None
    state: str = "UNKNOWN"  # UNKNOWN, RUNNING, DEAD, STALE, RESTARTING, CIRCUIT_OPEN
    last_check_ts: float = 0.0
    last_restart_ts: float = 0.0
    consecutive_failures: int = 0
    restart_history: List[RestartRecord] = field(default_factory=list)
    circuit_open_until: float = 0.0
    last_error: Optional[str] = None


class WatchdogSupervisor:
    """
    Watchdog supervisor for HOPE roles.

    Usage:
        supervisor = WatchdogSupervisor(root=Path("."), mode="DRY")
        supervisor.add_role("ENGINE")
        supervisor.add_role("LISTENER")

        # One check cycle
        supervisor.check_all()

        # Or continuous loop
        supervisor.run(check_interval_sec=30.0)
    """

    def __init__(
        self,
        root: Path,
        mode: str = "DRY",
        python_exe: Optional[Path] = None,
        health_path: Optional[Path] = None,
        pids_dir: Optional[Path] = None,
        state_path: Optional[Path] = None,
        audit_path: Optional[Path] = None,
    ):
        self.root = Path(root).resolve()
        self.mode = mode

        # Resolve Python executable
        if python_exe:
            self.python_exe = Path(python_exe)
        else:
            venv_py = self.root / ".venv" / "Scripts" / "python.exe"
            self.python_exe = venv_py if venv_py.exists() else Path("python")

        # Fixed paths (no discovery)
        self.health_path = health_path or (self.root / "state" / "health_v5.json")
        self.pids_dir = pids_dir or (self.root / "state" / "pids")
        self.state_path = state_path or (self.root / "state" / "watchdog" / "watchdog_state.json")
        self.audit_path = audit_path or (self.root / "state" / "watchdog" / "watchdog_events.jsonl")

        self.roles: Dict[str, RoleState] = {}
        self._running = False

    def add_role(self, role_name: str) -> None:
        """Add role to supervision."""
        config = get_role(role_name)  # Fail-closed: raises if unknown
        self.roles[config.name] = RoleState(role=config.name)

    def check_all(self) -> Dict[str, RoleState]:
        """
        Check all supervised roles.

        Returns dict of role states after check.
        """
        for role_name in list(self.roles.keys()):
            self._check_role(role_name)

        self._write_state()
        return dict(self.roles)

    def run(self, check_interval_sec: float = 30.0) -> None:
        """
        Run continuous supervision loop.

        Stops on KeyboardInterrupt or when self._running = False.
        """
        self._running = True
        self._audit("watchdog_start", {"roles": list(self.roles.keys())})

        try:
            while self._running:
                self.check_all()
                time.sleep(check_interval_sec)
        except KeyboardInterrupt:
            pass
        finally:
            self._audit("watchdog_stop", {})
            self._running = False

    def stop(self) -> None:
        """Stop supervision loop."""
        self._running = False

    def _check_role(self, role_name: str) -> None:
        """Check single role and take action if needed."""
        config = get_role(role_name)
        state = self.roles[role_name]
        now = time.time()

        state.last_check_ts = now

        # Check circuit breaker
        if now < state.circuit_open_until:
            state.state = "CIRCUIT_OPEN"
            return

        # Read PID from file
        pid_path = self.pids_dir / f"{role_name}.pid"
        pid = read_pid_file(pid_path)
        state.pid = pid

        # Check if process alive
        process_alive = pid is not None and pid_is_alive(pid)

        if not process_alive:
            state.state = "DEAD"
            self._handle_dead_role(role_name, config, state, "process_not_alive")
            return

        # Check heartbeat
        heartbeat = check_heartbeat(
            role=role_name,
            health_path=self.health_path,
            heartbeat_field=config.heartbeat_field,
            ttl_sec=config.ttl_sec,
        )

        if heartbeat.stale:
            state.state = "STALE"
            self._handle_dead_role(role_name, config, state, f"heartbeat_stale: {heartbeat.reason}")
            return

        # All good
        state.state = "RUNNING"
        state.consecutive_failures = 0
        state.last_error = None

    def _handle_dead_role(
        self,
        role_name: str,
        config: RoleConfig,
        state: RoleState,
        reason: str,
    ) -> None:
        """Handle dead or stale role — restart with backoff."""
        now = time.time()

        # Prune old restart history (keep last hour)
        hour_ago = now - 3600
        state.restart_history = [r for r in state.restart_history if r.ts > hour_ago]

        # Check rate limit
        if len(state.restart_history) >= config.max_restarts_per_hour:
            state.circuit_open_until = now + config.backoff_max_sec
            state.state = "CIRCUIT_OPEN"
            state.last_error = f"Max restarts exceeded ({config.max_restarts_per_hour}/hour)"
            self._audit("circuit_open", {
                "role": role_name,
                "reason": reason,
                "restarts_last_hour": len(state.restart_history),
            })
            return

        # Calculate backoff with jitter
        backoff = min(
            config.backoff_base_sec * (2 ** state.consecutive_failures),
            config.backoff_max_sec,
        )
        jitter = random.uniform(0, backoff * 0.2)
        total_backoff = backoff + jitter

        # Check if we're still in backoff period
        time_since_last_restart = now - state.last_restart_ts
        if time_since_last_restart < total_backoff:
            return  # Still in backoff

        # Kill existing process if any
        if state.pid and pid_is_alive(state.pid):
            self._audit("killing_stale", {"role": role_name, "pid": state.pid, "reason": reason})
            kill_pid(state.pid)

        # Restart
        state.state = "RESTARTING"
        self._audit("restart_attempt", {
            "role": role_name,
            "reason": reason,
            "consecutive_failures": state.consecutive_failures,
            "backoff_sec": total_backoff,
        })

        command = config.get_command(self.python_exe, self.root, self.mode)
        result = start_process(command, self.root)

        if result.success and result.pid:
            # Success
            pid_path = self.pids_dir / f"{role_name}.pid"
            write_pid_file(pid_path, result.pid)

            state.pid = result.pid
            state.state = "RUNNING"
            state.last_restart_ts = now
            state.consecutive_failures = 0
            state.restart_history.append(RestartRecord(ts=now, reason=reason))

            self._audit("restart_success", {
                "role": role_name,
                "pid": result.pid,
                "command": command,
            })
        else:
            # Failed to restart
            state.consecutive_failures += 1
            state.last_error = result.error
            state.last_restart_ts = now
            state.restart_history.append(RestartRecord(ts=now, reason=f"failed: {result.error}"))

            self._audit("restart_failed", {
                "role": role_name,
                "error": result.error,
                "consecutive_failures": state.consecutive_failures,
            })

    def _write_state(self) -> None:
        """Write watchdog state atomically."""
        now = time.time()

        obj = {
            "schema": WATCHDOG_STATE_SCHEMA,
            "ts": now,
            "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "watchdog_pid": os.getpid(),
            "mode": self.mode,
            "roles": {},
        }

        for role_name, state in self.roles.items():
            obj["roles"][role_name] = {
                "state": state.state,
                "pid": state.pid,
                "last_check_ts": state.last_check_ts,
                "last_restart_ts": state.last_restart_ts,
                "consecutive_failures": state.consecutive_failures,
                "restarts_last_hour": len(state.restart_history),
                "circuit_open_until": state.circuit_open_until if state.circuit_open_until > now else None,
                "last_error": state.last_error,
            }

        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.state_path, obj)

    def _audit(self, event_type: str, details: Dict[str, Any]) -> None:
        """Append event to watchdog audit trail."""
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)

        obj = {
            "event_type": event_type,
            "watchdog_pid": os.getpid(),
            "mode": self.mode,
            "details": details,
        }

        try:
            append_jsonl(self.audit_path, obj, schema=WATCHDOG_AUDIT_SCHEMA)
        except Exception:
            pass  # Audit failure shouldn't crash watchdog
