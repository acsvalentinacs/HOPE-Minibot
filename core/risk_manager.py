from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Tuple

# Импортируем обновленные типы
from .models import TradeSignal, PositionInfo

@dataclass
class RiskConfig:
    """
    Конфигурация рисков (совместима с тем, что ждет run_live_v5).
    """
    max_risk_per_signal_usd: float
    max_daily_loss_usd: float
    max_open_positions: int
    max_position_size_usd: float
    min_equity_usd: float
    min_signal_confidence: float
    per_symbol_caps_usd: Dict[str, float] = None


class RiskManager:
    def __init__(self, config: RiskConfig) -> None:
        self.config = config
        self._daily_pnl_usd: float = 0.0

    @property
    def daily_pnl_usd(self) -> float:
        return self._daily_pnl_usd

    def can_open(
        self,
        signal: TradeSignal,
        current_positions: List[PositionInfo],
        equity_usd: float,
    ) -> Tuple[bool, str, float]:
        """
        Возвращает (разрешено?, причина, размер_позиции_usd).
        """
        # 1. Проверка дневного стопа
        if self.daily_stop_hit():
            return False, "Daily stop hit", 0.0

        # 2. Лимит кол-ва позиций
        if self.config.max_open_positions > 0:
            if len(current_positions) >= self.config.max_open_positions:
                return False, f"Max positions ({self.config.max_open_positions}) reached", 0.0

        # 3. Расчет размера
        # Базовый лимит
        base_cap = self.config.max_position_size_usd
        if base_cap <= 0:
            base_cap = equity_usd

        # Лимит сигнала
        req_risk = signal.risk_usd
        if req_risk <= 0:
            req_risk = base_cap
        
        allowed = min(base_cap, req_risk, equity_usd)
        
        if allowed <= 0:
             return False, "Calculated size is 0", 0.0

        return True, "OK", allowed

    def notify_trade_result(self, pnl_usd: float) -> None:
        self._daily_pnl_usd += float(pnl_usd)

    def daily_stop_hit(self) -> bool:
        if self.config.max_daily_loss_usd >= 0:
            return False # Стоп выключен или настроен неверно (обычно это отрицательное число)
        
        # Если убыток -60, а стоп -50 -> True
        return self._daily_pnl_usd <= -abs(self.config.max_daily_loss_usd)
