# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T19:30:00Z
# Modified by: Claude (opus-4)
# Modified at (UTC): 2026-01-25T20:00:00Z
# Purpose: Spider run_id generation with strict schema (fail-closed) v1.4
# === END SIGNATURE ===
"""
Spider Run ID Module v1.4

Generates unique run_id per spider execution with strict schema.

Format: spider_v1__ts=<UTC>__pid=<PID>__nonce=<NONCE_HEX>__cmd=<CMD_PREFIX8>

Where:
- <UTC>: YYYYMMDDTHHMMSSZ (16 chars)
- <PID>: decimal PID (1-7 digits)
- <NONCE_HEX>: 16 bytes (128 bits) hex lowercase (32 chars)
- <CMD_PREFIX8>: first 8 chars of cmdline SHA256 (SSoT binding)

Example:
    spider_v1__ts=20260125T164500Z__pid=92396__nonce=9f2c0d7e3a6b4c1d__cmd=a1b2c3d4

Invariants:
- Unique per spider process
- Parseable without context
- No unstable sources (WMI, etc.)
- Immutable after generation
- Bound to exact cmdline via __cmd= prefix
"""

import hashlib
import os
import re
import secrets
import sys
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional, Tuple


# Strict regex for run_id validation (with optional __cmd= for backwards compat during transition)
RUN_ID_REGEX = re.compile(
    r'^spider_v1__ts=(\d{8}T\d{6}Z)__pid=([1-9]\d{0,6})__nonce=([0-9a-f]{32})(?:__cmd=([0-9a-f]{8}))?$'
)


class RunIdError(Exception):
    """Raised when run_id is invalid or cannot be generated."""
    pass


@dataclass(frozen=True)
class ParsedRunId:
    """Parsed components of a run_id."""
    version: str
    ts_utc: str
    pid: int
    nonce: str
    raw: str
    cmd_prefix: Optional[str] = None  # First 8 chars of cmdline sha256

    @property
    def timestamp_datetime(self) -> datetime:
        """Parse ts_utc to datetime object."""
        # Format: YYYYMMDDTHHMMSSZ
        return datetime.strptime(self.ts_utc, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


# Module-level singleton: run_id is generated ONCE per process
_current_run_id: Optional[str] = None
_current_cmdline_sha256: Optional[str] = None
_generation_ts: Optional[datetime] = None


def _get_cmdline_sha256() -> str:
    """
    Get SHA256 of raw command line (SSoT).

    Uses GetCommandLineW on Windows, /proc on Linux.
    """
    global _current_cmdline_sha256
    if _current_cmdline_sha256 is not None:
        return _current_cmdline_sha256

    try:
        # Import here to avoid circular imports
        from core.truth.cmdline_ssot import get_cmdline_sha256
        _current_cmdline_sha256 = get_cmdline_sha256()
        return _current_cmdline_sha256
    except Exception:
        # Fallback: hash sys.argv (less reliable but better than nothing)
        cmdline = " ".join(sys.argv)
        _current_cmdline_sha256 = hashlib.sha256(cmdline.encode("utf-8")).hexdigest()
        return _current_cmdline_sha256


def get_cmdline_sha256_cached() -> str:
    """Get cached cmdline SHA256."""
    return _get_cmdline_sha256()


def generate_run_id() -> str:
    """
    Generate a new run_id for this spider execution.

    IMPORTANT: Must be called ONCE at process start, BEFORE any network I/O.
    Subsequent calls return the same run_id (immutable after generation).

    Format: spider_v1__ts=<UTC>__pid=<PID>__nonce=<NONCE>__cmd=<CMD_PREFIX8>

    Returns:
        Valid run_id string

    Raises:
        RunIdError: If generation fails (should never happen)
    """
    global _current_run_id, _generation_ts

    if _current_run_id is not None:
        return _current_run_id

    try:
        # Get components
        now = datetime.now(timezone.utc)
        ts_str = now.strftime("%Y%m%dT%H%M%SZ")
        pid = os.getpid()
        nonce = secrets.token_bytes(16).hex()  # 32 hex chars, lowercase
        cmd_prefix = _get_cmdline_sha256()[:8]  # First 8 chars for SSoT binding

        # Format run_id with __cmd= suffix
        run_id = f"spider_v1__ts={ts_str}__pid={pid}__nonce={nonce}__cmd={cmd_prefix}"

        # Validate immediately (fail-closed)
        if not validate_run_id(run_id):
            raise RunIdError(f"Generated run_id failed validation: {run_id}")

        _current_run_id = run_id
        _generation_ts = now

        return run_id

    except Exception as e:
        raise RunIdError(f"Failed to generate run_id: {e}")


def get_current_run_id() -> Optional[str]:
    """
    Get current run_id without generating a new one.

    Returns:
        Current run_id or None if not yet generated
    """
    return _current_run_id


def validate_run_id(run_id: str) -> bool:
    """
    Validate run_id against strict schema.

    Args:
        run_id: String to validate

    Returns:
        True if valid, False otherwise
    """
    if not run_id:
        return False

    match = RUN_ID_REGEX.match(run_id)
    return match is not None


def parse_run_id(run_id: str) -> ParsedRunId:
    """
    Parse run_id into components.

    Args:
        run_id: Valid run_id string

    Returns:
        ParsedRunId with extracted components

    Raises:
        RunIdError: If run_id is invalid
    """
    if not run_id:
        raise RunIdError("Empty run_id")

    match = RUN_ID_REGEX.match(run_id)
    if not match:
        raise RunIdError(f"Invalid run_id format: {run_id}")

    ts_utc = match.group(1)
    pid = int(match.group(2))
    nonce = match.group(3)
    cmd_prefix = match.group(4)  # May be None for old run_ids

    return ParsedRunId(
        version="v1",
        ts_utc=ts_utc,
        pid=pid,
        nonce=nonce,
        raw=run_id,
        cmd_prefix=cmd_prefix,
    )


def reset_run_id() -> None:
    """
    Reset run_id singleton (FOR TESTING ONLY).

    WARNING: Do not call in production - run_id must be immutable.
    """
    global _current_run_id, _current_cmdline_sha256, _generation_ts
    _current_run_id = None
    _current_cmdline_sha256 = None
    _generation_ts = None
