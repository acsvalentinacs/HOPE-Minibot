from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .execution_layer import ExecutionContext, TradeMode, CircuitStatus, ApiStatus


log = logging.getLogger(__name__)


@dataclass
class HealthSnapshot:
    ts: str
    version: str
    mode: str

    positions_count: int
    open_symbols: List[str]

    realized_pnl: float
    unrealized_pnl: float

    daily_pnl: float
    daily_loss_limit: float

    api_status: str
    error_count: int
    last_error: Optional[str]

    circuit_status: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_health_snapshot(
    ctx: ExecutionContext,
    *,
    version: str,
    positions: List[Any],
    realized_pnl: float,
    unrealized_pnl: float,
    daily_pnl: float,
    daily_loss_limit: float,
) -> HealthSnapshot:
    """
    Конструктор HealthSnapshot.

    positions — список объектов позиций (любой структуры), от них нам нужен только symbol.
    """
    ts = datetime.now(timezone.utc).isoformat()
    open_symbols = []
    for p in positions:
        sym = getattr(p, "symbol", None) or getattr(p, "pair", None)
        if sym:
            open_symbols.append(str(sym))

    snapshot = HealthSnapshot(
        ts=ts,
        version=version,
        mode=ctx.mode.value,
        positions_count=len(positions),
        open_symbols=open_symbols,
        realized_pnl=float(realized_pnl),
        unrealized_pnl=float(unrealized_pnl),
        daily_pnl=float(daily_pnl),
        daily_loss_limit=float(daily_loss_limit),
        api_status=ctx.api_status.value,
        error_count=ctx.error_count,
        last_error=ctx.last_error,
        circuit_status=ctx.circuit_status.value,
    )
    return snapshot


def write_health_snapshot(snapshot: HealthSnapshot, path: Path) -> None:
    """
    Атомарная запись health.json.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = snapshot.to_dict()
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to write health snapshot: %r", exc)
