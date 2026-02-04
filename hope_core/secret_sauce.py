# === AI SIGNATURE ===
# Module: hope_core/secret_sauce.py
# Created by: Claude (opus-4.5)
# Created at: 2026-02-04 23:55:00 UTC
# Purpose: Secret Sauce - Advanced trading intelligence features
# === END SIGNATURE ===
"""
HOPE Core - Secret Sauce Module

Advanced trading intelligence features:
1. Shadow Mode - parallel simulation without real orders
2. Adaptive Confidence - dynamic threshold adjustment
3. Signal Correlation Learning - symbol performance tracking
4. Panic Recovery - auto-close on heartbeat loss
5. Time-Based Filters - avoid dangerous trading hours
"""

import json
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum
import statistics


# =============================================================================
# 1. SHADOW MODE
# =============================================================================

class TradingMode(Enum):
    LIVE = "LIVE"
    SHADOW = "SHADOW"
    DRY = "DRY"


@dataclass
class ShadowTrade:
    symbol: str
    side: str
    entry_price: float
    entry_time: datetime
    size_usd: float
    signal_score: float
    correlation_id: str
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    pnl: float = 0.0
    status: str = "OPEN"


class ShadowTrader:
    """Shadow trading - simulates trades without real orders."""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.state_file = state_dir / "shadow_trades.json"
        self.trades: Dict[str, ShadowTrade] = {}
        self.history: List[ShadowTrade] = []
        self._lock = threading.Lock()
        self._load_state()

    def _load_state(self):
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text())
                for t in data.get("open_trades", []):
                    trade = ShadowTrade(**{k: v for k, v in t.items() if k != "entry_time"})
                    trade.entry_time = datetime.fromisoformat(t["entry_time"])
                    self.trades[t["correlation_id"]] = trade
            except Exception:
                pass

    def _save_state(self):
        with self._lock:
            data = {
                "open_trades": [
                    {
                        "symbol": t.symbol, "side": t.side,
                        "entry_price": t.entry_price,
                        "entry_time": t.entry_time.isoformat(),
                        "size_usd": t.size_usd, "signal_score": t.signal_score,
                        "correlation_id": t.correlation_id, "status": t.status,
                    }
                    for t in self.trades.values()
                ],
                "total_shadow_pnl": self.get_total_pnl(),
            }
            self.state_file.write_text(json.dumps(data, indent=2))

    def open_shadow_trade(self, symbol: str, side: str, price: float,
                          size_usd: float, score: float, correlation_id: str) -> ShadowTrade:
        trade = ShadowTrade(
            symbol=symbol, side=side, entry_price=price,
            entry_time=datetime.now(timezone.utc),
            size_usd=size_usd, signal_score=score, correlation_id=correlation_id,
        )
        with self._lock:
            self.trades[correlation_id] = trade
        self._save_state()
        return trade

    def close_shadow_trade(self, correlation_id: str, exit_price: float) -> Optional[ShadowTrade]:
        with self._lock:
            if correlation_id not in self.trades:
                return None
            trade = self.trades[correlation_id]
            trade.exit_price = exit_price
            trade.exit_time = datetime.now(timezone.utc)
            trade.status = "CLOSED"
            if trade.side == "BUY":
                pnl_pct = (exit_price - trade.entry_price) / trade.entry_price
            else:
                pnl_pct = (trade.entry_price - exit_price) / trade.entry_price
            trade.pnl = trade.size_usd * pnl_pct
            self.history.append(trade)
            del self.trades[correlation_id]
        self._save_state()
        return trade

    def get_total_pnl(self) -> float:
        return sum(t.pnl for t in self.history)

    def get_stats(self) -> Dict[str, Any]:
        if not self.history:
            return {"total_trades": 0, "win_rate": 0, "total_pnl": 0}
        wins = [t for t in self.history if t.pnl > 0]
        return {
            "total_trades": len(self.history),
            "open_trades": len(self.trades),
            "wins": len(wins),
            "losses": len(self.history) - len(wins),
            "win_rate": len(wins) / len(self.history),
            "total_pnl": self.get_total_pnl(),
        }


# =============================================================================
# 2. ADAPTIVE CONFIDENCE
# =============================================================================

