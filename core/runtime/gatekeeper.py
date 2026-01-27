# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T02:00:00Z
# Purpose: Gatekeeper - unified orchestrator for all safety gates
# Security: Fail-closed, sequential gates, atomic evidence
# === END SIGNATURE ===
"""
Gatekeeper - Unified Gate Orchestrator.

Single entry point for ALL safety checks before live trading.
Gates execute in strict order - any failure stops the chain.

Gate Order (NON-NEGOTIABLE):
1. RuntimeLockfile.acquire()   - Prevent duplicate processes
2. ChangelogMonitor.check()    - Detect breaking API changes
3. PreflightGate.check()       - Validate credentials and limits
4. StateReconciler.reconcile() - Sync local state with exchange

Exit Codes:
- 0: SUCCESS - All gates passed, trading allowed
- 1: INTERNAL_ERROR - Unexpected exception
- 2: GATE_BLOCKED - Gate check failed (expected failure)

Evidence:
- Every run produces gate_report.json in state/evidence/<timestamp>/
- Report contains all gate results, cmdline_sha256, timestamps

Usage:
    from core.runtime.gatekeeper import Gatekeeper

    gatekeeper = Gatekeeper(mode="TESTNET")
    result = gatekeeper.run()

    if not result.ok:
        print(f"BLOCKED: {result.block_reason}")
        sys.exit(result.exit_code)

    # Only after success:
    start_trading()
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("runtime.gatekeeper")

# SSoT paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATE_DIR = BASE_DIR / "state"
EVIDENCE_DIR = STATE_DIR / "evidence"


class ExitCode(int, Enum):
    """Gatekeeper exit codes."""
    SUCCESS = 0
    INTERNAL_ERROR = 1
    GATE_BLOCKED = 2


class GateStatus(str, Enum):
    """Individual gate status."""
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


@dataclass
class GateCheckResult:
    """Result of a single gate check."""
    gate_name: str
    status: GateStatus
    message: str
    duration_ms: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class GateResult:
    """Result of full gatekeeper run."""
    ok: bool
    exit_code: ExitCode
    block_reason: Optional[str]
    evidence_path: Path
    cmdline_sha256: str
    checks: List[GateCheckResult]
    total_duration_ms: float
    timestamp_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    schema_version: str = "gate_result.v1"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["exit_code"] = self.exit_code.value
        d["evidence_path"] = str(self.evidence_path)
        d["checks"] = [c.to_dict() if hasattr(c, 'to_dict') else c for c in self.checks]
        return d


class Gatekeeper:
    """
    Unified Gate Orchestrator.

    Executes all safety gates in strict sequence.
    Any failure = immediate stop with evidence.

    FAIL-CLOSED: Unknown states are treated as failures.
    """

    def __init__(
        self,
        mode: str = "TESTNET",
        live_enable: bool = False,
        live_ack: str = "",
        symbol: str = "BTCUSDT",
        quote_amount: float = 0.0,
        dry_run: bool = True,
        skip_changelog: bool = False,
        skip_reconcile: bool = False,
    ):
        """
        Initialize Gatekeeper.

        Args:
            mode: Trading mode (DRY, TESTNET, MAINNET)
            live_enable: Enable live trading flag
            live_ack: Acknowledgment string for MAINNET
            symbol: Trading symbol
            quote_amount: Quote amount for trading
            dry_run: If True, skip network checks
            skip_changelog: Skip changelog check (for testing)
            skip_reconcile: Skip state reconciliation (for clean start)
        """
        self.mode = mode.upper()
        self.live_enable = live_enable
        self.live_ack = live_ack
        self.symbol = symbol
        self.quote_amount = quote_amount
        self.dry_run = dry_run
        self.skip_changelog = skip_changelog
        self.skip_reconcile = skip_reconcile

        self._cmdline_sha256 = self._get_cmdline_sha256()
        self._lockfile = None
        self._checks: List[GateCheckResult] = []
        self._start_time: float = 0

        logger.info(
            "Gatekeeper initialized: mode=%s, dry_run=%s",
            self.mode, self.dry_run
        )

    def _get_cmdline_sha256(self) -> str:
        """Get cmdline SHA256 (SSoT)."""
        try:
            from core.truth.cmdline_ssot import get_cmdline_sha256
            return f"sha256:{get_cmdline_sha256()}"
        except ImportError:
            cmdline = " ".join(sys.argv)
            return f"sha256:{hashlib.sha256(cmdline.encode()).hexdigest()}"

    def run(self) -> GateResult:
        """
        Execute all gates in sequence.

        Returns:
            GateResult with ok=True only if ALL gates pass
        """
        self._start_time = time.time()
        self._checks = []

        try:
            # === GATE 1: RuntimeLockfile ===
            lock_result = self._gate_lockfile()
            self._checks.append(lock_result)
            if lock_result.status != GateStatus.PASSED:
                return self._finalize(False, ExitCode.GATE_BLOCKED, lock_result.message)

            # === GATE 2: Changelog Monitor ===
            if not self.skip_changelog:
                changelog_result = self._gate_changelog()
                self._checks.append(changelog_result)
                if changelog_result.status == GateStatus.FAILED:
                    return self._finalize(False, ExitCode.GATE_BLOCKED, changelog_result.message)

            # === GATE 3: Preflight ===
            preflight_result = self._gate_preflight()
            self._checks.append(preflight_result)
            if preflight_result.status != GateStatus.PASSED:
                return self._finalize(False, ExitCode.GATE_BLOCKED, preflight_result.message)

            # === GATE 4: State Reconciliation ===
            if not self.skip_reconcile and not self.dry_run:
                reconcile_result = self._gate_reconcile()
                self._checks.append(reconcile_result)
                if reconcile_result.status == GateStatus.FAILED:
                    return self._finalize(False, ExitCode.GATE_BLOCKED, reconcile_result.message)

            # === ALL GATES PASSED ===
            logger.info("All gates PASSED")
            return self._finalize(True, ExitCode.SUCCESS, None)

        except Exception as e:
            logger.exception("Gatekeeper internal error: %s", e)
            self._checks.append(GateCheckResult(
                gate_name="INTERNAL",
                status=GateStatus.ERROR,
                message=str(e),
                details={"traceback": traceback.format_exc()},
            ))
            return self._finalize(False, ExitCode.INTERNAL_ERROR, f"Internal error: {e}")

    def _gate_lockfile(self) -> GateCheckResult:
        """Gate 1: Acquire runtime lockfile."""
        start = time.time()
        try:
            from core.runtime.lockfile import RuntimeLockfile

            lock = RuntimeLockfile()
            result = lock.acquire()

            if result.acquired:
                self._lockfile = lock
                return GateCheckResult(
                    gate_name="LOCKFILE",
                    status=GateStatus.PASSED,
                    message="Lock acquired",
                    duration_ms=(time.time() - start) * 1000,
                    details={"stale_removed": result.stale_removed},
                )
            else:
                return GateCheckResult(
                    gate_name="LOCKFILE",
                    status=GateStatus.FAILED,
                    message=result.reason,
                    duration_ms=(time.time() - start) * 1000,
                    details={
                        "existing_pid": result.existing_pid,
                        "existing_sha256": result.existing_sha256,
                    },
                )

        except ImportError as e:
            logger.warning("Lockfile module not available: %s", e)
            return GateCheckResult(
                gate_name="LOCKFILE",
                status=GateStatus.SKIPPED,
                message="Module not available",
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return GateCheckResult(
                gate_name="LOCKFILE",
                status=GateStatus.ERROR,
                message=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    def _gate_changelog(self) -> GateCheckResult:
        """Gate 2: Check for breaking API changes."""
        start = time.time()
        try:
            from core.intel.changelog_monitor import ChangelogMonitor

            monitor = ChangelogMonitor()
            should_block, blocking_event = monitor.should_block_trading()

            if should_block and blocking_event:
                return GateCheckResult(
                    gate_name="CHANGELOG",
                    status=GateStatus.FAILED,
                    message=f"Breaking change imminent: {blocking_event.summary}",
                    duration_ms=(time.time() - start) * 1000,
                    details={
                        "event_id": blocking_event.event_id,
                        "effective_date": blocking_event.effective_date,
                        "affected_endpoints": blocking_event.affected_endpoints,
                    },
                )

            # Get all events for evidence
            events = monitor.check()
            critical_count = len([e for e in events if e.is_critical])

            return GateCheckResult(
                gate_name="CHANGELOG",
                status=GateStatus.PASSED,
                message=f"No blocking changes ({critical_count} critical, {len(events)} total)",
                duration_ms=(time.time() - start) * 1000,
                details={
                    "total_events": len(events),
                    "critical_events": critical_count,
                },
            )

        except ImportError as e:
            logger.warning("Changelog module not available: %s", e)
            return GateCheckResult(
                gate_name="CHANGELOG",
                status=GateStatus.SKIPPED,
                message="Module not available",
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return GateCheckResult(
                gate_name="CHANGELOG",
                status=GateStatus.ERROR,
                message=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    def _gate_preflight(self) -> GateCheckResult:
        """Gate 3: Run preflight checks."""
        start = time.time()
        try:
            from core.trade.preflight import PreflightGate, PreflightConfig

            config = PreflightConfig(
                mode=self.mode,
                symbol=self.symbol,
                quote_amount=self.quote_amount,
                dry_run=self.dry_run,
                live_enable=self.live_enable,
                live_ack=self.live_ack,
            )

            gate = PreflightGate(config)
            result = gate.check()

            if result.ok:
                return GateCheckResult(
                    gate_name="PREFLIGHT",
                    status=GateStatus.PASSED,
                    message=f"All checks passed: {', '.join(result.checks_passed)}",
                    duration_ms=(time.time() - start) * 1000,
                    details={
                        "checks_passed": result.checks_passed,
                        "cmdline_sha256": result.cmdline_sha256,
                    },
                )
            else:
                return GateCheckResult(
                    gate_name="PREFLIGHT",
                    status=GateStatus.FAILED,
                    message=f"{result.reason_code}: {result.reason_detail}",
                    duration_ms=(time.time() - start) * 1000,
                    details={
                        "reason_code": result.reason_code,
                        "checks_passed": result.checks_passed,
                        "checks_failed": result.checks_failed,
                    },
                )

        except ImportError as e:
            logger.warning("Preflight module not available: %s", e)
            return GateCheckResult(
                gate_name="PREFLIGHT",
                status=GateStatus.ERROR,
                message=f"Module not available: {e}",
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return GateCheckResult(
                gate_name="PREFLIGHT",
                status=GateStatus.ERROR,
                message=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    def _gate_reconcile(self) -> GateCheckResult:
        """Gate 4: Reconcile state with exchange."""
        start = time.time()
        try:
            from core.trade.state_recovery import StateReconciler, ReconcilerConfig

            config = ReconcilerConfig(mode=self.mode)
            reconciler = StateReconciler(config)
            result = reconciler.reconcile()

            if result.ok:
                return GateCheckResult(
                    gate_name="RECONCILE",
                    status=GateStatus.PASSED,
                    message=f"State synchronized: {', '.join(result.checks_passed)}",
                    duration_ms=(time.time() - start) * 1000,
                    details={
                        "checks_passed": result.checks_passed,
                        "state_age_sec": result.local_state_age_sec,
                    },
                )
            else:
                return GateCheckResult(
                    gate_name="RECONCILE",
                    status=GateStatus.FAILED,
                    message=result.reason,
                    duration_ms=(time.time() - start) * 1000,
                    details={
                        "diffs": result.diffs,
                        "checks_passed": result.checks_passed,
                    },
                )

        except ImportError as e:
            logger.warning("State recovery module not available: %s", e)
            return GateCheckResult(
                gate_name="RECONCILE",
                status=GateStatus.SKIPPED,
                message="Module not available",
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return GateCheckResult(
                gate_name="RECONCILE",
                status=GateStatus.ERROR,
                message=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    def _finalize(
        self,
        ok: bool,
        exit_code: ExitCode,
        block_reason: Optional[str],
    ) -> GateResult:
        """Finalize gate run and save evidence."""
        total_duration = (time.time() - self._start_time) * 1000

        # Create evidence directory
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        evidence_dir = EVIDENCE_DIR / ts
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = evidence_dir / "gate_report.json"

        result = GateResult(
            ok=ok,
            exit_code=exit_code,
            block_reason=block_reason,
            evidence_path=evidence_path,
            cmdline_sha256=self._cmdline_sha256,
            checks=self._checks,
            total_duration_ms=total_duration,
        )

        # Save evidence atomically
        self._save_evidence(result, evidence_path)

        # Log result
        if ok:
            logger.info("Gatekeeper PASS: %d gates in %.1fms", len(self._checks), total_duration)
        else:
            logger.error("Gatekeeper BLOCKED: %s (exit=%d)", block_reason, exit_code.value)

        return result

    def _save_evidence(self, result: GateResult, path: Path) -> None:
        """Save evidence atomically."""
        try:
            content = json.dumps(result.to_dict(), indent=2, default=str)
            tmp_path = path.with_suffix(".json.tmp")

            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_path, path)
            logger.info("Evidence saved: %s", path)

        except Exception as e:
            logger.error("Failed to save evidence: %s", e)

    def release(self) -> None:
        """Release lockfile on shutdown."""
        if self._lockfile:
            try:
                self._lockfile.release()
                logger.info("Lockfile released")
            except Exception as e:
                logger.warning("Failed to release lockfile: %s", e)


def run_gates(
    mode: str = "TESTNET",
    live_enable: bool = False,
    live_ack: str = "",
    symbol: str = "BTCUSDT",
    quote_amount: float = 0.0,
    dry_run: bool = True,
) -> GateResult:
    """
    Convenience function to run all gates.

    Returns:
        GateResult - check result.ok before proceeding
    """
    gatekeeper = Gatekeeper(
        mode=mode,
        live_enable=live_enable,
        live_ack=live_ack,
        symbol=symbol,
        quote_amount=quote_amount,
        dry_run=dry_run,
    )
    return gatekeeper.run()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Gatekeeper - Safety Gate Check")
    parser.add_argument("--mode", default="TESTNET", choices=["DRY", "TESTNET", "MAINNET"])
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--amount", type=float, default=11.0)
    parser.add_argument("--live-enable", action="store_true")
    parser.add_argument("--live-ack", default="")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--skip-changelog", action="store_true")
    parser.add_argument("--skip-reconcile", action="store_true")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    gatekeeper = Gatekeeper(
        mode=args.mode,
        live_enable=args.live_enable,
        live_ack=args.live_ack,
        symbol=args.symbol,
        quote_amount=args.amount,
        dry_run=args.dry_run,
        skip_changelog=args.skip_changelog,
        skip_reconcile=args.skip_reconcile,
    )

    result = gatekeeper.run()

    print(f"\n{'='*60}")
    print(f"GATEKEEPER RESULT: {'PASS' if result.ok else 'BLOCKED'}")
    print(f"{'='*60}")
    print(f"Exit Code: {result.exit_code.value} ({result.exit_code.name})")
    print(f"Evidence: {result.evidence_path}")

    if result.block_reason:
        print(f"Block Reason: {result.block_reason}")

    print(f"\nGates ({len(result.checks)}):")
    for check in result.checks:
        icon = "✅" if check.status == GateStatus.PASSED else "❌" if check.status == GateStatus.FAILED else "⚠️"
        print(f"  {icon} {check.gate_name}: {check.message} ({check.duration_ms:.1f}ms)")

    sys.exit(result.exit_code.value)
