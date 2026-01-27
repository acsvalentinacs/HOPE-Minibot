# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 22:30:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-26T05:00:00Z
# P0 FIX: Renamed from types.py to avoid shadowing Python stdlib types module
# === END SIGNATURE ===
"""
type_defs.py - Re-export of core types from models.py

IMPORTANT: This file was renamed from types.py because 'types.py' shadowed
Python's built-in 'types' module, causing cascading import failures:
  enum.py -> types -> core/types.py -> minibot.core.models -> FAIL

Usage:
    from core.type_defs import EngineMode, TradeSide, PositionInfo
"""

from core.models import (
    EngineMode,
    EngineStatus,
    TradeSide,
    OrderSide,
    OrderStatus,
    PositionState,
    ExitReason,
    TradeSignal,
    PositionInfo,
    Fill,
    OrderRecord,
    ExecutionResult,
    GuardRejection,
    TradeJournalEntry,
    RiskLimits,
    new_uuid,
    now_ts,
)

__all__ = [
    "EngineMode",
    "EngineStatus",
    "TradeSide",
    "OrderSide",
    "OrderStatus",
    "PositionState",
    "ExitReason",
    "TradeSignal",
    "PositionInfo",
    "Fill",
    "OrderRecord",
    "ExecutionResult",
    "GuardRejection",
    "TradeJournalEntry",
    "RiskLimits",
    "new_uuid",
    "now_ts",
]
