# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-01-24 21:15:00 UTC
# Purpose: SecureIO layer with audit trail - enforces APPEND-ONLY for .env
# === END SIGNATURE ===
"""
SecureIO: Единственный шлюз для файловых операций AI.

Контракт:
- Все AI операции с файлами ДОЛЖНЫ идти через SecureIO
- .env файлы: ТОЛЬКО append (write/replace запрещены)
- Удаление: quarantine вместо delete
- Все операции логируются в JSONL аудит

Использование:
    from core.io_security import SecureIO, AIActor

    io = SecureIO(AIActor("session_id", "Claude", "opus-4.5"))
    io.write_text(Path("file.py"), content, reason="initial creation")
    io.append_env("NEW_KEY", "value", reason="add API key")
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Literal

__all__ = ["SecureIO", "AIActor", "IOViolation"]


# === PROTECTED PATHS ===
_ENV_PATTERNS = [".env", "*.env", ".env.*"]
_SECRETS_ROOT = Path("C:/secrets/hope")


@dataclass(frozen=True)
class AIActor:
    """Identity of the AI performing operations."""
    session_id: str
    name: str  # e.g., "Claude"
    model: str  # e.g., "opus-4.5"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditEntry:
    """Single audit log entry."""
    timestamp_utc: str
    timestamp_unix: float
    actor: dict
    operation: str  # write, append, delete, quarantine, read
    path: str
    reason: str
    success: bool
    error: Optional[str] = None
    content_sha256: Optional[str] = None
    old_sha256: Optional[str] = None
    quarantine_path: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


class IOViolation(Exception):
    """Raised when an operation violates security policy."""
    pass


class SecureIO:
    """
    Secure file I/O wrapper with audit trail.

    All AI file operations MUST go through this class.
    """

    def __init__(
        self,
        actor: AIActor,
        state_dir: Optional[Path] = None,
        secrets_root: Optional[Path] = None,
    ):
        self.actor = actor
        self.state_dir = state_dir or Path("state")
        self.secrets_root = secrets_root or _SECRETS_ROOT

        # Ensure audit directory exists
        self.audit_dir = self.state_dir / "audit" / "io_actions"
        self.audit_dir.mkdir(parents=True, exist_ok=True)

        # Quarantine directory for "deleted" files
        self.quarantine_dir = self.state_dir / "quarantine"
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)

        # Violations log
        self.violations_log = self.state_dir / "guard_violations.log"

    def _now_utc(self) -> tuple[str, float]:
        """Return (ISO string, unix timestamp)."""
        now = datetime.now(timezone.utc)
        return now.strftime("%Y-%m-%dT%H:%M:%SZ"), now.timestamp()

    def _sha256(self, content: str | bytes) -> str:
        """Compute SHA256 hash."""
        if isinstance(content, str):
            content = content.encode("utf-8")
        return hashlib.sha256(content).hexdigest()

    def _is_env_file(self, path: Path) -> bool:
        """Check if path is a protected .env file."""
        name = path.name.lower()
        # Check if in secrets root
        try:
            path.resolve().relative_to(self.secrets_root.resolve())
            in_secrets = True
        except ValueError:
            in_secrets = False

        # .env patterns
        is_env = (
            name == ".env" or
            name.endswith(".env") or
            name.startswith(".env.")
        )

        return is_env and in_secrets

    def _audit_log(self, entry: AuditEntry) -> None:
        """Append entry to audit log (atomic)."""
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        log_path = self.audit_dir / f"audit_{today}.jsonl"

        line = json.dumps(entry.to_dict(), ensure_ascii=False) + "\n"

        # Atomic append
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def _log_violation(self, operation: str, path: Path, reason: str) -> None:
        """Log security violation."""
        utc_str, unix_ts = self._now_utc()
        entry = {
            "timestamp_utc": utc_str,
            "timestamp_unix": unix_ts,
            "actor": self.actor.to_dict(),
            "operation": operation,
            "path": str(path),
            "violation": reason,
        }

        line = json.dumps(entry, ensure_ascii=False) + "\n"

        with open(self.violations_log, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def _atomic_write(self, path: Path, content: str) -> None:
        """Atomic write: temp -> fsync -> replace."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")

        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp, path)

    def write_text(
        self,
        path: Path,
        content: str,
        reason: str,
    ) -> tuple[str, Optional[Path]]:
        """
        Write text file with audit trail.

        BLOCKED for .env files in secrets root - use append_env() instead.

        Returns:
            (content_sha256, meta_path or None)
        """
        path = Path(path).resolve()
        utc_str, unix_ts = self._now_utc()

        # BLOCK: .env files cannot be written, only appended
        if self._is_env_file(path):
            self._log_violation("write", path, "BLOCKED: .env files are APPEND-ONLY")
            raise IOViolation(
                f"BLOCKED: Cannot write to .env file '{path}'. "
                "Use append_env() for adding new keys. "
                "Modification of existing content is FORBIDDEN."
            )

        # Compute hashes
        old_sha = None
        if path.exists():
            try:
                old_sha = self._sha256(path.read_text(encoding="utf-8"))
            except Exception:
                pass

        new_sha = self._sha256(content)

        # Write file
        try:
            self._atomic_write(path, content)
            success = True
            error = None
        except Exception as e:
            success = False
            error = str(e)
            raise
        finally:
            # Audit log
            entry = AuditEntry(
                timestamp_utc=utc_str,
                timestamp_unix=unix_ts,
                actor=self.actor.to_dict(),
                operation="write",
                path=str(path),
                reason=reason,
                success=success,
                error=error,
                content_sha256=new_sha if success else None,
                old_sha256=old_sha,
            )
            self._audit_log(entry)

        # Create sidecar metadata
        meta_path = self._write_sidecar_meta(path, new_sha, reason)

        return new_sha, meta_path

    def _write_sidecar_meta(
        self,
        path: Path,
        content_sha256: str,
        reason: str,
    ) -> Path:
        """Create/update sidecar .ai.meta.json file."""
        meta_path = path.with_suffix(path.suffix + ".ai.meta.json")
        utc_str, unix_ts = self._now_utc()

        # Load existing or create new
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        else:
            meta = {
                "created_by": self.actor.name,
                "created_by_model": self.actor.model,
                "created_by_session": self.actor.session_id,
                "created_at": utc_str,
            }

        # Update edit history
        if "edit_history" not in meta:
            meta["edit_history"] = []

        meta["edit_history"].append({
            "timestamp": utc_str,
            "actor": self.actor.name,
            "model": self.actor.model,
            "session": self.actor.session_id,
            "reason": reason,
            "content_sha256": content_sha256,
        })

        meta["last_modified"] = utc_str
        meta["content_sha256"] = content_sha256

        # Write meta (not through SecureIO to avoid recursion)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())

        return meta_path

    def append_env(
        self,
        key: str,
        value: str,
        reason: str,
        env_path: Optional[Path] = None,
    ) -> str:
        """
        APPEND-ONLY: Add new key to .env file.

        BLOCKED if key already exists.

        Returns:
            New file SHA256
        """
        env_path = Path(env_path or (self.secrets_root / ".env")).resolve()
        utc_str, unix_ts = self._now_utc()

        # Read current content
        if env_path.exists():
            current = env_path.read_text(encoding="utf-8")
            old_sha = self._sha256(current)
        else:
            current = ""
            old_sha = None

        # BLOCK: Key already exists
        if f"{key}=" in current:
            self._log_violation(
                "append_env",
                env_path,
                f"BLOCKED: Key '{key}' already exists. Cannot modify."
            )
            raise IOViolation(
                f"BLOCKED: Key '{key}' already exists in {env_path}. "
                "Modification of existing keys is FORBIDDEN. "
                "If you need a new value, use a different key name (e.g., KEY_V2)."
            )

        # Append new line
        try:
            with open(env_path, "a", encoding="utf-8") as f:
                if current and not current.endswith("\n"):
                    f.write("\n")
                f.write(f"{key}={value}\n")
                f.flush()
                os.fsync(f.fileno())

            new_content = env_path.read_text(encoding="utf-8")
            new_sha = self._sha256(new_content)
            success = True
            error = None
        except Exception as e:
            success = False
            error = str(e)
            new_sha = None
            raise
        finally:
            # Audit log
            entry = AuditEntry(
                timestamp_utc=utc_str,
                timestamp_unix=unix_ts,
                actor=self.actor.to_dict(),
                operation="append_env",
                path=str(env_path),
                reason=f"Added key: {key}. {reason}",
                success=success,
                error=error,
                content_sha256=new_sha,
                old_sha256=old_sha,
            )
            self._audit_log(entry)

        return new_sha

    def delete(
        self,
        path: Path,
        reason: str,
    ) -> tuple[bool, Optional[Path]]:
        """
        Delete = QUARANTINE (not actual delete).

        Files are moved to quarantine directory, not removed.

        Returns:
            (success, quarantine_path)
        """
        path = Path(path).resolve()
        utc_str, unix_ts = self._now_utc()

        # BLOCK: .env files cannot be deleted
        if self._is_env_file(path):
            self._log_violation("delete", path, "BLOCKED: .env files cannot be deleted")
            raise IOViolation(
                f"BLOCKED: Cannot delete .env file '{path}'. "
                ".env files are protected and cannot be removed."
            )

        if not path.exists():
            return False, None

        # Compute hash before quarantine
        try:
            old_sha = self._sha256(path.read_bytes())
        except Exception:
            old_sha = None

        # Create quarantine path
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        quarantine_subdir = self.quarantine_dir / timestamp
        quarantine_subdir.mkdir(parents=True, exist_ok=True)
        quarantine_path = quarantine_subdir / path.name

        # Move to quarantine
        try:
            shutil.move(str(path), str(quarantine_path))

            # Also move sidecar meta if exists
            meta_path = path.with_suffix(path.suffix + ".ai.meta.json")
            if meta_path.exists():
                shutil.move(str(meta_path), str(quarantine_subdir / meta_path.name))

            success = True
            error = None
        except Exception as e:
            success = False
            error = str(e)
            quarantine_path = None
            raise
        finally:
            # Audit log
            entry = AuditEntry(
                timestamp_utc=utc_str,
                timestamp_unix=unix_ts,
                actor=self.actor.to_dict(),
                operation="quarantine",
                path=str(path),
                reason=reason,
                success=success,
                error=error,
                old_sha256=old_sha,
                quarantine_path=str(quarantine_path) if quarantine_path else None,
            )
            self._audit_log(entry)

        return success, quarantine_path

    def read_text(
        self,
        path: Path,
        reason: str = "read",
        log: bool = True,
    ) -> str:
        """
        Read text file with optional audit logging.

        Set log=False for high-frequency reads to reduce noise.
        """
        path = Path(path).resolve()
        utc_str, unix_ts = self._now_utc()

        try:
            content = path.read_text(encoding="utf-8")
            sha = self._sha256(content)
            success = True
            error = None
        except Exception as e:
            success = False
            error = str(e)
            content = None
            sha = None
            raise
        finally:
            if log:
                entry = AuditEntry(
                    timestamp_utc=utc_str,
                    timestamp_unix=unix_ts,
                    actor=self.actor.to_dict(),
                    operation="read",
                    path=str(path),
                    reason=reason,
                    success=success,
                    error=error,
                    content_sha256=sha,
                )
                self._audit_log(entry)

        return content

    def check_env_integrity(
        self,
        expected_keys: list[str],
        env_path: Optional[Path] = None,
    ) -> dict:
        """
        Check .env file integrity.

        Returns:
            {
                "ok": bool,
                "path": str,
                "sha256": str,
                "keys_present": list[str],
                "keys_missing": list[str],
            }
        """
        env_path = Path(env_path or (self.secrets_root / ".env")).resolve()

        if not env_path.exists():
            return {
                "ok": False,
                "path": str(env_path),
                "sha256": None,
                "keys_present": [],
                "keys_missing": expected_keys,
                "error": "File not found",
            }

        content = env_path.read_text(encoding="utf-8")
        sha = self._sha256(content)

        keys_present = []
        keys_missing = []

        for key in expected_keys:
            if f"{key}=" in content:
                keys_present.append(key)
            else:
                keys_missing.append(key)

        return {
            "ok": len(keys_missing) == 0,
            "path": str(env_path),
            "sha256": sha,
            "keys_present": keys_present,
            "keys_missing": keys_missing,
        }


# === SINGLETON FOR CONVENIENCE ===
_default_io: Optional[SecureIO] = None


def get_secure_io(actor: Optional[AIActor] = None) -> SecureIO:
    """Get or create default SecureIO instance."""
    global _default_io

    if _default_io is None:
        if actor is None:
            actor = AIActor("default", "Unknown", "unknown")
        _default_io = SecureIO(actor)

    return _default_io
