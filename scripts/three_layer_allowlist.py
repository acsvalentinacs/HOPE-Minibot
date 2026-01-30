# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 16:10:00 UTC
# Purpose: Three-Layer AllowList (CORE + DYNAMIC + HOT) for autonomous trading
# === END SIGNATURE ===
"""
Three-Layer AllowList System for HOPE Trading

Architecture:
┌────────────────┐  ┌────────────────┐  ┌────────────────┐
│   CORE_LIST    │  │  DYNAMIC_LIST  │  │    HOT_LIST    │
│   (permanent)  │  │  (hourly)      │  │  (instant)     │
├────────────────┤  ├────────────────┤  ├────────────────┤
│ BTC, ETH, SOL  │  │ Top 20 by vol  │  │ Pump signals   │
│ BNB, XRP, DOGE │  │ $50M+ 24h      │  │ TTL: 15 min    │
├────────────────┤  ├────────────────┤  ├────────────────┤
│ Position: 100% │  │ Position: 100% │  │ Position: 50%  │
│ Timeout: 60s   │  │ Timeout: 45s   │  │ Timeout: 30s   │
└────────────────┘  └────────────────┘  └────────────────┘

Usage:
    from scripts.three_layer_allowlist import ThreeLayerAllowList

    allowlist = ThreeLayerAllowList()
    await allowlist.initialize()

    # Check if symbol allowed
    if allowlist.is_allowed("BTCUSDT"):
        params = allowlist.get_trading_params("BTCUSDT")

    # Process pump signal (auto-add to HOT)
    result = allowlist.process_pump_signal(signal)
"""

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from enum import Enum

# Add project root
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    import httpx
except ImportError:
    httpx = None

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
log = logging.getLogger("ALLOWLIST")

# Paths
STATE_DIR = ROOT / "state"
STATE_FILE = STATE_DIR / "allowlist_state.json"
STATE_DIR.mkdir(parents=True, exist_ok=True)


class ListType(str, Enum):
    """AllowList tier types."""
    CORE = "core"
    DYNAMIC = "dynamic"
    HOT = "hot"
    BLACKLIST = "blacklist"
    NONE = "none"


# === CONFIGURATION ===

# CORE_LIST: Blue chips, always allowed (never changes)
CORE_LIST = {
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "LTCUSDT", "MATICUSDT", "ATOMUSDT", "NEARUSDT", "UNIUSDT",
}

# BLACKLIST: Never trade (stablecoins, wrapped, collapsed)
BLACKLIST = {
    # Stablecoins
    "USDTUSDT", "USDCUSDT", "BUSDUSDT", "DAIUSDT", "TUSDUSDT",
    "FDUSDUSDT", "PYUSDUSDT", "EURUSDT", "USDPUSDT", "USD1USDT",
    # Wrapped / Synthetic
    "WBTCUSDT", "WETHUSDT", "STETHUSDT",
    # Gold-backed (low volatility)
    "PAXGUSDT", "XAUTUSDT",
    # Collapsed / High risk
    "LUNAUSDT", "USTCUSDT", "LUNCUSDT",
    # Meme pump-dump (known scams)
    "PUMPUSDT",
}

# DYNAMIC_LIST config
DYNAMIC_CONFIG = {
    "min_volume_usd": 50_000_000,  # $50M minimum 24h volume
    "max_symbols": 20,             # Maximum symbols in dynamic list
    "update_interval_sec": 3600,   # Update every hour
    "exclude_categories": [],      # Include all categories
}

# HOT_LIST config (RAISED thresholds for quality)
HOT_CONFIG = {
    "ttl_seconds": 900,            # 15 minutes TTL
    "max_entries": 10,             # Maximum HOT symbols
    "min_pump_score": 0.75,        # RAISED from 0.65 - only strong pumps
    "min_buys_per_sec": 50,        # RAISED from 25 - need real volume
    "min_delta_pct": 1.0,          # RAISED from 1.5 - need real movement
    "position_multiplier": 0.5,    # 50% position for HOT
    "timeout_override": 30,        # 30 second timeout for HOT
}

# Trading params by list type
TRADING_PARAMS = {
    ListType.CORE: {
        "position_multiplier": 1.0,
        "timeout_seconds": 60,
        "max_position_usd": 1000,
        "stop_loss_pct": 1.0,
        "take_profit_pct": 2.0,
    },
    ListType.DYNAMIC: {
        "position_multiplier": 1.0,
        "timeout_seconds": 45,
        "max_position_usd": 800,
        "stop_loss_pct": 0.8,
        "take_profit_pct": 1.5,
    },
    ListType.HOT: {
        "position_multiplier": 0.5,
        "timeout_seconds": 30,
        "max_position_usd": 500,
        "stop_loss_pct": 0.6,
        "take_profit_pct": 1.0,
    },
}


