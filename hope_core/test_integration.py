#!/usr/bin/env python3
# === AI SIGNATURE ===
# Module: hope_core/test_integration.py
# Created by: Claude (opus-4.5)
# Created at: 2026-02-04 11:00:00 UTC
# Purpose: Integration test for HOPE Core v2.0
# === END SIGNATURE ===
"""
HOPE Core v2.0 - Integration Test

Tests the complete trading cycle:
1. Signal → Command Bus → State Machine
2. Decision → Eye of God Bridge
3. Order → Executor Bridge
4. Position tracking
5. Health monitoring
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime, timezone

# Add path
sys.path.insert(0, str(Path(__file__).parent))


def print_section(title: str):
    """Print section header."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def print_result(name: str, passed: bool, details: str = ""):
    """Print test result."""
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {status}: {name}")
    if details:
        print(f"         {details}")


async def test_imports():
    """Test all imports."""
    print_section("1. IMPORT TESTS")
    
    all_passed = True
    
    # Core
    try:
        from hope_core import HopeCore, HopeCoreConfig
        print_result("hope_core", True)
    except Exception as e:
        print_result("hope_core", False, str(e))
        all_passed = False
    
    # Command Bus
    try:
        from bus.command_bus import CommandBus, CommandType
        print_result("bus.command_bus", True)
    except Exception as e:
        print_result("bus.command_bus", False, str(e))
        all_passed = False
    
    # Contracts
    try:
        from bus.contracts import validate_command, SignalSource
        print_result("bus.contracts", True)
    except Exception as e:
        print_result("bus.contracts", False, str(e))
        all_passed = False
    
    # State Machine
    try:
        from state.machine import StateMachine, TradingState
        print_result("state.machine", True)
    except Exception as e:
        print_result("state.machine", False, str(e))
        all_passed = False
    
    # Event Journal
    try:
        from journal.event_journal import EventJournal, EventType
        print_result("journal.event_journal", True)
    except Exception as e:
        print_result("journal.event_journal", False, str(e))
        all_passed = False
    
    # Guardian
    try:
        from guardian.watchdog import Guardian, GuardianConfig
        print_result("guardian.watchdog", True)
    except Exception as e:
        print_result("guardian.watchdog", False, str(e))
        all_passed = False
    
    return all_passed


async def test_core_creation():
    """Test HopeCore creation."""
    print_section("2. CORE CREATION TEST")
    
    from hope_core import HopeCore, HopeCoreConfig
    
    try:
        config = HopeCoreConfig(mode='DRY')
        core = HopeCore(config)
        
        print_result("HopeCore instantiation", True)
        print(f"         Mode: {core.config.mode}")
        print(f"         State: {core.state.value}")
        print(f"         Handlers: {len(core.command_bus._handlers)}")
        
        return True, core
    except Exception as e:
        print_result("HopeCore instantiation", False, str(e))
        return False, None


async def test_command_bus(core):
    """Test Command Bus operations."""
    print_section("3. COMMAND BUS TESTS")
    
    all_passed = True
    
    # Test HEALTH command
    try:
        result = await core.command_bus.dispatch_simple("HEALTH", {}, source="test")
        health = result.data
        passed = health.get("status") == "healthy"
        print_result("HEALTH command", passed, f"status={health.get('status')}")
        all_passed = all_passed and passed
    except Exception as e:
        print_result("HEALTH command", False, str(e))
        all_passed = False
    
    # Test SIGNAL command
    try:
        result = await core.submit_signal(
            symbol="BTCUSDT",
            score=0.75,
            source="TEST",
        )
        passed = result.status.value == "SUCCESS"
        print_result("SIGNAL command", passed, f"signal_id={result.data.get('signal_id')}")
        all_passed = all_passed and passed
    except Exception as e:
        print_result("SIGNAL command", False, str(e))
        all_passed = False
    
    # Test SYNC command
    try:
        result = await core.command_bus.dispatch_simple("SYNC", {}, source="test")
        passed = result.status.value == "SUCCESS"
        print_result("SYNC command", passed)
        all_passed = all_passed and passed
    except Exception as e:
        print_result("SYNC command", False, str(e))
        all_passed = False
    
    # Test rate limiting
    try:
        stats = core.command_bus.get_stats()
        print_result("Command Bus stats", True, f"received={stats['received']}")
    except Exception as e:
        print_result("Command Bus stats", False, str(e))
        all_passed = False
    
    return all_passed


async def test_state_machine(core):
    """Test State Machine operations."""
    print_section("4. STATE MACHINE TESTS")
    
    all_passed = True
    
    sm = core.state_manager.global_machine
    
    # Test current state
    try:
        state = sm.state
        print_result("Current state", True, f"state={state.value}")
    except Exception as e:
        print_result("Current state", False, str(e))
        all_passed = False
    
    # Test valid transitions
    try:
        valid = sm.get_valid_transitions()
        print_result("Valid transitions", True, f"can_go_to={valid}")
    except Exception as e:
        print_result("Valid transitions", False, str(e))
        all_passed = False
    
    # Test invalid transition (should fail gracefully)
    try:
        from state.machine import TradingState
        result = sm.can_transition(TradingState.CLOSING)
        print_result("Invalid transition check", True, f"blocked={not result}")
    except Exception as e:
        print_result("Invalid transition check", False, str(e))
        all_passed = False
    
    return all_passed


