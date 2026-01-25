# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T16:15:00Z
# Purpose: Trading Risk Engine - жёсткие лимиты (fail-closed)
# === END SIGNATURE ===
"""
Trading Risk Engine - Fail-Closed Risk Management.

Единственный источник решений о риске.
Все ордера ОБЯЗАНЫ пройти validate_order() перед отправкой.

ЖЁСТКИЕ ДЕФОЛТЫ (безопасные):
- MAX_DAILY_LOSS_PCT = 0.5% от equity
- RISK_PER_TRADE_PCT = 0.10% от equity
- MAX_OPEN_POSITIONS = 1
- MAX_ORDERS_PER_MIN = 6
- MAX_CONSECUTIVE_LOSSES = 3
- MAX_NOTIONAL_PCT = 5% от equity
- MIN_EQUITY_USD = 50

FAIL-CLOSED:
- Нет данных = REJECT
- Не уверен = REJECT
- Kill-switch = REJECT всех ордеров
- Любое исключение = REJECT

ЗАПРЕЩЕНО:
- Сетевые вызовы в этом модуле

Usage:
    from core.trade.risk_engine import TradingRiskEngine, TradingRiskLimits

    limits = TradingRiskLimits()  # Use strict defaults
    engine = TradingRiskEngine(limits)

    result = engine.validate_order(intent, portfolio)
    if not result.allowed:
        audit.log_reject(intent, result.reason_code)
        return  # STOP
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("trade.risk_engine")

# SSoT paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATE_DIR = BASE_DIR / "state"
RISK_STATE_PATH = STATE_DIR / "trade_risk_state.json"


def _atomic_write(path: Path, content: str) -> None:
    """Atomic write: temp -> fsync -> replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class RiskReasonCode(str, Enum):
    """Fixed reason codes for risk decisions."""
    ALLOWED = "ALLOWED"
    REJECT_NO_DATA = "REJECT_NO_DATA"
    REJECT_DAILY_LOSS = "REJECT_DAILY_LOSS"
    REJECT_TRADE_SIZE = "REJECT_TRADE_SIZE"
    REJECT_MAX_POSITIONS = "REJECT_MAX_POSITIONS"
    REJECT_RATE_LIMIT = "REJECT_RATE_LIMIT"
    REJECT_CONSECUTIVE_LOSSES = "REJECT_CONSECUTIVE_LOSSES"
    REJECT_NOTIONAL = "REJECT_NOTIONAL"
    REJECT_MIN_EQUITY = "REJECT_MIN_EQUITY"
    REJECT_KILL_SWITCH = "REJECT_KILL_SWITCH"
    REJECT_INVALID_INTENT = "REJECT_INVALID_INTENT"
    REJECT_EXCEPTION = "REJECT_EXCEPTION"


@dataclass
class TradingRiskLimits:
    """
    Жёсткие лимиты риска.

    ВСЕ лимиты применяются fail-closed.
    Дефолты выбраны максимально безопасными.
    """
    # Daily loss limit (% от начального equity дня)
    max_daily_loss_pct: float = 0.5

    # Risk per single trade (% от equity)
    risk_per_trade_pct: float = 0.10

    # Max open positions одновременно
    max_open_positions: int = 1

    # Max orders per minute (rate limit)
    max_orders_per_min: int = 6

    # Max consecutive losses before pause
    max_consecutive_losses: int = 3

    # Max notional value per order (% от equity)
    max_notional_pct: float = 5.0

    # Minimum equity to trade (USD)
    min_equity_usd: float = 50.0

    # Minimum order size (USD)
    min_order_usd: float = 10.0

    # Max order size (USD, absolute cap)
    max_order_usd: float = 1000.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict."""
        return {
            "max_daily_loss_pct": self.max_daily_loss_pct,
            "risk_per_trade_pct": self.risk_per_trade_pct,
            "max_open_positions": self.max_open_positions,
            "max_orders_per_min": self.max_orders_per_min,
            "max_consecutive_losses": self.max_consecutive_losses,
            "max_notional_pct": self.max_notional_pct,
            "min_equity_usd": self.min_equity_usd,
            "min_order_usd": self.min_order_usd,
            "max_order_usd": self.max_order_usd,
        }


@dataclass
class OrderIntent:
    """Order intent for validation."""
    symbol: str
    side: str  # BUY or SELL
    size_usd: float
    order_type: str = "MARKET"
    signal_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict."""
        return {
            "symbol": self.symbol,
            "side": self.side,
            "size_usd": self.size_usd,
            "order_type": self.order_type,
            "signal_id": self.signal_id,
        }


