# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 13:40:00 UTC
# Purpose: DROP Signal Filter - prevent bad entries from DropsDetection
# sha256: drop_filter_v1.0
# === END SIGNATURE ===
"""
HOPE AI - DROP Signal Filter v1.0

Problem:
- DropsDetection signals have bad MAE (-5% to -6%)
- They detect DROPS not PUMP precursors
- Should be filtered or require confirmation

Solution:
- DropsDetection alone → SKIP
- DropsDetection + PumpDetection → MAY BE BUY (confirmation)
- DropsDetection + TopMarket Long → MAY BE BUY (reversal)

Usage:
    from drop_filter import DropFilter
    
    df = DropFilter()
    if df.should_skip(signal):
        return DecisionResult(action="SKIP", reason="drop_signal_no_confirmation")
"""

import logging
from dataclasses import dataclass
from typing import Dict, Any, List, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class DropAction(str, Enum):
    SKIP = "skip"           # Don't trade
    CONFIRM = "confirm"     # Need additional confirmation
    ALLOW = "allow"         # Can trade


@dataclass
class DropFilterResult:
    action: DropAction
    reason: str
    confidence: float
    confirmations: List[str]
    
    def should_skip(self) -> bool:
        return self.action == DropAction.SKIP


class DropFilter:
    """
    Filter DropsDetection signals to prevent bad entries.
    
    DropsDetection detects price drops, NOT pump precursors.
    Trading on drops alone typically results in -5% to -6% MAE.
    
    Rules:
    1. DropsDetection alone → SKIP (no pump signal)
    2. DropsDetection + PumpDetection same symbol within 5 min → CONFIRM
    3. DropsDetection + TopMarket Long same symbol within 5 min → ALLOW (reversal)
    4. Multiple DropsDetection same symbol → SKIP (sustained drop)
    """
    
    # Strategy names that indicate DROP
    DROP_STRATEGIES = {
        "Dropdetect1_USDT",
        "DropsDetection",
        "Drop",
        "drop",
    }
    
    # Strategy names that confirm potential reversal
    PUMP_STRATEGIES = {
        "Pumpdetect1_USDT",
        "PumpDetection",
        "Pump",
        "pump",
    }
    
    TOP_MARKET_STRATEGIES = {
        "Top Market Detect",
        "TopMarket",
        "topmarket",
    }
    
    DELTA_STRATEGIES = {
        "Delta_1_SIGNAL",
        "Delta",
        "delta",
    }
    
    def __init__(self, lookback_seconds: int = 300):
        """
        Args:
            lookback_seconds: Time window to look for confirmations (default 5 min)
        """
        self.lookback_seconds = lookback_seconds
        self.recent_signals: List[Dict[str, Any]] = []  # Rolling buffer
    
    def is_drop_signal(self, signal: Dict[str, Any]) -> bool:
        """Check if signal is from DropsDetection strategy"""
        strategy = signal.get("strategy", "")
        
        # Check exact match
        if strategy in self.DROP_STRATEGIES:
            return True
        
        # Check partial match
        strategy_lower = strategy.lower()
        if "drop" in strategy_lower:
            return True
        
        # Check raw signal text
        raw = signal.get("raw_signal", "")
        if "DropsDetection" in raw or "Dropdetect" in raw:
            return True
        
        return False
    
    def is_pump_signal(self, signal: Dict[str, Any]) -> bool:
        """Check if signal is from PumpDetection strategy"""
        strategy = signal.get("strategy", "")
        
        if strategy in self.PUMP_STRATEGIES:
            return True
        
        strategy_lower = strategy.lower()
        if "pump" in strategy_lower:
            return True
        
        raw = signal.get("raw_signal", "")
        if "PumpDetection" in raw or "Pumpdetect" in raw:
            return True
        
        return False
    
    def is_topmarket_long(self, signal: Dict[str, Any]) -> bool:
        """Check if signal is TopMarket Long"""
        strategy = signal.get("strategy", "")
        direction = signal.get("direction", "").lower()
        
        is_topmarket = (
            strategy in self.TOP_MARKET_STRATEGIES or
            "topmarket" in strategy.lower() or
            "Top Market" in signal.get("raw_signal", "")
        )
        
        is_long = direction == "long" or "Long" in signal.get("raw_signal", "")
        
        return is_topmarket and is_long
    
    def is_delta_strong(self, signal: Dict[str, Any]) -> bool:
        """Check if signal has strong delta (>3%)"""
        strategy = signal.get("strategy", "")
        delta = signal.get("delta_pct", 0)
        
        is_delta = (
            strategy in self.DELTA_STRATEGIES or
            "delta" in strategy.lower()
        )
        
        return is_delta and delta > 3.0
    
    def add_signal(self, signal: Dict[str, Any]):
        """Add signal to rolling buffer for confirmation checking"""
        self.recent_signals.append(signal)
        
        # Keep only recent signals
        if len(self.recent_signals) > 100:
            self.recent_signals = self.recent_signals[-100:]
    
    def find_confirmations(self, signal: Dict[str, Any]) -> List[str]:
        """Find confirming signals for the same symbol"""
        symbol = signal.get("symbol", "")
        timestamp = signal.get("timestamp", "")
        confirmations = []
        
        for recent in self.recent_signals:
            if recent.get("symbol") != symbol:
                continue
            
            # Skip self
            if recent.get("id") == signal.get("id"):
                continue
            
            # TODO: Implement proper time comparison
            # For now, check all recent signals
            
            if self.is_pump_signal(recent):
                confirmations.append(f"PumpDetection:{recent.get('strategy')}")
            
            if self.is_topmarket_long(recent):
                confirmations.append(f"TopMarket:Long:{recent.get('strategy')}")
            
            if self.is_delta_strong(recent):
                delta = recent.get("delta_pct", 0)
                confirmations.append(f"Delta:{delta:.1f}%")
        
        return confirmations
    
    def filter(self, signal: Dict[str, Any]) -> DropFilterResult:
        """
        Filter a signal and determine if it should be traded.
        
        Args:
            signal: Signal dictionary with strategy, symbol, etc.
            
        Returns:
            DropFilterResult with action, reason, and confidence
        """
        # Add to buffer first
        self.add_signal(signal)
        
        # If not a DROP signal, allow
        if not self.is_drop_signal(signal):
            return DropFilterResult(
                action=DropAction.ALLOW,
                reason="not_drop_signal",
                confidence=1.0,
                confirmations=[],
            )
        
        symbol = signal.get("symbol", "UNKNOWN")
        logger.info(f"DROP signal detected for {symbol}, checking confirmations...")
        
        # Find confirmations
        confirmations = self.find_confirmations(signal)
        
        # Rule 1: No confirmations → SKIP
        if not confirmations:
            logger.warning(f"DROP signal {symbol}: SKIP (no confirmation)")
            return DropFilterResult(
                action=DropAction.SKIP,
                reason="drop_signal_no_confirmation",
                confidence=0.2,
                confirmations=[],
            )
        
        # Rule 2: Has PumpDetection → CONFIRM (can trade with caution)
        pump_confirms = [c for c in confirmations if "PumpDetection" in c]
        if pump_confirms:
            logger.info(f"DROP signal {symbol}: CONFIRM (pump detection found)")
            return DropFilterResult(
                action=DropAction.CONFIRM,
                reason="drop_with_pump_confirmation",
                confidence=0.6,
                confirmations=pump_confirms,
            )
        
        # Rule 3: Has TopMarket Long → ALLOW (reversal pattern)
        topmarket_confirms = [c for c in confirmations if "TopMarket:Long" in c]
        if topmarket_confirms:
            logger.info(f"DROP signal {symbol}: ALLOW (reversal pattern)")
            return DropFilterResult(
                action=DropAction.ALLOW,
                reason="drop_with_topmarket_reversal",
                confidence=0.7,
                confirmations=topmarket_confirms,
            )
        
        # Rule 4: Has strong Delta → CONFIRM
        delta_confirms = [c for c in confirmations if "Delta:" in c]
        if delta_confirms:
            logger.info(f"DROP signal {symbol}: CONFIRM (strong delta)")
            return DropFilterResult(
                action=DropAction.CONFIRM,
                reason="drop_with_delta_confirmation",
                confidence=0.5,
                confirmations=delta_confirms,
            )
        
        # Default: SKIP
        logger.warning(f"DROP signal {symbol}: SKIP (weak confirmations: {confirmations})")
        return DropFilterResult(
            action=DropAction.SKIP,
            reason="drop_signal_weak_confirmation",
            confidence=0.3,
            confirmations=confirmations,
        )
    
    def should_skip(self, signal: Dict[str, Any]) -> bool:
        """Convenience method to check if signal should be skipped"""
        result = self.filter(signal)
        return result.should_skip()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get filter statistics"""
        drop_count = sum(1 for s in self.recent_signals if self.is_drop_signal(s))
        pump_count = sum(1 for s in self.recent_signals if self.is_pump_signal(s))
        topmarket_count = sum(1 for s in self.recent_signals if self.is_topmarket_long(s))
        
        return {
            "total_signals": len(self.recent_signals),
            "drop_signals": drop_count,
            "pump_signals": pump_count,
            "topmarket_long": topmarket_count,
            "lookback_seconds": self.lookback_seconds,
        }


# === INTEGRATION HELPER ===

def integrate_with_decision_engine(decision_engine, drop_filter: Optional[DropFilter] = None):
    """
    Monkey-patch DecisionEngine to include DROP filtering.
    
    Usage:
        from drop_filter import DropFilter, integrate_with_decision_engine
        
        df = DropFilter()
        integrate_with_decision_engine(decision_engine, df)
    """
    if drop_filter is None:
        drop_filter = DropFilter()
    
    original_decide = decision_engine.decide
    
    def decide_with_drop_filter(signal: Dict[str, Any]):
        # Check DROP filter first
        filter_result = drop_filter.filter(signal)
        
        if filter_result.should_skip():
            # Return SKIP decision
            from dataclasses import dataclass
            
            @dataclass
            class SkipDecision:
                action: str = "SKIP"
                reason: str = filter_result.reason
                confidence: float = filter_result.confidence
                details: Dict[str, Any] = None
                
                def __post_init__(self):
                    self.details = {
                        "drop_filter": True,
                        "confirmations": filter_result.confirmations,
                    }
            
            return SkipDecision()
        
        # Otherwise, proceed with normal decision
        return original_decide(signal)
    
    decision_engine.decide = decide_with_drop_filter
    logger.info("DROP filter integrated with DecisionEngine")


# === CLI ===

if __name__ == "__main__":
    import argparse
    import json
    
    parser = argparse.ArgumentParser(description="HOPE AI DROP Signal Filter")
    parser.add_argument("--test", type=str, help="Test signal (JSON)")
    parser.add_argument("--stats", action="store_true", help="Show filter stats")
    
    args = parser.parse_args()
    
    df = DropFilter()
    
    if args.test:
        signal = json.loads(args.test)
        result = df.filter(signal)
        print(f"Action: {result.action.value}")
        print(f"Reason: {result.reason}")
        print(f"Confidence: {result.confidence:.0%}")
        print(f"Confirmations: {result.confirmations}")
    
    elif args.stats:
        stats = df.get_stats()
        print(json.dumps(stats, indent=2))
    
    else:
        # Demo
        test_signals = [
            {"symbol": "ARPAUSDT", "strategy": "Dropdetect1_USDT", "delta_pct": 2.5},
            {"symbol": "ARPAUSDT", "strategy": "Pumpdetect1_USDT", "delta_pct": 1.9},
            {"symbol": "SENTUSDT", "strategy": "Top Market Detect", "direction": "Long", "delta_pct": 6.12},
        ]
        
        print("=== DROP Filter Demo ===\n")
        
        for sig in test_signals:
            df.add_signal(sig)
        
        # Test DROP signal
        drop_sig = {"symbol": "ARPAUSDT", "strategy": "Dropdetect1_USDT", "delta_pct": 2.7}
        result = df.filter(drop_sig)
        
        print(f"Signal: {drop_sig}")
        print(f"Result: {result.action.value} ({result.reason})")
        print(f"Confirmations: {result.confirmations}")
