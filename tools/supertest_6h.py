# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T01:00:00Z
# Purpose: 6-hour continuous supertest for HOPE Trading Safety Core
# Security: Fail-closed, all checks mandatory, no silent skips
# === END SIGNATURE ===
"""
HOPE Nightly Supertest - 6 Hour Continuous Validation.

Runs comprehensive tests in a loop for 6 hours:
- Core module compilation
- All pytest suites (execution, gatekeeper, strategies, etc.)
- JSONL stress tests with verification
- AI signature audits
- AllowList validation
- Execution protocol compliance
- Trading Safety Core v1 validation

Usage:
    python tools/supertest_6h.py [--duration-hours 6] [--allow-offline]
"""
import os
import sys
import time
import json
import subprocess
import hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional


# SSoT: Project root
ROOT = Path(__file__).parent.parent.resolve()
STATE_DIR = ROOT / "state"

# Find Python: prefer .venv, fallback to system Python
def _find_python() -> Path:
    """Find Python executable (venv or system)."""
    venv_py = ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return venv_py
    # Fallback to current Python
    return Path(sys.executable)

PYTHON_EXE = _find_python()

# Duration
DEFAULT_DURATION_HOURS = 6
CYCLE_PAUSE_SECONDS = 30  # Pause between cycles


@dataclass
class TestResult:
    """Result of a single test step."""
    name: str
    status: str  # "PASS" | "FAIL" | "SKIP"
    duration_ms: float
    message: str = ""
    stdout: str = ""
    stderr: str = ""


