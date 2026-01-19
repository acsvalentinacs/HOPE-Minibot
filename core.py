from __future__ import annotations
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, List

import numpy as np
import pandas as pd

from .brain import init_brain

# ============================
# Utils: CSV loader (robust)
# ============================
def load_csv(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"CSV not found: {p}")

    df = pd.read_csv(p)
    cols_map = {c: c.strip() for c in df.columns}
    df.rename(columns=cols_map, inplace=True)
    lower = {c.lower(): c for c in df.columns}

    def pick(*names):
        for n in names:
            n2 = lower.get(n.lower())
            if n2 in df.columns:
                return n2
        return None

    ts_col   = pick("ts", "timestamp", "date", "time", "open_time", "Open time")
    open_col = pick("open", "Open", "o")
    high_col = pick("high", "High", "h")
    low_col  = pick("low", "Low", "l")
    close_col= pick("close", "Close", "c")
    vol_col  = pick("volume", "Volume", "v", "quote_volume", "Volume USDT")

    need = [open_col, high_col, low_col, close_col]
    if any(x is None for x in need):
        raise ValueError("CSV must contain OHLC columns (open,high,low,close).")

    out = pd.DataFrame({
        "open":  df[open_col].astype(float),
        "high":  df[high_col].astype(float),
        "low":   df[low_col].astype(float),
        "close": df[close_col].astype(float),
        "volume": df[vol_col].astype(float) if vol_col else np.zeros(len(df), dtype=float),
    })
    if ts_col:
        out["ts"] = pd.to_datetime(df[ts_col], errors="coerce", utc=True).dt.tz_convert(None)
    else:
        out["ts"] = pd.date_range("2000-01-01", periods=len(out), freq="H")

    return out.dropna().reset_index(drop=True)

# ============================
# Math helpers
# ============================
def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        (df["high"] - df["low"]),
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False).mean()

def detect_bar_minutes(ts: pd.Series) -> int:
    if ts.isna().all() or len(ts) < 3:
        return 60
    d = ts.diff().dropna().dt.total_seconds() / 60.0
    med = float(np.median(d.values)) if len(d) else 60.0
    candidates = [1, 3, 5, 15, 30, 60, 120, 240, 1440]
    best = min(candidates, key=lambda x: abs(x - med))
    return int(best)

# ============================
# Backtest core (long-only)
# ============================
@dataclass
class RunParams:
    sl_atr: float
    tp_atr: float
    atr_len: int
    cooldown_min: int
    enter_th: float
    exit_th: float
    exit_confirm_bars: int
    enter_on_next_open: bool

@dataclass
class RunResult:
    params: RunParams
    trades: int
    ret_pct: float
    max_dd_pct: float
    sharpe: float
    profit_factor: float
    avg_pnl_per_trade: float  # %
    pnl_sum: float
    gross_profit: float
    gross_loss: float
    df_len: int

