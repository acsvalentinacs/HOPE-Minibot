from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple, Optional
from minibot.indicators import ema

@dataclass
class SignalChecks:
    symbol: str
    last_close: float
    prev_close: float
    atr_abs: float
    impulse_ok: bool
    trend_ok: bool
    volatility_ok: bool
    reasons_extra: list[str] | None = None  # сюда добавим MTF и др.

class ConfidenceScorer:
    """
    Уверенность: импульс (50%), тренд (30%), вола (20%). Порог 80%.
    """
    def __init__(self, w_impulse=0.5, w_trend=0.3, w_vol=0.2, enter_threshold=0.8):
        self.w_impulse = float(w_impulse)
        self.w_trend = float(w_trend)
        self.w_vol = float(w_vol)
        self.enter_threshold = float(enter_threshold)

    def evaluate(self, checks: SignalChecks) -> Tuple[bool, float, List[str]]:
        score = 0.0
        reasons: List[str] = []

        if checks.impulse_ok:
            score += self.w_impulse
        else:
            reasons.append("no impulse")

        if checks.trend_ok:
            score += self.w_trend
        else:
            reasons.append("trend filter")

        if checks.volatility_ok:
            score += self.w_vol
        else:
            reasons.append("low volatility")

        # добавляем внешние причины (MTF, часы, лимиты и т.п.)
        if checks.reasons_extra:
            reasons.extend(checks.reasons_extra)

        ok = score >= self.enter_threshold
        # оставляем только содержательные причины
        keep = {"trend filter", "low volatility", "mtf downtrend", "silent hours", "max concurrent", "daily stop", "flash crash"}
        reasons_filtered = [r for r in reasons if r in keep]
        return ok, score, reasons_filtered

def mtf_trend_up(ohlcv: list[list[float]], fast:int=9, slow:int=50) -> Optional[bool]:
    """
    True = uptrend (EMA9 > EMA50), False = не ап, None = недостаточно данных.
    """
    if not ohlcv or len(ohlcv) < slow + 1:
        return None
    closes = [row[4] for row in ohlcv]
    e9 = ema(closes, fast)
    e50 = ema(closes, slow)
    return e9[-1] > e50[-1]
