# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 09:20:00 UTC
# Purpose: Policy-based decision engine for trading signals
# Contract: fail-closed, all checks must PASS for BUY
# === END SIGNATURE ===
"""
Decision Engine - Central policy enforcement for trading decisions.

Evaluates signals against multiple criteria:
- Market regime compatibility
- Anomaly score threshold
- ML prediction confidence
- Circuit breaker state
- Volume requirements
- News sentiment

INVARIANT: No trade without ALL checks passing (fail-closed).

Decision Flow:
    Signal → Regime Check → Anomaly Check → Prediction Check
          → Circuit Check → Volume Check → News Check → BUY/SKIP
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from ..contracts import MarketRegime, AnomalySeverity

logger = logging.getLogger(__name__)


class Action(str, Enum):
    """Trading action."""
    BUY = "BUY"
    SKIP = "SKIP"
    HOLD = "HOLD"  # For existing positions


class SkipReason(str, Enum):
    """Reason for skipping a signal."""
    REGIME_UNFAVORABLE = "regime_unfavorable"
    ANOMALY_HIGH = "anomaly_high"
    PREDICTION_LOW = "prediction_low"
    CIRCUIT_OPEN = "circuit_open"
    VOLUME_LOW = "volume_low"
    NEWS_NEGATIVE = "news_negative"
    SYMBOL_BLOCKED = "symbol_blocked"
    COOLDOWN_ACTIVE = "cooldown_active"
    MAX_POSITIONS = "max_positions"
    UNKNOWN = "unknown"


@dataclass
class PolicyConfig:
    """Decision engine policy configuration."""
    # Prediction thresholds
    prediction_min: float = 0.65           # Min probability to BUY
    prediction_strong: float = 0.80        # Strong signal threshold

    # Anomaly thresholds
    anomaly_max: float = 0.30              # Max anomaly score
    anomaly_critical: float = 0.50         # Critical anomaly (block all)

    # Volume thresholds (USD)
    volume_min_24h: float = 5_000_000      # Min 24h volume
    volume_strong: float = 20_000_000      # Strong volume threshold

    # Allowed regimes
    allowed_regimes: Set[MarketRegime] = field(default_factory=lambda: {
        MarketRegime.TRENDING_UP,
        MarketRegime.HIGH_VOLATILITY,
    })

    # Position limits
    max_positions: int = 5                 # Max concurrent positions
    cooldown_seconds: int = 300            # Min time between signals per symbol

    # News impact
    news_negative_threshold: float = -0.3  # News score below this = skip


@dataclass
class SignalContext:
    """Context for signal evaluation."""
    # Signal data
    signal_id: str
    symbol: str
    price: float
    direction: str
    delta_pct: float
    volume_24h: float

    # AI module outputs
    prediction_prob: Optional[float] = None
    regime: Optional[MarketRegime] = None
    anomaly_score: Optional[float] = None
    news_score: Optional[float] = None

    # System state
    circuit_state: str = "CLOSED"  # CLOSED/OPEN/HALF_OPEN
    active_positions: int = 0
    last_signal_time: Optional[float] = None  # For cooldown

    # Raw signal data for logging
    raw_signal: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Decision:
    """Decision result with full audit trail."""
    signal_id: str
    symbol: str
    action: Action
    confidence: float
    timestamp: str

    # Check results
    checks_passed: Dict[str, bool] = field(default_factory=dict)
    checks_values: Dict[str, Any] = field(default_factory=dict)

    # Skip reasons (if SKIP)
    reasons: List[SkipReason] = field(default_factory=list)

    # Recommendation details
    position_size_modifier: float = 1.0
    entry_price: float = 0.0
    stop_loss_pct: float = 2.0
    take_profit_pct: float = 5.0

    # Checksum for audit
    checksum: str = ""

    def __post_init__(self):
        """Compute checksum after initialization."""
        self.checksum = self._compute_checksum()

    def _compute_checksum(self) -> str:
        """Compute deterministic checksum."""
        data = {
            "signal_id": self.signal_id,
            "action": self.action.value,
            "checks": self.checks_passed,
            "reasons": [r.value for r in self.reasons],
        }
        canonical = json.dumps(data, sort_keys=True, default=str)
        return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging/persistence."""
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "action": self.action.value,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
            "checks_passed": self.checks_passed,
            "checks_values": self.checks_values,
            "reasons": [r.value for r in self.reasons],
            "position_size_modifier": self.position_size_modifier,
            "entry_price": self.entry_price,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "checksum": self.checksum,
        }


