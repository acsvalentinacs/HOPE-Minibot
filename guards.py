# minibot/guards.py — централизованные предохранители исполнения ордеров

from __future__ import annotations
import os, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNSTOP = ROOT / "RUNSTOP.flag"

def is_dry_run() -> bool:
    v = (os.getenv("HOPE_DRY_RUN","0").strip().lower() in {"1","true","yes"})
    return bool(v)

def is_runstop() -> bool:
    try:
        return RUNSTOP.exists()
    except Exception:
        return True

def is_strict_live_allowed() -> bool:
    # Только если явное подтверждение LIVE
    return (os.getenv("HOPE_STRICT_BINANCE","0").strip().lower() in {"1","true","yes"})

def can_execute_real_orders() -> bool:
    # Двойной предохранитель: НЕЛЬЗЯ исполнять, если DRY_RUN ИЛИ RUNSTOP
    if is_dry_run(): return False
    if is_runstop(): return False
    # Доп. защита: даже если DRY_RUN=0 и нет RUNSTOP — требуем строгий флаг
    if not is_strict_live_allowed(): return False
    return True

def require_live_or_raise():
    if not can_execute_real_orders():
        raise RuntimeError("LIVE запрещён: HOPE_DRY_RUN!=0 или RUNSTOP.flag, либо отсутствует HOPE_STRICT_BINANCE=1")