@dataclass
class HotEntry:
    """Entry in HOT_LIST with TTL."""
    symbol: str
    added_at: float  # Unix timestamp
    ttl_seconds: int
    pump_score: float
    buys_per_sec: float
    delta_pct: float
    vol_raise_pct: float
    source: str = "pump_detector"

    def is_expired(self) -> bool:
        return time.time() > self.added_at + self.ttl_seconds

    def time_remaining(self) -> int:
        return max(0, int(self.added_at + self.ttl_seconds - time.time()))


@dataclass
class AllowListState:
    """Persistent state for AllowList."""
    dynamic_list: List[str] = field(default_factory=list)
    dynamic_last_update: float = 0
    hot_entries: Dict[str, dict] = field(default_factory=dict)
    manual_additions: List[str] = field(default_factory=list)
    manual_removals: List[str] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=lambda: {
        "signals_processed": 0,
        "hot_additions": 0,
        "hot_expirations": 0,
        "dynamic_updates": 0,
    })

    def save(self):
        """Save state to file."""
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls) -> "AllowListState":
        """Load state from file."""
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                return cls(**data)
            except Exception as e:
                log.warning(f"Failed to load state: {e}")
        return cls()


class ThreeLayerAllowList:
    """
    Three-Layer AllowList for autonomous trading.

    Layers:
    1. CORE_LIST - Permanent blue chips (BTC, ETH, SOL...)
    2. DYNAMIC_LIST - Top 20 by volume, updated hourly
    3. HOT_LIST - Pump signals with 15-min TTL

    Features:
    - Auto-cleanup of expired HOT entries
    - Binance API integration for volume data
    - Different trading params per layer
    - Blacklist protection
    """

    def __init__(self):
        self.state = AllowListState.load()
        self.client: Optional[httpx.AsyncClient] = None
        self._all_binance_symbols: Set[str] = set()
        self._initialized = False

    async def initialize(self):
        """Initialize the AllowList system."""
        if self._initialized:
            return

        if httpx:
            self.client = httpx.AsyncClient(timeout=10.0)

        # Load Binance symbols
        await self._load_binance_symbols()

        # Update dynamic list if stale
        if self._is_dynamic_stale():
            await self.update_dynamic_list()

        # Cleanup expired HOT entries
        self._cleanup_hot_list()

        self._initialized = True
        log.info(
            f"AllowList initialized: CORE={len(CORE_LIST)}, "
            f"DYNAMIC={len(self.state.dynamic_list)}, "
            f"HOT={len(self.state.hot_entries)}"
        )

    async def close(self):
        """Cleanup resources."""
        if self.client:
            await self.client.aclose()
        self.state.save()

    async def _load_binance_symbols(self):
        """Load all valid Binance USDT trading pairs."""
        if not self.client:
            return

        try:
            resp = await self.client.get(
                "https://api.binance.com/api/v3/exchangeInfo"
            )
            if resp.status_code == 200:
                data = resp.json()
                self._all_binance_symbols = {
                    s["symbol"] for s in data["symbols"]
                    if s["symbol"].endswith("USDT")
                    and s["status"] == "TRADING"
                }
                log.info(f"Loaded {len(self._all_binance_symbols)} Binance symbols")
        except Exception as e:
            log.error(f"Failed to load Binance symbols: {e}")

    def _is_dynamic_stale(self) -> bool:
        """Check if DYNAMIC_LIST needs update."""
        age = time.time() - self.state.dynamic_last_update
        return age > DYNAMIC_CONFIG["update_interval_sec"]

    async def update_dynamic_list(self) -> List[str]:
        """
        Update DYNAMIC_LIST from Binance 24h volume data.

        Returns list of top symbols by volume ($50M+).
        """
        if not self.client:
            log.warning("No HTTP client - cannot update dynamic list")
            return self.state.dynamic_list

        try:
            resp = await self.client.get(
                "https://api.binance.com/api/v3/ticker/24hr"
            )
            if resp.status_code != 200:
                log.error(f"Binance API error: {resp.status_code}")
                return self.state.dynamic_list

            tickers = resp.json()

            # Filter and sort by volume
            valid_tickers = []
            for t in tickers:
                symbol = t["symbol"]
                if not symbol.endswith("USDT"):
                    continue
                if symbol in BLACKLIST:
                    continue
                if symbol in CORE_LIST:
                    continue  # Already in CORE

                volume_usd = float(t["quoteVolume"])
                if volume_usd < DYNAMIC_CONFIG["min_volume_usd"]:
                    continue

                valid_tickers.append({
                    "symbol": symbol,
                    "volume_usd": volume_usd,
                    "price_change_pct": float(t.get("priceChangePercent", 0)),
                })

            # Sort by volume descending
            valid_tickers.sort(key=lambda x: x["volume_usd"], reverse=True)

            # Take top N
            max_symbols = DYNAMIC_CONFIG["max_symbols"]
            self.state.dynamic_list = [
                t["symbol"] for t in valid_tickers[:max_symbols]
            ]
            self.state.dynamic_last_update = time.time()
            self.state.stats["dynamic_updates"] += 1
            self.state.save()

            log.info(
                f"DYNAMIC_LIST updated: {len(self.state.dynamic_list)} symbols | "
                f"Top: {', '.join(self.state.dynamic_list[:5])}"
            )

            return self.state.dynamic_list

        except Exception as e:
            log.error(f"Failed to update dynamic list: {e}")
            return self.state.dynamic_list

    def _cleanup_hot_list(self):
        """Remove expired entries from HOT_LIST."""
        expired = []
        for symbol, entry_data in list(self.state.hot_entries.items()):
            entry = HotEntry(**entry_data)
            if entry.is_expired():
                expired.append(symbol)
                del self.state.hot_entries[symbol]
                self.state.stats["hot_expirations"] += 1

        if expired:
            log.info(f"HOT_LIST cleanup: removed {len(expired)} expired entries")
            self.state.save()

    def _calculate_pump_score(self, signal: Dict) -> float:
        """
        Calculate pump score from signal metrics.

        Score components:
        - buys_per_sec: 35% weight (max at 100/s)
        - delta_pct: 35% weight (max at 10%)
        - vol_raise_pct: 30% weight (max at 300%)
        """
        buys = signal.get("buys_per_sec", 0)
        delta = signal.get("delta_pct", 0)
        vol_raise = signal.get("vol_raise_pct", 50)

        # Normalize to 0-1
        buys_score = min(buys / 100, 1.0)
        delta_score = min(delta / 10, 1.0)
        vol_score = min(vol_raise / 300, 1.0)

        # Weighted sum
        pump_score = (
            buys_score * 0.35 +
            delta_score * 0.35 +
            vol_score * 0.30
        )

        return round(pump_score, 3)

    def process_pump_signal(self, signal: Dict) -> Dict:
        """
        Process incoming pump signal.

        Automatically adds to HOT_LIST if criteria met:
        - pump_score >= 0.65 OR
        - (buys >= 25/s AND delta >= 1.5%)

        Returns: {"action": "ADDED_TO_HOT"|"ALREADY_ALLOWED"|"SKIPPED", ...}
        """
        self.state.stats["signals_processed"] += 1

        symbol = signal.get("symbol", "")
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"

        # Check blacklist first
        if symbol in BLACKLIST:
            return {
                "action": "SKIPPED",
                "reason": "BLACKLISTED",
                "symbol": symbol,
            }

        # Check if already in CORE or DYNAMIC
        list_type = self.get_list_type(symbol)
        if list_type in (ListType.CORE, ListType.DYNAMIC):
            return {
                "action": "ALREADY_ALLOWED",
                "list_type": list_type.value,
                "symbol": symbol,
            }

        # Calculate pump score
        pump_score = self._calculate_pump_score(signal)
        buys = signal.get("buys_per_sec", 0)
        delta = signal.get("delta_pct", 0)
        vol_raise = signal.get("vol_raise_pct", 50)

        # Check thresholds
        meets_score = pump_score >= HOT_CONFIG["min_pump_score"]
        meets_alt = (
            buys >= HOT_CONFIG["min_buys_per_sec"] and
            delta >= HOT_CONFIG["min_delta_pct"]
        )

        if not (meets_score or meets_alt):
            return {
                "action": "SKIPPED",
                "reason": f"pump_score={pump_score:.2f} < 0.65",
                "symbol": symbol,
                "pump_score": pump_score,
            }

        # Validate symbol exists on Binance
        if self._all_binance_symbols and symbol not in self._all_binance_symbols:
            return {
                "action": "SKIPPED",
                "reason": "NOT_ON_BINANCE",
                "symbol": symbol,
            }

        # Check HOT_LIST capacity
        self._cleanup_hot_list()
        if len(self.state.hot_entries) >= HOT_CONFIG["max_entries"]:
            # Remove oldest entry
            oldest = min(
                self.state.hot_entries.items(),
                key=lambda x: x[1]["added_at"]
            )
            del self.state.hot_entries[oldest[0]]
            log.info(f"HOT_LIST full - removed oldest: {oldest[0]}")

        # Add to HOT_LIST
        entry = HotEntry(
            symbol=symbol,
            added_at=time.time(),
            ttl_seconds=HOT_CONFIG["ttl_seconds"],
            pump_score=pump_score,
            buys_per_sec=buys,
            delta_pct=delta,
            vol_raise_pct=vol_raise,
            source=signal.get("source", "pump_detector"),
        )

        self.state.hot_entries[symbol] = asdict(entry)
        self.state.stats["hot_additions"] += 1
        self.state.save()

        log.info(
            f"HOT_LIST +{symbol} | score={pump_score:.2f} | "
            f"buys={buys:.0f}/s | delta={delta:.1f}% | TTL=15min"
        )

        return {
            "action": "ADDED_TO_HOT",
            "symbol": symbol,
            "pump_score": pump_score,
            "ttl_seconds": HOT_CONFIG["ttl_seconds"],
            "position_multiplier": HOT_CONFIG["position_multiplier"],
        }

    def get_list_type(self, symbol: str) -> ListType:
        """Get which list a symbol belongs to."""
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"

        if symbol in BLACKLIST:
            return ListType.BLACKLIST

        if symbol in CORE_LIST:
            return ListType.CORE

        if symbol in self.state.dynamic_list:
            return ListType.DYNAMIC

        # Check HOT_LIST (with expiry check)
        if symbol in self.state.hot_entries:
            entry = HotEntry(**self.state.hot_entries[symbol])
            if not entry.is_expired():
                return ListType.HOT
            else:
                # Cleanup expired
                del self.state.hot_entries[symbol]
                self.state.stats["hot_expirations"] += 1

        # Check manual additions
        if symbol in self.state.manual_additions:
            return ListType.DYNAMIC  # Treat as dynamic

        return ListType.NONE

    def is_allowed(self, symbol: str) -> bool:
        """Check if symbol is allowed for trading."""
        list_type = self.get_list_type(symbol)
        return list_type not in (ListType.BLACKLIST, ListType.NONE)

    def get_trading_params(self, symbol: str) -> Dict:
        """
        Get trading parameters for symbol based on its list type.

        Returns:
        {
            "list_type": "core"|"dynamic"|"hot",
            "position_multiplier": 0.5-1.0,
            "timeout_seconds": 30-60,
            "max_position_usd": 500-1000,
            "stop_loss_pct": 0.6-1.0,
            "take_profit_pct": 1.0-2.0,
        }
        """
        list_type = self.get_list_type(symbol)

        if list_type in (ListType.BLACKLIST, ListType.NONE):
            return {
                "list_type": list_type.value,
                "allowed": False,
                "reason": "not_in_allowlist",
            }

        params = TRADING_PARAMS.get(list_type, TRADING_PARAMS[ListType.DYNAMIC])
        return {
            "list_type": list_type.value,
            "allowed": True,
            **params,
        }

    def get_all_allowed(self) -> Dict[str, List[str]]:
        """Get all allowed symbols by list type."""
        self._cleanup_hot_list()

        return {
            "core": list(CORE_LIST),
            "dynamic": self.state.dynamic_list.copy(),
            "hot": list(self.state.hot_entries.keys()),
            "manual": self.state.manual_additions.copy(),
        }

    def add_manual(self, symbol: str) -> bool:
        """Manually add symbol to allowlist."""
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"

        if symbol in BLACKLIST:
            log.warning(f"Cannot add blacklisted symbol: {symbol}")
            return False

        if symbol not in self.state.manual_additions:
            self.state.manual_additions.append(symbol)
            self.state.save()
            log.info(f"Manual addition: {symbol}")

        return True

    def remove_manual(self, symbol: str) -> bool:
        """Remove manually added symbol."""
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"

        if symbol in self.state.manual_additions:
            self.state.manual_additions.remove(symbol)
            self.state.save()
            log.info(f"Manual removal: {symbol}")
            return True

        return False

    def get_status(self) -> Dict:
        """Get current AllowList status."""
        self._cleanup_hot_list()

        hot_entries = []
        for symbol, entry_data in self.state.hot_entries.items():
            entry = HotEntry(**entry_data)
            hot_entries.append({
                "symbol": symbol,
                "pump_score": entry.pump_score,
                "time_remaining": entry.time_remaining(),
            })

        return {
            "core_count": len(CORE_LIST),
            "dynamic_count": len(self.state.dynamic_list),
            "hot_count": len(self.state.hot_entries),
            "hot_entries": hot_entries,
            "dynamic_age_sec": int(time.time() - self.state.dynamic_last_update),
            "dynamic_stale": self._is_dynamic_stale(),
            "stats": self.state.stats,
        }

    def export_flat_list(self) -> List[str]:
        """Export flat list of all allowed symbols (for AllowList.txt)."""
        self._cleanup_hot_list()

        all_symbols = set(CORE_LIST)
        all_symbols.update(self.state.dynamic_list)
        all_symbols.update(self.state.hot_entries.keys())
        all_symbols.update(self.state.manual_additions)

        return sorted(all_symbols)


