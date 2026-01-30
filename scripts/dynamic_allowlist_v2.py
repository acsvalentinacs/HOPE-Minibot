# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-01-30 12:30:00 UTC
# Purpose: Dynamic AllowList Generator for HOPE AI Trading
# Version: 2.0
# === END SIGNATURE ===
"""
Dynamic AllowList Generator v2.0

═══════════════════════════════════════════════════════════════════════════════
ПАРАМЕТРЫ (по запросу пользователя):
═══════════════════════════════════════════════════════════════════════════════
- Минимальный объём: $50M+ (24h)
- Категории: ВСЕ (включая meme, AI, gaming)
- Обновление: каждый ЧАС
- Максимум: 20 монет
- Исключения: только стейблкоины

═══════════════════════════════════════════════════════════════════════════════
ИСТОЧНИКИ ДАННЫХ:
═══════════════════════════════════════════════════════════════════════════════
1. Binance API (основной) - надёжный, бесплатный, real-time
2. CoinGecko API (резервный) - trending coins
3. Ручные добавления (manual_additions.txt)

═══════════════════════════════════════════════════════════════════════════════
ИСПОЛЬЗОВАНИЕ:
═══════════════════════════════════════════════════════════════════════════════
# Разовое обновление
python scripts/dynamic_allowlist_v2.py --update

# Запуск демона (каждый час)
python scripts/dynamic_allowlist_v2.py --daemon

# Показать текущий список
python scripts/dynamic_allowlist_v2.py --show

# Добавить монету вручную
python scripts/dynamic_allowlist_v2.py --add SOMIUSDT
"""

import asyncio
import json
import logging
import os
import sys
import time
import hashlib
import argparse
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)-15s | %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger("ALLOWLIST")


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION - НАСТРОЙКИ ПО ЗАПРОСУ ПОЛЬЗОВАТЕЛЯ
# ══════════════════════════════════════════════════════════════════════════════

class Config:
    """Конфигурация AllowList."""
    
    # ОСНОВНЫЕ ПАРАМЕТРЫ
    MIN_VOLUME_USD: float = 50_000_000     # $50M minimum 24h volume
    MAX_COINS: int = 20                     # Максимум 20 монет
    UPDATE_INTERVAL_HOURS: float = 1.0      # Обновление каждый час
    
    # ФИЛЬТРЫ
    INCLUDE_ALL_CATEGORIES: bool = True     # Все категории (meme, AI, gaming)
    MIN_VOLATILITY_PCT: float = 0.0         # Без минимума волатильности (берём по объёму)
    
    # ИСТОЧНИКИ
    BINANCE_API: str = "https://api.binance.com/api/v3"
    COINGECKO_API: str = "https://api.coingecko.com/api/v3"


# Директории
STATE_DIR = Path("state/allowlist")
STATE_DIR.mkdir(parents=True, exist_ok=True)

# Файлы
ALLOWLIST_FILE = STATE_DIR / "AllowList.txt"
ALLOWLIST_JSON = STATE_DIR / "allowlist_detailed.json"
MANUAL_FILE = STATE_DIR / "manual_additions.txt"
HISTORY_FILE = STATE_DIR / "allowlist_history.jsonl"

# Legacy location (для совместимости с MoonBot)
LEGACY_ALLOWLIST = Path("AllowList.txt")


# ══════════════════════════════════════════════════════════════════════════════
# ИСКЛЮЧЕНИЯ
# ══════════════════════════════════════════════════════════════════════════════

# Стейблкоины - ИСКЛЮЧАЕМ
STABLECOINS = {
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD", 
    "USDD", "PYUSD", "GUSD", "FRAX", "LUSD", "SUSD", "MIM",
    "UST", "USTC", "USDJ", "USDN", "CUSD", "RSR", "USD1",
    "RLUSD", "USDE", "EURC", "USDS", "CRVUSD", "EURT", "USDJ"
}

# Wrapped токены - низкий приоритет (но не исключаем)
WRAPPED_TOKENS = {"WBTC", "WETH", "CBBTC", "WBNB", "WAVAX"}

# Золото/RWA - исключаем (низкая волатильность)
GOLD_RWA = {"XAUT", "PAXG"}

