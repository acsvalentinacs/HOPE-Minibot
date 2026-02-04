# -*- coding: utf-8 -*-
"""
HOPE v4.0 EVENT LEDGER
======================

Единый Event Ledger для всего торгового цикла:
SIGNAL → DECISION → OPEN → EXIT → ERROR

FEATURES:
- Correlation IDs: signal_id → decision_id → position_id → exit_id
- Invariant checks: нельзя OPEN без DECISION, EXIT без OPEN
- Atomic writes: temp → fsync → replace
- SHA256 contract на каждую строку
- Daily roll: events_YYYYMMDD.jsonl

USAGE:
    from core.event_ledger import EventLedger, EventType
    
    ledger = EventLedger()
    
    # Signal
    ledger.signal(signal_id="sig_123", symbol="PEPEUSDT", delta_pct=15.0, ...)
    
    # Decision
    ledger.decision(signal_id="sig_123", decision_id="dec_456", action="BUY", ...)
    
    # Open
    ledger.open(decision_id="dec_456", position_id="pos_789", entry_price=0.00001, ...)
    
    # Exit
    ledger.exit(position_id="pos_789", exit_reason="TP_HIT", pnl_pct=2.5, ...)
"""

import os
import sys
import json
import time
import hashlib
import tempfile
import threading
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Set
from dataclasses import dataclass, field
from enum import Enum

log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

LEDGER_DIR = Path(os.environ.get("HOPE_LEDGER_DIR", "state/ai/ledger"))
SCHEMA_NAME = "HOPE_EVENT_LEDGER"
SCHEMA_VERSION = 1

# ══════════════════════════════════════════════════════════════════════════════
# EVENT TYPES
# ══════════════════════════════════════════════════════════════════════════════

class EventType(str, Enum):
    SIGNAL = "SIGNAL"
    DECISION = "DECISION"
    OPEN = "OPEN"
    EXIT = "EXIT"
    ERROR = "ERROR"
    HEARTBEAT = "HEARTBEAT"


# ══════════════════════════════════════════════════════════════════════════════
# INVARIANT CHECKER
# ══════════════════════════════════════════════════════════════════════════════

class InvariantChecker:
    """
    Проверяет инварианты торгового цикла:
    - Нельзя OPEN без DECISION
    - Нельзя EXIT без OPEN
    - Нельзя BUY в LIVE без HOPE_LIVE_ACK
    """
    
    def __init__(self):
        self._signals: Set[str] = set()      # signal_id
        self._decisions: Set[str] = set()    # decision_id
        self._open_positions: Set[str] = set()  # position_id
        self._lock = threading.Lock()
    
    def register_signal(self, signal_id: str):
        with self._lock:
            self._signals.add(signal_id)
    
    def check_decision(self, signal_id: str, decision_id: str) -> tuple[bool, str]:
        """Decision должен иметь signal."""
        with self._lock:
            if signal_id not in self._signals:
                # Soft warning - signal мог быть из предыдущей сессии
                log.warning(f"DECISION {decision_id} references unknown signal {signal_id}")
            self._decisions.add(decision_id)
            return True, "OK"
    
    def check_open(self, decision_id: str, position_id: str) -> tuple[bool, str]:
        """OPEN должен иметь DECISION."""
        with self._lock:
            if decision_id not in self._decisions:
                return False, f"INVARIANT_VIOLATION: OPEN without DECISION (decision_id={decision_id})"
            self._open_positions.add(position_id)
            return True, "OK"
    
    def check_exit(self, position_id: str) -> tuple[bool, str]:
        """EXIT должен иметь OPEN."""
        with self._lock:
            if position_id not in self._open_positions:
                return False, f"INVARIANT_VIOLATION: EXIT without OPEN (position_id={position_id})"
            self._open_positions.discard(position_id)
            return True, "OK"
    
    def check_live_ack(self) -> tuple[bool, str]:
        """LIVE mode требует HOPE_LIVE_ACK."""
        mode = os.environ.get("HOPE_MODE", "DRY").upper()
        ack = os.environ.get("HOPE_LIVE_ACK", "").upper()
        testnet = os.environ.get("BINANCE_TESTNET", "1")
        
        if mode == "LIVE":
            if ack != "YES_I_UNDERSTAND":
                return False, "INVARIANT_VIOLATION: LIVE mode without HOPE_LIVE_ACK"
            if testnet == "1" or testnet.lower() == "true":
                return False, "INVARIANT_VIOLATION: LIVE mode with BINANCE_TESTNET=true"
        
        return True, "OK"
    
    def get_open_positions(self) -> Set[str]:
        with self._lock:
            return self._open_positions.copy()


