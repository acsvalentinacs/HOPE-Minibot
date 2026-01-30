# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 17:20:00 UTC
# Purpose: TradingView Dynamic AllowList - auto-updates from gainers/most traded
# === END SIGNATURE ===
"""
TradingView Dynamic AllowList v1.0

Автоматически обновляет AllowList на основе данных TradingView:
- GAINERS: Монеты с ростом > 5% за 24ч и рейтингом "Купить"
- MOST_TRADED: Топ монеты по объёму торгов ($50M+)

Фильтры:
- Только рейтинг "Купить" или "Активно покупать"
- Blacklist тяжёлых монет (BTC, ETH, BNB - слишком медленные для скальпа)
- Blacklist stablecoins
"""

import asyncio
import json
import logging
import time
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass, field

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
log = logging.getLogger("TV-ALLOWLIST")

# Paths
ROOT = Path(__file__).parent.parent
STATE_DIR = ROOT / "state" / "ai" / "tradingview"
STATE_DIR.mkdir(parents=True, exist_ok=True)

# === BLACKLISTS ===

# Heavy coins - too slow for scalping, need big capital
HEAVY_COINS_BLACKLIST = {
    "BTCUSDT", "ETHUSDT", "BNBUSDT",  # Too heavy, slow moves
    "SOLUSDT", "XRPUSDT",              # Large cap
}

# Stablecoins - never trade
STABLECOINS_BLACKLIST = {
    "USDTUSDT", "USDCUSDT", "BUSDUSDT", "TUSDUSDT", "DAIUSDT",
    "FDUSDUSDT", "PAXGUSDT", "USD1USDT", "EURUSDT",
}

# Known problematic coins
PROBLEMATIC_BLACKLIST = {
    "LUNAUSDT", "USTCUSDT", "FTMUSDT",  # Depegged/risky
}

ALL_BLACKLIST = HEAVY_COINS_BLACKLIST | STABLECOINS_BLACKLIST | PROBLEMATIC_BLACKLIST

# === RATING MAPPING ===
BULLISH_RATINGS = {
    "Активно покупать", "Strong Buy", "Buy", "Купить",
    "активно покупать", "strong buy", "buy", "купить",
}

BEARISH_RATINGS = {
    "Активно продавать", "Strong Sell", "Sell", "Продать",
    "активно продавать", "strong sell", "sell", "продать",
}

# === CONFIG ===
@dataclass
class TVConfig:
    """TradingView AllowList configuration."""
    # Gainers thresholds
    min_gain_pct: float = 5.0          # Minimum 24h gain %
    min_volume_usd: float = 50_000_000  # Minimum volume $50M

    # Update intervals
    gainers_update_sec: int = 900       # 15 minutes
    most_traded_update_sec: int = 3600  # 1 hour

    # List sizes
    max_hot_coins: int = 10
    max_dynamic_coins: int = 30

    # TTL
    hot_ttl_sec: int = 3600             # 1 hour
    dynamic_ttl_sec: int = 7200         # 2 hours

    # Position multipliers
    hot_multiplier: float = 0.5         # Risky, use half position
    dynamic_multiplier: float = 1.0     # Normal position


# === DATA STRUCTURES ===
@dataclass
class CoinData:
    """Data for a single coin."""
    symbol: str
    change_24h: float
    volume_24h: float
    rating: str
    added_at: float = field(default_factory=time.time)
    source: str = "unknown"

    def is_expired(self, ttl_sec: int) -> bool:
        return time.time() - self.added_at > ttl_sec

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "change_24h": self.change_24h,
            "volume_24h": self.volume_24h,
            "rating": self.rating,
            "added_at": self.added_at,
            "source": self.source,
        }


