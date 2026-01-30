# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-01-30 18:00:00 UTC
# Purpose: Dynamic AllowList from TradingView (Gainers + Most Traded)
# Version: 1.0
# === END SIGNATURE ===
"""
DYNAMIC ALLOWLIST FROM TRADINGVIEW v1.0

Автоматически обновляет AllowList на основе данных с TradingView:
- https://ru.tradingview.com/markets/cryptocurrencies/prices-gainers/
- https://ru.tradingview.com/markets/cryptocurrencies/prices-most-traded/

═══════════════════════════════════════════════════════════════════════════════
ЛОГИКА:
═══════════════════════════════════════════════════════════════════════════════

1. GAINERS (растущие):
   - Берём топ-20 монет с ростом > 5% за 24ч
   - Исключаем stablecoins и heavy coins
   - Добавляем в HOT_LIST с TTL=1h

2. MOST TRADED (самые торгуемые):
   - Берём топ-50 по объёму
   - Исключаем stablecoins и heavy coins
   - Добавляем в DYNAMIC_LIST

3. TECH RATING:
   - "Активно покупать" / "Купить" → приоритет
   - "Продать" / "Активно продавать" → пропускаем

═══════════════════════════════════════════════════════════════════════════════
ОБНОВЛЕНИЕ:
═══════════════════════════════════════════════════════════════════════════════

- GAINERS: каждые 15 минут (быстро меняются)
- MOST_TRADED: каждый час (стабильнее)
- Данные кэшируются локально

═══════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Set

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)-12s | %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger("TV_ALLOWLIST")


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

STATE_DIR = Path("state/allowlist")
STATE_DIR.mkdir(parents=True, exist_ok=True)

# Output files
GAINERS_FILE = STATE_DIR / "tradingview_gainers.json"
MOST_TRADED_FILE = STATE_DIR / "tradingview_most_traded.json"
DYNAMIC_ALLOWLIST_FILE = STATE_DIR / "dynamic_allowlist.json"
HOT_LIST_FILE = STATE_DIR / "hot_list.json"

# TradingView API (unofficial - scraping alternative)
# Note: TradingView doesn't have official API, we use their internal endpoints
TV_SCANNER_URL = "https://scanner.tradingview.com/crypto/scan"

# Update intervals
GAINERS_UPDATE_INTERVAL = 900      # 15 minutes
MOST_TRADED_UPDATE_INTERVAL = 3600 # 1 hour


# ══════════════════════════════════════════════════════════════════════════════
# BLACKLISTS
# ══════════════════════════════════════════════════════════════════════════════

# Heavy coins - не торгуем (по указанию пользователя)
HEAVY_COINS = {
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
}

# Stablecoins - не торгуем
STABLECOINS = {
    "USDTUSDT", "USDCUSDT", "DAIUSDT", "BUSDUSDT", "TUSDUSDT",
    "FDUSDUSDT", "PAXGUSDT", "XAUTUSDT", "PYUSDUSDT", "USDSUSDT",
    "EURCUSDT", "RLUSDUSTUSDT", "USD1USDT", "CRVUSDUSDT", "USDEUSDT",
}

# Wrapped tokens - не торгуем
WRAPPED_TOKENS = {
    "WETHUSDT", "WBTCUSDT", "CBBTCUSDT",
}

# Problematic from TESTNET
PROBLEMATIC_COINS = {
    "XVSUSDT", "DUSKUSDT", "KITEUSDT",
}

# Combined blacklist
FULL_BLACKLIST = HEAVY_COINS | STABLECOINS | WRAPPED_TOKENS | PROBLEMATIC_COINS


# ══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CoinData:
    """Данные о монете с TradingView."""
    symbol: str              # BTCUSDT
    name: str                # Bitcoin
    price: float             # 83514.40
    change_24h: float        # -4.74%
    volume_24h: float        # 83.83B
    market_cap: float        # 1.67T
    tech_rating: str         # "Активно покупать", "Купить", "Продать", etc.
    category: str            # "Мемы", "DeFi", etc.
    rank: int                # 1-1000
    
    def is_bullish(self) -> bool:
        """Проверить бычий ли тех. рейтинг."""
        return self.tech_rating in ["Активно покупать", "Купить"]
    
    def is_gainer(self) -> bool:
        """Проверить растёт ли монета."""
        return self.change_24h > 0


# ══════════════════════════════════════════════════════════════════════════════
# TRADINGVIEW SCANNER
# ══════════════════════════════════════════════════════════════════════════════

class TradingViewScanner:
    """
    Сканер TradingView для получения данных о криптовалютах.
    
    Использует внутренний API TradingView (scanner.tradingview.com).
    """
    
    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._cache_time: Dict[str, float] = {}
        
    async def get_gainers(self, limit: int = 50) -> List[CoinData]:
        """
        Получить топ растущих монет.
        
        Returns:
            List of CoinData sorted by 24h change (descending)
        """
        coins = await self._fetch_coins(sort_by="change", sort_order="desc", limit=limit)
        
        # Filter only gainers (positive change)
        gainers = [c for c in coins if c.change_24h > 0]
        
        return gainers
        
    async def get_most_traded(self, limit: int = 100) -> List[CoinData]:
        """
        Получить самые торгуемые монеты.
        
        Returns:
            List of CoinData sorted by volume (descending)
        """
        coins = await self._fetch_coins(sort_by="volume", sort_order="desc", limit=limit)
        
        return coins
        
    async def _fetch_coins(
        self, 
        sort_by: str = "volume",
        sort_order: str = "desc",
        limit: int = 100
    ) -> List[CoinData]:
        """Fetch coins from TradingView scanner API."""
        
        if not HTTPX_AVAILABLE:
            log.warning("httpx not available, using fallback data")
            return self._get_fallback_data()
            
        # TradingView scanner request
        payload = {
            "filter": [
                {"left": "exchange", "operation": "equal", "right": "BINANCE"},
                {"left": "is_primary", "operation": "equal", "right": True},
            ],
            "options": {"lang": "en"},
            "markets": ["crypto"],
            "symbols": {"query": {"types": []}, "tickers": []},
            "columns": [
                "base_currency_logoid",
                "currency_logoid", 
                "name",
                "close",
                "change",
                "volume",
                "market_cap_basic",
                "Recommend.All",
                "sector",
            ],
            "sort": {
                "sortBy": "volume" if sort_by == "volume" else "change",
                "sortOrder": sort_order,
            },
            "range": [0, limit],
        }
        
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    TV_SCANNER_URL,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    }
                )
                
                if resp.status_code != 200:
                    log.error(f"TradingView API error: {resp.status_code}")
                    return self._get_fallback_data()
                    
                data = resp.json()
                
                coins = []
                for item in data.get("data", []):
                    try:
                        symbol_raw = item.get("s", "")
                        # Extract symbol: "BINANCE:BTCUSDT" -> "BTCUSDT"
                        symbol = symbol_raw.split(":")[-1] if ":" in symbol_raw else symbol_raw
                        
                        if not symbol.endswith("USDT"):
                            continue
                            
                        d = item.get("d", [])
                        if len(d) < 8:
                            continue
                            
                        # Parse recommendation
                        rec_value = d[7] if d[7] else 0
                        if rec_value >= 0.5:
                            tech_rating = "Активно покупать"
                        elif rec_value >= 0.1:
                            tech_rating = "Купить"
                        elif rec_value >= -0.1:
                            tech_rating = "Нейтрально"
                        elif rec_value >= -0.5:
                            tech_rating = "Продать"
                        else:
                            tech_rating = "Активно продавать"
                            
                        coin = CoinData(
                            symbol=symbol,
                            name=d[2] if d[2] else symbol.replace("USDT", ""),
                            price=float(d[3]) if d[3] else 0,
                            change_24h=float(d[4]) if d[4] else 0,
                            volume_24h=float(d[5]) if d[5] else 0,
                            market_cap=float(d[6]) if d[6] else 0,
                            tech_rating=tech_rating,
                            category=d[8] if len(d) > 8 and d[8] else "Unknown",
                            rank=len(coins) + 1,
                        )
                        coins.append(coin)
                        
                    except Exception as e:
                        log.debug(f"Error parsing coin: {e}")
                        continue
                        
                log.info(f"Fetched {len(coins)} coins from TradingView")
                return coins
                
        except Exception as e:
            log.error(f"TradingView fetch error: {e}")
            return self._get_fallback_data()
            
    def _get_fallback_data(self) -> List[CoinData]:
        """Fallback data when API is unavailable."""
        # Based on user's provided data
        fallback = [
            CoinData("ENSOUSDT", "Enso", 1.59, 28.42, 249e6, 32.8e6, "Активно покупать", "DeFi", 1),
            CoinData("BULLAUSDT", "Bulla", 0.099, 22.06, 83e6, 28e6, "Активно покупать", "Мемы", 2),
            CoinData("WMTXUSDT", "World Mobile", 0.078, 10.03, 135e6, 65e6, "Купить", "DePIN", 3),
            CoinData("0GUSDT", "0G", 0.83, 9.61, 149e6, 178e6, "Продать", "DePIN", 4),
            CoinData("NOMUSDT", "Nomina", 0.01, 8.46, 66e6, 29e6, "Купить", "DeFi", 5),
            CoinData("SOMIUSDT", "Somnia", 0.27, 5.34, 123e6, 59e6, "Купить", "L1", 6),
            CoinData("SENTUSDT", "Sentient", 0.036, 5.40, 829e6, 262e6, "Нейтрально", "AI", 7),
            CoinData("GWEIUSDT", "ETHGas", 0.04, 4.14, 109e6, 71e6, "Активно покупать", "DeFi", 8),
            CoinData("FOGOUSDT", "Fogo", 0.038, 3.24, 203e6, 143e6, "Купить", "L1", 9),
            CoinData("MONUSDT", "Monad", 0.021, 3.51, 128e6, 225e6, "Продать", "L1", 10),
            CoinData("ZROUSDT", "LayerZero", 1.99, 2.98, 158e6, 590e6, "Купить", "Interop", 11),
            CoinData("AXSUSDT", "Axie Infinity", 2.16, 0.90, 219e6, 364e6, "Купить", "Gaming", 12),
            CoinData("PEPEUSDT", "Pepe", 0.0000046, -3.10, 555e6, 1.92e9, "Активно продавать", "Мемы", 13),
            CoinData("DOGEUSDT", "Dogecoin", 0.116, -3.00, 2.09e9, 19.5e9, "Продать", "Мемы", 14),
            CoinData("SUIUSDT", "Sui", 1.29, -4.06, 1.27e9, 4.9e9, "Активно продавать", "L1", 15),
            CoinData("AVAXUSDT", "Avalanche", 10.97, -3.52, 623e6, 4.73e9, "Продать", "L1", 16),
            CoinData("LINKUSDT", "Chainlink", 10.77, -5.56, 687e6, 7.62e9, "Активно продавать", "Oracle", 17),
            CoinData("ADAUSDT", "Cardano", 0.327, -4.48, 843e6, 11.8e9, "Активно продавать", "L1", 18),
            CoinData("AAVEUSDT", "Aave", 141.36, -7.04, 460e6, 2.17e9, "Активно продавать", "DeFi", 19),
            CoinData("TRUMPUSDT", "TRUMP", 4.55, -1.35, 232e6, 910e6, "Продать", "Мемы", 20),
        ]
        return fallback


# ══════════════════════════════════════════════════════════════════════════════
# DYNAMIC ALLOWLIST MANAGER
# ══════════════════════════════════════════════════════════════════════════════

class DynamicAllowListManager:
    """
    Менеджер динамического AllowList.
    
    Обновляет списки на основе данных TradingView:
    - HOT_LIST: топ растущие монеты (TTL=1h)
    - DYNAMIC_LIST: топ по объёму (обновление каждый час)
    """
    
    def __init__(self):
        self.scanner = TradingViewScanner()
        
        # Lists
        self.hot_list: Dict[str, Dict] = {}      # symbol -> {expires_at, data}
        self.dynamic_list: Set[str] = set()
        self.gainers_list: List[CoinData] = []
        
        # Timestamps
        self.last_gainers_update = 0
        self.last_traded_update = 0
        
        # Load saved state
        self._load_state()
        
    def _load_state(self):
        """Load saved state from files."""
        # Load HOT list
        if HOT_LIST_FILE.exists():
            try:
                with open(HOT_LIST_FILE) as f:
                    data = json.load(f)
                    # Filter expired
                    now = time.time()
                    self.hot_list = {
                        k: v for k, v in data.items()
                        if v.get("expires_at", 0) > now
                    }
            except:
                pass
                
        # Load DYNAMIC list
        if DYNAMIC_ALLOWLIST_FILE.exists():
            try:
                with open(DYNAMIC_ALLOWLIST_FILE) as f:
                    data = json.load(f)
                    self.dynamic_list = set(data.get("symbols", []))
                    self.last_traded_update = data.get("updated_at", 0)
            except:
                pass
                
    def _save_state(self):
        """Save state to files."""
        # Save HOT list
        with open(HOT_LIST_FILE, 'w') as f:
            json.dump(self.hot_list, f, indent=2)
            
        # Save DYNAMIC list
        with open(DYNAMIC_ALLOWLIST_FILE, 'w') as f:
            json.dump({
                "symbols": list(self.dynamic_list),
                "updated_at": self.last_traded_update,
                "updated_at_str": datetime.now(timezone.utc).isoformat(),
            }, f, indent=2)
            
    async def update_gainers(self) -> List[str]:
        """
        Обновить список растущих монет.
        
        Returns:
            List of new gainers added to HOT list
        """
        now = time.time()
        
        # Check if update needed
        if now - self.last_gainers_update < GAINERS_UPDATE_INTERVAL:
            log.debug("Gainers update skipped (too recent)")
            return []
            
        log.info("Updating gainers from TradingView...")
        
        gainers = await self.scanner.get_gainers(limit=50)
        self.gainers_list = gainers
        
        new_hot = []
        
        for coin in gainers:
            symbol = coin.symbol
            
            # Skip blacklisted
            if symbol in FULL_BLACKLIST:
                continue
                
            # Only add if:
            # 1. Change > 5%
            # 2. Tech rating is bullish OR neutral
            # 3. Volume > $10M
            if (coin.change_24h >= 5.0 and 
                coin.tech_rating in ["Активно покупать", "Купить", "Нейтрально"] and
                coin.volume_24h >= 10_000_000):
                
                # Add to HOT list with 1 hour TTL
                if symbol not in self.hot_list:
                    new_hot.append(symbol)
                    
                self.hot_list[symbol] = {
                    "expires_at": now + 3600,  # 1 hour TTL
                    "change_24h": coin.change_24h,
                    "volume_24h": coin.volume_24h,
                    "tech_rating": coin.tech_rating,
                    "added_at": now,
                    "reason": f"Gainer +{coin.change_24h:.1f}%",
                }
                
        self.last_gainers_update = now
        self._save_state()
        
        if new_hot:
            log.info(f"🔥 Added {len(new_hot)} new HOT coins: {', '.join(new_hot)}")
            
        # Save gainers to file
        with open(GAINERS_FILE, 'w') as f:
            json.dump({
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "gainers": [asdict(c) for c in gainers[:20]],
            }, f, indent=2)
            
        return new_hot
        
    async def update_most_traded(self) -> Set[str]:
        """
        Обновить список самых торгуемых монет.
        
        Returns:
            Set of symbols in DYNAMIC list
        """
        now = time.time()
        
        # Check if update needed
        if now - self.last_traded_update < MOST_TRADED_UPDATE_INTERVAL:
            log.debug("Most traded update skipped (too recent)")
            return self.dynamic_list
            
        log.info("Updating most traded from TradingView...")
        
        coins = await self.scanner.get_most_traded(limit=100)
        
        # Build new dynamic list
        new_dynamic = set()
        
        for coin in coins:
            symbol = coin.symbol
            
            # Skip blacklisted
            if symbol in FULL_BLACKLIST:
                continue
                
            # Only add if volume > $50M
            if coin.volume_24h >= 50_000_000:
                new_dynamic.add(symbol)
                
        self.dynamic_list = new_dynamic
        self.last_traded_update = now
        self._save_state()
        
        log.info(f"📊 Updated DYNAMIC list: {len(new_dynamic)} coins")
        
        # Save to file
        with open(MOST_TRADED_FILE, 'w') as f:
            json.dump({
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "most_traded": [asdict(c) for c in coins[:50]],
            }, f, indent=2)
            
        return new_dynamic
        
    async def update_all(self):
        """Update both lists."""
        await self.update_gainers()
        await self.update_most_traded()
        
    def cleanup_expired(self):
        """Remove expired entries from HOT list."""
        now = time.time()
        expired = [k for k, v in self.hot_list.items() if v.get("expires_at", 0) < now]
        
        for symbol in expired:
            del self.hot_list[symbol]
            log.info(f"❄️ Expired from HOT: {symbol}")
            
        if expired:
            self._save_state()
            
    def is_allowed(self, symbol: str) -> tuple:
        """
        Check if symbol is in any allowed list.
        
        Returns:
            (allowed: bool, list_name: str, multiplier: float)
        """
        if not symbol.endswith("USDT"):
            symbol += "USDT"
            
        # Cleanup expired first
        self.cleanup_expired()
        
        # Check blacklist
        if symbol in FULL_BLACKLIST:
            return False, "blacklist", 0
            
        # Check HOT list (highest priority, 50% position)
        if symbol in self.hot_list:
            return True, "hot", 0.5
            
        # Check DYNAMIC list (100% position)
        if symbol in self.dynamic_list:
            return True, "dynamic", 1.0
            
        return False, "none", 0
        
    def get_hot_list(self) -> List[str]:
        """Get current HOT list."""
        self.cleanup_expired()
        return list(self.hot_list.keys())
        
    def get_dynamic_list(self) -> List[str]:
        """Get current DYNAMIC list."""
        return list(self.dynamic_list)
        
    def get_status(self) -> Dict:
        """Get current status."""
        self.cleanup_expired()
        return {
            "hot_count": len(self.hot_list),
            "dynamic_count": len(self.dynamic_list),
            "hot_list": self.get_hot_list(),
            "last_gainers_update": datetime.fromtimestamp(self.last_gainers_update).isoformat() if self.last_gainers_update else None,
            "last_traded_update": datetime.fromtimestamp(self.last_traded_update).isoformat() if self.last_traded_update else None,
        }


# ══════════════════════════════════════════════════════════════════════════════
# DAEMON
# ══════════════════════════════════════════════════════════════════════════════

class TradingViewAllowListDaemon:
    """
    Демон для автоматического обновления AllowList.
    
    Запускать как фоновый процесс.
    """
    
    def __init__(self):
        self.manager = DynamicAllowListManager()
        self.running = False
        
    async def run(self):
        """Run daemon loop."""
        self.running = True
        log.info("🚀 TradingView AllowList Daemon started")
        
        while self.running:
            try:
                # Update lists
                await self.manager.update_all()
                
                # Log status
                status = self.manager.get_status()
                log.info(
                    f"📊 Status: HOT={status['hot_count']} | "
                    f"DYNAMIC={status['dynamic_count']}"
                )
                
                if status['hot_list']:
                    log.info(f"🔥 HOT: {', '.join(status['hot_list'][:10])}")
                    
            except Exception as e:
                log.error(f"Daemon error: {e}")
                
            # Sleep until next update
            await asyncio.sleep(60)  # Check every minute
            
    def stop(self):
        """Stop daemon."""
        self.running = False
        log.info("Daemon stopped")


# ══════════════════════════════════════════════════════════════════════════════
# SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_manager: Optional[DynamicAllowListManager] = None

def get_manager() -> DynamicAllowListManager:
    global _manager
    if _manager is None:
        _manager = DynamicAllowListManager()
    return _manager


# ══════════════════════════════════════════════════════════════════════════════
# INTEGRATION FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def is_tradingview_allowed(symbol: str) -> tuple:
    """
    Integration function for pump_detector.
    
    Returns:
        (allowed: bool, list_name: str, multiplier: float)
    """
    manager = get_manager()
    return manager.is_allowed(symbol)


async def force_update():
    """Force update all lists now."""
    manager = get_manager()
    await manager.update_all()


def get_current_hot() -> List[str]:
    """Get current HOT list."""
    manager = get_manager()
    return manager.get_hot_list()


def get_current_dynamic() -> List[str]:
    """Get current DYNAMIC list."""
    manager = get_manager()
    return manager.get_dynamic_list()


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="TradingView Dynamic AllowList")
    parser.add_argument("--update", action="store_true", help="Force update now")
    parser.add_argument("--status", action="store_true", help="Show current status")
    parser.add_argument("--daemon", action="store_true", help="Run as daemon")
    parser.add_argument("--gainers", action="store_true", help="Show current gainers")
    
    args = parser.parse_args()
    
    manager = get_manager()
    
    if args.update:
        print("Updating from TradingView...")
        await manager.update_all()
        status = manager.get_status()
        print(f"\n✅ Updated!")
        print(f"HOT: {status['hot_count']} coins")
        print(f"DYNAMIC: {status['dynamic_count']} coins")
        
        if status['hot_list']:
            print(f"\n🔥 HOT LIST:")
            for symbol in status['hot_list']:
                data = manager.hot_list.get(symbol, {})
                print(f"  • {symbol}: +{data.get('change_24h', 0):.1f}% | {data.get('tech_rating', '?')}")
                
    elif args.status:
        status = manager.get_status()
        print("=" * 60)
        print("TRADINGVIEW DYNAMIC ALLOWLIST STATUS")
        print("=" * 60)
        print(f"HOT list: {status['hot_count']} coins")
        print(f"DYNAMIC list: {status['dynamic_count']} coins")
        print(f"Last gainers update: {status['last_gainers_update']}")
        print(f"Last traded update: {status['last_traded_update']}")
        
        if status['hot_list']:
            print(f"\n🔥 HOT:")
            for s in status['hot_list']:
                print(f"  • {s}")
                
    elif args.gainers:
        print("Fetching gainers from TradingView...")
        gainers = await manager.scanner.get_gainers(limit=20)
        
        print("\n" + "=" * 70)
        print("TOP GAINERS (24h)")
        print("=" * 70)
        
        for coin in gainers:
            emoji = "🚀" if coin.change_24h >= 10 else "📈" if coin.change_24h >= 5 else "➡️"
            rating_emoji = "✅" if coin.is_bullish() else "⚠️"
            blacklisted = "❌" if coin.symbol in FULL_BLACKLIST else ""
            
            print(
                f"{emoji} {coin.symbol:12} | "
                f"+{coin.change_24h:6.2f}% | "
                f"${coin.volume_24h/1e6:8.1f}M | "
                f"{rating_emoji} {coin.tech_rating:18} | "
                f"{blacklisted}"
            )
            
    elif args.daemon:
        daemon = TradingViewAllowListDaemon()
        await daemon.run()
        
    else:
        parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())
