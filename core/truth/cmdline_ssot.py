# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T18:00:00Z
# Purpose: SSoT Command Line via GetCommandLineW (Windows) / /proc (Linux)
# === END SIGNATURE ===
"""
Command Line Single Source of Truth (SSoT)

On Windows: Uses GetCommandLineW via ctypes (the ONLY reliable source).
On Linux: Uses /proc/self/cmdline.

This module provides:
- get_raw_cmdline(): Raw command line as executed
- get_cmdline_sha256(): SHA256 hash for evidence/audit
- get_execution_evidence(): Full evidence dict for manifests

CRITICAL: sys.argv is NOT reliable on Windows due to argument parsing.
          GetCommandLineW returns the exact string passed to CreateProcess.
"""

import hashlib
import os
import sys
import time
from pathlib import Path
from typing import Dict, Any, Optional


def get_raw_cmdline() -> str:
    """
    Get raw command line as executed.

    Windows: GetCommandLineW via ctypes
    Linux: /proc/self/cmdline

    Returns:
        Raw command line string

    Raises:
        RuntimeError: If unable to retrieve command line
    """
    if sys.platform == "win32":
        return _get_cmdline_windows()
    else:
        return _get_cmdline_linux()


def _get_cmdline_windows() -> str:
    """Get command line on Windows via GetCommandLineW."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        GetCommandLineW = kernel32.GetCommandLineW
        GetCommandLineW.argtypes = []
        GetCommandLineW.restype = wintypes.LPWSTR

        cmdline = GetCommandLineW()
        if cmdline:
            return cmdline
        raise RuntimeError("GetCommandLineW returned NULL")

    except Exception as e:
        raise RuntimeError(f"Failed to get command line via GetCommandLineW: {e}")


def _get_cmdline_linux() -> str:
    """Get command line on Linux via /proc."""
    try:
        cmdline_path = Path("/proc/self/cmdline")
        if cmdline_path.exists():
            raw = cmdline_path.read_bytes()
            # Arguments are null-separated
            args = raw.decode("utf-8", errors="replace").split("\x00")
            return " ".join(arg for arg in args if arg)
        else:
            # Fallback to sys.argv (less reliable)
            return " ".join(sys.argv)
    except Exception as e:
        raise RuntimeError(f"Failed to get command line: {e}")


def get_cmdline_sha256() -> str:
    """
    Get SHA256 hash of raw command line.

    This hash serves as evidence of what was executed.
    """
    raw = get_raw_cmdline()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_python_executable_info() -> Dict[str, str]:
    """Get information about Python executable."""
    exe = sys.executable
    exe_real = os.path.realpath(exe)

    return {
        "python_exe": exe,
        "python_exe_real": exe_real,
        "python_version": sys.version,
        "platform": sys.platform,
    }


def get_execution_evidence(
    allowlist_path: Optional[Path] = None,
    extra_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Get full execution evidence for manifests.

    This should be called at the start of any critical operation
    and saved to manifest for audit trail.

    Args:
        allowlist_path: Path to allowlist file (will compute sha256)
        extra_data: Additional data to include

    Returns:
        Evidence dict with:
        - cmdline_raw, cmdline_sha256
        - python_exe, python_version
        - cwd, timestamp
        - allowlist_sha256 (if provided)
        - git_commit (if in git repo)
    """
    evidence: Dict[str, Any] = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "timestamp_unix": time.time(),
        "cmdline_raw": get_raw_cmdline(),
        "cmdline_sha256": get_cmdline_sha256(),
        "cwd": os.getcwd(),
        **get_python_executable_info(),
    }

    # Allowlist hash
    if allowlist_path:
        try:
            al_path = Path(allowlist_path)
            if al_path.exists():
                content = al_path.read_bytes()
                evidence["allowlist_path"] = str(al_path)
                evidence["allowlist_sha256"] = hashlib.sha256(content).hexdigest()
        except Exception:
            evidence["allowlist_sha256"] = "ERROR"

    # Git commit (optional)
    try:
        git_head = Path(".git/HEAD")
        if git_head.exists():
            ref = git_head.read_text().strip()
            if ref.startswith("ref:"):
                ref_path = Path(".git") / ref[5:]
                if ref_path.exists():
                    evidence["git_commit"] = ref_path.read_text().strip()[:12]
            else:
                evidence["git_commit"] = ref[:12]
    except Exception:
        pass

    # Extra data
    if extra_data:
        evidence.update(extra_data)

    return evidence


def print_evidence(evidence: Dict[str, Any], file=None) -> None:
    """
    Print evidence in standardized format.

    Format:
    === EXECUTION EVIDENCE ===
    cmdline_sha256: <hash>
    allowlist_sha256: <hash>
    cwd: <path>
    ...
    === END EVIDENCE ===
    """
    if file is None:
        file = sys.stderr

    print("=== EXECUTION EVIDENCE ===", file=file)
    for key, value in sorted(evidence.items()):
        if key == "cmdline_raw":
            # Truncate long command lines
            val_str = str(value)
            if len(val_str) > 200:
                val_str = val_str[:200] + "..."
            print(f"  {key}: {val_str}", file=file)
        else:
            print(f"  {key}: {value}", file=file)
    print("=== END EVIDENCE ===", file=file)


# Convenience: verify we're running from expected venv
def verify_venv(expected_venv_marker: str = ".venv") -> bool:
    """
    Verify Python is running from project venv.

    Args:
        expected_venv_marker: Marker in path (default: ".venv")

    Returns:
        True if running from venv
    """
    exe_real = os.path.realpath(sys.executable)
    return expected_venv_marker in exe_real


if __name__ == "__main__":
    # Self-test
    print("=== CMDLINE SSoT SELF-TEST ===")
    print(f"Raw cmdline: {get_raw_cmdline()}")
    print(f"SHA256: {get_cmdline_sha256()}")
    print()
    evidence = get_execution_evidence()
    print_evidence(evidence)
