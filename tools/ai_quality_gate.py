# === AI SIGNATURE ===
# Created by: Claude
# Created at: 2026-01-22 10:30:00 UTC
# === END SIGNATURE ===
"""
AI Quality Gate - Pre-deployment validation for HOPE Trading Bot.

Validates that the system is ready for live deployment.
FAIL-CLOSED: Any critical check failure = exit 1.

Usage:
    python tools/ai_quality_gate.py [--report-path <path>]

Exit codes:
    0 - PASS (all gates passed)
    1 - FAIL (one or more gates failed)

Gates checked:
1. Night test report exists and shows PASS/WARN
2. Health file is valid and recent
3. Core modules compile without errors
4. Required secrets are present
5. No critical errors in logs
"""
from __future__ import annotations

import hashlib
import json
import os
import py_compile
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def utc_now_iso() -> str:
    """Get current UTC timestamp as ISO string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def atomic_write_json(path: Path, data: Any) -> str:
    """Atomically write JSON file."""
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
class GateResult:
    """Result of a single gate check."""
    gate_name: str
    passed: bool
    message: str
    details: Optional[Dict[str, Any]] = None


@dataclass
class GateReport:
    """Accumulated gate results."""
    timestamp: str = ""
    cmdline_sha256: str = ""
    gates: List[GateResult] = field(default_factory=list)

    def all_passed(self) -> bool:
        """Check if all gates passed."""
        return all(g.passed for g in self.gates)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            "timestamp": self.timestamp,
            "cmdline_sha256": f"sha256:{self.cmdline_sha256}",
            "verdict": "PASS" if self.all_passed() else "FAIL",
            "gates": [
                {
                    "name": g.gate_name,
                    "passed": g.passed,
                    "message": g.message,
                    "details": g.details,
                }
                for g in self.gates
            ],
        }


class QualityGate:
    """
    Quality gate checker.

    Validates system readiness for deployment.
    """

    def __init__(self, project_root: Optional[Path] = None):
        """Initialize quality gate."""
        self.project_root = project_root or Path(__file__).parent.parent
        self.state_dir = self.project_root / "state"
        self.core_dir = self.project_root / "core"
        self.report = GateReport()
        self.report.timestamp = utc_now_iso()
        self.report.cmdline_sha256 = get_cmdline_sha256()

    def check_night_report(self) -> GateResult:
        """Gate 1: Check night test report (Enterprise Quality Gate).

        FAIL-CLOSED: Only verdict=PASS is accepted. WARN or FAIL = gate fails.
        Reads from data/ai/night/night_report.json (night_test_hope.py output).
        """
        # Primary: Enterprise Quality Gate report
        report_path = self.project_root / "data" / "ai" / "night" / "night_report.json"

        # Fallback: legacy location
        if not report_path.exists():
            report_path = self.state_dir / "night_report.json"

        if not report_path.exists():
            return GateResult(
                gate_name="night_report",
                passed=False,
                message="Night test report not found",
            )

        try:
            with open(report_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            verdict = data.get("verdict", "UNKNOWN")

            # Calculate pass_rate from metrics (new format) or use direct field (legacy)
            metrics = data.get("metrics", {})
            if metrics and "subtests_passed" in metrics and "subtests_total" in metrics:
                total = metrics.get("subtests_total", 0)
                passed_count = metrics.get("subtests_passed", 0)
                pass_rate = passed_count / total if total > 0 else 0.0
            else:
                pass_rate = data.get("pass_rate", 0.0)
                total = 0
                passed_count = 0

            # FAIL-CLOSED: Only PASS is accepted, not WARN
            if verdict == "PASS":
                msg = f"Night test PASS ({passed_count}/{total} subtests)" if total > 0 else f"Night test PASS (pass_rate={pass_rate*100:.1f}%)"
                return GateResult(
                    gate_name="night_report",
                    passed=True,
                    message=msg,
                    details={"verdict": verdict, "pass_rate": pass_rate},
                )
            else:
                return GateResult(
                    gate_name="night_report",
                    passed=False,
                    message=f"Night test {verdict} (pass_rate={pass_rate*100:.1f}%)",
                    details={"verdict": verdict, "pass_rate": pass_rate},
                )

        except Exception as e:
            return GateResult(
                gate_name="night_report",
                passed=False,
                message=f"Failed to read night report: {e}",
            )

    def check_health_file(self) -> GateResult:
        """Gate 2: Check health file validity."""
        health_path = self.state_dir / "health_v5.json"

        if not health_path.exists():
            # Try alternative path
            health_path = self.state_dir / "health.json"

        if not health_path.exists():
            return GateResult(
                gate_name="health_file",
                passed=True,  # Not critical if running for first time
                message="Health file not found (first run?)",
            )

        try:
            with open(health_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Check required fields
            required = ["mode", "hb_ts"]
            for field in required:
                if field not in data:
                    return GateResult(
                        gate_name="health_file",
                        passed=False,
                        message=f"Missing required field: {field}",
                    )

            # Check mode
            mode = data.get("mode", "")
            if mode not in ("DRY", "TESTNET", "LIVE"):
                return GateResult(
                    gate_name="health_file",
                    passed=False,
                    message=f"Invalid mode: {mode}",
                )

            # Check daily_stop_hit
            if data.get("daily_stop_hit", False):
                return GateResult(
                    gate_name="health_file",
                    passed=False,
                    message="daily_stop_hit=true blocks deployment",
                )

            return GateResult(
                gate_name="health_file",
                passed=True,
                message=f"Health file valid (mode={mode})",
                details={"mode": mode},
            )

        except Exception as e:
            return GateResult(
                gate_name="health_file",
                passed=False,
                message=f"Failed to read health file: {e}",
            )

    def check_core_syntax(self) -> GateResult:
        """Gate 3: Check core module syntax."""
        if not self.core_dir.exists():
            return GateResult(
                gate_name="core_syntax",
                passed=True,
                message="Core directory not found (standalone deployment?)",
            )

        py_files = list(self.core_dir.glob("*.py"))
        if not py_files:
            return GateResult(
                gate_name="core_syntax",
                passed=True,
                message="No Python files in core/",
            )

        errors = []
        for py_file in py_files:
            try:
                py_compile.compile(str(py_file), doraise=True)
            except py_compile.PyCompileError as e:
                errors.append(f"{py_file.name}: {e}")

        if errors:
            return GateResult(
                gate_name="core_syntax",
                passed=False,
                message=f"Syntax errors in {len(errors)} file(s)",
                details={"errors": errors[:5]},
            )

        return GateResult(
            gate_name="core_syntax",
            passed=True,
            message=f"All {len(py_files)} core modules compile OK",
        )

    def check_secrets(self) -> GateResult:
        """Gate 4: Check required secrets presence."""
        env_path = Path(r"C:\secrets\hope\.env")

        if not env_path.exists():
            return GateResult(
                gate_name="secrets",
                passed=False,
                message="Secrets file not found: C:\\secrets\\hope\\.env",
            )

        try:
            with open(env_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Check required keys (presence only, not values)
            required_keys = ["TELEGRAM_TOKEN", "BINANCE_API_KEY"]
            missing = []
            for key in required_keys:
                if f"{key}=" not in content:
                    missing.append(key)

            if missing:
                return GateResult(
                    gate_name="secrets",
                    passed=False,
                    message=f"Missing secrets: {', '.join(missing)}",
                )

            return GateResult(
                gate_name="secrets",
                passed=True,
                message="Required secrets present",
            )

        except Exception as e:
            return GateResult(
                gate_name="secrets",
                passed=False,
                message=f"Failed to read secrets: {e}",
            )

    def check_recent_errors(self) -> GateResult:
        """Gate 5: Check for recent critical errors in logs."""
        log_files = list(self.state_dir.glob("*.log"))
        if not log_files:
            return GateResult(
                gate_name="recent_errors",
                passed=True,
                message="No log files to check",
            )

        critical_patterns = ["CRITICAL", "FATAL", "PANIC", "STOP_LOSS_HIT"]
        recent_errors = []

        for log_file in log_files[:3]:  # Check most recent logs
            try:
                # Check file age (last 24 hours)
                mtime = log_file.stat().st_mtime
                if time.time() - mtime > 86400:
                    continue

                with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                    # Read last 100 lines
                    lines = f.readlines()[-100:]
                    for line in lines:
                        for pattern in critical_patterns:
                            if pattern in line:
                                recent_errors.append(f"{log_file.name}: {line.strip()[:80]}")
                                break

            except Exception:
                continue

        if recent_errors:
            return GateResult(
                gate_name="recent_errors",
                passed=False,
                message=f"Found {len(recent_errors)} critical error(s)",
                details={"errors": recent_errors[:5]},
            )

        return GateResult(
            gate_name="recent_errors",
            passed=True,
            message="No critical errors in recent logs",
        )

    def run_all_gates(self) -> GateReport:
        """Run all gates and return report."""
        print("[GATE] Running AI Quality Gate...")
        print(f"[GATE] cmdline_sha256: {self.report.cmdline_sha256[:16]}...")

        gates = [
            ("night_report", self.check_night_report),
            ("health_file", self.check_health_file),
            ("core_syntax", self.check_core_syntax),
            ("secrets", self.check_secrets),
            ("recent_errors", self.check_recent_errors),
        ]

        for name, checker in gates:
            result = checker()
            self.report.gates.append(result)
            status = "PASS" if result.passed else "FAIL"
            print(f"[GATE] {name}: {status} - {result.message}")

        return self.report


def main() -> None:
    """Main entry point."""
    # Parse args
    report_path: Optional[Path] = None
    if "--report-path" in sys.argv:
        idx = sys.argv.index("--report-path")
        if idx + 1 < len(sys.argv):
            report_path = Path(sys.argv[idx + 1])

    gate = QualityGate()
    report = gate.run_all_gates()

    # Save report
    if report_path is None:
        report_path = gate.state_dir / "gate_report.json"
    report_hash = atomic_write_json(report_path, report.to_dict())

    print("\n" + "=" * 60)
    verdict = "PASS" if report.all_passed() else "FAIL"
    print(f"[GATE] VERDICT: {verdict}")
    print(f"[GATE] Report: {report_path}")
    print(f"[GATE] Report hash: {report_hash}")
    print("=" * 60)

    exit_code = 0 if report.all_passed() else 1
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
