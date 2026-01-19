"""
Outcome Tracker - Track signal performance with MFE/MAE metrics.

Implements OUTCOME TRACKING RULE from CLAUDE.md:
- tracked_signals.jsonl: signal entries with entry_price, invalidation_price
- price_samples.jsonl: sampled prices per cycle
- signal_outcomes.jsonl: computed MFE/MAE per horizon

All writes are atomic (temp -> fsync -> replace) per CRITICAL RULE: FILE WRITING.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("outcome_tracker")

BASE_DIR = Path(r"C:\Users\kirillDev\Desktop\TradingBot\minibot")
STATE_DIR = BASE_DIR / "state"

TRACKED_SIGNALS_FILE = STATE_DIR / "tracked_signals.jsonl"
PRICE_SAMPLES_FILE = STATE_DIR / "price_samples.jsonl"
SIGNAL_OUTCOMES_FILE = STATE_DIR / "signal_outcomes.jsonl"

# Horizons for outcome tracking (seconds)
DEFAULT_HORIZONS = [3600, 14400, 86400]  # 1h, 4h, 24h


def _atomic_write(path: Path, content: str) -> None:
    """Atomic write: temp -> fsync -> replace."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _atomic_append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    """
    Atomic append to JSONL file.

    Reads existing content, appends new record, writes atomically.
    This ensures no partial writes or corruption.
    """
    lines = []
    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception as e:
            logger.warning("Failed to read %s: %s", path, e)

    lines.append(json.dumps(record, ensure_ascii=False, sort_keys=True))
    _atomic_write(path, "\n".join(lines) + "\n")


def _generate_signal_id(signal: Dict[str, Any]) -> str:
    """Generate deterministic signal ID from key fields."""
    key_fields = {
        "symbol": signal.get("symbol"),
        "side": signal.get("side"),
        "entry_price": signal.get("entry_price"),
        "ts_utc": signal.get("ts_utc"),
    }
    canonical = json.dumps(key_fields, sort_keys=True, ensure_ascii=False)
    hash_hex = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{hash_hex}"


@dataclass
class TrackedSignal:
    """Signal entry for outcome tracking."""
    signal_id: str
    ts_utc: float
    symbol: str
    side: str  # LONG or SHORT
    entry_price: float
    invalidation_price: Optional[float] = None
    timeframe: str = "1h"
    snapshot_id: str = ""
    leverage: int = 1
    margin_type: str = "ISOLATED"  # ISOLATED or CROSS

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TrackedSignal":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class PriceSample:
    """Price sample for outcome calculation."""
    ts_utc: float
    symbol: str
    price: float
    source: str = "binance"
    snapshot_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SignalOutcome:
    """Computed outcome for a signal at specific horizon."""
    signal_id: str
    horizon_sec: int
    mfe: float  # Maximum Favorable Excursion (%)
    mae: float  # Maximum Adverse Excursion (%)
    outcome_ts_utc: float
    reference_price: float
    pnl_pct: float = 0.0  # Current PnL at horizon

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def record_signal(signal: Dict[str, Any]) -> str:
    """
    Record a new signal for tracking.

    Args:
        signal: Dict with keys: symbol, side, entry_price, ts_utc,
                optional: invalidation_price, timeframe, snapshot_id, leverage, margin_type

    Returns:
        signal_id (sha256:...)
    """
    # Generate ID if not provided
    if "signal_id" not in signal or not signal["signal_id"]:
        signal["signal_id"] = _generate_signal_id(signal)

    # Ensure required fields
    required = ["symbol", "side", "entry_price"]
    for field in required:
        if field not in signal:
            raise ValueError(f"FAIL-CLOSED: signal missing required field: {field}")

    # Add timestamp if missing
    if "ts_utc" not in signal:
        signal["ts_utc"] = time.time()

    # Validate entry_price
    if signal["entry_price"] <= 0:
        raise ValueError(f"FAIL-CLOSED: invalid entry_price: {signal['entry_price']}")

    # Create TrackedSignal to validate schema
    tracked = TrackedSignal(
        signal_id=signal["signal_id"],
        ts_utc=signal["ts_utc"],
        symbol=signal["symbol"],
        side=signal["side"].upper(),
        entry_price=signal["entry_price"],
        invalidation_price=signal.get("invalidation_price"),
        timeframe=signal.get("timeframe", "1h"),
        snapshot_id=signal.get("snapshot_id", ""),
        leverage=signal.get("leverage", 1),
        margin_type=signal.get("margin_type", "ISOLATED"),
    )

    _atomic_append_jsonl(TRACKED_SIGNALS_FILE, tracked.to_dict())
    logger.info("Recorded signal: %s %s %s @ %.8f",
                tracked.signal_id[:24], tracked.symbol, tracked.side, tracked.entry_price)

    return tracked.signal_id


def record_price_sample(symbol: str, price: float, source: str = "binance",
                        snapshot_id: str = "") -> None:
    """
    Record a price sample for outcome calculation.

    Args:
        symbol: Trading pair (e.g., BTCUSDT)
        price: Current price
        source: Data source
        snapshot_id: Reference to market snapshot
    """
    if price <= 0:
        logger.warning("FAIL-CLOSED: invalid price sample: %s = %s", symbol, price)
        return

    sample = PriceSample(
        ts_utc=time.time(),
        symbol=symbol,
        price=price,
        source=source,
        snapshot_id=snapshot_id,
    )

    _atomic_append_jsonl(PRICE_SAMPLES_FILE, sample.to_dict())
    logger.debug("Recorded price: %s = %.8f", symbol, price)


def get_tracked_signals(max_age_sec: int = 86400 * 7) -> List[TrackedSignal]:
    """
    Load tracked signals from JSONL file.

    Args:
        max_age_sec: Only return signals younger than this (default 7 days)

    Returns:
        List of TrackedSignal objects
    """
    if not TRACKED_SIGNALS_FILE.exists():
        return []

    signals = []
    now = time.time()
    cutoff = now - max_age_sec

    try:
        for line in TRACKED_SIGNALS_FILE.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                if data.get("ts_utc", 0) >= cutoff:
                    signals.append(TrackedSignal.from_dict(data))
            except json.JSONDecodeError:
                logger.warning("Skipping malformed signal line")
    except Exception as e:
        logger.error("Failed to read tracked signals: %s", e)

    return signals


def get_price_samples(symbol: str, since_ts: float) -> List[PriceSample]:
    """
    Get price samples for a symbol since timestamp.

    Args:
        symbol: Trading pair
        since_ts: Unix timestamp to start from

    Returns:
        List of PriceSample objects
    """
    if not PRICE_SAMPLES_FILE.exists():
        return []

    samples = []
    try:
        for line in PRICE_SAMPLES_FILE.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                if data.get("symbol") == symbol and data.get("ts_utc", 0) >= since_ts:
                    samples.append(PriceSample(**data))
            except (json.JSONDecodeError, TypeError):
                pass
    except Exception as e:
        logger.error("Failed to read price samples: %s", e)

    return samples


def compute_outcomes(horizons: List[int] = None) -> int:
    """
    Compute MFE/MAE outcomes for tracked signals.

    Args:
        horizons: List of horizon seconds (default: 1h, 4h, 24h)

    Returns:
        Number of outcomes computed
    """
    if horizons is None:
        horizons = DEFAULT_HORIZONS

    signals = get_tracked_signals()
    if not signals:
        logger.info("No tracked signals to compute outcomes for")
        return 0

    now = time.time()
    outcomes_computed = 0

    for signal in signals:
        samples = get_price_samples(signal.symbol, signal.ts_utc)
        if not samples:
            continue

        for horizon in horizons:
            horizon_end = signal.ts_utc + horizon
            if now < horizon_end:
                # Horizon not yet reached
                continue

            # Get samples within horizon
            horizon_samples = [s for s in samples if signal.ts_utc <= s.ts_utc <= horizon_end]
            if not horizon_samples:
                continue

            # Calculate MFE/MAE
            entry = signal.entry_price
            prices = [s.price for s in horizon_samples]

            if signal.side == "LONG":
                mfe = max((p - entry) / entry * 100 for p in prices)
                mae = min((p - entry) / entry * 100 for p in prices)
            else:  # SHORT
                mfe = max((entry - p) / entry * 100 for p in prices)
                mae = min((entry - p) / entry * 100 for p in prices)

            # Get price at horizon end
            ref_price = horizon_samples[-1].price
            if signal.side == "LONG":
                pnl_pct = (ref_price - entry) / entry * 100
            else:
                pnl_pct = (entry - ref_price) / entry * 100

            outcome = SignalOutcome(
                signal_id=signal.signal_id,
                horizon_sec=horizon,
                mfe=round(mfe, 4),
                mae=round(mae, 4),
                outcome_ts_utc=horizon_end,
                reference_price=ref_price,
                pnl_pct=round(pnl_pct, 4),
            )

            _atomic_append_jsonl(SIGNAL_OUTCOMES_FILE, outcome.to_dict())
            outcomes_computed += 1

            logger.info("Outcome: %s %dh MFE=%.2f%% MAE=%.2f%% PnL=%.2f%%",
                       signal.signal_id[:16], horizon // 3600, mfe, mae, pnl_pct)

    return outcomes_computed


def get_symbols_to_track() -> List[str]:
    """Get list of symbols that need price tracking."""
    signals = get_tracked_signals(max_age_sec=86400)  # Last 24h
    return list(set(s.symbol for s in signals))


def cleanup_old_data(max_age_days: int = 30) -> int:
    """
    Remove data older than max_age_days.

    Args:
        max_age_days: Maximum age in days

    Returns:
        Number of lines removed
    """
    cutoff = time.time() - (max_age_days * 86400)
    removed = 0

    for filepath in [TRACKED_SIGNALS_FILE, PRICE_SAMPLES_FILE, SIGNAL_OUTCOMES_FILE]:
        if not filepath.exists():
            continue

        lines = []
        for line in filepath.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                ts = data.get("ts_utc") or data.get("outcome_ts_utc", 0)
                if ts >= cutoff:
                    lines.append(line)
                else:
                    removed += 1
            except json.JSONDecodeError:
                pass

        if lines:
            _atomic_write(filepath, "\n".join(lines) + "\n")
        elif filepath.exists():
            filepath.unlink()

    if removed > 0:
        logger.info("Cleaned up %d old records", removed)

    return removed


# CLI interface
def main() -> int:
    """CLI entrypoint for outcome tracker."""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if len(sys.argv) < 2:
        print("Usage: python -m core.outcome_tracker <command>")
        print("Commands:")
        print("  compute   - Compute outcomes for tracked signals")
        print("  status    - Show tracking status")
        print("  cleanup   - Remove old data (>30 days)")
        return 1

    command = sys.argv[1]

    if command == "compute":
        count = compute_outcomes()
        print(f"Computed {count} outcomes")
        return 0

    elif command == "status":
        signals = get_tracked_signals()
        symbols = get_symbols_to_track()
        print(f"Tracked signals: {len(signals)}")
        print(f"Symbols to track: {symbols}")

        if SIGNAL_OUTCOMES_FILE.exists():
            outcomes = len(SIGNAL_OUTCOMES_FILE.read_text(encoding="utf-8").splitlines())
            print(f"Outcomes computed: {outcomes}")
        return 0

    elif command == "cleanup":
        removed = cleanup_old_data()
        print(f"Removed {removed} old records")
        return 0

    else:
        print(f"Unknown command: {command}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