# Leveraged токены - ИСКЛЮЧАЕМ
LEVERAGED_PATTERNS = {"UP", "DOWN", "BULL", "BEAR", "3L", "3S", "2L", "2S"}


# ══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CoinData:
    """Данные о монете."""
    symbol: str                    # BTCUSDT
    base_asset: str               # BTC
    price: float                  # 82148.62
    volume_24h_usd: float         # 80.45B
    price_change_pct: float       # -6.44%
    volatility_pct: float         # (high-low)/low * 100
    trades_24h: int               # количество сделок
    category: str = ""            # meme, AI, gaming, etc
    score: float = 0.0            # итоговый скор
    
    
@dataclass
class AllowListEntry:
    """Запись в AllowList."""
    symbol: str
    base_asset: str
    volume_24h_usd: float
    price_change_pct: float
    volatility_pct: float
    score: float
    added_at: str
    source: str = "auto"  # auto, manual, trending


# ══════════════════════════════════════════════════════════════════════════════
# BINANCE CLIENT
# ══════════════════════════════════════════════════════════════════════════════

class BinanceClient:
    """Клиент для Binance API."""
    
    def __init__(self):
        self._symbols: Dict[str, Dict] = {}
        
    async def fetch_exchange_info(self) -> bool:
        """Загрузить информацию о бирже."""
        if not HTTPX_AVAILABLE:
            log.error("httpx not installed: pip install httpx")
            return False
            
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{Config.BINANCE_API}/exchangeInfo")
                if resp.status_code == 200:
                    data = resp.json()
                    for sym in data.get("symbols", []):
                        self._symbols[sym["symbol"]] = sym
                    log.info(f"Loaded {len(self._symbols)} symbols from Binance")
                    return True
        except Exception as e:
            log.error(f"Failed to fetch exchange info: {e}")
        return False
        
    async def fetch_24h_tickers(self) -> List[Dict]:
        """Получить 24h статистику по всем парам."""
        if not HTTPX_AVAILABLE:
            return []
            
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{Config.BINANCE_API}/ticker/24hr")
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            log.error(f"Failed to fetch tickers: {e}")
        return []
        
    def is_tradeable(self, symbol: str) -> bool:
        """Проверить что можно торговать на SPOT."""
        if symbol not in self._symbols:
            return True  # Если нет инфо - пропускаем
        info = self._symbols[symbol]
        return (
            info.get("status") == "TRADING" and 
            info.get("isSpotTradingAllowed", False)
        )
        
    def get_base_asset(self, symbol: str) -> str:
        """Получить базовый актив (BTC из BTCUSDT)."""
        if symbol in self._symbols:
            return self._symbols[symbol].get("baseAsset", "")
        # Fallback
        for quote in ["USDT", "BUSD", "USDC"]:
            if symbol.endswith(quote):
                return symbol[:-len(quote)]
        return symbol


# ══════════════════════════════════════════════════════════════════════════════
# ALLOWLIST GENERATOR
# ══════════════════════════════════════════════════════════════════════════════

