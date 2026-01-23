# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 16:00:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 16:00:00 UTC
# === END SIGNATURE ===
"""
Market Scanner - Fetches and stores market intelligence.

Produces: state/market_intel.json with:
- market_snapshot_id: sha256:xxx
- timestamp: Unix timestamp
- btc_price, eth_price, etc.

Usage:
    python -m core.market_scanner
    python -m core.market_scanner --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any


# SSoT: compute paths from module location
BASE_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = BASE_DIR / "state"
MARKET_INTEL_FILE = STATE_DIR / "market_intel.json"


def _sha256_json(data: dict) -> str:
    """Compute SHA256 of JSON-serialized data."""
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{h}"


def fetch_market_data() -> dict[str, Any]:
    """
    Fetch market data from APIs.

    Returns dict with BTC/ETH prices, volumes, etc.
    """
    # TODO: Implement actual API calls to Binance/CoinGecko
    # For now, return placeholder that satisfies contract
    return {
        "btc_price": 0.0,
        "eth_price": 0.0,
        "btc_24h_change": 0.0,
        "eth_24h_change": 0.0,
        "total_volume_24h": 0.0,
        "fear_greed_index": 50,
        "source": "placeholder",
    }


def create_market_intel(market_data: dict[str, Any]) -> dict[str, Any]:
    """
    Create market intelligence snapshot with required fields.
    """
    ts = time.time()
    snapshot_id = _sha256_json({"ts": ts, "data": market_data})

    return {
        "market_snapshot_id": snapshot_id,
        "snapshot_id": snapshot_id,  # Alias for compatibility
        "timestamp": ts,
        "ts": ts,  # Alias
        "data": market_data,
    }


def save_market_intel(intel: dict[str, Any]) -> Path:
    """
    Save market intelligence to state file.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Atomic write
    tmp = MARKET_INTEL_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(intel, f, indent=2, ensure_ascii=False)
    tmp.replace(MARKET_INTEL_FILE)

    return MARKET_INTEL_FILE


def main() -> int:
    """CLI entrypoint."""
    ap = argparse.ArgumentParser(description="Market Scanner")
    ap.add_argument("--dry-run", action="store_true", help="Don't save to file")
    ns = ap.parse_args()

    print("MARKET_SCANNER: Fetching market data...")

    try:
        market_data = fetch_market_data()
        intel = create_market_intel(market_data)

        print(f"  snapshot_id: {intel['market_snapshot_id'][:40]}...")
        print(f"  timestamp: {intel['timestamp']}")

        if ns.dry_run:
            print("  (dry-run: not saving)")
        else:
            path = save_market_intel(intel)
            print(f"  saved: {path}")

        return 0

    except Exception as e:
        print(f"MARKET_SCANNER: FAIL - {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
