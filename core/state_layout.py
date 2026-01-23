# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 13:45:00 UTC
# === END SIGNATURE ===
"""
core/state_layout.py - Unified State Directory Layout.

Single Source of Truth for all state/archive/quarantine/report paths.

ARCHITECTURE:
    All components MUST use this module for state paths.
    No hardcoded `.../state/...` strings allowed elsewhere.

DIRECTORY STRUCTURE:
    state/
    ├── archive/           # Rotated/archived files (immutable)
    │   └── <component>/   # Per-component archives
    │       └── YYYY-MM/   # Monthly subdirs
    ├── audit/             # Audit logs (append-only)
    │   └── io_actions/    # IO security layer audits
    ├── quarantine/        # Invalid/corrupted data (preserved for debugging)
    ├── reports/           # Generated reports (JSON)
    ├── cursors/           # Cursor files for polling
    └── *.jsonl            # Active logs (nexus_history, etc.)

ENVIRONMENT OVERRIDE:
    Set HOPE_STATE_DIR to relocate all state (useful for tests).

USAGE:
    from core.state_layout import StateLayout

    paths = StateLayout()
    paths.state_dir()              # state/
    paths.archive_dir("nexus")     # state/archive/nexus/
    paths.quarantine_dir()         # state/quarantine/
    paths.history_file("nexus")    # state/nexus_history.jsonl
    paths.audit_log("startup")     # state/audit/startup_audit.jsonl
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# Default state root (relative to minibot/)
_THIS_FILE = Path(__file__).resolve()
_CORE_DIR = _THIS_FILE.parent
_MINIBOT_DIR = _CORE_DIR.parent
_DEFAULT_STATE_ROOT = _MINIBOT_DIR / "state"


class StateLayout:
    """
    Unified state directory layout manager.

    All paths are created lazily (mkdir on first access).
    Supports override via HOPE_STATE_DIR environment variable.
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        """
        Initialize state layout.

        Args:
            root: Override state root (default: from env or <minibot>/state)
        """
        if root is not None:
            self._root = Path(root).resolve()
        else:
            env_root = os.environ.get("HOPE_STATE_DIR")
            if env_root:
                self._root = Path(env_root).resolve()
            else:
                self._root = _DEFAULT_STATE_ROOT

    def _ensure_dir(self, path: Path) -> Path:
        """Lazily create directory if it doesn't exist."""
        path.mkdir(parents=True, exist_ok=True)
        return path

    # === Root directories ===

    def state_dir(self) -> Path:
        """Root state directory."""
        return self._ensure_dir(self._root)

    def archive_dir(self, component: Optional[str] = None) -> Path:
        """
        Archive directory for rotated files.

        Args:
            component: Optional component name for subdirectory

        Returns:
            Path to archive root or component-specific archive
        """
        base = self._root / "archive"
        if component:
            # Add monthly subdir
            now = datetime.now(timezone.utc)
            month_dir = f"{now.year:04d}-{now.month:02d}"
            return self._ensure_dir(base / component / month_dir)
        return self._ensure_dir(base)

    def quarantine_dir(self) -> Path:
        """Quarantine directory for invalid/corrupted data."""
        return self._ensure_dir(self._root / "quarantine")

    def reports_dir(self) -> Path:
        """Reports directory for generated JSON reports."""
        return self._ensure_dir(self._root / "reports")

    def audit_dir(self) -> Path:
        """Audit logs directory."""
        return self._ensure_dir(self._root / "audit")

    def cursors_dir(self) -> Path:
        """Cursor files directory for polling state."""
        return self._ensure_dir(self._root / "cursors")

    # === Specific files ===

    def history_file(self, component: str) -> Path:
        """
        History JSONL file for a component.

        Args:
            component: Component name (e.g., "nexus")

        Returns:
            Path like state/nexus_history.jsonl
        """
        self._ensure_dir(self._root)
        return self._root / f"{component}_history.jsonl"

    def audit_log(self, category: str = "general") -> Path:
        """
        Audit log file for a category.

        Args:
            category: Audit category (e.g., "startup", "io", "maintenance")

        Returns:
            Path like state/audit/startup_audit.jsonl
        """
        return self.audit_dir() / f"{category}_audit.jsonl"

    def cursor_file(self, component: str) -> Path:
        """
        Cursor file for a component's polling state.

        Args:
            component: Component name

        Returns:
            Path like state/cursors/orchestrator_cursor.txt
        """
        return self.cursors_dir() / f"{component}_cursor.txt"

    def processed_ids_file(self, component: str) -> Path:
        """
        Processed IDs file for deduplication.

        Args:
            component: Component name

        Returns:
            Path like state/cursors/orchestrator_processed.txt
        """
        return self.cursors_dir() / f"{component}_processed.txt"

    def quarantine_blob(self, source: str, blob_sha256: str) -> Path:
        """
        Generate path for quarantine blob.

        Args:
            source: Source component/function
            blob_sha256: SHA256 of the blob content

        Returns:
            Path like state/quarantine/nexus_poll_a1b2c3d4.blob
        """
        short_sha = blob_sha256[:16] if len(blob_sha256) >= 16 else blob_sha256
        return self.quarantine_dir() / f"{source}_{short_sha}.blob"

    def quarantine_meta(self, source: str, blob_sha256: str) -> Path:
        """
        Generate path for quarantine metadata file.

        Args:
            source: Source component/function
            blob_sha256: SHA256 of the blob content

        Returns:
            Path like state/quarantine/nexus_poll_a1b2c3d4.meta.json
        """
        short_sha = blob_sha256[:16] if len(blob_sha256) >= 16 else blob_sha256
        return self.quarantine_dir() / f"{source}_{short_sha}.meta.json"

    def report_file(self, report_type: str) -> Path:
        """
        Generate timestamped report file path.

        Args:
            report_type: Type of report (e.g., "maintenance", "verify")

        Returns:
            Path like state/reports/maintenance_20260123_134500.json
        """
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y%m%d_%H%M%S")
        return self.reports_dir() / f"{report_type}_{ts}.json"

    # === Archive rotation helpers ===

    def rotated_file(self, original: Path, rotation_index: int) -> Path:
        """
        Generate path for rotated file.

        Args:
            original: Original file path
            rotation_index: Rotation number (1, 2, 3, ...)

        Returns:
            Path like state/nexus_history.jsonl.1
        """
        return original.with_suffix(f"{original.suffix}.{rotation_index}")

    def archived_file(self, component: str, original_name: str) -> Path:
        """
        Generate path for archived file in monthly subdir.

        Args:
            component: Component name
            original_name: Original filename

        Returns:
            Path like state/archive/nexus/2026-01/nexus_history.jsonl.1
        """
        return self.archive_dir(component) / original_name


