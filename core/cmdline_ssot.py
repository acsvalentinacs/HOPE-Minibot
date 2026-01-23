# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 16:00:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 16:00:00 UTC
# === END SIGNATURE ===
"""
Cmdline SSoT (Single Source of Truth) - Windows GetCommandLineW wrapper.

This is the ONLY allowed source for command line hashing/ID generation.
DO NOT use sys.argv for hashing - it's lossy and platform-dependent.

Usage:
    from core.cmdline_ssot import get_cmdline_sha256
    cmdline_hash = get_cmdline_sha256()
    # Returns: "sha256:<64hex>"
"""
from __future__ import annotations

import hashlib
import sys


def _get_raw_cmdline() -> str:
    """
    Get raw command line from OS.

    On Windows: uses GetCommandLineW via ctypes
    On other platforms: falls back to reconstructed sys.argv
    """
    if sys.platform == "win32":
        try:
            import ctypes
            GetCommandLineW = ctypes.windll.kernel32.GetCommandLineW
            GetCommandLineW.restype = ctypes.c_wchar_p
            return GetCommandLineW() or ""
        except Exception:
            pass

    # Fallback for non-Windows or ctypes failure
    # Note: this is lossy (quoting/escaping lost)
    import shlex
    return " ".join(shlex.quote(arg) for arg in sys.argv)


def get_cmdline_sha256() -> str:
    """
    Get SHA256 hash of the raw command line.

    Returns:
        "sha256:<64hex>" format string

    This is the SSoT for command line identity. Use this instead of
    hashing sys.argv directly.
    """
    raw = _get_raw_cmdline()
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"sha256:{h}"


def get_cmdline_raw() -> str:
    """
    Get raw command line string (for debugging/logging).

    Returns:
        Raw command line string
    """
    return _get_raw_cmdline()


if __name__ == "__main__":
    # Self-test when run as module
    raw = get_cmdline_raw()
    sha = get_cmdline_sha256()
    print(f"Raw cmdline: {raw[:80]}{'...' if len(raw) > 80 else ''}")
    print(f"SHA256: {sha}")
