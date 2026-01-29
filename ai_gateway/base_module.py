# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 16:00:00 UTC
# Purpose: Base class for AI modules with lifecycle hooks
# === END SIGNATURE ===
"""
AI-Gateway Base Module: Abstract base for all AI modules.

Provides lifecycle hooks:
- on_start(): Called when module starts
- on_stop(): Called when module stops
- run_once(): Execute one iteration

INVARIANT: All modules inherit from BaseAIModule.
INVARIANT: Lifecycle methods are async.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from .contracts import BaseArtifact, ModuleStatus
from .jsonl_writer import write_artifact
from .status_manager import get_status_manager

logger = logging.getLogger(__name__)


class ModuleState(str, Enum):
    """Module runtime state."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class ModuleConfig:
    """Configuration for AI module."""
    module_id: str
    interval_seconds: float = 300.0  # 5 min default
    max_consecutive_errors: int = 5
    enabled: bool = False
    timeout_seconds: float = 60.0
    extra: Dict[str, Any] = field(default_factory=dict)


class BaseAIModule(ABC):
    """
    Abstract base class for AI modules.

    Lifecycle:
        STOPPED → on_start() → RUNNING → run_once() (loop) → on_stop() → STOPPED

    Error handling:
        - Consecutive errors > max → auto-stop, state = ERROR
        - Single error → log, continue loop
    """

    def __init__(self, config: ModuleConfig):
        self.config = config
        self._state = ModuleState.STOPPED
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._consecutive_errors = 0
        self._last_run: Optional[datetime] = None
        self._last_error: Optional[str] = None
        self._status_manager = get_status_manager()

    @property
    def module_id(self) -> str:
        return self.config.module_id

    @property
    def state(self) -> ModuleState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._state == ModuleState.RUNNING

    @property
    def last_run(self) -> Optional[datetime]:
        return self._last_run

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    # === Lifecycle Hooks ===

    async def on_start(self) -> None:
        """
        Called when module starts.

        Override to initialize resources (connections, models, etc).
        Default: no-op.
        """
        pass

    async def on_stop(self) -> None:
        """
        Called when module stops.

        Override to cleanup resources.
        Default: no-op.
        """
        pass

    @abstractmethod
    async def run_once(self) -> Optional[BaseArtifact]:
        """
        Execute one iteration of the module.

        Must be implemented by subclasses.

        Returns:
            Artifact if successful, None if nothing to report.

        Raises:
            Exception on error (will be caught by runner).
        """
        ...

    # === Lifecycle Control ===

    async def start(self) -> bool:
        """
        Start the module.

        Returns:
            True if started successfully, False otherwise.
        """
        if self._state in (ModuleState.RUNNING, ModuleState.STARTING):
            logger.warning(f"Module {self.module_id} already running/starting")
            return False

        self._state = ModuleState.STARTING
        self._stop_event.clear()
        self._consecutive_errors = 0

        try:
            await asyncio.wait_for(
                self.on_start(),
                timeout=self.config.timeout_seconds
            )
        except asyncio.TimeoutError:
            logger.error(f"Module {self.module_id} on_start() timed out")
            self._state = ModuleState.ERROR
            self._status_manager.mark_error(self.module_id, "Startup timeout")
            return False
        except Exception as e:
            logger.error(f"Module {self.module_id} on_start() failed: {e}")
            self._state = ModuleState.ERROR
            self._status_manager.mark_error(self.module_id, str(e))
            return False

        self._state = ModuleState.RUNNING
        self._status_manager.mark_healthy(self.module_id)

        # Start background loop
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"Module {self.module_id} started (interval={self.config.interval_seconds}s)")

        return True

    async def stop(self, timeout: float = 10.0) -> bool:
        """
        Stop the module gracefully.

        Args:
            timeout: Max seconds to wait for graceful stop.

        Returns:
            True if stopped cleanly, False if forced.
        """
        if self._state == ModuleState.STOPPED:
            return True

        self._state = ModuleState.STOPPING
        self._stop_event.set()

        if self._task is not None and not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(f"Module {self.module_id} stop timed out, cancelling...")
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass

        try:
            await asyncio.wait_for(
                self.on_stop(),
                timeout=self.config.timeout_seconds
            )
        except asyncio.TimeoutError:
            logger.warning(f"Module {self.module_id} on_stop() timed out")
        except Exception as e:
            logger.error(f"Module {self.module_id} on_stop() failed: {e}")

        self._state = ModuleState.STOPPED
        self._task = None
        logger.info(f"Module {self.module_id} stopped")

        return True

    async def restart(self) -> bool:
        """Restart the module."""
        await self.stop()
        await asyncio.sleep(0.5)
        return await self.start()

    async def run_now(self) -> Optional[BaseArtifact]:
        """
        Execute one iteration immediately (outside of loop).

        Returns:
            Artifact if successful, None otherwise.
        """
        if self._state != ModuleState.RUNNING:
            logger.warning(f"Module {self.module_id} not running, cannot run_now")
            return None

        return await self._execute_once()

    # === Internal ===

    async def _run_loop(self) -> None:
        """Background loop - runs run_once() at interval."""
        while not self._stop_event.is_set():
            # Execute
            await self._execute_once()

            # Wait for interval or stop
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.config.interval_seconds
                )
                # Stop event was set
                break
            except asyncio.TimeoutError:
                # Interval elapsed, continue loop
                pass

    async def _execute_once(self) -> Optional[BaseArtifact]:
        """Execute run_once with error handling."""
        try:
            artifact = await asyncio.wait_for(
                self.run_once(),
                timeout=self.config.timeout_seconds
            )

            self._last_run = datetime.utcnow()
            self._consecutive_errors = 0
            self._last_error = None
            self._status_manager.mark_healthy(self.module_id)

            # Write artifact if produced
            if artifact is not None:
                artifact = artifact.with_checksum()
                if write_artifact(artifact):
                    logger.debug(f"Module {self.module_id} wrote artifact")
                else:
                    logger.warning(f"Module {self.module_id} failed to write artifact")

            return artifact

        except asyncio.TimeoutError:
            self._handle_error("Execution timeout")
            return None
        except Exception as e:
            self._handle_error(str(e))
            return None

    def _handle_error(self, error: str) -> None:
        """Handle execution error."""
        self._consecutive_errors += 1
        self._last_error = error
        logger.error(f"Module {self.module_id} error ({self._consecutive_errors}): {error}")

        if self._consecutive_errors >= self.config.max_consecutive_errors:
            logger.error(f"Module {self.module_id} max errors reached, stopping")
            self._state = ModuleState.ERROR
            self._stop_event.set()
            self._status_manager.mark_error(self.module_id, f"Max errors: {error}")
        else:
            self._status_manager.mark_warning(self.module_id, error)

    def get_info(self) -> Dict[str, Any]:
        """Get module info dict."""
        return {
            "module_id": self.module_id,
            "state": self._state.value,
            "is_running": self.is_running,
            "interval_seconds": self.config.interval_seconds,
            "consecutive_errors": self._consecutive_errors,
            "last_run": self._last_run.isoformat() + "Z" if self._last_run else None,
            "last_error": self._last_error,
            "enabled": self.config.enabled,
        }
