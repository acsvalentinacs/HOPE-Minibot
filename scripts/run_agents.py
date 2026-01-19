"""
Dual-Agent Launcher - Run Claude and GPT agents as concurrent services.

This script launches both IPC agents so ACK protocol works properly:
- Claude processes tasks from GPT
- GPT processes responses from Claude and sends ACKs
- Claude receives ACKs and clears pending_acks

Usage:
    python scripts/run_agents.py
    python scripts/run_agents.py --poll_sec=5
    python scripts/run_agents.py --enable_debug_tasks

Press Ctrl+C to stop both agents.
"""
from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.ipc_agent import (
    ClaudeAgent,
    GPTAgent,
    init_ipc_folders,
    setup_ipc_logging,
)


class AgentRunner:
    """Runs an IPC agent in a background thread."""

    def __init__(self, agent, poll_sec: float = 2.0) -> None:
        self._agent = agent
        self._poll_sec = poll_sec
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._logger = logging.getLogger(f"runner.{agent._role.value}")

    def start(self) -> None:
        """Start agent in background thread."""
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._logger.info("Started %s agent (poll=%.1fs)", self._agent._role.value, self._poll_sec)

    def stop(self) -> None:
        """Stop agent gracefully."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
        self._logger.info("Stopped %s agent", self._agent._role.value)

    def _run_loop(self) -> None:
        """Main polling loop."""
        while self._running:
            try:
                result = self._agent.process_cycle()
                if result["processed"] > 0 or result["resent"] > 0:
                    self._logger.info(
                        "[%s] processed=%d resent=%d",
                        self._agent._role.value,
                        result["processed"],
                        result["resent"],
                    )
            except Exception as e:
                self._logger.error("[%s] cycle error: %s", self._agent._role.value, e)

            time.sleep(self._poll_sec)


def main() -> int:
    """Launch dual agents."""
    setup_ipc_logging()
    init_ipc_folders()

    logger = logging.getLogger("launcher")

    # Parse args
    poll_sec = 2.0
    enable_debug = False

    for arg in sys.argv[1:]:
        if arg.startswith("--poll_sec="):
            poll_sec = float(arg.split("=")[1])
        elif arg == "--enable_debug_tasks":
            enable_debug = True

    # Create agents
    claude = ClaudeAgent()
    gpt = GPTAgent()

    if enable_debug:
        claude.enable_debug_handlers("chat_friends")
        gpt.enable_debug_handlers("chat_friends")
        logger.warning("Debug handlers ENABLED on both agents")

    # Create runners
    claude_runner = AgentRunner(claude, poll_sec)
    gpt_runner = AgentRunner(gpt, poll_sec)

    # Handle Ctrl+C
    stop_event = threading.Event()

    def signal_handler(sig, frame):
        logger.info("Shutdown signal received")
        stop_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start agents
    logger.info("=== DUAL AGENT LAUNCHER ===")
    logger.info("Poll interval: %.1f seconds", poll_sec)
    logger.info("Press Ctrl+C to stop")

    claude_runner.start()
    gpt_runner.start()

    # Wait for shutdown
    while not stop_event.is_set():
        time.sleep(1.0)

        # Periodic status
        claude_stats = claude.get_stats()
        gpt_stats = gpt.get_stats()

        if claude_stats["pending_acks_count"] > 0 or gpt_stats["pending_acks_count"] > 0:
            logger.debug(
                "pending_acks: claude=%d gpt=%d",
                claude_stats["pending_acks_count"],
                gpt_stats["pending_acks_count"],
            )

    # Stop agents
    claude_runner.stop()
    gpt_runner.stop()

    logger.info("=== SHUTDOWN COMPLETE ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
