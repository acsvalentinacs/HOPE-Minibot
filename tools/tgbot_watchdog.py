# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T16:50:00Z
# Purpose: Watchdog supervisor - auto-restart bot on hang
# Security: Fail-closed, external process, no shared state
# === END SIGNATURE ===
"""
HOPE TG Bot Watchdog Supervisor.

PROBLEM: Bot hangs due to blocking I/O in async context.
SOLUTION: External process monitors heartbeat, kills and restarts if stale.

RUN: python tools/tgbot_watchdog.py
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# === CONFIGURATION ===
BOT_DIR = Path(r"C:\Users\kirillDev\Desktop\TradingBot\minibot")
VENV_PYTHON = Path(r"C:\Users\kirillDev\Desktop\TradingBot\.venv\Scripts\python.exe")
BOT_SCRIPT = BOT_DIR / "tg_bot_simple.py"
HEALTH_FILE = BOT_DIR / "state" / "health_tgbot.json"
LOCK_FILE = Path(r"C:\Users\kirillDev\Desktop\TradingBot\state\pids\tg_bot_simple.lock")
LOG_FILE = BOT_DIR / "logs" / "watchdog.log"
RESTART_LOG = BOT_DIR / "state" / "watchdog_restarts.jsonl"

STALE_THRESHOLD_SEC = 60
CHECK_INTERVAL_SEC = 15
MAX_RESTARTS_PER_HOUR = 10


def log(msg: str) -> None:
    """Log to file and stdout."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def get_heartbeat_age() -> float:
    """Get heartbeat age in seconds. Returns inf if missing/error."""
    try:
        if not HEALTH_FILE.exists():
            return float("inf")

        data = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))

        # Try multiple timestamp fields
        hb_str = data.get("hb_ts") or data.get("heartbeat_utc") or ""
        if not hb_str:
            return float("inf")

        # Parse ISO format
        hb_str = hb_str.replace("Z", "+00:00")
        if "+" not in hb_str and "-" not in hb_str[10:]:
            hb_str += "+00:00"

        hb_dt = datetime.fromisoformat(hb_str)
        now = datetime.now(timezone.utc)
        return (now - hb_dt).total_seconds()
    except Exception as e:
        log(f"ERROR reading heartbeat: {e}")
        return float("inf")


def get_bot_pid() -> int | None:
    """Get bot PID from health file or lock file."""
    try:
        # Try health file first
        if HEALTH_FILE.exists():
            data = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
            pid = data.get("pid")
            if pid:
                return int(pid)

        # Try lock file
        if LOCK_FILE.exists():
            pid_str = LOCK_FILE.read_text(encoding="utf-8").strip()
            if pid_str.isdigit():
                return int(pid_str)
    except Exception:
        pass
    return None


def is_process_alive(pid: int) -> bool:
    """Check if process exists."""
    try:
        # Windows: tasklist
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return str(pid) in result.stdout
    except Exception:
        return False


def kill_bot(pid: int) -> bool:
    """Kill bot process forcefully."""
    try:
        log(f"Killing bot PID {pid}...")
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True,
            timeout=10,
        )
        time.sleep(2)

        # Clean lock
        if LOCK_FILE.exists():
            LOCK_FILE.unlink(missing_ok=True)

        return not is_process_alive(pid)
    except Exception as e:
        log(f"ERROR killing: {e}")
        return False


def start_bot() -> int | None:
    """Start bot process."""
    try:
        log("Starting bot...")

        # Clean locks
        if LOCK_FILE.exists():
            LOCK_FILE.unlink(missing_ok=True)

        # Delete old health to force fresh
        if HEALTH_FILE.exists():
            HEALTH_FILE.unlink(missing_ok=True)

        # Start
        proc = subprocess.Popen(
            [str(VENV_PYTHON), "-u", str(BOT_SCRIPT)],
            cwd=str(BOT_DIR),
            stdout=subprocess.DEVNULL,
            stderr=open(BOT_DIR / "logs" / "bot_err.log", "a"),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )

        log(f"Bot started PID={proc.pid}")

        # Wait for heartbeat
        for i in range(15):
            time.sleep(1)
            age = get_heartbeat_age()
            if age < 30:
                log(f"Bot healthy, HB age={age:.1f}s")
                return proc.pid
            if i % 5 == 0:
                log(f"Waiting for heartbeat... ({i}s)")

        log("WARNING: No heartbeat after 15s")
        return proc.pid

    except Exception as e:
        log(f"ERROR starting: {e}")
        return None


def record_restart(reason: str) -> None:
    """Log restart event."""
    try:
        RESTART_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": datetime.now(timezone.utc).isoformat(), "reason": reason}
        with open(RESTART_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def count_recent_restarts() -> int:
    """Count restarts in last hour."""
    try:
        if not RESTART_LOG.exists():
            return 0

        cutoff = datetime.now(timezone.utc).timestamp() - 3600
        count = 0

        for line in RESTART_LOG.read_text(encoding="utf-8").splitlines()[-50:]:
            try:
                entry = json.loads(line)
                ts_str = entry.get("ts", "").replace("Z", "+00:00")
                ts = datetime.fromisoformat(ts_str).timestamp()
                if ts > cutoff:
                    count += 1
            except Exception:
                pass
        return count
    except Exception:
        return 0


def main_loop() -> None:
    """Main watchdog loop."""
    log("=" * 60)
    log("HOPE TG BOT WATCHDOG v1.0")
    log(f"Monitoring: {HEALTH_FILE}")
    log(f"Stale threshold: {STALE_THRESHOLD_SEC}s")
    log(f"Check interval: {CHECK_INTERVAL_SEC}s")
    log("=" * 60)

    while True:
        try:
            # Rate limit restarts
            recent = count_recent_restarts()
            if recent >= MAX_RESTARTS_PER_HOUR:
                log(f"Too many restarts ({recent}/h). Pausing 10min...")
                time.sleep(600)
                continue

            pid = get_bot_pid()
            hb_age = get_heartbeat_age()

            # Case 1: No PID
            if pid is None:
                log("No bot PID, starting...")
                record_restart("no_pid")
                start_bot()
                time.sleep(CHECK_INTERVAL_SEC)
                continue

            # Case 2: Process dead
            if not is_process_alive(pid):
                log(f"Bot PID {pid} dead, restarting...")
                record_restart("process_dead")
                if LOCK_FILE.exists():
                    LOCK_FILE.unlink(missing_ok=True)
                start_bot()
                time.sleep(CHECK_INTERVAL_SEC)
                continue

            # Case 3: Heartbeat stale
            if hb_age > STALE_THRESHOLD_SEC:
                log(f"HEARTBEAT STALE ({hb_age:.0f}s > {STALE_THRESHOLD_SEC}s)")
                record_restart(f"stale_hb_{hb_age:.0f}s")
                kill_bot(pid)
                time.sleep(3)
                start_bot()
                time.sleep(CHECK_INTERVAL_SEC)
                continue

            # All OK
            log(f"OK: PID={pid}, HB={hb_age:.1f}s")

        except KeyboardInterrupt:
            log("Watchdog stopped by user")
            break
        except Exception as e:
            log(f"ERROR: {e}")

        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main_loop()
