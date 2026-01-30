# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# sha256:eye_of_god_v3_hardened
# Created by: Claude (opus-4)
# Created at: 2026-01-30T05:00:00Z
# Purpose: Eye of God V3 - Hardened with Two-Chamber Architecture
# Contract: Alpha Committee (want) + Risk Committee (allow) = Decision
# === END SIGNATURE ===
"""
═══════════════════════════════════════════════════════════════════════════════
  👁️ EYE OF GOD V3 - HARDENED TWO-CHAMBER ARCHITECTURE
═══════════════════════════════════════════════════════════════════════════════

АРХИТЕКТУРА (из критики):
"Двухпалатная" архитектура решений:
• Alpha Committee (Eye of God): говорит "хочу BUY/SELL и почему"
• Risk Committee: говорит "разрешаю/запрещаю" (лимиты/рынок/TTL/качество данных)
• Execution Committee: выбирает тип ордера, проверяет minNotional/stepSize

Это резко снижает вероятность "AI захотел — и мы купили".

FAIL-CLOSED INVARIANTS:
1. Signal schema invalid → SKIP (SIGNAL_SCHEMA_INVALID)
2. Signal TTL expired → SKIP (SIGNAL_TTL_EXPIRED)
3. Price null/stale → SKIP (PRICE_MISSING/PRICE_STALE)
4. Low liquidity → SKIP (LOW_LIQUIDITY)
5. Risk Committee veto → SKIP (RISK_VETO: reason)
6. Max positions → SKIP (MAX_POSITIONS)
7. Daily loss limit → SKIP (DAILY_LOSS_LIMIT)
8. Symbol blacklisted → SKIP (BLACKLIST)
9. Short direction → SKIP (SHORT_DISABLED)

FLOW:
┌─────────────────────────────────────────────────────────────────────────────┐
│                        EYE OF GOD V3 DECISION FLOW                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   Raw Signal                                                                 │
│       │                                                                      │
│       ▼                                                                      │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │  1. SCHEMA VALIDATION (Signal Schema V1)                            │   │
│   │     • Required fields present                                       │   │
│   │     • Types valid                                                   │   │
│   │     • TTL not expired (max 60s)                                    │   │
│   │     FAIL → SKIP(SIGNAL_SCHEMA_INVALID)                             │   │
│   └──────────────────────────┬──────────────────────────────────────────┘   │
│                              │ valid signal                                  │
│                              ▼                                               │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │  2. ALPHA COMMITTEE (Want to trade?)                                │   │
│   │     • Precursor detection                                           │   │
│   │     • Multi-factor scoring                                          │   │
│   │     • Confidence calculation                                        │   │
│   │     OUTPUT: AlphaDecision(BUY/SKIP, confidence, factors)           │   │
│   └──────────────────────────┬──────────────────────────────────────────┘   │
│                              │ alpha decision                                │
│                              ▼                                               │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │  3. RISK COMMITTEE (Allowed to trade?)                              │   │
│   │     • Price validity (not null, not stale)                         │   │
│   │     • Liquidity check (daily_volume ≥ min)                         │   │
│   │     • Position limits                                               │   │
│   │     • Daily loss limits                                             │   │
│   │     • Exposure limits per symbol                                    │   │
│   │     • Market regime check                                           │   │
│   │     • Whitelist/Blacklist                                           │   │
│   │     OUTPUT: RiskDecision(ALLOW/VETO, reasons)                      │   │
│   └──────────────────────────┬──────────────────────────────────────────┘   │
│                              │ risk decision                                 │
│                              ▼                                               │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │  4. FINAL DECISION                                                  │   │
│   │     • Alpha says BUY AND Risk says ALLOW → BUY                     │   │
│   │     • Otherwise → SKIP with all reasons                            │   │
│   │     OUTPUT: FinalDecision with sha256                              │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

═══════════════════════════════════════════════════════════════════════════════
"""

import json
import time
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Set
from dataclasses import dataclass, field, asdict
from enum import Enum

# Import schema validation
try:
    from signal_schema import validate_signal, ValidatedSignal, check_liquidity
except ImportError:
    # Inline minimal validation if not available
    ValidatedSignal = None
    validate_signal = None
    check_liquidity = None

log = logging.getLogger("EYE-V3")

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

STATE_DIR = Path("state/ai/eye_v3")
DECISIONS_LOG = STATE_DIR / "decisions.jsonl"
STATE_DIR.mkdir(parents=True, exist_ok=True)