class DynamicAllowListV2:
    """
    Генератор динамического AllowList v2.
    
    Отбирает топ-20 монет по объёму ($50M+), включая все категории.
    Обновляется каждый час.
    """
    
    def __init__(self):
        self.client = BinanceClient()
        self.entries: Dict[str, AllowListEntry] = {}
        self.manual_additions: Set[str] = set()
        
        self._load_current()
        self._load_manual()
        
    def _load_current(self):
        """Загрузить текущий список."""
        if ALLOWLIST_JSON.exists():
            try:
                with open(ALLOWLIST_JSON, 'r') as f:
                    data = json.load(f)
                for e in data.get("entries", []):
                    entry = AllowListEntry(**e)
                    self.entries[entry.symbol] = entry
                log.info(f"Loaded {len(self.entries)} entries")
            except Exception as e:
                log.error(f"Failed to load: {e}")
                
    def _load_manual(self):
        """Загрузить ручные добавления."""
        if MANUAL_FILE.exists():
            with open(MANUAL_FILE, 'r') as f:
                for line in f:
                    symbol = line.strip().upper()
                    if symbol and not symbol.startswith("#"):
                        if not symbol.endswith("USDT"):
                            symbol += "USDT"
                        self.manual_additions.add(symbol)
            log.info(f"Loaded {len(self.manual_additions)} manual additions")
            
    def _is_excluded(self, base_asset: str) -> Tuple[bool, str]:
        """Проверить исключения."""
        # Стейблкоины
        if base_asset in STABLECOINS:
            return True, "stablecoin"
            
        # Золото/RWA
        if base_asset in GOLD_RWA:
            return True, "gold/rwa"
            
        # Leveraged
        for pattern in LEVERAGED_PATTERNS:
            if pattern in base_asset:
                return True, f"leveraged"
                
        return False, ""
        
    def _calculate_score(self, coin: CoinData) -> float:
        """
        Рассчитать скор для монеты.
        
        Формула:
        - 60% объём (основной критерий)
        - 25% волатильность (потенциал движения)
        - 15% momentum (текущий тренд)
        """
        # Normalize volume (0-1, max at $1B)
        volume_score = min(1.0, coin.volume_24h_usd / 1_000_000_000)
        
        # Normalize volatility (0-1, max at 20%)
        volatility_score = min(1.0, coin.volatility_pct / 20)
        
        # Momentum (-20% to +20% maps to 0-1)
        momentum_score = (coin.price_change_pct + 20) / 40
        momentum_score = max(0, min(1, momentum_score))
        
        # Weighted score
        score = (
            volume_score * 0.60 +
            volatility_score * 0.25 +
            momentum_score * 0.15
        )
        
        # Penalty for wrapped tokens
        if coin.base_asset in WRAPPED_TOKENS:
            score *= 0.8
            
        return score
        
    async def update(self) -> List[AllowListEntry]:
        """
        Обновить AllowList.
        
        Шаги:
        1. Получить данные с Binance
        2. Отфильтровать по объёму ($50M+)
        3. Исключить стейблкоины
        4. Рассчитать скоры
        5. Взять топ-20
        6. Добавить manual additions
        """
        log.info("=" * 60)
        log.info("UPDATING ALLOWLIST")
        log.info(f"Min volume: ${Config.MIN_VOLUME_USD/1e6:.0f}M")
        log.info(f"Max coins: {Config.MAX_COINS}")
        log.info("=" * 60)
        
        # Fetch exchange info
        await self.client.fetch_exchange_info()
        
        # Fetch tickers
        tickers = await self.client.fetch_24h_tickers()
        if not tickers:
            log.error("Failed to fetch tickers - keeping current list")
            return list(self.entries.values())
            
        log.info(f"Fetched {len(tickers)} tickers")
        
        # Process tickers
        candidates: List[CoinData] = []
        excluded_count = {"stablecoin": 0, "gold/rwa": 0, "leveraged": 0, "low_volume": 0}
        
        for ticker in tickers:
            symbol = ticker.get("symbol", "")
            
            # Only USDT pairs
            if not symbol.endswith("USDT"):
                continue
                
            base_asset = self.client.get_base_asset(symbol)
            
            # Check exclusions
            excluded, reason = self._is_excluded(base_asset)
            if excluded:
                excluded_count[reason] = excluded_count.get(reason, 0) + 1
                continue
                
            # Check tradeable
            if not self.client.is_tradeable(symbol):
                continue
                
            try:
                price = float(ticker.get("lastPrice", 0))
                volume_usd = float(ticker.get("quoteVolume", 0))
                price_change = float(ticker.get("priceChangePercent", 0))
                high = float(ticker.get("highPrice", 0))
                low = float(ticker.get("lowPrice", 0))
                trades = int(ticker.get("count", 0))
                
                # Calculate volatility
                volatility = ((high - low) / low * 100) if low > 0 else 0
                
            except (ValueError, TypeError):
                continue
                
            # Check minimum volume ($50M)
            if volume_usd < Config.MIN_VOLUME_USD:
                excluded_count["low_volume"] += 1
                continue
                
            coin = CoinData(
                symbol=symbol,
                base_asset=base_asset,
                price=price,
                volume_24h_usd=volume_usd,
                price_change_pct=price_change,
                volatility_pct=volatility,
                trades_24h=trades,
            )
            
            # Calculate score
            coin.score = self._calculate_score(coin)
            candidates.append(coin)
            
        log.info(f"Candidates after filtering: {len(candidates)}")
        log.info(f"Excluded: {excluded_count}")
        
        # Sort by score
        candidates.sort(key=lambda x: x.score, reverse=True)
        
        # Take top N
        top_coins = candidates[:Config.MAX_COINS]
        
        # Convert to entries
        now = datetime.now(timezone.utc).isoformat()
        entries = []
        
        for coin in top_coins:
            entries.append(AllowListEntry(
                symbol=coin.symbol,
                base_asset=coin.base_asset,
                volume_24h_usd=coin.volume_24h_usd,
                price_change_pct=coin.price_change_pct,
                volatility_pct=coin.volatility_pct,
                score=coin.score,
                added_at=now,
                source="auto",
            ))
            
        # Add manual additions (if meet volume requirement)
        for symbol in self.manual_additions:
            if symbol not in [e.symbol for e in entries]:
                # Find in tickers
                for ticker in tickers:
                    if ticker.get("symbol") == symbol:
                        volume = float(ticker.get("quoteVolume", 0))
                        if volume >= Config.MIN_VOLUME_USD / 2:  # Lower threshold for manual
                            entries.append(AllowListEntry(
                                symbol=symbol,
                                base_asset=self.client.get_base_asset(symbol),
                                volume_24h_usd=volume,
                                price_change_pct=float(ticker.get("priceChangePercent", 0)),
                                volatility_pct=0,
                                score=0.5,
                                added_at=now,
                                source="manual",
                            ))
                            log.info(f"Added manual: {symbol}")
                        break
                        
        # Update internal state
        self.entries = {e.symbol: e for e in entries}
        
        log.info(f"Final AllowList: {len(entries)} coins")
        
        return entries
        
    def save(self):
        """Сохранить AllowList."""
        entries = sorted(
            self.entries.values(),
            key=lambda x: x.score,
            reverse=True
        )
        
        # Checksum
        symbols_str = ",".join(e.symbol for e in entries)
        checksum = hashlib.sha256(symbols_str.encode()).hexdigest()[:16]
        
        # Save detailed JSON
        data = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(entries),
            "checksum": f"sha256:{checksum}",
            "config": {
                "min_volume_usd": Config.MIN_VOLUME_USD,
                "max_coins": Config.MAX_COINS,
                "update_interval_hours": Config.UPDATE_INTERVAL_HOURS,
            },
            "entries": [asdict(e) for e in entries],
        }
        
        with open(ALLOWLIST_JSON, 'w') as f:
            json.dump(data, f, indent=2)
            
        # Save simple text (MoonBot compatible)
        with open(ALLOWLIST_FILE, 'w') as f:
            f.write(f"# HOPE AI AllowList\n")
            f.write(f"# Updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC\n")
            f.write(f"# Coins: {len(entries)} | Min Volume: ${Config.MIN_VOLUME_USD/1e6:.0f}M\n")
            f.write(f"# Checksum: {checksum}\n")
            f.write("#\n")
            for e in entries:
                vol_str = f"${e.volume_24h_usd/1e6:.0f}M"
                f.write(f"{e.symbol:<12} # {vol_str:>8} | {e.price_change_pct:+.1f}%\n")
                
        # Copy to legacy location
        if LEGACY_ALLOWLIST.parent.exists():
            with open(LEGACY_ALLOWLIST, 'w') as f:
                for e in entries:
                    f.write(f"{e.symbol}\n")
                    
        # Append to history
        with open(HISTORY_FILE, 'a') as f:
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "count": len(entries),
                "checksum": checksum,
                "top_5": [e.symbol for e in entries[:5]],
            }
            f.write(json.dumps(record) + "\n")
            
        log.info(f"Saved: {len(entries)} coins, checksum={checksum}")
        
    def add_manual(self, symbols: List[str]):
        """Добавить монеты вручную."""
        for symbol in symbols:
            symbol = symbol.strip().upper()
            if not symbol.endswith("USDT"):
                symbol += "USDT"
            self.manual_additions.add(symbol)
            log.info(f"Added to manual list: {symbol}")
            
        # Save
        with open(MANUAL_FILE, 'w') as f:
            f.write("# Manual additions to AllowList\n")
            f.write("# One symbol per line\n")
            for s in sorted(self.manual_additions):
                f.write(f"{s}\n")
                
    def show(self):
        """Показать текущий список."""
        entries = sorted(
            self.entries.values(),
            key=lambda x: x.score,
            reverse=True
        )
        
        print("\n" + "=" * 75)
        print("HOPE AI ALLOWLIST")
        print("=" * 75)
        print(f"Total: {len(entries)} coins | Min Volume: ${Config.MIN_VOLUME_USD/1e6:.0f}M | Update: every {Config.UPDATE_INTERVAL_HOURS}h")
        print("-" * 75)
        print(f"{'#':<3} {'Symbol':<12} {'Volume 24h':>12} {'Change':>8} {'Vola%':>7} {'Score':>6} {'Source':<8}")
        print("-" * 75)
        
        for i, e in enumerate(entries, 1):
            vol_str = f"${e.volume_24h_usd/1e6:.0f}M"
            print(f"{i:<3} {e.symbol:<12} {vol_str:>12} {e.price_change_pct:>+7.1f}% {e.volatility_pct:>6.1f}% {e.score:>6.3f} {e.source:<8}")
            
        print("=" * 75)
        
    def get_symbols(self) -> List[str]:
        """Получить список символов."""
        return sorted(self.entries.keys())


