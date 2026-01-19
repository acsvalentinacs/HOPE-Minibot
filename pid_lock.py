import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Dict, Tuple, List

logger = logging.getLogger("pid_lock")

STATE_DIR = Path(__file__).resolve().parents[1] / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

# Role -> regex that MUST be present in the process CommandLine for that role
ROLE_SIGNATURES: Dict[str, str] = {
    "engine": r"minibot\\run_live_v5\.py",
    "tgbot": r"(minibot\\)?tg_bot_simple\.py",
    "listener": r"(tools\\)?hunters_listener_v1\.py",
}

def _get_lockfile(role: str) -> Path:
    return STATE_DIR / f"{role}.pid"

def _get_metafile(role: str) -> Path:
    return STATE_DIR / f"{role}.meta.json"

def _run_cmd(cmd: List[str]) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except Exception as e:
        return 1, "", str(e)

def _is_process_running(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    rc, out, _ = _run_cmd(["tasklist", "/fi", f"PID eq {pid}"])
    if rc != 0:
        return False
    return str(pid) in (out or "")

def _get_process_cmdline(pid: int) -> Optional[str]:
    if not pid or pid <= 0:
        return None

    # Try WMIC (works on many Win10 systems, despite deprecation)
    rc, out, _ = _run_cmd(["wmic", "process", "where", f"(ProcessId={pid})", "get", "CommandLine", "/value"])
    if rc == 0 and out:
        for line in out.splitlines():
            line = line.strip()
            if line.lower().startswith("commandline="):
                return line.split("=", 1)[1].strip() or None

    # Fallback: PowerShell Get-CimInstance
    ps = (
        "$p = Get-CimInstance Win32_Process -Filter \"ProcessId=%d\" -ErrorAction SilentlyContinue; "
        "if ($p) { $p.CommandLine }"
    ) % pid
    rc, out, _ = _run_cmd(["powershell", "-NoProfile", "-Command", ps])
    if rc == 0:
        s = (out or "").strip()
        return s if s else None

    return None

def _find_pid_by_signature(signature_regex: str) -> Optional[int]:
    # Ask PowerShell to find python processes matching signature
    # Return first PID (if any)
    sig = signature_regex.replace("'", "''")
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -match '%s' } | "
        "Select-Object -ExpandProperty ProcessId"
    ) % sig
    rc, out, _ = _run_cmd(["powershell", "-NoProfile", "-Command", ps])
    if rc != 0 or not out:
        return None
    for line in out.splitlines():
        line = line.strip()
        if line.isdigit():
            return int(line)
    return None

def _write_meta(role: str, pid: int, signature: Optional[str], cmdline: Optional[str]) -> None:
    meta = {
        "ts": time.time(),
        "role": role,
        "pid": pid,
        "signature": signature,
        "cmdline": cmdline,
        "host": os.environ.get("COMPUTERNAME"),
        "user": os.environ.get("USERNAME"),
    }
    try:
        _get_metafile(role).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug(f"meta write failed for {role}: {e}")

def acquire_pid_lock(role: str) -> bool:
    if not role or not isinstance(role, str):
        logger.error(f"Invalid role: {role}")
        return False

    lockfile = _get_lockfile(role)
    current_pid = os.getpid()
    signature = ROLE_SIGNATURES.get(role)
    current_cmdline = _get_process_cmdline(current_pid)

    if lockfile.exists():
        old_pid: Optional[int] = None
        try:
            old_pid_text = lockfile.read_text(encoding="utf-8").strip()
            old_pid = int(old_pid_text) if old_pid_text else None
        except Exception as e:
            logger.warning(f"Could not read lockfile {lockfile}: {e}")
            old_pid = None

        if old_pid and old_pid != current_pid:
            if _is_process_running(old_pid):
                # Strong check: if we have a signature, try to locate real process by signature
                if signature:
                    found = _find_pid_by_signature(signature)
                    if found and found != current_pid:
                        # Self-heal lockfile to the real pid we found
                        try:
                            lockfile.write_text(str(found), encoding="utf-8")
                            _write_meta(role, found, signature, _get_process_cmdline(found))
                            logger.error(f"{role} already running (PID {found}) [signature match]; lockfile had {old_pid}")
                        except Exception:
                            logger.error(f"{role} already running (PID {found}) [signature match]; lockfile had {old_pid}")
                        return False

                    # If no process matches signature, treat old_pid as stale even if PID exists (likely unrelated process)
                    logger.warning(f"{role} lockfile PID {old_pid} is running but no signature-match process found; treating as stale")
                else:
                    logger.error(f"{role} already running (PID {old_pid})")
                    return False
            else:
                logger.info(f"Old {role} process (PID {old_pid}) not running, acquiring lock")

    try:
        lockfile.write_text(str(current_pid), encoding="utf-8")
        _write_meta(role, current_pid, signature, current_cmdline)
        logger.info(f"{role} lock acquired (PID {current_pid})")
        return True
    except OSError as e:
        logger.error(f"Failed to acquire {role} lock: {e}")
        return False

def release_pid_lock(role: str) -> None:
    if not role:
        return
    lockfile = _get_lockfile(role)
    metafile = _get_metafile(role)
    try:
        if lockfile.exists():
            lockfile.unlink()
            logger.info(f"{role} lock released")
    except OSError as e:
        logger.error(f"Failed to release {role} lock: {e}")
    try:
        if metafile.exists():
            metafile.unlink()
    except OSError as e:
        logger.debug("Failed to unlink metafile %s: %s", metafile, e)

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    if acquire_pid_lock("test"):
        print(f"✅ Lock acquired (PID {os.getpid()})")
        time.sleep(2)
        release_pid_lock("test")
        print("✅ Lock released")
    else:
        print("❌ Failed to acquire lock")
        sys.exit(1)