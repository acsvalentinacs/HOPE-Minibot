# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-01-30 19:00:00 UTC
# Purpose: Test EGRESS GATE - verify Telegram filter works correctly
# === END SIGNATURE ===
"""
Test EGRESS GATE for Telegram messages.

Tests:
1. Normal message (should PASS)
2. PUMP with delta >= 10% (should PASS)
3. PUMP with delta < 10% (should BLOCK)
4. PUMP with MICRO type (should BLOCK)
5. Status message (should PASS)
"""

import asyncio
import sys
import os

# Fix Windows console encoding
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.telegram_sender import TelegramSender, _egress_gate_check


def test_egress_gate_logic():
    """Test the gate logic without sending."""
    print("=" * 60)
    print("EGRESS GATE LOGIC TEST")
    print("=" * 60)

    test_cases = [
        # (message, expected_allowed, description)
        (
            "Test message from HOPE bot",
            True,
            "Normal message"
        ),
        (
            "HOPE STATUS\nMode: DRY\nPositions: 0/3",
            True,
            "Status message"
        ),
        (
            "PUMP: BTCUSDT\nType: BREAKOUT\nConf: 75%\nDelta: 15.50%\nPrice: $100000",
            True,
            "PUMP delta=15.5% (should PASS)"
        ),
        (
            "PUMP: ETHUSDT\nType: MOMENTUM\nConf: 60%\nDelta: 10.00%\nPrice: $3500",
            True,
            "PUMP delta=10% exact (should PASS)"
        ),
        (
            "PUMP: XRPUSDT\nType: MICRO\nConf: 50%\nDelta: 0.47%\nPrice: $0.55",
            False,
            "PUMP MICRO type (should BLOCK)"
        ),
        (
            "PUMP: SUIUSDT\nType: TEST_ACTIVITY\nConf: 55%\nDelta: 0.12%\nPrice: $1.20",
            False,
            "PUMP TEST_ACTIVITY (should BLOCK)"
        ),
        (
            "PUMP: DOGEUSDT\nType: SCALP\nConf: 45%\nDelta: 3.5%\nPrice: $0.08",
            False,
            "PUMP SCALP type (should BLOCK)"
        ),
        (
            "PUMP: SOLUSDT\nType: BREAKOUT\nConf: 70%\nDelta: 5.00%\nPrice: $150",
            False,
            "PUMP delta=5% < 10% (should BLOCK)"
        ),
        (
            "PUMP: AVAXUSDT\nType: MOMENTUM\nConf: 65%\nDelta: 9.99%\nPrice: $35",
            False,
            "PUMP delta=9.99% < 10% (should BLOCK)"
        ),
        (
            "PUMP: UNKNOWN without delta field",
            False,
            "PUMP without Delta (fail-closed, should BLOCK)"
        ),
        (
            "Circuit breaker triggered! Trading paused.",
            True,
            "Alert message (should PASS)"
        ),
    ]

    passed = 0
    failed = 0

    for msg, expected, desc in test_cases:
        allowed, reason = _egress_gate_check(msg)
        status = "PASS" if allowed == expected else "FAIL"
        icon = "✅" if status == "PASS" else "❌"

        if status == "PASS":
            passed += 1
        else:
            failed += 1

        print(f"\n{icon} {desc}")
        print(f"   Expected: {'ALLOW' if expected else 'BLOCK'}")
        print(f"   Got: {'ALLOW' if allowed else 'BLOCK'} ({reason})")

    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)

    return failed == 0


async def test_real_send():
    """Send a real test message to verify Telegram works."""
    print("\n" + "=" * 60)
    print("REAL TELEGRAM SEND TEST")
    print("=" * 60)

    sender = TelegramSender()

    # Test 1: Normal message (should go through)
    test_msg = "🧪 EGRESS GATE TEST\n\nThis is a test message.\nIf you see this, Telegram sending works!\n\nTimestamp: " + __import__('datetime').datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"\nSending test message...")
    result = await sender.send(test_msg)

    if result:
        print("✅ Test message sent successfully!")
    else:
        print("❌ Test message failed to send!")

    await sender.close()
    return result


async def main():
    # First test logic
    logic_ok = test_egress_gate_logic()

    if not logic_ok:
        print("\n⚠️ Logic tests failed! Fix before sending real messages.")
        return 1

    # Then test real send
    print("\nLogic tests passed. Testing real Telegram send...")
    send_ok = await test_real_send()

    if send_ok:
        print("\n✅ ALL TESTS PASSED - EGRESS GATE working correctly!")
        return 0
    else:
        print("\n❌ Telegram send failed - check token/chat_id")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
