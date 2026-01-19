"""Position Sizing - risk-based quantity calculation."""
import math
from dataclasses import dataclass
from typing import Dict, Any

@dataclass
class SizingResult:
    qty: float
    position_value_usd: float
    risk_per_unit: float
    def to_dict(self) -> Dict[str, Any]:
        return {
            "qty": self.qty,
            "position_value_usd": round(self.position_value_usd, 2),
            "risk_per_unit": round(self.risk_per_unit, 8),
        }

class PositionSizer:
    def __init__(self, max_risk_usd: float = 25.0, max_position_usd: float = 250.0):
        self.max_risk_usd = max_risk_usd
        self.max_position_usd = max_position_usd
    
    def calculate(
        self,
        entry_price: float,
        stop_price: float,
        step_size: float = 0.00001,
        min_qty: float = 0.00001,
        min_notional: float = 10.0,
    ) -> SizingResult:
        if entry_price <= 0:
            raise ValueError("entry_price must be > 0")
        if stop_price <= 0:
            raise ValueError("stop_price must be > 0")
        if step_size <= 0:
            raise ValueError("step_size must be > 0")
        risk_per_unit = abs(entry_price - stop_price)
        if risk_per_unit <= 0:
            raise ValueError("risk_per_unit must be > 0")
        qty_by_risk = self.max_risk_usd / risk_per_unit
        qty_by_value = self.max_position_usd / entry_price
        qty = min(qty_by_risk, qty_by_value)
        qty = math.floor(qty / step_size) * step_size
        if qty < min_qty:
            raise ValueError(f"qty {qty} < min_qty {min_qty}")
        position_value = qty * entry_price
        if position_value < min_notional:
            raise ValueError(f"position_value {position_value} < min_notional {min_notional}")
        return SizingResult(qty=qty, position_value_usd=position_value, risk_per_unit=risk_per_unit)