# -*- coding: utf-8 -*-
"""
HOPE Process Policy v2

Single-instance enforcement with robust ownership tracking.

Architecture:
1. Named mutex (Global -> Local fallback) - primary atomic lock
2. JSON lockfile with ownership metadata - for diagnostics and stale detection
3. Policy passport - for watchdog/status integration

Exit codes contract:
  0   = OK
  2   = Bad arguments
  10  = Import/setup error
  20  = Policy internal error (mutex/lock creation failed)
  100 = Already running (role occupied)

All comments in English to avoid encoding issues.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    import ctypes
    from ctypes import wintypes
except Exception:  # pragma: no cover
    ctypes = None
    wintypes = None


# Windows error codes
ERROR_ALREADY_EXISTS = 183
ERROR_ACCESS_DENIED = 5
STILL_ACTIVE = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# Exit codes (documented contract)
EXIT_OK = 0
EXIT_BAD_ARGS = 2
EXIT_IMPORT_ERROR = 10
EXIT_POLICY_ERROR = 20
EXIT_ALREADY_RUNNING = 100

# Lockfile schema versions
LOCKFILE_SCHEMA_V1 = "hope.lock.v1"
LOCKFILE_SCHEMA_V2 = "hope.lock.v2"
LOCKFILE_SCHEMA = LOCKFILE_SCHEMA_V2  # Current version


class PolicyError(RuntimeError):
    """Raised when policy enforcement fails."""
    pass


# =============================================================================
# Path utilities
# =============================================================================

def find_project_root(start: Optional[Path] = None, max_up: int = 12) -> Path:
    """Find HOPE project root by locating .venv/Scripts/python.exe upwards."""
    p = (start or Path(__file__)).resolve()
    for up in range(0, max_up + 1):
        try:
            cand = p.parents[up]
        except IndexError:
            break
        if (cand / ".venv" / "Scripts" / "python.exe").exists():
            return cand
    return Path(__file__).resolve().parents[1]


def venv_python(root: Path) -> Path:
    """Return path to venv python.exe."""
    return (root / ".venv" / "Scripts" / "python.exe").resolve()


def normpath(p: str) -> str:
    """Normalize path for comparison."""
    try:
        return os.path.normcase(os.path.realpath(p))
    except Exception:
        return os.path.normcase(p)


def is_running_under(exe: str, expected: str) -> bool:
    """Check if exe matches expected path."""
    return normpath(exe) == normpath(expected)


def _safe_role(role: str) -> str:
    """Sanitize role name for filesystem/mutex names."""
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in role.strip())


# =============================================================================
# Process utilities (Windows API)
# =============================================================================

def _get_real_executable() -> str:
    """Get actual executable path using Windows API (bypasses venv launcher lie)."""
    if sys.platform != "win32" or ctypes is None:
        return sys.executable
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        GetCurrentProcess = kernel32.GetCurrentProcess
        GetCurrentProcess.restype = wintypes.HANDLE

        QueryFullProcessImageNameW = kernel32.QueryFullProcessImageNameW
        QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD)
        ]
        QueryFullProcessImageNameW.restype = wintypes.BOOL

        h = GetCurrentProcess()
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
    except Exception:
        pass
    return sys.executable


def _get_process_executable(pid: int) -> Optional[str]:
    """Get executable path for a given PID using Windows API."""
    if pid <= 0 or ctypes is None:
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        OpenProcess = kernel32.OpenProcess
        OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        OpenProcess.restype = wintypes.HANDLE

        QueryFullProcessImageNameW = kernel32.QueryFullProcessImageNameW
        QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD)
        ]
        QueryFullProcessImageNameW.restype = wintypes.BOOL

        CloseHandle = kernel32.CloseHandle
        CloseHandle.argtypes = [wintypes.HANDLE]
        CloseHandle.restype = wintypes.BOOL

        h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return None
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value
        finally:
            CloseHandle(h)
    except Exception:
        pass
    return None


def _pid_is_alive(pid: int) -> bool:
    """Check if PID is still running."""
    if pid <= 0 or ctypes is None:
        return False
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        OpenProcess = k32.OpenProcess
        OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        OpenProcess.restype = wintypes.HANDLE

        GetExitCodeProcess = k32.GetExitCodeProcess
        GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        GetExitCodeProcess.restype = wintypes.BOOL

        CloseHandle = k32.CloseHandle
        CloseHandle.argtypes = [wintypes.HANDLE]
        CloseHandle.restype = wintypes.BOOL

        h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            code = wintypes.DWORD(0)
            ok = GetExitCodeProcess(h, ctypes.byref(code))
            if not ok:
                return False
            return int(code.value) == STILL_ACTIVE
        finally:
            CloseHandle(h)
    except Exception:
        return False


def _get_process_birth_filetime(pid: int) -> Optional[int]:
    """
    Get process creation time as Windows FILETIME (100-nanosecond intervals since 1601).

    This provides a unique identity anchor for PID reuse detection.
    Returns None if:
    - PID invalid or process not accessible
    - ACCESS_DENIED (elevated/other-user process)
    - Any API error

    The caller should treat None as "unknown" and fall back to exe verification.
    """
    if pid <= 0 or ctypes is None:
        return None
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        OpenProcess = k32.OpenProcess
        OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        OpenProcess.restype = wintypes.HANDLE

        CloseHandle = k32.CloseHandle
        CloseHandle.argtypes = [wintypes.HANDLE]
        CloseHandle.restype = wintypes.BOOL

        # FILETIME structure: 64-bit value as two 32-bit parts
        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD),
                        ("dwHighDateTime", wintypes.DWORD)]

        GetProcessTimes = k32.GetProcessTimes
        GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(FILETIME),  # lpCreationTime
            ctypes.POINTER(FILETIME),  # lpExitTime
            ctypes.POINTER(FILETIME),  # lpKernelTime
            ctypes.POINTER(FILETIME),  # lpUserTime
        ]
        GetProcessTimes.restype = wintypes.BOOL

        h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            # ACCESS_DENIED or process not found
            return None
        try:
            creation = FILETIME()
            exit_time = FILETIME()
            kernel = FILETIME()
            user = FILETIME()

            ok = GetProcessTimes(
                h,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            )
            if not ok:
                return None

            # Combine to 64-bit FILETIME value
            ft = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            return ft
        finally:
            CloseHandle(h)
    except Exception:
        return None


def _get_current_process_birth_filetime() -> Optional[int]:
    """Get birth_filetime for current process (always accessible)."""
    if ctypes is None:
        return None
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        GetCurrentProcess = k32.GetCurrentProcess
        GetCurrentProcess.restype = wintypes.HANDLE

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD),
                        ("dwHighDateTime", wintypes.DWORD)]

        GetProcessTimes = k32.GetProcessTimes
        GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
        ]
        GetProcessTimes.restype = wintypes.BOOL

        h = GetCurrentProcess()
        creation = FILETIME()
        exit_time = FILETIME()
        kernel = FILETIME()
        user = FILETIME()

        ok = GetProcessTimes(
            h,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if not ok:
            return None

        ft = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return ft
    except Exception:
        return None


def _get_cmdline_raw() -> str:
    """
    Get raw command line string using GetCommandLineW on Windows.
    This is the single source of truth for cmdline identity.
    """
    if sys.platform == "win32" and ctypes is not None:
        try:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCommandLineW.restype = ctypes.c_wchar_p
            cmdline = k32.GetCommandLineW() or ""
            return cmdline
        except Exception:
            pass
    return " ".join(sys.argv)


def _cmdline_hash(cmdline_raw: str) -> str:
    """
    Generate full SHA256 hash of command line.
    Format: sha256:<64 hex chars>

    This is verifiable: sha256(cmdline_raw) must equal the hash portion.
    """
    digest = hashlib.sha256(cmdline_raw.encode("utf-8", errors="replace")).hexdigest()
    return f"sha256:{digest}"


# =============================================================================
# Windows Mutex
# =============================================================================

def _root_hash(root: Path) -> str:
    """Generate short hash of project root for mutex isolation between worktrees."""
    # Normalize: absolute, normalized, lowercase, no trailing slashes
    p = os.path.abspath(str(root))
    p = os.path.normpath(p)
    p = p.rstrip("\\/").lower()
    return hashlib.sha256(p.encode("utf-8", errors="replace")).hexdigest()[:12]


def role_mutex_candidates(role: str, root: Path) -> Tuple[str, str]:
    """
    Return (Global, Local) mutex names with root hash for worktree isolation.

    Format: HOPE_ROLE_<ROLE>_<root_hash>
    This ensures different project clones/worktrees don't block each other.
    """
    safe = _safe_role(role)
    rhash = _root_hash(root)
    return (f"Global\\HOPE_ROLE_{safe}_{rhash}", f"Local\\HOPE_ROLE_{safe}_{rhash}")


class _WinMutex:
    """Windows named mutex wrapper."""

    def __init__(self, name: str):
        if ctypes is None:
            raise PolicyError("ctypes unavailable; cannot create Windows mutex")

        self.name = name
        self.handle = None

        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self._CreateMutexW = self._kernel32.CreateMutexW
        self._CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        self._CreateMutexW.restype = wintypes.HANDLE

        self._CloseHandle = self._kernel32.CloseHandle
        self._CloseHandle.argtypes = [wintypes.HANDLE]
        self._CloseHandle.restype = wintypes.BOOL

    def acquire(self) -> Tuple[bool, bool]:
        """
        Attempt to acquire mutex.
        Returns (acquired, already_existed).
        """
        h = self._CreateMutexW(None, False, self.name)
        if not h:
            err = ctypes.get_last_error()
            raise OSError(err, f"CreateMutexW failed for {self.name}")
        self.handle = h
        err = ctypes.get_last_error()
        already = (err == ERROR_ALREADY_EXISTS)
        return (not already), already

    def release(self) -> None:
        """Release mutex handle."""
        if self.handle:
            try:
                self._CloseHandle(self.handle)
            finally:
                self.handle = None


# =============================================================================
# JSON Lockfile with ownership metadata
# =============================================================================

_MUTEX_HOLD: Optional[_WinMutex] = None
_LOCKFILE_PATH: Optional[Path] = None


def _lockfile_path(root: Path, role: str) -> Path:
    """Get lockfile path for role. Uses .lock.json extension for explicit JSON format."""
    safe = _safe_role(role)
    d = root / "state" / "locks"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{safe}.lock.json"


def _create_lock_data(role: str, root: Path, mutex_name: str) -> Dict[str, Any]:
    """
    Create JSON lock data with full ownership metadata (schema v2).

    Self-verifiable contract:
    - cmdline_raw: actual command line from GetCommandLineW
    - cmdline_hash: sha256(cmdline_raw) - verifiable by gate
    - birth_filetime: process creation time - unique identity anchor
    """
    real_exe = _get_real_executable()
    ts = time.time()
    pid = os.getpid()
    birth_ft = _get_current_process_birth_filetime()
    cmdline_raw = _get_cmdline_raw()

    data: Dict[str, Any] = {
        "schema": LOCKFILE_SCHEMA,
        "role": role,
        "root": str(root),
        "root_hash": _root_hash(root),
        "owner_pid": pid,
        "birth_filetime": birth_ft,  # None if unavailable (non-Windows)
        "real_exe": real_exe,
        "cmdline_raw": cmdline_raw,
        "cmdline_hash": _cmdline_hash(cmdline_raw),
        "started_ts": ts,
        "mutex_name": mutex_name,
        # For human readability
        "started_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)),
    }

    return data


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    """Atomically write JSON file using temp+replace+fsync pattern."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _read_lock_data(path: Path) -> Optional[Dict[str, Any]]:
    """Read and parse lock JSON. Returns None if unreadable/invalid."""
    try:
        txt = path.read_text(encoding="utf-8", errors="ignore")
        # Support legacy format (pid=...\nts=...)
        if txt.startswith("pid="):
            lines = txt.strip().split("\n")
            pid = 0
            ts = 0.0
            for line in lines:
                if line.startswith("pid="):
                    pid = int(line.split("=", 1)[1].strip())
                elif line.startswith("ts="):
                    ts = float(line.split("=", 1)[1].strip())
            return {"pid": pid, "started_ts": ts, "legacy": True}
        return json.loads(txt)
    except Exception:
        return None


