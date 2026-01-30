# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 11:45:00 UTC
# Purpose: Process configurations and registry for HOPE system
# === END SIGNATURE ===
"""
Process Registry — Central configuration for all HOPE processes.

Defines:
- Process configurations (command, env, dependencies)
- Health check endpoints
- Restart policies
- Port assignments

Usage:
    from core.process_registry import PROCESS_REGISTRY, ProcessConfig

    config = PROCESS_REGISTRY["friend_bridge"]
    print(config.command)  # "python -m core.friend_bridge_server"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

# Project root
PROJECT_ROOT = Path(__file__).parent.parent


class RestartPolicy(str, Enum):
    """Process restart policies."""
    ALWAYS = "always"          # Always restart on exit
    ON_FAILURE = "on-failure"  # Restart only on non-zero exit
    NEVER = "never"            # Never restart


class ProcessStatus(str, Enum):
    """Process status values."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"
    RESTARTING = "restarting"


@dataclass
class HealthCheck:
    """Health check configuration."""
    endpoint: Optional[str] = None     # HTTP endpoint (e.g., "http://localhost:8765/healthz")
    interval_sec: int = 30             # Check interval
    timeout_sec: int = 5               # Request timeout
    retries: int = 3                   # Retries before marking unhealthy
    start_period_sec: int = 10         # Grace period after start


@dataclass
class ProcessConfig:
    """Configuration for a managed process."""
    name: str                                           # Unique identifier
    display_name: str                                   # Human-readable name
    command: str                                        # Command to run
    args: List[str] = field(default_factory=list)      # Command arguments
    env: Dict[str, str] = field(default_factory=dict)  # Environment variables
    depends_on: List[str] = field(default_factory=list)  # Process dependencies
    health_check: Optional[HealthCheck] = None         # Health check config
    restart_policy: RestartPolicy = RestartPolicy.ON_FAILURE
    max_restarts: int = 3                              # Max consecutive restarts
    restart_delay_sec: int = 5                         # Delay between restarts
    port: Optional[int] = None                         # Main port (for display)
    description: str = ""                              # Process description
    critical: bool = False                             # If True, failure stops all

    def get_full_command(self) -> str:
        """Get full command with arguments."""
        if self.args:
            return f"{self.command} {' '.join(self.args)}"
        return self.command


# =============================================================================
# PROCESS REGISTRY — SSoT for all managed processes
# =============================================================================