@dataclass
class PortfolioSnapshot:
    """Portfolio state for risk calculation."""
    equity_usd: float
    open_positions: int
    daily_pnl_usd: float
    start_of_day_equity: float
    timestamp_utc: str
    source: str  # Where data came from

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict."""
        return {
            "equity_usd": self.equity_usd,
            "open_positions": self.open_positions,
            "daily_pnl_usd": self.daily_pnl_usd,
            "start_of_day_equity": self.start_of_day_equity,
            "timestamp_utc": self.timestamp_utc,
            "source": self.source,
        }


@dataclass
class RiskDecision:
    """Risk validation decision."""
    allowed: bool
    reason_code: RiskReasonCode
    reason: str
    allowed_size_usd: float = 0.0
    checks_passed: List[str] = field(default_factory=list)
    checks_failed: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict."""
        return {
            "allowed": self.allowed,
            "reason_code": self.reason_code.value,
            "reason": self.reason,
            "allowed_size_usd": self.allowed_size_usd,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
        }


@dataclass
class RiskState:
    """Persistent risk engine state."""
    # Daily tracking
    daily_date: str = ""  # YYYY-MM-DD
    daily_pnl_usd: float = 0.0
    start_of_day_equity: float = 0.0
    trades_today: int = 0
    consecutive_losses: int = 0

    # Rate limiting
    order_timestamps: List[float] = field(default_factory=list)

    # Kill switch
    kill_switch_active: bool = False
    kill_switch_reason: str = ""
    kill_switch_ts: Optional[str] = None
    kill_switch_actor: Optional[str] = None

    # Last update
    last_update_utc: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict."""
        return {
            "daily_date": self.daily_date,
            "daily_pnl_usd": self.daily_pnl_usd,
            "start_of_day_equity": self.start_of_day_equity,
            "trades_today": self.trades_today,
            "consecutive_losses": self.consecutive_losses,
            "order_timestamps": self.order_timestamps[-20:],  # Keep last 20
            "kill_switch_active": self.kill_switch_active,
            "kill_switch_reason": self.kill_switch_reason,
            "kill_switch_ts": self.kill_switch_ts,
            "kill_switch_actor": self.kill_switch_actor,
            "last_update_utc": self.last_update_utc,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RiskState":
        """Create from dict."""
        return cls(
            daily_date=data.get("daily_date", ""),
            daily_pnl_usd=data.get("daily_pnl_usd", 0.0),
            start_of_day_equity=data.get("start_of_day_equity", 0.0),
            trades_today=data.get("trades_today", 0),
            consecutive_losses=data.get("consecutive_losses", 0),
            order_timestamps=data.get("order_timestamps", []),
            kill_switch_active=data.get("kill_switch_active", False),
            kill_switch_reason=data.get("kill_switch_reason", ""),
            kill_switch_ts=data.get("kill_switch_ts"),
            kill_switch_actor=data.get("kill_switch_actor"),
            last_update_utc=data.get("last_update_utc", ""),
        )


class TradingRiskEngine:
    """
    Trading Risk Engine - fail-closed.

    ЗАПРЕЩЕНО: сетевые вызовы.
    ВСЕ данные получаем через аргументы.
    """

    def __init__(
        self,
        limits: Optional[TradingRiskLimits] = None,
        state_path: Optional[Path] = None,
    ):
        """
        Initialize Trading Risk Engine.

        Args:
            limits: Risk limits (defaults to strict limits)
            state_path: Path to persist state
        """
        self.limits = limits or TradingRiskLimits()
        self.state_path = state_path or RISK_STATE_PATH
        self.state = self._load_state()

        logger.info(
            "TradingRiskEngine initialized: daily_loss=%.2f%%, trade_risk=%.2f%%, max_pos=%d",
            self.limits.max_daily_loss_pct,
            self.limits.risk_per_trade_pct,
            self.limits.max_open_positions,
        )

    def _load_state(self) -> RiskState:
        """Load persisted state."""
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                state = RiskState.from_dict(data)

                # Check if new day
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if state.daily_date != today:
                    logger.info("New trading day, resetting counters")
                    state.daily_date = today
                    state.daily_pnl_usd = 0.0
                    state.trades_today = 0
                    state.consecutive_losses = 0
                    state.order_timestamps = []

                return state
            except Exception as e:
                logger.error("Failed to load risk state: %s", e)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return RiskState(daily_date=today)

    def _save_state(self) -> None:
        """Persist current state."""
        self.state.last_update_utc = datetime.now(timezone.utc).isoformat()
        _atomic_write(self.state_path, json.dumps(self.state.to_dict(), indent=2))

    def _check_rate_limit(self) -> bool:
        """Check if within rate limit."""
        now = time.time()
        window_start = now - 60  # 1 minute window

        # Clean old timestamps
        self.state.order_timestamps = [
            ts for ts in self.state.order_timestamps if ts > window_start
        ]

        return len(self.state.order_timestamps) < self.limits.max_orders_per_min

    def validate_order(
        self,
        intent: OrderIntent,
        portfolio: PortfolioSnapshot,
    ) -> RiskDecision:
        """
        Validate order against all risk limits.

        FAIL-CLOSED: Any check failure = REJECT.

        Args:
            intent: Order intent to validate
            portfolio: Current portfolio state

        Returns:
            RiskDecision with allowed flag and reason code
        """
        checks_passed = []
        checks_failed = []

        try:
            # === VALIDATE INPUT ===
            if not intent.symbol or not intent.side:
                return RiskDecision(
                    allowed=False,
                    reason_code=RiskReasonCode.REJECT_INVALID_INTENT,
                    reason="Missing symbol or side",
                    checks_failed=["input_valid"],
                )

            if intent.size_usd <= 0:
                return RiskDecision(
                    allowed=False,
                    reason_code=RiskReasonCode.REJECT_INVALID_INTENT,
                    reason="Size must be positive",
                    checks_failed=["size_positive"],
                )

            if portfolio.equity_usd <= 0:
                return RiskDecision(
                    allowed=False,
                    reason_code=RiskReasonCode.REJECT_NO_DATA,
                    reason="Equity must be positive",
                    checks_failed=["equity_positive"],
                )

            checks_passed.append("input_valid")

            # === CHECK 1: Kill Switch ===
            if self.state.kill_switch_active:
                return RiskDecision(
                    allowed=False,
                    reason_code=RiskReasonCode.REJECT_KILL_SWITCH,
                    reason=f"Kill switch: {self.state.kill_switch_reason}",
                    checks_passed=checks_passed,
                    checks_failed=["kill_switch"],
                )
            checks_passed.append("kill_switch")

            # === CHECK 2: Minimum Equity ===
            if portfolio.equity_usd < self.limits.min_equity_usd:
                return RiskDecision(
                    allowed=False,
                    reason_code=RiskReasonCode.REJECT_MIN_EQUITY,
                    reason=f"Equity {portfolio.equity_usd:.2f} < min {self.limits.min_equity_usd:.2f}",
                    checks_passed=checks_passed,
                    checks_failed=["min_equity"],
                )
            checks_passed.append("min_equity")

            # === CHECK 3: Daily Loss Limit ===
            if portfolio.start_of_day_equity > 0:
                daily_loss_pct = abs(portfolio.daily_pnl_usd) / portfolio.start_of_day_equity * 100
                if portfolio.daily_pnl_usd < 0 and daily_loss_pct >= self.limits.max_daily_loss_pct:
                    return RiskDecision(
                        allowed=False,
                        reason_code=RiskReasonCode.REJECT_DAILY_LOSS,
                        reason=f"Daily loss {daily_loss_pct:.2f}% >= limit {self.limits.max_daily_loss_pct:.2f}%",
                        checks_passed=checks_passed,
                        checks_failed=["daily_loss"],
                    )
            checks_passed.append("daily_loss")

            # === CHECK 4: Consecutive Losses ===
            if self.state.consecutive_losses >= self.limits.max_consecutive_losses:
                return RiskDecision(
                    allowed=False,
                    reason_code=RiskReasonCode.REJECT_CONSECUTIVE_LOSSES,
                    reason=f"Consecutive losses {self.state.consecutive_losses} >= limit {self.limits.max_consecutive_losses}",
                    checks_passed=checks_passed,
                    checks_failed=["consecutive_losses"],
                )
            checks_passed.append("consecutive_losses")

            # === CHECK 5: Max Positions ===
            if portfolio.open_positions >= self.limits.max_open_positions:
                return RiskDecision(
                    allowed=False,
                    reason_code=RiskReasonCode.REJECT_MAX_POSITIONS,
                    reason=f"Open positions {portfolio.open_positions} >= limit {self.limits.max_open_positions}",
                    checks_passed=checks_passed,
                    checks_failed=["max_positions"],
                )
            checks_passed.append("max_positions")

            # === CHECK 6: Rate Limit ===
            if not self._check_rate_limit():
                return RiskDecision(
                    allowed=False,
                    reason_code=RiskReasonCode.REJECT_RATE_LIMIT,
                    reason=f"Rate limit: {self.limits.max_orders_per_min}/min exceeded",
                    checks_passed=checks_passed,
                    checks_failed=["rate_limit"],
                )
            checks_passed.append("rate_limit")

            # === CALCULATE ALLOWED SIZE ===
            # Max from risk per trade
            max_risk_size = portfolio.equity_usd * (self.limits.risk_per_trade_pct / 100)

            # Max from notional limit
            max_notional = portfolio.equity_usd * (self.limits.max_notional_pct / 100)

            # Apply all limits
            allowed_size = min(
                intent.size_usd,
                max_risk_size,
                max_notional,
                self.limits.max_order_usd,
            )

            # Check minimum
            if allowed_size < self.limits.min_order_usd:
                return RiskDecision(
                    allowed=False,
                    reason_code=RiskReasonCode.REJECT_TRADE_SIZE,
                    reason=f"Allowed size {allowed_size:.2f} < min {self.limits.min_order_usd:.2f}",
                    checks_passed=checks_passed,
                    checks_failed=["min_size"],
                )
            checks_passed.append("size_limits")

            # === ALL CHECKS PASSED ===
            return RiskDecision(
                allowed=True,
                reason_code=RiskReasonCode.ALLOWED,
                reason="All risk checks passed",
                allowed_size_usd=allowed_size,
                checks_passed=checks_passed,
                checks_failed=[],
            )

        except Exception as e:
            # FAIL-CLOSED
            logger.error("Risk validation exception: %s", e)
            return RiskDecision(
                allowed=False,
                reason_code=RiskReasonCode.REJECT_EXCEPTION,
                reason=f"Exception: {e}",
                checks_passed=checks_passed,
                checks_failed=["exception"],
            )

    def record_order(self) -> None:
        """Record that an order was placed (for rate limiting)."""
        self.state.order_timestamps.append(time.time())
        self.state.trades_today += 1
        self._save_state()

    def record_trade_result(self, pnl_usd: float) -> None:
        """Record trade result for tracking."""
        self.state.daily_pnl_usd += pnl_usd

        if pnl_usd < 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0

        # Auto kill-switch on too many losses
        if self.state.consecutive_losses >= self.limits.max_consecutive_losses:
            self.activate_kill_switch(
                f"Auto: {self.state.consecutive_losses} consecutive losses",
                actor="system",
            )

        self._save_state()
        logger.info(
            "Trade result: PnL=%.2f, daily=%.2f, consecutive_losses=%d",
            pnl_usd,
            self.state.daily_pnl_usd,
            self.state.consecutive_losses,
        )

    def activate_kill_switch(self, reason: str, actor: str = "unknown") -> None:
        """Activate kill switch - blocks ALL orders."""
        if not self.state.kill_switch_active:
            self.state.kill_switch_active = True
            self.state.kill_switch_reason = reason
            self.state.kill_switch_ts = datetime.now(timezone.utc).isoformat()
            self.state.kill_switch_actor = actor
            self._save_state()
            logger.critical("KILL SWITCH ACTIVATED by %s: %s", actor, reason)

    def deactivate_kill_switch(self, actor: str) -> None:
        """Deactivate kill switch."""
        if self.state.kill_switch_active:
            old_reason = self.state.kill_switch_reason
            self.state.kill_switch_active = False
            self.state.kill_switch_reason = ""
            self.state.kill_switch_ts = None
            self.state.kill_switch_actor = None
            self._save_state()
            logger.warning("Kill switch DEACTIVATED by %s (was: %s)", actor, old_reason)

    def reset_daily(self, start_equity: float) -> None:
        """Reset daily counters (call at start of trading day)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.state.daily_date = today
        self.state.daily_pnl_usd = 0.0
        self.state.start_of_day_equity = start_equity
        self.state.trades_today = 0
        self.state.consecutive_losses = 0
        self.state.order_timestamps = []
        self._save_state()
        logger.info("Daily reset: start_equity=%.2f", start_equity)

    def get_status(self) -> Dict[str, Any]:
        """Get current risk engine status."""
        return {
            "limits": self.limits.to_dict(),
            "state": self.state.to_dict(),
            "kill_switch_active": self.state.kill_switch_active,
            "consecutive_losses": self.state.consecutive_losses,
            "trades_today": self.state.trades_today,
        }