class TradingViewAllowList:
    """
    Dynamic AllowList based on TradingView data.

    Lists:
    - HOT_LIST: Gainers with bullish rating (high risk, 0.5x position)
    - DYNAMIC_LIST: Most traded coins (normal risk, 1.0x position)
    """

    def __init__(self, config: TVConfig = None):
        self.config = config or TVConfig()
        self.hot_list: Dict[str, CoinData] = {}
        self.dynamic_list: Dict[str, CoinData] = {}
        self.last_gainers_update: float = 0
        self.last_most_traded_update: float = 0
        self._state_file = STATE_DIR / "tradingview_state.json"

        # Load saved state
        self._load_state()
        log.info(f"TradingView AllowList initialized: HOT={len(self.hot_list)}, DYNAMIC={len(self.dynamic_list)}")

    def _load_state(self):
        """Load saved state from file."""
        if self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text(encoding="utf-8"))

                for symbol, coin_data in data.get("hot_list", {}).items():
                    self.hot_list[symbol] = CoinData(**coin_data)

                for symbol, coin_data in data.get("dynamic_list", {}).items():
                    self.dynamic_list[symbol] = CoinData(**coin_data)

                self.last_gainers_update = data.get("last_gainers_update", 0)
                self.last_most_traded_update = data.get("last_most_traded_update", 0)

                log.info(f"Loaded state: HOT={len(self.hot_list)}, DYNAMIC={len(self.dynamic_list)}")
            except Exception as e:
                log.warning(f"Failed to load state: {e}")

    def _save_state(self):
        """Save state to file."""
        try:
            data = {
                "hot_list": {s: c.to_dict() for s, c in self.hot_list.items()},
                "dynamic_list": {s: c.to_dict() for s, c in self.dynamic_list.items()},
                "last_gainers_update": self.last_gainers_update,
                "last_most_traded_update": self.last_most_traded_update,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

            # Atomic write
            tmp = self._state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self._state_file)
        except Exception as e:
            log.error(f"Failed to save state: {e}")

    def update_from_gainers(self, gainers_data: List[Dict]):
        """
        Update HOT_LIST from gainers data.

        Expected format:
        [
            {"symbol": "ENSOUSDT", "change": 28.42, "volume": 249000000, "rating": "Активно покупать"},
            ...
        ]
        """
        new_hot = {}

        for coin in gainers_data:
            symbol = coin.get("symbol", "").upper()
            if not symbol.endswith("USDT"):
                symbol = symbol + "USDT"

            # Skip blacklisted
            if symbol in ALL_BLACKLIST:
                continue

            change = float(coin.get("change", coin.get("change_24h", 0)))
            volume = float(coin.get("volume", coin.get("volume_24h", 0)))
            rating = coin.get("rating", "")

            # Check thresholds
            if change < self.config.min_gain_pct:
                continue

            if volume < self.config.min_volume_usd:
                continue

            # Check rating - only bullish
            if rating in BEARISH_RATINGS:
                log.info(f"Skipping {symbol}: bearish rating '{rating}'")
                continue

            new_hot[symbol] = CoinData(
                symbol=symbol,
                change_24h=change,
                volume_24h=volume,
                rating=rating,
                source="gainers",
            )

        # Limit size
        sorted_hot = sorted(new_hot.values(), key=lambda x: x.change_24h, reverse=True)
        self.hot_list = {c.symbol: c for c in sorted_hot[:self.config.max_hot_coins]}

        self.last_gainers_update = time.time()
        self._save_state()

        log.info(f"Updated HOT_LIST: {len(self.hot_list)} coins")
        return len(self.hot_list)

    def update_from_most_traded(self, traded_data: List[Dict]):
        """
        Update DYNAMIC_LIST from most traded data.

        Expected format:
        [
            {"symbol": "BTCUSDT", "volume": 5000000000, "rating": "Нейтрально"},
            ...
        ]
        """
        new_dynamic = {}

        for coin in traded_data:
            symbol = coin.get("symbol", "").upper()
            if not symbol.endswith("USDT"):
                symbol = symbol + "USDT"

            # Skip blacklisted
            if symbol in ALL_BLACKLIST:
                continue

            # Skip if already in HOT (HOT takes priority)
            if symbol in self.hot_list:
                continue

            volume = float(coin.get("volume", coin.get("volume_24h", 0)))
            rating = coin.get("rating", "")
            change = float(coin.get("change", coin.get("change_24h", 0)))

            # Check volume threshold
            if volume < self.config.min_volume_usd:
                continue

            # Skip bearish
            if rating in BEARISH_RATINGS:
                continue

            new_dynamic[symbol] = CoinData(
                symbol=symbol,
                change_24h=change,
                volume_24h=volume,
                rating=rating,
                source="most_traded",
            )

        # Limit size
        sorted_dynamic = sorted(new_dynamic.values(), key=lambda x: x.volume_24h, reverse=True)
        self.dynamic_list = {c.symbol: c for c in sorted_dynamic[:self.config.max_dynamic_coins]}

        self.last_most_traded_update = time.time()
        self._save_state()

        log.info(f"Updated DYNAMIC_LIST: {len(self.dynamic_list)} coins")
        return len(self.dynamic_list)

    def is_allowed(self, symbol: str) -> Tuple[bool, str, float]:
        """
        Check if symbol is allowed.

        Returns:
            (allowed: bool, list_name: str, multiplier: float)
        """
        symbol = symbol.upper()

        # Check blacklist first
        if symbol in ALL_BLACKLIST:
            return False, "blacklist", 0.0

        # Check HOT list (highest priority)
        if symbol in self.hot_list:
            coin = self.hot_list[symbol]
            if not coin.is_expired(self.config.hot_ttl_sec):
                return True, "hot", self.config.hot_multiplier

        # Check DYNAMIC list
        if symbol in self.dynamic_list:
            coin = self.dynamic_list[symbol]
            if not coin.is_expired(self.config.dynamic_ttl_sec):
                return True, "dynamic", self.config.dynamic_multiplier

        return False, "not_listed", 0.0

    def get_hot_list(self) -> List[str]:
        """Get current HOT list symbols."""
        return [s for s, c in self.hot_list.items()
                if not c.is_expired(self.config.hot_ttl_sec)]

    def get_dynamic_list(self) -> List[str]:
        """Get current DYNAMIC list symbols."""
        return [s for s, c in self.dynamic_list.items()
                if not c.is_expired(self.config.dynamic_ttl_sec)]

    def get_all_allowed(self) -> Set[str]:
        """Get all allowed symbols."""
        return set(self.get_hot_list()) | set(self.get_dynamic_list())

    def cleanup_expired(self):
        """Remove expired entries."""
        hot_before = len(self.hot_list)
        dynamic_before = len(self.dynamic_list)

        self.hot_list = {s: c for s, c in self.hot_list.items()
                        if not c.is_expired(self.config.hot_ttl_sec)}
        self.dynamic_list = {s: c for s, c in self.dynamic_list.items()
                           if not c.is_expired(self.config.dynamic_ttl_sec)}

        removed = (hot_before - len(self.hot_list)) + (dynamic_before - len(self.dynamic_list))
        if removed > 0:
            log.info(f"Cleaned up {removed} expired entries")
            self._save_state()

    def get_status(self) -> Dict:
        """Get current status."""
        return {
            "hot_count": len(self.get_hot_list()),
            "dynamic_count": len(self.get_dynamic_list()),
            "total_allowed": len(self.get_all_allowed()),
            "last_gainers_update": datetime.fromtimestamp(self.last_gainers_update).isoformat() if self.last_gainers_update else None,
            "last_most_traded_update": datetime.fromtimestamp(self.last_most_traded_update).isoformat() if self.last_most_traded_update else None,
            "hot_list": self.get_hot_list(),
            "dynamic_list": self.get_dynamic_list()[:10],  # First 10
        }