PROCESS_REGISTRY: Dict[str, ProcessConfig] = {

    # =========================================================================
    # INFRASTRUCTURE PROCESSES
    # =========================================================================

    "friend_bridge": ProcessConfig(
        name="friend_bridge",
        display_name="Friend Bridge",
        command="python",
        args=["-m", "core.friend_bridge_server", "--insecure"],
        env={},
        depends_on=[],
        health_check=HealthCheck(
            endpoint="http://localhost:8765/healthz",
            interval_sec=30,
            timeout_sec=5,
            retries=3,
        ),
        restart_policy=RestartPolicy.ALWAYS,
        max_restarts=5,
        restart_delay_sec=3,
        port=8765,
        description="HTTP API for Claude <-> GPT communication",
        critical=False,
    ),

    "dashboard": ProcessConfig(
        name="dashboard",
        display_name="Live Dashboard",
        command="python",
        args=["scripts/live_dashboard.py", "--port", "8080"],
        env={},
        depends_on=[],
        health_check=HealthCheck(
            endpoint="http://localhost:8080/",
            interval_sec=30,
            timeout_sec=5,
            retries=3,
        ),
        restart_policy=RestartPolicy.ALWAYS,
        max_restarts=3,
        restart_delay_sec=5,
        port=8080,
        description="Real-time web visualization dashboard",
        critical=False,
    ),

    # =========================================================================
    # TRADING PROCESSES
    # =========================================================================

    # NOTE: eye_of_god_v3.py is a LIBRARY, not a daemon!
    # It should be imported by autotrader.py, not run standalone.
    # This entry is kept for manual testing only.
    "eye_of_god": ProcessConfig(
        name="eye_of_god",
        display_name="Eye of God V3",
        command="python",
        args=["scripts/eye_of_god_v3.py", "--test"],  # Test mode only
        env={"TRADING_MODE": "DRY"},
        depends_on=["friend_bridge"],
        health_check=None,
        restart_policy=RestartPolicy.NEVER,  # One-shot test, not a daemon
        max_restarts=0,
        restart_delay_sec=0,
        port=None,
        description="Two-chamber trading decision system (library, not daemon)",
        critical=False,  # Not critical - it's a library
    ),

    "autotrader": ProcessConfig(
        name="autotrader",
        display_name="Autotrader",
        command="python",
        args=["scripts/autotrader.py", "--mode", "DRY"],
        env={"TRADING_MODE": "DRY"},
        depends_on=[],  # Eye of God V3 is now integrated inside autotrader
        health_check=None,
        restart_policy=RestartPolicy.ON_FAILURE,
        max_restarts=3,
        restart_delay_sec=10,
        port=None,
        description="Autonomous trading execution loop (includes Eye of God V3)",
        critical=True,
    ),

    "order_executor": ProcessConfig(
        name="order_executor",
        display_name="Order Executor",
        command="python",
        args=["scripts/order_executor.py", "--mode", "DRY"],
        env={"TRADING_MODE": "DRY"},
        depends_on=["autotrader"],
        health_check=None,
        restart_policy=RestartPolicy.ON_FAILURE,
        max_restarts=2,  # More conservative for order execution
        restart_delay_sec=15,
        port=None,
        description="Real Binance order execution",
        critical=True,
    ),

    # =========================================================================
    # AI PROCESSES
    # =========================================================================

    "ai_gateway": ProcessConfig(
        name="ai_gateway",
        display_name="AI Gateway Server",
        command="python",
        args=["-m", "ai_gateway.server"],
        env={},
        depends_on=[],
        health_check=HealthCheck(
            endpoint="http://localhost:8100/health",
            interval_sec=30,
            timeout_sec=5,
            retries=3,
        ),
        restart_policy=RestartPolicy.ON_FAILURE,
        max_restarts=3,
        restart_delay_sec=5,
        port=8100,
        description="AI Gateway HTTP API",
        critical=False,
    ),

    "eye_trainer": ProcessConfig(
        name="eye_trainer",
        display_name="Eye Trainer",
        command="python",
        args=["scripts/eye_trainer.py"],
        env={},
        depends_on=[],
        health_check=None,
        restart_policy=RestartPolicy.NEVER,  # One-shot training process
        max_restarts=0,
        restart_delay_sec=0,
        port=None,
        description="Eye of God model training",
        critical=False,
    ),

    # =========================================================================
    # MONITORING PROCESSES
    # =========================================================================

    "position_watchdog": ProcessConfig(
        name="position_watchdog",
        display_name="Position Watchdog",
        command="python",
        args=["scripts/position_watchdog.py"],
        env={},
        depends_on=["order_executor"],
        health_check=None,
        restart_policy=RestartPolicy.ALWAYS,
        max_restarts=5,
        restart_delay_sec=5,
        port=None,
        description="Monitor and protect open positions",
        critical=True,
    ),

    "engine_watchdog": ProcessConfig(
        name="engine_watchdog",
        display_name="Engine Watchdog",
        command="python",
        args=["scripts/engine_watchdog.py"],
        env={},
        depends_on=[],
        health_check=None,
        restart_policy=RestartPolicy.ALWAYS,
        max_restarts=10,
        restart_delay_sec=3,
        port=None,
        description="Monitor all HOPE engine processes",
        critical=False,
    ),

    # =========================================================================
    # TELEGRAM BOT
    # =========================================================================

    "telegram_bot": ProcessConfig(
        name="telegram_bot",
        display_name="Telegram Bot",
        command=r"C:\Users\kirillDev\Desktop\TradingBot\.venv\Scripts\python.exe",
        args=["tg_bot_simple.py"],
        env={},
        depends_on=[],
        health_check=None,  # No HTTP, uses Telegram polling
        restart_policy=RestartPolicy.ALWAYS,
        max_restarts=5,
        restart_delay_sec=10,
        port=None,
        description="Telegram control panel bot",
        critical=False,
    ),

    # =========================================================================
    # SIGNAL PROCESSING
    # =========================================================================

    "signal_watcher": ProcessConfig(
        name="signal_watcher",
        display_name="Signal Watcher",
        command="python",
        args=["scripts/signal_watcher.py", "--watch"],
        env={},
        depends_on=["autotrader"],  # Needs autotrader to forward signals
        health_check=None,
        restart_policy=RestartPolicy.ALWAYS,
        max_restarts=5,
        restart_delay_sec=5,
        port=None,
        description="Watches MoonBot signals and forwards to AutoTrader",
        critical=False,
    ),

    "pump_detector": ProcessConfig(
        name="pump_detector",
        display_name="Pump Detector",
        command="python",
        args=["scripts/pump_detector.py", "--top", "20"],
        env={},
        depends_on=["autotrader"],
        health_check=None,
        restart_policy=RestartPolicy.ALWAYS,
        max_restarts=5,
        restart_delay_sec=10,
        port=None,
        description="Real-time pump detection from Binance WebSocket",
        critical=False,
    ),
}


# =============================================================================
# PROCESS GROUPS — For bulk operations
# =============================================================================

