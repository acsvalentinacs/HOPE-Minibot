# -*- coding: utf-8 -*-
"""
Windows Mutex primitives with proper acquire/release contract.

Invariants:
- acquire_mutex returns ownership only on WAIT_OBJECT_0 or WAIT_ABANDONED
- release_mutex calls ReleaseMutex before CloseHandle
- All handles closed on any exit path
"""
from __future__ import annotations

import ctypes
from typing import Optional, Tuple

WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102
ERROR_ALREADY_EXISTS = 183


def _k32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _last_err() -> int:
    return ctypes.get_last_error()


def close_handle(h: int) -> None:
    if not h:
        return
    k32 = _k32()
    CloseHandle = k32.CloseHandle
    CloseHandle.argtypes = [ctypes.c_void_p]
    CloseHandle.restype = ctypes.c_bool
    CloseHandle(ctypes.c_void_p(h))


def acquire_mutex(name: str, timeout_ms: int) -> Tuple[str, Optional[int]]:
    """
    Acquire named mutex with timeout.

    Returns:
        ("acquired", handle) - ownership acquired, caller must release
        ("already_exists", None) - mutex exists, timeout_ms=0 fast-fail
        ("timeout", None) - waited and timed out
        ("error", None) - Win32 API failure
    """
    if not name:
        return "error", None

    k32 = _k32()

    CreateMutexW = k32.CreateMutexW
    CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    CreateMutexW.restype = ctypes.c_void_p

    WaitForSingleObject = k32.WaitForSingleObject
    WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    WaitForSingleObject.restype = ctypes.c_uint32

    # bInitialOwner=False: we explicitly wait for ownership
    h = CreateMutexW(None, False, name)
    if not h:
        return "error", None

    err = _last_err()

    # Fast-fail path for timeout=0 when mutex already exists
    if err == ERROR_ALREADY_EXISTS and timeout_ms == 0:
        close_handle(int(h))
        return "already_exists", None

    wait = WaitForSingleObject(h, max(0, int(timeout_ms)))

    if wait in (WAIT_OBJECT_0, WAIT_ABANDONED):
        return "acquired", int(h)

    if wait == WAIT_TIMEOUT:
        close_handle(int(h))
        return "timeout", None

    close_handle(int(h))
    return "error", None


def release_mutex(handle: int) -> None:
    """
    Release ownership and close handle.
    Safe to call even if not owned (ReleaseMutex may fail silently).
    """
    if not handle:
        return

    k32 = _k32()

    ReleaseMutex = k32.ReleaseMutex
    ReleaseMutex.argtypes = [ctypes.c_void_p]
    ReleaseMutex.restype = ctypes.c_bool

    try:
        ReleaseMutex(ctypes.c_void_p(handle))
    finally:
        close_handle(handle)
