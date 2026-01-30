# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 21:30:00 UTC
# Purpose: Sync AutoTrader trades to ML training data
# === END SIGNATURE ===
"""
HOPE AI - Training Data Sync

Collects completed trades from AutoTrader and adds to training dataset.
Merges signal features with trade outcomes (win/loss based on PnL).

Usage:
    python scripts/sync_training_data.py --status    # Show current status
    python scripts/sync_training_data.py --sync      # Sync new trades
    python scripts/sync_training_data.py --continuous 30  # Every 30 sec
"""

import json
import hashlib
import argparse
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Set

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
TRADES_FILE = PROJECT_ROOT / "state" / "ai" / "autotrader" / "trades.jsonl"
SIGNALS_FILE = PROJECT_ROOT / "state" / "ai" / "signals" / "moonbot_signals.jsonl"
TRAINING_FILE = PROJECT_ROOT / "state" / "ai" / "training" / "training_samples.jsonl"
SYNCED_FILE = PROJECT_ROOT / "state" / "ai" / "training" / "synced_trades.json"

# Ensure dirs
TRAINING_FILE.parent.mkdir(parents=True, exist_ok=True)


def load_jsonl(path: Path) -> List[Dict]:
    """Load JSONL file."""
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


def load_synced_ids() -> Set[str]:
    """Load already synced trade IDs."""
    if not SYNCED_FILE.exists():
        return set()
    try:
        data = json.loads(SYNCED_FILE.read_text(encoding="utf-8"))
        return set(data.get("synced_ids", []))
    except:
        return set()