PROCESS_GROUPS: Dict[str, List[str]] = {
    "infrastructure": ["friend_bridge", "dashboard", "telegram_bot"],
    "trading": ["autotrader", "order_executor"],
    "signals": ["signal_watcher", "pump_detector"],  # Signal generation
    "monitoring": ["position_watchdog", "engine_watchdog"],
    "ai": ["ai_gateway", "eye_trainer"],
    "all": list(PROCESS_REGISTRY.keys()),
    "minimal": ["friend_bridge", "dashboard", "telegram_bot"],
    "auto_trading": [  # Full automatic trading stack
        "ai_gateway",
        "autotrader",
        "pump_detector",
    ],
    "production": [
        "friend_bridge",
        "dashboard",
        "ai_gateway",
        "autotrader",
        "pump_detector",
        "position_watchdog",
        "engine_watchdog",
    ],
}


# =============================================================================
# STARTUP PROFILES — Predefined configurations
# =============================================================================

STARTUP_PROFILES: Dict[str, Dict[str, Any]] = {
    "dev": {
        "processes": ["friend_bridge", "dashboard", "telegram_bot"],
        "mode": "DRY",
        "description": "Development mode: infrastructure only",
    },
    "test": {
        "processes": [
            "friend_bridge",
            "dashboard",
            "telegram_bot",
            "eye_of_god",
            "autotrader",
        ],
        "mode": "DRY",
        "description": "Testing mode: trading without execution",
    },
    "testnet": {
        "processes": PROCESS_GROUPS["production"],
        "mode": "TESTNET",
        "description": "Testnet mode: full system on Binance testnet",
    },
    "production": {
        "processes": PROCESS_GROUPS["production"],
        "mode": "LIVE",
        "description": "Production mode: REAL MONEY TRADING",
    },
}


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def get_process_config(name: str) -> Optional[ProcessConfig]:
    """Get process configuration by name."""
    return PROCESS_REGISTRY.get(name)


def get_startup_order(processes: List[str]) -> List[str]:
    """
    Return processes in dependency order (topological sort).

    Processes with dependencies come after their dependencies.
    """
    # Build dependency graph
    graph: Dict[str, List[str]] = {name: [] for name in processes}
    for name in processes:
        config = PROCESS_REGISTRY.get(name)
        if config:
            for dep in config.depends_on:
                if dep in processes:
                    graph[name].append(dep)

    # Topological sort (Kahn's algorithm)
    in_degree = {name: 0 for name in processes}
    for name in processes:
        for dep in graph[name]:
            in_degree[name] += 1

    queue = [name for name in processes if in_degree[name] == 0]
    result = []

    while queue:
        node = queue.pop(0)
        result.append(node)

        for name in processes:
            if node in graph[name]:
                in_degree[name] -= 1
                if in_degree[name] == 0:
                    queue.append(name)

    return result


def get_dependent_processes(name: str) -> List[str]:
    """Get all processes that depend on the given process."""
    dependents = []
    for proc_name, config in PROCESS_REGISTRY.items():
        if name in config.depends_on:
            dependents.append(proc_name)
    return dependents


def validate_registry() -> List[str]:
    """
    Validate process registry for errors.

    Returns list of error messages (empty if valid).
    """
    errors = []

    for name, config in PROCESS_REGISTRY.items():
        # Check name matches
        if config.name != name:
            errors.append(f"{name}: config.name mismatch ({config.name})")

        # Check dependencies exist
        for dep in config.depends_on:
            if dep not in PROCESS_REGISTRY:
                errors.append(f"{name}: unknown dependency '{dep}'")

        # Check no circular dependencies
        visited = set()
        stack = [name]
        while stack:
            current = stack.pop()
            if current in visited:
                errors.append(f"{name}: circular dependency detected")
                break
            visited.add(current)
            cfg = PROCESS_REGISTRY.get(current)
            if cfg:
                stack.extend(cfg.depends_on)

    return errors


# =============================================================================
# MAIN — For testing
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("HOPE PROCESS REGISTRY")
    print("=" * 60)

    # Validate
    errors = validate_registry()
    if errors:
        print("\nERRORS:")
        for err in errors:
            print(f"  - {err}")
    else:
        print("\nRegistry validation: OK")

    # Show processes
    print(f"\nTotal processes: {len(PROCESS_REGISTRY)}")
    print("\nProcesses:")
    for name, config in PROCESS_REGISTRY.items():
        deps = f" (depends: {', '.join(config.depends_on)})" if config.depends_on else ""
        critical = " [CRITICAL]" if config.critical else ""
        print(f"  {name}: {config.display_name}{deps}{critical}")

    # Show groups
    print("\nGroups:")
    for group, procs in PROCESS_GROUPS.items():
        print(f"  {group}: {len(procs)} processes")

    # Show startup order
    print("\nStartup order (production):")
    order = get_startup_order(PROCESS_GROUPS["production"])
    for i, name in enumerate(order, 1):
        print(f"  {i}. {name}")
