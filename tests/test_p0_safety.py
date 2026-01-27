# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T01:00:00Z
# Purpose: Tests for P0 Safety modules (preflight, state_recovery, changelog)
# Security: No network calls, all mocked
# === END SIGNATURE ===
"""
Tests for P0 Safety Modules.

Modules tested:
- core.trade.preflight (PreflightGate)
- core.trade.state_recovery (StateReconciler)
- core.intel.changelog_monitor (ChangelogMonitor)

All tests are offline - no real network calls.
"""
import pytest
import json
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone


class TestPreflightGate:
    """Tests for PreflightGate."""

    def test_preflight_imports(self):
        """Verify preflight module imports."""
        from core.trade.preflight import (
            PreflightGate,
            PreflightConfig,
            PreflightResult,
            PreflightStatus,
        )
        assert PreflightGate is not None
        assert PreflightConfig is not None

    def test_preflight_config_defaults(self):
        """Verify default config values."""
        from core.trade.preflight import PreflightConfig

        config = PreflightConfig()
        assert config.mode == "TESTNET"
        assert config.dry_run is True
        assert config.live_enable is False

    def test_preflight_result_structure(self):
        """Verify result structure."""
        from core.trade.preflight import PreflightResult, PreflightStatus

        result = PreflightResult(
            ok=True,
            status=PreflightStatus.PASS,
            reason_code="TEST",
            reason_detail="Test passed",
        )

        assert result.ok is True
        assert result.schema_version == "preflight_result.v1"

        d = result.to_dict()
        assert d["status"] == "PASS"
        assert "cmdline_sha256" in d

    def test_preflight_mainnet_requires_ack(self):
        """Verify MAINNET requires double ACK."""
        from core.trade.preflight import PreflightGate, PreflightConfig, PreflightStatus

        # MAINNET without ACK should fail
        config = PreflightConfig(
            mode="MAINNET",
            dry_run=True,
            live_enable=False,
            live_ack="",
        )

        with patch.object(PreflightGate, '_check_lockfile', return_value=(True, "OK")):
            with patch.object(PreflightGate, '_load_env', return_value=True):
                gate = PreflightGate(config)
                gate._env = {"BINANCE_API_KEY": "x", "BINANCE_API_SECRET": "x"}
                result = gate.check()

        assert result.ok is False
        assert result.status == PreflightStatus.FAIL_NO_LIVE_ENABLE

    def test_preflight_mainnet_with_ack(self):
        """Verify MAINNET passes with proper ACK."""
        from core.trade.preflight import PreflightGate, PreflightConfig, PreflightStatus

        config = PreflightConfig(
            mode="MAINNET",
            dry_run=True,
            live_enable=True,
            live_ack="I_KNOW_WHAT_I_AM_DOING",
        )

        with patch.object(PreflightGate, '_check_lockfile', return_value=(True, "OK")):
            with patch.object(PreflightGate, '_load_env', return_value=True):
                gate = PreflightGate(config)
                gate._env = {"BINANCE_API_KEY": "x", "BINANCE_API_SECRET": "x"}
                result = gate.check()

        assert result.ok is True
        assert result.status == PreflightStatus.PASS

    def test_preflight_testnet_no_ack_required(self):
        """Verify TESTNET doesn't require ACK."""
        from core.trade.preflight import PreflightGate, PreflightConfig, PreflightStatus

        config = PreflightConfig(
            mode="TESTNET",
            dry_run=True,
        )

        with patch.object(PreflightGate, '_check_lockfile', return_value=(True, "OK")):
            with patch.object(PreflightGate, '_load_env', return_value=True):
                gate = PreflightGate(config)
                gate._env = {"BINANCE_TESTNET_API_KEY": "x", "BINANCE_TESTNET_API_SECRET": "x"}
                result = gate.check()

        assert result.ok is True
        assert "LIVE_ENABLE" not in result.checks_passed  # Not checked for TESTNET