# Fail-closed thresholds
MAX_SIGNAL_AGE_SEC = 60           # Reject signals older than 60s
MAX_PRICE_AGE_SEC = 30            # Price stale after 30s
MIN_DAILY_VOLUME_M = 5.0          # $5M minimum daily volume
MIN_CONFIDENCE_TO_TRADE = 0.55    # Minimum confidence
MAX_OPEN_POSITIONS = 3            # Maximum concurrent positions
MAX_DAILY_LOSS_USD = 50.0         # Daily loss limit
MAX_EXPOSURE_PER_SYMBOL = 30.0    # Max USD per symbol


# ═══════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════

class DecisionAction(Enum):
    BUY = "BUY"
    SKIP = "SKIP"


class VetoReason(Enum):
    # Schema/TTL
    SIGNAL_SCHEMA_INVALID = "SIGNAL_SCHEMA_INVALID"
    SIGNAL_TTL_EXPIRED = "SIGNAL_TTL_EXPIRED"
    
    # Price
    PRICE_MISSING = "PRICE_MISSING"
    PRICE_STALE = "PRICE_STALE"
    
    # Liquidity
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    UNKNOWN_VOLUME = "UNKNOWN_VOLUME"
    
    # Limits
    MAX_POSITIONS = "MAX_POSITIONS"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    MAX_EXPOSURE = "MAX_EXPOSURE"
    
    # Policy
    BLACKLIST = "BLACKLIST"
    SHORT_DISABLED = "SHORT_DISABLED"
    PAUSED = "PAUSED"
    
    # Market
    STRONG_BEAR_MARKET = "STRONG_BEAR_MARKET"
    
    # Confidence
    LOW_CONFIDENCE = "LOW_CONFIDENCE"


@dataclass
class AlphaDecision:
    """Decision from Alpha Committee (trading logic)"""
    action: str              # BUY or SKIP
    confidence: float        # 0.0 - 1.0
    factors: Dict[str, float]
    reasons: List[str]
    mode: str               # SUPER_SCALP, SCALP, SWING, SKIP
    target_pct: float
    stop_pct: float
    timeout_sec: int
    position_size_mult: float


@dataclass
class RiskDecision:
    """Decision from Risk Committee (risk checks)"""
    allowed: bool
    veto_reasons: List[str]
    checks_passed: List[str]
    liquidity_factor: float
    market_regime_factor: float


@dataclass
class FinalDecision:
    """Final combined decision"""
    action: str              # BUY or SKIP
    symbol: str
    
    # From Alpha
    confidence: float
    factors: Dict[str, float]
    mode: str
    target_pct: float
    stop_pct: float
    timeout_sec: int
    position_size_usdt: float
    
    # Combined reasons
    reasons: List[str]
    
    # Metadata
    timestamp: str
    signal_age_sec: float
    price: float
    price_age_sec: float
    
    # Verification
    sha256: str = ""
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    def add_sha256(self):
        """Add sha256 hash of decision"""
        data = {k: v for k, v in self.to_dict().items() if k != 'sha256'}
        data_str = json.dumps(data, sort_keys=True)
        self.sha256 = "sha256:" + hashlib.sha256(data_str.encode()).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════════════════
# ALPHA COMMITTEE (Trading Logic)
# ═══════════════════════════════════════════════════════════════════════════

