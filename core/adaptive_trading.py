# === AI SIGNATURE ===
# Module: core/adaptive_trading.py
# Created by: Claude (opus-4.5)
# Created at: 2026-02-04 20:00:00 UTC
# Purpose: Adaptive trading strategies for HOPE AI
# === END SIGNATURE ===
"""
Adaptive Trading Module

Implements 6 intelligent trading optimizations:
1. Adaptive Position Sizing - scale by confidence
2. Smart Symbol Rotation - hot symbols pool
3. Momentum Cascade Detection - market-wide pump detection
4. Auto-Trailing Activation - dynamic trailing stops
5. Loss Recovery Mode - reduce risk when losing
6. Time-Based Strategy Switching - time-aware strategies

Usage:
    from core.adaptive_trading import AdaptiveTrader
    trader = AdaptiveTrader(base_position_usdt=20.0)

    # Get adjusted parameters for a trade
    params = trader.get_trade_params(
        symbol="BTCUSDT",
        confidence=0.65,
        open_positions=["ETHUSDT", "XRPUSDT"],
        daily_pnl=-15.0,
    )
"""

import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from enum import Enum

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

class TradingStrategy(str, Enum):
    """Trading strategy modes."""
    SCALP_TIGHT = "SCALP_TIGHT"      # Asian session - tight targets
    MOMENTUM = "MOMENTUM"             # US session - follow trends
    STANDARD = "STANDARD"             # Default balanced mode
    RECOVERY = "RECOVERY"             # Loss recovery - conservative


@dataclass
class StrategyConfig:
    """Configuration for a trading strategy."""
    name: str
    target_pct: float           # Take profit %
    stop_pct: float             # Stop loss %
    trailing_activation: float  # When to activate trailing (% profit)
    trailing_delta: float       # Trail distance %
    min_confidence: float       # Minimum confidence to trade
    position_multiplier: float  # Position size multiplier


# Strategy configurations
STRATEGIES: Dict[TradingStrategy, StrategyConfig] = {
    TradingStrategy.SCALP_TIGHT: StrategyConfig(
        name="SCALP_TIGHT",
        target_pct=0.8,             # Smaller target
        stop_pct=0.4,               # Tighter stop
        trailing_activation=0.4,    # Activate early
        trailing_delta=0.2,         # Tight trail
        min_confidence=0.40,        # Higher bar
        position_multiplier=0.8,    # Smaller positions
    ),
    TradingStrategy.MOMENTUM: StrategyConfig(
        name="MOMENTUM",
        target_pct=2.0,             # Larger target
        stop_pct=0.8,               # Wider stop
        trailing_activation=0.8,    # Activate later
        trailing_delta=0.4,         # Wider trail
        min_confidence=0.35,        # Lower bar
        position_multiplier=1.2,    # Larger positions
    ),
    TradingStrategy.STANDARD: StrategyConfig(
        name="STANDARD",
        target_pct=1.5,             # Balanced target
        stop_pct=0.5,               # Balanced stop
        trailing_activation=0.5,    # Balanced activation
        trailing_delta=0.3,         # Balanced trail
        min_confidence=0.35,        # Standard bar
        position_multiplier=1.0,    # Standard size
    ),
    TradingStrategy.RECOVERY: StrategyConfig(
        name="RECOVERY",
        target_pct=1.0,             # Conservative target
        stop_pct=0.3,               # Tight stop
        trailing_activation=0.3,    # Early activation
        trailing_delta=0.15,        # Tight trail
        min_confidence=0.50,        # High bar
        position_multiplier=0.5,    # Half positions
    ),
}


# ═══════════════════════════════════════════════════════════════════════════════
# HOT SYMBOLS POOL
# ═══════════════════════════════════════════════════════════════════════════════

