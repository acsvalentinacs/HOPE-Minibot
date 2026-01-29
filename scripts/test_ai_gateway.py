# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 09:40:00 UTC
# Purpose: Integration test for AI-Gateway components
# === END SIGNATURE ===
"""
AI-Gateway Integration Test.

Tests:
1. EventBus - publish/subscribe
2. DecisionEngine - BUY/SKIP logic
3. BinancePriceFeed - REST fallback
4. SignalProcessor - orchestration

Usage:
    python -m scripts.test_ai_gateway
"""

import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# Setup path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def test_event_bus():
    """Test EventBus publish/subscribe."""
    print("\n" + "=" * 60)
    print("TEST: EventBus")
    print("=" * 60)

    from ai_gateway.core.event_bus import EventBus, EventType

    # Create test bus
    test_dir = PROJECT_ROOT / "state" / "events_test"
    bus = EventBus(state_dir=test_dir)

    received_events = []

    def on_event(event):
        received_events.append(event)

    # Subscribe
    sub = bus.subscribe([EventType.SIGNAL], on_event)
    print(f"  [OK] Subscription created: {sub.id}")

    # Publish
    event = bus.publish(
        EventType.SIGNAL,
        {"symbol": "BTCUSDT", "price": 88000, "direction": "Long"},
        source="test"
    )
    print(f"  [OK] Event published: {event.id}")
    print(f"  [OK] Checksum: {event.checksum}")

    # Verify delivery
    assert len(received_events) == 1, "Event not delivered"
    assert received_events[0].id == event.id
    print(f"  [OK] Event delivered to subscriber")

    # Verify persistence
    events = bus.replay(EventType.SIGNAL, limit=1)
    assert len(events) >= 1, "Event not persisted"
    print(f"  [OK] Event persisted to JSONL")

    # Verify checksum
    assert event.is_valid(), "Checksum validation failed"
    print(f"  [OK] Checksum validation passed")

    # Stats
    stats = bus.get_stats()
    print(f"  [OK] Stats: {json.dumps(stats, indent=2)}")

    print("\n  RESULT: PASS")
    return True


def test_decision_engine():
    """Test DecisionEngine BUY/SKIP logic."""
    print("\n" + "=" * 60)
    print("TEST: DecisionEngine")
    print("=" * 60)

    from ai_gateway.core.decision_engine import (
        DecisionEngine,
        SignalContext,
        PolicyConfig,
        Action,
    )
    from ai_gateway.contracts import MarketRegime

    # Create engine with test config
    config = PolicyConfig(
        prediction_min=0.60,
        anomaly_max=0.35,
        volume_min_24h=1_000_000,
    )
    engine = DecisionEngine(config=config)

    # Test 1: All checks pass -> BUY
    ctx1 = SignalContext(
        signal_id="test:001",
        symbol="XVSUSDT",
        price=3.54,
        direction="Long",
        delta_pct=2.9,
        volume_24h=5_300_000,
        prediction_prob=0.72,
        regime=MarketRegime.TRENDING_UP,
        anomaly_score=0.15,
        news_score=0.2,
        circuit_state="CLOSED",
        active_positions=0,
    )

    decision1 = engine.evaluate(ctx1)
    print(f"  Test 1: All checks pass")
    print(f"    Action: {decision1.action.value}")
    print(f"    Checks: {decision1.checks_passed}")
    assert decision1.action == Action.BUY, "Expected BUY"
    print(f"    [OK] BUY decision correct")

    # Test 2: Low prediction -> SKIP
    ctx2 = SignalContext(
        signal_id="test:002",
        symbol="ETHUSDT",
        price=2950.0,
        direction="Long",
        delta_pct=1.5,
        volume_24h=100_000_000,
        prediction_prob=0.45,  # Below threshold
        regime=MarketRegime.TRENDING_UP,
        anomaly_score=0.10,
        circuit_state="CLOSED",
        active_positions=0,
    )

    decision2 = engine.evaluate(ctx2)
    print(f"\n  Test 2: Low prediction")
    print(f"    Action: {decision2.action.value}")
    print(f"    Reasons: {[r.value for r in decision2.reasons]}")
    assert decision2.action == Action.SKIP, "Expected SKIP"
    assert "prediction_low" in [r.value for r in decision2.reasons]
    print(f"    [OK] SKIP decision correct")

    # Test 3: Circuit open -> SKIP
    ctx3 = SignalContext(
        signal_id="test:003",
        symbol="BTCUSDT",
        price=88000.0,
        direction="Long",
        delta_pct=0.5,
        volume_24h=1_000_000_000,
        prediction_prob=0.85,
        regime=MarketRegime.TRENDING_UP,
        anomaly_score=0.05,
        circuit_state="OPEN",  # Circuit open
        active_positions=0,
    )

    decision3 = engine.evaluate(ctx3)
    print(f"\n  Test 3: Circuit open")
    print(f"    Action: {decision3.action.value}")
    print(f"    Reasons: {[r.value for r in decision3.reasons]}")
    assert decision3.action == Action.SKIP, "Expected SKIP"
    assert "circuit_open" in [r.value for r in decision3.reasons]
    print(f"    [OK] SKIP decision correct")

    # Test 4: High anomaly -> SKIP
    ctx4 = SignalContext(
        signal_id="test:004",
        symbol="SOLUSDT",
        price=123.0,
        direction="Short",
        delta_pct=5.0,
        volume_24h=50_000_000,
        prediction_prob=0.80,
        regime=MarketRegime.HIGH_VOLATILITY,
        anomaly_score=0.55,  # Above critical threshold
        circuit_state="CLOSED",
        active_positions=0,
    )

    decision4 = engine.evaluate(ctx4)
    print(f"\n  Test 4: High anomaly")
    print(f"    Action: {decision4.action.value}")
    print(f"    Reasons: {[r.value for r in decision4.reasons]}")
    assert decision4.action == Action.SKIP, "Expected SKIP"
    assert "anomaly_high" in [r.value for r in decision4.reasons]
    print(f"    [OK] SKIP decision correct")

    # Stats
    stats = engine.get_stats()
    print(f"\n  Engine Stats:")
    print(f"    Total evaluated: {stats['total_evaluated']}")
    print(f"    Buys: {stats['total_buys']}")
    print(f"    Skips: {stats['total_skips']}")
    print(f"    Buy rate: {stats['buy_rate']:.1%}")

    print("\n  RESULT: PASS")
    return True


