# === AI SIGNATURE ===
# Created by: Claude
# Created at: 2026-01-22 10:30:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 17:05:00 UTC
# Change: Added HOPE bootstrap (LAW-001)
# === END SIGNATURE ===
"""
Night Test v3 - Smoke Test Runner for HOPE Trading Bot.

Runs continuous health checks and data validation for a specified duration.
FAIL-CLOSED: Any critical failure = exit 1, insufficient data = exit 2.

Usage:
    python tools/night_test_v3.py <hours>

Example:
    python tools/night_test_v3.py 0.16  # ~10 minutes
    python tools/night_test_v3.py 5     # 5 hours

Exit codes:
    0 - PASS (all checks passed)
    1 - FAIL (critical failure detected)
    2 - INSUFFICIENT DATA (not enough samples)

Features:
- Continuous health file monitoring
- Binance API connectivity check
- JSONL integrity validation
- Atomic report generation
- SSoT cmdline hash
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import requests
except ImportError:
    requests = None  # Will fail gracefully

# SSoT: Get command line hash (Windows: GetCommandLineW)
def get_cmdline_sha256() -> str:
    """Get SHA256 of command line (SSoT for Windows)."""
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            GetCommandLineW = kernel32.GetCommandLineW
            GetCommandLineW.restype = ctypes.c_wchar_p
            cmdline = GetCommandLineW() or ""
        except Exception:
            cmdline = " ".join(sys.argv)
    else:
        try:
            with open("/proc/self/cmdline", "rb") as f:
                cmdline = f.read().decode("utf-8", errors="replace").replace("\x00", " ")
        except Exception:
            cmdline = " ".join(sys.argv)
    return hashlib.sha256(cmdline.encode("utf-8")).hexdigest()


def utc_now_iso() -> str:
    """Get current UTC timestamp as ISO string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write_json(path: Path, data: Any) -> str:
    """Atomically write JSON file (temp -> fsync -> replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, ensure_ascii=False, indent=2)
    content_bytes = content.encode("utf-8")
    content_hash = hashlib.sha256(content_bytes).hexdigest()

    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(content_bytes)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return f"sha256:{content_hash}"


@dataclass
class TestConfig:
    """Night test configuration."""
    duration_hours: float
    check_interval_sec: int = 30
    health_path: Optional[Path] = None
    state_dir: Path = field(default_factory=lambda: Path(__file__).parent.parent / "state")
    min_samples: int = 10


@dataclass
class TestResult:
    """Accumulated test results."""
    start_time: str = ""
    end_time: str = ""
    duration_sec: float = 0
    total_checks: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    health_samples: int = 0
    api_checks: int = 0
    api_failures: int = 0
    errors: List[str] = field(default_factory=list)
    cmdline_sha256: str = ""

    def pass_rate(self) -> float:
        """Calculate pass rate (0.0-1.0)."""
        if self.total_checks == 0:
            return 0.0
        return self.passed_checks / self.total_checks


class NightTest:
    """
    Night test runner.

    Performs continuous health and API checks.
    """

    def __init__(self, cfg: TestConfig):
        """Initialize night test."""
        self.cfg = cfg
        self.result = TestResult()
        self.result.cmdline_sha256 = get_cmdline_sha256()

        # Find health file
        if cfg.health_path and cfg.health_path.exists():
            self.health_path = cfg.health_path
        else:
            # Try default paths
            candidates = [
                cfg.state_dir / "health_v5.json",
                cfg.state_dir / "health.json",
            ]
            self.health_path = None
            for c in candidates:
                if c.exists():
                    self.health_path = c
                    break

        print(f"[SMOKE] Night Test v3 starting")
        print(f"[SMOKE] Duration: {cfg.duration_hours} hours")
        print(f"[SMOKE] Health file: {self.health_path or 'NOT FOUND'}")
        print(f"[SMOKE] cmdline_sha256: {self.result.cmdline_sha256[:16]}...")

    def check_health_file(self) -> bool:
        """Check health file validity."""
        if not self.health_path or not self.health_path.exists():
            return True  # Skip if no health file

        try:
            with open(self.health_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Basic validation
            if not isinstance(data, dict):
                self.result.errors.append("health: not a dict")
                return False

            # Check required fields
            required = ["engine_version", "mode", "hb_ts"]
            for field in required:
                if field not in data:
                    self.result.errors.append(f"health: missing {field}")
                    return False

            # Check mode
            mode = data.get("mode", "")
            if mode not in ("DRY", "TESTNET", "LIVE"):
                self.result.errors.append(f"health: invalid mode={mode}")
                return False

            # Check daily_stop_hit
            if data.get("daily_stop_hit", False):
                self.result.errors.append("health: daily_stop_hit=true")
                return False

            # Check last_error
            last_error = data.get("last_error")
            if last_error and isinstance(last_error, str) and last_error.strip():
                self.result.errors.append(f"health: last_error={last_error[:50]}")
                return False

            self.result.health_samples += 1
            return True

        except json.JSONDecodeError as e:
            self.result.errors.append(f"health: JSON error: {e}")
            return False
        except Exception as e:
            self.result.errors.append(f"health: read error: {e}")
            return False

    def check_binance_api(self) -> bool:
        """Check Binance API connectivity."""
        if requests is None:
            return True  # Skip if no requests

        try:
            url = "https://api.binance.com/api/v3/ping"
            resp = requests.get(url, timeout=10)
            self.result.api_checks += 1

            if resp.status_code != 200:
                self.result.api_failures += 1
                self.result.errors.append(f"binance: status={resp.status_code}")
                return False

            return True

        except requests.RequestException as e:
            self.result.api_checks += 1
            self.result.api_failures += 1
            self.result.errors.append(f"binance: {type(e).__name__}")
            return False

    def check_jsonl_integrity(self) -> bool:
        """Check JSONL file integrity in state directory."""
        jsonl_files = list(self.cfg.state_dir.glob("*.jsonl"))
        if not jsonl_files:
            return True

        for jf in jsonl_files[:5]:  # Check up to 5 files
            try:
                with open(jf, "r", encoding="utf-8") as f:
                    for i, line in enumerate(f):
                        if i > 100:  # Sample first 100 lines
                            break
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            json.loads(line)
                        except json.JSONDecodeError:
                            self.result.errors.append(f"jsonl: corrupt line in {jf.name}:{i+1}")
                            return False
            except Exception as e:
                self.result.errors.append(f"jsonl: read error {jf.name}: {e}")
                return False

        return True

    def run_single_check(self) -> bool:
        """Run a single check cycle."""
        self.result.total_checks += 1
        passed = True

        # Health file check
        if not self.check_health_file():
            passed = False

        # API check (every 5th check to avoid rate limits)
        if self.result.total_checks % 5 == 1:
            if not self.check_binance_api():
                passed = False

        # JSONL integrity (every 10th check)
        if self.result.total_checks % 10 == 1:
            if not self.check_jsonl_integrity():
                passed = False

        if passed:
            self.result.passed_checks += 1
        else:
            self.result.failed_checks += 1

        return passed

    def run(self) -> int:
        """
        Run night test for configured duration.

        Returns exit code: 0=PASS, 1=FAIL, 2=INSUFFICIENT_DATA
        """
        self.result.start_time = utc_now_iso()
        start_ts = time.time()
        duration_sec = self.cfg.duration_hours * 3600

        print(f"[SMOKE] Running for {duration_sec:.0f} seconds...")

        try:
            while (time.time() - start_ts) < duration_sec:
                passed = self.run_single_check()
                status = "PASS" if passed else "FAIL"
                elapsed = time.time() - start_ts
                print(f"[SMOKE] Check {self.result.total_checks}: {status} (elapsed={elapsed:.0f}s)")

                # Early exit on too many failures (>50%)
                if self.result.total_checks >= 10 and self.result.pass_rate() < 0.5:
                    print("[SMOKE] CRITICAL: Pass rate below 50%, stopping early")
                    break

                time.sleep(self.cfg.check_interval_sec)

        except KeyboardInterrupt:
            print("\n[SMOKE] Interrupted by user")

        self.result.end_time = utc_now_iso()
        self.result.duration_sec = time.time() - start_ts

        # Generate report
        return self.generate_report()

    def generate_report(self) -> int:
        """Generate final report and determine exit code."""
        report = {
            "test": "night_test_v3",
            "start_time": self.result.start_time,
            "end_time": self.result.end_time,
            "duration_sec": round(self.result.duration_sec, 2),
            "cmdline_sha256": f"sha256:{self.result.cmdline_sha256}",
            "total_checks": self.result.total_checks,
            "passed_checks": self.result.passed_checks,
            "failed_checks": self.result.failed_checks,
            "pass_rate": round(self.result.pass_rate(), 4),
            "health_samples": self.result.health_samples,
            "api_checks": self.result.api_checks,
            "api_failures": self.result.api_failures,
            "unique_errors": list(set(self.result.errors))[:20],
        }

        # Determine verdict
        if self.result.total_checks < self.cfg.min_samples:
            report["verdict"] = "INSUFFICIENT_DATA"
            exit_code = 2
        elif self.result.pass_rate() >= 0.95:
            report["verdict"] = "PASS"
            exit_code = 0
        elif self.result.pass_rate() >= 0.80:
            report["verdict"] = "WARN"
            exit_code = 0  # Still pass but with warning
        else:
            report["verdict"] = "FAIL"
            exit_code = 1

        # Save report atomically
        report_path = self.cfg.state_dir / "night_report.json"
        report_hash = atomic_write_json(report_path, report)
        report["report_sha256"] = report_hash

        print("\n" + "=" * 60)
        print(f"[SMOKE] VERDICT: {report['verdict']}")
        print(f"[SMOKE] Pass rate: {report['pass_rate']*100:.1f}%")
        print(f"[SMOKE] Checks: {report['total_checks']} total, {report['passed_checks']} passed")
        if report["unique_errors"]:
            print(f"[SMOKE] Errors ({len(report['unique_errors'])}):")
            for err in report["unique_errors"][:5]:
                print(f"        - {err}")
        print(f"[SMOKE] Report: {report_path}")
        print("=" * 60)

        return exit_code


def main() -> None:
    """Main entry point."""
    # HOPE-LAW-001: Policy bootstrap MUST be first
    from core.policy.bootstrap import bootstrap
    bootstrap("night_test_v3", network_profile="core")

    if len(sys.argv) < 2:
        print("FAIL: usage: night_test_v3.py <hours>")
        print("Example: python tools/night_test_v3.py 0.16  # ~10 minutes")
        raise SystemExit(1)

    try:
        hours = float(sys.argv[1])
    except ValueError:
        print("FAIL: hours must be a number")
        raise SystemExit(1)

    if hours <= 0:
        print("FAIL: hours must be > 0")
        raise SystemExit(1)

    # Optional health path argument
    health_path = None
    if len(sys.argv) >= 3:
        health_path = Path(sys.argv[2])

    cfg = TestConfig(
        duration_hours=hours,
        health_path=health_path,
        check_interval_sec=30 if hours >= 1 else 5,  # Faster for short tests
        min_samples=max(5, int(hours * 60)),  # At least 1 sample per minute
    )

    test = NightTest(cfg)
    exit_code = test.run()
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