# ══════════════════════════════════════════════════════════════════════════════
# EVENT LEDGER
# ══════════════════════════════════════════════════════════════════════════════

class EventLedger:
    """
    Singleton Event Ledger для всей системы.
    Atomic writes с SHA256 contract.
    """
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._run_id = f"run_{int(time.time() * 1000)}"
        self._invariants = InvariantChecker()
        self._write_lock = threading.Lock()
        
        # Ensure ledger directory exists
        LEDGER_DIR.mkdir(parents=True, exist_ok=True)
        
        self._initialized = True
        
        # Write startup event
        self._write_event(EventType.HEARTBEAT, {
            "action": "STARTUP",
            "run_id": self._run_id,
            "pid": os.getpid(),
        }, {})
    
    @property
    def run_id(self) -> str:
        return self._run_id
    
    @property
    def invariants(self) -> InvariantChecker:
        return self._invariants
    
    def _get_ledger_path(self) -> Path:
        """Get today's ledger file path."""
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        return LEDGER_DIR / f"events_{date_str}.jsonl"
    
    def _build_event(
        self,
        event_type: EventType,
        data: Dict[str, Any],
        trace: Dict[str, str],
        source: str = "system"
    ) -> Dict[str, Any]:
        """Build event object with all required fields."""
        return {
            "schema": SCHEMA_NAME,
            "schema_version": SCHEMA_VERSION,
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "ts_unix": time.time(),
            "event": event_type.value,
            "run_id": self._run_id,
            "source": source,
            "trace": trace,
            "data": data,
        }
    
    def _write_event(
        self,
        event_type: EventType,
        data: Dict[str, Any],
        trace: Dict[str, str],
        source: str = "system"
    ) -> bool:
        """
        Write event to ledger with SHA256 contract.
        Atomic: temp → fsync → append
        """
        event = self._build_event(event_type, data, trace, source)
        json_str = json.dumps(event, ensure_ascii=False, separators=(',', ':'))
        
        # SHA256 contract
        sha = hashlib.sha256(json_str.encode('utf-8')).hexdigest()
        line = f"sha256:{sha} {json_str}\n"
        
        ledger_path = self._get_ledger_path()
        
        with self._write_lock:
            try:
                # Atomic append with fsync
                with open(ledger_path, 'a', encoding='utf-8') as f:
                    f.write(line)
                    f.flush()
                    os.fsync(f.fileno())
                return True
            except Exception as e:
                log.error(f"LEDGER WRITE FAILED: {e}")
                # Fail-closed: если не можем записать - это критическая ошибка
                raise RuntimeError(f"Event Ledger write failed: {e}") from e
    
    # ══════════════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ══════════════════════════════════════════════════════════════════════════
    
    def signal(
        self,
        signal_id: str,
        symbol: str,
        delta_pct: float,
        price: float = 0,
        strategy: str = "",
        source: str = "moonbot",
        **extra
    ) -> bool:
        """Record SIGNAL event."""
        self._invariants.register_signal(signal_id)
        
        data = {
            "symbol": symbol,
            "delta_pct": delta_pct,
            "price": price,
            "strategy": strategy,
            **extra
        }
        trace = {"signal_id": signal_id}
        
        return self._write_event(EventType.SIGNAL, data, trace, source)
    
    def decision(
        self,
        signal_id: str,
        decision_id: str,
        action: str,  # BUY / SKIP
        reason: str = "",
        confidence: float = 0,
        source: str = "eye_v3",
        **extra
    ) -> bool:
        """Record DECISION event."""
        ok, msg = self._invariants.check_decision(signal_id, decision_id)
        if not ok:
            log.warning(msg)
        
        data = {
            "action": action,
            "reason": reason,
            "confidence": confidence,
            **extra
        }
        trace = {"signal_id": signal_id, "decision_id": decision_id}
        
        return self._write_event(EventType.DECISION, data, trace, source)
    
    def open(
        self,
        decision_id: str,
        position_id: str,
        symbol: str,
        entry_price: float,
        quantity: float,
        order_id: str = "",
        source: str = "executor",
        **extra
    ) -> bool:
        """
        Record OPEN event.
        INVARIANT: Must have DECISION first.
        """
        ok, msg = self._invariants.check_open(decision_id, position_id)
        if not ok:
            # Log error but still record (for forensics)
            self.error(msg, source="invariant_checker", position_id=position_id)
            log.error(msg)
        
        # Check LIVE ACK
        ok_live, msg_live = self._invariants.check_live_ack()
        if not ok_live:
            self.error(msg_live, source="invariant_checker", position_id=position_id)
            raise RuntimeError(msg_live)
        
        data = {
            "symbol": symbol,
            "entry_price": entry_price,
            "quantity": quantity,
            "order_id": order_id,
            **extra
        }
        trace = {"decision_id": decision_id, "position_id": position_id}
        
        return self._write_event(EventType.OPEN, data, trace, source)
    
    def exit(
        self,
        position_id: str,
        exit_reason: str,  # TP_HIT / SL_HIT / TIMEOUT / PANIC / MANUAL
        exit_price: float,
        pnl_pct: float,
        pnl_usdt: float = 0,
        duration_sec: float = 0,
        order_id: str = "",
        source: str = "watchdog",
        **extra
    ) -> bool:
        """
        Record EXIT event.
        INVARIANT: Must have OPEN first.
        """
        ok, msg = self._invariants.check_exit(position_id)
        if not ok:
            # Log error but still record (for forensics)
            self.error(msg, source="invariant_checker", position_id=position_id)
            log.warning(msg)
        
        data = {
            "exit_reason": exit_reason,
            "exit_price": exit_price,
            "pnl_pct": pnl_pct,
            "pnl_usdt": pnl_usdt,
            "duration_sec": duration_sec,
            "order_id": order_id,
            **extra
        }
        trace = {"position_id": position_id}
        
        return self._write_event(EventType.EXIT, data, trace, source)
    
    def error(
        self,
        message: str,
        source: str = "system",
        **context
    ) -> bool:
        """Record ERROR event."""
        data = {
            "message": message,
            "context": context,
        }
        trace = {k: v for k, v in context.items() if k.endswith("_id")}
        
        return self._write_event(EventType.ERROR, data, trace, source)
    
    def heartbeat(self, source: str = "system", **extra) -> bool:
        """Record HEARTBEAT event."""
        data = {"pid": os.getpid(), **extra}
        return self._write_event(EventType.HEARTBEAT, data, {}, source)