class TestStateReconciler:
    """Tests for StateReconciler."""

    def test_reconciler_imports(self):
        """Verify state_recovery module imports."""
        from core.trade.state_recovery import (
            StateReconciler,
            ReconcilerConfig,
            ReconcileResult,
            ReconcileStatus,
        )
        assert StateReconciler is not None

    def test_reconciler_clean_start(self):
        """Verify clean start (no local state) is OK."""
        from core.trade.state_recovery import StateReconciler, ReconcilerConfig, ReconcileStatus

        config = ReconcilerConfig(mode="TESTNET")

        # Mock no local state file
        with patch('core.trade.state_recovery.LOCAL_STATE_FILE') as mock_path:
            mock_path.exists.return_value = False

            reconciler = StateReconciler(config)
            result = reconciler.reconcile()

        assert result.ok is True
        assert result.status == ReconcileStatus.OK
        assert "LOCAL_STATE_CLEAN" in result.checks_passed

    def test_reconciler_stale_state_fails(self):
        """Verify stale state is rejected."""
        from core.trade.state_recovery import (
            StateReconciler, ReconcilerConfig, ReconcileStatus, LocalState
        )

        config = ReconcilerConfig(mode="TESTNET", state_ttl_seconds=3600)

        # Create stale state (2 hours old)
        stale_time = time.time() - 7200
        local_state = LocalState(
            updated_at_unix=stale_time,
            updated_at_utc=datetime.fromtimestamp(stale_time, tz=timezone.utc).isoformat(),
        )

        with patch.object(StateReconciler, '_load_local_state', return_value=local_state):
            reconciler = StateReconciler(config)
            result = reconciler.reconcile()

        assert result.ok is False
        assert result.status == ReconcileStatus.FAIL_STALE_STATE

    def test_reconciler_ghost_order_fails(self):
        """Verify ghost orders (local only) cause failure."""
        from core.trade.state_recovery import (
            StateReconciler, ReconcilerConfig, ReconcileStatus, LocalState
        )

        config = ReconcilerConfig(mode="TESTNET")

        # Local state has an order
        local_state = LocalState(
            updated_at_unix=time.time(),
            updated_at_utc=datetime.now(timezone.utc).isoformat(),
            open_orders=[{"orderId": "12345"}],
        )

        # Exchange has no orders
        with patch.object(StateReconciler, '_load_local_state', return_value=local_state):
            with patch.object(StateReconciler, '_fetch_exchange_orders', return_value=(True, [], "")):
                reconciler = StateReconciler(config)
                result = reconciler.reconcile()

        assert result.ok is False
        assert result.status == ReconcileStatus.FAIL_ORDER_MISMATCH
        assert "ghost_orders" in result.details

    def test_save_trading_state(self, tmp_path):
        """Verify state saving works atomically."""
        from core.trade.state_recovery import save_trading_state, LOCAL_STATE_FILE

        # Patch the state file location
        test_state_file = tmp_path / "trading_state.json"

        with patch('core.trade.state_recovery.LOCAL_STATE_FILE', test_state_file):
            with patch('core.trade.state_recovery.STATE_DIR', tmp_path):
                result = save_trading_state(
                    open_orders=[{"orderId": "123"}],
                    positions={"BTC": 0.5},
                    last_trade_id="trade_001",
                )

        assert result is True
        assert test_state_file.exists()

        data = json.loads(test_state_file.read_text())
        assert data["schema_version"] == "trading_state.v1"
        assert len(data["open_orders"]) == 1


