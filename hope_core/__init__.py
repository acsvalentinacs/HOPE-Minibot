# === AI SIGNATURE ===
# Module: hope_core/__init__.py
# Created by: Claude (opus-4.5)
# Created at: 2026-02-04 22:20:00 UTC
# Purpose: HOPE Core v2.0 package initialization
# === END SIGNATURE ===
"""
HOPE Core v2.0 - Command Bus + State Machine Trading Architecture

Usage:
    from hope_core import HopeCore, HopeCoreConfig

    config = HopeCoreConfig(mode='DRY')
    core = HopeCore(config)
    result = core.process_signal_sync({'symbol': 'BTCUSDT', 'score': 0.85})
"""

# Re-export main classes from submodules
from hope_core.bus.command_bus import (
    CommandBus, CommandType, CommandStatus, CommandResult,
    Command, RateLimiter, CircuitBreaker
)
from hope_core.bus.contracts import validate_command, SignalSource
from hope_core.state.machine import (
    StateMachine, StateMachineManager, TradingState, StateTransition
)
from hope_core.journal.event_journal import (
    EventJournal, EventType, EventLevel, Event
)

# Import main HopeCore class
from hope_core.hope_core import HopeCore, HopeCoreConfig

__all__ = [
    # Main
    'HopeCore',
    'HopeCoreConfig',
    # Command Bus
    'CommandBus',
    'CommandType',
    'CommandStatus',
    'CommandResult',
    'Command',
    'RateLimiter',
    'CircuitBreaker',
    # Contracts
    'validate_command',
    'SignalSource',
    # State Machine
    'StateMachine',
    'StateMachineManager',
    'TradingState',
    'StateTransition',
    # Journal
    'EventJournal',
    'EventType',
    'EventLevel',
    'Event',
]

__version__ = "2.0.0"