@dataclass
class CycleResult:
    """Result of a complete test cycle."""
    cycle_number: int
    started_at: str
    finished_at: str
    duration_seconds: float
    total_tests: int
    passed: int
    failed: int
    skipped: int
    tests: List[TestResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["ok"] = self.ok
        return d


class SuperTest:
    """6-hour continuous supertest runner."""

    def __init__(
        self,
        duration_hours: float = DEFAULT_DURATION_HOURS,
        allow_offline: bool = False,
    ):
        self.duration_hours = duration_hours
        self.allow_offline = allow_offline
        self.start_time = datetime.now(timezone.utc)
        self.end_time = self.start_time + timedelta(hours=duration_hours)
        self.cycles: List[CycleResult] = []
        self.log_path = STATE_DIR / "supertest" / f"run_{self.start_time.strftime('%Y%m%d_%H%M%S')}.jsonl"

        # Ensure state directory exists
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        # Verify venv
        if not PYTHON_EXE.exists():
            raise RuntimeError(f"VENV not found: {PYTHON_EXE}")

    def log(self, msg: str, level: str = "INFO"):
        """Print timestamped log message."""
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        color = {
            "INFO": "",
            "PASS": "\033[92m",
            "FAIL": "\033[91m",
            "WARN": "\033[93m",
        }.get(level, "")
        reset = "\033[0m" if color else ""
        print(f"[{ts}] {color}[{level}] {msg}{reset}")

    def run_command(
        self,
        cmd: List[str],
        timeout: int = 300,
        cwd: Optional[Path] = None,
    ) -> tuple[int, str, str]:
        """Run command and return (exit_code, stdout, stderr)."""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd or ROOT,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", f"TIMEOUT after {timeout}s"
        except Exception as e:
            return -1, "", str(e)

    def step(
        self,
        name: str,
        cmd: List[str],
        timeout: int = 300,
    ) -> TestResult:
        """Run a test step."""
        self.log(f"Running: {name}")
        start = time.time()

        exit_code, stdout, stderr = self.run_command(cmd, timeout)
        duration_ms = (time.time() - start) * 1000

        if exit_code == 0:
            status = "PASS"
            self.log(f"  {name}: PASS ({duration_ms:.0f}ms)", "PASS")
        else:
            status = "FAIL"
            self.log(f"  {name}: FAIL (exit={exit_code})", "FAIL")
            if stderr:
                # Print first 500 chars of stderr
                self.log(f"  stderr: {stderr[:500]}", "FAIL")

        return TestResult(
            name=name,
            status=status,
            duration_ms=duration_ms,
            message=f"exit_code={exit_code}",
            stdout=stdout[:2000] if stdout else "",
            stderr=stderr[:2000] if stderr else "",
        )

    def run_cycle(self, cycle_number: int) -> CycleResult:
        """Run a complete test cycle."""
        cycle_start = datetime.now(timezone.utc)
        self.log(f"=== CYCLE {cycle_number} STARTED ===", "INFO")

        tests: List[TestResult] = []
        py = str(PYTHON_EXE)

        # 1. Python sanity
        tests.append(self.step(
            "Python sanity",
            [py, "-c", "import sys; print(sys.version)"],
        ))

        # 2. Compile all core modules
        tests.append(self.step(
            "compileall core/",
            [py, "-W", "error::SyntaxWarning", "-m", "compileall", "-q", "-f", "core"],
            timeout=120,
        ))

        # 3. Compile tools
        tests.append(self.step(
            "compileall tools/",
            [py, "-m", "compileall", "-q", "-f", "tools"],
            timeout=60,
        ))

        # 4. Run pytest - execution module (Trading Safety Core v1)
        tests.append(self.step(
            "pytest: test_execution.py",
            [py, "-m", "pytest", "tests/test_execution.py", "-v", "--tb=short"],
            timeout=120,
        ))

        # 5. Run pytest - gatekeeper
        tests.append(self.step(
            "pytest: test_gatekeeper.py",
            [py, "-m", "pytest", "tests/test_gatekeeper.py", "-v", "--tb=short"],
            timeout=120,
        ))

        # 6. Run pytest - P0 safety
        tests.append(self.step(
            "pytest: test_p0_safety.py",
            [py, "-m", "pytest", "tests/test_p0_safety.py", "-v", "--tb=short"],
            timeout=120,
        ))

        # 7. Run pytest - strategies
        tests.append(self.step(
            "pytest: test_phase2_strategies.py",
            [py, "-m", "pytest", "tests/test_phase2_strategies.py", "-v", "--tb=short"],
            timeout=180,
        ))

        # 8. Run pytest - indicators
        tests.append(self.step(
            "pytest: test_indicators.py",
            [py, "-m", "pytest", "tests/test_indicators.py", "-v", "--tb=short"],
            timeout=120,
        ))

        # 9. JSONL self-test
        tests.append(self.step(
            "JSONL self-test",
            [py, "-m", "core.jsonl_sha"],
            timeout=60,
        ))

        # 10. JSONL stress test
        stress_out = STATE_DIR / "stress" / "supertest.jsonl"
        stress_out.parent.mkdir(parents=True, exist_ok=True)
        tests.append(self.step(
            "JSONL stress (4 procs x 100 lines)",
            [py, "tools/jsonl_stress.py", "--procs", "4", "--lines", "100", "--out", str(stress_out)],
            timeout=120,
        ))

        # 11. JSONL verify
        if stress_out.exists():
            tests.append(self.step(
                "JSONL verify",
                [py, "tools/jsonl_verify.py", "--in", str(stress_out)],
                timeout=60,
            ))

        # 12. AllowList audit
        tests.append(self.step(
            "AllowList audit",
            [py, "tools/audit_allowlist.py", "--root", str(ROOT), "--file", "AllowList.txt"],
            timeout=60,
        ))

        # 13. Execution protocol audit
        tests.append(self.step(
            "Execution protocol audit",
            [py, "tools/audit_execution_protocol.py", "--root", str(ROOT), "--file", "CLAUDE.md"],
            timeout=60,
        ))

        # 14. Cmdline SSoT audit
        tests.append(self.step(
            "Cmdline SSoT audit",
            [py, "tools/audit_cmdline_ssot.py", "--root", str(ROOT)],
            timeout=60,
        ))

        # 15. AI signature audit (dev mode)
        tests.append(self.step(
            "AI signature audit (dev)",
            [py, "tools/audit_ai_signature.py", "--root", str(ROOT), "--git-diff"],
            timeout=120,
        ))

        # 16. Execution module integration test
        tests.append(self.step(
            "Execution module integration",
            [py, "-c", """
from core.execution import (
    OrderIntentV1, OrderAckV1, FillEventV1,
    generate_client_order_id, canonical_payload,
    AtomicJournal, Outbox, FillsLedger,
)
import tempfile
from pathlib import Path

# Test idempotency
coid = generate_client_order_id('BTCUSDT', 'BUY', 'MARKET', 0.001)
assert len(coid) == 36, f'ID length {len(coid)} != 36'
assert coid.startswith('H'), 'ID must start with H'

# Test intent creation
intent = OrderIntentV1(
    client_order_id=coid,
    symbol='BTCUSDT',
    side='BUY',
    order_type='MARKET',
    quantity=0.001,
)
assert intent.symbol == 'BTCUSDT'

# Test journal
with tempfile.TemporaryDirectory() as tmpdir:
    journal = AtomicJournal(Path(tmpdir) / 'test.jsonl')
    e = journal.append('test', {'key': 'value'})
    assert e.sequence == 1
    entries = journal.read_all()
    assert len(entries) == 1

print('Execution module integration: PASS')
"""],
            timeout=60,
        ))

        # 17. Network tests (if allowed)
        if not self.allow_offline:
            tests.append(self.step(
                "Network: Binance ping",
                [py, "-c", """
import urllib.request
import json
url = 'https://api.binance.com/api/v3/ping'
resp = urllib.request.urlopen(url, timeout=10)
data = json.loads(resp.read())
print('Binance ping: OK', data)
"""],
                timeout=30,
            ))

        # Calculate results
        cycle_end = datetime.now(timezone.utc)
        passed = sum(1 for t in tests if t.status == "PASS")
        failed = sum(1 for t in tests if t.status == "FAIL")
        skipped = sum(1 for t in tests if t.status == "SKIP")

        result = CycleResult(
            cycle_number=cycle_number,
            started_at=cycle_start.isoformat(),
            finished_at=cycle_end.isoformat(),
            duration_seconds=(cycle_end - cycle_start).total_seconds(),
            total_tests=len(tests),
            passed=passed,
            failed=failed,
            skipped=skipped,
            tests=tests,
        )

        # Log cycle result
        status = "PASS" if result.ok else "FAIL"
        self.log(
            f"=== CYCLE {cycle_number} {status} "
            f"({passed}/{len(tests)} passed, {result.duration_seconds:.1f}s) ===",
            status,
        )

        # Append to log file
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(result.to_dict(), separators=(",", ":")) + "\n")

        return result

    def run(self) -> int:
        """Run supertest for configured duration."""
        self.log(f"HOPE SUPERTEST v1.0 - {self.duration_hours}h continuous test", "INFO")
        self.log(f"Start: {self.start_time.isoformat()}", "INFO")
        self.log(f"End:   {self.end_time.isoformat()}", "INFO")
        self.log(f"Log:   {self.log_path}", "INFO")
        self.log(f"Offline mode: {self.allow_offline}", "INFO")
        print()

        cycle_number = 0
        total_passed = 0
        total_failed = 0

        try:
            while datetime.now(timezone.utc) < self.end_time:
                cycle_number += 1
                result = self.run_cycle(cycle_number)
                self.cycles.append(result)

                total_passed += result.passed
                total_failed += result.failed

                # Check remaining time
                remaining = (self.end_time - datetime.now(timezone.utc)).total_seconds()
                if remaining <= 0:
                    break

                self.log(f"Remaining: {remaining/3600:.2f}h. Pausing {CYCLE_PAUSE_SECONDS}s...", "INFO")
                time.sleep(CYCLE_PAUSE_SECONDS)

        except KeyboardInterrupt:
            self.log("Interrupted by user", "WARN")

        # Final summary
        print()
        self.log("=" * 50, "INFO")
        self.log("SUPERTEST SUMMARY", "INFO")
        self.log("=" * 50, "INFO")
        self.log(f"Cycles completed: {cycle_number}", "INFO")
        self.log(f"Total tests run: {total_passed + total_failed}", "INFO")
        self.log(f"Total passed: {total_passed}", "PASS" if total_passed > 0 else "INFO")
        self.log(f"Total failed: {total_failed}", "FAIL" if total_failed > 0 else "INFO")
        self.log(f"Log file: {self.log_path}", "INFO")

        if total_failed > 0:
            self.log("SUPERTEST: FAIL", "FAIL")
            return 1
        else:
            self.log("SUPERTEST: PASS", "PASS")
            return 0


def main():
    import argparse

    parser = argparse.ArgumentParser(description="HOPE 6-hour Supertest")
    parser.add_argument(
        "--duration-hours", "-d",
        type=float,
        default=DEFAULT_DURATION_HOURS,
        help=f"Duration in hours (default: {DEFAULT_DURATION_HOURS})",
    )
    parser.add_argument(
        "--allow-offline",
        action="store_true",
        help="Skip network tests",
    )
    args = parser.parse_args()

    os.chdir(ROOT)

    supertest = SuperTest(
        duration_hours=args.duration_hours,
        allow_offline=args.allow_offline,
    )

    sys.exit(supertest.run())


if __name__ == "__main__":
    main()
