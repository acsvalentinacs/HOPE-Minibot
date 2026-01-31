# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-01-31 12:10:00 UTC
# Purpose: Auto-update HOT_LIST with top gainers and volatile coins
# === END SIGNATURE ===
"""
HOT LIST UPDATER - Automatically finds and updates hot coins for trading.

This module:
1. Fetches top gainers from Binance (24h price change)
2. Fetches most volatile coins (high-low range)
3. Combines into HOT_LIST for Three-Layer AllowList
4. Updates state/hot_list.json every 5 minutes

SAFE: Does not modify existing CORE_LIST or DYNAMIC_LIST.
"""

import os
import sys
import json
import time
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Set

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from binance.client import Client
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger("HOT_LIST")

# Configuration
HOT_LIST_FILE = PROJECT_ROOT / "state" / "hot_list.json"
MIN_VOLUME_USD = 5_000_000  # $5M minimum 24h volume
MIN_GAIN_PCT = 3.0  # Minimum 3% gain to be considered
MAX_SYMBOLS = 40  # Maximum symbols in hot list
UPDATE_INTERVAL = 300  # 5 minutes

# Exclusions
STABLECOINS = {
    'USDCUSDT', 'FDUSDUSDT', 'TUSDUSDT', 'BUSDUSDT', 'USDPUSDT',
    'EURUSDT', 'GBPUSDT', 'USTCUSDT', 'DAIUSDT', 'USDTUSDT',
    'USD1USDT', 'RLUSDT'
}

BLACKLIST = {
    'LUNAUSDT', 'USTCUSDT', 'SRMUST'  # Dead/delisted coins
}


def get_binance_client() -> Client:
    """Initialize Binance client."""
    load_dotenv('C:/secrets/hope.env')
    return Client(
        os.getenv('BINANCE_API_KEY'),
        os.getenv('BINANCE_API_SECRET')
    )


def fetch_top_gainers(client: Client, limit: int = 20) -> List[Dict]:
    """Fetch top gaining coins by 24h price change."""
    tickers = client.get_ticker()

    gainers = [
        t for t in tickers
        if t['symbol'].endswith('USDT')
        and float(t['quoteVolume']) > MIN_VOLUME_USD
        and t['symbol'] not in STABLECOINS
        and t['symbol'] not in BLACKLIST
        and float(t['priceChangePercent']) > MIN_GAIN_PCT
    ]

    gainers.sort(key=lambda x: float(x['priceChangePercent']), reverse=True)
    return gainers[:limit]


def fetch_most_volatile(client: Client, limit: int = 20) -> List[Dict]:
    """Fetch most volatile coins by high-low range."""
    tickers = client.get_ticker()

    volatile = []
    for t in tickers:
        if not t['symbol'].endswith('USDT'):
            continue
        if float(t['quoteVolume']) < MIN_VOLUME_USD:
            continue
        if t['symbol'] in STABLECOINS or t['symbol'] in BLACKLIST:
            continue

        low = float(t['lowPrice'])
        high = float(t['highPrice'])
        if low <= 0:
            continue

        range_pct = (high - low) / low * 100
        if range_pct > 5:  # At least 5% range
            t['_range_pct'] = range_pct
            volatile.append(t)

    volatile.sort(key=lambda x: x['_range_pct'], reverse=True)
    return volatile[:limit]


def build_hot_list(client: Client) -> Dict:
    """Build combined hot list from gainers and volatile coins."""
    log.info("Fetching top gainers...")
    gainers = fetch_top_gainers(client)

    log.info("Fetching most volatile...")
    volatile = fetch_most_volatile(client)

    # Combine unique symbols
    hot_symbols: Set[str] = set()
    details = {}

    for t in gainers:
        sym = t['symbol']
        hot_symbols.add(sym)
        details[sym] = {
            'change_pct': float(t['priceChangePercent']),
            'volume_usd': float(t['quoteVolume']),
            'source': 'gainer',
            'price': float(t['lastPrice'])
        }

    for t in volatile:
        sym = t['symbol']
        if sym not in hot_symbols:
            hot_symbols.add(sym)
            details[sym] = {
                'range_pct': t.get('_range_pct', 0),
                'volume_usd': float(t['quoteVolume']),
                'source': 'volatile',
                'price': float(t['lastPrice'])
            }

    # Sort by volume and limit
    sorted_symbols = sorted(
        hot_symbols,
        key=lambda s: details[s]['volume_usd'],
        reverse=True
    )[:MAX_SYMBOLS]

    result = {
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'count': len(sorted_symbols),
        'symbols': sorted_symbols,
        'details': {s: details[s] for s in sorted_symbols}
    }

    return result


def save_hot_list(data: Dict) -> None:
    """Save hot list to file."""
    HOT_LIST_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(HOT_LIST_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    log.info(f"Saved {data['count']} symbols to {HOT_LIST_FILE}")


def run_once() -> Dict:
    """Run single update cycle."""
    client = get_binance_client()
    hot_list = build_hot_list(client)
    save_hot_list(hot_list)

    # Print summary
    print("\n" + "="*60)
    print("HOT LIST UPDATED")
    print("="*60)
    print(f"Symbols: {hot_list['count']}")
    print(f"Updated: {hot_list['updated_at']}")
    print("\nTop 10:")
    for i, sym in enumerate(hot_list['symbols'][:10], 1):
        d = hot_list['details'][sym]
        if 'change_pct' in d:
            print(f"  {i:2}. {sym:12} +{d['change_pct']:.2f}% | Vol: ${d['volume_usd']/1e6:.1f}M")
        else:
            print(f"  {i:2}. {sym:12} range:{d['range_pct']:.2f}% | Vol: ${d['volume_usd']/1e6:.1f}M")
    print("="*60)

    return hot_list


def run_daemon():
    """Run as daemon, updating every 5 minutes."""
    log.info("Starting HOT_LIST updater daemon...")

    while True:
        try:
            run_once()
        except Exception as e:
            log.error(f"Update failed: {e}")

        log.info(f"Sleeping {UPDATE_INTERVAL}s...")
        time.sleep(UPDATE_INTERVAL)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="HOT LIST Updater")
    parser.add_argument("--daemon", action="store_true", help="Run as daemon")
    parser.add_argument("--once", action="store_true", help="Run once and exit")

    args = parser.parse_args()

    if args.daemon:
        run_daemon()
    else:
        run_once()
