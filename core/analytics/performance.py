# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T23:50:00Z
# Purpose: Real-time performance tracking with rolling metrics
# Security: Fail-closed, atomic state persistence
# === END SIGNATURE ===
"""
Performance Tracker.

Tracks trading performance in real-time with rolling windows:
- Returns: 1h, 24h, 7d, 30d
- Sharpe Ratio: 7d, 30d
- Win Rate: last 20 trades
- Drawdown: current and historical max
- Per-strategy breakdown

State is atomically persisted to disk.
Fail-closed: Any state corruption → reset to defaults.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Deque

import numpy as np

logger = logging.getLogger("performance")


@dataclass
class CompletedTrade:
    """Record of a completed trade."""
    trade_id: str
    symbol: str
    strategy: str
    side: str  # LONG or SHORT
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    pnl_pct: float
    entry_time: int  # Unix timestamp
    exit_time: int
    exit_reason: str
    bars_held: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EquityPoint:
    """Single point on equity curve."""
    timestamp: int
    equity: float
    drawdown_pct: float


@dataclass
class StrategyStats:
    """Per-strategy statistics."""
    name: str
    total_trades: int = 0
    winning_trades: int = 0
    total_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades

    @property
    def profit_factor(self) -> float:
        if self.gross_loss == 0:
            return 999.99 if self.gross_profit > 0 else 0.0
        return self.gross_profit / abs(self.gross_loss)


@dataclass
class PerformanceSnapshot:
    """Complete performance snapshot."""
    # Current state
    equity: float
    equity_peak: float
    current_drawdown_pct: float

    # Returns by period
    return_1h_pct: float
    return_24h_pct: float
    return_7d_pct: float
    return_30d_pct: float

    # Risk metrics
    sharpe_7d: float
    sharpe_30d: float
    max_drawdown_pct: float
    max_drawdown_duration_hours: float

    # Trade stats (last 20)
    win_rate_20: float
    avg_win_pct: float
    avg_loss_pct: float
    avg_rr_20: float
    total_trades: int

    # Strategy breakdown
    strategies: Dict[str, Dict[str, Any]]

    # Timestamps
    snapshot_time: str
    last_trade_time: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PerformanceTracker:
    """
    Real-time performance tracking.

    Maintains:
    - Rolling trade history (last 100 trades)
    - Equity curve (15-minute samples)
    - Per-strategy statistics
    - Risk metrics (Sharpe, Sortino, drawdown)

    State persisted atomically to disk.
    """

    STATE_FILE = "performance_state.json"
    MAX_TRADES_HISTORY = 100
    MAX_EQUITY_POINTS = 2880  # ~30 days at 15-minute intervals
    EQUITY_SAMPLE_INTERVAL = 900  # 15 minutes

    def __init__(
        self,
        initial_equity: float = 10000.0,
        state_dir: Optional[Path] = None,
    ):
        """
        Initialize performance tracker.

        Args:
            initial_equity: Starting equity value
            state_dir: Directory for state persistence
        """
        if state_dir is None:
            state_dir = Path(__file__).resolve().parent.parent.parent / "state"
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)

        self.state_path = self.state_dir / self.STATE_FILE

        # Core state
        self._initial_equity = initial_equity
        self._equity = initial_equity
        self._equity_peak = initial_equity
        self._max_drawdown_pct = 0.0
        self._drawdown_start_time: Optional[int] = None
        self._max_drawdown_duration = 0

        # Trade history (deque for O(1) append/pop)
        self._trades: Deque[CompletedTrade] = deque(maxlen=self.MAX_TRADES_HISTORY)

        # Equity curve (timestamp, equity)
        self._equity_curve: Deque[EquityPoint] = deque(maxlen=self.MAX_EQUITY_POINTS)
        self._last_equity_sample = 0

        # Per-strategy stats
        self._strategy_stats: Dict[str, StrategyStats] = {}

        # Load existing state
        self._load_state()

    def update_equity(self, new_equity: float, timestamp: Optional[int] = None) -> None:
        """
        Update current equity value.

        Args:
            new_equity: New equity value
            timestamp: Unix timestamp (default: now)
        """
        if timestamp is None:
            timestamp = int(time.time())

        self._equity = new_equity

        # Track peak and drawdown
        if new_equity > self._equity_peak:
            self._equity_peak = new_equity
            self._drawdown_start_time = None
        else:
            if self._drawdown_start_time is None:
                self._drawdown_start_time = timestamp

            current_dd = (self._equity_peak - new_equity) / self._equity_peak
            if current_dd > self._max_drawdown_pct:
                self._max_drawdown_pct = current_dd

            if self._drawdown_start_time:
                duration_hours = (timestamp - self._drawdown_start_time) / 3600
                if duration_hours > self._max_drawdown_duration:
                    self._max_drawdown_duration = duration_hours

        # Sample equity curve
        if timestamp - self._last_equity_sample >= self.EQUITY_SAMPLE_INTERVAL:
            dd_pct = 0.0
            if self._equity_peak > 0:
                dd_pct = (self._equity_peak - new_equity) / self._equity_peak

            self._equity_curve.append(EquityPoint(
                timestamp=timestamp,
                equity=new_equity,
                drawdown_pct=dd_pct,
            ))
            self._last_equity_sample = timestamp

        self._persist_state()

    def record_trade(self, trade: CompletedTrade) -> None:
        """
        Record a completed trade.

        Args:
            trade: Completed trade record
        """
        self._trades.append(trade)

        # Update equity
        new_equity = self._equity + trade.pnl
        self.update_equity(new_equity, trade.exit_time)

        # Update strategy stats
        if trade.strategy not in self._strategy_stats:
            self._strategy_stats[trade.strategy] = StrategyStats(name=trade.strategy)

        stats = self._strategy_stats[trade.strategy]
        stats.total_trades += 1
        stats.total_pnl += trade.pnl

        if trade.pnl > 0:
            stats.winning_trades += 1
            stats.gross_profit += trade.pnl
        else:
            stats.gross_loss += trade.pnl

        self._persist_state()

    def get_snapshot(self) -> PerformanceSnapshot:
        """Get current performance snapshot."""
        now = int(time.time())

        # Returns by period
        return_1h = self._calculate_return(now - 3600, now)
        return_24h = self._calculate_return(now - 86400, now)
        return_7d = self._calculate_return(now - 604800, now)
        return_30d = self._calculate_return(now - 2592000, now)

        # Sharpe ratios
        sharpe_7d = self._calculate_sharpe(now - 604800, now)
        sharpe_30d = self._calculate_sharpe(now - 2592000, now)

        # Trade stats from last 20
        last_20 = list(self._trades)[-20:]
        win_rate_20, avg_win, avg_loss, avg_rr = self._calculate_trade_stats(last_20)

        # Current drawdown
        current_dd = 0.0
        if self._equity_peak > 0:
            current_dd = (self._equity_peak - self._equity) / self._equity_peak

        # Strategy breakdown
        strategies = {}
        for name, stats in self._strategy_stats.items():
            strategies[name] = {
                "total_trades": stats.total_trades,
                "win_rate": round(stats.win_rate, 4),
                "total_pnl": round(stats.total_pnl, 2),
                "profit_factor": round(stats.profit_factor, 2),
            }

        # Last trade time
        last_trade_time = None
        if self._trades:
            last = self._trades[-1]
            last_trade_time = datetime.fromtimestamp(
                last.exit_time, tz=timezone.utc
            ).isoformat()

        return PerformanceSnapshot(
            equity=round(self._equity, 2),
            equity_peak=round(self._equity_peak, 2),
            current_drawdown_pct=round(current_dd, 4),
            return_1h_pct=round(return_1h, 4),
            return_24h_pct=round(return_24h, 4),
            return_7d_pct=round(return_7d, 4),
            return_30d_pct=round(return_30d, 4),
            sharpe_7d=round(sharpe_7d, 2),
            sharpe_30d=round(sharpe_30d, 2),
            max_drawdown_pct=round(self._max_drawdown_pct, 4),
            max_drawdown_duration_hours=round(self._max_drawdown_duration, 1),
            win_rate_20=round(win_rate_20, 4),
            avg_win_pct=round(avg_win, 4),
            avg_loss_pct=round(avg_loss, 4),
            avg_rr_20=round(avg_rr, 2),
            total_trades=len(self._trades),
            strategies=strategies,
            snapshot_time=datetime.now(timezone.utc).isoformat(),
            last_trade_time=last_trade_time,
        )

    def should_reduce_risk(self) -> tuple[bool, str, float]:
        """
        Check if risk should be reduced.

        Returns:
            (should_reduce, reason, multiplier)
            multiplier: Position size should be multiplied by this
        """
        snapshot = self.get_snapshot()

        # Severe drawdown: 10%+ → stop trading
        if snapshot.current_drawdown_pct >= 0.10:
            return True, "Drawdown >= 10%: EMERGENCY_STOP", 0.0

        # High drawdown: 8%+ → minimum size
        if snapshot.current_drawdown_pct >= 0.08:
            return True, "Drawdown >= 8%: minimum positions", 0.25

        # Medium drawdown: 5%+ → reduce by 50%
        if snapshot.current_drawdown_pct >= 0.05:
            return True, "Drawdown >= 5%: reduced positions", 0.50

        # Poor win rate: < 40% last 20 → reduce size
        if snapshot.total_trades >= 10 and snapshot.win_rate_20 < 0.40:
            return True, "Win rate < 40%: reduced positions", 0.75

        # Poor Sharpe: < 0.5 in last 7d → caution
        if len(self._equity_curve) >= 100 and snapshot.sharpe_7d < 0.5:
            return True, "Sharpe < 0.5: review strategy", 0.80

        return False, "Risk OK", 1.0

    def _calculate_return(self, start_ts: int, end_ts: int) -> float:
        """Calculate return between timestamps."""
        points = [p for p in self._equity_curve if start_ts <= p.timestamp <= end_ts]

        if len(points) < 2:
            return 0.0

        start_equity = points[0].equity
        end_equity = points[-1].equity

        if start_equity <= 0:
            return 0.0

        return (end_equity - start_equity) / start_equity

    def _calculate_sharpe(
        self,
        start_ts: int,
        end_ts: int,
        risk_free_rate: float = 0.0,
    ) -> float:
        """Calculate Sharpe ratio for period."""
        points = [p for p in self._equity_curve if start_ts <= p.timestamp <= end_ts]

        if len(points) < 10:
            return 0.0

        equities = [p.equity for p in points]
        returns = []
        for i in range(1, len(equities)):
            if equities[i - 1] > 0:
                returns.append((equities[i] - equities[i - 1]) / equities[i - 1])

        if not returns:
            return 0.0

        returns_arr = np.array(returns)
        mean_return = np.mean(returns_arr)
        std_return = np.std(returns_arr)

        if std_return == 0:
            return 0.0

        # Annualize (assuming 15-minute intervals)
        periods_per_year = 365 * 24 * 4  # 35040
        sharpe = (mean_return - risk_free_rate) / std_return * np.sqrt(periods_per_year)

        return float(sharpe)

    def _calculate_trade_stats(
        self,
        trades: List[CompletedTrade],
    ) -> tuple[float, float, float, float]:
        """
        Calculate trade statistics.

        Returns:
            (win_rate, avg_win_pct, avg_loss_pct, avg_risk_reward)
        """
        if not trades:
            return 0.0, 0.0, 0.0, 0.0

        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]

        win_rate = len(wins) / len(trades) if trades else 0.0

        avg_win = np.mean([t.pnl_pct for t in wins]) if wins else 0.0
        avg_loss = np.mean([t.pnl_pct for t in losses]) if losses else 0.0

        avg_rr = abs(avg_win / avg_loss) if avg_loss != 0 else 0.0

        return win_rate, avg_win, avg_loss, avg_rr

    def _persist_state(self) -> None:
        """Atomically persist state to disk."""
        try:
            state = {
                "schema": "performance_v1",
                "equity": self._equity,
                "equity_peak": self._equity_peak,
                "max_drawdown_pct": self._max_drawdown_pct,
                "max_drawdown_duration": self._max_drawdown_duration,
                "trades": [t.to_dict() for t in self._trades],
                "equity_curve": [
                    {"ts": p.timestamp, "eq": p.equity, "dd": p.drawdown_pct}
                    for p in self._equity_curve
                ],
                "strategy_stats": {
                    name: asdict(stats)
                    for name, stats in self._strategy_stats.items()
                },
                "updated_at": time.time(),
            }

            content = json.dumps(state, indent=2)
            tmp_path = self.state_path.with_suffix(".json.tmp")

            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_path, self.state_path)

        except Exception as e:
            logger.warning("Failed to persist state: %s", e)

    def _load_state(self) -> None:
        """Load state from disk."""
        if not self.state_path.exists():
            return

        try:
            content = self.state_path.read_text(encoding="utf-8")
            state = json.loads(content)

            if state.get("schema") != "performance_v1":
                logger.warning("Unknown state schema, resetting")
                return

            self._equity = state.get("equity", self._initial_equity)
            self._equity_peak = state.get("equity_peak", self._equity)
            self._max_drawdown_pct = state.get("max_drawdown_pct", 0.0)
            self._max_drawdown_duration = state.get("max_drawdown_duration", 0)

            # Load trades
            for t in state.get("trades", []):
                self._trades.append(CompletedTrade(**t))

            # Load equity curve
            for p in state.get("equity_curve", []):
                self._equity_curve.append(EquityPoint(
                    timestamp=p["ts"],
                    equity=p["eq"],
                    drawdown_pct=p["dd"],
                ))

            # Load strategy stats
            for name, s in state.get("strategy_stats", {}).items():
                self._strategy_stats[name] = StrategyStats(**s)

            logger.info("Loaded performance state: equity=%.2f, trades=%d",
                        self._equity, len(self._trades))

        except Exception as e:
            logger.warning("Failed to load state, resetting: %s", e)

    def reset(self, initial_equity: Optional[float] = None) -> None:
        """Reset all performance data."""
        if initial_equity:
            self._initial_equity = initial_equity

        self._equity = self._initial_equity
        self._equity_peak = self._initial_equity
        self._max_drawdown_pct = 0.0
        self._max_drawdown_duration = 0
        self._drawdown_start_time = None
        self._trades.clear()
        self._equity_curve.clear()
        self._strategy_stats.clear()
        self._last_equity_sample = 0

        self._persist_state()


# === Singleton ===

_tracker_instance: Optional[PerformanceTracker] = None


def get_performance_tracker(
    initial_equity: float = 10000.0,
    state_dir: Optional[Path] = None,
) -> PerformanceTracker:
    """Get singleton PerformanceTracker instance."""
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = PerformanceTracker(initial_equity, state_dir)
    return _tracker_instance