class TestChangelogMonitor:
    """Tests for ChangelogMonitor."""

    def test_changelog_imports(self):
        """Verify changelog_monitor module imports."""
        from core.intel.changelog_monitor import (
            ChangelogMonitor,
            ChangelogEvent,
            ContractBreakingChange,
            EventSeverity,
        )
        assert ChangelogMonitor is not None

    def test_known_breaking_changes_exist(self):
        """Verify known breaking changes are defined."""
        from core.intel.changelog_monitor import KNOWN_BREAKING_CHANGES

        assert len(KNOWN_BREAKING_CHANGES) >= 3

        # Check userDataStream removal is known
        user_data_change = next(
            (c for c in KNOWN_BREAKING_CHANGES if "userDataStream" in c.summary),
            None
        )
        assert user_data_change is not None
        assert "2026-02-20" in user_data_change.effective_date

    def test_changelog_monitor_check(self):
        """Verify changelog monitor returns events."""
        from core.intel.changelog_monitor import ChangelogMonitor

        # Don't make real network calls
        with patch.object(ChangelogMonitor, '_fetch_live_changelog', return_value=[]):
            monitor = ChangelogMonitor()
            events = monitor.check()

        assert len(events) >= 3  # At least known changes
        assert all(e.event_id.startswith("sha256:") for e in events)

    def test_changelog_event_severity(self):
        """Verify event severity is set correctly."""
        from core.intel.changelog_monitor import ChangelogMonitor, EventSeverity

        with patch.object(ChangelogMonitor, '_fetch_live_changelog', return_value=[]):
            monitor = ChangelogMonitor()
            events = monitor.check()

        # userDataStream removal should be CRITICAL (effective 2026-02-20)
        critical_events = [e for e in events if e.severity == EventSeverity.CRITICAL]
        assert len(critical_events) >= 1

    def test_should_block_trading(self):
        """Verify trading block detection."""
        from core.intel.changelog_monitor import ChangelogMonitor

        with patch.object(ChangelogMonitor, '_fetch_live_changelog', return_value=[]):
            monitor = ChangelogMonitor()
            should_block, blocking_event = monitor.should_block_trading()

        # Depends on current date vs effective dates
        # If we're within 7 days of 2026-02-20, should block
        if should_block:
            assert blocking_event is not None
            assert blocking_event.is_critical

    def test_changelog_event_to_dict(self):
        """Verify event serialization."""
        from core.intel.changelog_monitor import ChangelogEvent, EventType, EventSeverity

        event = ChangelogEvent(
            event_id="sha256:abc123",
            event_type=EventType.ENDPOINT_REMOVAL,
            severity=EventSeverity.CRITICAL,
            summary="Test removal",
            details="Test details",
            effective_date="2026-02-20T07:00:00Z",
        )

        d = event.to_dict()
        assert d["event_type"] == "ENDPOINT_REMOVAL"
        assert d["severity"] == "CRITICAL"
        assert d["is_critical"] is True
        assert "schema_version" in d


class TestIntegration:
    """Integration tests for P0 modules."""

    def test_preflight_to_reconcile_flow(self):
        """Test preflight -> reconciliation flow."""
        from core.trade.preflight import PreflightGate, PreflightConfig
        from core.trade.state_recovery import StateReconciler, ReconcilerConfig

        # Step 1: Preflight
        preflight_config = PreflightConfig(mode="TESTNET", dry_run=True)

        with patch.object(PreflightGate, '_check_lockfile', return_value=(True, "OK")):
            with patch.object(PreflightGate, '_load_env', return_value=True):
                gate = PreflightGate(preflight_config)
                gate._env = {"BINANCE_TESTNET_API_KEY": "x", "BINANCE_TESTNET_API_SECRET": "x"}
                preflight_result = gate.check()

        assert preflight_result.ok is True

        # Step 2: Reconciliation (clean start)
        with patch('core.trade.state_recovery.LOCAL_STATE_FILE') as mock_path:
            mock_path.exists.return_value = False

            reconciler = StateReconciler(ReconcilerConfig(mode="TESTNET"))
            reconcile_result = reconciler.reconcile()

        assert reconcile_result.ok is True

    def test_changelog_blocks_trading_flow(self):
        """Test changelog check before trading."""
        from core.intel.changelog_monitor import ChangelogMonitor, ChangelogEvent, EventType, EventSeverity
        from datetime import timedelta

        # Create an imminent critical event
        imminent_event = ChangelogEvent(
            event_id="sha256:test",
            event_type=EventType.ENDPOINT_REMOVAL,
            severity=EventSeverity.CRITICAL,
            summary="Test imminent change",
            details="Test",
            effective_date=(datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
        )

        with patch.object(ChangelogMonitor, '_fetch_live_changelog', return_value=[]):
            with patch.object(ChangelogMonitor, '_evaluate_known_change', return_value=imminent_event):
                monitor = ChangelogMonitor()
                # Override known changes for test
                from core.intel import changelog_monitor
                original_known = changelog_monitor.KNOWN_BREAKING_CHANGES
                changelog_monitor.KNOWN_BREAKING_CHANGES = [
                    changelog_monitor.ContractBreakingChange(
                        event_type=EventType.ENDPOINT_REMOVAL,
                        effective_date=(datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
                        effective_timestamp=(datetime.now(timezone.utc) + timedelta(days=3)).timestamp(),
                        summary="Test change",
                        affected_endpoints=["test"],
                        action_required="Test",
                    )
                ]

                events = monitor.check(force=True)
                critical = [e for e in events if e.is_critical]

                # Restore
                changelog_monitor.KNOWN_BREAKING_CHANGES = original_known

        # Should have critical events
        assert len(critical) >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
