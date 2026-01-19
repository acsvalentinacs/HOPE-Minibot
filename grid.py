from __future__ import annotations
import itertools
import math
from pathlib import Path
from typing import Dict, Any, List

import numpy as np
import pandas as pd

from .brain import init_brain
from .core import load_csv, simulate, RunParams

def _resolved_filters_from_cfg(cfg: Dict[str, Any]) -> Dict[str, float]:
    mf = cfg.get("metrics_filters", {}) or {}
    return {
        "min_trades":      int(mf.get("min_trades", 0)),
        "min_return_pct":  float(mf.get("min_return_pct", -1e9)),
        "max_drawdown_pct":float(mf.get("max_drawdown_pct",  1e9)),
        "min_sharpe":      float(mf.get("min_sharpe", -1e9)),
    }

def _grid_params(cfg: Dict[str, Any]) -> List[RunParams]:
    g = cfg.get("grid", {}) or {}
    sl_atr   = g.get("sl_atr",   [1.0])
    tp_atr   = g.get("tp_atr",   [2.0])
    atr_len  = g.get("atr_len",  [14])
    cooldown = g.get("cooldown_min", [0])
    enter_th = g.get("enter_th", [0.8])
    exit_th  = g.get("exit_th",  [0.5])
    exit_cb  = g.get("exit_confirm_bars", [2])

    exec_cfg = cfg.get("execution", {}) or {}
    enter_on_next_open = bool(exec_cfg.get("enter_on_next_open", False))

    out: List[RunParams] = []
    for a,b,c,d,e,f,h in itertools.product(sl_atr, tp_atr, atr_len, cooldown, enter_th, exit_th, exit_cb):
        out.append(RunParams(
            sl_atr=float(a),
            tp_atr=float(b),
            atr_len=int(c),
            cooldown_min=int(d),
            enter_th=float(e),
            exit_th=float(f),
            exit_confirm_bars=int(h),
            enter_on_next_open=enter_on_next_open
        ))
    return out

def _print_header(csv_path: str, equity: float, risk: float, filters: Dict[str, float], combos: int):
    print("[MiniBot] GRID-SEARCH START")
    print(f"[MiniBot] CSV={csv_path}  equity={equity}  risk={risk}")
    print("[MiniBot] Filters (resolved): "
          f"min_trades={filters['min_trades']}, "
          f"min_return_pct={filters['min_return_pct']}, "
          f"max_drawdown_pct={filters['max_drawdown_pct']}, "
          f"min_sharpe={filters['min_sharpe']}")
    print(f"[MiniBot] Combinations: {combos}")

def grid_search(csv_path: str,
                equity: float,
                risk_per_trade: float,
                cfg: Dict[str, Any]) -> pd.DataFrame:
    df = load_csv(csv_path)
    brain = init_brain(cfg, df)

    print(f"[MiniBot] Precomputing {len(df)} votes...")
    votes = np.array([brain.vote(df, i) for i in range(len(df))], dtype=float)

    filters = _resolved_filters_from_cfg(cfg)
    params_list = _grid_params(cfg)
    _print_header(csv_path, equity, risk_per_trade, filters, len(params_list))

    cost_cfg = cfg.get("costs", {}) or {}
    fee_bps  = float(cost_cfg.get("fee_bps", 6.0))
    slip_bps = float(cost_cfg.get("slip_bps", 1.0))

    rows = []
    for rp in params_list:
        res = simulate(df, votes, equity, risk_per_trade, rp, fee_bps=fee_bps, slip_bps=slip_bps)

        if res.trades < filters["min_trades"]:
            continue
        if res.ret_pct < filters["min_return_pct"]:
            continue
        if res.max_dd_pct < filters["max_drawdown_pct"]:
            continue
        if res.sharpe < filters["min_sharpe"]:
            continue

        rows.append({
            "sl_atr": rp.sl_atr,
            "tp_atr": rp.tp_atr,
            "atr_len": rp.atr_len,
            "cooldown_min": rp.cooldown_min,
            "enter_th": rp.enter_th,
            "exit_th": rp.exit_th,
            "exit_confirm_bars": rp.exit_confirm_bars,
            "trades": res.trades,
            "ret_pct": round(res.ret_pct, 4),
            "max_dd_pct": round(res.max_dd_pct, 4),
            "sharpe": round(res.sharpe, 4),
            "profit_factor": round(res.profit_factor, 4) if math.isfinite(res.profit_factor) else float("inf"),
            "avg_pnl_per_trade": round(res.avg_pnl_per_trade, 6),
            "pnl_sum": round(res.pnl_sum, 2),
            "gross_profit": round(res.gross_profit, 2),
            "gross_loss": round(res.gross_loss, 2),
        })

    if not rows:
        return pd.DataFrame()

    df_out = pd.DataFrame(rows).sort_values(
        by=["ret_pct", "profit_factor", "sharpe", "trades"],
        ascending=[False, False, False, False]
    ).reset_index(drop=True)

    _store_results_safe(df_out)

    head = df_out.head(5)
    print("[MiniBot] TOP-5 (ret_pct, PF, sharpe, trades):")
    for i, r in head.iterrows():
        print(f"  #{i+1}: ret={r.ret_pct:.2f}%  PF={r.profit_factor}  sh={r.sharpe:.2f}  n={int(r.trades)}  "
              f"sl={r.sl_atr} tp={r.tp_atr} enter={r.enter_th} exit={r.exit_th}/{int(r.exit_confirm_bars)} cd={int(r.cooldown_min)}m")
    return df_out

def _store_results_safe(df_out: pd.DataFrame):
    try:
        from .storage import GridStorage
        try:
            gs = GridStorage()
            gs.write_dataframe(df_out)
            print("[MiniBot] Results written via storage.")
            return
        except Exception:
            pass
    except Exception:
        pass

    runs = Path("runs")
    runs.mkdir(parents=True, exist_ok=True)
    out = runs / "grid_results.csv"
    if out.exists():
        df_prev = pd.read_csv(out)
        df_merged = pd.concat([df_prev, df_out], ignore_index=True)
    else:
        df_merged = df_out
    df_merged.to_csv(out, index=False, encoding="utf-8")
    print(f"[MiniBot] Results appended to {out}")
