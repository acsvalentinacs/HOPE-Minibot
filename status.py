# minibot/status.py — безопасный статус без падений на slippage_guard

from typing import Any

def _safe(getter, default: str = "—"):
    try:
        v = getter()
        return "—" if v is None else v
    except Exception:
        return default

def build_status_text(rm: Any) -> str:
    """
    Собирает короткую сводку для heartbeat/статуса.
    Предполагаемые поля rm:
      - dry_run: bool
      - max_concurrent: int
      - daily_stop_usd: float
      - slippage_guard: объект с полем max_bps
      - tp_atr_mult / sl_atr_mult (если есть)
      - flags: dict-like (RUNSTOP / COOLDOWN), если есть
    Все обращения безопасны.
    """
    dry = _safe(lambda: rm.dry_run, "—")
    mc  = _safe(lambda: rm.max_concurrent, "—")
    ds  = _safe(lambda: f"{float(rm.daily_stop_usd):.2f}", "—")
    tp  = _safe(lambda: f"{float(getattr(rm, 'tp_atr_mult', 0.0)):.2f}", "—")
    sl  = _safe(lambda: f"{float(getattr(rm, 'sl_atr_mult', 0.0)):.2f}", "—")
    # ВАЖНО: используем slippage_guard, а не slip_guard
    bps = _safe(lambda: f"{float(getattr(rm.slippage_guard, 'max_bps', 0.0)):.2f}", "—")

    # Флаги, если есть
    runstop = _safe(lambda: bool(getattr(rm, 'flags', {}).get('RUNSTOP', False)), "—")
    cooldown = _safe(lambda: bool(getattr(rm, 'flags', {}).get('COOLDOWN', False)), "—")

    lines = [
        "Minibot статус:",
        f"Dry run: {dry}",
        f"Max concurrent: {mc}",
        f"Daily stop, USD: {ds}",
        f"TP x ATR: {tp}",
        f"SL x ATR: {sl}",
        f"Slip Max (bps): {bps}",
        f"RUNSTOP: {runstop}",
        f"COOLDOWN: {cooldown}",
    ]
    return "\n".join(lines)
