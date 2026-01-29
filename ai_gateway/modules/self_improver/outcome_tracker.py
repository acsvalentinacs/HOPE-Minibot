# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 10:00:00 UTC
# Purpose: Track signal outcomes for self-improving loop
# === END SIGNATURE ===
"""
Outcome Tracker - Monitors signal outcomes (WIN/LOSS) at multiple horizons.

Horizons:
- 1m: Ultra-short scalp
- 5m: Short scalp
- 15m: Medium term
- 60m: Position trade

MFE/MAE:
- MFE (Maximum Favorable Excursion): Best price in our favor
- MAE (Maximum Adverse Excursion): Worst price against us

All outcomes persisted to JSONL for training.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Horizons to track (in minutes)
HORIZONS = [1, 5, 15, 60]

# Target profit thresholds for WIN classification
WIN_THRESHOLDS = {
    1: 0.3,   # 0.3% for 1m
    5: 0.5,   # 0.5% for 5m
    15: 1.0,  # 1.0% for 15m
    60: 2.0,  # 2.0% for 60m
}


@dataclass
class TrackedSignal:
    """Signal being tracked for outcome."""
    signal_id: str
    symbol: str
    entry_price: float
    direction: str  # "Long" or "Short"
    entry_time: datetime
    signal_data: Dict[str, Any]

    # Price tracking
    prices: List[float] = field(default_factory=list)
    timestamps: List[float] = field(default_factory=list)

    # Computed outcomes
    outcomes: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    mfe: float = 0.0  # Maximum Favorable Excursion
    mae: float = 0.0  # Maximum Adverse Excursion

    # Status
    is_complete: bool = False


class OutcomeTracker:
    """
    Tracks trading signal outcomes.

    Usage:
        tracker = OutcomeTracker(state_dir=Path("state/ai/outcomes"))

        # Register signal for tracking
        tracker.register_signal(signal_data)

        # Update with price data
        tracker.update_prices(symbol, current_price)

        # Get outcomes for training
        outcomes = tracker.get_completed_outcomes()
    """

    def __init__(
        self,
        state_dir: Path = Path("state/ai/outcomes"),
        max_track_minutes: int = 120,  # Stop tracking after 2h
    ):
        self.state_dir = state_dir
        self.max_track_minutes = max_track_minutes

        # Active signals being tracked
        self._active: Dict[str, TrackedSignal] = {}

        # Completed signals
        self._completed: List[TrackedSignal] = []

        # Pending outcomes file
        self.pending_file = state_dir / "pending_signals.jsonl"
        self.outcomes_file = state_dir / "completed_outcomes.jsonl"

        # Create directories
        self.state_dir.mkdir(parents=True, exist_ok=True)

        # Load pending signals from disk
        self._load_pending()

        logger.info(f"OutcomeTracker initialized, {len(self._active)} pending signals")

    def _load_pending(self) -> None:
        """Load pending signals from disk."""
        if not self.pending_file.exists():
            return

        try:
            with open(self.pending_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        signal = TrackedSignal(
                            signal_id=data["signal_id"],
                            symbol=data["symbol"],
                            entry_price=data["entry_price"],
                            direction=data["direction"],
                            entry_time=datetime.fromisoformat(data["entry_time"]),
                            signal_data=data.get("signal_data", {}),
                            prices=data.get("prices", []),
                            timestamps=data.get("timestamps", []),
                        )
                        self._active[signal.signal_id] = signal
                    except (KeyError, json.JSONDecodeError) as e:
                        logger.warning(f"Failed to load pending signal: {e}")
        except Exception as e:
            logger.error(f"Failed to load pending file: {e}")

    def _save_pending(self) -> None:
        """Save pending signals to disk (atomic write)."""
        tmp_path = self.pending_file.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
                for signal in self._active.values():
                    data = {
                        "signal_id": signal.signal_id,
                        "symbol": signal.symbol,
                        "entry_price": signal.entry_price,
                        "direction": signal.direction,
                        "entry_time": signal.entry_time.isoformat(),
                        "signal_data": signal.signal_data,
                        "prices": signal.prices,
                        "timestamps": signal.timestamps,
                    }
                    f.write(json.dumps(data, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.pending_file)
        except Exception as e:
            logger.error(f"Failed to save pending: {e}")
            if tmp_path.exists():
                tmp_path.unlink()

    def _generate_signal_id(self, signal: Dict[str, Any]) -> str:
        """Generate unique signal ID from content."""
        content = json.dumps(signal, sort_keys=True, default=str, ensure_ascii=False)
        return "sig:" + hashlib.sha256(content.encode()).hexdigest()[:16]

    def register_signal(self, signal: Dict[str, Any]) -> str:
        """
        Register a signal for outcome tracking.

        Args:
            signal: Signal data dict with at minimum:
                - symbol: Trading pair
                - price: Entry price
                - direction: "Long" or "Short"

        Returns:
            signal_id for tracking
        """
        signal_id = self._generate_signal_id(signal)

        # Check if already tracking
        if signal_id in self._active:
            logger.debug(f"Signal {signal_id[:12]} already being tracked")
            return signal_id

        # Extract required fields
        symbol = signal.get("symbol", "UNKNOWN")
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"

        price = float(signal.get("price", 0))
        if price <= 0:
            logger.warning(f"Invalid price {price} for signal, skipping")
            return signal_id

        direction = signal.get("direction", "Long")

        # Create tracked signal
        tracked = TrackedSignal(
            signal_id=signal_id,
            symbol=symbol,
            entry_price=price,
            direction=direction,
            entry_time=datetime.utcnow(),
            signal_data=signal,
        )

        # Add entry price as first data point
        tracked.prices.append(price)
        tracked.timestamps.append(time.time())

        self._active[signal_id] = tracked
        self._save_pending()

        logger.info(f"Registered signal {signal_id[:12]} {symbol} @ {price}")
        return signal_id

    def update_prices(self, prices: Dict[str, float]) -> int:
        """
        Update prices for tracked symbols.

        Args:
            prices: Dict mapping symbol -> current_price

        Returns:
            Number of completed signals
        """
        now = time.time()
        completed_count = 0

        for signal in list(self._active.values()):
            symbol = signal.symbol

            if symbol not in prices:
                continue

            current_price = prices[symbol]
            signal.prices.append(current_price)
            signal.timestamps.append(now)

            # Calculate MFE/MAE
            self._update_excursions(signal)

            # Check if all horizons complete
            elapsed_minutes = (now - signal.timestamps[0]) / 60

            for horizon in HORIZONS:
                if horizon not in signal.outcomes and elapsed_minutes >= horizon:
                    self._compute_horizon_outcome(signal, horizon)

            # Mark complete if max time reached or all horizons done
            if elapsed_minutes >= self.max_track_minutes:
                self._finalize_signal(signal)
                completed_count += 1
            elif all(h in signal.outcomes for h in HORIZONS):
                self._finalize_signal(signal)
                completed_count += 1

        if completed_count > 0:
            self._save_pending()

        return completed_count

    def _update_excursions(self, signal: TrackedSignal) -> None:
        """Update MFE and MAE for signal."""
        if not signal.prices:
            return

        entry = signal.entry_price
        is_long = signal.direction == "Long"

        for price in signal.prices:
            pnl_pct = ((price - entry) / entry) * 100
            if not is_long:
                pnl_pct = -pnl_pct

            if pnl_pct > signal.mfe:
                signal.mfe = pnl_pct
            if pnl_pct < signal.mae:
                signal.mae = pnl_pct

    def _compute_horizon_outcome(self, signal: TrackedSignal, horizon: int) -> None:
        """Compute outcome for specific horizon."""
        if not signal.prices or len(signal.prices) < 2:
            return

        # Find price at horizon
        target_time = signal.timestamps[0] + (horizon * 60)
        horizon_price = None

        for i, ts in enumerate(signal.timestamps):
            if ts >= target_time:
                horizon_price = signal.prices[i]
                break

        if horizon_price is None:
            horizon_price = signal.prices[-1]  # Use latest

        # Calculate PnL
        entry = signal.entry_price
        pnl_pct = ((horizon_price - entry) / entry) * 100
        if signal.direction != "Long":
            pnl_pct = -pnl_pct

        # Determine WIN/LOSS
        threshold = WIN_THRESHOLDS.get(horizon, 0.5)
        is_win = pnl_pct >= threshold

        signal.outcomes[horizon] = {
            "horizon_minutes": horizon,
            "entry_price": entry,
            "exit_price": horizon_price,
            "pnl_pct": round(pnl_pct, 4),
            "threshold": threshold,
            "win": is_win,
        }

        logger.debug(f"Signal {signal.signal_id[:12]} {horizon}m: {pnl_pct:.2f}% ({'WIN' if is_win else 'LOSS'})")

    def _finalize_signal(self, signal: TrackedSignal) -> None:
        """Finalize signal tracking and persist outcome."""
        signal.is_complete = True

        # Remove from active
        if signal.signal_id in self._active:
            del self._active[signal.signal_id]

        # Add to completed
        self._completed.append(signal)

        # Persist to outcomes file
        outcome_data = {
            "signal_id": signal.signal_id,
            "symbol": signal.symbol,
            "direction": signal.direction,
            "entry_price": signal.entry_price,
            "entry_time": signal.entry_time.isoformat() + "Z",
            "mfe": round(signal.mfe, 4),
            "mae": round(signal.mae, 4),
            "outcomes": {
                f"{h}m": signal.outcomes.get(h, {})
                for h in HORIZONS
            },
            "signal_data": signal.signal_data,
            "completed_at": datetime.utcnow().isoformat() + "Z",
        }

        try:
            with open(self.outcomes_file, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(outcome_data, ensure_ascii=False) + "\n")
            logger.info(f"Finalized signal {signal.signal_id[:12]} MFE={signal.mfe:.2f}% MAE={signal.mae:.2f}%")
        except Exception as e:
            logger.error(f"Failed to persist outcome: {e}")

    def get_completed_outcomes(self, min_count: int = 0) -> List[Dict[str, Any]]:
        """
        Get completed outcomes for training.

        Args:
            min_count: Minimum required outcomes (0 = no minimum)

        Returns:
            List of outcome dicts
        """
        outcomes = []

        if not self.outcomes_file.exists():
            return outcomes

        try:
            with open(self.outcomes_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        outcomes.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.error(f"Failed to read outcomes: {e}")

        if min_count > 0 and len(outcomes) < min_count:
            logger.warning(f"Insufficient outcomes: {len(outcomes)} < {min_count}")
            return []

        return outcomes

    def get_stats(self) -> Dict[str, Any]:
        """Get tracking statistics."""
        outcomes = self.get_completed_outcomes()

        if not outcomes:
            return {
                "active_signals": len(self._active),
                "completed_signals": 0,
                "win_rate_5m": 0.0,
                "avg_mfe": 0.0,
                "avg_mae": 0.0,
            }

        # Calculate stats
        wins_5m = sum(1 for o in outcomes if o.get("outcomes", {}).get("5m", {}).get("win", False))
        mfes = [o.get("mfe", 0) for o in outcomes]
        maes = [o.get("mae", 0) for o in outcomes]

        return {
            "active_signals": len(self._active),
            "completed_signals": len(outcomes),
            "win_rate_5m": wins_5m / len(outcomes) if outcomes else 0.0,
            "avg_mfe": sum(mfes) / len(mfes) if mfes else 0.0,
            "avg_mae": sum(maes) / len(maes) if maes else 0.0,
        }

    @property
    def active_symbols(self) -> Set[str]:
        """Get set of symbols currently being tracked."""
        return {s.symbol for s in self._active.values()}
