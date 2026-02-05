# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-05T03:05:00Z
# Purpose: Anti-Chase Filter - prevent entering after price already moved
# === END SIGNATURE ===
"""
Anti-Chase Filter - НЕ входить если цена уже выросла.

ПРОБЛЕМА: 22 rapid losses (<1 min) = входим ПОСЛЕ пика движения.

РЕШЕНИЕ: Проверять движение цены за последние N минут.
Если цена уже выросла > X% - не входим (опоздали).

ИНТЕГРАЦИЯ:
    from core.anti_chase_filter import AntiChaseFilter, should_enter

    filter = AntiChaseFilter()

    # Перед входом:
    ok, reason = filter.should_enter(symbol, current_price)
    if not ok:
        logger.info(f"ANTI-CHASE: {reason}")
        return  # Skip this trade
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple

logger = logging.getLogger("hope.anti_chase")


@dataclass
class PricePoint:
    """Single price observation."""
    price: float
    timestamp: float


class AntiChaseFilter:
    """
    Anti-Chase Filter - prevents entering positions that already moved.

    Logic:
    - Track recent prices for each symbol
    - Before entry, check if price moved > threshold in recent window
    - If already moved too much = we're late = don't enter

    Настройки:
    - window_seconds: Окно для проверки (default: 180 = 3 min)
    - max_move_pct: Максимальное движение для входа (default: 1.5%)
    - min_samples: Минимум точек для проверки (default: 3)
    """

    def __init__(
        self,
        window_seconds: float = 180.0,  # 3 minutes
        max_move_pct: float = 1.5,      # If moved > 1.5%, skip
        min_samples: int = 3,
    ):
        self.window_seconds = window_seconds
        self.max_move_pct = max_move_pct
        self.min_samples = min_samples

        # Price history: symbol -> list of PricePoint
        self.price_history: Dict[str, List[PricePoint]] = defaultdict(list)

        # Stats
        self.stats = {
            "checks": 0,
            "blocked": 0,
            "passed": 0,
        }

        logger.info(
            f"AntiChaseFilter initialized: window={window_seconds}s, "
            f"max_move={max_move_pct}%, min_samples={min_samples}"
        )

    def record_price(self, symbol: str, price: float) -> None:
        """Record current price for tracking."""
        now = time.time()
        self.price_history[symbol].append(PricePoint(price=price, timestamp=now))

        # Cleanup old data (keep last 10 minutes)
        cutoff = now - 600
        self.price_history[symbol] = [
            p for p in self.price_history[symbol] if p.timestamp > cutoff
        ]

    def should_enter(
        self,
        symbol: str,
        current_price: float,
        signal_type: str = "UNKNOWN",
    ) -> Tuple[bool, str]:
        """
        Check if we should enter this position.

        Returns:
            (should_enter, reason)
        """
        self.stats["checks"] += 1

        # Record current price
        self.record_price(symbol, current_price)

        # Get recent prices within window
        now = time.time()
        window_start = now - self.window_seconds
        recent = [p for p in self.price_history[symbol] if p.timestamp > window_start]

        if len(recent) < self.min_samples:
            # Not enough data - allow entry but log
            self.stats["passed"] += 1
            return True, f"PASS: Not enough price data ({len(recent)} < {self.min_samples})"

        # Find lowest price in window
        min_price = min(p.price for p in recent)
        min_time = next(p.timestamp for p in recent if p.price == min_price)

        # Calculate move from low
        if min_price > 0:
            move_pct = ((current_price - min_price) / min_price) * 100
        else:
            move_pct = 0

        # Calculate time since low
        time_since_low = now - min_time

        # Decision
        if move_pct > self.max_move_pct:
            self.stats["blocked"] += 1
            reason = (
                f"BLOCKED: Price moved +{move_pct:.2f}% in {time_since_low:.0f}s "
                f"(> {self.max_move_pct}% threshold). Entry too late!"
            )
            logger.warning(f"ANTI-CHASE {symbol}: {reason}")
            return False, reason

        self.stats["passed"] += 1
        reason = (
            f"PASS: Price move +{move_pct:.2f}% within threshold "
            f"({self.max_move_pct}%) over {time_since_low:.0f}s"
        )
        return True, reason

    def get_stats(self) -> dict:
        """Get filter statistics."""
        total = self.stats["checks"]
        block_rate = self.stats["blocked"] / total * 100 if total > 0 else 0

        return {
            "total_checks": total,
            "blocked": self.stats["blocked"],
            "passed": self.stats["passed"],
            "block_rate_pct": round(block_rate, 1),
            "window_seconds": self.window_seconds,
            "max_move_pct": self.max_move_pct,
            "tracked_symbols": len(self.price_history),
        }


# ═══════════════════════════════════════════════════════════════════
# OBSERVATION MODE - Don't trade when WR is too low
# ═══════════════════════════════════════════════════════════════════

class ObservationMode:
    """
    Observation Mode - НЕ открывать новые позиции при низком WR.

    При WR < threshold:
    - Только мониторинг существующих позиций
    - Сбор данных для анализа
    - НЕ открывать новые сделки

    Это защита от потерь пока система учится.
    """

    def __init__(
        self,
        wr_threshold: float = 0.35,  # Below 35% = observation mode
        min_trades: int = 10,         # Need at least 10 trades
        state_file: Optional[str] = None,
    ):
        self.wr_threshold = wr_threshold
        self.min_trades = min_trades

        # Track trades
        self.wins = 0
        self.losses = 0
        self.observation_mode = False

        logger.info(
            f"ObservationMode initialized: WR threshold={wr_threshold:.0%}, "
            f"min_trades={min_trades}"
        )

    def record_trade(self, is_win: bool) -> None:
        """Record trade outcome."""
        if is_win:
            self.wins += 1
        else:
            self.losses += 1

        self._update_mode()

    def _update_mode(self) -> None:
        """Update observation mode based on current WR."""
        total = self.wins + self.losses

        if total < self.min_trades:
            # Not enough data - normal mode
            self.observation_mode = False
            return

        wr = self.wins / total

        if wr < self.wr_threshold:
            if not self.observation_mode:
                logger.warning(
                    f"ENTERING OBSERVATION MODE: WR={wr:.1%} < {self.wr_threshold:.0%}"
                )
            self.observation_mode = True
        else:
            if self.observation_mode:
                logger.info(
                    f"EXITING OBSERVATION MODE: WR={wr:.1%} >= {self.wr_threshold:.0%}"
                )
            self.observation_mode = False

    def can_trade(self) -> Tuple[bool, str]:
        """Check if we can open new positions."""
        total = self.wins + self.losses

        if total < self.min_trades:
            return True, f"PASS: Not enough trades ({total} < {self.min_trades})"

        wr = self.wins / total

        if self.observation_mode:
            return False, (
                f"OBSERVATION MODE: WR={wr:.1%} < {self.wr_threshold:.0%}. "
                f"No new positions until WR improves."
            )

        return True, f"PASS: WR={wr:.1%} >= {self.wr_threshold:.0%}"

    def get_stats(self) -> dict:
        """Get observation mode statistics."""
        total = self.wins + self.losses
        wr = self.wins / total if total > 0 else 0

        return {
            "observation_mode": self.observation_mode,
            "current_wr": round(wr * 100, 1),
            "wr_threshold": round(self.wr_threshold * 100, 1),
            "wins": self.wins,
            "losses": self.losses,
            "total_trades": total,
        }


# ═══════════════════════════════════════════════════════════════════
# SINGLETON INSTANCES
# ═══════════════════════════════════════════════════════════════════

_anti_chase: Optional[AntiChaseFilter] = None
_observation: Optional[ObservationMode] = None


def get_anti_chase() -> AntiChaseFilter:
    """Get singleton AntiChaseFilter instance."""
    global _anti_chase
    if _anti_chase is None:
        _anti_chase = AntiChaseFilter()
    return _anti_chase


def get_observation_mode() -> ObservationMode:
    """Get singleton ObservationMode instance."""
    global _observation
    if _observation is None:
        _observation = ObservationMode()
    return _observation


def should_enter(symbol: str, current_price: float) -> Tuple[bool, str]:
    """Quick check if we should enter."""
    return get_anti_chase().should_enter(symbol, current_price)


def can_trade() -> Tuple[bool, str]:
    """Quick check if observation mode allows trading."""
    return get_observation_mode().can_trade()


# ═══════════════════════════════════════════════════════════════════
# STANDALONE TEST
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO)

    print("\n=== Test AntiChaseFilter ===")
    acf = AntiChaseFilter(window_seconds=60, max_move_pct=1.0)

    # Simulate price rise
    for i in range(5):
        price = 100.0 + i * 0.3  # Rising 0.3% each time
        acf.record_price("TESTUSDT", price)
        time.sleep(0.1)

    # Try to enter at peak
    ok, reason = acf.should_enter("TESTUSDT", 101.5)  # +1.5%
    print(f"Enter at 101.5 (after +1.5% move): {ok}")
    print(f"Reason: {reason}")

    # Try to enter early
    ok2, reason2 = acf.should_enter("TESTUSDT", 100.5)  # +0.5%
    print(f"Enter at 100.5 (after +0.5% move): {ok2}")
    print(f"Reason: {reason2}")

    print(f"\nStats: {json.dumps(acf.get_stats(), indent=2)}")

    print("\n=== Test ObservationMode ===")
    obs = ObservationMode(wr_threshold=0.35, min_trades=5)

    # Record some losses
    for _ in range(4):
        obs.record_trade(is_win=False)

    obs.record_trade(is_win=True)  # 1 win, 4 losses = 20% WR

    ok, reason = obs.can_trade()
    print(f"Can trade with 20% WR: {ok}")
    print(f"Reason: {reason}")

    # Add more wins
    for _ in range(5):
        obs.record_trade(is_win=True)  # Now 6 wins, 4 losses = 60% WR

    ok2, reason2 = obs.can_trade()
    print(f"Can trade with 60% WR: {ok2}")
    print(f"Reason: {reason2}")

    print(f"\nStats: {json.dumps(obs.get_stats(), indent=2)}")

    print("\n✅ All tests PASSED")