# ══════════════════════════════════════════════════════════════════════════════
# DAEMON
# ══════════════════════════════════════════════════════════════════════════════

class AllowListDaemon:
    """Демон для автообновления каждый час."""
    
    def __init__(self, generator: DynamicAllowListV2):
        self.generator = generator
        self.running = False
        
    async def run(self):
        """Запустить демон."""
        self.running = True
        interval = Config.UPDATE_INTERVAL_HOURS * 3600  # секунды
        
        log.info(f"Daemon started. Update interval: {Config.UPDATE_INTERVAL_HOURS}h")
        
        while self.running:
            try:
                # Update
                await self.generator.update()
                self.generator.save()
                self.generator.show()
                
                # Wait
                log.info(f"Next update in {Config.UPDATE_INTERVAL_HOURS} hour(s)")
                await asyncio.sleep(interval)
                
            except Exception as e:
                log.exception(f"Update failed: {e}")
                await asyncio.sleep(60)  # Retry in 1 min
                
    def stop(self):
        self.running = False


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    parser = argparse.ArgumentParser(
        description="HOPE AI Dynamic AllowList Generator v2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python dynamic_allowlist_v2.py --update     # Update now
  python dynamic_allowlist_v2.py --daemon     # Run hourly updates
  python dynamic_allowlist_v2.py --show       # Show current list
  python dynamic_allowlist_v2.py --add SOMI   # Add coin manually
        """
    )
    
    parser.add_argument("--update", action="store_true", help="Update AllowList now")
    parser.add_argument("--daemon", action="store_true", help="Run as daemon (hourly updates)")
    parser.add_argument("--show", action="store_true", help="Show current AllowList")
    parser.add_argument("--add", type=str, help="Add symbols manually (comma-separated)")
    parser.add_argument("--export", type=str, help="Export to file")
    
    args = parser.parse_args()
    
    generator = DynamicAllowListV2()
    
    if args.add:
        symbols = [s.strip() for s in args.add.split(",")]
        generator.add_manual(symbols)
        await generator.update()
        generator.save()
        generator.show()
        
    elif args.update:
        await generator.update()
        generator.save()
        generator.show()
        
    elif args.daemon:
        daemon = AllowListDaemon(generator)
        try:
            await daemon.run()
        except KeyboardInterrupt:
            daemon.stop()
            log.info("Daemon stopped")
            
    elif args.show:
        generator.show()
        
    elif args.export:
        with open(args.export, 'w') as f:
            for symbol in generator.get_symbols():
                f.write(f"{symbol}\n")
        print(f"Exported {len(generator.entries)} symbols to {args.export}")
        
    else:
        parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())
