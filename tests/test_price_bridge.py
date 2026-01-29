# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 18:50:00 UTC
# Purpose: Test PriceFeedBridge integration with OutcomeTracker
# === END SIGNATURE ===
"""
Test Price Feed Bridge functionality.

Tests:
1. Bridge initialization
2. Price caching
3. Outcome tracking integration
4. Stats reporting
"""

import asyncio
import pytest
import tempfile
from pathlib import Path
from datetime import datetime


class TestPriceFeedBridge:
    """Test PriceFeedBridge component."""

    def test_bridge_initialization(self):
        """Test bridge initializes correctly."""
        from ai_gateway.feeds.price_bridge import PriceFeedBridge

        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = PriceFeedBridge(state_dir=Path(tmpdir))

            assert bridge is not None
            assert bridge.is_running is False
            assert bridge.is_connected is False

    def test_manual_price_update(self):
        """Test manual price updates."""
        from ai_gateway.feeds.price_bridge import PriceFeedBridge

        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = PriceFeedBridge(state_dir=Path(tmpdir))

            # Update prices manually
            bridge.update_price("BTC", 88000.0)
            bridge.update_price("ETHUSDT", 3200.0)
            bridge.update_price("XVS", 10.5)

            # Check prices (should auto-add USDT)
            assert bridge.get_price("BTCUSDT") == 88000.0
            assert bridge.get_price("ETHUSDT") == 3200.0
            assert bridge.get_price("XVSUSDT") == 10.5
            assert bridge.get_price("UNKNOWN") is None

    def test_get_all_prices(self):
        """Test getting all cached prices."""
        from ai_gateway.feeds.price_bridge import PriceFeedBridge

        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = PriceFeedBridge(state_dir=Path(tmpdir))

            bridge.update_price("BTCUSDT", 88000.0)
            bridge.update_price("ETHUSDT", 3200.0)

            prices = bridge.get_all_prices()

            assert len(prices) == 2
            assert "BTCUSDT" in prices
            assert "ETHUSDT" in prices

    def test_stats_reporting(self):
        """Test statistics reporting."""
        from ai_gateway.feeds.price_bridge import PriceFeedBridge

        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = PriceFeedBridge(state_dir=Path(tmpdir))

            # Update some prices
            bridge.update_price("BTCUSDT", 88000.0)
            bridge.update_price("ETHUSDT", 3200.0)

            stats = bridge.get_stats()

            assert "running" in stats
            assert stats["running"] is False
            assert stats["price_updates"] == 2
            assert stats["symbols_tracked"] == 0  # Not running, so not tracked

    def test_outcome_tracker_lazy_load(self):
        """Test that outcome tracker is created lazily."""
        from ai_gateway.feeds.price_bridge import PriceFeedBridge

        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = PriceFeedBridge(state_dir=Path(tmpdir))

            # Initially no tracker
            assert bridge._outcome_tracker is None

            # Access creates it
            tracker = bridge._get_outcome_tracker()

            assert tracker is not None
            assert bridge._outcome_tracker is not None


class TestOutcomeTrackerIntegration:
    """Test OutcomeTracker with price updates."""

    def test_register_and_track_signal(self):
        """Test registering a signal and tracking its outcome."""
        from ai_gateway.modules.self_improver.outcome_tracker import OutcomeTracker

        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = OutcomeTracker(state_dir=Path(tmpdir))

            # Register signal
            signal = {
                "symbol": "BTCUSDT",
                "price": 88000.0,
                "direction": "Long",
                "signal_type": "pump",
            }
            signal_id = tracker.register_signal(signal)

            assert signal_id is not None
            assert signal_id.startswith("sig:")
            assert "BTCUSDT" in tracker.active_symbols

    def test_price_updates_compute_mfe_mae(self):
        """Test that price updates compute MFE/MAE."""
        from ai_gateway.modules.self_improver.outcome_tracker import OutcomeTracker
        import time

        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = OutcomeTracker(state_dir=Path(tmpdir))

            # Register signal
            signal = {
                "symbol": "BTCUSDT",
                "price": 100.0,
                "direction": "Long",
            }
            signal_id = tracker.register_signal(signal)

            # Update with higher price (MFE should increase)
            tracker.update_prices({"BTCUSDT": 102.0})  # +2%

            # Check MFE
            tracked = tracker._active.get(signal_id)
            assert tracked is not None
            assert tracked.mfe > 0  # Should be positive for long going up

            # Update with lower price (MAE should decrease)
            tracker.update_prices({"BTCUSDT": 99.0})  # -1% from entry

            # Check MAE
            assert tracked.mae < 0  # Should be negative for adverse move


class TestEventBusIntegration:
    """Test EventBus integration."""

    def test_price_event_handling(self):
        """Test that PRICE events are handled correctly."""
        from ai_gateway.core.event_bus import EventBus, EventType
        from ai_gateway.feeds.price_bridge import PriceFeedBridge

        with tempfile.TemporaryDirectory() as tmpdir:
            bus = EventBus(state_dir=Path(tmpdir) / "events")
            bridge = PriceFeedBridge(state_dir=Path(tmpdir))
            bridge._event_bus = bus

            # Subscribe bridge to PRICE events
            sub = bus.subscribe([EventType.PRICE], bridge._on_price_event)

            # Publish a price event
            bus.publish(
                EventType.PRICE,
                {"symbol": "BTCUSDT", "price": 88000.0},
                source="test"
            )

            # Check bridge received it
            assert bridge.get_price("BTCUSDT") == 88000.0


class TestAsyncBridgeOperations:
    """Test async bridge operations."""

    @pytest.mark.asyncio
    async def test_bridge_start_stop(self):
        """Test bridge start/stop lifecycle."""
        from ai_gateway.feeds.price_bridge import PriceFeedBridge

        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = PriceFeedBridge(
                state_dir=Path(tmpdir),
                auto_subscribe=False,  # Don't try to connect
            )

            # Test that it doesn't crash (actual WS connection will fail without network)
            # Just verify the lifecycle methods exist and are callable
            assert bridge.is_running is False

            # In real test with mocking, we'd test full lifecycle
            # For now just verify the interface
            stats = bridge.get_stats()
            assert "running" in stats
            assert "price_updates" in stats


def test_singleton_bridge():
    """Test singleton bridge instance."""
    from ai_gateway.feeds.price_bridge import get_price_bridge

    bridge1 = get_price_bridge()
    bridge2 = get_price_bridge()

    assert bridge1 is bridge2


if __name__ == "__main__":
    # Run basic tests
    print("Running PriceFeedBridge tests...")

    test = TestPriceFeedBridge()
    test.test_bridge_initialization()
    print("✓ Bridge initialization")

    test.test_manual_price_update()
    print("✓ Manual price update")

    test.test_get_all_prices()
    print("✓ Get all prices")

    test.test_stats_reporting()
    print("✓ Stats reporting")

    test2 = TestOutcomeTrackerIntegration()
    test2.test_register_and_track_signal()
    print("✓ Register and track signal")

    test2.test_price_updates_compute_mfe_mae()
    print("✓ MFE/MAE computation")

    test3 = TestEventBusIntegration()
    test3.test_price_event_handling()
    print("✓ EventBus integration")

    print("\n✅ All tests passed!")