class DecisionEngine:
    """
    Policy-based decision engine.

    INVARIANT: No BUY without ALL checks passing.

    Usage:
        engine = DecisionEngine()

        # Evaluate signal
        ctx = SignalContext(
            signal_id="sig:abc123",
            symbol="XVSUSDT",
            price=3.54,
            direction="Long",
            delta_pct=2.9,
            volume_24h=5_300_000,
            prediction_prob=0.72,
            regime=MarketRegime.TRENDING_UP,
            anomaly_score=0.15,
        )

        decision = engine.evaluate(ctx)

        if decision.action == Action.BUY:
            execute_trade(decision)
        else:
            log_skip(decision)
    """

    # Required checks for BUY decision
    REQUIRED_CHECKS = [
        "regime_ok",
        "anomaly_ok",
        "prediction_ok",
        "circuit_ok",
        "volume_ok",
        "news_ok",
        "cooldown_ok",
        "positions_ok",
    ]

    def __init__(
        self,
        config: Optional[PolicyConfig] = None,
        blocked_symbols: Optional[Set[str]] = None,
    ):
        """
        Initialize decision engine.

        Args:
            config: Policy configuration
            blocked_symbols: Symbols to always skip
        """
        self.config = config or PolicyConfig()
        self.blocked_symbols = blocked_symbols or set()

        # Track last signal time per symbol (for cooldown)
        self._last_signal_times: Dict[str, float] = {}

        # Stats
        self._total_evaluated = 0
        self._total_buys = 0
        self._total_skips = 0
        self._skip_reasons_count: Dict[SkipReason, int] = {r: 0 for r in SkipReason}

        logger.info("DecisionEngine initialized")

    def evaluate(self, ctx: SignalContext) -> Decision:
        """
        Evaluate signal and return decision.

        INVARIANT: ALL checks must pass for BUY.

        Args:
            ctx: Signal context with all required data

        Returns:
            Decision with action, confidence, and audit trail
        """
        self._total_evaluated += 1
        timestamp = datetime.utcnow().isoformat() + "Z"

        # Check if symbol is blocked
        if ctx.symbol in self.blocked_symbols:
            return self._create_skip_decision(
                ctx, timestamp,
                {k: False for k in self.REQUIRED_CHECKS},
                {},
                [SkipReason.SYMBOL_BLOCKED]
            )

        # Run all checks
        checks = {}
        values = {}
        reasons = []

        # 1. Regime check
        regime_ok, regime_value = self._check_regime(ctx)
        checks["regime_ok"] = regime_ok
        values["regime"] = regime_value
        if not regime_ok:
            reasons.append(SkipReason.REGIME_UNFAVORABLE)

        # 2. Anomaly check
        anomaly_ok, anomaly_value = self._check_anomaly(ctx)
        checks["anomaly_ok"] = anomaly_ok
        values["anomaly_score"] = anomaly_value
        if not anomaly_ok:
            reasons.append(SkipReason.ANOMALY_HIGH)

        # 3. Prediction check
        prediction_ok, prediction_value = self._check_prediction(ctx)
        checks["prediction_ok"] = prediction_ok
        values["prediction_prob"] = prediction_value
        if not prediction_ok:
            reasons.append(SkipReason.PREDICTION_LOW)

        # 4. Circuit breaker check
        circuit_ok, circuit_value = self._check_circuit(ctx)
        checks["circuit_ok"] = circuit_ok
        values["circuit_state"] = circuit_value
        if not circuit_ok:
            reasons.append(SkipReason.CIRCUIT_OPEN)

        # 5. Volume check
        volume_ok, volume_value = self._check_volume(ctx)
        checks["volume_ok"] = volume_ok
        values["volume_24h"] = volume_value
        if not volume_ok:
            reasons.append(SkipReason.VOLUME_LOW)

        # 6. News check
        news_ok, news_value = self._check_news(ctx)
        checks["news_ok"] = news_ok
        values["news_score"] = news_value
        if not news_ok:
            reasons.append(SkipReason.NEWS_NEGATIVE)

        # 7. Cooldown check
        cooldown_ok, cooldown_value = self._check_cooldown(ctx)
        checks["cooldown_ok"] = cooldown_ok
        values["cooldown_remaining"] = cooldown_value
        if not cooldown_ok:
            reasons.append(SkipReason.COOLDOWN_ACTIVE)

        # 8. Position limit check
        positions_ok, positions_value = self._check_positions(ctx)
        checks["positions_ok"] = positions_ok
        values["active_positions"] = positions_value
        if not positions_ok:
            reasons.append(SkipReason.MAX_POSITIONS)

        # INVARIANT: ALL checks must pass for BUY
        if all(checks.values()):
            decision = self._create_buy_decision(ctx, timestamp, checks, values)
            self._total_buys += 1

            # Update cooldown
            self._last_signal_times[ctx.symbol] = time.time()

            logger.info(
                f"DECISION: BUY {ctx.symbol} @ {ctx.price:.4f} "
                f"(prob={ctx.prediction_prob:.2f}, delta={ctx.delta_pct:.1f}%)"
            )
        else:
            decision = self._create_skip_decision(ctx, timestamp, checks, values, reasons)
            self._total_skips += 1

            for reason in reasons:
                self._skip_reasons_count[reason] += 1

            logger.info(
                f"DECISION: SKIP {ctx.symbol} - reasons: {[r.value for r in reasons]}"
            )

        return decision

    def _check_regime(self, ctx: SignalContext) -> tuple[bool, str]:
        """Check if market regime is favorable."""
        if ctx.regime is None:
            # FAIL-CLOSED: Unknown regime = skip
            return False, "unknown"

        ok = ctx.regime in self.config.allowed_regimes
        return ok, ctx.regime.value

    def _check_anomaly(self, ctx: SignalContext) -> tuple[bool, float]:
        """Check if anomaly score is acceptable."""
        if ctx.anomaly_score is None:
            # FAIL-CLOSED: Unknown anomaly = skip
            return False, -1.0

        # Critical anomaly blocks all
        if ctx.anomaly_score >= self.config.anomaly_critical:
            return False, ctx.anomaly_score

        ok = ctx.anomaly_score < self.config.anomaly_max
        return ok, ctx.anomaly_score

    def _check_prediction(self, ctx: SignalContext) -> tuple[bool, float]:
        """Check if prediction probability is sufficient."""
        if ctx.prediction_prob is None:
            # FAIL-CLOSED: No prediction = skip
            return False, -1.0

        ok = ctx.prediction_prob >= self.config.prediction_min
        return ok, ctx.prediction_prob

    def _check_circuit(self, ctx: SignalContext) -> tuple[bool, str]:
        """Check if circuit breaker is closed."""
        ok = ctx.circuit_state == "CLOSED"
        return ok, ctx.circuit_state

    def _check_volume(self, ctx: SignalContext) -> tuple[bool, float]:
        """Check if volume is sufficient."""
        ok = ctx.volume_24h >= self.config.volume_min_24h
        return ok, ctx.volume_24h

    def _check_news(self, ctx: SignalContext) -> tuple[bool, float]:
        """Check if news sentiment is acceptable."""
        if ctx.news_score is None:
            # No news data = assume neutral (pass)
            return True, 0.0

        ok = ctx.news_score >= self.config.news_negative_threshold
        return ok, ctx.news_score

    def _check_cooldown(self, ctx: SignalContext) -> tuple[bool, float]:
        """Check if symbol is in cooldown period."""
        last_time = self._last_signal_times.get(ctx.symbol)
        if last_time is None:
            return True, 0.0

        elapsed = time.time() - last_time
        remaining = self.config.cooldown_seconds - elapsed

        if remaining > 0:
            return False, remaining

        return True, 0.0

    def _check_positions(self, ctx: SignalContext) -> tuple[bool, int]:
        """Check if position limit is reached."""
        ok = ctx.active_positions < self.config.max_positions
        return ok, ctx.active_positions

    def _create_buy_decision(
        self,
        ctx: SignalContext,
        timestamp: str,
        checks: Dict[str, bool],
        values: Dict[str, Any],
    ) -> Decision:
        """Create BUY decision with position sizing."""
        # Calculate confidence
        confidence = ctx.prediction_prob or 0.5

        # Position size modifier based on signal strength
        size_modifier = 1.0
        if ctx.prediction_prob and ctx.prediction_prob >= self.config.prediction_strong:
            size_modifier = 1.5
        if ctx.volume_24h >= self.config.volume_strong:
            size_modifier *= 1.2
        size_modifier = min(size_modifier, 2.0)  # Cap at 2x

        # Risk parameters based on direction
        if ctx.direction == "Long":
            stop_loss_pct = 2.0
            take_profit_pct = max(5.0, ctx.delta_pct * 2)
        else:
            stop_loss_pct = 2.0
            take_profit_pct = max(3.0, ctx.delta_pct * 1.5)

        return Decision(
            signal_id=ctx.signal_id,
            symbol=ctx.symbol,
            action=Action.BUY,
            confidence=confidence,
            timestamp=timestamp,
            checks_passed=checks,
            checks_values=values,
            reasons=[],
            position_size_modifier=round(size_modifier, 2),
            entry_price=ctx.price,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
        )

    def _create_skip_decision(
        self,
        ctx: SignalContext,
        timestamp: str,
        checks: Dict[str, bool],
        values: Dict[str, Any],
        reasons: List[SkipReason],
    ) -> Decision:
        """Create SKIP decision."""
        return Decision(
            signal_id=ctx.signal_id,
            symbol=ctx.symbol,
            action=Action.SKIP,
            confidence=0.0,
            timestamp=timestamp,
            checks_passed=checks,
            checks_values=values,
            reasons=reasons,
            position_size_modifier=0.0,
            entry_price=ctx.price,
        )

    def update_config(self, **kwargs) -> None:
        """Update policy configuration."""
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
                logger.info(f"Policy updated: {key}={value}")

    def block_symbol(self, symbol: str) -> None:
        """Add symbol to blocklist."""
        self.blocked_symbols.add(symbol.upper())

    def unblock_symbol(self, symbol: str) -> None:
        """Remove symbol from blocklist."""
        self.blocked_symbols.discard(symbol.upper())

    def get_stats(self) -> Dict[str, Any]:
        """Get engine statistics."""
        return {
            "total_evaluated": self._total_evaluated,
            "total_buys": self._total_buys,
            "total_skips": self._total_skips,
            "buy_rate": (
                self._total_buys / self._total_evaluated
                if self._total_evaluated > 0 else 0.0
            ),
            "skip_reasons": {
                r.value: c for r, c in self._skip_reasons_count.items() if c > 0
            },
            "blocked_symbols": list(self.blocked_symbols),
            "config": {
                "prediction_min": self.config.prediction_min,
                "anomaly_max": self.config.anomaly_max,
                "volume_min_24h": self.config.volume_min_24h,
                "max_positions": self.config.max_positions,
            },
        }


# === Singleton Instance ===

_engine: Optional[DecisionEngine] = None


def get_decision_engine(config: Optional[PolicyConfig] = None) -> DecisionEngine:
    """Get or create singleton decision engine."""
    global _engine

    if _engine is None:
        _engine = DecisionEngine(config=config)

    return _engine


def evaluate_signal(ctx: SignalContext) -> Decision:
    """Convenience function to evaluate signal."""
    return get_decision_engine().evaluate(ctx)