def simulate(df: pd.DataFrame,
             votes: np.ndarray,
             equity0: float,
             risk_per_trade: float,
             rp: RunParams,
             fee_bps: float = 6.0,
             slip_bps: float = 1.0) -> RunResult:
    """
    Симуляция с комиссиями и проскальзыванием:
      - slippage применяется симметрично: вход дороже, выход дешевле
      - комиссии: на вход и на выход по fee_bps
    """
    n = len(df)
    if n < 3:
        return RunResult(rp, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, n)

    bar_min = detect_bar_minutes(df["ts"])
    cooldown_bars = max(0, int(round(rp.cooldown_min / max(1, bar_min))))

    atr_ts = atr(df, rp.atr_len)
    atr_val = atr_ts.values
    high = df["high"].values
    low  = df["low"].values
    openp= df["open"].values
    close= df["close"].values

    equity = equity0
    peak_equity = equity0
    max_dd = 0.0

    in_pos = False
    entry_price = 0.0
    entry_equity = equity0
    atr_entry = 0.0
    pos_qty = 0.0
    sl_px = 0.0
    tp_px = 0.0
    cooldown = 0

    exit_hold = 0
    trade_pnls: List[float] = []
    port_ret: List[float] = []

    slip = slip_bps / 10000.0
    fee  = fee_bps  / 10000.0

    for i in range(1, n):
        v = float(votes[i])
        port_ret.append(0.0)

        if in_pos:
            dP = close[i] - close[i-1]
            if np.isfinite(dP) and pos_qty != 0.0:
                port_ret[-1] = (pos_qty * dP) / max(1e-12, equity)

            hit_sl = low[i] <= sl_px if np.isfinite(sl_px) else False
            hit_tp = high[i] >= tp_px if np.isfinite(tp_px) else False

            exit_now = False
            price_exit_raw = close[i]

            if hit_sl and hit_tp:
                exit_now = True
                price_exit_raw = sl_px
            elif hit_sl:
                exit_now = True
                price_exit_raw = sl_px
            elif hit_tp:
                exit_now = True
                price_exit_raw = tp_px
            else:
                if v <= rp.exit_th:
                    exit_hold += 1
                    if exit_hold >= rp.exit_confirm_bars:
                        exit_now = True
                        price_exit_raw = close[i]
                else:
                    exit_hold = 0

            if exit_now:
                # Эффективные цены выхода с проскальзыванием
                price_exit_eff = price_exit_raw * (1 - slip)
                entry_eff      = entry_price   * (1 + slip)

                gross = pos_qty * (price_exit_eff - entry_eff)
                fee_value = abs(pos_qty * entry_eff) * fee + abs(pos_qty * price_exit_eff) * fee
                pnl_value = gross - fee_value

                equity += pnl_value
                trade_pnls.append(pnl_value)

                in_pos = False
                pos_qty = 0.0
                exit_hold = 0
                cooldown = cooldown_bars

                peak_equity = max(peak_equity, equity)
                dd = (equity - peak_equity) / max(1e-12, peak_equity)
                max_dd = min(max_dd, dd)
        else:
            if cooldown > 0:
                cooldown -= 1
            elif v >= rp.enter_th:
                atr_now = float(atr_val[i])
                if np.isfinite(atr_now) and atr_now > 0.0:
                    in_pos = True
                    entry_equity = equity
                    raw_entry = openp[i] if rp.enter_on_next_open else close[i - 1]
                    entry_price = raw_entry
                    atr_entry = atr_now
                    pos_qty = (entry_equity * risk_per_trade) / max(1e-12, rp.sl_atr * atr_entry)
                    sl_px = entry_price - rp.sl_atr * atr_entry
                    tp_px = entry_price + rp.tp_atr * atr_entry
                    exit_hold = 0

        peak_equity = max(peak_equity, equity)
        dd = (equity - peak_equity) / max(1e-12, peak_equity)
        max_dd = min(max_dd, dd)

    trades = len(trade_pnls)
    ret_pct = (equity - equity0) / equity0 * 100.0

    gp = sum(x for x in trade_pnls if x > 0)
    gl = sum(x for x in trade_pnls if x < 0)
    profit_factor = float("inf") if gl == 0 else (gp / abs(gl)) if abs(gl) > 1e-12 else float("inf")
    avg_pnl = (np.mean(trade_pnls) / equity0 * 100.0) if trades > 0 else 0.0

    r = np.array(port_ret, dtype=float)
    r = r[np.isfinite(r)]
    mean_r = float(np.mean(r)) if len(r) else 0.0
    std_r  = float(np.std(r)) if len(r) else 0.0
    bars_per_year = int(round((60*24*365) / max(1, detect_bar_minutes(df["ts"]))))
    sharpe = (mean_r / std_r) * math.sqrt(bars_per_year) if std_r > 1e-12 else 0.0

    return RunResult(
        params=rp,
        trades=trades,
        ret_pct=ret_pct,
        max_dd_pct=max_dd * 100.0,
        sharpe=sharpe,
        profit_factor=float(profit_factor),
        avg_pnl_per_trade=avg_pnl,
        pnl_sum=(equity - equity0),
        gross_profit=gp,
        gross_loss=gl,
        df_len=n
    )
