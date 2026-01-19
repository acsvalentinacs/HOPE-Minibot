from __future__ import annotations
import os
import threading
from prometheus_client import start_http_server, Gauge

_started = False
_lock = threading.Lock()

# ---- Live / runtime ----
ntp_offset   = Gauge("mb_ntp_offset_seconds", "NTP clock offset (sec)")
daily_pnl    = Gauge("mb_daily_realized_pnl_usd", "Daily realized PnL (USD)")
loss_streak  = Gauge("mb_loss_streak", "Consecutive losing trades today")
trades_today = Gauge("mb_trades_today", "Trades executed today")
vote_last    = Gauge("mb_vote", "Last brain vote (0..1)")
price_last   = Gauge("mb_price", "Last price")
interlock_ok = Gauge("mb_interlock_ok", "Interlocks state (1 ok / 0 blocked)")
shadow_mode  = Gauge("mb_shadow_mode", "Shadow mode (1 enabled, 0 disabled)")
fsm_state    = Gauge("mb_fsm_state", "Orchestrator FSM: 0=INIT,1=GRID,2=PLATEAU,3=WFA,4=READY,5=LIVE")

# ---- Pipeline stages ----
grid_combinations     = Gauge("mb_grid_combinations", "Grid combinations in last run")
grid_runtime_sec      = Gauge("mb_grid_runtime_seconds", "Grid stage runtime (sec)")

plateau_neighbors     = Gauge("mb_plateau_neighbors", "Neighbors tested in plateau audit (last run)")
plateau_pass_rate_pct = Gauge("mb_plateau_pass_rate_pct", "Plateau pass rate % (last run)")

wfa_windows_total     = Gauge("mb_wfa_windows_total", "WFA windows total (last run)")
wfa_windows_positive  = Gauge("mb_wfa_windows_positive", "WFA windows positive (last run)")

stage_rc              = Gauge("mb_stage_rc", "Last stage return code (0=ok)")
stage_end_unix        = Gauge("mb_stage_end_unix", "Last stage end time (unix)")

def ensure_server(port: int | None = None) -> int:
    global _started
    with _lock:
        if _started:
            return int(os.getenv("MB_METRICS_PORT", port or 9409))
        p = int(os.getenv("MB_METRICS_PORT", port or 9409))
        start_http_server(p)
        _started = True
        return p
