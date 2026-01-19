"""
minibot/rsi_smart_v5.py

RSI/SmartTrend v5 - модуль стратегии:
- add_indicators() - добавляет EMA, RSI, ATR
- backtest_rsi_smart() - симулирует торговлю
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class Trade:
    """Одна завершённая сделка"""
    symbol: str
    side: str  # "LONG"
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    notional_usd: float
    qty: float
    pnl_usd: float
    pnl_pct: float
    bars_held: int
    exit_reason: str


def _ensure_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    """Убедиться, что индекс DatetimeIndex"""
    if not isinstance(df.index, pd.DatetimeIndex):
        if "timestamp" in df.columns:
            df = df.copy()
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df = df.set_index("timestamp")
        else:
            raise ValueError(
                "DataFrame must have DatetimeIndex или колонку 'timestamp' в миллисекундах."
            )
    return df.sort_index()


def add_indicators(
    df: pd.DataFrame,
    rsi_period: int = 14,
    ema_fast_period: int = 9,
    ema_slow_period: int = 50,
    atr_period: int = 14,
) -> pd.DataFrame:
    """
    Добавляет в df колонки: rsi, ema_fast, ema_slow, atr.
    Ожидаются колонки: open, high, low, close.
    """
    df = _ensure_datetime_index(df).copy()

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    # --- EMA ---
    df["ema_fast"] = close.ewm(span=ema_fast_period, adjust=False).mean()
    df["ema_slow"] = close.ewm(span=ema_slow_period, adjust=False).mean()

    # --- RSI (Wilder) ---
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)

    roll_up = up.ewm(alpha=1.0 / rsi_period, adjust=False).mean()
    roll_down = down.ewm(alpha=1.0 / rsi_period, adjust=False).mean()

    rs = roll_up / roll_down.replace(0, np.nan)
    df["rsi"] = 100.0 - (100.0 / (1.0 + rs))

    # --- ATR ---
    prev_close = close.shift(1)
    tr1 = (high - low).abs()
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    df["atr"] = tr.ewm(alpha=1.0 / atr_period, adjust=False).mean()

    return df


def backtest_rsi_smart(
    df: pd.DataFrame,
    symbol: str,
    initial_equity_usd: float = 1000.0,
    notional_usd: float = 100.0,
    rsi_lower: float = 30.0,
    rsi_upper: float = 70.0,
    atr_sl_mult: float = 2.0,
    atr_tp_mult: float = 3.0,
    max_bars_in_trade: int = 48,
) -> Dict[str, Any]:
    """
    Простой бэктест по RSI/SmartTrend:
    - только LONG по тренду (ema_fast > ema_slow);
    - вход по пересечению rsi снизу rsi_lower;
    - выход по rsi > rsi_upper или SL/TP по ATR или timeout.

    df должен уже содержать индикаторы (rsi, ema_fast, ema_slow, atr).
    """
    df = _ensure_datetime_index(df)

    required_cols = ["open", "high", "low", "close", "rsi", "ema_fast", "ema_slow", "atr"]
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"Column '{col}' is required in df for backtest_rsi_smart.")

    equity = float(initial_equity_usd)
    trades: List[Trade] = []

    in_position = False
    entry_price = 0.0
    entry_time: Optional[pd.Timestamp] = None
    qty = 0.0
    bars_held = 0
    sl_price = 0.0
    tp_price = 0.0

    equity_curve: List[Tuple[pd.Timestamp, float]] = []

    closes = df["close"]
    highs = df["high"]
    lows = df["low"]
    ema_fast = df["ema_fast"]
    ema_slow = df["ema_slow"]
    rsi = df["rsi"]
    atr = df["atr"]

    prev_rsi = rsi.shift(1)

    for i, (ts, row) in enumerate(df.iterrows()):
        c = float(row["close"])
        h = float(row["high"])
        l = float(row["low"])
        ef = float(row["ema_fast"])
        es = float(row["ema_slow"])
        r = float(row["rsi"]) if not math.isnan(row["rsi"]) else None
        r_prev = float(prev_rsi.iloc[i]) if not math.isnan(prev_rsi.iloc[i]) else None
        a = float(row["atr"]) if not math.isnan(row["atr"]) else None

        equity_curve.append((ts, equity))

        if r is None or r_prev is None or a is None:
            continue

        # --- Выход из позиции ---
        if in_position:
            bars_held += 1
            exit_reason = None
            exit_price = c

            # SL / TP по ATR
            if l <= sl_price:
                exit_reason = "SL_ATR"
                exit_price = sl_price
            elif h >= tp_price:
                exit_reason = "TP_ATR"
                exit_price = tp_price

            # Выход по RSI
            if exit_reason is None and r >= rsi_upper:
                exit_reason = "RSI_EXIT"

            # Таймаут
            if exit_reason is None and bars_held >= max_bars_in_trade:
                exit_reason = "TIMEOUT"

            if exit_reason is not None:
                pnl_usd = (exit_price - entry_price) * qty
                pnl_pct = pnl_usd / notional_usd if notional_usd > 0 else 0.0
                equity += pnl_usd

                trades.append(
                    Trade(
                        symbol=symbol,
                        side="LONG",
                        entry_time=entry_time or ts,
                        exit_time=ts,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        notional_usd=notional_usd,
                        qty=qty,
                        pnl_usd=pnl_usd,
                        pnl_pct=pnl_pct * 100.0,
                        bars_held=bars_held,
                        exit_reason=exit_reason,
                    )
                )

                in_position = False
                entry_price = 0.0
                entry_time = None
                qty = 0.0
                bars_held = 0
                sl_price = 0.0
                tp_price = 0.0

            continue

        # --- Вход в позицию ---
        # Тренд вверх
        if ef <= es:
            continue

        # Кросс RSI снизу rsi_lower
        if r_prev < rsi_lower <= r:
            entry_price = c
            entry_time = ts

            sl_dist = atr_sl_mult * a
            if sl_dist <= 0:
                continue

            sl_price = entry_price - sl_dist
            tp_price = entry_price + atr_tp_mult * a

            qty = notional_usd / entry_price if entry_price > 0 else 0.0
            if qty <= 0:
                continue

            in_position = True
            bars_held = 0

    # Закрытие висящей позиции в конце
    if in_position and entry_time is not None:
        last_ts = df.index[-1]
        last_close = float(closes.iloc[-1])
        pnl_usd = (last_close - entry_price) * qty
        pnl_pct = pnl_usd / notional_usd if notional_usd > 0 else 0.0
        equity += pnl_usd

        trades.append(
            Trade(
                symbol=symbol,
                side="LONG",
                entry_time=entry_time,
                exit_time=last_ts,
                entry_price=entry_price,
                exit_price=last_close,
                notional_usd=notional_usd,
                qty=qty,
                pnl_usd=pnl_usd,
                pnl_pct=pnl_pct * 100.0,
                bars_held=bars_held,
                exit_reason="FORCE_EXIT_END",
            )
        )

    # Статистика
    if trades:
        pnl_list = [t.pnl_usd for t in trades]
        wins = [p for p in pnl_list if p > 0]
        losses = [p for p in pnl_list if p < 0]

        total_pnl = sum(pnl_list)
        win_rate = len(wins) / len(trades) * 100.0
        avg_pnl = total_pnl / len(trades)
        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    else:
        total_pnl = 0.0
        win_rate = 0.0
        avg_pnl = 0.0
        profit_factor = 0.0

    if equity_curve:
        eq_series = pd.Series(
            data=[v for _, v in equity_curve],
            index=[ts for ts, _ in equity_curve],
        )
        roll_max = eq_series.cummax()
        drawdown = eq_series - roll_max
        max_dd = float(drawdown.min())
        max_dd_pct = (max_dd / initial_equity_usd) * 100.0 if initial_equity_usd > 0 else 0.0
    else:
        eq_series = pd.Series(dtype=float)
        max_dd = 0.0
        max_dd_pct = 0.0

    stats: Dict[str, Any] = {
        "symbol": symbol,
        "initial_equity": initial_equity_usd,
        "final_equity": equity,
        "total_pnl": total_pnl,
        "total_trades": len(trades),
        "win_rate_pct": win_rate,
        "avg_pnl_per_trade": avg_pnl,
        "profit_factor": profit_factor,
        "max_drawdown": max_dd,
        "max_drawdown_pct": max_dd_pct,
    }

    return {
        "symbol": symbol,
        "trades": trades,
        "equity_curve": eq_series,
        "stats": stats,
    }
