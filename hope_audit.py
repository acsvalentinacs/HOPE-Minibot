"""Windows command line SSoT verification."""
import ctypes
import ctypes.wintypes
import hashlib
import json
import os
import sys
import time
from typing import List, Dict, Any

class AuditError(RuntimeError):
    pass

def get_command_line_w() -> str:
    GetCommandLineW = ctypes.windll.kernel32.GetCommandLineW
    GetCommandLineW.restype = ctypes.wintypes.LPCWSTR
    return GetCommandLineW() or ""

def command_line_to_argv_w(cmdline: str) -> List[str]:
    CommandLineToArgvW = ctypes.windll.shell32.CommandLineToArgvW
    CommandLineToArgvW.argtypes = [ctypes.wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    CommandLineToArgvW.restype = ctypes.POINTER(ctypes.wintypes.LPWSTR)
    argc = ctypes.c_int(0)
    argv_ptr = CommandLineToArgvW(cmdline, ctypes.byref(argc))
    if not argv_ptr:
        return []
    try:
        return [argv_ptr[i] for i in range(argc.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(argv_ptr)

def argv_from_ssot() -> List[str]:
    return command_line_to_argv_w(get_command_line_w())

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def atomic_write_bytes(path: str, data: bytes, fsync: bool = True) -> None:
    abs_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    tmp = f"{abs_path}.tmp.{os.getpid()}.{int(time.time()*1000)}"
    with open(tmp, "wb") as f:
        f.write(data)
        if fsync:
            f.flush()
            os.fsync(f.fileno())
    os.replace(tmp, abs_path)

def atomic_write_json(path: str, obj: Any) -> None:
    data = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
    atomic_write_bytes(path, data)

def runtime_facts() -> Dict[str, Any]:
    return {
        "python_exe": sys.executable,
        "python_version": sys.version.split()[0],
        "cwd": os.getcwd(),
        "cmdline_w": get_command_line_w(),
        "pid": os.getpid(),
        "ts": int(time.time()),
    }