# === SINGLETON ===
_instance: Optional[ThreeLayerAllowList] = None


def get_allowlist() -> ThreeLayerAllowList:
    """Get singleton AllowList instance."""
    global _instance
    if _instance is None:
        _instance = ThreeLayerAllowList()
    return _instance


async def get_allowlist_async() -> ThreeLayerAllowList:
    """Get initialized singleton AllowList instance."""
    allowlist = get_allowlist()
    if not allowlist._initialized:
        await allowlist.initialize()
    return allowlist


# === CLI ===

async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Three-Layer AllowList Manager")
    parser.add_argument("--show", action="store_true", help="Show current status")
    parser.add_argument("--update", action="store_true", help="Update DYNAMIC_LIST now")
    parser.add_argument("--export", type=str, help="Export to AllowList.txt")
    parser.add_argument("--add", type=str, help="Add symbols manually (comma-separated)")
    parser.add_argument("--remove", type=str, help="Remove manual symbols")
    parser.add_argument("--test-signal", type=str, help="Test signal: SYMBOL,buys,delta,vol")
    parser.add_argument("--daemon", action="store_true", help="Run as daemon (hourly updates)")

    args = parser.parse_args()

    allowlist = ThreeLayerAllowList()
    await allowlist.initialize()

    if args.show:
        status = allowlist.get_status()
        print("\n" + "=" * 60)
        print("THREE-LAYER ALLOWLIST STATUS")
        print("=" * 60)
        print(f"\nCORE_LIST ({status['core_count']} symbols):")
        print(f"  {', '.join(sorted(CORE_LIST)[:10])}...")
        print(f"\nDYNAMIC_LIST ({status['dynamic_count']} symbols):")
        print(f"  {', '.join(allowlist.state.dynamic_list[:10])}...")
        print(f"  Age: {status['dynamic_age_sec'] // 60} min (stale: {status['dynamic_stale']})")
        print(f"\nHOT_LIST ({status['hot_count']} symbols):")
        for entry in status["hot_entries"]:
            print(f"  {entry['symbol']}: score={entry['pump_score']:.2f}, TTL={entry['time_remaining']}s")
        print(f"\nStats: {status['stats']}")
        print("=" * 60)

    elif args.update:
        print("Updating DYNAMIC_LIST from Binance...")
        symbols = await allowlist.update_dynamic_list()
        print(f"Updated: {len(symbols)} symbols")
        for i, s in enumerate(symbols, 1):
            print(f"  {i:2}. {s}")

    elif args.export:
        symbols = allowlist.export_flat_list()
        export_path = Path(args.export)
        export_path.write_text("\n".join(symbols) + "\n", encoding="utf-8")
        print(f"Exported {len(symbols)} symbols to {export_path}")

    elif args.add:
        for symbol in args.add.upper().split(","):
            symbol = symbol.strip()
            if allowlist.add_manual(symbol):
                print(f"Added: {symbol}")

    elif args.remove:
        for symbol in args.remove.upper().split(","):
            symbol = symbol.strip()
            if allowlist.remove_manual(symbol):
                print(f"Removed: {symbol}")

    elif args.test_signal:
        parts = args.test_signal.split(",")
        if len(parts) >= 4:
            signal = {
                "symbol": parts[0].upper(),
                "buys_per_sec": float(parts[1]),
                "delta_pct": float(parts[2]),
                "vol_raise_pct": float(parts[3]),
            }
            result = allowlist.process_pump_signal(signal)
            print(f"\nSignal: {signal}")
            print(f"Result: {json.dumps(result, indent=2)}")
        else:
            print("Format: SYMBOL,buys_per_sec,delta_pct,vol_raise_pct")

    elif args.daemon:
        print("Starting AllowList daemon (hourly updates)...")
        try:
            while True:
                if allowlist._is_dynamic_stale():
                    await allowlist.update_dynamic_list()
                allowlist._cleanup_hot_list()
                await asyncio.sleep(60)  # Check every minute
        except KeyboardInterrupt:
            print("\nDaemon stopped")

    else:
        parser.print_help()

    await allowlist.close()


if __name__ == "__main__":
    asyncio.run(main())
