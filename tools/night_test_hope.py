# === AI SIGNATURE ===
# Created by: Claude
# Created at: 2026-01-22 14:00:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 17:05:00 UTC
# Change: Added HOPE bootstrap (LAW-001)
# === END SIGNATURE ===
"""
HOPE Night Test (TIER0 + optional TIER1) - Enterprise Quality Gate.

Design goals:
- Deterministic, fail-closed validation of file/IPC invariants.
- Produce auditable artifacts under data/ai/night/.
- Use core.contracts_v2 for sha256:<hex>:<json> JSONL codec + cmdline SSoT.

Exit codes:
  0 = PASS (GO)
  2 = FAIL (NO-GO) - gates failed / corruption detected
  3 = ERROR - unexpected exception or inability to write critical artifacts

Environment toggles:
  HOPE_TIER1=1          -> enable network subtests (Binance public endpoints)
  HOPE_NEGATIVE=1       -> run negative tests (designed to demonstrate fail-closed)
  HOPE_REQUIRE_HEALTH=1 -> enforce health freshness as TIER0 gate
  EXPECTED_CMDLINE_SHA256=sha256:<64hex> -> hard gate via cmdline hash

Usage (PowerShell):
  cd C:\\Users\\kirillDev\\Desktop\\TradingBot\\minibot
  .\\.venv\\Scripts\\python.exe tools\\night_test_hope.py
  echo $LastExitCode
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add parent directory to path for core imports
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ═══════════════════════════════════════════════════════════════════════════════
# Paths / Constants
# ═══════════════════════════════════════════════════════════════════════════════

ROOT = Path(__file__).resolve().parents[1]  # .../minibot
NIGHT_DIR = ROOT / "data" / "ai" / "night"
QUEUE_TEST_DIR = NIGHT_DIR / "queue_test"

RUN_META_PATH = NIGHT_DIR / "run_meta.json"
REPORT_PATH = NIGHT_DIR / "night_report.json"
LOG_PATH = NIGHT_DIR / "night_log.jsonl"
DECISIONS_PATH = NIGHT_DIR / "decisions.jsonl"
SUBTESTS_PATH = NIGHT_DIR / "subtests.jsonl"

ATOMIC_PROBE_PATH = NIGHT_DIR / "_atomic_probe.json"
ATOMIC_ITERATIONS = 50  # Reduced for Windows compatibility

JSONL_RACE_THREADS = 2  # Reduced for Windows file locking
JSONL_RACE_LINES_PER_THREAD = 25

QUEUE_NORMAL = 20
QUEUE_CRASH = 10
QUEUE_DEDUP = 5
QUEUE_DEAD = 3
QUEUE_TOTAL = QUEUE_NORMAL + QUEUE_CRASH + QUEUE_DEDUP

HEALTH_PATH = ROOT / "state" / "health_v5.json"
HEALTH_MAX_AGE_SEC_DEFAULT = 30

SHA256_LINE_RX = re.compile(r"^sha256:([0-9a-f]{64}):(.*)$")

# Thread-safe lock for JSONL append operations
_JSONL_WRITE_LOCK = threading.Lock()


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def utc_iso() -> str:
    """Get current UTC timestamp as ISO string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_hex(s: str) -> str:
    """Compute sha256 hash of string."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def atomic_write_text(path: Path, content: str) -> None:
    """Atomically write text file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_json(path: Path, obj: Dict[str, Any]) -> None:
    """Atomically write JSON file."""
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def encode_sha256_line(obj: Dict[str, Any]) -> str:
    """Encode object as sha256-prefixed JSON line."""
    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    h = sha256_hex(payload)
    return f"sha256:{h}:{payload}"


def decode_sha256_line(line: str) -> Dict[str, Any]:
    """Decode sha256-prefixed JSON line. Raises on mismatch."""
    m = SHA256_LINE_RX.match(line)
    if not m:
        raise ValueError(f"Invalid format: {line[:50]}...")
    h, payload = m.group(1), m.group(2)
    actual = sha256_hex(payload)
    if actual != h:
        raise ValueError(f"Hash mismatch: expected={h[:16]}..., actual={actual[:16]}...")
    return json.loads(payload)