def save_synced_ids(ids: Set[str]) -> None:
    """Save synced trade IDs."""
    data = {
        "synced_ids": list(ids),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    SYNCED_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def extract_features_from_signal(signal: Dict) -> Optional[Dict]:
    """Extract ML features from signal data."""
    try:
        # Try to get from various signal formats
        precursor = signal.get("precursor", {})
        details = precursor.get("details", {})

        return {
            "symbol": signal.get("symbol", "UNKNOWN"),
            "buys_per_sec": details.get("buys_per_sec", 0),
            "delta_pct": details.get("delta_pct", 0),
            "vol_raise_pct": details.get("vol_raise_pct", 0),
            "signal_count": details.get("signal_count", 0),
            "is_precursor": details.get("is_precursor", False),
            "confidence": signal.get("decision", {}).get("confidence", 0.5),
        }
    except:
        return None


def get_training_status() -> Dict[str, Any]:
    """Get current training data status."""
    samples = load_jsonl(TRAINING_FILE)
    trades = load_jsonl(TRADES_FILE)
    signals = load_jsonl(SIGNALS_FILE)
    synced_ids = load_synced_ids()

    # Count wins/losses
    wins = sum(1 for s in samples if s.get("win_5m") is True)
    losses = sum(1 for s in samples if s.get("win_5m") is False)

    # Count trades with outcomes
    order_events = [t for t in trades if t.get("event") == "ORDER"]
    close_events = [t for t in trades if t.get("event") == "CLOSE"]

    # Pending (not synced)
    pending_count = 0
    for close in close_events:
        pos_id = close.get("data", {}).get("position_id", "")
        if pos_id and pos_id not in synced_ids:
            pending_count += 1

    return {
        "total_samples": len(samples),
        "wins": wins,
        "losses": losses,
        "win_rate": wins / (wins + losses) * 100 if (wins + losses) > 0 else 0,
        "threshold": 100,
        "optimal": 200,
        "until_retrain": max(0, 100 - len(samples)),
        "trades_total": len(order_events),
        "closes_total": len(close_events),
        "signals_available": len(signals),
        "synced_trades": len(synced_ids),
        "pending_sync": pending_count,
    }


def sync_trades() -> Dict[str, Any]:
    """Sync new trades to training data."""
    trades = load_jsonl(TRADES_FILE)
    signals = load_jsonl(SIGNALS_FILE)
    synced_ids = load_synced_ids()

    # Build signal lookup by signal_id
    signal_lookup: Dict[str, Dict] = {}
    for sig in signals:
        sig_id = sig.get("signal_id", "")
        if sig_id:
            signal_lookup[sig_id] = sig

    # Match ORDER + CLOSE events
    orders: Dict[str, Dict] = {}
    closes: Dict[str, Dict] = {}

    for trade in trades:
        event = trade.get("event", "")
        data = trade.get("data", {})

        if event == "ORDER":
            order_data = data.get("order", {})
            order_id = order_data.get("order_id", "")
            if order_id:
                orders[f"pos_{order_id}"] = {
                    "order_id": order_id,
                    "signal_id": data.get("signal_id", ""),
                    "symbol": data.get("symbol", ""),
                    "entry_price": order_data.get("avg_price", 0),
                    "quantity": order_data.get("filled_quantity", 0),
                    "timestamp": trade.get("timestamp", ""),
                }
        elif event == "CLOSE":
            pos_id = data.get("position_id", "")
            if pos_id:
                closes[pos_id] = {
                    "position_id": pos_id,
                    "symbol": data.get("symbol", ""),
                    "reason": data.get("reason", ""),
                    "pnl_pct": data.get("pnl_pct", 0),
                    "timestamp": trade.get("timestamp", ""),
                }

    # Create training samples
    new_samples = []
    for pos_id, close in closes.items():
        # Skip already synced
        if pos_id in synced_ids:
            continue

        # Find matching order
        order = orders.get(pos_id)
        if not order:
            continue

        # Skip broken entries (PnL -100% usually means error)
        pnl = close.get("pnl_pct", 0)
        if pnl <= -99:
            print(f"  [SKIP] {pos_id}: PnL={pnl}% (error entry)")
            synced_ids.add(pos_id)
            continue

        # Determine win/loss
        # Win: PnL > 0 OR reason == TARGET_HIT
        # Loss: PnL <= 0 OR reason == STOPPED
        is_win = pnl > 0 or close.get("reason") == "TARGET_HIT"

        # Get signal features if available
        signal_id = order.get("signal_id", "")
        signal = signal_lookup.get(signal_id, {})
        features = extract_features_from_signal(signal)

        sample = {
            "signal_id": signal_id,
            "symbol": order.get("symbol", close.get("symbol", "UNKNOWN")),
            "entry_price": order.get("entry_price", 0),
            "pnl_pct": pnl,
            "reason": close.get("reason", "UNKNOWN"),
            "win_5m": is_win,
            "entry_time": order.get("timestamp", ""),
            "exit_time": close.get("timestamp", ""),
            "features": features or {},
            "synced_at": datetime.now(timezone.utc).isoformat(),
        }

        new_samples.append(sample)
        synced_ids.add(pos_id)

        status = "[WIN]" if is_win else "[LOSS]"
        print(f"  {status} {sample['symbol']}: PnL={pnl:+.2f}% ({close.get('reason', '?')})")

    # Append to training file
    if new_samples:
        with open(TRAINING_FILE, "a", encoding="utf-8") as f:
            for sample in new_samples:
                f.write(json.dumps(sample, default=str) + "\n")

    # Save synced IDs
    save_synced_ids(synced_ids)

    return {
        "new_samples": len(new_samples),
        "total_synced": len(synced_ids),
    }


def print_status():
    """Print formatted status."""
    status = get_training_status()

    pct = min(100, status["total_samples"] / status["threshold"] * 100)
    bar_len = 40
    filled = int(bar_len * pct / 100)
    bar = "#" * filled + "-" * (bar_len - filled)

    print("=" * 60)
    print("       ML TRAINING DATA STATUS")
    print("=" * 60)
    print(f"  Samples:      {status['total_samples']} / {status['threshold']} (optimal: {status['optimal']})")
    print(f"  Progress:     [{bar}] {pct:.0f}%")
    print(f"  Until retrain: {status['until_retrain']} more samples")
    print()
    print(f"  Win rate:     {status['win_rate']:.1f}% ({status['wins']}W / {status['losses']}L)")
    print()
    print("  Data sources:")
    print(f"    Signals:    {status['signals_available']}")
    print(f"    Trades:     {status['trades_total']}")
    print(f"    Closes:     {status['closes_total']}")
    print(f"    Synced:     {status['synced_trades']}")
    print(f"    Pending:    {status['pending_sync']}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="HOPE AI Training Data Sync")
    parser.add_argument("--status", action="store_true", help="Show status")
    parser.add_argument("--sync", action="store_true", help="Sync new trades")
    parser.add_argument("--continuous", type=int, metavar="SEC", help="Run continuously")
    args = parser.parse_args()

    if args.status:
        print_status()
        return

    if args.sync:
        print("[SYNC] Syncing trades to training data...")
        result = sync_trades()
        print(f"[DONE] Added {result['new_samples']} new samples")
        print()
        print_status()
        return

    if args.continuous:
        print(f"[CONTINUOUS] Running every {args.continuous} seconds (Ctrl+C to stop)")
        try:
            while True:
                result = sync_trades()
                if result["new_samples"] > 0:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Added {result['new_samples']} samples")
                else:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] No new trades")
                time.sleep(args.continuous)
        except KeyboardInterrupt:
            print("\n[STOPPED]")
        return

    # Default: show status
    print_status()


if __name__ == "__main__":
    main()
