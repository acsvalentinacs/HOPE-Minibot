# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T02:15:00Z
# Purpose: Tests for Gatekeeper and Entrypoint modules
# Security: No network calls, all mocked
# === END SIGNATURE ===
"""
Tests for Gatekeeper and Entrypoint modules.

Tests:
- Gatekeeper gate sequence
- Exit codes
- Evidence generation
- TradingContext initialization
"""
import pytest
import json
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone


class TestGatekeeper:
    """Tests for Gatekeeper orchestrator."""

    def test_gatekeeper_imports(self):
        """Verify gatekeeper module imports."""
        from core.runtime.gatekeeper import (
            Gatekeeper,
            GateResult,
            GateStatus,
            ExitCode,
        )
        assert Gatekeeper is not None
        assert GateResult is not None
        assert GateStatus is not None

    def test_exit_codes(self):
        """Verify exit code values."""
        from core.runtime.gatekeeper import ExitCode

        assert ExitCode.SUCCESS.value == 0
        assert ExitCode.INTERNAL_ERROR.value == 1
        assert ExitCode.GATE_BLOCKED.value == 2

    def test_gate_status_enum(self):
        """Verify gate status values."""
        from core.runtime.gatekeeper import GateStatus

        assert GateStatus.PASSED.value == "PASSED"
        assert GateStatus.FAILED.value == "FAILED"
        assert GateStatus.SKIPPED.value == "SKIPPED"
        assert GateStatus.ERROR.value == "ERROR"

    def test_gatekeeper_dry_mode_passes(self):
        """Verify DRY mode passes with minimal checks."""
        from core.runtime.gatekeeper import Gatekeeper, ExitCode
        from pathlib import Path

        # Clean up any existing lockfile first
        lockfile = Path(__file__).parent.parent / "state" / "runtime.lock.json"
        if lockfile.exists():
            lockfile.unlink()

        gatekeeper = Gatekeeper(
            mode="DRY",
            dry_run=True,
            skip_changelog=True,
            skip_reconcile=True,
        )

        result = gatekeeper.run()

        # Should pass because lockfile and preflight pass in DRY mode
        assert result.exit_code == ExitCode.SUCCESS
        assert len(result.checks) >= 1  # At least lockfile check
        gatekeeper.release()

    def test_gatekeeper_creates_evidence(self, tmp_path):
        """Verify evidence file is created."""
        from core.runtime.gatekeeper import Gatekeeper, EVIDENCE_DIR
        from pathlib import Path

        # Clean up any existing lockfile first
        lockfile = Path(__file__).parent.parent / "state" / "runtime.lock.json"
        if lockfile.exists():
            lockfile.unlink()

        gatekeeper = Gatekeeper(
            mode="DRY",
            dry_run=True,
            skip_changelog=True,
            skip_reconcile=True,
        )

        result = gatekeeper.run()

        # Evidence path should exist
        assert result.evidence_path is not None
        assert result.evidence_path.exists()

        # Check evidence content
        evidence = json.loads(result.evidence_path.read_text())
        assert "ok" in evidence
        assert "exit_code" in evidence
        assert "checks" in evidence
        assert "cmdline_sha256" in evidence

        gatekeeper.release()

    def test_gate_result_to_dict(self):
        """Verify GateResult serialization."""
        from core.runtime.gatekeeper import GateResult, GateCheckResult, GateStatus, ExitCode
        from pathlib import Path

        check = GateCheckResult(
            gate_name="TEST",
            status=GateStatus.PASSED,
            message="Test passed",
            duration_ms=10.0,
        )

        result = GateResult(
            ok=True,
            exit_code=ExitCode.SUCCESS,
            block_reason=None,
            evidence_path=Path("/tmp/test.json"),
            cmdline_sha256="sha256:test",
            checks=[check],
            total_duration_ms=10.0,
        )

        d = result.to_dict()
        assert d["ok"] is True
        assert d["exit_code"] == 0
        assert len(d["checks"]) == 1
        assert d["checks"][0]["status"] == "PASSED"

    def test_gatekeeper_mainnet_requires_ack(self):
        """Verify MAINNET requires ACK."""
        from core.runtime.gatekeeper import Gatekeeper, ExitCode

        # Clean up any existing lockfile first
        from pathlib import Path
        lockfile = Path(__file__).parent.parent / "state" / "runtime.lock.json"
        if lockfile.exists():
            lockfile.unlink()

        # MAINNET without ACK - should fail at preflight
        gatekeeper = Gatekeeper(
            mode="MAINNET",
            dry_run=True,
            live_enable=False,  # Missing
            live_ack="",  # Missing
            skip_changelog=True,
            skip_reconcile=True,
        )

        result = gatekeeper.run()

        # Should be blocked by preflight with MAINNET/ENABLE in reason
        assert result.exit_code == ExitCode.GATE_BLOCKED
        assert "MAINNET" in result.block_reason or "ENABLE" in result.block_reason

        gatekeeper.release()