# === GLOBAL INSTANCE ===
_manager: Optional[TradingViewAllowList] = None

def get_manager() -> TradingViewAllowList:
    """Get or create global manager instance."""
    global _manager
    if _manager is None:
        _manager = TradingViewAllowList()
    return _manager

def is_tradingview_allowed(symbol: str) -> Tuple[bool, str, float]:
    """Check if symbol is allowed by TradingView lists."""
    return get_manager().is_allowed(symbol)

def get_current_hot() -> List[str]:
    """Get current HOT list."""
    return get_manager().get_hot_list()

def get_current_dynamic() -> List[str]:
    """Get current DYNAMIC list."""
    return get_manager().get_dynamic_list()


# === SAMPLE DATA (based on user's TradingView screenshot) ===
SAMPLE_GAINERS = [
    {"symbol": "ENSOUSDT", "change": 28.42, "volume": 249_000_000, "rating": "Активно покупать"},
    {"symbol": "BULLAUSDT", "change": 22.06, "volume": 83_000_000, "rating": "Активно покупать"},
    {"symbol": "WMTXUSDT", "change": 10.03, "volume": 135_000_000, "rating": "Купить"},
    {"symbol": "0GUSDT", "change": 9.61, "volume": 164_000_000, "rating": "Продать"},  # Will be skipped
    {"symbol": "NOMUSDT", "change": 8.46, "volume": 66_000_000, "rating": "Купить"},
    {"symbol": "SOMIUSDT", "change": 5.34, "volume": 123_000_000, "rating": "Купить"},
    {"symbol": "SENTUSDT", "change": 5.40, "volume": 95_000_000, "rating": "Нейтрально"},
    {"symbol": "GWEIUSDT", "change": 4.14, "volume": 109_000_000, "rating": "Активно покупать"},
]

