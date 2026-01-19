"""
Windows Command Line Parser v1.0

GetCommandLineW() as single source of truth for argv parsing.
Bypasses Python's sys.argv corruption on Windows.

Usage:
    from core.win_cmdline import get_argv

    args = get_argv()
    print(f"Executable: {args[0]}")
    print(f"Arguments: {args[1:]}")
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import List

_kernel32 = ctypes.windll.kernel32
_shell32 = ctypes.windll.shell32

_kernel32.GetCommandLineW.restype = wintypes.LPWSTR

_shell32.CommandLineToArgvW.argtypes = [
    wintypes.LPCWSTR,
    ctypes.POINTER(ctypes.c_int),
]
_shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)

_kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
_kernel32.LocalFree.restype = wintypes.HLOCAL


def get_argv() -> List[str]:
    """
    Get command line arguments via GetCommandLineW().

    This is the ONLY reliable way to get Unicode arguments on Windows.
    Python's sys.argv may be corrupted by console encoding issues.

    Returns:
        List of command line arguments (argv[0] is the executable)
    """
    cmd_line = _kernel32.GetCommandLineW()
    argc = ctypes.c_int(0)

    argv_ptr = _shell32.CommandLineToArgvW(cmd_line, ctypes.byref(argc))
    if not argv_ptr:
        return []

    try:
        result = [argv_ptr[i] for i in range(argc.value)]
        return result
    finally:
        _kernel32.LocalFree(argv_ptr)


def get_args_only() -> List[str]:
    """
    Get only the arguments (without executable path).

    Returns:
        List of arguments after the executable
    """
    argv = get_argv()
    return argv[1:] if len(argv) > 1 else []


def get_python_argv() -> List[str]:
    """
    Return sys.argv-like list derived from GetCommandLineW().

    Handles:
    - python script.py a b  -> ["script.py", "a", "b"]
    - python -m pkg.mod a b -> ["pkg.mod", "a", "b"]

    This is the SINGLE SOURCE OF TRUTH for argv in HOPE project.
    """
    argv = get_argv()
    if not argv:
        return []

    # Find where actual args start (after python.exe and -m module)
    i = 0
    while i < len(argv):
        arg = argv[i]
        # Skip python executable
        if arg.lower().endswith(("python.exe", "python3.exe", "python")):
            i += 1
            continue
        # Skip -m and module name
        if arg == "-m" and i + 1 < len(argv):
            i += 2
            continue
        # Skip other python flags (-u, -B, etc)
        if arg.startswith("-") and not arg.startswith("--"):
            i += 1
            continue
        break

    return argv[i:] if i < len(argv) else []


def get_python_args_only() -> List[str]:
    """Get arguments only (without script/module name)."""
    argv = get_python_argv()
    return argv[1:] if len(argv) > 1 else []


if __name__ == "__main__":
    print("=== WIN CMDLINE TEST ===\n")

    argv = get_argv()
    print(f"argc: {len(argv)}")
    for i, arg in enumerate(argv):
        print(f"  argv[{i}]: {arg}")