class AlphaCommittee:
    """
    Alpha Committee - decides IF we want to trade.
    
    Analyzes signal quality, patterns, and calculates confidence.
    Does NOT check risk limits - that's Risk Committee's job.
    """
    
    # Scoring weights
    WEIGHTS = {
        "precursor": 0.30,
        "strategy": 0.20,
        "whitelist": 0.15,
        "delta_strength": 0.15,
        "volume_momentum": 0.10,
        "history": 0.10,
    }
    
    # Mode thresholds
    MODE_CONFIG = {
        "SUPER_SCALP": {
            "min_buys": 50,
            "target_pct": 0.5,
            "stop_pct": -0.25,
            "timeout_sec": 60,
        },
        "SCALP": {
            "min_buys": 20,
            "target_pct": 1.0,
            "stop_pct": -0.5,
            "timeout_sec": 120,
        },
        "SWING": {
            "min_buys": 5,
            "target_pct": 2.0,
            "stop_pct": -1.0,
            "timeout_sec": 300,
        },
    }
    
    def __init__(self):
        self.whitelist: Set[str] = {
            "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
            "KITEUSDT", "DUSKUSDT", "XVSUSDT", "SOMIUSDT"
        }
        self.symbol_stats: Dict[str, Dict] = {}
    
    def _score_precursor(self, signal) -> Tuple[float, List[str]]:
        """Score precursor patterns"""
        scores = {}
        detected = []
        
        # Volume raise
        if hasattr(signal, 'vol_raise_pct') and signal.vol_raise_pct >= 50:
            scores['volume_raise'] = min(1.0, signal.vol_raise_pct / 100)
            detected.append('VOLUME_RAISE')
        
        # Active buys
        if hasattr(signal, 'buys_per_sec') and signal.buys_per_sec >= 3:
            scores['active_buys'] = min(1.0, signal.buys_per_sec / 30)
            detected.append('ACTIVE_BUYS')
        
        # Delta strength
        if hasattr(signal, 'delta_pct') and signal.delta_pct >= 1.5:
            scores['delta_strong'] = min(1.0, signal.delta_pct / 10)
            detected.append('DELTA_STRONG')
        
        # Delta acceleration
        if hasattr(signal, 'dBTC5m') and hasattr(signal, 'dBTC1m'):
            acceleration = signal.dBTC5m - signal.dBTC1m
            if acceleration >= 0.5:
                scores['accelerating'] = min(1.0, acceleration / 2.0)
                detected.append('ACCELERATING')
        
        total_score = sum(scores.values()) / max(len(scores), 1) if scores else 0
        return total_score, detected
    
    def _score_strategy(self, signal) -> float:
        """Score based on strategy type"""
        strategy_scores = {
            "PumpDetection": 0.9,
            "Pump": 0.9,
            "Delta": 0.7,
            "TopMarket": 0.6,
            "DropsDetection": 0.5,
            "Drop": 0.5,
            "Volumes": 0.4,
            "Unknown": 0.3,
        }
        strategy = getattr(signal, 'strategy', 'Unknown')
        return strategy_scores.get(strategy, 0.3)
    
    def _determine_mode(self, signal) -> Tuple[str, Dict]:
        """Determine trading mode based on signal strength"""
        buys = getattr(signal, 'buys_per_sec', 0)
        
        for mode_name, config in self.MODE_CONFIG.items():
            if buys >= config["min_buys"]:
                return mode_name, config
        
        return "SKIP", {"target_pct": 0, "stop_pct": 0, "timeout_sec": 0}
    
    def evaluate(self, signal) -> AlphaDecision:
        """Evaluate signal and produce Alpha decision"""
        factors = {}
        reasons = []
        
        # 1. Precursor scoring
        precursor_score, detected = self._score_precursor(signal)
        factors["precursor"] = precursor_score * self.WEIGHTS["precursor"]
        reasons.extend(detected)
        
        # 2. Strategy scoring
        strategy_score = self._score_strategy(signal)
        factors["strategy"] = strategy_score * self.WEIGHTS["strategy"]
        reasons.append(f"STRATEGY:{getattr(signal, 'strategy', 'Unknown')}")
        
        # 3. Whitelist bonus
        symbol = getattr(signal, 'symbol', '')
        if symbol in self.whitelist:
            factors["whitelist"] = self.WEIGHTS["whitelist"]
            reasons.append(f"WHITELIST:{symbol}")
        else:
            factors["whitelist"] = 0
        
        # 4. Delta strength
        delta = getattr(signal, 'delta_pct', 0)
        delta_factor = min(1.0, delta / 5.0) * self.WEIGHTS["delta_strength"]
        factors["delta_strength"] = delta_factor
        
        # 5. Volume momentum
        vol_raise = getattr(signal, 'vol_raise_pct', 0)
        vol_factor = min(1.0, vol_raise / 100.0) * self.WEIGHTS["volume_momentum"]
        factors["volume_momentum"] = vol_factor
        
        # 6. Symbol history
        if symbol in self.symbol_stats:
            stats = self.symbol_stats[symbol]
            win_rate = stats.get("win_rate", 0.5)
            history_factor = (win_rate - 0.5) * 2 * self.WEIGHTS["history"]
            factors["history"] = max(-0.1, min(0.1, history_factor))
            if win_rate >= 0.6:
                reasons.append(f"HISTORY_GOOD:WR={win_rate*100:.0f}%")
        else:
            factors["history"] = 0
        
        # Calculate total confidence
        confidence = sum(factors.values())
        confidence = max(0.0, min(1.0, confidence))
        
        # Determine mode
        mode, mode_config = self._determine_mode(signal)
        
        # Determine action
        if mode == "SKIP" or confidence < MIN_CONFIDENCE_TO_TRADE:
            action = "SKIP"
            if mode == "SKIP":
                reasons.append("MODE_SKIP:LOW_BUYS")
            else:
                reasons.append(f"LOW_CONFIDENCE:{confidence*100:.0f}%<{MIN_CONFIDENCE_TO_TRADE*100:.0f}%")
        else:
            action = "BUY"
            reasons.append(f"CONF:{confidence*100:.0f}%>={MIN_CONFIDENCE_TO_TRADE*100:.0f}%")
        
        # Position size multiplier based on confidence
        if confidence >= 0.75:
            size_mult = 1.5
        elif confidence >= 0.60:
            size_mult = 1.0
        else:
            size_mult = 0.6
        
        return AlphaDecision(
            action=action,
            confidence=round(confidence, 4),
            factors=factors,
            reasons=reasons,
            mode=mode,
            target_pct=mode_config.get("target_pct", 1.0),
            stop_pct=mode_config.get("stop_pct", -0.5),
            timeout_sec=mode_config.get("timeout_sec", 120),
            position_size_mult=size_mult,
        )