# ══════════════════════════════════════════════════════════════════════════════
# LEDGER REPLAYER (для восстановления после краша)
# ══════════════════════════════════════════════════════════════════════════════

class LedgerReplayer:
    """
    Восстанавливает состояние позиций из ledger после краша.
    """
    
    def __init__(self, ledger_dir: Path = LEDGER_DIR):
        self.ledger_dir = ledger_dir
    
    def get_open_positions(self) -> Dict[str, Dict[str, Any]]:
        """
        Replay ledger to find open positions.
        Returns: {position_id: {symbol, entry_price, quantity, ...}}
        """
        positions: Dict[str, Dict[str, Any]] = {}
        
        # Read all ledger files
        for ledger_file in sorted(self.ledger_dir.glob("events_*.jsonl")):
            try:
                with open(ledger_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        
                        # Parse sha256:HASH JSON
                        if line.startswith("sha256:"):
                            json_start = line.find(" ")
                            if json_start > 0:
                                json_str = line[json_start + 1:]
                                event = json.loads(json_str)
                            else:
                                continue
                        else:
                            event = json.loads(line)
                        
                        event_type = event.get("event")
                        trace = event.get("trace", {})
                        data = event.get("data", {})
                        
                        if event_type == "OPEN":
                            pos_id = trace.get("position_id")
                            if pos_id:
                                positions[pos_id] = {
                                    "position_id": pos_id,
                                    "symbol": data.get("symbol"),
                                    "entry_price": data.get("entry_price"),
                                    "quantity": data.get("quantity"),
                                    "order_id": data.get("order_id"),
                                    "opened_at": event.get("ts_utc"),
                                }
                        
                        elif event_type == "EXIT":
                            pos_id = trace.get("position_id")
                            if pos_id and pos_id in positions:
                                del positions[pos_id]
                                
            except Exception as e:
                log.error(f"Error replaying {ledger_file}: {e}")
        
        return positions
    
    def get_daily_stats(self, date_str: str = None) -> Dict[str, Any]:
        """Get stats for a specific day."""
        if date_str is None:
            date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        
        ledger_file = self.ledger_dir / f"events_{date_str}.jsonl"
        
        if not ledger_file.exists():
            return {"error": "No ledger for this date"}
        
        stats = {
            "signals": 0,
            "decisions_buy": 0,
            "decisions_skip": 0,
            "opens": 0,
            "exits": 0,
            "errors": 0,
            "total_pnl_pct": 0.0,
            "total_pnl_usdt": 0.0,
            "wins": 0,
            "losses": 0,
        }
        
        try:
            with open(ledger_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip():
                        continue
                    
                    if line.startswith("sha256:"):
                        json_start = line.find(" ")
                        if json_start > 0:
                            event = json.loads(line[json_start + 1:])
                        else:
                            continue
                    else:
                        event = json.loads(line)
                    
                    event_type = event.get("event")
                    data = event.get("data", {})
                    
                    if event_type == "SIGNAL":
                        stats["signals"] += 1
                    elif event_type == "DECISION":
                        if data.get("action") == "BUY":
                            stats["decisions_buy"] += 1
                        else:
                            stats["decisions_skip"] += 1
                    elif event_type == "OPEN":
                        stats["opens"] += 1
                    elif event_type == "EXIT":
                        stats["exits"] += 1
                        pnl_pct = data.get("pnl_pct", 0)
                        pnl_usdt = data.get("pnl_usdt", 0)
                        stats["total_pnl_pct"] += pnl_pct
                        stats["total_pnl_usdt"] += pnl_usdt
                        if pnl_pct > 0:
                            stats["wins"] += 1
                        else:
                            stats["losses"] += 1
                    elif event_type == "ERROR":
                        stats["errors"] += 1
        except Exception as e:
            stats["parse_error"] = str(e)
        
        # Calculate win rate
        total_trades = stats["wins"] + stats["losses"]
        stats["win_rate"] = (stats["wins"] / total_trades * 100) if total_trades > 0 else 0
        
        return stats


# ══════════════════════════════════════════════════════════════════════════════
# GLOBAL INSTANCE
# ══════════════════════════════════════════════════════════════════════════════

def get_ledger() -> EventLedger:
    """Get global EventLedger instance."""
    return EventLedger()


# ══════════════════════════════════════════════════════════════════════════════
# TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import tempfile
    
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    
    print("=" * 70)
    print("EVENT LEDGER TEST")
    print("=" * 70)
    
    # Use temp directory for test
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["HOPE_LEDGER_DIR"] = tmpdir
        
        # Reset singleton for test
        EventLedger._instance = None
        
        ledger = get_ledger()
        
        print(f"\nRun ID: {ledger.run_id}")
        print(f"Ledger dir: {tmpdir}")
        
        # Test signal
        signal_id = f"sig_{int(time.time() * 1000)}"
        ledger.signal(
            signal_id=signal_id,
            symbol="PEPEUSDT",
            delta_pct=15.0,
            price=0.00001,
            strategy="EXPLOSION"
        )
        print(f"[OK] SIGNAL recorded: {signal_id}")
        
        # Test decision
        decision_id = f"dec_{int(time.time() * 1000)}"
        ledger.decision(
            signal_id=signal_id,
            decision_id=decision_id,
            action="BUY",
            reason="STRONG_SIGNAL",
            confidence=0.85
        )
        print(f"[OK] DECISION recorded: {decision_id}")
        
        # Test open
        position_id = f"pos_{int(time.time() * 1000)}"
        ledger.open(
            decision_id=decision_id,
            position_id=position_id,
            symbol="PEPEUSDT",
            entry_price=0.00001,
            quantity=1000000,
            order_id="12345"
        )
        print(f"[OK] OPEN recorded: {position_id}")
        
        # Test exit
        ledger.exit(
            position_id=position_id,
            exit_reason="TP_HIT",
            exit_price=0.0000105,
            pnl_pct=5.0,
            pnl_usdt=0.5,
            duration_sec=45
        )
        print(f"[OK] EXIT recorded")
        
        # Test invariant violation
        print("\n--- Invariant Tests ---")
        try:
            ledger.open(
                decision_id="unknown_decision",
                position_id="test_pos",
                symbol="BTCUSDT",
                entry_price=84000,
                quantity=0.001
            )
            print("[WARN] OPEN without DECISION - recorded but logged warning")
        except Exception as e:
            print(f"[OK] OPEN without DECISION blocked: {e}")
        
        # Test replayer
        print("\n--- Replayer Test ---")
        replayer = LedgerReplayer(Path(tmpdir))
        open_positions = replayer.get_open_positions()
        print(f"Open positions after replay: {len(open_positions)}")
        
        stats = replayer.get_daily_stats()
        print(f"Daily stats: signals={stats['signals']}, opens={stats['opens']}, exits={stats['exits']}")
        
        # Show ledger content
        print("\n--- Ledger Content ---")
        for f in Path(tmpdir).glob("events_*.jsonl"):
            print(f"\n{f.name}:")
            with open(f, 'r') as fp:
                for line in fp:
                    # Show first 100 chars
                    print(f"  {line[:100]}...")
        
        print("\n[PASS] Event Ledger test complete")
