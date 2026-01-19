from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, List, Tuple
import pandas as pd
import numpy as np
from dateutil.relativedelta import relativedelta

from . import config
from .brain import init_brain
from .core import load_csv, simulate, RunParams, RunResult

# ---------- общие помощники ----------
def _resolved_filters_from_cfg(cfg: Dict[str, Any]) -> Dict[str, float]:
    mf = cfg.get("metrics_filters", {}) or {}
    return {
        "min_trades":       int(mf.get("min_trades", 0)),
        "min_return_pct":   float(mf.get("min_return_pct", -1e9)),
        "max_drawdown_pct": float(mf.get("max_drawdown_pct",  1e9)),
        "min_sharpe":       float(mf.get("min_sharpe", -1e9)),
    }

def _get_best_params() -> Tuple[RunParams, pd.DataFrame]:
    src = Path("runs/grid_results.csv")
    if not src.exists():
        raise FileNotFoundError("runs/grid_results.csv not found. Run grid-search first.")
    df = pd.read_csv(src)
    if df.empty:
        raise ValueError("grid_results.csv is empty.")
    df_top = df.sort_values(by="ret_pct", ascending=False)
    best = df_top.iloc[0]
    rp = RunParams(
        sl_atr=float(best["sl_atr"]),
        tp_atr=float(best["tp_atr"]),
        atr_len=int(best["atr_len"]),
        cooldown_min=int(best["cooldown_min"]),
        enter_th=float(best["enter_th"]),
        exit_th=float(best["exit_th"]),
        exit_confirm_bars=int(best["exit_confirm_bars"]),
        enter_on_next_open=False
    )
    return rp, df_top

# ============================
# 1) Walk-Forward Analysis
# ============================
def _get_wfa_windows(df: pd.DataFrame, wfa_cfg: Dict[str, Any]) -> List[dict]:
    is_m   = int(wfa_cfg.get("is_months", 6))
    oos_m  = int(wfa_cfg.get("oos_months", 3))
    step_m = int(wfa_cfg.get("roll_step_months", 3))
    start_date = df["ts"].min()
    end_date   = df["ts"].max()
    windows: List[dict] = []
    cur = start_date
    while True:
        is_end  = cur + relativedelta(months=is_m)
        oos_end = is_end + relativedelta(months=oos_m)
        if oos_end > end_date:
            break
        windows.append({"is_start": cur, "is_end": is_end, "oos_start": is_end, "oos_end": oos_end})
        cur = cur + relativedelta(months=step_m)
    return windows

def run_wfa(csv_path: str, equity: float, risk_per_trade: float, cfg: Dict[str, Any]):
    print("[MiniBot] WFA TUNE START")
    wfa_cfg  = cfg.get("wfa", {}) or {}
    cost_cfg = cfg.get("costs", {}) or {}
    fee_bps  = float(cost_cfg.get("fee_bps", 6.0))
    slip_bps = float(cost_cfg.get("slip_bps", 1.0))

    best_rp, _ = _get_best_params()
    print(f"[MiniBot] Best params from grid: SL={best_rp.sl_atr}, TP={best_rp.tp_atr}, "
          f"Enter={best_rp.enter_th}, Exit={best_rp.exit_th}/{best_rp.exit_confirm_bars}, "
          f"CD={best_rp.cooldown_min}m")

    df = load_csv(csv_path)
    brain = init_brain(cfg, df)
    print(f"[MiniBot] Precomputing {len(df)} votes for WFA...")
    votes = np.array([brain.vote(df, i) for i in range(len(df))], dtype=float)

    windows = _get_wfa_windows(df, wfa_cfg)
    if not windows:
        print("[MiniBot] ERROR: Not enough data for WFA windows.")
        return

    print(f"[MiniBot] Running WFA on {len(windows)} OOS windows (with costs)...")
    oos_rows = []
    oos_results: List[RunResult] = []
    for i, w in enumerate(windows):
        mask = (df["ts"] >= w["oos_start"]) & (df["ts"] < w["oos_end"])
        df_oos   = df[mask].reset_index(drop=True)
        votes_oos = votes[mask.values]
        if df_oos.empty:
            continue
        res = simulate(df_oos, votes_oos, equity, risk_per_trade, best_rp, fee_bps=fee_bps, slip_bps=slip_bps)
        oos_results.append(res)
        oos_rows.append({
            "window": i + 1,
            "oos_start": w["oos_start"],
            "oos_end": w["oos_end"],
            "trades": res.trades,
            "ret_pct": res.ret_pct,
            "pnl_sum": res.pnl_sum,
            "profit_factor": res.profit_factor,
            "max_dd_pct": res.max_dd_pct,
            "sharpe": res.sharpe
        })
        print(f"  Window {i+1} [{w['oos_start'].date()} -> {w['oos_end'].date()}]: "
              f"Trades={res.trades}, PnL={res.pnl_sum:+.2f} ({res.ret_pct:+.2f}%), "
              f"PF={res.profit_factor:.2f}, DD={res.max_dd_pct:.2f}%")

    n_pos = sum(1 for r in oos_results if r.pnl_sum > 0)
    n_tot = len(oos_results)
    pass_rate = (n_pos / n_tot * 100.0) if n_tot > 0 else 0.0
    req = int(wfa_cfg.get("require_positive_oos_windows", 0))
    print("-" * 30)
    print(f"[MiniBot] WFA Summary: {n_pos} of {n_tot} OOS windows were profitable ({pass_rate:.1f}%)")
    status = "PASSED" if n_pos >= req else "FAILED"
    print(f"[MiniBot] WFA {status} (>= {req} positive windows required)")

    runs = Path("runs"); runs.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(oos_rows).to_csv(runs / "wfa_results.csv", index=False, encoding="utf-8")
    (runs / "wfa_summary.txt").write_text(
        f"WFA {status}\n"
        f"Positive windows: {n_pos}/{n_tot} ({pass_rate:.1f}%)\n"
        f"Requirement: >= {req}\n",
        encoding="utf-8"
    )
    print(f"[MiniBot] WFA results saved to runs/wfa_results.csv")

