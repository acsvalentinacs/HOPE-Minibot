from __future__ import annotations
from typing import Iterable, List

def ema(values: Iterable[float], period: int) -> List[float]:
    vals = list(float(x) for x in values)
    if period <= 1 or len(vals) == 0:
        return vals
    k = 2.0 / (period + 1.0)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(out[-1] + k * (v - out[-1]))
    return out

def true_range(h: float, l: float, prev_close: float) -> float:
    return max(h - l, abs(h - prev_close), abs(l - prev_close))

def atr(ohlcv: list[list[float]], period: int = 14) -> float:
    """
    ohlcv: [[ts, open, high, low, close, vol], ...]
    Возвращает последний ATR (абсолютный).
    """
    if not ohlcv or len(ohlcv) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(ohlcv)):
        _, _, h, l, c, _ = ohlcv[i]
        prev_c = ohlcv[i-1][4]
        trs.append(true_range(h, l, prev_c))
    # SMA по TR за период
    if len(trs) < period:
        return sum(trs) / max(1, len(trs))
    return sum(trs[-period:]) / period
