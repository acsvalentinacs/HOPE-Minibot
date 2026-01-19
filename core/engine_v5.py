"""
engine_v5.py — минимальный DRY-движок для HOPE v5.

Задача:
- Дать run_live_v5 рабочий ExecutionEngineV5 без внешних зависимостей.
- Хранить позиции в state/exec_positions_v5.json.
- Обеспечить совместимость со SmartTrend (symbol/state).
"""

from __future__ import annotations

import json
import time
import logging
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import List, Dict, Any, Optional


logger = logging.getLogger("ExecutionEngineV5")

ROOT_DIR = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT_DIR / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

POSITIONS_FILE = STATE_DIR / "exec_positions_v5.json"


@dataclass
class PositionV5:
    symbol: str
    side: str
    qty: float
    entry_price: float
    state: str = "OPEN"
    opened_ts: float = field(default_factory=time.time)
    closed_ts: Optional[float] = None
    close_price: Optional[float] = None
    pnl_usd: float = 0.0


class ExecutionEngineV5:
    """
    Упрощённый ExecutionEngineV5 для DRY-режима.

    Ожидаемый интерфейс:
    - __init__(mode: str = "DRY", **kwargs)
    - handle_signal(signal: dict)
    - process_signal(signal: dict)  # алиас
    - get_open_positions() -> list[dict]
    - get_daily_pnl() -> float
    - tick(now=None)  # no-op, на будущее
    """

    def __init__(
        self,
        mode: str = "DRY",
        risk_per_trade_usd: float = 15.0,
        base_equity: float = 1000.0,
        **kwargs: Any,
    ) -> None:
        self.mode = (mode or "DRY").upper()
        self.risk_per_trade_usd = float(risk_per_trade_usd)
        self.base_equity = float(base_equity)
        self.daily_pnl_usd: float = 0.0
        self.positions: List[PositionV5] = []

        self._load_positions()
        logger.info(
            "ExecutionEngineV5 инициализирован. mode=%s, positions=%d",
            self.mode,
            len(self.positions),
        )

    # ---------- Работа с состоянием ----------

    def _load_positions(self) -> None:
        if not POSITIONS_FILE.exists():
            return
        try:
            raw = json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                self.positions = [PositionV5(**p) for p in raw]
        except Exception as e:
            logger.error("Не удалось прочитать %s: %s", POSITIONS_FILE, e)
            self.positions = []

    def _save_positions(self) -> None:
        try:
            data = [asdict(p) for p in self.positions]
            POSITIONS_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("Не удалось записать %s: %s", POSITIONS_FILE, e)

    # ---------- Публичный API для ядра ----------

    def handle_signal(self, signal: Dict[str, Any]) -> None:
        """
        Основная точка входа для run_live_v5.
        """
        side = (signal.get("side") or "").upper()
        symbol = signal.get("symbol") or ""
        price = float(signal.get("price") or 0.0)
        risk_usd = float(signal.get("risk_usd") or self.risk_per_trade_usd)

        if not symbol or price <= 0:
            logger.warning("Некорректный сигнал: %s", signal)
            return

        if side == "LONG":
            self._open_long(symbol, price, risk_usd)
        elif side == "CLOSE":
            reason = signal.get("reason") or ""
            self._close_symbol(symbol, price, reason=reason)
        else:
            logger.info("Игнорирую сигнал с неизвестной стороной: %s", signal)

    def process_signal(self, signal: Dict[str, Any]) -> None:
        """
        Алиас на handle_signal — на случай, если ядро зовёт другое имя.
        """
        self.handle_signal(signal)

    # ---------- Внутренняя логика сделок (DRY) ----------

    def _open_long(self, symbol: str, price: float, risk_usd: float) -> None:
        qty = 0.0
        if price > 0:
            qty = risk_usd / price

        pos = PositionV5(
            symbol=symbol,
            side="LONG",
            qty=qty,
            entry_price=price,
        )
        self.positions.append(pos)
        self._save_positions()

        logger.info(
            "OPEN LONG %s qty=%.6f @ %.2f (risk=%.2f DRY)",
            symbol,
            qty,
            price,
            risk_usd,
        )

    def _close_symbol(self, symbol: str, price: float, reason: str = "") -> None:
        for pos in self.positions:
            if pos.symbol == symbol and pos.state == "OPEN":
                pos.state = "CLOSED"
                pos.closed_ts = time.time()
                pos.close_price = price
                pos.pnl_usd = (price - pos.entry_price) * pos.qty
                self.daily_pnl_usd += pos.pnl_usd
                self._save_positions()

                logger.info(
                    "CLOSE %s @ %.2f PnL=%.4f USD (%s)",
                    symbol,
                    price,
                    pos.pnl_usd,
                    reason,
                )
                break

    # ---------- Методы, которые может вызывать health/диагностика ----------

    def get_open_positions(self) -> List[Dict[str, Any]]:
        return [
            asdict(p)
            for p in self.positions
            if p.state == "OPEN"
        ]

    def get_daily_pnl(self) -> float:
        return float(self.daily_pnl_usd)

    @property
    def open_positions_count(self) -> int:
        return len([p for p in self.positions if p.state == "OPEN"])

    def tick(self, now: Optional[float] = None) -> None:
        """
        На будущее — можно будет сюда положить периодические проверки.
        Сейчас — no-op.
        """
        return


class HOPEEngineV5(ExecutionEngineV5):
    """
    Алиас, чтобы ядро могло импортировать либо HOPEEngineV5, либо ExecutionEngineV5.
    """
    pass


__all__ = ["ExecutionEngineV5", "HOPEEngineV5"]
