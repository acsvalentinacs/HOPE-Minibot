# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 12:00:00 UTC
# Purpose: Multi-process manager for HOPE system
# Contract: Start/Stop/Monitor all HOPE processes from registry
# === END SIGNATURE ===
"""
HOPE Process Manager — Unified management for all HOPE processes.

Features:
  1. Start/stop/restart individual or all processes
  2. Health monitoring with auto-restart
  3. Dependency-aware startup order
  4. State persistence
  5. Telegram alerts on failures

Usage:
    python -m scripts.hope_process_manager start           # Start all
    python -m scripts.hope_process_manager start dashboard # Start one
    python -m scripts.hope_process_manager stop            # Stop all
    python -m scripts.hope_process_manager status          # Show status
    python -m scripts.hope_process_manager restart eye_of_god
    python -m scripts.hope_process_manager daemon          # Run as daemon

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │                    HOPE PROCESS MANAGER                      │
    ├─────────────────────────────────────────────────────────────┤
    │                                                              │
    │  ┌─────────────────┐                                        │
    │  │ Process Registry│  ← Configuration SSoT                  │
    │  └────────┬────────┘                                        │
    │           │                                                  │
    │           ▼                                                  │
    │  ┌─────────────────┐     ┌─────────────────┐               │
    │  │  Process Pool   │────▶│  Health Monitor │               │
    │  │  (subprocess)   │     │  (async loop)   │               │
    │  └────────┬────────┘     └────────┬────────┘               │
    │           │                       │                         │
    │           ▼                       ▼                         │
    │  ┌─────────────────┐     ┌─────────────────┐               │
    │  │   State File    │     │ Telegram Alerts │               │
    │  │   (JSON)        │     │  (on failure)   │               │
    │  └─────────────────┘     └─────────────────┘               │
    │                                                              │
    └─────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.process_registry import (
    PROCESS_REGISTRY,
    PROCESS_GROUPS,
    STARTUP_PROFILES,
    ProcessConfig,
    ProcessStatus,
    RestartPolicy,
    get_startup_order,
    get_dependent_processes,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("PROCMGR")

# ═══════════════════════════════════════════════════════════════════════════════
# PATHS
# ═══════════════════════════════════════════════════════════════════════════════

ROOT = Path(__file__).parent.parent
STATE_DIR = ROOT / "state" / "processes"
LOGS_DIR = ROOT / "logs"
MANAGER_STATE_FILE = STATE_DIR / "manager_state.json"
STOP_FLAG_FILE = ROOT / "state" / "STOP.flag"

# Python executable
PYTHON_EXE = sys.executable


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ProcessState:
    """Runtime state for a managed process."""
    name: str
    pid: Optional[int] = None
    status: str = ProcessStatus.STOPPED.value
    started_at: Optional[str] = None
    restarts: int = 0
    consecutive_failures: int = 0
    last_health_check: Optional[str] = None
    health_status: str = "unknown"
    exit_code: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ProcessState":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ManagerState:
    """Overall manager state."""
    started_at: str = ""
    processes: Dict[str, ProcessState] = field(default_factory=dict)
    profile: str = "dev"

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "profile": self.profile,
            "processes": {k: v.to_dict() for k, v in self.processes.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ManagerState":
        state = cls(
            started_at=data.get("started_at", ""),
            profile=data.get("profile", "dev"),
        )
        for name, proc_data in data.get("processes", {}).items():
            state.processes[name] = ProcessState.from_dict(proc_data)
        return state


# ═══════════════════════════════════════════════════════════════════════════════
# PROCESS CONTROLLER
# ═══════════════════════════════════════════════════════════════════════════════

class ProcessController:
    """Manage individual process lifecycle."""

    def __init__(self, config: ProcessConfig):
        self.config = config
        self.process: Optional[subprocess.Popen] = None
        self.state = ProcessState(name=config.name)

    def is_running(self) -> bool:
        """Check if process is running."""
        if self.state.pid is None:
            return False

        try:
            if sys.platform == "win32":
                import ctypes
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(0x1000, False, self.state.pid)
                if handle:
                    kernel32.CloseHandle(handle)
                    return True
                return False
            else:
                os.kill(self.state.pid, 0)
                return True
        except (OSError, PermissionError):
            return False

    def start(self) -> tuple[bool, str]:
        """Start the process."""
        if self.is_running():
            return False, f"Already running (PID {self.state.pid})"

        # Build command
        cmd = [PYTHON_EXE] if self.config.command == "python" else [self.config.command]
        cmd.extend(self.config.args)

        # Ensure log directory exists
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        stdout_log = LOGS_DIR / f"{self.config.name}_stdout.log"
        stderr_log = LOGS_DIR / f"{self.config.name}_stderr.log"

        try:
            # Write start marker
            with open(stdout_log, "a", encoding="utf-8") as f:
                f.write(f"\n=== START {datetime.now().isoformat()} ===\n")

            # Open log files
            stdout_handle = open(stdout_log, "a", encoding="utf-8")
            stderr_handle = open(stderr_log, "a", encoding="utf-8")

            # Platform-specific flags
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

            # Start process
            env = os.environ.copy()
            env.update(self.config.env)

            self.process = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                stdout=stdout_handle,
                stderr=stderr_handle,
                env=env,
                creationflags=creationflags,
            )

            # Update state
            self.state.pid = self.process.pid
            self.state.status = ProcessStatus.RUNNING.value
            self.state.started_at = datetime.now(timezone.utc).isoformat()
            self.state.exit_code = None

            logger.info(f"Started {self.config.name} (PID {self.state.pid})")
            return True, str(self.state.pid)

        except Exception as e:
            logger.error(f"Failed to start {self.config.name}: {e}")
            self.state.status = ProcessStatus.FAILED.value
            return False, str(e)

    def stop(self, timeout: int = 10) -> bool:
        """Stop the process gracefully."""
        if not self.is_running():
            self.state.status = ProcessStatus.STOPPED.value
            self.state.pid = None
            return True

        self.state.status = ProcessStatus.STOPPING.value
        logger.info(f"Stopping {self.config.name} (PID {self.state.pid})...")

        try:
            if sys.platform == "win32":
                # Send CTRL_BREAK_EVENT
                os.kill(self.state.pid, signal.CTRL_BREAK_EVENT)
            else:
                os.kill(self.state.pid, signal.SIGTERM)

            # Wait for graceful shutdown
            for _ in range(timeout):
                if not self.is_running():
                    break
                time.sleep(1)

            # Force kill if still running
            if self.is_running():
                logger.warning(f"Force killing {self.config.name}")
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/PID", str(self.state.pid)],
                                   capture_output=True)
                else:
                    os.kill(self.state.pid, signal.SIGKILL)
                time.sleep(1)

        except Exception as e:
            logger.error(f"Error stopping {self.config.name}: {e}")

        self.state.status = ProcessStatus.STOPPED.value
        self.state.pid = None
        return not self.is_running()

    async def health_check(self) -> bool:
        """Perform health check."""
        if not self.config.health_check or not self.config.health_check.endpoint:
            # No health check configured, assume healthy if running
            self.state.health_status = "running" if self.is_running() else "stopped"
            return self.is_running()

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.config.health_check.endpoint,
                    timeout=aiohttp.ClientTimeout(total=self.config.health_check.timeout_sec)
                ) as resp:
                    self.state.health_status = "healthy" if resp.status == 200 else "unhealthy"
                    self.state.last_health_check = datetime.now(timezone.utc).isoformat()
                    return resp.status == 200
        except Exception as e:
            logger.debug(f"Health check failed for {self.config.name}: {e}")
            self.state.health_status = "unhealthy"
            self.state.last_health_check = datetime.now(timezone.utc).isoformat()
            return False

    def get_uptime(self) -> Optional[str]:
        """Get process uptime as human-readable string."""
        if not self.state.started_at or not self.is_running():
            return None

        try:
            started = datetime.fromisoformat(self.state.started_at.replace("Z", "+00:00"))
            delta = datetime.now(timezone.utc) - started
            hours, remainder = divmod(int(delta.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        except Exception:
            return None


# ═══════════════════════════════════════════════════════════════════════════════
# PROCESS MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class ProcessManager:
    """
    HOPE Process Manager.

    Manages all processes defined in PROCESS_REGISTRY.
    """

    def __init__(self):
        self.controllers: Dict[str, ProcessController] = {}
        self.state = ManagerState(started_at=datetime.now(timezone.utc).isoformat())
        self.running = False

        # Initialize controllers for all registered processes
        for name, config in PROCESS_REGISTRY.items():
            self.controllers[name] = ProcessController(config)

        # Load previous state
        self._load_state()

    def _load_state(self):
        """Load state from file."""
        if not MANAGER_STATE_FILE.exists():
            return

        try:
            data = json.loads(MANAGER_STATE_FILE.read_text(encoding="utf-8"))
            self.state = ManagerState.from_dict(data)

            # Sync PIDs from state to controllers
            for name, proc_state in self.state.processes.items():
                if name in self.controllers:
                    self.controllers[name].state = proc_state

            # Verify PIDs are still valid
            for name, ctrl in self.controllers.items():
                if ctrl.state.pid and not ctrl.is_running():
                    ctrl.state.pid = None
                    ctrl.state.status = ProcessStatus.STOPPED.value

            logger.debug("Loaded previous state")
        except Exception as e:
            logger.warning(f"Failed to load state: {e}")

    def _save_state(self):
        """Save state to file."""
        STATE_DIR.mkdir(parents=True, exist_ok=True)

        # Collect state from controllers
        for name, ctrl in self.controllers.items():
            self.state.processes[name] = ctrl.state

        # Write atomically
        tmp_file = MANAGER_STATE_FILE.with_suffix(".tmp")
        tmp_file.write_text(
            json.dumps(self.state.to_dict(), indent=2),
            encoding="utf-8"
        )
        tmp_file.replace(MANAGER_STATE_FILE)

    def start_process(self, name: str) -> tuple[bool, str]:
        """Start a single process."""
        if name not in self.controllers:
            return False, f"Unknown process: {name}"

        ctrl = self.controllers[name]
        config = PROCESS_REGISTRY[name]

        # Check dependencies
        for dep in config.depends_on:
            if dep in self.controllers:
                if not self.controllers[dep].is_running():
                    return False, f"Dependency not running: {dep}"

        success, result = ctrl.start()
        self._save_state()
        return success, result

    def stop_process(self, name: str) -> tuple[bool, str]:
        """Stop a single process."""
        if name not in self.controllers:
            return False, f"Unknown process: {name}"

        # Stop dependents first
        dependents = get_dependent_processes(name)
        for dep in dependents:
            if dep in self.controllers and self.controllers[dep].is_running():
                logger.info(f"Stopping dependent: {dep}")
                self.controllers[dep].stop()

        success = self.controllers[name].stop()
        self._save_state()
        return success, "Stopped" if success else "Failed to stop"

    def restart_process(self, name: str) -> tuple[bool, str]:
        """Restart a single process."""
        self.stop_process(name)
        time.sleep(2)  # Brief pause
        return self.start_process(name)

    def start_all(self, processes: Optional[List[str]] = None) -> Dict[str, tuple[bool, str]]:
        """Start multiple processes in dependency order."""
        if processes is None:
            processes = list(PROCESS_REGISTRY.keys())

        # Get startup order
        ordered = get_startup_order(processes)
        results = {}

        for name in ordered:
            if name in self.controllers:
                success, msg = self.start_process(name)
                results[name] = (success, msg)

                # Wait for process to stabilize
                if success:
                    time.sleep(1)

        return results

    def stop_all(self, processes: Optional[List[str]] = None) -> Dict[str, tuple[bool, str]]:
        """Stop multiple processes in reverse dependency order."""
        if processes is None:
            processes = list(PROCESS_REGISTRY.keys())

        # Get shutdown order (reverse of startup)
        ordered = get_startup_order(processes)
        ordered.reverse()

        results = {}
        for name in ordered:
            if name in self.controllers:
                success, msg = self.stop_process(name)
                results[name] = (success, msg)

        return results

    def get_status(self) -> Dict[str, Any]:
        """Get status of all processes."""
        status = {
            "manager": {
                "started_at": self.state.started_at,
                "profile": self.state.profile,
            },
            "processes": {},
            "summary": {
                "total": len(self.controllers),
                "running": 0,
                "stopped": 0,
                "failed": 0,
            }
        }

        for name, ctrl in self.controllers.items():
            is_running = ctrl.is_running()
            uptime = ctrl.get_uptime()

            proc_status = {
                "display_name": PROCESS_REGISTRY[name].display_name,
                "pid": ctrl.state.pid if is_running else None,
                "status": ctrl.state.status,
                "running": is_running,
                "uptime": uptime,
                "health": ctrl.state.health_status,
                "restarts": ctrl.state.restarts,
                "port": PROCESS_REGISTRY[name].port,
            }
            status["processes"][name] = proc_status

            # Update summary
            if is_running:
                status["summary"]["running"] += 1
            elif ctrl.state.status == ProcessStatus.FAILED.value:
                status["summary"]["failed"] += 1
            else:
                status["summary"]["stopped"] += 1

        return status

    async def monitor_loop(self, interval: int = 30):
        """Health monitoring loop."""
        logger.info(f"Starting health monitor (interval={interval}s)")
        self.running = True

        while self.running:
            # Check STOP flag
            if STOP_FLAG_FILE.exists():
                logger.info("STOP flag detected")
                break

            # Health check all processes
            for name, ctrl in self.controllers.items():
                if not ctrl.is_running():
                    continue

                healthy = await ctrl.health_check()

                if not healthy:
                    ctrl.state.consecutive_failures += 1
                    config = PROCESS_REGISTRY[name]

                    # Check restart policy
                    if (config.restart_policy == RestartPolicy.ALWAYS or
                        (config.restart_policy == RestartPolicy.ON_FAILURE and
                         ctrl.state.consecutive_failures >= config.health_check.retries if config.health_check else 3)):

                        if ctrl.state.restarts < config.max_restarts:
                            logger.warning(f"Restarting unhealthy process: {name}")
                            ctrl.state.restarts += 1
                            ctrl.stop()
                            time.sleep(config.restart_delay_sec)
                            ctrl.start()
                        else:
                            logger.error(f"Max restarts exceeded for {name}")
                            ctrl.state.status = ProcessStatus.FAILED.value
                else:
                    ctrl.state.consecutive_failures = 0

            self._save_state()
            await asyncio.sleep(interval)

        self.running = False


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def print_status(status: Dict[str, Any]):
    """Pretty print status."""
    summary = status["summary"]
    print("\n" + "=" * 60)
    print("HOPE PROCESS MANAGER")
    print("=" * 60)
    print(f"Running: {summary['running']}/{summary['total']} | "
          f"Stopped: {summary['stopped']} | Failed: {summary['failed']}")
    print("-" * 60)

    for name, proc in sorted(status["processes"].items()):
        # Status indicator (ASCII for Windows compatibility)
        if proc["running"]:
            indicator = "[OK]"
        elif proc["status"] == "failed":
            indicator = "[!!]"
        else:
            indicator = "[--]"

        # Format line
        uptime = proc["uptime"] or "--:--:--"
        pid = f"PID {proc['pid']}" if proc["pid"] else "N/A"
        port = f":{proc['port']}" if proc["port"] else ""

        print(f"{indicator} {proc['display_name']:<20} {uptime} | {pid:<10} | {proc['health']:<10}{port}")

    print("=" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="HOPE Process Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m scripts.hope_process_manager start              # Start all
  python -m scripts.hope_process_manager start dashboard    # Start one
  python -m scripts.hope_process_manager stop               # Stop all
  python -m scripts.hope_process_manager status             # Show status
  python -m scripts.hope_process_manager restart eye_of_god # Restart one
  python -m scripts.hope_process_manager daemon             # Run as daemon

Process groups:
  infrastructure  - friend_bridge, dashboard, telegram_bot
  trading         - eye_of_god, autotrader, order_executor
  monitoring      - position_watchdog, engine_watchdog
  minimal         - basic infrastructure only
  production      - full production stack
        """
    )

    parser.add_argument(
        "command",
        choices=["start", "stop", "restart", "status", "daemon", "list"],
        help="Command to execute"
    )
    parser.add_argument(
        "target",
        nargs="?",
        default="all",
        help="Process name, group name, or 'all' (default: all)"
    )
    parser.add_argument(
        "--profile",
        choices=["dev", "test", "testnet", "production"],
        default="dev",
        help="Startup profile (default: dev)"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Health check interval in seconds (default: 30)"
    )

    args = parser.parse_args()
    manager = ProcessManager()

    # Resolve target to process list
    if args.target == "all":
        processes = list(PROCESS_REGISTRY.keys())
    elif args.target in PROCESS_GROUPS:
        processes = PROCESS_GROUPS[args.target]
    elif args.target in PROCESS_REGISTRY:
        processes = [args.target]
    else:
        print(f"Unknown target: {args.target}")
        print(f"Available processes: {', '.join(PROCESS_REGISTRY.keys())}")
        print(f"Available groups: {', '.join(PROCESS_GROUPS.keys())}")
        sys.exit(1)

    # Execute command
    if args.command == "status":
        status = manager.get_status()
        print_status(status)

    elif args.command == "list":
        print("\nAvailable processes:")
        for name, config in PROCESS_REGISTRY.items():
            deps = f" (depends: {', '.join(config.depends_on)})" if config.depends_on else ""
            print(f"  {name}: {config.display_name}{deps}")

        print("\nAvailable groups:")
        for group, procs in PROCESS_GROUPS.items():
            print(f"  {group}: {', '.join(procs)}")

    elif args.command == "start":
        print(f"Starting {len(processes)} process(es)...")
        results = manager.start_all(processes)
        for name, (success, msg) in results.items():
            status = "[OK]" if success else "[FAIL]"
            print(f"  {status} {name}: {msg}")

    elif args.command == "stop":
        print(f"Stopping {len(processes)} process(es)...")
        results = manager.stop_all(processes)
        for name, (success, msg) in results.items():
            status = "[OK]" if success else "[FAIL]"
            print(f"  {status} {name}: {msg}")

    elif args.command == "restart":
        for name in processes:
            print(f"Restarting {name}...")
            success, msg = manager.restart_process(name)
            status = "[OK]" if success else "[FAIL]"
            print(f"  {status} {name}: {msg}")

    elif args.command == "daemon":
        print("Starting Process Manager daemon...")
        print(f"Profile: {args.profile}")
        print(f"Health check interval: {args.interval}s")
        print("Press Ctrl+C to stop\n")

        # Start processes based on profile
        if args.profile in STARTUP_PROFILES:
            profile = STARTUP_PROFILES[args.profile]
            processes = profile["processes"]
            print(f"Starting {profile['description']}...")
            manager.start_all(processes)

        # Run monitor loop
        try:
            asyncio.run(manager.monitor_loop(args.interval))
        except KeyboardInterrupt:
            print("\nShutting down...")
            manager.stop_all()


if __name__ == "__main__":
    main()
