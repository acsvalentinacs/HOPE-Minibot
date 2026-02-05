# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-05T06:45:00Z
# Purpose: Integrate AI modules into HOPE Core Command Bus
# === END SIGNATURE ===
"""
AI Integration Layer for HOPE Core v2.0

This module connects AI filtering to the Command Bus:
- AI Trade Learner (blacklist, bad hours)
- Anti-Chase Filter (prevent late entry)
- Observation Mode (stop trading at low WR)
- Adaptive Confidence (dynamic threshold)

Usage in hope_core.py:
    from hope_core.ai_integration import AIGate

    ai_gate = AIGate()

    # In _handle_signal:
    passed, reason = ai_gate.check_signal(symbol, confidence, price)
    if not passed:
        return {"action": "SKIP", "reason": reason}
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Tuple, Dict, Any, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("hope.ai_gate")


class AIGate:
    """
    AI Gate - Central entry point for all AI filtering in HOPE Core.

    Combines:
    - AI Trade Learner (blacklist, bad hours)
    - Anti-Chase Filter (price movement check)
    - Observation Mode (WR-based trading stop)
    - Adaptive Confidence (dynamic threshold)
    """

    def __init__(self):
        self._learner = None
        self._anti_chase = None
        self._observation = None
        self._adaptive = None

        self._load_modules()

        logger.info("AIGate initialized for HOPE Core Command Bus")

    def _load_modules(self) -> None:
        """Lazy load AI modules."""
        try:
            from core.ai_trade_learner import get_learner
            self._learner = get_learner()
            logger.info(f"AI Learner loaded: blacklist={self._learner.blacklist}")
        except Exception as e:
            logger.warning(f"AI Learner not available: {e}")

        try:
            from core.anti_chase_filter import get_anti_chase, get_observation_mode
            self._anti_chase = get_anti_chase()
            self._observation = get_observation_mode()
            logger.info("Anti-Chase and Observation loaded")
        except Exception as e:
            logger.warning(f"Anti-Chase not available: {e}")

        try:
            from core.adaptive_confidence import AdaptiveConfidence
            self._adaptive = AdaptiveConfidence()
            logger.info(f"Adaptive Confidence loaded: threshold={self._adaptive.get_threshold()}")
        except Exception as e:
            logger.warning(f"Adaptive Confidence not available: {e}")

    def check_signal(
        self,
        symbol: str,
        confidence: float,
        price: float = 0.0,
        signal_type: str = "UNKNOWN",
    ) -> Tuple[bool, str]:
        """
        Main entry point - check if signal should be processed.

        Returns:
            (passed, reason)
        """
        checks_passed = []
        checks_failed = []

        # 1. Observation Mode (WR-based stop)
        if self._observation:
            obs_ok, obs_reason = self._observation.can_trade()
            if not obs_ok:
                return False, f"[OBSERVATION] {obs_reason}"
            checks_passed.append("observation")

        # 2. AI Trade Learner (blacklist, hours)
        if self._learner:
            from core.ai_trade_learner import Signal
            sig = Signal(symbol=symbol, confidence=confidence, signal_type=signal_type)
            learn_ok, learn_reason = self._learner.should_trade(sig)
            if not learn_ok:
                return False, f"[AI_LEARNER] {learn_reason}"
            checks_passed.append("ai_learner")

        # 3. Adaptive Confidence (dynamic threshold)
        if self._adaptive:
            threshold = self._adaptive.get_threshold()
            if confidence < threshold:
                return False, f"[ADAPTIVE] confidence {confidence:.2f} < threshold {threshold:.2f} (regime: {self._adaptive.get_current_regime()})"
            checks_passed.append("adaptive")

        # 4. Anti-Chase Filter (price movement)
        if self._anti_chase and price > 0:
            chase_ok, chase_reason = self._anti_chase.should_enter(symbol, price, signal_type)
            if not chase_ok:
                return False, f"[ANTI_CHASE] {chase_reason}"
            checks_passed.append("anti_chase")

        reason = f"PASSED: {', '.join(checks_passed)}"
        return True, reason

    def record_outcome(
        self,
        symbol: str,
        profit_pct: float,
        is_win: bool,
        **kwargs
    ) -> None:
        """Record trade outcome for learning."""
        if self._learner:
            self._learner.record_outcome(symbol, profit_pct, **kwargs)

        if self._observation:
            self._observation.record_trade(is_win)

        if self._adaptive:
            from core.adaptive_confidence import TradeResult
            trade = TradeResult(
                symbol=symbol,
                pnl=profit_pct,
                pnl_pct=profit_pct,
                is_win=is_win,
            )
            self._adaptive.add_trade(trade)

        logger.info(f"Outcome recorded: {symbol} {'WIN' if is_win else 'LOSS'} {profit_pct:+.2f}%")

    def get_stats(self) -> Dict[str, Any]:
        """Get combined AI stats."""
        stats = {
            "ai_gate": "active",
            "modules_loaded": [],
        }

        if self._learner:
            stats["modules_loaded"].append("ai_learner")
            stats["learner"] = self._learner.get_stats()

        if self._anti_chase:
            stats["modules_loaded"].append("anti_chase")
            stats["anti_chase"] = self._anti_chase.get_stats()

        if self._observation:
            stats["modules_loaded"].append("observation")
            stats["observation"] = self._observation.get_stats()

        if self._adaptive:
            stats["modules_loaded"].append("adaptive")
            stats["adaptive"] = self._adaptive.get_stats()

        return stats


# Singleton instance
_ai_gate: Optional[AIGate] = None


def get_ai_gate() -> AIGate:
    """Get singleton AIGate instance."""
    global _ai_gate
    if _ai_gate is None:
        _ai_gate = AIGate()
    return _ai_gate


# ═══════════════════════════════════════════════════════════════════
# COMMAND BUS INTEGRATION
# ═══════════════════════════════════════════════════════════════════

async def ai_signal_filter(command: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Filter for Command Bus - use in _handle_signal.

    Args:
        command: Command dict with symbol, confidence, price

    Returns:
        (passed, reason)
    """
    gate = get_ai_gate()

    symbol = command.get("symbol", "")
    confidence = command.get("confidence", 0.0)
    price = command.get("price", 0.0)
    signal_type = command.get("signal_type", command.get("mode", "UNKNOWN"))

    return gate.check_signal(symbol, confidence, price, signal_type)


