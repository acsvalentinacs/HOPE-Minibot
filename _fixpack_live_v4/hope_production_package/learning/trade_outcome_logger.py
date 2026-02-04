# -*- coding: utf-8 -*-
"""
TRADE OUTCOME LOGGER v1.0 - Запись результатов для ML
"""

import os, json, time, logging, hashlib
from dataclasses import dataclass, asdict
from typing import Dict, Any, Optional
from pathlib import Path

log = logging.getLogger(__name__)
TRADES_DIR = Path("state/trades")

@dataclass
class TradeOutcome:
    timestamp: float
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    pnl_usdt: float
    pnl_pct: float
    status: str
    duration_sec: float
    signal_delta_pct: float
    signal_type: str
    tp_target_pct: float
    sl_target_pct: float
    
    def to_jsonl(self) -> str:
        d = asdict(self)
        d["_id"] = hashlib.sha256(f"{self.timestamp}:{self.symbol}".encode()).hexdigest()[:16]
        return json.dumps(d, ensure_ascii=False)

class TradeOutcomeLogger:
    def __init__(self):
        self.trades_file = TRADES_DIR / "trades.jsonl"
        self.signals_file = TRADES_DIR / "signals_training.jsonl"
        TRADES_DIR.mkdir(parents=True, exist_ok=True)
        self._total = self._wins = self._losses = 0
        self._pnl = 0.0
    
    def log_trade(self, outcome: TradeOutcome):
        try:
            with open(self.trades_file, "a", encoding="utf-8") as f:
                f.write(outcome.to_jsonl() + "\n")
            self._total += 1
            self._pnl += outcome.pnl_usdt
            if outcome.pnl_usdt > 0:
                self._wins += 1
            else:
                self._losses += 1
            log.info(f"Trade: {outcome.symbol} {outcome.status} ${outcome.pnl_usdt:.2f}")
        except Exception as e:
            log.error(f"Log error: {e}")
    
    def log_signal(self, signal: Dict, decision: str, reason: str):
        try:
            entry = {
                "ts": time.time(), "symbol": signal.get("symbol"),
                "delta": signal.get("delta_pct"), "type": signal.get("type"),
                "decision": decision, "reason": reason
            }
            with open(self.signals_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            log.error(f"Signal log error: {e}")
    
    def get_stats(self):
        wr = self._wins / self._total if self._total > 0 else 0
        return {"trades": self._total, "wins": self._wins, "winrate": f"{wr*100:.1f}%", "pnl": f"${self._pnl:.2f}"}

_logger: Optional[TradeOutcomeLogger] = None

def get_trade_logger():
    global _logger
    if _logger is None:
        _logger = TradeOutcomeLogger()
    return _logger

if __name__ == "__main__":
    print("TRADE OUTCOME LOGGER [PASS]")
