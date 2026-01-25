# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T12:30:00Z
# Purpose: Egress Audit Log - JSONL append-only with lock+fsync (stdlib-only)
# === END SIGNATURE ===
"""
Egress Audit Log Module (stdlib-only, Windows-safe)

All egress attempts (ALLOW/DENY) are logged to JSONL.
- Append-only
- Exclusive lock during write (Windows: msvcrt.locking)
- fsync after write
- No secrets in logs (URL hashed, no headers/query/payload)

Default path: staging/history/egress_audit.jsonl
"""

import json
import os
import hashlib
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, Any

# Windows-safe file locking
try:
    import msvcrt
    _HAS_MSVCRT = True
except ImportError:
    _HAS_MSVCRT = False

try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False


class AuditAction(str, Enum):
    """Egress audit action type."""
    ALLOW = "ALLOW"
    DENY = "DENY"


class AuditReason(str, Enum):
    """
    Standardized deny/allow reasons.

    Keep this enum fixed - changes require version bump.
    """
    # ALLOW reasons
    HOST_IN_ALLOWLIST = "host_in_allowlist"

    # DENY reasons
    HOST_NOT_IN_ALLOWLIST = "host_not_in_allowlist"
    REDIRECT_TO_DIFFERENT_HOST = "redirect_to_different_host"
    POLICY_LOAD_FAILED = "policy_load_failed"
    INVALID_URL = "invalid_url"
    MISSING_HOST = "missing_host"
    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"
    RESPONSE_TOO_LARGE = "response_too_large"

    # Special
    UNKNOWN = "unknown"


def _get_default_audit_path() -> Path:
    """
    Get default audit log path.

    Returns:
        Path to staging/history/egress_audit.jsonl
    """
    # Find repo root
    current = Path(__file__).resolve()
    repo_root = None

    for parent in [current] + list(current.parents):
        if (parent / ".git").exists() or (parent / "AllowList.txt").exists():
            repo_root = parent
            break

    if repo_root is None:
        # Fallback to current working directory
        repo_root = Path.cwd()

    return repo_root / "staging" / "history" / "egress_audit.jsonl"


def _hash_url(url: str) -> str:
    """
    Hash URL for audit (no secrets in logs).

    Args:
        url: Full URL (may contain sensitive querystring)

    Returns:
        SHA256 hash of URL (first 16 chars)
    """
    return hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]


def _lock_file(f) -> None:
    """
    Acquire exclusive lock on file (cross-platform).

    Args:
        f: File object opened for writing
    """
    if _HAS_MSVCRT:
        # Windows: lock entire file
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    elif _HAS_FCNTL:
        # Unix: advisory lock
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(f) -> None:
    """
    Release exclusive lock on file (cross-platform).

    Args:
        f: File object
    """
    if _HAS_MSVCRT:
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except Exception:
            pass  # Ignore unlock errors
    elif _HAS_FCNTL:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass


def append_audit_record(
    action: AuditAction,
    host: str,
    reason: AuditReason,
    url: str = "",
    latency_ms: int = 0,
    process: str = "unknown",
    notes: Optional[str] = None,
    audit_path: Optional[Path] = None,
) -> str:
    """
    Append audit record to JSONL log (atomic, locked, fsynced).

    Args:
        action: ALLOW or DENY
        host: Target hostname
        reason: Standardized reason code
        url: Full URL (will be hashed, not stored)
        latency_ms: Request latency in milliseconds
        process: Entrypoint/process name
        notes: Optional notes (NO SECRETS)
        audit_path: Custom audit log path (default: staging/history/egress_audit.jsonl)

    Returns:
        request_id (UUID) for this record

    Raises:
        IOError: If write fails
    """
    if audit_path is None:
        audit_path = _get_default_audit_path()

    audit_path = Path(audit_path)

    # Ensure directory exists
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    # Generate record
    request_id = str(uuid.uuid4())
    ts_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

    record = {
        "ts_utc": ts_utc,
        "request_id": request_id,
        "process": process,
        "action": action.value if isinstance(action, AuditAction) else str(action),
        "host": host,
        "reason": reason.value if isinstance(reason, AuditReason) else str(reason),
        "latency_ms": latency_ms,
        "url_sha256": _hash_url(url) if url else "",
    }

    if notes:
        # Sanitize notes - remove anything that looks like a secret
        sanitized = notes[:200]  # Max length
        if any(s in sanitized.lower() for s in ['key=', 'secret=', 'token=', 'password=']):
            sanitized = "[REDACTED]"
        record["notes"] = sanitized

    # Serialize to JSON line
    line = json.dumps(record, ensure_ascii=True, separators=(',', ':')) + '\n'

    # Atomic append with lock and fsync
    retries = 3
    last_error = None

    for attempt in range(retries):
        try:
            with open(audit_path, 'a', encoding='utf-8', newline='\n') as f:
                try:
                    _lock_file(f)
                    f.write(line)
                    f.flush()
                    os.fsync(f.fileno())
                finally:
                    _unlock_file(f)
            return request_id
        except (IOError, OSError, BlockingIOError) as e:
            last_error = e
            # Brief sleep before retry (Windows lock contention)
            import time
            time.sleep(0.01 * (attempt + 1))

    # All retries failed
    raise IOError(f"Failed to write audit record after {retries} attempts: {last_error}")


def read_audit_log(
    audit_path: Optional[Path] = None,
    last_n: int = 100,
) -> list:
    """
    Read last N records from audit log.

    Args:
        audit_path: Custom path (default: staging/history/egress_audit.jsonl)
        last_n: Number of records to return

    Returns:
        List of record dicts (most recent last)
    """
    if audit_path is None:
        audit_path = _get_default_audit_path()

    audit_path = Path(audit_path)

    if not audit_path.exists():
        return []

    records = []
    try:
        with open(audit_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except Exception:
        return []

    # Return last N
    return records[-last_n:]


def get_audit_stats(audit_path: Optional[Path] = None) -> dict:
    """
    Get summary statistics from audit log.

    Returns:
        Dict with counts of ALLOW/DENY, top denied hosts, etc.
    """
    records = read_audit_log(audit_path, last_n=10000)

    allow_count = sum(1 for r in records if r.get('action') == 'ALLOW')
    deny_count = sum(1 for r in records if r.get('action') == 'DENY')

    # Top denied hosts
    denied_hosts: dict = {}
    for r in records:
        if r.get('action') == 'DENY':
            host = r.get('host', 'unknown')
            denied_hosts[host] = denied_hosts.get(host, 0) + 1

    top_denied = sorted(denied_hosts.items(), key=lambda x: -x[1])[:10]

    return {
        "total_records": len(records),
        "allow_count": allow_count,
        "deny_count": deny_count,
        "top_denied_hosts": top_denied,
    }
