#!/usr/bin/env python3
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-20 12:00:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 17:05:00 UTC
# Change: Added HOPE bootstrap (LAW-001), moved logging to main()
# === END SIGNATURE ===
"""
HOPE Supervisor - Process manager with auto-restart.

Manages all HOPE system processes:
- telegram_bot
- gpt_orchestrator_runner
- claude_executor_runner
- claude_validator_runner
- router_runner

Features:
- Auto-restart on crash
- Health monitoring
- Graceful shutdown (Ctrl+C)
- Logging to logs/supervisor.log
"""

import subprocess
import sys
import time
import signal
import logging
from pathlib import Path
from typing import Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime

# Setup paths
MINIBOT_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = MINIBOT_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# Python executable
PYTHON = r"C:\Users\kirillDev\AppData\Local\Programs\Python\Python312\python.exe"

# Logger initialized in main() after bootstrap
logger: logging.Logger | None = None


def _setup_logging() -> logging.Logger:
    """Setup logging after bootstrap."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOGS_DIR / "supervisor.log", encoding="utf-8")
        ]
    )
    return logging.getLogger("supervisor")


@dataclass
class ProcessConfig:
    """Configuration for a managed process."""
    name: str
    module: str
    enabled: bool = True
    restart_delay: float = 3.0
    max_restarts: int = 10
    restart_window: float = 60.0  # Reset restart count after this many seconds


@dataclass
class ProcessState:
    """Runtime state for a managed process."""
    process: Optional[subprocess.Popen] = None
    restart_count: int = 0
    last_restart: float = 0
    started_at: float = 0


# Process configurations
PROCESSES: Dict[str, ProcessConfig] = {
    "telegram_bot": ProcessConfig(
        name="Telegram Bot",
        module="integrations.telegram_bot",
        enabled=True,
    ),
    "gpt_orchestrator": ProcessConfig(
        name="GPT Orchestrator",
        module="core.gpt_orchestrator_runner",
        enabled=False,  # Enable when ready
    ),
    "claude_executor": ProcessConfig(
        name="Claude Executor",
        module="core.claude_executor_runner",
        enabled=False,  # Enable when ready
    ),
    "claude_validator": ProcessConfig(
        name="Claude Validator",
        module="core.claude_validator_runner",
        enabled=False,  # Enable when ready
    ),
    "router": ProcessConfig(
        name="Router",
        module="core.router_runner",
        enabled=False,  # Enable when ready
    ),
}


class Supervisor:
    """Process supervisor with auto-restart capability."""

    def __init__(self):
        self.states: Dict[str, ProcessState] = {}
        self.running = False

        # Initialize states
        for key in PROCESSES:
            self.states[key] = ProcessState()

    def start_process(self, key: str) -> bool:
        """Start a managed process."""
        config = PROCESSES.get(key)
        if not config or not config.enabled:
            return False

        state = self.states[key]

        # Check restart limits
        now = time.time()
        if now - state.last_restart > config.restart_window:
            state.restart_count = 0

        if state.restart_count >= config.max_restarts:
            logger.error(f"[{config.name}] Max restarts ({config.max_restarts}) reached. Giving up.")
            return False

        try:
            # Start the process
            cmd = [PYTHON, "-m", config.module]
            state.process = subprocess.Popen(
                cmd,
                cwd=str(MINIBOT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
            )
            state.started_at = now
            state.last_restart = now
            state.restart_count += 1

            logger.info(f"[{config.name}] Started (PID: {state.process.pid}, restart #{state.restart_count})")
            return True

        except Exception as e:
            logger.error(f"[{config.name}] Failed to start: {e}")
            return False

    def stop_process(self, key: str) -> None:
        """Stop a managed process."""
        config = PROCESSES.get(key)
        state = self.states.get(key)
        if not config or not state or not state.process:
            return

        try:
            state.process.terminate()
            state.process.wait(timeout=5)
            logger.info(f"[{config.name}] Stopped gracefully")
        except subprocess.TimeoutExpired:
            state.process.kill()
            logger.warning(f"[{config.name}] Killed (timeout)")
        except Exception as e:
            logger.error(f"[{config.name}] Error stopping: {e}")
        finally:
            state.process = None

    def check_process(self, key: str) -> bool:
        """Check if process is running, restart if dead."""
        config = PROCESSES.get(key)
        state = self.states.get(key)
        if not config or not config.enabled or not state:
            return False

        if state.process is None:
            return self.start_process(key)

        # Check if process is still running
        poll = state.process.poll()
        if poll is not None:
            logger.warning(f"[{config.name}] Died (exit code: {poll}). Restarting in {config.restart_delay}s...")
            state.process = None
            time.sleep(config.restart_delay)
            return self.start_process(key)

        return True

    def start_all(self) -> None:
        """Start all enabled processes."""
        logger.info("=" * 50)
        logger.info("HOPE Supervisor starting...")
        logger.info("=" * 50)

        for key, config in PROCESSES.items():
            if config.enabled:
                self.start_process(key)
            else:
                logger.info(f"[{config.name}] Disabled, skipping")

    def stop_all(self) -> None:
        """Stop all processes."""
        logger.info("Stopping all processes...")
        for key in PROCESSES:
            self.stop_process(key)
        logger.info("All processes stopped")

    def run(self) -> None:
        """Main supervisor loop."""
        self.running = True

        # Handle Ctrl+C
        def signal_handler(sig, frame):
            logger.info("Received shutdown signal")
            self.running = False

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        self.start_all()

        # Monitor loop
        try:
            while self.running:
                for key in PROCESSES:
                    if PROCESSES[key].enabled:
                        self.check_process(key)
                time.sleep(5)  # Check every 5 seconds
        finally:
            self.stop_all()

    def status(self) -> str:
        """Get status of all processes."""
        lines = ["HOPE Supervisor Status", "=" * 40]
        for key, config in PROCESSES.items():
            state = self.states.get(key)
            if not config.enabled:
                status = "DISABLED"
            elif state and state.process and state.process.poll() is None:
                uptime = time.time() - state.started_at
                status = f"RUNNING (PID: {state.process.pid}, uptime: {int(uptime)}s)"
            else:
                status = "STOPPED"
            lines.append(f"{config.name}: {status}")
        return "\n".join(lines)


def main():
    """Entry point."""
    # HOPE-LAW-001: Policy bootstrap MUST be first
    from core.policy.bootstrap import bootstrap
    bootstrap("supervisor", network_profile="core")

    global logger
    logger = _setup_logging()

    supervisor = Supervisor()

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "status":
            print(supervisor.status())
            return
        elif cmd == "help":
            print("Usage: python -m tools.supervisor [command]")
            print("Commands:")
            print("  (none)  - Start supervisor")
            print("  status  - Show process status")
            print("  help    - Show this help")
            return

    supervisor.run()


if __name__ == "__main__":
    main()
