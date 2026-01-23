# -*- coding: utf-8 -*-
"""
P5: Process Manager — PID operations with Windows kernel32 verification

Invariants:
- PID liveness via kernel32 (not just psutil)
- Kill uses kernel32 TerminateProcess
- Fail-closed: uncertain state = assume dead
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
STILL_ACTIVE = 259


def _k32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def pid_is_alive(pid: int) -> bool:
    """
    Check if PID is alive using GetExitCodeProcess.

    Fail-closed: API failure = False (assume dead).
    """
    if pid <= 0:
        return False

    try:
        k32 = _k32()
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False

        try:
            code = ctypes.c_uint32()
            ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
            if not ok:
                return False
            return code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    except Exception:
        return False


def kill_pid(pid: int, timeout_sec: float = 5.0) -> bool:
    """
    Kill process by PID.

    Returns True if process is dead after kill attempt.
    """
    if pid <= 0:
        return True

    if not pid_is_alive(pid):
        return True

    try:
        k32 = _k32()
        h = k32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if h:
            try:
                k32.TerminateProcess(h, 1)
            finally:
                k32.CloseHandle(h)

        # Wait for death
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if not pid_is_alive(pid):
                return True
            time.sleep(0.1)

        return not pid_is_alive(pid)
    except Exception:
        return not pid_is_alive(pid)


@dataclass
class StartResult:
    """Result of process start attempt."""
    success: bool
    pid: Optional[int]
    error: Optional[str]


def start_process(
    command: List[str],
    working_dir: Path,
    env: Optional[dict] = None,
) -> StartResult:
    """
    Start process with given command.

    Returns StartResult with PID on success.
    """
    try:
        full_env = dict(os.environ)
        if env:
            full_env.update(env)

        # Set PYTHONEXECUTABLE to prevent spawn clones
        if command and command[0].endswith("python.exe"):
            full_env["PYTHONEXECUTABLE"] = command[0]

        proc = subprocess.Popen(
            command,
            cwd=str(working_dir),
            env=full_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        # Give it a moment to start
        time.sleep(0.5)

        if proc.poll() is None and pid_is_alive(proc.pid):
            return StartResult(success=True, pid=proc.pid, error=None)

        return StartResult(
            success=False,
            pid=None,
            error=f"Process exited immediately (returncode={proc.poll()})",
        )
    except Exception as e:
        return StartResult(success=False, pid=None, error=str(e))


def write_pid_file(pid_path: Path, pid: int) -> None:
    """
    Write PID file atomically.

    Uses temp + replace for atomicity.
    """
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = pid_path.with_suffix(f".tmp.{os.getpid()}")

    try:
        tmp.write_text(f"{pid}\n", encoding="utf-8")
        tmp.replace(pid_path)
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass
        raise


def remove_pid_file(pid_path: Path) -> None:
    """Remove PID file if exists."""
    try:
        if pid_path.exists():
            pid_path.unlink()
    except Exception:
        pass