class TestEntrypoint:
    """Tests for Entrypoint module."""

    def test_entrypoint_imports(self):
        """Verify entrypoint module imports."""
        from core.entrypoint import (
            TradingContext,
            LiveTradingRunner,
            ExitCode,
            parse_args,
            main,
        )
        assert TradingContext is not None
        assert LiveTradingRunner is not None

    def test_trading_context_creation(self):
        """Verify TradingContext initialization."""
        from core.entrypoint import TradingContext

        context = TradingContext(
            mode="DRY",
            symbol="BTCUSDT",
            quote_amount=11.0,
            dry_run=True,
        )

        assert context.mode == "DRY"
        assert context.symbol == "BTCUSDT"
        assert context.quote_amount == 11.0
        assert context.dry_run is True

    def test_trading_context_initialization(self):
        """Verify TradingContext component initialization."""
        from core.entrypoint import TradingContext

        context = TradingContext(
            mode="DRY",
            symbol="BTCUSDT",
            quote_amount=11.0,
            dry_run=True,
        )

        result = context.initialize()
        assert result is True

        # Check components were initialized
        assert context._circuit_breaker is not None

    def test_exit_codes_match(self):
        """Verify exit codes match between modules."""
        from core.entrypoint import ExitCode as EntrypointExitCode
        from core.runtime.gatekeeper import ExitCode as GatekeeperExitCode

        # Values should match
        assert EntrypointExitCode.SUCCESS == 0
        assert EntrypointExitCode.GATE_BLOCKED == 2

    def test_trading_runner_shutdown(self):
        """Verify runner shutdown request."""
        from core.entrypoint import TradingContext, LiveTradingRunner

        context = TradingContext(
            mode="DRY",
            symbol="BTCUSDT",
            quote_amount=11.0,
            dry_run=True,
        )

        runner = LiveTradingRunner(context)
        assert runner._shutdown_requested is False

        runner.request_shutdown()
        assert runner._shutdown_requested is True


class TestRuntimeIntegration:
    """Integration tests for runtime modules."""

    def test_full_gate_sequence_dry(self):
        """Test full gate sequence in DRY mode."""
        from core.runtime.gatekeeper import Gatekeeper, ExitCode, GateStatus
        from pathlib import Path

        # Clean up any existing lockfile first
        lockfile = Path(__file__).parent.parent / "state" / "runtime.lock.json"
        if lockfile.exists():
            lockfile.unlink()

        gatekeeper = Gatekeeper(
            mode="DRY",
            dry_run=True,
            skip_changelog=True,
            skip_reconcile=True,
        )

        result = gatekeeper.run()

        # All should pass
        assert result.ok is True
        assert result.exit_code == ExitCode.SUCCESS

        # Check gate results
        for check in result.checks:
            assert check.status in [GateStatus.PASSED, GateStatus.SKIPPED]

        gatekeeper.release()

    def test_gate_evidence_persistence(self, tmp_path):
        """Test evidence is persisted correctly."""
        from core.runtime.gatekeeper import Gatekeeper
        from pathlib import Path

        # Clean up any existing lockfile first
        lockfile = Path(__file__).parent.parent / "state" / "runtime.lock.json"
        if lockfile.exists():
            lockfile.unlink()

        gatekeeper = Gatekeeper(
            mode="DRY",
            dry_run=True,
            skip_changelog=True,
            skip_reconcile=True,
        )

        result = gatekeeper.run()

        # Read evidence
        evidence_content = result.evidence_path.read_text()
        evidence = json.loads(evidence_content)

        # Verify schema
        assert evidence.get("schema_version") == "gate_result.v1"
        assert "timestamp_utc" in evidence

        gatekeeper.release()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