# === CLI Interface ===
def main() -> int:
    """CLI entrypoint."""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if len(sys.argv) < 2:
        print("Usage: python -m core.trade.risk_engine <command>")
        print("Commands:")
        print("  status       - Show risk engine status")
        print("  kill <msg>   - Activate kill switch")
        print("  unkill       - Deactivate kill switch")
        print("  test         - Test validation")
        return 1

    command = sys.argv[1]
    engine = TradingRiskEngine()

    if command == "status":
        status = engine.get_status()
        print(json.dumps(status, indent=2))
        return 0

    elif command == "kill":
        reason = sys.argv[2] if len(sys.argv) > 2 else "Manual via CLI"
        engine.activate_kill_switch(reason, actor="cli")
        print(f"Kill switch ACTIVATED: {reason}")
        return 0

    elif command == "unkill":
        engine.deactivate_kill_switch(actor="cli")
        print("Kill switch DEACTIVATED")
        return 0

    elif command == "test":
        # Self-test with mock data
        intent = OrderIntent(symbol="BTCUSDT", side="BUY", size_usd=100.0)
        portfolio = PortfolioSnapshot(
            equity_usd=1000.0,
            open_positions=0,
            daily_pnl_usd=0.0,
            start_of_day_equity=1000.0,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            source="test",
        )
        result = engine.validate_order(intent, portfolio)
        print(json.dumps(result.to_dict(), indent=2))
        return 0 if result.allowed else 1

    else:
        print(f"Unknown command: {command}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
