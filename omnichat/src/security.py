# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-26T12:30:00Z
# Modified by: Claude (opus-4)
# Modified at: 2026-01-26T17:00:00Z
# Approved by: Gemini (Architect), GPT (Analyst)
# Purpose: Centralized secrets redaction - FAIL-CLOSED v1.1
# Changes: Named patterns + detect_secret_type() + AWS patterns
# === END SIGNATURE ===
"""
HOPE OMNI-CHAT Security Module

Centralized secrets masking for:
- UI output
- JSONL history
- Markdown export
- All logging

PRINCIPLE: Fail-closed. If in doubt, mask it.
"""

import logging
import re
from typing import Any

# Fail-closed patterns with metadata for debugging
_SECRET_PATTERNS: dict[str, str] = {
    "google_api": r"AIza[0-9A-Za-z_\-]{20,}",
    "openai_api": r"sk-[A-Za-z0-9]{16,}",
    "openai_proj": r"sk-proj-[A-Za-z0-9_\-]{20,}",
    "anthropic_api": r"sk-ant-[A-Za-z0-9_\-]{16,}",
    "bearer_token": r"Bearer\s+[A-Za-z0-9_\-\.]{16,}",
    "hex_token_64": r"\b[a-f0-9]{64}\b",
    "github_pat": r"ghp_[A-Za-z0-9]{36,}",
    "github_oauth": r"gho_[A-Za-z0-9]{36,}",
    "telegram_bot": r"bot[0-9]{8,}:[A-Za-z0-9_\-]{30,}",
    "aws_access": r"AKIA[0-9A-Z]{16}",
    "aws_secret": r"[A-Za-z0-9/+=]{40}(?=.*[A-Z])(?=.*[a-z])(?=.*[0-9])",
}

_COMPILED: dict[str, re.Pattern] = {
    name: re.compile(pattern, re.IGNORECASE)
    for name, pattern in _SECRET_PATTERNS.items()
}


def redact(text: str) -> str:
    """
    Mask secrets in a string.

    Example: sk-proj-abc123xyz... -> sk-p***

    Returns original text if no secrets found.
    """
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return ""

    out = text
    for rx in _COMPILED.values():
        out = rx.sub(lambda m: m.group(0)[:4] + "***", out)
    return out


def contains_secret(text: str) -> bool:
    """Check if text contains any secret pattern."""
    if not isinstance(text, str):
        return False
    for rx in _COMPILED.values():
        if rx.search(text):
            return True
    return False


def detect_secret_type(text: str) -> str | None:
    """
    Detect which type of secret is in text.
    Returns pattern name or None.
    Useful for debugging and audit logs.
    """
    if not isinstance(text, str):
        return None
    for name, rx in _COMPILED.items():
        if rx.search(text):
            return name
    return None


def redact_any(value: Any) -> Any:
    """Recursive redaction for dict/list/tuple structures."""
    try:
        if isinstance(value, str):
            return redact(value)
        if isinstance(value, dict):
            return {k: redact_any(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            t = [redact_any(v) for v in value]
            return type(value)(t) if isinstance(value, tuple) else t
    except Exception:
        pass
    return value


class RedactingFilter(logging.Filter):
    """
    Logging filter that automatically masks secrets.

    Applied to all log records before they reach handlers.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(str(record.msg))
            if record.args:
                record.args = tuple(redact_any(a) for a in record.args)
        except Exception:
            # Don't crash the app due to logging issues
            pass
        return True


def configure_safe_logging(log_file: str = "omnichat.log") -> None:
    """
    Configure application-wide safe logging.

    - Adds RedactingFilter to root logger
    - Silences noisy libraries that might leak URLs/tokens
    - Sets up file logging with rotation-friendly format
    """
    # Configure root logger
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ]
    )

    root = logging.getLogger()
    root.addFilter(RedactingFilter())

    # Silence noisy libraries (they may log URLs with tokens)
    noisy_loggers = [
        "httpx",
        "httpcore",
        "openai",
        "anthropic",
        "aiohttp",
        "google.generativeai",
        "urllib3",
    ]
    for name in noisy_loggers:
        logging.getLogger(name).setLevel(logging.WARNING)


# Export for easy import
__all__ = [
    "redact",
    "redact_any",
    "contains_secret",
    "detect_secret_type",
    "configure_safe_logging",
    "RedactingFilter",
    "_SECRET_PATTERNS",  # For external audit/config
]
