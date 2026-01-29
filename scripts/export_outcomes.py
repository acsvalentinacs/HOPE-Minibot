# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 13:45:00 UTC
# Purpose: Export outcomes history to JSONL for ML training
# === END SIGNATURE ===
"""
Outcome History Exporter

Exports completed outcomes to JSONL format for ML training.

Output format (per line):
{
    "signal_id": "sig:abc123",
    "symbol": "BTCUSDT",
    "entry_price": 88100.0,
    "direction": "Long",
    "entry_time": "2026-01-29T12:00:00Z",
    "mfe_1m": 0.5,
    "mfe_5m": 1.2,
    "mfe_15m": 2.0,
    "mfe_60m": 3.5,
    "mae_1m": -0.2,
    "mae_5m": -0.5,
    "mae_15m": -0.8,
    "mae_60m": -1.0,
    "outcome_1m": "WIN",
    "outcome_5m": "WIN",
    "outcome_15m": "WIN",
    "outcome_60m": "WIN",
    "signal_data": {...}
}

Usage:
    python scripts/export_outcomes.py
    python scripts/export_outcomes.py --output data/training/outcomes.jsonl
    python scripts/export_outcomes.py --since 2026-01-29
"""

import json
import sys
import os
import argparse
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

GATEWAY_URL = "http://127.0.0.1:8100"
DEFAULT_OUTPUT = Path("data/training/outcomes_export.jsonl")
STATE_DIR = Path("state/ai/outcomes")


# ═══════════════════════════════════════════════════════════════════════════
# EXPORTER
# ═══════════════════════════════════════════════════════════════════════════

class OutcomeExporter:
    """Exports outcome data for ML training."""

    def __init__(self, gateway_url: str = GATEWAY_URL):
        self.gateway_url = gateway_url

    def fetch_from_gateway(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """Fetch outcomes from running gateway."""
        try:
            resp = requests.get(
                f"{self.gateway_url}/outcomes/completed",
                params={"limit": limit},
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("outcomes", [])
            return []
        except Exception as e:
            print(f"Gateway fetch failed: {e}")
            return []

    def load_from_file(self, path: Path) -> List[Dict[str, Any]]:
        """Load outcomes from JSONL file."""
        outcomes = []
        if not path.exists():
            return outcomes

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    outcomes.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        return outcomes

    def transform_for_ml(self, outcome: Dict[str, Any]) -> Dict[str, Any]:
        """Transform outcome to ML training format."""
        # Flatten horizons
        horizons = outcome.get("outcomes", {})

        result = {
            "signal_id": outcome.get("signal_id", ""),
            "symbol": outcome.get("symbol", ""),
            "entry_price": outcome.get("entry_price", 0),
            "direction": outcome.get("direction", "Long"),
            "entry_time": outcome.get("entry_time", ""),
            "mfe_total": outcome.get("mfe", 0),
            "mae_total": outcome.get("mae", 0),
        }

        # Add per-horizon metrics
        for horizon in [1, 5, 15, 60]:
            h_data = horizons.get(str(horizon), {})
            result[f"mfe_{horizon}m"] = h_data.get("mfe", 0)
            result[f"mae_{horizon}m"] = h_data.get("mae", 0)
            result[f"outcome_{horizon}m"] = h_data.get("outcome", "UNKNOWN")
            result[f"price_{horizon}m"] = h_data.get("price", 0)

        # Add signal metadata
        signal_data = outcome.get("signal_data", {})
        result["strategy"] = signal_data.get("strategy", "unknown")
        result["dBTC"] = signal_data.get("dBTC", 0)
        result["dBTC5m"] = signal_data.get("dBTC5m", 0)
        result["dBTC1m"] = signal_data.get("dBTC1m", 0)

        return result

    def export(
        self,
        output_path: Path,
        since: Optional[datetime] = None,
        include_pending: bool = False,
    ) -> int:
        """
        Export outcomes to JSONL file.

        Args:
            output_path: Output file path
            since: Only export outcomes after this time
            include_pending: Include pending (incomplete) signals

        Returns:
            Number of outcomes exported
        """
        # Collect outcomes from multiple sources
        all_outcomes = []

        # 1. From gateway API
        gateway_outcomes = self.fetch_from_gateway(limit=10000)
        all_outcomes.extend(gateway_outcomes)
        print(f"Fetched {len(gateway_outcomes)} from gateway")

        # 2. From state files
        completed_file = STATE_DIR / "completed_outcomes.jsonl"
        if completed_file.exists():
            file_outcomes = self.load_from_file(completed_file)
            # Dedupe by signal_id
            seen = {o.get("signal_id") for o in all_outcomes}
            for o in file_outcomes:
                if o.get("signal_id") not in seen:
                    all_outcomes.append(o)
            print(f"Loaded {len(file_outcomes)} from {completed_file}")

        # Filter by time
        if since:
            filtered = []
            for o in all_outcomes:
                entry_time = o.get("entry_time", "")
                if entry_time:
                    try:
                        dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                        if dt >= since:
                            filtered.append(o)
                    except:
                        filtered.append(o)  # Include if can't parse
                else:
                    filtered.append(o)
            all_outcomes = filtered
            print(f"Filtered to {len(all_outcomes)} since {since}")

        # Transform and export
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write
        tmp_path = output_path.with_suffix(".tmp")
        exported = 0

        try:
            with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
                for outcome in all_outcomes:
                    ml_record = self.transform_for_ml(outcome)
                    f.write(json.dumps(ml_record, ensure_ascii=False) + "\n")
                    exported += 1
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_path, output_path)

        except Exception as e:
            if tmp_path.exists():
                tmp_path.unlink()
            raise

        # Calculate checksum
        with open(output_path, "rb") as f:
            checksum = hashlib.sha256(f.read()).hexdigest()[:16]

        print(f"\nExported {exported} outcomes to {output_path}")
        print(f"Checksum: sha256:{checksum}")

        return exported


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Export Outcomes for ML Training")
    parser.add_argument("--output", "-o", type=str, default=str(DEFAULT_OUTPUT),
                        help="Output file path")
    parser.add_argument("--url", type=str, default=GATEWAY_URL, help="Gateway URL")
    parser.add_argument("--since", type=str, help="Export since date (YYYY-MM-DD)")
    parser.add_argument("--include-pending", action="store_true",
                        help="Include pending signals")

    args = parser.parse_args()

    # Parse since date
    since = None
    if args.since:
        try:
            since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"Invalid date format: {args.since}")
            print("Use YYYY-MM-DD format")
            sys.exit(1)

    # Export
    exporter = OutcomeExporter(gateway_url=args.url)
    output_path = Path(args.output)

    try:
        count = exporter.export(
            output_path=output_path,
            since=since,
            include_pending=args.include_pending,
        )

        print(f"\n[OK] Export complete: {count} records")

    except Exception as e:
        print(f"[ERROR] Export failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
