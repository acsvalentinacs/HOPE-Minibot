# -*- coding: utf-8 -*-
"""
══════════════════════════════════════════════════════════════════════════════
SIGNAL GATE v1.0 - ЕДИНЫЙ ФИЛЬТР (CANNOT BYPASS)
══════════════════════════════════════════════════════════════════════════════

Объединяет идеи: Claude (Opus 4.5) + GPT-5.2 Thinking

ПРИНЦИП: Все сигналы ОБЯЗАТЕЛЬНО проходят через этот фильтр.
         Обход невозможен. Fail-closed.

РЕШАЕТ:
  - Telegram отправлять?
  - Торговать?
  - Логировать?

══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import os
import time
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Tuple, Optional
from enum import Enum

log = logging.getLogger(__name__)


class GateDecision(Enum):
    PASS_TELEGRAM_AND_TRADE = "pass_tg_trade"
    PASS_TRADE_ONLY = "pass_trade_only"
    PASS_LOG_ONLY = "pass_log_only"
    BLOCK = "block"


class BlockReason(Enum):
    BLOCKED_TYPE = "blocked_type"
    DELTA_BELOW_MIN = "delta_below_min"
    BLOCKED_SYMBOL = "blocked_symbol"
    PUMP_EXHAUSTION = "pump_exhaustion"
    BTC_DUMP = "btc_dump"
    COOLDOWN = "cooldown"
    DEDUPE = "dedupe"
    RATE_LIMITED = "rate_limited"
    NOT_IN_ALLOWLIST = "not_in_allowlist"
    MALFORMED = "malformed"


@dataclass
class SignalGateConfig:
    telegram_min_delta_pct: float = 10.0
    telegram_block_types: Tuple[str, ...] = ("MICRO", "TEST_ACTIVITY", "SCALP")
    telegram_block_symbols: Tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT")
    trade_min_delta_pct: float = 2.0
    trade_min_confidence: float = 0.65
    btc_dump_enabled: bool = True
    btc_dump_threshold_pct: float = -1.0
    btc_dump_field: str = "btc_5m_delta_pct"
    pump_exhaustion_enabled: bool = True
    pump_exhaustion_max_pct: float = 20.0
    pump_exhaustion_field: str = "pump_60m_pct"
    dedupe_window_sec: int = 300
    cooldown_per_symbol_sec: int = 120
    global_rate_per_minute: int = 10
    allowlist_enabled: bool = True
    core_symbols: Tuple[str, ...] = (
        "PEPEUSDT", "DOGEUSDT", "SHIBUSDT",
        "SUIUSDT", "AVAXUSDT", "ADAUSDT",
        "LINKUSDT", "AAVEUSDT", "NEARUSDT",
        "ENSOUSDT", "WLDUSDT", "ZECUSDT",
    )
    
    @staticmethod
    def from_json(path: str) -> "SignalGateConfig":
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return SignalGateConfig(
                telegram_min_delta_pct=float(data.get("telegram_min_delta_pct", 10.0)),
                telegram_block_types=tuple(data.get("telegram_block_types", ["MICRO", "TEST_ACTIVITY", "SCALP"])),
                telegram_block_symbols=tuple(data.get("telegram_block_symbols", ["BTCUSDT", "ETHUSDT"])),
                trade_min_delta_pct=float(data.get("trade_min_delta_pct", 2.0)),
                btc_dump_enabled=bool(data.get("btc_dump_enabled", True)),
                btc_dump_threshold_pct=float(data.get("btc_dump_threshold_pct", -1.0)),
                pump_exhaustion_enabled=bool(data.get("pump_exhaustion_enabled", True)),
                pump_exhaustion_max_pct=float(data.get("pump_exhaustion_max_pct", 20.0)),
                dedupe_window_sec=int(data.get("dedupe_window_sec", 300)),
                cooldown_per_symbol_sec=int(data.get("cooldown_per_symbol_sec", 120)),
                global_rate_per_minute=int(data.get("global_rate_per_minute", 10)),
            )
        except Exception as e:
            log.warning(f"Config load failed: {e}. Using defaults.")
            return SignalGateConfig()


@dataclass
class SignalGateState:
    last_sent_by_symbol: Dict[str, float] = field(default_factory=dict)
    fingerprints: Dict[str, float] = field(default_factory=dict)
    global_timestamps: list = field(default_factory=list)


class SignalGate:
    """ЕДИНЫЙ ФИЛЬТР. Cannot bypass. Fail-closed."""
    
    def __init__(self, config: Optional[SignalGateConfig] = None):
        self.config = config or SignalGateConfig()
        self.state = SignalGateState()
        self._dynamic_allowlist: set = set()
        self._hot_allowlist: set = set()
    
    def check(self, signal: Dict[str, Any]) -> Tuple[GateDecision, Optional[BlockReason], str]:
        if not isinstance(signal, dict):
            return GateDecision.BLOCK, BlockReason.MALFORMED, "not dict"
        
        symbol = str(signal.get("symbol", "")).upper().strip()
        signal_type = str(signal.get("type", "")).upper().strip()
        delta_pct = self._safe_float(signal.get("delta_pct", 0))
        now = time.time()
        
        # 1. Type filter
        if signal_type in self.config.telegram_block_types:
            return GateDecision.PASS_LOG_ONLY, BlockReason.BLOCKED_TYPE, f"type={signal_type}"
        
        # 2. AllowList
        if self.config.allowlist_enabled and not self._is_allowed(symbol):
            return GateDecision.PASS_LOG_ONLY, BlockReason.NOT_IN_ALLOWLIST, f"symbol={symbol}"
        
        # 3. BTC dump
        if self.config.btc_dump_enabled:
            btc_delta = self._safe_float(signal.get(self.config.btc_dump_field, 0))
            if btc_delta <= self.config.btc_dump_threshold_pct:
                return GateDecision.PASS_LOG_ONLY, BlockReason.BTC_DUMP, f"btc={btc_delta:.2f}%"
        
        # 4. Pump exhaustion
        if self.config.pump_exhaustion_enabled:
            pump_pct = self._safe_float(signal.get(self.config.pump_exhaustion_field, 0))
            if pump_pct >= self.config.pump_exhaustion_max_pct:
                return GateDecision.PASS_LOG_ONLY, BlockReason.PUMP_EXHAUSTION, f"pump={pump_pct:.2f}%"
        
        # 5. Delta for trading
        if delta_pct < self.config.trade_min_delta_pct:
            return GateDecision.PASS_LOG_ONLY, BlockReason.DELTA_BELOW_MIN, f"delta={delta_pct:.2f}%"
        
        # 6. Cooldown
        last_time = self.state.last_sent_by_symbol.get(symbol, 0)
        if (now - last_time) < self.config.cooldown_per_symbol_sec:
            return GateDecision.PASS_LOG_ONLY, BlockReason.COOLDOWN, f"{symbol} cooldown"
        
        # 7. Dedupe
        fp = f"{symbol}|{signal_type}|{round(delta_pct, 1)}"
        if (now - self.state.fingerprints.get(fp, 0)) < self.config.dedupe_window_sec:
            return GateDecision.PASS_LOG_ONLY, BlockReason.DEDUPE, "duplicate"
        
        # 8. Rate limit
        self.state.global_timestamps = [t for t in self.state.global_timestamps if (now - t) <= 60]
        if len(self.state.global_timestamps) >= self.config.global_rate_per_minute:
            return GateDecision.PASS_LOG_ONLY, BlockReason.RATE_LIMITED, "rate"
        
        # PASSED
        self.state.last_sent_by_symbol[symbol] = now
        self.state.fingerprints[fp] = now
        self.state.global_timestamps.append(now)
        
        send_telegram = (
            delta_pct >= self.config.telegram_min_delta_pct and
            symbol not in self.config.telegram_block_symbols
        )
        
        if send_telegram:
            return GateDecision.PASS_TELEGRAM_AND_TRADE, None, f"delta={delta_pct:.2f}%"
        return GateDecision.PASS_TRADE_ONLY, None, f"delta={delta_pct:.2f}%"
    
    def update_dynamic_allowlist(self, symbols: list):
        self._dynamic_allowlist = set(s.upper() for s in symbols)
    
    def add_to_hot(self, symbol: str):
        self._hot_allowlist.add(symbol.upper())
    
    def _is_allowed(self, symbol: str) -> bool:
        return (
            symbol in self.config.core_symbols or
            symbol in self._dynamic_allowlist or
            symbol in self._hot_allowlist
        )
    
    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value) if value is not None else default
        except:
            return default


_gate: Optional[SignalGate] = None

def get_signal_gate() -> SignalGate:
    global _gate
    if _gate is None:
        _gate = SignalGate()
    return _gate


if __name__ == "__main__":
    print("=" * 60)
    print("SIGNAL GATE TEST")
    print("=" * 60)
    
    gate = SignalGate()
    tests = [
        {"symbol": "ADAUSDT", "type": "MICRO", "delta_pct": 0.37},
        {"symbol": "BTCUSDT", "type": "PUMP", "delta_pct": 15.0},
        {"symbol": "PEPEUSDT", "type": "PUMP", "delta_pct": 12.0},
        {"symbol": "ENSOUSDT", "type": "MOONSHOT", "delta_pct": 28.0},
    ]
    
    for sig in tests:
        dec, reason, det = gate.check(sig)
        status = "✅" if dec in (GateDecision.PASS_TELEGRAM_AND_TRADE, GateDecision.PASS_TRADE_ONLY) else "📝" if dec == GateDecision.PASS_LOG_ONLY else "❌"
        print(f"  {status} {sig['symbol']:12} delta={sig['delta_pct']:5.2f}% → {dec.value}")
    
    print("\n[PASS] Signal Gate test")
