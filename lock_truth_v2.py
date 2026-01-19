# -*- coding: utf-8 -*-
"""
HOPE Lock Truth v2

Invariants:
- cmdline_raw: ONLY from GetCommandLineW (single source)
- cmdline_hash: sha256:<64hex> of cmdline_raw (self-verifiable)
- root_hash: 12-char hex (no prefix) for mutex salting
- fail-closed: Windows API failure -> raise RuntimeError
- atomic write: temp -> flush -> fsync -> replace
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from minibot.mutex_win32 import (
    acquire_mutex as _acquire_mutex,
    release_mutex as _release_mutex,
    close_handle as _close_handle,
)

LOCK_SCHEMA = "hope.lock.v2"
ROOT_HASH_LEN = 12
DEFAULT_MUTEX_TIMEOUT_MS = 5000

STILL_ACTIVE = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_ABANDONED = 0x00000080


def _k32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _last_err() -> int:
    return ctypes.get_last_error()


def sha256_prefixed(s: str) -> str:
    """Full SHA256 with prefix. Self-verifiable."""
    h = hashlib.sha256(s.encode("utf-8", errors="replace")).hexdigest()
    return f"sha256:{h}"


def norm_root(root: Path) -> str:
    """
    Stable normalization for hashing.
    Uses Windows-native normalization (abspath, normpath, lower).
    Must match p0_lock_gate_v2.py exactly.
    """
    p = os.path.abspath(str(root))
    p = os.path.normpath(p)
    p = p.rstrip("\\/")
    return p.lower()


def root_hash_short(root: Path) -> str:
    """12-char root hash for mutex salting. No prefix."""
    s = norm_root(root).encode("utf-8", errors="replace")
    return hashlib.sha256(s).hexdigest()[:ROOT_HASH_LEN]


def compute_mutex_name(role: str, root: Path) -> str:
    """Compute canonical mutex name: Global\\HOPE_{ROLE}_{root_hash12}."""
    rh = root_hash_short(root)
    return f"Global\\HOPE_{role.strip().upper()}_{rh}"


# Alias for P9 compatibility
root_hash12 = root_hash_short


def get_cmdline_raw() -> str:
    """
    Single source of truth for command line: GetCommandLineW().
    No fallback by design (fail-closed).
    """
    k32 = _k32()
    GetCommandLineW = k32.GetCommandLineW
    GetCommandLineW.restype = ctypes.c_wchar_p
    s = GetCommandLineW()
    if not s:
        raise RuntimeError("GetCommandLineW returned empty")
    return str(s)


def get_module_filename() -> str:
    """Current process executable path via GetModuleFileNameW."""
    k32 = _k32()
    buf = ctypes.create_unicode_buffer(32768)
    GetModuleFileNameW = k32.GetModuleFileNameW
    GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
    GetModuleFileNameW.restype = ctypes.c_uint32
    n = GetModuleFileNameW(None, buf, ctypes.c_uint32(len(buf)))
    if n == 0:
        raise RuntimeError(f"GetModuleFileNameW failed err={_last_err()}")
    return buf.value


def _filetime_struct():
    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", ctypes.c_uint32),
                    ("dwHighDateTime", ctypes.c_uint32)]
    return FILETIME


def get_current_birth_filetime() -> int:
    """
    Process creation time as FILETIME (100ns ticks since 1601).
    Unique identity anchor for PID reuse detection.
    """
    k32 = _k32()

    GetCurrentProcess = k32.GetCurrentProcess
    GetCurrentProcess.restype = ctypes.c_void_p
    hproc = GetCurrentProcess()

    FILETIME = _filetime_struct()
    creation = FILETIME()
    exit_t = FILETIME()
    kernel_t = FILETIME()
    user_t = FILETIME()

    GetProcessTimes = k32.GetProcessTimes
    GetProcessTimes.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
    ]
    GetProcessTimes.restype = ctypes.c_bool

    ok = GetProcessTimes(
        hproc,
        ctypes.byref(creation),
        ctypes.byref(exit_t),
        ctypes.byref(kernel_t),
        ctypes.byref(user_t),
    )
    if not ok:
        raise RuntimeError(f"GetProcessTimes failed err={_last_err()}")

    return (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)


# Alias for P9 compatibility
get_birth_filetime_100ns = get_current_birth_filetime


def get_process_birth_filetime(pid: int) -> Optional[int]:
    """Get birth filetime for external PID. Returns None on failure."""
    if pid <= 0:
        return None

    k32 = _k32()

    OpenProcess = k32.OpenProcess
    OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
    OpenProcess.restype = ctypes.c_void_p

    CloseHandle = k32.CloseHandle
    CloseHandle.argtypes = [ctypes.c_void_p]
    CloseHandle.restype = ctypes.c_bool

    h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return None

    try:
        FILETIME = _filetime_struct()
        creation = FILETIME()
        exit_t = FILETIME()
        kernel_t = FILETIME()
        user_t = FILETIME()

        GetProcessTimes = k32.GetProcessTimes
        GetProcessTimes.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
        ]
        GetProcessTimes.restype = ctypes.c_bool

        ok = GetProcessTimes(
            h,
            ctypes.byref(creation),
            ctypes.byref(exit_t),
            ctypes.byref(kernel_t),
            ctypes.byref(user_t),
        )
        if not ok:
            return None

        return (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
    finally:
        CloseHandle(h)


def pid_is_alive(pid: int) -> bool:
    """Check if PID is alive using GetExitCodeProcess."""
    if pid <= 0:
        return False

    k32 = _k32()

    OpenProcess = k32.OpenProcess
    OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
    OpenProcess.restype = ctypes.c_void_p

    GetExitCodeProcess = k32.GetExitCodeProcess
    GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    GetExitCodeProcess.restype = ctypes.c_bool

    CloseHandle = k32.CloseHandle
    CloseHandle.argtypes = [ctypes.c_void_p]
    CloseHandle.restype = ctypes.c_bool

    h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return False

    try:
        code = ctypes.c_uint32()
        ok = GetExitCodeProcess(h, ctypes.byref(code))
        if not ok:
            return False
        return code.value == STILL_ACTIVE
    finally:
        CloseHandle(h)


def atomic_write_json(path: Path, obj: Dict[str, Any]) -> None:
    """Atomic JSON write: temp -> flush -> fsync -> replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{int(time.time() * 1000)}")
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"

    fd = os.open(str(tmp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", closefd=False, newline="\n") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    finally:
        os.close(fd)

    os.replace(str(tmp), str(path))


# Public API wrappers (delegate to mutex_win32)
def acquire_mutex_with_timeout(
    name: str, timeout_ms: int = DEFAULT_MUTEX_TIMEOUT_MS
) -> Tuple[str, Optional[int]]:
    """Acquire named mutex with timeout. Returns (status, handle)."""
    return _acquire_mutex(name, timeout_ms)


def release_mutex(handle: int) -> None:
    """Release mutex ownership and close handle."""
    _release_mutex(handle)


def close_handle(handle: int) -> None:
    """Close handle without releasing mutex (legacy compat)."""
    _close_handle(handle)


def build_lock(role: str, root: Path, mutex_name: str) -> Dict[str, Any]:
    """
    Build canonical lock object with full ownership metadata.
    Self-verifiable: sha256(cmdline_raw) == cmdline_hash.
    """
    now = time.time()
    cmd_raw = get_cmdline_raw()
    root_n = norm_root(root)
    rh12 = root_hash_short(root)

    return {
        "schema": LOCK_SCHEMA,
        "role": role.strip().upper(),
        "root": str(root.resolve()),
        "root_norm": root_n,
        "root_hash": rh12,
        "root_hash12": rh12,  # P9: explicit alias for verify
        "owner_pid": os.getpid(),
        "birth_filetime": get_current_birth_filetime(),
        "birth_filetime_100ns": get_current_birth_filetime(),  # P9: explicit alias
        "real_exe": get_module_filename(),
        "cmdline_raw": cmd_raw,
        "cmdline_hash": sha256_prefixed(cmd_raw),
        "started_ts": float(now),
        "created_ts": float(now),  # P9: alias
        "started_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "mutex_name": mutex_name,
        "contract": {
            "cmdline_source": "GetCommandLineW",
            "hash_alg": "sha256",
            "hash_prefix": "sha256:",
            "atomic_write": "temp+flush+fsync+replace",
            "fail_closed": True,
        },
    }
