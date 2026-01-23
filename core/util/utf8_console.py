# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 21:37:00 UTC
# === END SIGNATURE ===
"""
UTF-8 console setup for Windows.

MUST be imported and called BEFORE any other imports that may output text.
"""
from __future__ import annotations

import io
import os
import sys


def setup_utf8_console() -> None:
    """
    Best-effort UTF-8 for Windows console + for subprocesses.
    Must be called before logging/output.
    """
    if sys.platform != "win32":
        return

    # Force UTF-8 mode for Python + propagate to subprocesses
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        try:
            sys.stdout = io.TextIOWrapper(
                sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
            )
            sys.stderr = io.TextIOWrapper(
                sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
            )
        except Exception:
            pass