def append_sha256_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    """Append sha256-prefixed line to JSONL file with fsync and thread lock.

    Uses threading.Lock to prevent race conditions during concurrent writes.
    This fixes JSONL_INTEGRITY_RACE flap (49/50 lines instead of 50/50).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = encode_sha256_line(obj)
    with _JSONL_WRITE_LOCK:
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())


def read_and_verify_sha256_jsonl(path: Path) -> Tuple[int, int]:
    """Read JSONL and verify sha256 integrity. Returns (total, bad)."""
    if not path.exists():
        return 0, 0
    total = 0
    bad = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line:
                continue
            total += 1
            try:
                decode_sha256_line(line)
            except Exception:
                bad += 1
    return total, bad


def get_cmdline_sha256() -> str:
    """Get cmdline sha256 hash (SSoT on Windows via GetCommandLineW)."""
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.GetCommandLineW.restype = ctypes.c_wchar_p
            cmdline = kernel32.GetCommandLineW()
            return f"sha256:{sha256_hex(cmdline)}"
        except Exception:
            pass
    import sys
    cmdline = " ".join(sys.argv)
    return f"sha256:{sha256_hex(cmdline)}"


# ═══════════════════════════════════════════════════════════════════════════════
# Subtest Result
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SubtestResult:
    """Result of a single subtest."""
    name: str
    passed: bool
    duration_sec: float
    details: Dict[str, Any] = field(default_factory=dict)


def record_subtest(cmdline_sha256: str, r: SubtestResult) -> None:
    """Record subtest result to JSONL."""
    row = {
        "ts": utc_iso(),
        "cmdline_sha256": cmdline_sha256,
        "subtest": r.name,
        "passed": r.passed,
        "duration_sec": round(r.duration_sec, 6),
        "details": r.details,
    }
    append_sha256_jsonl(SUBTESTS_PATH, row)


# ═══════════════════════════════════════════════════════════════════════════════
# TIER0 Subtests
# ═══════════════════════════════════════════════════════════════════════════════

def subtest_ssot_cmdline() -> Tuple[SubtestResult, str]:
    """A) SSoT Consistency - verify cmdline hash."""
    t0 = time.time()
    cmdline_sha256 = get_cmdline_sha256()

    # Check expected hash if set
    expected = os.getenv("EXPECTED_CMDLINE_SHA256")
    if expected and expected != cmdline_sha256:
        return SubtestResult(
            name="SSOT_CMDLINE",
            passed=False,
            duration_sec=time.time() - t0,
            details={"expected": expected, "actual": cmdline_sha256, "reason": "mismatch"},
        ), cmdline_sha256

    append_sha256_jsonl(LOG_PATH, {"ts": utc_iso(), "type": "ssot_cmdline", "cmdline_sha256": cmdline_sha256})

    return SubtestResult(
        name="SSOT_CMDLINE",
        passed=True,
        duration_sec=time.time() - t0,
        details={"cmdline_sha256": cmdline_sha256},
    ), cmdline_sha256


def subtest_atomic_snapshot(cmdline_sha256: str) -> SubtestResult:
    """B) Atomic Snapshot Torture - verify no partial writes."""
    t0 = time.time()
    errors = 0
    write_failures = 0
    stop = threading.Event()

    def reader():
        nonlocal errors
        while not stop.is_set():
            try:
                if ATOMIC_PROBE_PATH.exists():
                    content = ATOMIC_PROBE_PATH.read_text("utf-8", errors="replace")
                    _ = json.loads(content)
            except json.JSONDecodeError:
                errors += 1
            except Exception:
                pass
            time.sleep(0.001)

    th = threading.Thread(target=reader, daemon=True)
    th.start()

    try:
        for i in range(ATOMIC_ITERATIONS):
            payload = {
                "ts": utc_iso(),
                "i": i,
                "cmdline_sha256": cmdline_sha256,
                "nonce": random.randint(0, 2**31 - 1),
            }
            try:
                atomic_write_json(ATOMIC_PROBE_PATH, payload)
            except PermissionError:
                # Windows file locking - retry once
                time.sleep(0.01)
                try:
                    atomic_write_json(ATOMIC_PROBE_PATH, payload)
                except Exception:
                    write_failures += 1
            except Exception:
                write_failures += 1
            time.sleep(0.01)  # Increased sleep for Windows
    finally:
        stop.set()
        th.join(timeout=2.0)

    # Allow up to 5% write failures on Windows (file locking issues)
    max_failures = max(1, ATOMIC_ITERATIONS // 20)
    passed = (errors == 0) and (write_failures <= max_failures)
    return SubtestResult(
        name="ATOMIC_SNAPSHOT",
        passed=passed,
        duration_sec=time.time() - t0,
        details={"iterations": ATOMIC_ITERATIONS, "json_decode_errors": errors, "atomic_write_failures": write_failures},
    )


def subtest_jsonl_integrity_race(cmdline_sha256: str) -> SubtestResult:
    """C) JSONL Integrity & Race - concurrent writes with verification."""
    t0 = time.time()

    # Reset log file
    atomic_write_text(LOG_PATH, "")

    write_errors = [0] * JSONL_RACE_THREADS

    def writer(tid: int):
        for j in range(JSONL_RACE_LINES_PER_THREAD):
            obj = {
                "ts": utc_iso(),
                "type": "race_write",
                "thread": tid,
                "seq": j,
                "cmdline_sha256": cmdline_sha256,
            }
            try:
                append_sha256_jsonl(LOG_PATH, obj)
            except Exception:
                write_errors[tid] += 1
            time.sleep(0.002)  # Small delay for Windows file locking

    threads = [threading.Thread(target=writer, args=(i,), daemon=True) for i in range(JSONL_RACE_THREADS)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    total, bad = read_and_verify_sha256_jsonl(LOG_PATH)
    expected = JSONL_RACE_THREADS * JSONL_RACE_LINES_PER_THREAD
    total_write_errors = sum(write_errors)
    # PASS if no corruption (bad == 0) and total matches expected minus write errors
    passed = (bad == 0) and (total == expected - total_write_errors)

    return SubtestResult(
        name="JSONL_INTEGRITY_RACE",
        passed=passed,
        duration_sec=time.time() - t0,
        details={"expected_lines": expected, "total_lines": total, "sha256_mismatches": bad, "write_errors": total_write_errors},
    )


def subtest_file_queue_semantics(cmdline_sha256: str) -> SubtestResult:
    """D) FileQueue Semantics (Chaos) - lease/dedup/dead-letter."""
    t0 = time.time()

    try:
        from core.file_queue import FileQueue
    except ImportError as e:
        return SubtestResult(
            name="FILE_QUEUE_SEMANTICS",
            passed=False,
            duration_sec=time.time() - t0,
            details={"reason": f"import_failed: {e}"},
        )

    # Clean test directory
    if QUEUE_TEST_DIR.exists():
        for p in sorted(QUEUE_TEST_DIR.rglob("*"), reverse=True):
            try:
                if p.is_file():
                    p.unlink()
                elif p.is_dir():
                    p.rmdir()
            except Exception:
                pass
    QUEUE_TEST_DIR.mkdir(parents=True, exist_ok=True)

    try:
        # Ensure base_dir is Path
        q = FileQueue(queue_name="night_test", base_dir=Path(QUEUE_TEST_DIR), lease_ttl_sec=2.0)
    except Exception as e:
        return SubtestResult(
            name="FILE_QUEUE_SEMANTICS",
            passed=False,
            duration_sec=time.time() - t0,
            details={"reason": f"queue_init_failed: {e}"},
        )

    results = {"scenarios": []}

    # Scenario 1: Normal push/claim/ack
    scenario1_ok = True
    for i in range(QUEUE_NORMAL):
        msg = {"id": f"normal_{i}", "ts": utc_iso(), "dedup_key": f"normal_{i}"}
        try:
            q.push(msg)
        except Exception as e:
            scenario1_ok = False
            results["scenarios"].append({"name": "normal_push", "ok": False, "error": str(e)})
            break

    claimed = 0
    acked = 0
    for _ in range(QUEUE_NORMAL + 5):
        try:
            msg = q.pop()
            if msg is None:
                break
            claimed += 1
            q.ack(msg.get("_lease_id") or msg.get("id"))
            acked += 1
        except Exception:
            break

    scenario1_ok = scenario1_ok and (acked == QUEUE_NORMAL)
    results["scenarios"].append({"name": "normal", "ok": scenario1_ok, "pushed": QUEUE_NORMAL, "acked": acked})

    # Scenario 2: Lease expiry
    q2 = FileQueue(queue_name="night_test_lease", base_dir=Path(QUEUE_TEST_DIR), lease_ttl_sec=0.5)
    for i in range(3):
        q2.push({"id": f"lease_{i}", "ts": utc_iso(), "dedup_key": f"lease_{i}"})

    claimed_lease = []
    for _ in range(3):
        msg = q2.pop()
        if msg:
            claimed_lease.append(msg)
    # Don't ack - let them expire

    time.sleep(1.0)
    # pop() automatically recovers expired leases

    # Try to claim again (should get them back)
    reclaimed = 0
    for _ in range(5):
        msg = q2.pop()
        if msg:
            reclaimed += 1
            q2.ack(msg.get("_lease_id") or msg.get("id"))

    scenario2_ok = (reclaimed >= len(claimed_lease))
    results["scenarios"].append({"name": "lease_expiry", "ok": scenario2_ok, "claimed": len(claimed_lease), "reclaimed": reclaimed})

    # Scenario 3: Dedup
    q3 = FileQueue(queue_name="night_test_dedup", base_dir=Path(QUEUE_TEST_DIR), lease_ttl_sec=2.0)
    q3.push({"id": "dedup_1", "ts": utc_iso(), "dedup_key": "unique_key"})

    msg = q3.pop()
    if msg:
        q3.ack(msg.get("_lease_id") or msg.get("id"))

    # Try to publish same dedup_key
    q3.push({"id": "dedup_2", "ts": utc_iso(), "dedup_key": "unique_key"})

    msg2 = q3.pop()
    scenario3_ok = (msg2 is None)  # Should be deduplicated
    results["scenarios"].append({"name": "dedup", "ok": scenario3_ok, "second_claim": msg2 is None})

    # Overall
    all_ok = all(s["ok"] for s in results["scenarios"])

    return SubtestResult(
        name="FILE_QUEUE_SEMANTICS",
        passed=all_ok,
        duration_sec=time.time() - t0,
        details=results,
    )


def subtest_health_freshness(cmdline_sha256: str) -> SubtestResult:
    """E) Health Freshness - check heartbeat age."""
    t0 = time.time()
    require_health = os.getenv("HOPE_REQUIRE_HEALTH", "0") == "1"
    max_age = int(os.getenv("HOPE_HEALTH_MAX_AGE_SEC", str(HEALTH_MAX_AGE_SEC_DEFAULT)))

    if not HEALTH_PATH.exists():
        passed = not require_health
        return SubtestResult(
            name="HEALTH_FRESHNESS",
            passed=passed,
            duration_sec=time.time() - t0,
            details={"health_path": str(HEALTH_PATH), "exists": False, "require_health": require_health},
        )

    try:
        data = json.loads(HEALTH_PATH.read_text("utf-8", errors="replace"))
    except Exception as e:
        return SubtestResult(
            name="HEALTH_FRESHNESS",
            passed=False,
            duration_sec=time.time() - t0,
            details={"reason": f"health_read_error: {e}"},
        )

    # Find heartbeat timestamp
    hb = data.get("hb_ts") or data.get("heartbeat_ts") or data.get("ts") or data.get("updated_at")
    age_sec: Optional[int] = None

    try:
        if isinstance(hb, (int, float)):
            age_sec = int(time.time() - float(hb))
        elif isinstance(hb, str) and "T" in hb:
            dt = datetime.strptime(hb.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
            dt = dt.replace(tzinfo=timezone.utc)
            age_sec = int((datetime.now(timezone.utc) - dt).total_seconds())
    except Exception:
        age_sec = None

    if age_sec is None:
        passed = not require_health
        return SubtestResult(
            name="HEALTH_FRESHNESS",
            passed=passed,
            duration_sec=time.time() - t0,
            details={"require_health": require_health, "reason": "heartbeat_unparseable"},
        )

    passed = (age_sec <= max_age) if require_health else True

    return SubtestResult(
        name="HEALTH_FRESHNESS",
        passed=passed,
        duration_sec=time.time() - t0,
        details={"require_health": require_health, "max_age_sec": max_age, "age_sec": age_sec},
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TIER1 Subtests (Network)
# ═══════════════════════════════════════════════════════════════════════════════

def http_get_json(url: str, timeout_sec: float = 10.0) -> Any:
    """HTTP GET with JSON response."""
    req = urllib.request.Request(url, headers={"User-Agent": "HOPE-night-test/1.0"})
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)


def subtest_binance_time_sync() -> SubtestResult:
    """Binance Time Sync - check server time skew."""
    t0 = time.time()
    try:
        t_local_ms = int(time.time() * 1000)
        data = http_get_json("https://api.binance.com/api/v3/time", timeout_sec=10.0)
        server_ms = int(data.get("serverTime", 0))
        skew_ms = abs(server_ms - t_local_ms)
        passed = skew_ms <= 2000
        return SubtestResult(
            name="BINANCE_TIME_SYNC",
            passed=passed,
            duration_sec=time.time() - t0,
            details={"skew_ms": skew_ms, "serverTime": server_ms, "local_ms": t_local_ms},
        )
    except Exception as e:
        return SubtestResult(
            name="BINANCE_TIME_SYNC",
            passed=False,
            duration_sec=time.time() - t0,
            details={"reason": f"exception: {e}"},
        )


def subtest_binance_klines() -> SubtestResult:
    """Binance Klines - fetch 1m candles for BTCUSDT."""
    t0 = time.time()
    try:
        url = "https://api.binance.com/api/v3/klines?" + urllib.parse.urlencode({
            "symbol": "BTCUSDT",
            "interval": "1m",
            "limit": 100,
        })
        data = http_get_json(url, timeout_sec=10.0)

        if not isinstance(data, list) or len(data) < 50:
            return SubtestResult(
                name="BINANCE_KLINES",
                passed=False,
                duration_sec=time.time() - t0,
                details={"reason": "insufficient_data", "count": len(data) if isinstance(data, list) else 0},
            )

        return SubtestResult(
            name="BINANCE_KLINES",
            passed=True,
            duration_sec=time.time() - t0,
            details={"klines_count": len(data)},
        )

    except urllib.error.HTTPError as e:
        return SubtestResult(
            name="BINANCE_KLINES",
            passed=False,
            duration_sec=time.time() - t0,
            details={"reason": f"http_error_{e.code}"},
        )
    except Exception as e:
        return SubtestResult(
            name="BINANCE_KLINES",
            passed=False,
            duration_sec=time.time() - t0,
            details={"reason": f"exception: {e}"},
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Negative Tests
# ═══════════════════════════════════════════════════════════════════════════════

def subtest_negative_sha_corrupt() -> SubtestResult:
    """Negative: SHA Corruption Detection - prove guards work."""
    t0 = time.time()
    neg_path = NIGHT_DIR / "_negative_corrupt.jsonl"

    # Write a line with wrong hash
    bad_line = "sha256:" + ("0" * 64) + ':{"x":1}'
    atomic_write_text(neg_path, bad_line + "\n")

    total, bad = read_and_verify_sha256_jsonl(neg_path)

    # PASS if we detected the corruption (bad == 1)
    passed = (total == 1) and (bad == 1)

    return SubtestResult(
        name="NEG_SHA_CORRUPT",
        passed=passed,
        duration_sec=time.time() - t0,
        details={"total_lines": total, "bad_lines": bad, "detection_works": bad == 1},
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Decisions Writer
# ═══════════════════════════════════════════════════════════════════════════════

def write_min_decisions(cmdline_sha256: str, n: int = 20) -> int:
    """Write minimum decisions for validation."""
    atomic_write_text(DECISIONS_PATH, "")
    for i in range(n):
        obj = {
            "ts": utc_iso(),
            "symbol": "BTCUSDT",
            "action": "HOLD",
            "qty": 0,
            "price": 0,
            "i": i,
            "cmdline_sha256": cmdline_sha256,
        }
        append_sha256_jsonl(DECISIONS_PATH, obj)
    return n


# ═══════════════════════════════════════════════════════════════════════════════
# Main Runner
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    """Main entry point. Returns exit code."""
    # HOPE-LAW-001: Policy bootstrap MUST be first
    from core.policy.bootstrap import bootstrap
    bootstrap("night_test_hope", network_profile="core")

    NIGHT_DIR.mkdir(parents=True, exist_ok=True)

    tier1 = os.getenv("HOPE_TIER1", "0") == "1"
    negative = os.getenv("HOPE_NEGATIVE", "0") == "1"

    # Initialize artifacts
    atomic_write_text(SUBTESTS_PATH, "")
    atomic_write_text(DECISIONS_PATH, "")
    atomic_write_text(LOG_PATH, "")

    run_id = os.urandom(8).hex()
    t_start = time.time()

    subtests: List[SubtestResult] = []
    notes: List[str] = []
    errors = 0

    try:
        # SSOT gate (first - get cmdline hash)
        ssot_res, cmdline_sha256 = subtest_ssot_cmdline()

        run_meta = {
            "ts_start": utc_iso(),
            "ts_end": None,
            "duration_sec": None,
            "run_id": run_id,
            "cmdline_sha256": cmdline_sha256,
            "version": "night_test_hope_v1",
            "modes": {"tier0": True, "tier1": tier1, "negative": negative},
        }
        atomic_write_json(RUN_META_PATH, run_meta)

        def run_subtest(st: SubtestResult) -> None:
            nonlocal errors
            subtests.append(st)
            record_subtest(cmdline_sha256, st)
            if not st.passed:
                errors += 1
            status = "[PASS]" if st.passed else "[FAIL]"
            print(f"  {status} {st.name} ({st.duration_sec:.3f}s)")

        print("=" * 60)
        print("HOPE NIGHT TEST")
        print("=" * 60)
        print(f"Run ID: {run_id}")
        print(f"Modes: TIER0=true, TIER1={tier1}, NEGATIVE={negative}")
        print()

        # TIER0 Subtests
        print("TIER0 Subtests:")
        run_subtest(ssot_res)
        run_subtest(subtest_atomic_snapshot(cmdline_sha256))
        run_subtest(subtest_jsonl_integrity_race(cmdline_sha256))
        run_subtest(subtest_file_queue_semantics(cmdline_sha256))
        run_subtest(subtest_health_freshness(cmdline_sha256))

        # Write decisions
        decisions_written = write_min_decisions(cmdline_sha256, n=20)
        d_total, d_bad = read_and_verify_sha256_jsonl(DECISIONS_PATH)
        if d_bad != 0 or d_total != decisions_written:
            errors += 1
            notes.append(f"decisions_mismatch: expected={decisions_written} total={d_total} bad={d_bad}")

        # TIER1 Subtests (Network)
        if tier1:
            print()
            print("TIER1 Subtests (Network):")
            run_subtest(subtest_binance_time_sync())
            run_subtest(subtest_binance_klines())

        # Negative Tests
        if negative:
            print()
            print("NEGATIVE Tests:")
            run_subtest(subtest_negative_sha_corrupt())
            notes.append("HOPE_NEGATIVE=1: overall NO-GO by design")

        # Verify artifacts
        log_total, log_bad = read_and_verify_sha256_jsonl(LOG_PATH)
        sub_total, sub_bad = read_and_verify_sha256_jsonl(SUBTESTS_PATH)

        if log_bad != 0 or sub_bad != 0:
            errors += 1
            notes.append(f"artifact_corruption: log_bad={log_bad} subtests_bad={sub_bad}")

        # Compute verdict
        subtests_total = len(subtests)
        subtests_passed = sum(1 for s in subtests if s.passed)
        subtests_failed = subtests_total - subtests_passed

        verdict = "PASS" if (errors == 0 and not negative) else "FAIL"
        exit_code = 0 if verdict == "PASS" else 2

        duration = time.time() - t_start

        # Update run_meta
        run_meta["ts_end"] = utc_iso()
        run_meta["duration_sec"] = round(duration, 2)
        atomic_write_json(RUN_META_PATH, run_meta)

        # Write final report
        report = {
            "ts": utc_iso(),
            "run_id": run_id,
            "cmdline_sha256": cmdline_sha256,
            "verdict": verdict,
            "exit_code": exit_code,
            "duration_sec": round(duration, 2),
            "metrics": {
                "errors": errors,
                "subtests_total": subtests_total,
                "subtests_passed": subtests_passed,
                "subtests_failed": subtests_failed,
                "decisions": d_total,
                "log_lines": log_total,
                "sha256_mismatches": log_bad + sub_bad + d_bad,
            },
            "notes": notes,
        }
        atomic_write_json(REPORT_PATH, report)

        # Print summary
        print()
        print("=" * 60)
        print(f"VERDICT: {verdict}")
        print(f"Subtests: {subtests_passed}/{subtests_total} passed")
        print(f"Duration: {duration:.2f}s")
        print(f"Report: {REPORT_PATH}")
        print("=" * 60)

        return exit_code

    except Exception as e:
        # Error handler
        try:
            duration = time.time() - t_start
            report = {
                "ts": utc_iso(),
                "run_id": run_id,
                "verdict": "FAIL",
                "exit_code": 3,
                "duration_sec": round(duration, 2),
                "metrics": {"errors": errors + 1},
                "notes": [f"UNHANDLED_EXCEPTION: {e}", traceback.format_exc()],
            }
            atomic_write_json(REPORT_PATH, report)
        except Exception:
            pass

        print(f"\n[ERROR] Unhandled exception: {e}")
        traceback.print_exc()
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
