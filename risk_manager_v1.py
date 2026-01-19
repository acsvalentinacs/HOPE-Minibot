"""Risk Management - fail-closed position control."""
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

@dataclass
class RiskLimits:
    max_risk_per_trade_usd: float = 25.0
    max_position_value_usd: float = 250.0
    max_open_positions: int = 3
    max_daily_loss_usd: float = 75.0
    min_equity_usd: float = 200.0
    min_signal_confidence: float = 0.65
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.04

@dataclass
class RiskVerdict:
    allowed: bool
    reasons: List[str] = field(default_factory=list)
    def to_dict(self) -> Dict[str, Any]:
        return {"allowed": self.allowed, "reasons": self.reasons}

class RiskManager:
    def __init__(self, limits: Optional[RiskLimits] = None):
        self.limits = limits or RiskLimits()
    
    def check_trade(
        self,
        equity_usd: float,
        daily_pnl_usd: float,
        open_positions: int,
        signal_confidence: float,
        position_value_usd: float,
    ) -> RiskVerdict:
        reasons = []
        if equity_usd < self.limits.min_equity_usd:
            reasons.append(f"equity {equity_usd:.2f} < min {self.limits.min_equity_usd}")
        if daily_pnl_usd <= -self.limits.max_daily_loss_usd:
            reasons.append(f"daily loss {abs(daily_pnl_usd):.2f} >= max {self.limits.max_daily_loss_usd}")
        if open_positions >= self.limits.max_open_positions:
            reasons.append(f"positions {open_positions} >= max {self.limits.max_open_positions}")
        if signal_confidence < self.limits.min_signal_confidence:
            reasons.append(f"confidence {signal_confidence:.2f} < min {self.limits.min_signal_confidence}")
        if position_value_usd > self.limits.max_position_value_usd:
            reasons.append(f"position {position_value_usd:.2f} > max {self.limits.max_position_value_usd}")
        return RiskVerdict(allowed=(len(reasons) == 0), reasons=reasons)
    
    def calculate_stops(self, entry_price: float, side: str) -> Dict[str, float]:
        if entry_price <= 0:
            raise ValueError("entry_price must be > 0")
        side = side.upper()
        if side == "BUY":
            stop_loss = entry_price * (1 - self.limits.stop_loss_pct)
            take_profit = entry_price * (1 + self.limits.take_profit_pct)
        elif side == "SELL":
            stop_loss = entry_price * (1 + self.limits.stop_loss_pct)
            take_profit = entry_price * (1 - self.limits.take_profit_pct)
        else:
            raise ValueError("side must be BUY or SELL")
        return {"stop_loss": round(stop_loss, 8), "take_profit": round(take_profit, 8)}