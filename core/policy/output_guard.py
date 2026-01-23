# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 16:15:00 UTC
# === END SIGNATURE ===
"""
core/policy/output_guard.py - Output Leak Prevention.

HOPE-LAW-001 / HOPE-RULE-001:
    Blocks any output (stdout/stderr/logging) containing:
    - Secret patterns (tokens, keys, credentials)
    - Forbidden promise phrases

FAIL-CLOSED:
    - Secret detected in output -> PolicyViolation (output blocked)
    - Forbidden phrase detected -> PolicyViolation (output blocked)
"""
from __future__ import annotations

import io
import re
import sys
from dataclasses import dataclass
from typing import List, Optional, TextIO, Tuple

from core.policy.loader import Policy


class PolicyViolation(RuntimeError):
    """
    Raised when output contains prohibited content.

    CRITICAL: This exception must NEVER include the actual secret.
    Only pattern name is included for debugging.
    """
    pass


@dataclass(frozen=True)
class GuardConfig:
    """Compiled regex patterns for efficient matching."""
    secret_regexes: Tuple[re.Pattern, ...]
    forbidden_phrase_regexes: Tuple[re.Pattern, ...]


def _compile_patterns(policy: Policy) -> GuardConfig:
    """Compile policy patterns to regex objects."""
    secret = tuple(
        re.compile(p, re.IGNORECASE | re.MULTILINE)
        for p in policy.secret_patterns
    )
    forbidden = tuple(
        re.compile(p, re.IGNORECASE | re.MULTILINE)
        for p in policy.forbidden_phrases
    )
    return GuardConfig(
        secret_regexes=secret,
        forbidden_phrase_regexes=forbidden,
    )


def detect_secret(cfg: GuardConfig, text: str) -> Optional[str]:
    """
    Detect secret patterns in text.

    Args:
        cfg: Compiled guard config
        text: Text to check

    Returns:
        Pattern string if match found, None otherwise
    """
    for rx in cfg.secret_regexes:
        if rx.search(text):
            # Return pattern, NOT the matched content (to avoid leaking)
            return f"<secret_pattern:{rx.pattern[:30]}...>"
    return None


def detect_forbidden_phrase(cfg: GuardConfig, text: str) -> Optional[str]:
    """
    Detect forbidden promise phrases in text.

    Args:
        cfg: Compiled guard config
        text: Text to check

    Returns:
        Pattern string if match found, None otherwise
    """
    for rx in cfg.forbidden_phrase_regexes:
        if rx.search(text):
            return f"<forbidden_phrase:{rx.pattern[:30]}...>"
    return None


class _GuardedWriter(io.TextIOBase):
    """
    Wrapper around stdout/stderr that blocks prohibited output.

    FAIL-CLOSED: Raises PolicyViolation on detection.
    """

    def __init__(self, wrapped: TextIO, cfg: GuardConfig, channel: str):
        self._wrapped = wrapped
        self._cfg = cfg
        self._channel = channel

    def write(self, s: str) -> int:
        if not s:
            return 0

        # Check for secrets
        hit = detect_secret(self._cfg, s)
        if hit:
            raise PolicyViolation(
                f"OUTPUT BLOCKED ({self._channel}): Secret pattern detected. "
                f"Pattern: {hit}. Output suppressed to prevent leak."
            )

        # Check for forbidden phrases
        hit2 = detect_forbidden_phrase(self._cfg, s)
        if hit2:
            raise PolicyViolation(
                f"OUTPUT BLOCKED ({self._channel}): Forbidden phrase detected. "
                f"Pattern: {hit2}. Output suppressed."
            )

        return self._wrapped.write(s)

    def flush(self) -> None:
        return self._wrapped.flush()

    def fileno(self) -> int:
        return self._wrapped.fileno()

    @property
    def encoding(self) -> str:
        return getattr(self._wrapped, "encoding", "utf-8")


class _LoggingGuardFilter:
    """
    Logging filter that blocks prohibited log messages.
    """

    def __init__(self, cfg: GuardConfig):
        self._cfg = cfg

    def filter(self, record) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True  # Let malformed records through

        # Check for secrets
        hit = detect_secret(self._cfg, msg)
        if hit:
            raise PolicyViolation(
                f"LOGGING BLOCKED: Secret pattern detected. "
                f"Pattern: {hit}. Log suppressed."
            )

        # Check for forbidden phrases
        hit2 = detect_forbidden_phrase(self._cfg, msg)
        if hit2:
            raise PolicyViolation(
                f"LOGGING BLOCKED: Forbidden phrase detected. "
                f"Pattern: {hit2}. Log suppressed."
            )

        return True


_INSTALLED = False


def install_output_guards(policy: Policy) -> None:
    """
    Install output guards on stdout/stderr/logging.

    CRITICAL: Must be called BEFORE any logging or print statements.

    Args:
        policy: Loaded policy configuration

    Raises:
        PolicyViolation: If installation fails (fail-closed)
    """
    global _INSTALLED

    if _INSTALLED:
        return  # Idempotent

    cfg = _compile_patterns(policy)

    # Guard stdout
    if policy.enforce_channels.get("stdout", False):
        sys.stdout = _GuardedWriter(sys.__stdout__, cfg, channel="stdout")  # type: ignore

    # Guard stderr
    if policy.enforce_channels.get("stderr", False):
        sys.stderr = _GuardedWriter(sys.__stderr__, cfg, channel="stderr")  # type: ignore

    # Guard logging
    if policy.enforce_channels.get("logging", False):
        try:
            import logging
            root_logger = logging.getLogger()
            root_logger.addFilter(_LoggingGuardFilter(cfg))
        except Exception as e:
            raise PolicyViolation(
                f"Failed to install logging guard (fail-closed): {e}"
            ) from e

    _INSTALLED = True


def is_installed() -> bool:
    """Check if output guards are installed."""
    return _INSTALLED