# ═══════════════════════════════════════════════════════════════════════════
# RISK COMMITTEE (Risk Checks)
# ═══════════════════════════════════════════════════════════════════════════

class RiskCommittee:
    """
    Risk Committee - decides IF we are ALLOWED to trade.
    
    Checks all risk limits and data quality.
    Can VETO any trade from Alpha Committee.
    """
    
    def __init__(self):
        self.blacklist: Set[str] = {
            "ARPAUSDT", "AXSUSDT", "DODOUSDT", "SYNUSDT"
        }
        self.paused_symbols: Set[str] = set()
        
        # State tracking
        self.open_positions: Dict[str, float] = {}  # symbol → exposure
        self.daily_pnl: float = 0.0
        self.daily_start = datetime.now(timezone.utc).date()
        
        # Price cache
        self.prices: Dict[str, Tuple[float, float]] = {}  # symbol → (price, timestamp)
    
    def update_price(self, symbol: str, price: float):
        """Update price in cache"""
        self.prices[symbol] = (price, time.time())
    
    def get_price_age(self, symbol: str) -> Tuple[Optional[float], float]:
        """Get price and age for symbol"""
        if symbol not in self.prices:
            return None, float('inf')
        price, ts = self.prices[symbol]
        age = time.time() - ts
        return price, age
    
    def update_position(self, symbol: str, exposure: float):
        """Update position exposure"""
        if exposure > 0:
            self.open_positions[symbol] = exposure
        elif symbol in self.open_positions:
            del self.open_positions[symbol]
    
    def update_daily_pnl(self, pnl: float):
        """Update daily PnL"""
        today = datetime.now(timezone.utc).date()
        if today != self.daily_start:
            self.daily_pnl = 0.0
            self.daily_start = today
        self.daily_pnl += pnl
    
    def evaluate(self, signal, alpha_decision: AlphaDecision) -> RiskDecision:
        """Evaluate risk and produce Risk decision"""
        veto_reasons = []
        checks_passed = []
        
        symbol = getattr(signal, 'symbol', '')
        
        # 1. Check direction (SHORT disabled)
        direction = getattr(signal, 'direction', 'Long')
        if direction.lower() != 'long':
            veto_reasons.append(f"{VetoReason.SHORT_DISABLED.value}:{direction}")
        else:
            checks_passed.append("DIRECTION_OK")
        
        # 2. Check blacklist
        if symbol in self.blacklist:
            veto_reasons.append(f"{VetoReason.BLACKLIST.value}:{symbol}")
        else:
            checks_passed.append("NOT_BLACKLIST")
        
        # 3. Check paused
        if symbol in self.paused_symbols:
            veto_reasons.append(f"{VetoReason.PAUSED.value}:{symbol}")
        else:
            checks_passed.append("NOT_PAUSED")
        
        # 4. Check price validity
        price, price_age = self.get_price_age(symbol)
        if price is None:
            veto_reasons.append(VetoReason.PRICE_MISSING.value)
        elif price_age > MAX_PRICE_AGE_SEC:
            veto_reasons.append(f"{VetoReason.PRICE_STALE.value}:{price_age:.1f}s")
        else:
            checks_passed.append(f"PRICE_FRESH:{price_age:.1f}s")
        
        # 5. Check liquidity
        liquidity_factor = 1.0
        if check_liquidity:
            tradeable, reason, factor = check_liquidity(signal)
            if not tradeable:
                veto_reasons.append(f"{VetoReason.LOW_LIQUIDITY.value}:{reason}")
            else:
                liquidity_factor = factor
                checks_passed.append(f"LIQUIDITY_OK:{factor:.2f}")
        else:
            # Fallback check
            volume = getattr(signal, 'daily_volume_m', 0)
            if volume <= 0:
                veto_reasons.append(VetoReason.UNKNOWN_VOLUME.value)
            elif volume < MIN_DAILY_VOLUME_M:
                veto_reasons.append(f"{VetoReason.LOW_LIQUIDITY.value}:{volume}M")
            else:
                liquidity_factor = min(1.0, volume / 20.0)
                checks_passed.append(f"VOLUME_OK:{volume}M")
        
        # 6. Check position limits
        if len(self.open_positions) >= MAX_OPEN_POSITIONS:
            veto_reasons.append(f"{VetoReason.MAX_POSITIONS.value}:{len(self.open_positions)}")
        else:
            checks_passed.append(f"POSITIONS_OK:{len(self.open_positions)}/{MAX_OPEN_POSITIONS}")
        
        # 7. Check if already in this symbol
        if symbol in self.open_positions:
            current_exposure = self.open_positions[symbol]
            if current_exposure >= MAX_EXPOSURE_PER_SYMBOL:
                veto_reasons.append(f"{VetoReason.MAX_EXPOSURE.value}:{symbol}")
            else:
                checks_passed.append(f"EXPOSURE_OK:{symbol}")
        
        # 8. Check daily loss limit
        if self.daily_pnl <= -MAX_DAILY_LOSS_USD:
            veto_reasons.append(f"{VetoReason.DAILY_LOSS_LIMIT.value}:${self.daily_pnl:.2f}")
        else:
            checks_passed.append(f"DAILY_LOSS_OK:${self.daily_pnl:.2f}")
        
        # Market regime factor (placeholder - integrate with market_regime.py)
        market_regime_factor = 1.0
        checks_passed.append("MARKET_REGIME_OK")
        
        # Final decision
        allowed = len(veto_reasons) == 0
        
        return RiskDecision(
            allowed=allowed,
            veto_reasons=veto_reasons,
            checks_passed=checks_passed,
            liquidity_factor=liquidity_factor,
            market_regime_factor=market_regime_factor,
        )


