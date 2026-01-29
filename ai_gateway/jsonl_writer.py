# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 03:38:00 UTC
# Purpose: Atomic JSONL writer for AI artifacts with rotation
# === END SIGNATURE ===
"""
AI-Gateway JSONL Writer: Atomic writes with rotation and cleanup.

Writes artifacts to state/ai/*.jsonl for Core consumption.
Core reads these files without importing AI libraries.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .contracts import BaseArtifact

logger = logging.getLogger(__name__)


# Default configuration
DEFAULT_STATE_DIR = Path("state/ai")
MAX_FILE_SIZE_MB = 10
MAX_FILE_AGE_DAYS = 7
ROTATION_CHECK_INTERVAL = 3600  # 1 hour


class JSONLWriter:
    """
    Atomic JSONL writer for AI artifacts.

    Features:
    - Atomic writes (temp -> fsync -> replace for append)
    - Automatic file rotation by size
    - Old file cleanup
    - Checksum validation before write
    """

    def __init__(
        self,
        state_dir: Optional[Path] = None,
        max_file_size_mb: float = MAX_FILE_SIZE_MB,
        max_file_age_days: int = MAX_FILE_AGE_DAYS,
    ):
        self._state_dir = state_dir or DEFAULT_STATE_DIR
        self._max_file_size = max_file_size_mb * 1024 * 1024  # Convert to bytes
        self._max_file_age = max_file_age_days * 24 * 3600  # Convert to seconds
        self._last_rotation_check = 0.0

        # Ensure directory exists
        self._state_dir.mkdir(parents=True, exist_ok=True)

    def _get_artifact_file(self, module: str) -> Path:
        """Get path for module's artifact file."""
        return self._state_dir / f"{module}.jsonl"

    def _get_rotated_file(self, module: str) -> Path:
        """Get path for rotated file with timestamp."""
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        return self._state_dir / f"{module}_{ts}.jsonl"

    def _needs_rotation(self, file_path: Path) -> bool:
        """Check if file needs rotation based on size."""
        if not file_path.exists():
            return False
        return file_path.stat().st_size > self._max_file_size

    def _rotate_file(self, module: str) -> None:
        """Rotate artifact file if needed."""
        current_file = self._get_artifact_file(module)
        if not self._needs_rotation(current_file):
            return

        rotated_file = self._get_rotated_file(module)
        try:
            os.rename(current_file, rotated_file)
            logger.info(f"Rotated {current_file} -> {rotated_file}")
        except Exception as e:
            logger.error(f"Failed to rotate {current_file}: {e}")

    def _cleanup_old_files(self) -> None:
        """Remove old rotated files beyond retention period."""
        now = time.time()

        # Only check periodically
        if now - self._last_rotation_check < ROTATION_CHECK_INTERVAL:
            return
        self._last_rotation_check = now

        try:
            for file_path in self._state_dir.glob("*_*.jsonl"):
                # Skip current files (no timestamp in name)
                if file_path.stat().st_mtime < now - self._max_file_age:
                    file_path.unlink()
                    logger.info(f"Cleaned up old file: {file_path}")
        except Exception as e:
            logger.warning(f"Cleanup error: {e}")

    def write_artifact(self, artifact: BaseArtifact) -> bool:
        """
        Write artifact to JSONL file atomically.

        Args:
            artifact: Pydantic artifact model to write

        Returns:
            True if write succeeded, False otherwise

        FAIL-CLOSED: Invalid artifact → False (no write)
        INVARIANT: Written artifacts always have valid checksum
        """
        module = getattr(artifact, "module", "unknown")

        # FAIL-CLOSED: TTL must be positive
        ttl = getattr(artifact, "ttl_seconds", 0)
        if ttl <= 0:
            logger.error(f"Invalid TTL ({ttl}) for {module} - REJECTED")
            return False

        # Ensure checksum is computed
        if not artifact.checksum:
            artifact = artifact.with_checksum()

        # FAIL-CLOSED: Checksum must be valid before write
        if not artifact.is_valid():
            logger.error(f"Artifact checksum mismatch for {module} - REJECTED")
            return False

        # Check rotation
        self._rotate_file(module)
        self._cleanup_old_files()

        # Prepare JSON line
        try:
            json_line = artifact.json(ensure_ascii=False) + "\n"
        except Exception as e:
            logger.error(f"Failed to serialize artifact: {e}")
            return False

        # Atomic append
        file_path = self._get_artifact_file(module)
        return self._atomic_append(file_path, json_line)

    def _atomic_append(self, file_path: Path, content: str) -> bool:
        """
        Atomically append content to file.

        Uses read-modify-write pattern with temp file for safety.
        """
        try:
            # Read existing content
            existing = ""
            if file_path.exists():
                existing = file_path.read_text(encoding="utf-8")

            # Write to temp file
            tmp_path = file_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(existing)
                f.write(content)
                f.flush()
                os.fsync(f.fileno())

            # Atomic replace
            os.replace(tmp_path, file_path)
            return True

        except Exception as e:
            logger.error(f"Atomic append failed for {file_path}: {e}")
            # Cleanup temp file if exists
            try:
                tmp_path = file_path.with_suffix(".tmp")
                if tmp_path.exists():
                    tmp_path.unlink()
            except:
                pass
            return False

    def write_line(self, module: str, data: Dict[str, Any]) -> bool:
        """
        Write raw dict as JSONL line (for non-artifact data).

        Args:
            module: Module name (determines file)
            data: Dict to write as JSON line

        Returns:
            True if write succeeded
        """
        # Add timestamp if not present
        if "timestamp" not in data:
            data["timestamp"] = datetime.utcnow().isoformat() + "Z"

        try:
            json_line = json.dumps(data, ensure_ascii=False, default=str) + "\n"
        except Exception as e:
            logger.error(f"Failed to serialize data: {e}")
            return False

        file_path = self._get_artifact_file(module)
        return self._atomic_append(file_path, json_line)

    def read_latest(self, module: str, count: int = 1) -> List[Dict[str, Any]]:
        """
        Read latest N artifacts from module file.

        Args:
            module: Module name
            count: Number of recent artifacts to return

        Returns:
            List of artifact dicts (newest first)
        """
        file_path = self._get_artifact_file(module)
        if not file_path.exists():
            return []

        try:
            lines = file_path.read_text(encoding="utf-8").strip().split("\n")
            lines = [l for l in lines if l.strip()]  # Filter empty

            # Get last N lines
            recent_lines = lines[-count:] if count < len(lines) else lines
            recent_lines.reverse()  # Newest first

            results = []
            for line in recent_lines:
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

            return results

        except Exception as e:
            logger.error(f"Failed to read {file_path}: {e}")
            return []

    def read_valid_artifact(self, module: str) -> Optional[Dict[str, Any]]:
        """
        Read latest valid (not expired) artifact.

        Returns:
            Artifact dict if valid and not expired, None otherwise
        """
        artifacts = self.read_latest(module, count=1)
        if not artifacts:
            return None

        artifact = artifacts[0]

        # Check TTL
        created_at = artifact.get("created_at", "")
        ttl_seconds = artifact.get("ttl_seconds", 300)

        try:
            if created_at.endswith("Z"):
                created_at = created_at[:-1]
            created_dt = datetime.fromisoformat(created_at)
            age = (datetime.utcnow() - created_dt).total_seconds()

            if age > ttl_seconds:
                logger.debug(f"Artifact {module} expired (age={age:.0f}s, ttl={ttl_seconds}s)")
                return None

        except Exception as e:
            logger.warning(f"Failed to parse artifact timestamp: {e}")
            return None

        return artifact

    def get_file_stats(self, module: str) -> Dict[str, Any]:
        """Get statistics for module's artifact file."""
        file_path = self._get_artifact_file(module)

        if not file_path.exists():
            return {
                "exists": False,
                "path": str(file_path),
            }

        stat = file_path.stat()
        return {
            "exists": True,
            "path": str(file_path),
            "size_bytes": stat.st_size,
            "size_mb": round(stat.st_size / 1024 / 1024, 2),
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat() + "Z",
            "line_count": sum(1 for _ in open(file_path, encoding="utf-8")),
        }


# === Module-level convenience functions ===

_default_writer: Optional[JSONLWriter] = None


def get_writer(state_dir: Optional[Path] = None) -> JSONLWriter:
    """Get or create default JSONL writer."""
    global _default_writer
    if _default_writer is None:
        _default_writer = JSONLWriter(state_dir)
    return _default_writer


def write_artifact(artifact: BaseArtifact) -> bool:
    """Write artifact using default writer."""
    return get_writer().write_artifact(artifact)


def read_latest(module: str, count: int = 1) -> List[Dict[str, Any]]:
    """Read latest artifacts using default writer."""
    return get_writer().read_latest(module, count)


def read_valid(module: str) -> Optional[Dict[str, Any]]:
    """Read latest valid artifact using default writer."""
    return get_writer().read_valid_artifact(module)