# === Singleton instance ===

_default_layout: Optional[StateLayout] = None


def get_layout() -> StateLayout:
    """Get the default StateLayout singleton."""
    global _default_layout
    if _default_layout is None:
        _default_layout = StateLayout()
    return _default_layout


def reset_layout(root: Optional[Path] = None) -> StateLayout:
    """
    Reset the default StateLayout (useful for tests).

    Args:
        root: New state root (or None to use default)

    Returns:
        New StateLayout instance
    """
    global _default_layout
    _default_layout = StateLayout(root)
    return _default_layout


# === Convenience functions ===

def state_dir() -> Path:
    """Get state directory (convenience wrapper)."""
    return get_layout().state_dir()


def archive_dir(component: Optional[str] = None) -> Path:
    """Get archive directory (convenience wrapper)."""
    return get_layout().archive_dir(component)


def quarantine_dir() -> Path:
    """Get quarantine directory (convenience wrapper)."""
    return get_layout().quarantine_dir()


def reports_dir() -> Path:
    """Get reports directory (convenience wrapper)."""
    return get_layout().reports_dir()


def audit_dir() -> Path:
    """Get audit directory (convenience wrapper)."""
    return get_layout().audit_dir()


def history_file(component: str) -> Path:
    """Get history file path (convenience wrapper)."""
    return get_layout().history_file(component)


def audit_log(category: str = "general") -> Path:
    """Get audit log path (convenience wrapper)."""
    return get_layout().audit_log(category)