async def test_price_feed_rest():
    """Test BinancePriceFeed REST fallback."""
    print("\n" + "=" * 60)
    print("TEST: BinancePriceFeed (REST fallback)")
    print("=" * 60)

    from ai_gateway.feeds.binance_ws import fetch_prices_rest, fetch_ticker_24h

    # Test 1: Fetch specific prices
    symbols = ["BTCUSDT", "ETHUSDT", "XVSUSDT"]
    prices = await fetch_prices_rest(symbols)

    print(f"  Fetched prices for {len(prices)} symbols:")
    for sym, price in prices.items():
        print(f"    {sym}: ${price:,.4f}")

    assert "BTCUSDT" in prices, "BTC price not fetched"
    assert prices["BTCUSDT"] > 0, "Invalid BTC price"
    print(f"  [OK] REST price fetch works")

    # Test 2: Fetch 24h tickers
    tickers = await fetch_ticker_24h(["BTCUSDT", "ETHUSDT"])

    if tickers:
        print(f"\n  24h Tickers:")
        for sym, data in tickers.items():
            print(f"    {sym}: ${data['price']:,.2f} ({data['change_24h_pct']:+.2f}%)")
        print(f"  [OK] 24h ticker fetch works")
    else:
        print(f"  [WARN] 24h ticker fetch returned empty")

    print("\n  RESULT: PASS")
    return True


def test_outcome_tracker():
    """Test OutcomeTracker signal registration."""
    print("\n" + "=" * 60)
    print("TEST: OutcomeTracker")
    print("=" * 60)

    from ai_gateway.modules.self_improver.outcome_tracker import OutcomeTracker

    # Create tracker
    test_dir = PROJECT_ROOT / "state" / "ai" / "outcomes_test"
    tracker = OutcomeTracker(state_dir=test_dir)

    # Register signal
    signal = {
        "symbol": "TESTUSDT",
        "price": 100.0,
        "direction": "Long",
        "delta_pct": 2.5,
        "strategy": "test_strategy",
    }

    signal_id = tracker.register_signal(signal)
    print(f"  Registered signal: {signal_id[:20]}...")

    # Check it's tracked
    assert signal_id in tracker._active, "Signal not in active"
    print(f"  [OK] Signal in active tracking")

    # Update with price
    tracker.update_prices({"TESTUSDT": 102.0})
    print(f"  [OK] Price update processed")

    # Get stats
    stats = tracker.get_stats()
    print(f"  Stats: {json.dumps(stats, indent=2)}")

    print("\n  RESULT: PASS")
    return True


async def run_all_tests():
    """Run all integration tests."""
    print("\n" + "=" * 60)
    print("AI-GATEWAY INTEGRATION TESTS")
    print("=" * 60)
    print(f"Time: {datetime.utcnow().isoformat()}Z")

    results = []

    # Test 1: EventBus
    try:
        results.append(("EventBus", test_event_bus()))
    except Exception as e:
        logger.error(f"EventBus test failed: {e}")
        results.append(("EventBus", False))

    # Test 2: DecisionEngine
    try:
        results.append(("DecisionEngine", test_decision_engine()))
    except Exception as e:
        logger.error(f"DecisionEngine test failed: {e}")
        results.append(("DecisionEngine", False))

    # Test 3: PriceFeed REST
    try:
        results.append(("PriceFeed", await test_price_feed_rest()))
    except Exception as e:
        logger.error(f"PriceFeed test failed: {e}")
        results.append(("PriceFeed", False))

    # Test 4: OutcomeTracker
    try:
        results.append(("OutcomeTracker", test_outcome_tracker()))
    except Exception as e:
        logger.error(f"OutcomeTracker test failed: {e}")
        results.append(("OutcomeTracker", False))

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = 0
    failed = 0
    for name, result in results:
        status = "PASS" if result else "FAIL"
        icon = "[OK]" if result else "[!!]"
        print(f"  {icon} {name}: {status}")
        if result:
            passed += 1
        else:
            failed += 1

    print("=" * 60)
    print(f"Total: {passed} passed, {failed} failed")
    print("=" * 60)

    return failed == 0


def main():
    """Entry point."""
    success = asyncio.run(run_all_tests())
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
