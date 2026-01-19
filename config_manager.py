from __future__ import annotations
from typing import Dict, Any, List, Tuple, Optional
import copy
import pathlib
import math

try:
    import pandas as pd  # optional for narrowing by stats
except Exception:
    pd = None  # fallback

import yaml


class ConfigManager:
    def __init__(self, path: str = "risk_config.yaml"):
        self.path = pathlib.Path(path)

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            raise FileNotFoundError(f"{self.path} not found")
        with self.path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def save(self, cfg: Dict[str, Any]) -> None:
        with self.path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)

    # ---------- helpers ----------
    @staticmethod
    def _shift_numeric_list(xs: List[float], delta: float, lo=None, hi=None, as_int: bool=False) -> List[float]:
        if not xs:
            return xs
        out = []
        for x in xs:
            v = x + delta
            if lo is not None: v = max(lo, v)
            if hi is not None: v = min(hi, v)
            out.append(int(round(v)) if as_int else float(v))
        # dedup + sorted
        out = sorted({int(v) if as_int else round(v, 10) for v in out})
        return out

    @staticmethod
    def _bump_steps(xs: List[int], add: int) -> List[int]:
        base = sorted({int(x) for x in xs}) if xs else []
        extra = [int(x)+add for x in base]
        out = sorted({*base, *extra}) if base else [add]
        return out

    @staticmethod
    def _narrow_around_top(df, col: str, top_frac: float=0.2, k: int=3, decimals: int=2) -> List[float]:
        """Возвращает компактный список значений вокруг медианы топ-результатов по ret_pct."""
        if df is None or df.empty or col not in df.columns:
            return []
        n = max(1, int(len(df) * top_frac))
        top = df.sort_values("ret_pct", ascending=False).head(n)
        med = float(top[col].median())
        step = float(top[col].diff().abs().median()) if top[col].nunique() > 1 else 0.0
        if not step or math.isnan(step) or step == 0.0:
            step = max(0.02, abs(med) * 0.05)  # heuristics
        vals = [round(med + j*step, decimals) for j in range(-(k//2), (k//2)+1)]
        return sorted({v for v in vals})

    def _ensure_defaults(self, cfg: Dict[str, Any]) -> None:
        cfg.setdefault("grid", {})
        g = cfg["grid"]
        g.setdefault("sl_atr", [1.0, 1.2, 1.3])
        g.setdefault("tp_atr", [2.4, 2.8, 3.2])
        g.setdefault("atr_len", [14, 21])
        g.setdefault("cooldown_min", [180, 240, 360])
        g.setdefault("enter_th", [0.8, 0.85, 0.9])
        g.setdefault("exit_th", [0.45, 0.5])
        g.setdefault("exit_confirm_bars", [2, 3])

        cfg.setdefault("robustness", {})
        cfg["robustness"].setdefault("neighborhood_pass_rate_pct", 60.0)

        cfg.setdefault("regime_filters", {})
        rf = cfg["regime_filters"]
        rf.setdefault("max_spread_bps", 8)
        rf.setdefault("atr_percentile_range", [10, 95])

    def tweak_params(
        self,
        cfg: Dict[str, Any],
        failure_mode: str,
        iteration_count: int,
        grid_results_df: Optional["pd.DataFrame"] = None,
    ) -> Tuple[Dict[str, Any], str]:
        """
        Многоуровневая эвристика:
          1–3: мелкая подстройка
          4–5: авто-сужение сетки по медианам топ-результатов
          6–7: ужесточение режимных фильтров
          8+: сигнал оркестратору переключить таймфрейм/датасет
        """
        new_cfg = copy.deepcopy(cfg)
        self._ensure_defaults(new_cfg)
        g  = new_cfg["grid"]
        rf = new_cfg["regime_filters"]

        reason = ""
        level = iteration_count

        if level <= 3:
            if failure_mode == "PLATEAU_FAILED":
                g["enter_th"]          = self._shift_numeric_list(g["enter_th"], +0.02, lo=0.0, hi=0.98)
                g["cooldown_min"]      = self._bump_steps(g["cooldown_min"], 60)
                g["exit_confirm_bars"] = self._bump_steps(g["exit_confirm_bars"], 1)
                reason = "L1 Plateau → enter_th+0.02, cooldown+60, exit_cb+1"
            elif failure_mode == "WFA_FAILED":
                g["enter_th"]          = self._shift_numeric_list(g["enter_th"], +0.03, lo=0.0, hi=0.98)
                g["exit_th"]           = self._shift_numeric_list(g["exit_th"],  -0.02, lo=0.1, hi=0.9)
                g["atr_len"]           = sorted({*g["atr_len"], *(int(x)+7 for x in g["atr_len"])})
                g["cooldown_min"]      = self._bump_steps(g["cooldown_min"], 60)
                reason = "L1 WFA → enter_th+0.03, exit_th-0.02, atr_len+7, cooldown+60"
            else:
                reason = "L1 no-op"

        elif level <= 5:
            # авто-сужение по топ-результатам
            if pd is not None and grid_results_df is not None and not grid_results_df.empty:
                tp_new = self._narrow_around_top(grid_results_df, "tp_atr", top_frac=0.2, k=3, decimals=2) or g["tp_atr"]
                sl_new = self._narrow_around_top(grid_results_df, "sl_atr", top_frac=0.2, k=3, decimals=2) or g["sl_atr"]
                g["tp_atr"] = tp_new
                g["sl_atr"] = sl_new
                # слегка поджать вход и увеличить cooldown
                g["enter_th"]     = self._shift_numeric_list(g["enter_th"], +0.01, lo=0.0, hi=0.98)
                g["cooldown_min"] = self._bump_steps(g["cooldown_min"], 60)
                reason = f"L2 Narrow grid → tp_atr{tp_new}, sl_atr{sl_new}, enter_th+0.01, cooldown+60"
            else:
                # если DataFrame нет — fallback к лёгкой подстройке
                g["enter_th"]     = self._shift_numeric_list(g["enter_th"], +0.02, lo=0.0, hi=0.98)
                g["cooldown_min"] = self._bump_steps(g["cooldown_min"], 60)
                reason = "L2 (fallback) → enter_th+0.02, cooldown+60"

        elif level <= 7:
            # режимные фильтры: ужесточаем рынок
            rf["max_spread_bps"] = max(3, int(rf.get("max_spread_bps", 8)) - 2)
            lo, hi = rf.get("atr_percentile_range", [10, 95])
            lo = min(40, max(10, int(lo) + 10))  # поднимаем нижний порог
            rf["atr_percentile_range"] = [lo, hi]
            # + небольшие tweaks сетки
            g["enter_th"] = self._shift_numeric_list(g["enter_th"], +0.01, lo=0.0, hi=0.98)
            reason = f"L3 Regime tighten → max_spread_bps={rf['max_spread_bps']}, atr_percentile_range={rf['atr_percentile_range']}, enter_th+0.01"

        else:
            # сигнал на смену ТФ/датасета
            reason = "SWITCH_TIMEFRAME: exhausted tweaks on current dataset"

        new_cfg["grid"] = g
        new_cfg["regime_filters"] = rf
        return new_cfg, reason