def _is_lock_owner_alive(data: Dict[str, Any]) -> bool:
    """
    Check if lock owner is still alive.

    Verification priority (schema v2):
    1. PID must be alive (basic check)
    2. birth_filetime must match (strongest - unique identity anchor)
    3. real_exe must match (fallback if birth_filetime unavailable)

    Returns False if any verification fails (fail-closed).
    """
    # Support both old "pid" and new "owner_pid" field names
    pid = data.get("owner_pid") or data.get("pid", 0)
    if not pid or not _pid_is_alive(pid):
        return False

    # Schema v2: birth_filetime is the primary identity anchor
    expected_birth_ft = data.get("birth_filetime")
    if expected_birth_ft is not None:
        actual_birth_ft = _get_process_birth_filetime(pid)
        if actual_birth_ft is None:
            # Cannot verify birth_filetime (ACCESS_DENIED or API error)
            # Fall back to exe verification
            pass
        elif actual_birth_ft != expected_birth_ft:
            # PID was reused by different process
            return False
        else:
            # birth_filetime matches - definitive proof of same process
            return True

    # Fallback: verify real_exe matches (weaker protection against PID reuse)
    expected_exe = data.get("real_exe")
    if expected_exe:
        actual_exe = _get_process_executable(pid)
        if actual_exe:
            # Normalize for comparison
            if normpath(actual_exe) != normpath(expected_exe):
                return False  # PID reused by different process

    return True


