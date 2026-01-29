# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 03:35:00 UTC
# Purpose: Module status management with emoji indicators for Telegram
# === END SIGNATURE ===
"""
AI-Gateway Status Manager: Track module health with visual indicators.

Provides 🟢🟡🔴⚪ status display for Telegram bot.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

from .contracts import ModuleStatus, ModuleStatusArtifact, create_artifact_id

logger = logging.getLogger(__name__)


# Status emoji mapping (Russian tooltips)
STATUS_DISPLAY = {
    ModuleStatus.HEALTHY: ("🟢", "Работает нормально"),
    ModuleStatus.WARNING: ("🟡", "Требует внимания"),
    ModuleStatus.ERROR: ("🔴", "Ошибка, требуется ремонт"),
    ModuleStatus.DISABLED: ("⚪", "Отключен пользователем"),
}

# Module names in Russian
MODULE_NAMES_RU = {
    "sentiment": "Анализ настроений",
    "regime": "Детектор режима",
    "doctor": "Доктор стратегии",
    "anomaly": "Сканер аномалий",
    "self_improver": "Самообучение ИИ",
}

# Default TTL for status artifacts
STATUS_TTL_SECONDS = 60


class StatusManager:
    """
    Manage AI module statuses with persistence and Telegram display.

    Thread-safe singleton for tracking module health across the gateway.

    INVARIANT: Single instance per state_dir path.
    INVARIANT: All public methods are thread-safe.
    """

    _instances: dict[str, "StatusManager"] = {}
    _lock = Lock()

    def __new__(cls, state_dir: Optional[Path] = None) -> "StatusManager":
        resolved_dir = str(state_dir or Path("state/ai"))
        with cls._lock:
            if resolved_dir not in cls._instances:
                instance = super().__new__(cls)
                instance._initialized = False
                cls._instances[resolved_dir] = instance
            return cls._instances[resolved_dir]

    def __init__(self, state_dir: Optional[Path] = None):
        if self._initialized:
            return

        self._state_dir = state_dir or Path("state/ai")
        self._status_file = self._state_dir / "module_status.json"

        # Module states
        self._modules: Dict[str, ModuleStatus] = {
            "sentiment": ModuleStatus.DISABLED,
            "regime": ModuleStatus.DISABLED,
            "doctor": ModuleStatus.DISABLED,
            "anomaly": ModuleStatus.DISABLED,
            "self_improver": ModuleStatus.DISABLED,
        }

        # Tracking data
        self._last_run: Dict[str, datetime] = {}
        self._error_counts: Dict[str, int] = {}
        self._last_errors: Dict[str, str] = {}
        self._enabled: Dict[str, bool] = {}

        # Load persisted state
        self._load_state()
        self._initialized = True

    def _load_state(self) -> None:
        """Load persisted module states from disk."""
        try:
            if self._status_file.exists():
                data = json.loads(self._status_file.read_text(encoding="utf-8"))

                for module, status_str in data.get("modules", {}).items():
                    if module in self._modules:
                        try:
                            self._modules[module] = ModuleStatus(status_str)
                        except ValueError:
                            self._modules[module] = ModuleStatus.DISABLED

                for module, ts_str in data.get("last_run", {}).items():
                    try:
                        self._last_run[module] = datetime.fromisoformat(ts_str.replace("Z", ""))
                    except (ValueError, TypeError):
                        pass

                self._error_counts = data.get("error_counts", {})
                self._enabled = data.get("enabled", {})

                logger.info(f"Loaded module states from {self._status_file}")
        except Exception as e:
            logger.warning(f"Failed to load module states: {e}")

    def _save_state(self) -> None:
        """Persist module states to disk (atomic write)."""
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)

            data = {
                "modules": {k: v.value for k, v in self._modules.items()},
                "last_run": {k: v.isoformat() + "Z" for k, v in self._last_run.items()},
                "error_counts": self._error_counts,
                "enabled": self._enabled,
                "updated_at": datetime.utcnow().isoformat() + "Z",
            }

            # Atomic write
            tmp_file = self._status_file.with_suffix(".tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, self._status_file)

        except Exception as e:
            logger.error(f"Failed to save module states: {e}")

    # === Status Updates ===

    def set_status(self, module: str, status: ModuleStatus, error_msg: Optional[str] = None) -> None:
        """Update module status."""
        if module not in self._modules:
            logger.warning(f"Unknown module: {module}")
            return

        old_status = self._modules[module]
        self._modules[module] = status

        if status == ModuleStatus.ERROR and error_msg:
            self._error_counts[module] = self._error_counts.get(module, 0) + 1
            self._last_errors[module] = error_msg
        elif status == ModuleStatus.HEALTHY:
            self._error_counts[module] = 0
            self._last_errors.pop(module, None)

        if old_status != status:
            logger.info(f"Module {module}: {old_status.value} -> {status.value}")

        self._save_state()

    def mark_healthy(self, module: str) -> None:
        """Mark module as healthy after successful run."""
        self.set_status(module, ModuleStatus.HEALTHY)
        self._last_run[module] = datetime.utcnow()
        self._save_state()

    def mark_warning(self, module: str, reason: str) -> None:
        """Mark module with warning status."""
        self.set_status(module, ModuleStatus.WARNING, reason)

    def mark_error(self, module: str, error: str) -> None:
        """Mark module as errored."""
        self.set_status(module, ModuleStatus.ERROR, error)

    def enable_module(self, module: str) -> bool:
        """Enable a module (user action)."""
        if module not in self._modules:
            return False

        self._enabled[module] = True
        if self._modules[module] == ModuleStatus.DISABLED:
            self._modules[module] = ModuleStatus.HEALTHY

        self._save_state()
        logger.info(f"Module {module} enabled")
        return True

    def disable_module(self, module: str) -> bool:
        """Disable a module (user action)."""
        if module not in self._modules:
            return False

        self._enabled[module] = False
        self._modules[module] = ModuleStatus.DISABLED

        self._save_state()
        logger.info(f"Module {module} disabled")
        return True

    def is_enabled(self, module: str) -> bool:
        """Check if module is enabled."""
        return self._enabled.get(module, False)

    # === Status Queries ===

    def get_status(self, module: str) -> ModuleStatus:
        """Get current module status."""
        return self._modules.get(module, ModuleStatus.DISABLED)

    def get_emoji(self, module: str) -> str:
        """Get status emoji for module."""
        status = self.get_status(module)
        return STATUS_DISPLAY.get(status, ("⚪", ""))[0]

    def get_tooltip(self, module: str) -> str:
        """Get Russian tooltip for module status."""
        status = self.get_status(module)
        return STATUS_DISPLAY.get(status, ("", "Неизвестно"))[1]

    def get_last_run(self, module: str) -> Optional[datetime]:
        """Get last successful run time."""
        return self._last_run.get(module)

    def get_error_count(self, module: str) -> int:
        """Get consecutive error count."""
        return self._error_counts.get(module, 0)

    def get_last_error(self, module: str) -> Optional[str]:
        """Get last error message."""
        return self._last_errors.get(module)

    # === Aggregate Status ===

    def get_gateway_status(self) -> ModuleStatus:
        """Get overall gateway status (worst of all enabled modules)."""
        enabled_statuses = [
            self._modules[m] for m in self._modules
            if self._enabled.get(m, False)
        ]

        if not enabled_statuses:
            return ModuleStatus.DISABLED

        # Priority: ERROR > WARNING > HEALTHY
        if any(s == ModuleStatus.ERROR for s in enabled_statuses):
            return ModuleStatus.ERROR
        if any(s == ModuleStatus.WARNING for s in enabled_statuses):
            return ModuleStatus.WARNING
        return ModuleStatus.HEALTHY

    def get_active_count(self) -> int:
        """Get count of active (non-disabled) modules."""
        return sum(1 for m, e in self._enabled.items() if e and self._modules.get(m) != ModuleStatus.DISABLED)

    # === Artifact Generation ===

    def to_artifact(self) -> ModuleStatusArtifact:
        """Generate status artifact for Core consumption."""
        artifact = ModuleStatusArtifact(
            artifact_id=create_artifact_id("status"),
            ttl_seconds=STATUS_TTL_SECONDS,
            modules=self._modules.copy(),
            last_run_times=self._last_run.copy(),
            error_counts=self._error_counts.copy(),
            gateway_status=self.get_gateway_status(),
            active_modules=self.get_active_count(),
            total_modules=len(self._modules),
        )
        return artifact.with_checksum()

    # === Telegram Display ===

    def format_status_block(self) -> str:
        """
        Format status block for Telegram display (Russian).

        Returns box-drawing formatted status panel.
        """
        lines = []
        lines.append("╔══════════════════════════════╗")
        lines.append("║    🤖 AI-GATEWAY СТАТУС      ║")
        lines.append("╠══════════════════════════════╣")

        gateway_emoji = STATUS_DISPLAY.get(self.get_gateway_status(), ("⚪", ""))[0]
        lines.append(f"║ Шлюз: {gateway_emoji} ({self.get_active_count()}/{len(self._modules)} активно)    ║")
        lines.append("╟──────────────────────────────╢")

        for module in ["sentiment", "regime", "doctor", "anomaly", "self_improver"]:
            emoji = self.get_emoji(module)
            name_ru = MODULE_NAMES_RU.get(module, module)
            status = self.get_status(module)

            # Pad name to align
            name_padded = name_ru.ljust(18)
            status_text = STATUS_DISPLAY.get(status, ("", ""))[1][:8]

            lines.append(f"║ {emoji} {name_padded} ║")

        lines.append("╚══════════════════════════════╝")

        return "\n".join(lines)

    def format_detail_block(self, module: str) -> str:
        """
        Format detailed status for single module (Russian).
        """
        if module not in self._modules:
            return f"❌ Модуль '{module}' не найден"

        emoji = self.get_emoji(module)
        name_ru = MODULE_NAMES_RU.get(module, module)
        status = self.get_status(module)
        tooltip = self.get_tooltip(module)

        lines = [
            f"╔══ {emoji} {name_ru} ══╗",
            f"║ Статус: {tooltip}",
        ]

        last_run = self.get_last_run(module)
        if last_run:
            age_sec = (datetime.utcnow() - last_run).total_seconds()
            if age_sec < 60:
                age_str = f"{int(age_sec)} сек назад"
            elif age_sec < 3600:
                age_str = f"{int(age_sec / 60)} мин назад"
            else:
                age_str = f"{int(age_sec / 3600)} ч назад"
            lines.append(f"║ Последний запуск: {age_str}")

        error_count = self.get_error_count(module)
        if error_count > 0:
            lines.append(f"║ Ошибок подряд: {error_count}")
            last_error = self.get_last_error(module)
            if last_error:
                lines.append(f"║ Последняя: {last_error[:30]}...")

        enabled = "Да" if self.is_enabled(module) else "Нет"
        lines.append(f"║ Включен: {enabled}")
        lines.append("╚" + "═" * (len(lines[0]) - 2) + "╝")

        return "\n".join(lines)


# === Singleton Access ===

def get_status_manager(state_dir: Optional[Path] = None) -> StatusManager:
    """Get or create the global status manager instance."""
    return StatusManager(state_dir)