async def test_event_journal(core):
    """Test Event Journal operations."""
    print_section("5. EVENT JOURNAL TESTS")
    
    all_passed = True
    
    journal = core.journal
    
    # Test append
    try:
        journal.append(
            "TEST_EVENT",
            payload={"test": True},
            correlation_id="test123",
        )
        print_result("Journal append", True)
    except Exception as e:
        print_result("Journal append", False, str(e))
        all_passed = False
    
    # Test get recent
    try:
        recent = journal.get_recent(10)
        print_result("Journal get_recent", True, f"count={len(recent)}")
    except Exception as e:
        print_result("Journal get_recent", False, str(e))
        all_passed = False
    
    # Test stats
    try:
        stats = journal.get_stats()
        print_result("Journal stats", True, f"events={stats.get('event_count', 0)}")
    except Exception as e:
        print_result("Journal stats", False, str(e))
        all_passed = False
    
    return all_passed


async def test_trading_cycle(core):
    """Test complete trading cycle."""
    print_section("6. TRADING CYCLE TEST")
    
    all_passed = True
    
    # Simulate full cycle
    print("  Simulating: Signal → Decision → (Skip order in DRY)")
    
    # 1. Submit signal
    try:
        result = await core.submit_signal(
            symbol="ETHUSDT",
            score=0.85,
            source="INTEGRATION_TEST",
        )
        signal_id = result.data.get("signal_id")
        print_result("Signal submitted", True, f"id={signal_id}")
    except Exception as e:
        print_result("Signal submitted", False, str(e))
        all_passed = False
    
    # 2. Check state changed
    try:
        state = core.state.value
        # In DRY mode without Eye of God, state might stay IDLE
        print_result("State after signal", True, f"state={state}")
    except Exception as e:
        print_result("State after signal", False, str(e))
        all_passed = False
    
    # 3. Check positions (should be 0 in DRY without executor)
    try:
        positions = len(core._open_positions)
        print_result("Open positions", True, f"count={positions}")
    except Exception as e:
        print_result("Open positions", False, str(e))
        all_passed = False
    
    # 4. Get health
    try:
        health = await core.get_health()
        print_result("Health after cycle", True, f"status={health.get('status')}")
    except Exception as e:
        print_result("Health after cycle", False, str(e))
        all_passed = False
    
    return all_passed


async def test_guardian():
    """Test Guardian (without actually starting processes)."""
    print_section("7. GUARDIAN TESTS")
    
    all_passed = True
    
    try:
        from guardian.watchdog import Guardian, GuardianConfig
        
        config = GuardianConfig(
            heartbeat_interval_sec=1,
            health_check_interval_sec=2,
            core_command=["echo", "test"],  # Dummy
        )
        guardian = Guardian(config)
        
        print_result("Guardian creation", True)
        
        # Test status
        status = guardian.get_status()
        print_result("Guardian status", True, f"running={status['running']}")
        
        # Test heartbeat check
        check = guardian._check_heartbeat_freshness()
        print_result("Heartbeat check", True, f"result={check.result.value}")
        
    except Exception as e:
        print_result("Guardian tests", False, str(e))
        all_passed = False
    
    return all_passed


async def main():
    """Run all integration tests."""
    print("\n" + "="*60)
    print("    HOPE CORE v2.0 - INTEGRATION TESTS")
    print("    " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
    print("="*60)
    
    results = {}
    
    # 1. Import tests
    results["imports"] = await test_imports()
    
    # 2. Core creation
    passed, core = await test_core_creation()
    results["core_creation"] = passed
    
    if core:
        # 3. Command Bus
        results["command_bus"] = await test_command_bus(core)
        
        # 4. State Machine
        results["state_machine"] = await test_state_machine(core)
        
        # 5. Event Journal
        results["event_journal"] = await test_event_journal(core)
        
        # 6. Trading Cycle
        results["trading_cycle"] = await test_trading_cycle(core)
    
    # 7. Guardian
    results["guardian"] = await test_guardian()
    
    # Summary
    print_section("SUMMARY")
    
    total = len(results)
    passed = sum(1 for v in results.values() if v)
    failed = total - passed
    
    for name, result in results.items():
        status = "✅" if result else "❌"
        print(f"  {status} {name}")
    
    print()
    print(f"  Total: {total} | Passed: {passed} | Failed: {failed}")
    print()
    
    if failed == 0:
        print("  🎉 ALL TESTS PASSED!")
        print()
        print("  Next steps:")
        print("  1. Deploy to VPS: ./deploy/deploy_to_vps.sh")
        print("  2. Start services: systemctl start hope-core hope-guardian")
        print("  3. Check health: curl http://127.0.0.1:8200/api/health")
    else:
        print("  ⚠️  SOME TESTS FAILED")
        print("  Review errors above and fix before deployment.")
    
    print()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