def _acquire_lockfile(root: Path, role: str, mutex_name: str) -> Dict[str, Any]:
    """
    Acquire lockfile with JSON ownership metadata.
    Called AFTER mutex is acquired (lockfile is secondary, for diagnostics).
    Returns the lock data written.
    """
    global _LOCKFILE_PATH

    p = _lockfile_path(root, role)
    _LOCKFILE_PATH = p

    # Check existing lock
    if p.exists():
        data = _read_lock_data(p)
        if data and _is_lock_owner_alive(data):
            # Real owner still running - should not happen if mutex was acquired
            raise SystemExit(EXIT_ALREADY_RUNNING)
        # Stale lock - will be overwritten by atomic write

    # Create lock data and write atomically
    lock_data = _create_lock_data(role, root, mutex_name)
    _atomic_write_json(p, lock_data)

    def _cleanup() -> None:
        # Only delete if we're still the owner
        if _LOCKFILE_PATH and _LOCKFILE_PATH.exists():
            try:
                data = _read_lock_data(_LOCKFILE_PATH)
                if data and data.get("owner_pid") == os.getpid():
                    _LOCKFILE_PATH.unlink(missing_ok=True)
            except Exception:
                pass

    atexit.register(_cleanup)
    return lock_data


def _release_lockfile() -> None:
    """Explicitly release lockfile (for testing)."""
    global _LOCKFILE_PATH
    if _LOCKFILE_PATH and _LOCKFILE_PATH.exists():
        try:
            _LOCKFILE_PATH.unlink(missing_ok=True)
        except Exception:
            pass
        _LOCKFILE_PATH = None


