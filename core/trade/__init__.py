# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T16:10:00Z
# Modified by: Claude (opus-4)
# Modified at (UTC): 2026-01-28T10:35:00Z
# Purpose: LIVE Trading Package - fail-closed trading infrastructure with Safety Core
# === END SIGNATURE ===
"""
HOPE LIVE Trading Package.

Provides fail-closed trading infrastructure:
- live_gate: MAINNET access control
- risk_engine: Position/order risk validation
- order_audit: Append-only audit trail
- order_router: Order execution with gates
- position_tracker: Portfolio state
- delisting_detector: Automatic delisting protection

CRITICAL: All trading goes through order_router.
Direct network calls in this package are FORBIDDEN.
"""

from .live_gate import LiveGate, LiveGateResult
from .risk_engine import TradingRiskEngine, TradingRiskLimits
from .order_audit import OrderAudit, AuditEvent
from .order_router import TradingOrderRouter, ExecutionResult
from .delisting_detector import DelistingDetector, DelistingEvent

__all__ = [
    "LiveGate",
    "LiveGateResult",
    "TradingRiskEngine",
    "TradingRiskLimits",
    "OrderAudit",
    "AuditEvent",
    "TradingOrderRouter",
    "ExecutionResult",
    "DelistingDetector",
    "DelistingEvent",
]