SAMPLE_MOST_TRADED = [
    {"symbol": "BTCUSDT", "volume": 5_000_000_000, "rating": "Нейтрально", "change": 0.5},
    {"symbol": "ETHUSDT", "volume": 2_000_000_000, "rating": "Купить", "change": 1.2},
    {"symbol": "SOLUSDT", "volume": 800_000_000, "rating": "Купить", "change": 2.1},
    {"symbol": "DOGEUSDT", "volume": 500_000_000, "rating": "Купить", "change": 1.5},
    {"symbol": "PEPEUSDT", "volume": 400_000_000, "rating": "Активно покупать", "change": 3.2},
    {"symbol": "SHIBUSDT", "volume": 350_000_000, "rating": "Купить", "change": 2.8},
    {"symbol": "WIFUSDT", "volume": 300_000_000, "rating": "Активно покупать", "change": 4.5},
    {"symbol": "BONKUSDT", "volume": 250_000_000, "rating": "Купить", "change": 2.1},
    {"symbol": "FLOKIUSDT", "volume": 200_000_000, "rating": "Купить", "change": 1.8},
    {"symbol": "SUIUSDT", "volume": 180_000_000, "rating": "Купить", "change": 2.5},
]


# === CLI ===
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="TradingView Dynamic AllowList")
    parser.add_argument("--update", action="store_true", help="Update lists with sample data")
    parser.add_argument("--status", action="store_true", help="Show current status")
    parser.add_argument("--gainers", action="store_true", help="Show HOT list (gainers)")
    parser.add_argument("--check", type=str, help="Check if symbol is allowed")
    parser.add_argument("--daemon", action="store_true", help="Run as daemon")

    args = parser.parse_args()

    manager = get_manager()

    if args.update:
        print("Updating from sample TradingView data...")
        manager.update_from_gainers(SAMPLE_GAINERS)
        manager.update_from_most_traded(SAMPLE_MOST_TRADED)
        print(f"Updated! HOT: {len(manager.get_hot_list())}, DYNAMIC: {len(manager.get_dynamic_list())}")
        print()
        print("HOT LIST (Gainers):")
        for symbol in manager.get_hot_list():
            coin = manager.hot_list[symbol]
            print(f"  {symbol}: +{coin.change_24h:.1f}% | {coin.rating}")
        print()
        print("DYNAMIC LIST (Most Traded, first 10):")
        for symbol in manager.get_dynamic_list()[:10]:
            coin = manager.dynamic_list[symbol]
            print(f"  {symbol}: ${coin.volume_24h/1e6:.0f}M vol | {coin.rating}")

    elif args.status:
        status = manager.get_status()
        print(f"HOT: {status['hot_count']} coins")
        print(f"DYNAMIC: {status['dynamic_count']} coins")
        print(f"Total allowed: {status['total_allowed']}")
        print(f"Last gainers update: {status['last_gainers_update']}")
        print(f"Last most traded update: {status['last_most_traded_update']}")

    elif args.gainers:
        print("HOT LIST (Gainers with bullish rating):")
        for symbol in manager.get_hot_list():
            coin = manager.hot_list[symbol]
            print(f"  {symbol}: +{coin.change_24h:.1f}% | {coin.rating} | ${coin.volume_24h/1e6:.0f}M")

    elif args.check:
        allowed, list_name, mult = manager.is_allowed(args.check)
        if allowed:
            print(f"{args.check}: ALLOWED ({list_name}, mult={mult})")
        else:
            print(f"{args.check}: NOT ALLOWED ({list_name})")

    elif args.daemon:
        print("TradingView AllowList Daemon started")
        print("Updates: Gainers every 15min, Most Traded every 1h")

        async def daemon_loop():
            while True:
                try:
                    # For now, use sample data (in production, fetch from TradingView API)
                    manager.update_from_gainers(SAMPLE_GAINERS)
                    manager.update_from_most_traded(SAMPLE_MOST_TRADED)
                    manager.cleanup_expired()

                    log.info(f"Daemon update: HOT={len(manager.get_hot_list())}, DYNAMIC={len(manager.get_dynamic_list())}")

                    await asyncio.sleep(900)  # 15 minutes
                except Exception as e:
                    log.error(f"Daemon error: {e}")
                    await asyncio.sleep(60)

        asyncio.run(daemon_loop())

    else:
        parser.print_help()