# =============================================================================
# Policy Passport (for watchdog/status integration)
# =============================================================================

def _write_policy_passport(root: Path, role: str) -> None:
    """
    Write policy passport JSON for watchdog/status integration.
    This file describes the running role instance.
    """
    passport_dir = root / "state" / "health"
    passport_dir.mkdir(parents=True, exist_ok=True)
    passport_path = passport_dir / f"policy_passport_{_safe_role(role)}.json"

    data = {
        "role": role,
        "pid": os.getpid(),
        "real_exe": _get_real_executable(),
        "lockfile": str(_lockfile_path(root, role)),
        "mutex_name": role_mutex_candidates(role, root)[0],  # Primary mutex name
        "root": str(root),
        "started_ts": time.time(),
        "started_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    try:
        passport_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass  # Non-critical

    def _cleanup_passport() -> None:
        try:
            if passport_path.exists():
                # Only delete if we're the owner
                try:
                    pdata = json.loads(passport_path.read_text(encoding="utf-8"))
                    if pdata.get("pid") == os.getpid():
                        passport_path.unlink(missing_ok=True)
                except Exception:
                    pass
        except Exception:
            pass

    atexit.register(_cleanup_passport)


# =============================================================================
# Main enforcement API
# =============================================================================

def enforce_single_instance(role: str, root: Optional[Path] = None, exit_code: int = EXIT_ALREADY_RUNNING) -> None:
    """
    Enforce single instance per role.

    Order of operations (fail-closed):
    1. Acquire mutex (atomic, primary lock)
    2. Acquire lockfile (JSON with metadata, for diagnostics)
    3. Write policy passport (for watchdog integration)

    On conflict: exits with exit_code (default 100).
    """
    global _MUTEX_HOLD

    root = root or find_project_root(Path(__file__))

    # 1. Mutex first (atomic, most reliable)
    names = role_mutex_candidates(role, root)
    last_err: Optional[int] = None

    acquired_mutex_name: Optional[str] = None
    for name in names:
        try:
            m = _WinMutex(name)
            acquired, already = m.acquire()
            _MUTEX_HOLD = m
            if already or not acquired:
                sys.stderr.write(f"[HOPE][policy] Role '{role}' already running (mutex exists). Exiting.\n")
                sys.stderr.flush()
                raise SystemExit(exit_code)
            acquired_mutex_name = name
            break  # Mutex acquired
        except OSError as e:
            last_err = getattr(e, "winerror", None) or (e.args[0] if e.args else None)
            if last_err == ERROR_ACCESS_DENIED:
                continue  # Try Local namespace
            raise
    else:
        # No mutex could be acquired
        sys.stderr.write(f"[HOPE][policy] Cannot create mutex for role '{role}' (last_err={last_err}). Exiting.\n")
        sys.stderr.flush()
        raise SystemExit(EXIT_POLICY_ERROR)

    # 2. Lockfile (secondary, for diagnostics and stale detection)
    _acquire_lockfile(root, role, acquired_mutex_name or names[0])

    # 3. Policy passport (for watchdog/status)
    _write_policy_passport(root, role)


def release_single_instance() -> None:
    """Explicitly release mutex and lockfile (mainly for testing)."""
    global _MUTEX_HOLD
    if _MUTEX_HOLD:
        _MUTEX_HOLD.release()
        _MUTEX_HOLD = None
    _release_lockfile()


# =============================================================================
# Venv context detection
# =============================================================================

def _is_in_venv_context(root: Path) -> bool:
    """
    Check if we're running in a venv context (even if using base Python).
    True if:
    - VIRTUAL_ENV is set, OR
    - sys.prefix points to the venv, OR
    - venv site-packages is in sys.path
    """
    venv_path = str(root / ".venv")

    if os.environ.get("VIRTUAL_ENV", "").lower() == venv_path.lower():
        return True

    if venv_path.lower() in sys.prefix.lower():
        return True

    venv_site = str(root / ".venv" / "Lib" / "site-packages")
    for p in sys.path:
        if venv_site.lower() in p.lower():
            return True

    return False


def try_reexec_to_venv(root: Path, *, env_flag: str = "HOPE_REEXEC_TO_VENV", default_on: bool = True) -> None:
    """
    Re-exec into venv python if not already there.

    IMPORTANT: On Windows, re-exec into venv launcher causes it to spawn
    Python312 as child, resulting in 2 processes. We check venv context
    to avoid this when packages are already accessible.
    """
    enabled = os.environ.get(env_flag)
    if enabled is None:
        enabled = "1" if default_on else "0"
    enabled = enabled.strip() not in ("0", "false", "False", "no", "")

    if not enabled:
        return

    vpy = venv_python(root)
    if not vpy.exists():
        return

    vpy_s = str(vpy)
    cur = str(sys.executable)

    # Already running under venv python
    if is_running_under(cur, vpy_s):
        return

    # Already in venv context (packages accessible)
    if _is_in_venv_context(root):
        return

    # Avoid infinite loops
    if os.environ.get("HOPE_REEXEC_DONE") == "1":
        return

    os.environ["HOPE_REEXEC_DONE"] = "1"
    os.environ.setdefault("PYTHONUTF8", "1")

    argv = [vpy_s] + sys.argv
    try:
        os.execv(vpy_s, argv)
    except Exception:
        # Fallback: spawn and exit
        try:
            import subprocess
            subprocess.Popen(argv, cwd=str(root), env=os.environ.copy())
        finally:
            raise SystemExit(EXIT_OK)


# =============================================================================
# Utilities
# =============================================================================

def now_ts() -> float:
    """Current timestamp."""
    return time.time()


def get_launch_python(root: Path) -> str:
    """
    Get Python executable for launching new processes.
    On Windows, returns base Python to avoid launcher spawning child.
    """
    vpy = venv_python(root)
    if not vpy.exists():
        return str(vpy)

    base = getattr(sys, "_base_executable", None)
    if base and Path(base).exists():
        return base

    return str(vpy)


def read_role_lock(root: Path, role: str) -> Optional[Dict[str, Any]]:
    """Read lock data for a role (for external tools like /status)."""
    p = _lockfile_path(root, role)
    if not p.exists():
        # Try legacy .lock extension
        legacy = p.with_suffix(".lock").with_suffix(".lock")
        safe = _safe_role(role)
        legacy = root / "state" / "locks" / f"{safe}.lock"
        if legacy.exists():
            return _read_lock_data(legacy)
        return None
    return _read_lock_data(p)


def is_role_running(root: Path, role: str) -> bool:
    """Check if a role is currently running (for external tools)."""
    data = read_role_lock(root, role)
    if not data:
        return False
    return _is_lock_owner_alive(data)