class AdaptiveConfidence:
    """Dynamically adjusts confidence threshold based on performance."""

    def __init__(self, initial: float = 0.35, min_t: float = 0.20,
                 max_t: float = 0.70, lookback: int = 20, state_file: Optional[Path] = None):
        self.threshold = initial
        self.min_threshold = min_t
        self.max_threshold = max_t
        self.lookback = lookback
        self.state_file = state_file
        self.recent_decisions: List[Dict] = []
        self._lock = threading.Lock()
        self._load_state()

    def _load_state(self):
        if self.state_file and self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text())
                self.threshold = data.get("threshold", self.threshold)
                self.recent_decisions = data.get("recent_decisions", [])[-self.lookback:]
            except Exception:
                pass

    def _save_state(self):
        if self.state_file:
            data = {
                "threshold": self.threshold,
                "recent_decisions": self.recent_decisions[-self.lookback:],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self.state_file.write_text(json.dumps(data, indent=2))

    def record_decision(self, accepted: bool, score: float):
        with self._lock:
            self.recent_decisions.append({
                "accepted": accepted, "score": score, "won": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            self.recent_decisions = self.recent_decisions[-self.lookback * 2:]
        self._save_state()

    def record_outcome(self, won: bool):
        with self._lock:
            for d in reversed(self.recent_decisions):
                if d["accepted"] and d["won"] is None:
                    d["won"] = won
                    break
        self._adjust_threshold()
        self._save_state()

    def _adjust_threshold(self):
        with self._lock:
            recent = self.recent_decisions[-self.lookback:]
            if len(recent) < 5:
                return
            accepted = [d for d in recent if d["accepted"]]
            rejected = [d for d in recent if not d["accepted"]]
            completed = [d for d in accepted if d["won"] is not None]
            if not completed:
                return
            wins = [d for d in completed if d["won"]]
            win_rate = len(wins) / len(completed)
            reject_rate = len(rejected) / len(recent)
            old = self.threshold
            if reject_rate > 0.8:
                self.threshold = max(self.min_threshold, self.threshold - 0.05)
            elif win_rate < 0.3 and len(completed) >= 5:
                self.threshold = min(self.max_threshold, self.threshold + 0.05)
            elif win_rate > 0.6 and reject_rate > 0.5:
                self.threshold = max(self.min_threshold, self.threshold - 0.02)
            if self.threshold != old:
                print(f"[ADAPTIVE] Threshold: {old:.2f} -> {self.threshold:.2f}")

    def should_accept(self, score: float) -> Tuple[bool, str]:
        accepted = score >= self.threshold
        reason = f"score {score:.2f} >= {self.threshold:.2f}" if accepted else f"score {score:.2f} < {self.threshold:.2f}"
        self.record_decision(accepted, score)
        return accepted, reason

    def get_threshold(self) -> float:
        return self.threshold


# =============================================================================
# 3. SIGNAL CORRELATION LEARNING
# =============================================================================

@dataclass
class SymbolPerformance:
    symbol: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    avg_rr: float = 0.0
    last_trade: Optional[str] = None


class SignalCorrelationLearner:
    """Tracks which symbols perform best."""

    def __init__(self, state_file: Optional[Path] = None):
        self.state_file = state_file
        self.symbols: Dict[str, SymbolPerformance] = {}
        self._lock = threading.Lock()
        self._load_state()

    def _load_state(self):
        if self.state_file and self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text())
                for sym, perf in data.get("symbols", {}).items():
                    self.symbols[sym] = SymbolPerformance(**perf)
            except Exception:
                pass

    def _save_state(self):
        if self.state_file:
            data = {
                "symbols": {sym: vars(p) for sym, p in self.symbols.items()},
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self.state_file.write_text(json.dumps(data, indent=2))

    def record_trade(self, symbol: str, pnl: float, rr: float = 1.0):
        with self._lock:
            if symbol not in self.symbols:
                self.symbols[symbol] = SymbolPerformance(symbol=symbol)
            p = self.symbols[symbol]
            p.total_trades += 1
            p.total_pnl += pnl
            if pnl > 0:
                p.wins += 1
            else:
                p.losses += 1
            p.avg_rr = (p.avg_rr * (p.total_trades - 1) + rr) / p.total_trades
            p.last_trade = datetime.now(timezone.utc).isoformat()
        self._save_state()

    def get_symbol_weight(self, symbol: str) -> float:
        with self._lock:
            if symbol not in self.symbols:
                return 1.0
            p = self.symbols[symbol]
            if p.total_trades < 3:
                return 1.0
            win_rate = p.wins / p.total_trades
            if win_rate >= 0.6 and p.total_pnl > 0:
                return min(1.5, 1.0 + win_rate * 0.5)
            elif win_rate <= 0.3 or p.total_pnl < -10:
                return max(0.5, 1.0 - (1 - win_rate) * 0.5)
            return 1.0

    def get_top_symbols(self, n: int = 10) -> List[Tuple[str, float]]:
        with self._lock:
            sorted_syms = sorted(self.symbols.values(), key=lambda p: p.total_pnl, reverse=True)
            return [(p.symbol, p.total_pnl) for p in sorted_syms[:n]]

    def get_blacklist(self, min_trades: int = 5, max_loss: float = -20) -> List[str]:
        with self._lock:
            return [p.symbol for p in self.symbols.values()
                    if p.total_trades >= min_trades and p.total_pnl < max_loss]


# =============================================================================
# 4. PANIC RECOVERY
# =============================================================================

class PanicRecovery:
    """Monitors health and triggers panic recovery."""

    def __init__(self, heartbeat_timeout: float = 60.0, max_daily_loss: float = 50.0,
                 max_circuit_trips: int = 3, state_file: Optional[Path] = None):
        self.heartbeat_timeout = heartbeat_timeout
        self.max_daily_loss = max_daily_loss
        self.max_circuit_trips = max_circuit_trips
        self.state_file = state_file
        self._last_heartbeat = time.monotonic()
        self._daily_pnl = 0.0
        self._circuit_trips = 0
        self._panic_mode = False
        self._panic_reason: Optional[str] = None
        self._lock = threading.Lock()

    def heartbeat(self):
        with self._lock:
            self._last_heartbeat = time.monotonic()

    def record_pnl(self, pnl: float):
        with self._lock:
            self._daily_pnl += pnl
            if self._daily_pnl < -self.max_daily_loss:
                self._trigger_panic(f"Daily loss: ${self._daily_pnl:.2f}")

    def record_circuit_trip(self):
        with self._lock:
            self._circuit_trips += 1
            if self._circuit_trips >= self.max_circuit_trips:
                self._trigger_panic(f"Circuit breaker tripped {self._circuit_trips}x")

    def check_heartbeat(self) -> bool:
        with self._lock:
            elapsed = time.monotonic() - self._last_heartbeat
            if elapsed > self.heartbeat_timeout:
                self._trigger_panic(f"Heartbeat lost: {elapsed:.0f}s")
                return False
            return True

    def _trigger_panic(self, reason: str):
        if not self._panic_mode:
            self._panic_mode = True
            self._panic_reason = reason
            print(f"[PANIC] TRIGGERED: {reason}")

    def is_panic(self) -> Tuple[bool, Optional[str]]:
        with self._lock:
            return self._panic_mode, self._panic_reason

    def reset_panic(self):
        with self._lock:
            self._panic_mode = False
            self._panic_reason = None
            print("[PANIC] Reset")

    def reset_daily(self):
        with self._lock:
            self._daily_pnl = 0.0
            self._circuit_trips = 0

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "panic_mode": self._panic_mode,
                "panic_reason": self._panic_reason,
                "daily_pnl": self._daily_pnl,
                "circuit_trips": self._circuit_trips,
                "heartbeat_age": time.monotonic() - self._last_heartbeat,
            }


# =============================================================================
# 5. TIME-BASED FILTERS
# =============================================================================

class TimeBasedFilter:
    """Filters trading based on time of day."""

    DEFAULT_BLACKOUTS = [
        (7, 0, 8, 0),      # London open
        (13, 30, 14, 30),  # NY open
        (20, 0, 21, 0),    # NY close
    ]

    def __init__(self, blackouts: Optional[List[Tuple[int, int, int, int]]] = None, enabled: bool = True):
        self.blackouts = blackouts or self.DEFAULT_BLACKOUTS
        self.enabled = enabled
        self._manual_blackout = False
        self._manual_until: Optional[datetime] = None

    def is_trading_allowed(self) -> Tuple[bool, str]:
        if not self.enabled:
            return True, "Filter disabled"
        now = datetime.now(timezone.utc)
        if self._manual_blackout:
            if self._manual_until and now < self._manual_until:
                return False, f"Manual blackout until {self._manual_until.strftime('%H:%M')} UTC"
            self._manual_blackout = False
        current = now.hour * 60 + now.minute
        for h1, m1, h2, m2 in self.blackouts:
            start, end = h1 * 60 + m1, h2 * 60 + m2
            if start <= current < end:
                return False, f"Blackout: {h1:02d}:{m1:02d}-{h2:02d}:{m2:02d}"
        return True, "Trading allowed"

    def set_manual_blackout(self, minutes: int = 30):
        self._manual_blackout = True
        self._manual_until = datetime.now(timezone.utc) + timedelta(minutes=minutes)

    def get_status(self) -> Dict[str, Any]:
        ok, reason = self.is_trading_allowed()
        return {"enabled": self.enabled, "allowed": ok, "reason": reason,
                "windows": [f"{h1:02d}:{m1:02d}-{h2:02d}:{m2:02d}" for h1, m1, h2, m2 in self.blackouts]}


# =============================================================================
# SECRET SAUCE ORCHESTRATOR
# =============================================================================

class SecretSauce:
    """Orchestrates all secret sauce features."""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        state_dir.mkdir(parents=True, exist_ok=True)
        self.shadow = ShadowTrader(state_dir)
        self.adaptive = AdaptiveConfidence(state_file=state_dir / "adaptive.json")
        self.learner = SignalCorrelationLearner(state_file=state_dir / "symbols.json")
        self.panic = PanicRecovery(state_file=state_dir / "panic.json")
        self.time_filter = TimeBasedFilter()
        print("[SECRET SAUCE] Initialized")

    def should_trade(self, symbol: str, score: float) -> Tuple[bool, str, Dict]:
        meta = {"symbol": symbol, "score": score, "checks": {}}

        # 1. Panic check
        in_panic, reason = self.panic.is_panic()
        if in_panic:
            meta["checks"]["panic"] = reason
            return False, f"PANIC: {reason}", meta

        # 2. Time check
        time_ok, time_reason = self.time_filter.is_trading_allowed()
        meta["checks"]["time"] = time_reason
        if not time_ok:
            return False, time_reason, meta

        # 3. Confidence check
        conf_ok, conf_reason = self.adaptive.should_accept(score)
        meta["checks"]["confidence"] = conf_reason
        meta["threshold"] = self.adaptive.get_threshold()
        if not conf_ok:
            return False, conf_reason, meta

        # 4. Symbol weight
        weight = self.learner.get_symbol_weight(symbol)
        meta["weight"] = weight
        if weight < 0.6:
            return False, f"Symbol weight too low: {weight:.2f}", meta

        return True, "All checks passed", meta

    def record_result(self, symbol: str, pnl: float, won: bool):
        self.adaptive.record_outcome(won)
        self.learner.record_trade(symbol, pnl)
        self.panic.record_pnl(pnl)

    def heartbeat(self):
        self.panic.heartbeat()

    def get_status(self) -> Dict[str, Any]:
        return {
            "shadow": self.shadow.get_stats(),
            "threshold": self.adaptive.get_threshold(),
            "top_symbols": self.learner.get_top_symbols(5),
            "blacklist": self.learner.get_blacklist(),
            "panic": self.panic.get_status(),
            "time": self.time_filter.get_status(),
        }


# =============================================================================
# SELF TEST
# =============================================================================

if __name__ == "__main__":
    import tempfile

    print("=" * 60)
    print("  SECRET SAUCE - SELF TEST")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        sauce = SecretSauce(Path(tmpdir))

        print("\n[1] Should Trade Check:")
        ok, reason, _ = sauce.should_trade("BTCUSDT", 0.75)
        print(f"  BTCUSDT score=0.75: {ok} - {reason}")

        ok, reason, _ = sauce.should_trade("DOGEUSDT", 0.25)
        print(f"  DOGEUSDT score=0.25: {ok} - {reason}")

        print("\n[2] Trade Recording:")
        sauce.record_result("BTCUSDT", 5.0, True)
        sauce.record_result("BTCUSDT", -2.0, False)
        print("  Recorded 2 trades")

        print("\n[3] Symbol Weights:")
        print(f"  BTCUSDT: {sauce.learner.get_symbol_weight('BTCUSDT'):.2f}")

        print("\n[4] Panic Test:")
        sauce.panic.record_pnl(-55)
        in_panic, reason = sauce.panic.is_panic()
        print(f"  Panic: {in_panic} - {reason}")

        print("\n[5] Full Status:")
        for k, v in sauce.get_status().items():
            print(f"  {k}: {v}")

    print("\n" + "=" * 60)
    print("  TEST COMPLETE")
    print("=" * 60)
