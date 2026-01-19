from __future__ import annotations

import time
import uuid as _uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# =========================
#  HELPERS (EXPOSED)
# =========================

def new_uuid() -> str:
    """Генерирует уникальный ID (hex string)."""
    return _uuid.uuid4().hex

def now_ts() -> float:
    """Возвращает текущий timestamp (float)."""
    return time.time()


# =========================
#  ENUMS
# =========================

class EngineMode(str, Enum):
    """Режим работы движка."""
    DRY = "DRY"
    TESTNET = "TESTNET"
    LIVE = "LIVE"


class TradeSide(str, Enum):
    """Направление позиции / сигнала."""
    LONG = "LONG"
    SHORT = "SHORT"
    CLOSE = "CLOSE"


class OrderSide(str, Enum):
    """Направление ордера на бирже (BUY/SELL)."""
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    """Статус биржевого ордера."""
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class PositionState(str, Enum):
    """Состояние позиции."""
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    LIQUIDATED = "LIQUIDATED"


class ExitReason(str, Enum):
    """Причина выхода."""
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    MANUAL = "MANUAL"
    SYSTEM = "SYSTEM"
    UNKNOWN = "UNKNOWN"


# =========================
#  DATA CLASSES
# =========================

@dataclass
class TradeSignal:
    symbol: str = "BTCUSDT"
    side: TradeSide = TradeSide.LONG
    risk_usd: float = 0.0
    source: str = "unknown"
    
    v: int = 1
    ts: float = field(default_factory=now_ts)
    confidence: float = 1.0
    reason: Optional[str] = None
    
    price: Optional[float] = None
    close_price: Optional[float] = None
    
    signal_id: str = field(default_factory=new_uuid)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PositionInfo:
    symbol: str = "BTCUSDT"
    side: TradeSide = TradeSide.LONG
    
    position_id: str = field(default_factory=new_uuid)
    entry_price: float = 0.0
    qty: float = 0.0
    entry_cost_usd: float = 0.0
    size_usd: float = 0.0
    avg_price: float = 0.0
    
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    
    ts_opened: float = field(default_factory=now_ts)
    open_signal_source: str = "unknown"
    state: PositionState = PositionState.OPEN
    
    created_at: float = field(default_factory=now_ts)
    updated_at: float = field(default_factory=now_ts)
    
    tags: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Fill:
    price: float
    qty: float
    fee: float
    timestamp: float


@dataclass
class OrderRecord:
    client_order_id: str
    symbol: str
    side: OrderSide
    status: OrderStatus
    requested_qty: float
    executed_qty: float
    avg_price: float
    fills: List[Fill] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0


@dataclass
class ExecutionResult:
    success: bool
    reason: str
    position: Optional[PositionInfo] = None
    order_ids: List[str] = field(default_factory=list)


@dataclass
class GuardRejection:
    guard: str
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TradeJournalEntry:
    symbol: str
    side: TradeSide
    
    journal_id: str = field(default_factory=new_uuid)
    entry_price: float = 0.0
    exit_price: float = 0.0
    qty: float = 0.0
    
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    
    entry_time: float = 0.0
    exit_time: float = 0.0
    exit_reason: ExitReason = ExitReason.UNKNOWN
    
    source: str = "unknown"
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskLimits:
    max_risk_per_signal_usd: float = 0.0
    max_daily_loss_usd: float = 0.0
    max_open_positions: int = 1
    max_position_size_usd: float = 0.0
    min_equity_usd: float = 0.0
    min_signal_confidence: float = 0.0


@dataclass
class EngineStatus:
    mode: EngineMode = EngineMode.DRY
    version: str = "5.0.0"
    
    equity_usd: float = 0.0
    initial_equity: float = 0.0
    daily_pnl: float = 0.0
    daily_stop_hit: bool = False
    
    open_positions_count: int = 0
    open_positions: List[PositionInfo] = field(default_factory=list)
    
    # Поля из старых контрактов для совместимости
    daily_pnl_usd: float = 0.0 
    portfolio_load_pct: float = 0.0
    open_orders_count: int = 0
    circuit_breaker_active: bool = False
    
    total_trades: int = 0
    win_count: int = 0
    loss_count: int = 0
    
    last_update: float = field(default_factory=now_ts)
    last_signal_ts: Optional[float] = None
    last_heartbeat_ts: float = field(default_factory=now_ts)
    uptime_sec: float = 0.0
    
    last_error: Optional[str] = None
    last_errors: List[str] = field(default_factory=list)
    
    balance_usd: Optional[float] = None
    queue_size: Optional[int] = None


__all__ = [
    "new_uuid",
    "now_ts",
    "EngineMode",
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
    "EngineStatus",
]
