"""
HOPE/NORE Signal Outcome Tracker v1.0

Tracks signal performance via sampled prices (MFE/MAE).
Works within Binance API allowlist (ticker/24hr only, no klines).

Design principles:
- Append-only journals: signals.jsonl, price_samples.jsonl, outcomes.jsonl
- sha256: prefix for self-documenting format
- Atomic writes: temp -> fsync -> replace
- Fail-closed: missing samples = outcome not computed, reason logged
- Cursor-based: tracks computed outcomes to avoid reprocessing

File format (each line):
    sha256:<hash>:<json>

Usage:
    from core.signal_outcomes import OutcomeTracker, TrackedSignal

    tracker = OutcomeTracker()

    # Record signal entry
    tracker.record_signal(TrackedSignal(
        signal_id="sha256:abc123",
        symbol="BTCUSDT",
        direction="long",
        entry_price=95000.0,
        entry_ts=time.time(),
    ))

    # Record price samples (every pipeline cycle)
    tracker.record_price_samples(time.time(), {"BTCUSDT": 95100.0, ...})

    # Compute outcomes for completed horizons
    outcomes = tracker.update_outcomes(time.time())
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

STATE_DIR = Path(r"C:\Users\kirillDev\Desktop\TradingBot\minibot\state")
STATE_DIR.mkdir(parents=True, exist_ok=True)

SIGNALS_JSONL = STATE_DIR / "tracked_signals.jsonl"
SAMPLES_JSONL = STATE_DIR / "price_samples.jsonl"
OUTCOMES_JSONL = STATE_DIR / "signal_outcomes.jsonl"
CURSOR_JSON = STATE_DIR / "outcomes_cursor.json"

DEFAULT_HORIZONS_SEC = (3600, 4 * 3600, 24 * 3600)  # 1h, 4h, 24h
MIN_SAMPLES_FOR_OUTCOME = 3


def _atomic_write_text(path: Path, text: str) -> None:
    """Write text atomically: temp -> fsync -> replace."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as e:
        logger.error("Atomic write failed for %s: %s", path, e)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _sha256_hex(payload: str) -> str:
    """Compute sha256 hex digest."""
    return sha256(payload.encode("utf-8")).hexdigest()