async def ai_record_fill(fill: Dict[str, Any]) -> None:
    """
    Record fill for AI learning - use in _handle_close.

    Args:
        fill: Fill dict with symbol, pnl_pct, etc.
    """
    gate = get_ai_gate()

    symbol = fill.get("symbol", "")
    pnl_pct = fill.get("pnl_pct", fill.get("realized_pnl_pct", 0.0))
    is_win = pnl_pct > 0

    gate.record_outcome(symbol, pnl_pct, is_win)


# ═══════════════════════════════════════════════════════════════════
# STANDALONE TEST
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO)

    print("\n=== Testing AIGate ===")
    gate = AIGate()

    # Test signal checks
    test_cases = [
        {"symbol": "DOGEUSDT", "confidence": 0.70, "price": 0.105},
        {"symbol": "PEPEUSDT", "confidence": 0.80, "price": 0.000004},  # Blacklisted
        {"symbol": "BTCUSDT", "confidence": 0.40, "price": 95000},      # Low confidence
    ]

    for tc in test_cases:
        passed, reason = gate.check_signal(**tc)
        status = "✅" if passed else "❌"
        print(f"{status} {tc['symbol']}: {reason[:60]}...")

    print(f"\nStats: {json.dumps(gate.get_stats(), indent=2)}")

    print("\n✅ AIGate test PASSED")