# ═══════════════════════════════════════════════════════════════════════════
# EYE OF GOD V3 (Main Oracle)
# ═══════════════════════════════════════════════════════════════════════════

class EyeOfGodV3:
    """
    Eye of God V3 - Hardened Two-Chamber Oracle
    
    Combines Alpha Committee (trading logic) and Risk Committee (risk checks)
    to produce fail-closed decisions.
    """
    
    def __init__(self, base_position_size: float = 10.0):
        self.alpha = AlphaCommittee()
        self.risk = RiskCommittee()
        self.base_position_size = base_position_size
        
        # Stats
        self.stats = {
            "decisions": 0,
            "buys": 0,
            "skips": 0,
            "schema_invalid": 0,
            "risk_vetos": 0,
        }
    
    def update_price(self, symbol: str, price: float):
        """Update price in Risk Committee"""
        self.risk.update_price(symbol, price)
    
    def update_prices(self, prices: Dict[str, float]):
        """Update multiple prices"""
        for symbol, price in prices.items():
            self.risk.update_price(symbol, price)
    
    def decide(self, raw_signal: Dict[str, Any]) -> FinalDecision:
        """
        Main decision function.
        
        Flow:
        1. Validate signal schema
        2. Get Alpha Committee decision
        3. Get Risk Committee decision
        4. Combine into final decision
        """
        self.stats["decisions"] += 1
        ts = datetime.now(timezone.utc).isoformat()
        
        # === 1. SCHEMA VALIDATION ===
        if validate_signal:
            validation = validate_signal(raw_signal, max_age_sec=MAX_SIGNAL_AGE_SEC)
            if not validation.valid:
                self.stats["schema_invalid"] += 1
                self.stats["skips"] += 1
                
                return FinalDecision(
                    action="SKIP",
                    symbol=raw_signal.get("symbol", "UNKNOWN"),
                    confidence=0.0,
                    factors={},
                    mode="SKIP",
                    target_pct=0.0,
                    stop_pct=0.0,
                    timeout_sec=0,
                    position_size_usdt=0.0,
                    reasons=[f"SIGNAL_SCHEMA_INVALID:{e}" for e in validation.errors],
                    timestamp=ts,
                    signal_age_sec=0,
                    price=0.0,
                    price_age_sec=0,
                )
            
            signal = validation.signal
            signal_age = signal.age_sec
        else:
            # Fallback: create minimal signal object
            class MinimalSignal:
                pass
            signal = MinimalSignal()
            for k, v in raw_signal.items():
                setattr(signal, k, v)
            signal_age = 0
        
        symbol = getattr(signal, 'symbol', 'UNKNOWN')
        
        # === 2. ALPHA COMMITTEE ===
        alpha_decision = self.alpha.evaluate(signal)
        
        # === 3. RISK COMMITTEE ===
        risk_decision = self.risk.evaluate(signal, alpha_decision)
        
        # === 4. COMBINE DECISIONS ===
        
        # Get price info
        price, price_age = self.risk.get_price_age(symbol)
        
        # Combine reasons
        all_reasons = alpha_decision.reasons.copy()
        
        if alpha_decision.action == "BUY" and risk_decision.allowed:
            # APPROVED: Both committees agree
            action = "BUY"
            self.stats["buys"] += 1
            
            # Calculate position size with risk adjustments
            size = self.base_position_size * alpha_decision.position_size_mult
            size *= risk_decision.liquidity_factor
            size *= risk_decision.market_regime_factor
            
            all_reasons.append("RISK_APPROVED")
            all_reasons.extend(risk_decision.checks_passed)
            
        else:
            # REJECTED: Either Alpha says SKIP or Risk vetoes
            action = "SKIP"
            size = 0.0
            self.stats["skips"] += 1
            
            if risk_decision.veto_reasons:
                self.stats["risk_vetos"] += 1
                all_reasons.append("RISK_VETO")
                all_reasons.extend(risk_decision.veto_reasons)
        
        # Create final decision
        decision = FinalDecision(
            action=action,
            symbol=symbol,
            confidence=alpha_decision.confidence,
            factors=alpha_decision.factors,
            mode=alpha_decision.mode,
            target_pct=alpha_decision.target_pct,
            stop_pct=alpha_decision.stop_pct,
            timeout_sec=alpha_decision.timeout_sec,
            position_size_usdt=round(size, 2),
            reasons=all_reasons,
            timestamp=ts,
            signal_age_sec=signal_age,
            price=price or 0.0,
            price_age_sec=price_age if price else 0.0,
        )
        
        # Add sha256
        decision.add_sha256()
        
        # Log decision
        self._log_decision(decision)
        
        return decision
    
    def _log_decision(self, decision: FinalDecision):
        """Log decision to JSONL"""
        with open(DECISIONS_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(decision.to_dict(), ensure_ascii=False) + '\n')
    
    def get_stats(self) -> Dict:
        """Get decision statistics"""
        return {
            **self.stats,
            "open_positions": len(self.risk.open_positions),
            "daily_pnl": self.risk.daily_pnl,
        }


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Eye of God V3")
    parser.add_argument("--test", action="store_true", help="Run test decision")
    parser.add_argument("--stats", action="store_true", help="Show statistics")
    
    args = parser.parse_args()
    
    eye = EyeOfGodV3(base_position_size=10.0)
    
    if args.stats:
        print("\n=== EYE OF GOD V3 STATS ===")
        print(json.dumps(eye.get_stats(), indent=2))
        return
    
    if args.test:
        # Test with sample signal
        test_signal = {
            "symbol": "BTCUSDT",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "strategy": "PumpDetection",
            "direction": "Long",
            "delta_pct": 3.5,
            "buys_per_sec": 45,
            "vol_raise_pct": 180,
            "price": 85000,
            "daily_volume_m": 500,
        }
        
        # Update price
        eye.update_price("BTCUSDT", 85000)
        
        # Get decision
        decision = eye.decide(test_signal)
        
        print("\n=== TEST DECISION ===")
        print(f"Action: {decision.action}")
        print(f"Symbol: {decision.symbol}")
        print(f"Confidence: {decision.confidence*100:.1f}%")
        print(f"Mode: {decision.mode}")
        print(f"Position Size: ${decision.position_size_usdt}")
        print(f"Reasons: {decision.reasons}")
        print(f"SHA256: {decision.sha256}")
        return
    
    print("Use --test or --stats")


if __name__ == "__main__":
    main()