def _json_canonical(obj: object) -> str:
    """Canonical JSON: sorted keys, no spaces, ascii-safe."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _append_sha256_jsonl(path: Path, obj: dict) -> str:
    """
    Append object to JSONL file with sha256: prefix.

    Returns the hash (without prefix).
    """
    raw = _json_canonical(obj)
    hash_hex = _sha256_hex(raw)[:16]
    line = f"sha256:{hash_hex}:{raw}\n"

    try:
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        logger.error("Failed to append to %s: %s", path, e)
        raise

    return hash_hex


def _parse_sha256_jsonl_line(line: str) -> Optional[Tuple[str, dict]]:
    """
    Parse a sha256:hash:json line.

    Returns (hash, parsed_dict) or None if malformed.
    """
    line = line.rstrip("\n\r")
    if not line:
        return None

    parts = line.split(":", 2)
    if len(parts) != 3 or parts[0] != "sha256":
        return None

    hash_hex = parts[1]
    try:
        obj = json.loads(parts[2])
        return (hash_hex, obj)
    except json.JSONDecodeError:
        return None


@dataclass(frozen=True)
class TrackedSignal:
    """
    Signal entry record for outcome tracking.

    All fields are immutable after creation.
    """
    signal_id: str          # sha256:xxx from event_contract
    symbol: str             # BTCUSDT
    direction: str          # "long" | "short"
    entry_price: float      # price at signal generation
    entry_ts: float         # unix timestamp
    invalidation_price: Optional[float] = None  # price that invalidates signal


@dataclass(frozen=True)
class Outcome:
    """
    Computed outcome for a signal at a specific horizon.

    MFE/MAE are percentages relative to entry_price.
    """
    signal_id: str
    horizon_sec: int
    computed_ts: float
    mfe: Optional[float]    # Maximum Favorable Excursion (%)
    mae: Optional[float]    # Maximum Adverse Excursion (%)
    samples_used: int
    reason: str             # "ok" | "insufficient_samples" | "invalidated" | "pending"


@dataclass
class WeeklyStats:
    """Aggregated weekly statistics for reporting."""
    week_start: float
    week_end: float
    total_signals: int
    completed_outcomes: int
    avg_mfe_1h: Optional[float]
    avg_mae_1h: Optional[float]
    avg_mfe_4h: Optional[float]
    avg_mae_4h: Optional[float]
    avg_mfe_24h: Optional[float]
    avg_mae_24h: Optional[float]
    win_rate_1h: Optional[float]   # MFE > |MAE|
    win_rate_4h: Optional[float]
    win_rate_24h: Optional[float]
    invalidated_count: int
    by_signal_type: Dict[str, Dict[str, float]]


class OutcomeTracker:
    """
    Tracks signal outcomes using sampled prices.

    Since klines API is not in allowlist, we use sampled ticker prices
    collected at each pipeline cycle. This gives lower-bound MFE/MAE.
    """

    def __init__(
        self,
        signals_path: Path = SIGNALS_JSONL,
        samples_path: Path = SAMPLES_JSONL,
        outcomes_path: Path = OUTCOMES_JSONL,
        cursor_path: Path = CURSOR_JSON,
    ):
        self._signals_path = signals_path
        self._samples_path = samples_path
        self._outcomes_path = outcomes_path
        self._cursor_path = cursor_path
        self._cursor = self._load_cursor()

    def record_signal(self, signal: TrackedSignal) -> str:
        """
        Record signal entry for outcome tracking.

        Returns the hash of the recorded entry.
        """
        obj = {
            "kind": "signal_entry",
            "signal_id": signal.signal_id,
            "symbol": signal.symbol,
            "direction": signal.direction,
            "entry_price": signal.entry_price,
            "entry_ts": signal.entry_ts,
            "invalidation_price": signal.invalidation_price,
        }
        hash_hex = _append_sha256_jsonl(self._signals_path, obj)
        logger.info("Recorded signal %s for tracking", signal.signal_id)
        return hash_hex

    def record_price_samples(self, ts: float, prices: Dict[str, float]) -> str:
        """
        Record price samples for all symbols.

        Should be called every pipeline cycle.
        """
        if not prices:
            logger.warning("Empty prices dict, skipping sample")
            return ""

        obj = {
            "kind": "price_sample",
            "ts": ts,
            "prices": prices,
        }
        hash_hex = _append_sha256_jsonl(self._samples_path, obj)
        logger.debug("Recorded %d price samples at %s", len(prices), ts)
        return hash_hex

    def update_outcomes(
        self,
        now_ts: float,
        horizons_sec: Iterable[int] = DEFAULT_HORIZONS_SEC,
        max_samples_scan: int = 50000,
    ) -> List[Outcome]:
        """
        Compute outcomes for signals with completed horizons.

        Skips already-computed outcomes via cursor.
        Fail-closed: insufficient samples = outcome with reason, not silent skip.
        """
        signals = self._load_signals()
        samples = self._load_samples(max_lines=max_samples_scan)

        if not signals:
            logger.debug("No signals to process")
            return []

        computed: List[Outcome] = []

        for signal in signals:
            for horizon in horizons_sec:
                cursor_key = f"{signal.signal_id}:{horizon}"

                if cursor_key in self._cursor.get("done", {}):
                    continue

                outcome = self._compute_outcome(signal, horizon, now_ts, samples)

                if outcome.reason != "pending":
                    self._persist_outcome(outcome)
                    self._cursor.setdefault("done", {})[cursor_key] = outcome.computed_ts
                    computed.append(outcome)

        if computed:
            self._save_cursor()
            logger.info("Computed %d new outcomes", len(computed))

        return computed

    def _compute_outcome(
        self,
        signal: TrackedSignal,
        horizon_sec: int,
        now_ts: float,
        samples: List[Tuple[float, Dict[str, float]]],
    ) -> Outcome:
        """
        Compute MFE/MAE for a single signal at given horizon.

        Fail-closed: returns explicit reason if cannot compute.
        """
        end_ts = signal.entry_ts + horizon_sec

        if now_ts < end_ts:
            return Outcome(
                signal_id=signal.signal_id,
                horizon_sec=horizon_sec,
                computed_ts=now_ts,
                mfe=None,
                mae=None,
                samples_used=0,
                reason="pending",
            )

        window = [
            (ts, prices[signal.symbol])
            for (ts, prices) in samples
            if signal.entry_ts <= ts <= end_ts and signal.symbol in prices
        ]

        if len(window) < MIN_SAMPLES_FOR_OUTCOME:
            return Outcome(
                signal_id=signal.signal_id,
                horizon_sec=horizon_sec,
                computed_ts=now_ts,
                mfe=None,
                mae=None,
                samples_used=len(window),
                reason="insufficient_samples",
            )

        prices = [p for _, p in window]
        entry = signal.entry_price

        if signal.invalidation_price is not None:
            if signal.direction == "long":
                if any(p <= signal.invalidation_price for p in prices):
                    return Outcome(
                        signal_id=signal.signal_id,
                        horizon_sec=horizon_sec,
                        computed_ts=now_ts,
                        mfe=None,
                        mae=None,
                        samples_used=len(prices),
                        reason="invalidated",
                    )
            elif signal.direction == "short":
                if any(p >= signal.invalidation_price for p in prices):
                    return Outcome(
                        signal_id=signal.signal_id,
                        horizon_sec=horizon_sec,
                        computed_ts=now_ts,
                        mfe=None,
                        mae=None,
                        samples_used=len(prices),
                        reason="invalidated",
                    )

        p_max = max(prices)
        p_min = min(prices)

        if signal.direction == "long":
            mfe = (p_max - entry) / entry * 100
            mae = (p_min - entry) / entry * 100
        else:
            mfe = (entry - p_min) / entry * 100
            mae = (entry - p_max) / entry * 100

        return Outcome(
            signal_id=signal.signal_id,
            horizon_sec=horizon_sec,
            computed_ts=now_ts,
            mfe=round(mfe, 4),
            mae=round(mae, 4),
            samples_used=len(prices),
            reason="ok",
        )

    def _persist_outcome(self, outcome: Outcome) -> None:
        """Persist outcome to JSONL."""
        obj = {
            "kind": "signal_outcome",
            "signal_id": outcome.signal_id,
            "horizon_sec": outcome.horizon_sec,
            "computed_ts": outcome.computed_ts,
            "mfe": outcome.mfe,
            "mae": outcome.mae,
            "samples_used": outcome.samples_used,
            "reason": outcome.reason,
        }
        _append_sha256_jsonl(self._outcomes_path, obj)

    def _load_signals(self) -> List[TrackedSignal]:
        """Load all tracked signals from JSONL."""
        if not self._signals_path.exists():
            return []

        signals: List[TrackedSignal] = []

        try:
            with open(self._signals_path, "r", encoding="utf-8") as f:
                for line in f:
                    parsed = _parse_sha256_jsonl_line(line)
                    if parsed is None:
                        continue

                    _, obj = parsed
                    if obj.get("kind") != "signal_entry":
                        continue

                    signals.append(TrackedSignal(
                        signal_id=obj["signal_id"],
                        symbol=obj["symbol"],
                        direction=obj["direction"],
                        entry_price=float(obj["entry_price"]),
                        entry_ts=float(obj["entry_ts"]),
                        invalidation_price=obj.get("invalidation_price"),
                    ))
        except OSError as e:
            logger.error("Failed to load signals: %s", e)

        return signals

    def _load_samples(self, max_lines: int) -> List[Tuple[float, Dict[str, float]]]:
        """Load price samples from JSONL (most recent first)."""
        if not self._samples_path.exists():
            return []

        rows: List[Tuple[float, Dict[str, float]]] = []

        try:
            with open(self._samples_path, "r", encoding="utf-8") as f:
                for i, line in enumerate(f):
                    if i >= max_lines:
                        break

                    parsed = _parse_sha256_jsonl_line(line)
                    if parsed is None:
                        continue

                    _, obj = parsed
                    if obj.get("kind") != "price_sample":
                        continue

                    ts = float(obj["ts"])
                    prices = {k: float(v) for k, v in obj["prices"].items()}
                    rows.append((ts, prices))
        except OSError as e:
            logger.error("Failed to load samples: %s", e)

        return rows

    def _load_cursor(self) -> dict:
        """Load cursor state from JSON."""
        if not self._cursor_path.exists():
            return {"done": {}}

        try:
            content = self._cursor_path.read_text(encoding="utf-8")
            return json.loads(content)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load cursor: %s", e)
            return {"done": {}}

    def _save_cursor(self) -> None:
        """Save cursor state atomically."""
        _atomic_write_text(self._cursor_path, _json_canonical(self._cursor))

    def get_weekly_stats(self, now_ts: Optional[float] = None) -> WeeklyStats:
        """
        Compute weekly statistics from outcomes.

        Used for weekly Telegram reports.
        """
        if now_ts is None:
            now_ts = time.time()

        week_sec = 7 * 24 * 3600
        week_start = now_ts - week_sec
        week_end = now_ts

        outcomes = self._load_outcomes()
        signals = self._load_signals()

        week_outcomes = [
            o for o in outcomes
            if week_start <= o.computed_ts <= week_end
        ]

        week_signals = [
            s for s in signals
            if week_start <= s.entry_ts <= week_end
        ]

        def calc_avg(values: List[Optional[float]]) -> Optional[float]:
            valid = [v for v in values if v is not None]
            return round(sum(valid) / len(valid), 4) if valid else None

        def calc_win_rate(outcomes_list: List[Outcome]) -> Optional[float]:
            valid = [o for o in outcomes_list if o.mfe is not None and o.mae is not None]
            if not valid:
                return None
            wins = sum(1 for o in valid if o.mfe > abs(o.mae))
            return round(wins / len(valid) * 100, 2)

        outcomes_1h = [o for o in week_outcomes if o.horizon_sec == 3600]
        outcomes_4h = [o for o in week_outcomes if o.horizon_sec == 4 * 3600]
        outcomes_24h = [o for o in week_outcomes if o.horizon_sec == 24 * 3600]

        return WeeklyStats(
            week_start=week_start,
            week_end=week_end,
            total_signals=len(week_signals),
            completed_outcomes=len([o for o in week_outcomes if o.reason == "ok"]),
            avg_mfe_1h=calc_avg([o.mfe for o in outcomes_1h]),
            avg_mae_1h=calc_avg([o.mae for o in outcomes_1h]),
            avg_mfe_4h=calc_avg([o.mfe for o in outcomes_4h]),
            avg_mae_4h=calc_avg([o.mae for o in outcomes_4h]),
            avg_mfe_24h=calc_avg([o.mfe for o in outcomes_24h]),
            avg_mae_24h=calc_avg([o.mae for o in outcomes_24h]),
            win_rate_1h=calc_win_rate(outcomes_1h),
            win_rate_4h=calc_win_rate(outcomes_4h),
            win_rate_24h=calc_win_rate(outcomes_24h),
            invalidated_count=len([o for o in week_outcomes if o.reason == "invalidated"]),
            by_signal_type={},
        )

    def _load_outcomes(self) -> List[Outcome]:
        """Load all outcomes from JSONL."""
        if not self._outcomes_path.exists():
            return []

        outcomes: List[Outcome] = []

        try:
            with open(self._outcomes_path, "r", encoding="utf-8") as f:
                for line in f:
                    parsed = _parse_sha256_jsonl_line(line)
                    if parsed is None:
                        continue

                    _, obj = parsed
                    if obj.get("kind") != "signal_outcome":
                        continue

                    outcomes.append(Outcome(
                        signal_id=obj["signal_id"],
                        horizon_sec=obj["horizon_sec"],
                        computed_ts=obj["computed_ts"],
                        mfe=obj.get("mfe"),
                        mae=obj.get("mae"),
                        samples_used=obj["samples_used"],
                        reason=obj["reason"],
                    ))
        except OSError as e:
            logger.error("Failed to load outcomes: %s", e)

        return outcomes

    def get_stats(self) -> Dict[str, int]:
        """Get basic statistics."""
        return {
            "total_signals": len(self._load_signals()),
            "total_samples": sum(1 for _ in open(self._samples_path, "r", encoding="utf-8")) if self._samples_path.exists() else 0,
            "total_outcomes": len(self._load_outcomes()),
            "cursor_entries": len(self._cursor.get("done", {})),
        }


def get_outcome_tracker() -> OutcomeTracker:
    """Get singleton tracker instance."""
    global _tracker_instance
    if "_tracker_instance" not in globals():
        _tracker_instance = OutcomeTracker()
    return _tracker_instance


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    print("=== SIGNAL OUTCOME TRACKER TEST ===\n")

    tracker = OutcomeTracker()

    test_signal = TrackedSignal(
        signal_id="sha256:test123456789",
        symbol="BTCUSDT",
        direction="long",
        entry_price=95000.0,
        entry_ts=time.time() - 7200,
    )
    tracker.record_signal(test_signal)

    for i in range(10):
        ts = test_signal.entry_ts + i * 600
        prices = {
            "BTCUSDT": 95000.0 + i * 100,
            "ETHUSDT": 3200.0 + i * 10,
        }
        tracker.record_price_samples(ts, prices)

    outcomes = tracker.update_outcomes(time.time())

    print(f"Computed {len(outcomes)} outcomes:")
    for o in outcomes:
        print(f"  {o.signal_id} @ {o.horizon_sec}s: MFE={o.mfe}%, MAE={o.mae}%, reason={o.reason}")

    print("\nStats:")
    stats = tracker.get_stats()
    for k, v in stats.items():
        print(f"  {k}: {v}")