# Top 20 liquid symbols by volume (updated periodically)
DEFAULT_HOT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT", "MATICUSDT",
    "SHIBUSDT", "LTCUSDT", "UNIUSDT", "ATOMUSDT", "NEARUSDT",
    "ARBUSDT", "OPUSDT", "SUIUSDT", "PEPEUSDT", "BONKUSDT",
]


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE PARAMETERS OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AdaptiveTradeParams:
    """Output parameters for an adaptive trade."""
    position_size_usdt: float
    target_pct: float
    stop_pct: float
    trailing_activation_pct: float
    trailing_delta_pct: float
    strategy: TradingStrategy
    adjustments: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "position_size_usdt": round(self.position_size_usdt, 2),
            "target_pct": round(self.target_pct, 4),
            "stop_pct": round(self.stop_pct, 4),
            "trailing_activation_pct": round(self.trailing_activation_pct, 4),
            "trailing_delta_pct": round(self.trailing_delta_pct, 4),
            "strategy": self.strategy.value,
            "adjustments": self.adjustments,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# ADAPTIVE TRADER
# ═══════════════════════════════════════════════════════════════════════════════

class AdaptiveTrader:
    """
    Intelligent trading parameter optimizer.

    Dynamically adjusts position sizes, targets, and strategies based on:
    - Signal confidence
    - Current open positions
    - Daily P&L
    - Time of day
    - Market conditions (cascade detection)
    """

    def __init__(
        self,
        base_position_usdt: float = 20.0,
        min_position_usdt: float = 5.0,
        max_position_usdt: float = 100.0,
        hot_symbols: Optional[List[str]] = None,
        loss_recovery_threshold: float = -20.0,
        cascade_threshold: int = 3,
    ):
        """
        Initialize AdaptiveTrader.

        Args:
            base_position_usdt: Base position size in USDT
            min_position_usdt: Minimum position size
            max_position_usdt: Maximum position size
            hot_symbols: List of preferred symbols (default: top 20)
            loss_recovery_threshold: Daily PnL to trigger recovery mode
            cascade_threshold: Number of concurrent signals to detect cascade
        """
        self.base_position = base_position_usdt
        self.min_position = min_position_usdt
        self.max_position = max_position_usdt
        self.hot_symbols = hot_symbols or DEFAULT_HOT_SYMBOLS.copy()
        self.loss_recovery_threshold = loss_recovery_threshold
        self.cascade_threshold = cascade_threshold

        # State tracking
        self._recent_signals: List[Tuple[datetime, str]] = []
        self._cascade_cooldown_until: Optional[datetime] = None

        log.info(f"AdaptiveTrader initialized: base=${base_position_usdt}, "
                 f"recovery_threshold=${loss_recovery_threshold}")

    # ─────────────────────────────────────────────────────────────────────────
    # 1. ADAPTIVE POSITION SIZING
    # ─────────────────────────────────────────────────────────────────────────

    def calculate_position_size(
        self,
        confidence: float,
        strategy_multiplier: float = 1.0,
    ) -> float:
        """
        Calculate position size based on confidence.

        Formula: base_size * (confidence / 0.5) * strategy_multiplier

        Examples:
            - 35% confidence, base $20 → $14
            - 50% confidence, base $20 → $20
            - 70% confidence, base $20 → $28

        Args:
            confidence: Signal confidence (0.0-1.0)
            strategy_multiplier: Additional multiplier from strategy

        Returns:
            Position size in USDT
        """
        # Scale linearly with confidence, normalized to 0.5
        confidence_factor = confidence / 0.5

        # Apply strategy multiplier
        raw_size = self.base_position * confidence_factor * strategy_multiplier

        # Clamp to min/max
        final_size = max(self.min_position, min(self.max_position, raw_size))

        return round(final_size, 2)

    # ─────────────────────────────────────────────────────────────────────────
    # 2. SMART SYMBOL ROTATION
    # ─────────────────────────────────────────────────────────────────────────

    def get_available_symbols(
        self,
        open_positions: Set[str],
        blocked_symbols: Optional[Set[str]] = None,
    ) -> List[str]:
        """
        Get available symbols from hot pool, excluding open positions.

        Args:
            open_positions: Set of symbols with open positions
            blocked_symbols: Additional symbols to exclude

        Returns:
            List of available symbols in priority order
        """
        blocked = open_positions.copy()
        if blocked_symbols:
            blocked.update(blocked_symbols)

        available = [s for s in self.hot_symbols if s not in blocked]

        log.debug(f"Symbol rotation: {len(available)}/{len(self.hot_symbols)} available "
                  f"(blocked: {len(blocked)})")

        return available

    def is_symbol_preferred(self, symbol: str) -> bool:
        """Check if symbol is in hot pool."""
        return symbol in self.hot_symbols

    def update_hot_symbols(self, symbols: List[str]) -> None:
        """Update hot symbols list (e.g., from volume data)."""
        self.hot_symbols = symbols[:20]  # Keep top 20
        log.info(f"Hot symbols updated: {len(self.hot_symbols)} symbols")

    # ─────────────────────────────────────────────────────────────────────────
    # 3. MOMENTUM CASCADE DETECTION
    # ─────────────────────────────────────────────────────────────────────────

    def record_signal(self, symbol: str) -> None:
        """Record a signal for cascade detection."""
        now = datetime.now(timezone.utc)
        self._recent_signals.append((now, symbol))

        # Clean old signals (older than 60 seconds)
        cutoff = now.timestamp() - 60
        self._recent_signals = [
            (ts, sym) for ts, sym in self._recent_signals
            if ts.timestamp() > cutoff
        ]

    def detect_cascade(self) -> Tuple[bool, int]:
        """
        Detect if we're in a momentum cascade (market-wide pump).

        Returns:
            (is_cascade, signal_count) - True if >= cascade_threshold signals in 60s
        """
        now = datetime.now(timezone.utc)

        # Check cooldown
        if self._cascade_cooldown_until and now < self._cascade_cooldown_until:
            return True, len(self._recent_signals)

        # Count unique symbols in recent signals
        unique_symbols = set(sym for _, sym in self._recent_signals)
        signal_count = len(unique_symbols)

        is_cascade = signal_count >= self.cascade_threshold

        if is_cascade:
            # Set cooldown for 5 minutes
            from datetime import timedelta
            self._cascade_cooldown_until = now + timedelta(minutes=5)
            log.warning(f"🔥 CASCADE DETECTED: {signal_count} symbols signaling! "
                        f"Cooldown until {self._cascade_cooldown_until}")

        return is_cascade, signal_count

    # ─────────────────────────────────────────────────────────────────────────
    # 4. AUTO-TRAILING ACTIVATION
    # ─────────────────────────────────────────────────────────────────────────

    def calculate_trailing_params(
        self,
        target_pct: float,
        strategy: TradingStrategy,
    ) -> Tuple[float, float]:
        """
        Calculate dynamic trailing stop parameters.

        Trailing activates at 30% of target, with delta proportional to target.

        Args:
            target_pct: Take profit target percentage
            strategy: Current trading strategy

        Returns:
            (trailing_activation_pct, trailing_delta_pct)
        """
        config = STRATEGIES[strategy]

        # Dynamic activation: 30% of target, but minimum from strategy config
        activation = max(config.trailing_activation, target_pct * 0.3)

        # Dynamic delta: 15-20% of target, but minimum from strategy config
        delta = max(config.trailing_delta, target_pct * 0.15)

        return round(activation, 4), round(delta, 4)

    # ─────────────────────────────────────────────────────────────────────────
    # 5. LOSS RECOVERY MODE
    # ─────────────────────────────────────────────────────────────────────────

    def should_enter_recovery(self, daily_pnl: float) -> bool:
        """Check if we should enter loss recovery mode."""
        return daily_pnl <= self.loss_recovery_threshold

    def get_recovery_adjustments(self, daily_pnl: float) -> Dict[str, float]:
        """
        Calculate recovery mode adjustments.

        As losses deepen, become more conservative:
        - At threshold (-$20): 50% position size, +10% confidence bar
        - At 2x threshold (-$40): 25% position size, +20% confidence bar

        Args:
            daily_pnl: Daily P&L in USDT (negative = loss)

        Returns:
            Dict with adjustment factors
        """
        if daily_pnl >= self.loss_recovery_threshold:
            return {"position_multiplier": 1.0, "confidence_boost": 0.0}

        # How deep in recovery are we?
        depth = abs(daily_pnl / self.loss_recovery_threshold)  # 1.0 at threshold, 2.0 at 2x

        # Position reduction: 50% at threshold, more as losses deepen
        position_mult = max(0.25, 1.0 - (depth * 0.5))

        # Confidence boost: +10% at threshold, more as losses deepen
        conf_boost = min(0.20, depth * 0.10)

        return {
            "position_multiplier": round(position_mult, 2),
            "confidence_boost": round(conf_boost, 2),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 6. TIME-BASED STRATEGY SWITCHING
    # ─────────────────────────────────────────────────────────────────────────

    def get_time_based_strategy(self) -> TradingStrategy:
        """
        Determine trading strategy based on time of day (UTC).

        Time windows:
        - 00:00-04:00 UTC: Asian low volume → SCALP_TIGHT
        - 04:00-08:00 UTC: Asian/Europe overlap → STANDARD
        - 08:00-13:00 UTC: Europe session → STANDARD
        - 13:00-17:00 UTC: US session → MOMENTUM
        - 17:00-21:00 UTC: US session continued → MOMENTUM
        - 21:00-00:00 UTC: Low volume → SCALP_TIGHT

        Returns:
            Appropriate TradingStrategy for current time
        """
        hour = datetime.now(timezone.utc).hour

        if hour in range(0, 4):      # 00:00-04:00 Asian low volume
            return TradingStrategy.SCALP_TIGHT
        elif hour in range(4, 8):    # 04:00-08:00 Asia/Europe overlap
            return TradingStrategy.STANDARD
        elif hour in range(8, 13):   # 08:00-13:00 Europe session
            return TradingStrategy.STANDARD
        elif hour in range(13, 21):  # 13:00-21:00 US session
            return TradingStrategy.MOMENTUM
        else:                        # 21:00-00:00 Low volume
            return TradingStrategy.SCALP_TIGHT

    # ─────────────────────────────────────────────────────────────────────────
    # MAIN INTERFACE
    # ─────────────────────────────────────────────────────────────────────────

    def get_trade_params(
        self,
        symbol: str,
        confidence: float,
        open_positions: Optional[Set[str]] = None,
        daily_pnl: float = 0.0,
        force_strategy: Optional[TradingStrategy] = None,
    ) -> AdaptiveTradeParams:
        """
        Get optimized trade parameters for a signal.

        Combines all 6 adaptive strategies to produce optimal parameters.

        Args:
            symbol: Trading symbol
            confidence: Signal confidence (0.0-1.0)
            open_positions: Set of symbols with open positions
            daily_pnl: Today's P&L in USDT
            force_strategy: Override automatic strategy selection

        Returns:
            AdaptiveTradeParams with optimized values
        """
        adjustments = []
        open_positions = open_positions or set()

        # 1. Record signal for cascade detection
        self.record_signal(symbol)

        # 2. Detect cascade
        is_cascade, cascade_count = self.detect_cascade()

        # 3. Determine strategy
        if force_strategy:
            strategy = force_strategy
            adjustments.append(f"STRATEGY_FORCED:{strategy.value}")
        elif self.should_enter_recovery(daily_pnl):
            strategy = TradingStrategy.RECOVERY
            adjustments.append(f"RECOVERY_MODE:pnl=${daily_pnl:.2f}")
        elif is_cascade:
            strategy = TradingStrategy.SCALP_TIGHT  # Conservative during cascade
            adjustments.append(f"CASCADE_DETECTED:{cascade_count}_signals")
        else:
            strategy = self.get_time_based_strategy()
            adjustments.append(f"TIME_STRATEGY:{strategy.value}")

        config = STRATEGIES[strategy]

        # 4. Get recovery adjustments
        recovery = self.get_recovery_adjustments(daily_pnl)
        if recovery["position_multiplier"] < 1.0:
            adjustments.append(f"RECOVERY_REDUCTION:{recovery['position_multiplier']:.0%}")

        # 5. Adjust confidence threshold
        effective_confidence = confidence
        if recovery["confidence_boost"] > 0:
            effective_confidence = confidence - recovery["confidence_boost"]
            adjustments.append(f"CONF_BOOST:+{recovery['confidence_boost']:.0%}")
        if is_cascade:
            effective_confidence -= 0.10  # Additional penalty during cascade
            adjustments.append("CASCADE_CONF_PENALTY:-10%")

        # 6. Check if symbol is in hot pool
        if not self.is_symbol_preferred(symbol):
            effective_confidence -= 0.05  # Small penalty for non-hot symbols
            adjustments.append("NON_HOT_SYMBOL:-5%")

        # 7. Calculate position size
        position_size = self.calculate_position_size(
            confidence=effective_confidence,
            strategy_multiplier=config.position_multiplier * recovery["position_multiplier"],
        )
        adjustments.append(f"POSITION:${position_size:.2f}")

        # 8. Calculate trailing params
        trailing_activation, trailing_delta = self.calculate_trailing_params(
            target_pct=config.target_pct,
            strategy=strategy,
        )

        # 9. Build result
        result = AdaptiveTradeParams(
            position_size_usdt=position_size,
            target_pct=config.target_pct,
            stop_pct=config.stop_pct,
            trailing_activation_pct=trailing_activation,
            trailing_delta_pct=trailing_delta,
            strategy=strategy,
            adjustments=adjustments,
        )

        log.info(f"[ADAPTIVE] {symbol} @ {confidence:.0%} → "
                 f"${position_size:.2f} | {strategy.value} | "
                 f"T:{config.target_pct}%/S:{config.stop_pct}% | "
                 f"Adjustments: {', '.join(adjustments[:3])}")

        return result

    def should_trade(
        self,
        symbol: str,
        confidence: float,
        open_positions: Optional[Set[str]] = None,
        daily_pnl: float = 0.0,
    ) -> Tuple[bool, str]:
        """
        Quick check if a trade should be taken.

        Returns:
            (should_trade, reason)
        """
        open_positions = open_positions or set()

        # Check if symbol already has position
        if symbol in open_positions:
            return False, f"ALREADY_OPEN:{symbol}"

        # Check cascade
        is_cascade, count = self.detect_cascade()

        # Determine effective strategy and min confidence
        if self.should_enter_recovery(daily_pnl):
            min_conf = STRATEGIES[TradingStrategy.RECOVERY].min_confidence
        elif is_cascade:
            min_conf = STRATEGIES[TradingStrategy.SCALP_TIGHT].min_confidence + 0.10
        else:
            strategy = self.get_time_based_strategy()
            min_conf = STRATEGIES[strategy].min_confidence

        # Non-hot symbol penalty
        if not self.is_symbol_preferred(symbol):
            min_conf += 0.05

        if confidence < min_conf:
            return False, f"LOW_CONF:{confidence:.0%}<{min_conf:.0%}"

        return True, "OK"


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLETON INSTANCE
# ═══════════════════════════════════════════════════════════════════════════════

_adaptive_trader: Optional[AdaptiveTrader] = None


def get_adaptive_trader() -> AdaptiveTrader:
    """Get or create singleton AdaptiveTrader instance."""
    global _adaptive_trader
    if _adaptive_trader is None:
        _adaptive_trader = AdaptiveTrader()
    return _adaptive_trader


def init_adaptive_trader(**kwargs) -> AdaptiveTrader:
    """Initialize AdaptiveTrader with custom parameters."""
    global _adaptive_trader
    _adaptive_trader = AdaptiveTrader(**kwargs)
    return _adaptive_trader


# ═══════════════════════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("=== AdaptiveTrader Tests ===\n")

    trader = AdaptiveTrader(base_position_usdt=20.0)

    # Test 1: Position sizing
    print("1. Position Sizing:")
    for conf in [0.35, 0.50, 0.65, 0.80]:
        size = trader.calculate_position_size(conf)
        print(f"   {conf:.0%} confidence -> ${size:.2f}")

    # Test 2: Time-based strategy
    print(f"\n2. Current Strategy: {trader.get_time_based_strategy().value}")

    # Test 3: Recovery mode
    print("\n3. Recovery Mode:")
    for pnl in [0, -10, -20, -40]:
        adj = trader.get_recovery_adjustments(pnl)
        print(f"   PnL ${pnl}: position={adj['position_multiplier']:.0%}, "
              f"conf_boost=+{adj['confidence_boost']:.0%}")

    # Test 4: Full trade params
    print("\n4. Full Trade Params:")
    params = trader.get_trade_params(
        symbol="BTCUSDT",
        confidence=0.55,
        open_positions={"ETHUSDT"},
        daily_pnl=-15.0,
    )
    print(f"   {params.to_dict()}")

    # Test 5: Cascade detection
    print("\n5. Cascade Detection:")
    for sym in ["BTCUSDT", "ETHUSDT", "XRPUSDT", "DOGEUSDT"]:
        trader.record_signal(sym)
        is_cascade, count = trader.detect_cascade()
        print(f"   After {sym}: cascade={is_cascade}, count={count}")

    print("\n=== All Tests PASS ===")
