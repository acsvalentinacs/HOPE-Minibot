# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 09:35:00 UTC
# Purpose: Update market_intel.json with fresh data from APIs
# === END SIGNATURE ===
"""
Market Intel Updater.

Fetches fresh data from:
- Binance: Top USDT pairs by volume, BTC/ETH prices
- CoinGecko: Global metrics, BTC dominance
- RSS News: Latest headlines

Writes to state/market_intel.json with sha256: checksum.

Usage:
    python -m scripts.update_market_intel
"""

import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

# Setup path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Paths
STATE_DIR = PROJECT_ROOT / "state"
MARKET_INTEL_PATH = STATE_DIR / "market_intel.json"

# API URLs
BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/24hr"
COINGECKO_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"


def fetch_json(url: str, timeout: int = 10) -> Optional[Dict[str, Any]]:
    """Fetch JSON from URL with error handling."""
    try:
        req = Request(url, headers={"User-Agent": "HOPE-Bot/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                logger.error(f"HTTP {resp.status} from {url}")
                return None
            return json.loads(resp.read().decode("utf-8"))
    except URLError as e:
        logger.error(f"URL error: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        return None
    except Exception as e:
        logger.error(f"Fetch error: {e}")
        return None


def fetch_binance_tickers() -> Dict[str, Any]:
    """Fetch Binance 24h tickers."""
    result = {
        "btc": None,
        "eth": None,
        "top_volume": [],
        "volume_anomalies": [],
    }

    data = fetch_json(BINANCE_TICKER_URL)
    if not data:
        return result

    # Filter USDT pairs only
    usdt_pairs = [t for t in data if t["symbol"].endswith("USDT")]

    # Sort by volume
    usdt_pairs.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)

    # Get BTC and ETH
    for t in usdt_pairs:
        symbol = t["symbol"]
        if symbol == "BTCUSDT":
            result["btc"] = {
                "price": float(t["lastPrice"]),
                "change_24h_pct": float(t["priceChangePercent"]),
                "volume_24h": float(t["quoteVolume"]),
                "high_24h": float(t["highPrice"]),
                "low_24h": float(t["lowPrice"]),
            }
        elif symbol == "ETHUSDT":
            result["eth"] = {
                "price": float(t["lastPrice"]),
                "change_24h_pct": float(t["priceChangePercent"]),
                "volume_24h": float(t["quoteVolume"]),
                "high_24h": float(t["highPrice"]),
                "low_24h": float(t["lowPrice"]),
            }

    # Top 10 by volume
    for t in usdt_pairs[:10]:
        result["top_volume"].append({
            "symbol": t["symbol"],
            "price": float(t["lastPrice"]),
            "change_24h_pct": float(t["priceChangePercent"]),
            "volume_24h": float(t["quoteVolume"]),
        })

    # Calculate average volume for anomaly detection
    volumes = [float(t.get("quoteVolume", 0)) for t in usdt_pairs[:100]]
    avg_volume = sum(volumes) / len(volumes) if volumes else 0

    # Find volume anomalies (>50% above average with significant price move)
    for t in usdt_pairs[:50]:
        volume = float(t.get("quoteVolume", 0))
        change = abs(float(t.get("priceChangePercent", 0)))
        if volume > avg_volume * 1.5 and change > 3:
            result["volume_anomalies"].append({
                "symbol": t["symbol"],
                "price": float(t["lastPrice"]),
                "change_24h_pct": float(t["priceChangePercent"]),
                "volume_24h": volume,
                "volume_vs_avg_pct": round((volume / avg_volume - 1) * 100, 1),
            })

    return result


def fetch_coingecko_global() -> Dict[str, Any]:
    """Fetch CoinGecko global metrics."""
    result = {
        "total_market_cap_usd": None,
        "btc_dominance": None,
        "eth_dominance": None,
        "market_cap_change_24h_pct": None,
        "active_cryptos": None,
    }

    data = fetch_json(COINGECKO_GLOBAL_URL)
    if not data or "data" not in data:
        return result

    global_data = data["data"]

    result["total_market_cap_usd"] = global_data.get("total_market_cap", {}).get("usd")
    result["btc_dominance"] = global_data.get("market_cap_percentage", {}).get("btc")
    result["eth_dominance"] = global_data.get("market_cap_percentage", {}).get("eth")
    result["market_cap_change_24h_pct"] = global_data.get("market_cap_change_percentage_24h_usd")
    result["active_cryptos"] = global_data.get("active_cryptocurrencies")

    return result


def compute_checksum(data: Dict[str, Any]) -> str:
    """Compute deterministic checksum."""
    canonical = json.dumps(data, sort_keys=True, default=str, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()[:16]


def atomic_write(path: Path, content: str) -> None:
    """Atomic file write (temp -> fsync -> replace)."""
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def update_market_intel() -> Dict[str, Any]:
    """
    Update market_intel.json with fresh data.

    Returns:
        Updated market intel dict
    """
    logger.info("Fetching market data...")

    # Fetch data
    binance_data = fetch_binance_tickers()
    coingecko_data = fetch_coingecko_global()

    # Build market intel
    timestamp = datetime.now(timezone.utc).isoformat()

    intel = {
        "schema": "market_intel:v1",
        "timestamp": timestamp,
        "timestamp_unix": time.time(),

        # Global metrics
        "global": {
            "total_market_cap_usd": coingecko_data.get("total_market_cap_usd"),
            "btc_dominance": coingecko_data.get("btc_dominance"),
            "eth_dominance": coingecko_data.get("eth_dominance"),
            "market_cap_change_24h_pct": coingecko_data.get("market_cap_change_24h_pct"),
            "active_cryptos": coingecko_data.get("active_cryptos"),
        },

        # Major coins
        "btc": binance_data.get("btc"),
        "eth": binance_data.get("eth"),

        # Top volume pairs
        "top_volume": binance_data.get("top_volume", []),

        # Volume anomalies
        "volume_anomalies": binance_data.get("volume_anomalies", []),

        # Metadata
        "_meta": {
            "sources": ["binance", "coingecko"],
            "ttl_seconds": 300,  # 5 minutes
        },
    }

    # Add checksum
    intel["checksum"] = compute_checksum({
        k: v for k, v in intel.items()
        if k not in ("checksum", "_meta")
    })

    # Write atomically
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    content = json.dumps(intel, indent=2, ensure_ascii=False)
    atomic_write(MARKET_INTEL_PATH, content)

    logger.info(f"Market intel updated: {MARKET_INTEL_PATH}")

    # Log summary
    if intel["btc"]:
        logger.info(f"  BTC: ${intel['btc']['price']:,.2f} ({intel['btc']['change_24h_pct']:+.2f}%)")
    if intel["eth"]:
        logger.info(f"  ETH: ${intel['eth']['price']:,.2f} ({intel['eth']['change_24h_pct']:+.2f}%)")
    if intel["global"]["total_market_cap_usd"]:
        cap = intel["global"]["total_market_cap_usd"]
        logger.info(f"  Market Cap: ${cap/1e12:.2f}T")
    if intel["volume_anomalies"]:
        logger.info(f"  Volume Anomalies: {len(intel['volume_anomalies'])} detected")

    return intel


def main():
    """Entry point."""
    try:
        intel = update_market_intel()

        print("\n" + "=" * 60)
        print("MARKET INTEL UPDATE COMPLETE")
        print("=" * 60)
        print(f"Timestamp: {intel['timestamp']}")
        print(f"Checksum: {intel['checksum']}")
        print(f"File: {MARKET_INTEL_PATH}")
        print("=" * 60)

        return 0

    except Exception as e:
        logger.error(f"Failed to update market intel: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
