# minibot/state_writer.py
# Атомарно пишет снапшоты в logs/state/*
from __future__ import annotations
import json, os, time, tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT   = Path(__file__).resolve().parents[1]
LOGS   = ROOT / "logs"
STATE  = LOGS / "state"
STATE.mkdir(parents=True, exist_ok=True)

def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(path.parent)) as tmp:
        tmp.write(data)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)

def write_positions(rows: List[Dict[str, Any]]) -> None:
    """
    rows: [{symbol, side, qty, entry_price, unrealized_pnl, ...}, ...]
    """
    if not isinstance(rows, list):
        rows = []
    _atomic_write(STATE / "positions.json", json.dumps(rows, ensure_ascii=False))

def write_balance(bal: Dict[str, Any]) -> None:
    """
    Формат 1:
      {"total": {"USDT": 123.45, ...}, "free": {...}}
    Формат 2:
      {"assets": [{"asset":"USDT","free":123.45,"total":123.45}, ...]}
    """
    if not isinstance(bal, dict):
        bal = {}
    _atomic_write(STATE / "balance.json", json.dumps(bal, ensure_ascii=False))

def append_order(order: Dict[str, Any]) -> None:
    """
    order: {ts, symbol, side, type, qty, price, status, ...}
    — дописывает одной строкой JSON
    """
    p = STATE / "orders.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(order, ensure_ascii=False)
    with p.open("a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")

def now_ts() -> float:
    return time.time()