# ============================
# 2) Plateau Audit (по соседям)
# ============================
def run_plateau_audit(cfg: Dict[str, Any]):
    print("[MiniBot] PLATEAU AUDIT START")
    src = Path("runs/grid_results.csv")
    if not src.exists():
        raise FileNotFoundError("grid_results.csv not found.")
    df = pd.read_csv(src)
    if df.empty:
        print("[MiniBot] ERROR: grid_results.csv is empty.")
        return

    filters = _resolved_filters_from_cfg(cfg)
    rob_cfg = cfg.get("robustness", {}) or {}
    req_pass_rate = float(rob_cfg.get("neighborhood_pass_rate_pct", 60.0))

    df_sorted = df.sort_values(by="ret_pct", ascending=False)
    best = df_sorted.iloc[0]
    print(f"[MiniBot] Best params found: ret={best['ret_pct']:.2f}%, PF={best['profit_factor']:.2f}, "
          f"sl={best['sl_atr']}, tp={best['tp_atr']}, enter={best['enter_th']}")

    param_cols = ["sl_atr", "tp_atr", "atr_len", "cooldown_min", "enter_th", "exit_th", "exit_confirm_bars"]

    audit_rows = []
    neighbors_count = 0
    passed_neighbors = 0

    for i, row in df.iterrows():
        if i == best.name:
            continue
        diff_count = 0
        diff_col = ""
        for col in param_cols:
            if col in row and col in best and row[col] != best[col]:
                diff_count += 1
                diff_col = col
        if diff_count == 1:
            neighbors_count += 1
            is_ok = (
                row["trades"]   >= filters["min_trades"] and
                row["ret_pct"]  >= filters["min_return_pct"] and
                row["max_dd_pct"] >= filters["max_drawdown_pct"] and
                row["sharpe"]   >= filters["min_sharpe"]
            )
            if is_ok:
                passed_neighbors += 1
            audit_rows.append({
                "neighbor_param": diff_col,
                "value": row[diff_col],
                "ret_pct": row["ret_pct"],
                "pf": row["profit_factor"],
                "ok": bool(is_ok)
            })

    if neighbors_count == 0:
        print("[MiniBot] ERROR: No neighbors found.")
        return

    pass_rate = (passed_neighbors / neighbors_count * 100.0)
    status = "PASSED" if pass_rate >= req_pass_rate else "FAILED"
    print("-" * 30)
    print(f"[MiniBot] Plateau Audit Summary: {passed_neighbors} of {neighbors_count} neighbors passed filters.")
    print(f"[MiniBot] Pass Rate: {pass_rate:.1f}% (Required: {req_pass_rate}%)")
    print(f"[MiniBot] Plateau Audit {status}")

    runs = Path("runs"); runs.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(audit_rows).to_csv(runs / "plateau_audit.csv", index=False, encoding="utf-8")
    (runs / "plateau_summary.txt").write_text(
        f"PLATEAU {status}\nPass Rate: {pass_rate:.1f}%\nRequirement: >= {req_pass_rate}%\n",
        encoding="utf-8"
    )
    print(f"[MiniBot] Plateau audit results saved to runs/plateau_audit.csv")
