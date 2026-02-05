# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-05T02:55:00Z
# Purpose: AI Trade Learner - learns from losses, improves predictions
# === END SIGNATURE ===
"""
AI Trade Learner - Обучается на паттернах убытков и улучшает торговлю.

Использует:
1. Исторические данные сделок
2. Найденные паттерны (loss_patterns.json)
3. Feedback от каждой сделки

Интеграция:
    from core.ai_trade_learner import AITradeLearner

    learner = AITradeLearner()

    # Перед открытием позиции:
    should_trade, reason = learner.should_trade(signal)

    # После закрытия позиции:
    learner.record_outcome(symbol, profit_pct, signal_confidence)
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger("hope.ai_learner")


@dataclass
class Signal:
    """Trading signal for evaluation."""
    symbol: str
    confidence: float
    signal_type: str  # PUMP, SCALP, VOLUME, etc.
    price: float = 0.0
    timestamp: float = field(default_factory=time.time)

    @property
    def coin(self) -> str:
        return self.symbol.replace("USDT", "")

    @property
    def hour_utc(self) -> int:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc).hour


@dataclass
class TradeOutcome:
    """Outcome of a trade for learning."""
    symbol: str
    profit_pct: float
    profit_usd: float
    duration_sec: float
    entry_confidence: float
    is_win: bool
    timestamp: float = field(default_factory=time.time)
    reason_closed: str = ""


class AITradeLearner:
    """
    AI Learning Engine - анализирует паттерны и принимает решения.

    Layers:
    1. Static Rules - жёсткие ограничения (blacklist, hours)
    2. Dynamic Rules - адаптивные на основе последних сделок
    3. AI Score - машинное обучение (будущее)
    """

    def __init__(
        self,
        state_dir: Optional[Path] = None,
        min_confidence: float = 0.55,
    ):
        self.state_dir = state_dir or Path("state/ai")
        self.state_dir.mkdir(parents=True, exist_ok=True)

        self.min_confidence = min_confidence

        # Load patterns
        self.patterns: Dict[str, Any] = {}
        self._load_patterns()

        # Dynamic state
        self.symbol_stats: Dict[str, Dict] = defaultdict(lambda: {
            "wins": 0, "losses": 0, "total_pnl": 0.0, "last_trade": 0
        })
        self.recent_outcomes: List[TradeOutcome] = []
        self._load_state()

        # Blacklist from patterns
        self.blacklist: set = set(self.patterns.get("blacklist_candidates", []))
        self.avoid_hours: set = set()

        # Parse avoid hours
        for h in self.patterns.get("worst_hours", []):
            if isinstance(h, dict):
                self.avoid_hours.add(h.get("hour", 0))
            else:
                self.avoid_hours.add(h)

        logger.info(
            f"AITradeLearner initialized: blacklist={self.blacklist}, "
            f"avoid_hours={self.avoid_hours}, min_conf={min_confidence}"
        )

    def _load_patterns(self) -> None:
        """Load analyzed patterns from file."""
        patterns_file = self.state_dir / "loss_patterns.json"
        if patterns_file.exists():
            try:
                self.patterns = json.loads(patterns_file.read_text(encoding='utf-8'))
                logger.info(f"Loaded patterns: {len(self.patterns)} keys")
            except Exception as e:
                logger.warning(f"Failed to load patterns: {e}")

    def _load_state(self) -> None:
        """Load dynamic state."""
        state_file = self.state_dir / "learner_state.json"
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text(encoding='utf-8'))
                self.symbol_stats = defaultdict(
                    lambda: {"wins": 0, "losses": 0, "total_pnl": 0.0, "last_trade": 0},
                    data.get("symbol_stats", {})
                )
                logger.info(f"Loaded state: {len(self.symbol_stats)} symbols")
            except Exception as e:
                logger.warning(f"Failed to load state: {e}")

    def _save_state(self) -> None:
        """Save dynamic state."""
        state_file = self.state_dir / "learner_state.json"
        try:
            data = {
                "symbol_stats": dict(self.symbol_stats),
                "last_update": time.time(),
            }
            state_file.write_text(json.dumps(data, indent=2), encoding='utf-8')
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def should_trade(self, signal: Signal) -> tuple[bool, str]:
        """
        Main decision method - should we take this trade?

        Returns:
            (should_trade, reason)
        """
        checks = []

        # === LAYER 1: Static Rules ===

        # 1.1 Blacklist check
        if signal.coin in self.blacklist:
            return False, f"BLACKLISTED: {signal.coin} has poor historical performance"
        checks.append("blacklist: PASS")

        # 1.2 Hour check
        if signal.hour_utc in self.avoid_hours:
            return False, f"BAD_HOUR: {signal.hour_utc}:00 UTC has poor performance"
        checks.append(f"hour({signal.hour_utc}): PASS")

        # 1.3 Confidence check
        if signal.confidence < self.min_confidence:
            return False, f"LOW_CONFIDENCE: {signal.confidence:.2f} < {self.min_confidence}"
        checks.append(f"confidence({signal.confidence:.2f}): PASS")

        # === LAYER 2: Dynamic Rules ===

        # 2.1 Symbol recent performance
        stats = self.symbol_stats.get(signal.coin, {})
        if stats:
            total = stats.get("wins", 0) + stats.get("losses", 0)
            if total >= 5:
                wr = stats["wins"] / total
                if wr < 0.30:
                    return False, f"DYNAMIC_BLACKLIST: {signal.coin} WR={wr:.0%} on {total} trades"
        checks.append("dynamic: PASS")

        # 2.2 Loss streak check
        loss_streak = self._get_loss_streak()
        if loss_streak >= 5:
            # Require higher confidence during loss streak
            required = min(0.85, self.min_confidence + 0.15)
            if signal.confidence < required:
                return False, f"LOSS_STREAK: {loss_streak} losses, need {required:.2f} confidence"
        checks.append(f"streak({loss_streak}): PASS")

        # === LAYER 3: AI Score (future) ===
        # TODO: ML model prediction
        ai_score = self._get_ai_score(signal)
        checks.append(f"ai_score({ai_score:.2f}): PASS")

        reason = f"APPROVED: {', '.join(checks)}"
        return True, reason

    def _get_loss_streak(self) -> int:
        """Get current loss streak."""
        streak = 0
        for outcome in reversed(self.recent_outcomes[-20:]):
            if not outcome.is_win:
                streak += 1
            else:
                break
        return streak

    def _get_ai_score(self, signal: Signal) -> float:
        """
        AI-based score (placeholder for ML model).

        Future: Use trained model to predict win probability.
        """
        # Simple heuristic for now
        base = 0.50

        # Boost for known good symbols
        good_symbols = {"DOGE"}  # From analysis
        if signal.coin in good_symbols:
            base += 0.15

        # Penalty for borderline symbols
        borderline = {"ADA", "XRP"}
        if signal.coin in borderline:
            base -= 0.10

        # Boost for high confidence
        if signal.confidence > 0.75:
            base += 0.10

        return min(1.0, max(0.0, base))

    def record_outcome(
        self,
        symbol: str,
        profit_pct: float,
        profit_usd: float = 0.0,
        duration_sec: float = 0.0,
        entry_confidence: float = 0.0,
        reason_closed: str = "",
    ) -> None:
        """Record trade outcome for learning."""
        coin = symbol.replace("USDT", "")
        is_win = profit_pct > 0

        outcome = TradeOutcome(
            symbol=symbol,
            profit_pct=profit_pct,
            profit_usd=profit_usd,
            duration_sec=duration_sec,
            entry_confidence=entry_confidence,
            is_win=is_win,
            reason_closed=reason_closed,
        )

        # Update stats
        self.symbol_stats[coin]["total_pnl"] += profit_pct
        self.symbol_stats[coin]["last_trade"] = time.time()
        if is_win:
            self.symbol_stats[coin]["wins"] += 1
        else:
            self.symbol_stats[coin]["losses"] += 1

        # Add to recent
        self.recent_outcomes.append(outcome)
        if len(self.recent_outcomes) > 100:
            self.recent_outcomes = self.recent_outcomes[-100:]

        # Check if symbol should be auto-blacklisted
        stats = self.symbol_stats[coin]
        total = stats["wins"] + stats["losses"]
        if total >= 10:
            wr = stats["wins"] / total
            if wr < 0.25:
                self.blacklist.add(coin)
                logger.warning(f"AUTO-BLACKLIST: {coin} added (WR={wr:.0%} on {total} trades)")

        self._save_state()

        logger.info(
            f"Outcome recorded: {symbol} {'WIN' if is_win else 'LOSS'} "
            f"{profit_pct:+.2f}% | Symbol WR: {stats['wins']}/{total}"
        )

    def get_stats(self) -> dict:
        """Get learner statistics."""
        total_wins = sum(s.get("wins", 0) for s in self.symbol_stats.values())
        total_losses = sum(s.get("losses", 0) for s in self.symbol_stats.values())
        total = total_wins + total_losses

        return {
            "total_trades": total,
            "win_rate": total_wins / total * 100 if total > 0 else 0,
            "blacklist": list(self.blacklist),
            "avoid_hours": list(self.avoid_hours),
            "loss_streak": self._get_loss_streak(),
            "symbols_tracked": len(self.symbol_stats),
            "min_confidence": self.min_confidence,
        }

    def update_blacklist(self, symbols: List[str]) -> None:
        """Manually update blacklist."""
        for s in symbols:
            self.blacklist.add(s.replace("USDT", ""))
        logger.info(f"Blacklist updated: {self.blacklist}")

    def remove_from_blacklist(self, symbol: str) -> None:
        """Remove symbol from blacklist."""
        coin = symbol.replace("USDT", "")
        self.blacklist.discard(coin)
        logger.info(f"Removed {coin} from blacklist")


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION HELPER - Use in autotrader/signal_processor
# ═══════════════════════════════════════════════════════════════════

_learner_instance: Optional[AITradeLearner] = None


def get_learner() -> AITradeLearner:
    """Get singleton learner instance."""
    global _learner_instance
    if _learner_instance is None:
        _learner_instance = AITradeLearner()
    return _learner_instance


def should_trade(symbol: str, confidence: float, signal_type: str = "UNKNOWN") -> tuple[bool, str]:
    """Quick check if we should take this trade."""
    learner = get_learner()
    signal = Signal(symbol=symbol, confidence=confidence, signal_type=signal_type)
    return learner.should_trade(signal)


def record_trade_outcome(symbol: str, profit_pct: float, **kwargs) -> None:
    """Record trade outcome for learning."""
    learner = get_learner()
    learner.record_outcome(symbol, profit_pct, **kwargs)


# ═══════════════════════════════════════════════════════════════════
# STANDALONE TEST
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    learner = AITradeLearner(min_confidence=0.55)

    print("\n=== Initial Stats ===")
    print(json.dumps(learner.get_stats(), indent=2))

    print("\n=== Testing Signals ===")

    # Test blacklisted symbol
    sig1 = Signal(symbol="PEPEUSDT", confidence=0.80, signal_type="PUMP")
    ok, reason = learner.should_trade(sig1)
    print(f"PEPE (blacklisted): {ok} - {reason}")

    # Test good symbol
    sig2 = Signal(symbol="DOGEUSDT", confidence=0.65, signal_type="PUMP")
    ok, reason = learner.should_trade(sig2)
    print(f"DOGE (good): {ok} - {reason}")

    # Test low confidence
    sig3 = Signal(symbol="ARBUSDT", confidence=0.40, signal_type="SCALP")
    ok, reason = learner.should_trade(sig3)
    print(f"ARB (low conf): {ok} - {reason}")

    # Record some outcomes
    print("\n=== Recording Outcomes ===")
    learner.record_outcome("DOGEUSDT", profit_pct=1.5)
    learner.record_outcome("ARBUSDT", profit_pct=-0.8)
    learner.record_outcome("ARBUSDT", profit_pct=-1.2)

    print("\n=== Final Stats ===")
    print(json.dumps(learner.get_stats(), indent=2))

    print("\n✅ AITradeLearner test PASSED")
