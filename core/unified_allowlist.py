# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 13:25:00 UTC
# Modified by: Claude (opus-4) - merged from two Claude instances
# Modified at: 2026-01-30 13:25:00 UTC
# Purpose: Three-Layer AllowList System (CORE + DYNAMIC + HOT)
# Version: 3.1 (UNIFIED)
# === END SIGNATURE ===
"""
Three-Layer AllowList System v3.1

═══════════════════════════════════════════════════════════════════════════════
АРХИТЕКТУРА (3 СЛОЯ):
═══════════════════════════════════════════════════════════════════════════════

┌──────────────────────────────────────────────────────────────────────────────┐
│                           UNIFIED ALLOWLIST                                   │
│                                                                              │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐                 │
│  │   CORE_LIST    │  │  DYNAMIC_LIST  │  │    HOT_LIST    │                 │
│  │   (постоянный) │  │  (каждый час)  │  │  (мгновенный)  │                 │
│  │                │  │                │  │                │                 │
│  │  BTC, ETH      │  │  Top 20 by vol │  │  Pump signals  │                 │
│  │  SOL, BNB      │  │  $50M+ 24h     │  │  TTL: 15 min   │                 │
│  │                │  │                │  │                │                 │
│  │  Position: 100%│  │  Position: 100%│  │  Position: 50% │                 │
│  │  Timeout: std  │  │  Timeout: std  │  │  Timeout: 30s  │                 │
│  └────────────────┘  └────────────────┘  └────────────────┘                 │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘

═══════════════════════════════════════════════════════════════════════════════
ЛОГИКА:
═══════════════════════════════════════════════════════════════════════════════

1. MoonBot Signal приходит (например BIFI)
2. Fast Scanner проверяет: buys/sec > 30? delta > 2%? vol_raise > 100%?
3. Если PUMP_SCORE > 0.7 → добавляем в HOT_LIST на 15 минут
4. Eye of God видит BIFI в HOT_LIST → разрешает торговлю
5. Trade execution с уменьшенной позицией (50%) и коротким timeout (30s)

═══════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import json
import logging
import os
import time
import hashlib
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Any, Tuple
from enum import Enum

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
log = logging.getLogger("ALLOWLIST")


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

STATE_DIR = Path("state/allowlist")
STATE_DIR.mkdir(parents=True, exist_ok=True)

BINANCE_API = "https://api.binance.com/api/v3"


class ListType(str, Enum):
    CORE = "core"
    DYNAMIC = "dynamic"
    HOT = "hot"


# ══════════════════════════════════════════════════════════════════════════════
# CORE LIST - Постоянный список топ монет
# ══════════════════════════════════════════════════════════════════════════════

CORE_LIST = {
    "BTCUSDT": {"name": "Bitcoin", "priority": 1},
    "ETHUSDT": {"name": "Ethereum", "priority": 2},
    "SOLUSDT": {"name": "Solana", "priority": 3},
    "BNBUSDT": {"name": "BNB", "priority": 4},
    "XRPUSDT": {"name": "XRP", "priority": 5},
    "DOGEUSDT": {"name": "Dogecoin", "priority": 6},
    "ADAUSDT": {"name": "Cardano", "priority": 7},
    "AVAXUSDT": {"name": "Avalanche", "priority": 8},
}


# ══════════════════════════════════════════════════════════════════════════════
# EXCLUSIONS
# ══════════════════════════════════════════════════════════════════════════════

STABLECOINS = {
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD",
    "USDD", "PYUSD", "GUSD", "FRAX", "LUSD", "SUSD", "MIM",
    "UST", "USTC", "USDJ", "USDN", "CUSD", "RSR", "USD1",
    "RLUSD", "USDE", "EURC", "USDS", "CRVUSD", "EURT"
}

GOLD_RWA = {"XAUT", "PAXG"}

LEVERAGED_PATTERNS = {"UP", "DOWN", "BULL", "BEAR", "3L", "3S", "2L", "2S"}


# ══════════════════════════════════════════════════════════════════════════════
# HOT LIST SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

class HotListConfig:
    """Настройки HOT_LIST для pump-сигналов."""

    # TTL - время жизни записи в HOT_LIST
    TTL_SECONDS: int = 900  # 15 минут

    # Пороги для добавления в HOT_LIST
    MIN_PUMP_SCORE: float = 0.7
    MIN_BUYS_PER_SEC: float = 30
    MIN_DELTA_PCT: float = 2.0
    MIN_VOL_RAISE_PCT: float = 100

    # Ограничения торговли для HOT_LIST
    POSITION_SIZE_MULTIPLIER: float = 0.5  # 50% от стандартной позиции
    TIMEOUT_SECONDS: int = 30  # Короткий timeout для pump-скальпа

    # Максимум записей в HOT_LIST
    MAX_ENTRIES: int = 10


# ══════════════════════════════════════════════════════════════════════════════
# DYNAMIC LIST SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

class DynamicListConfig:
    """Настройки DYNAMIC_LIST."""

    MIN_VOLUME_USD: float = 50_000_000  # $50M minimum 24h volume
    MAX_COINS: int = 20                  # Maximum 20 coins
    UPDATE_INTERVAL_HOURS: float = 1.0   # Update every hour


# ══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class AllowListEntry:
    """Запись в AllowList."""
    symbol: str
    list_type: str  # core, dynamic, hot
    added_at: str
    expires_at: Optional[str] = None  # Только для HOT

    # Trading parameters
    position_multiplier: float = 1.0  # 1.0 = 100%, 0.5 = 50%
    timeout_override: Optional[int] = None  # None = use default

    # Metadata
    pump_score: float = 0.0
    volume_24h: float = 0.0
    reason: str = ""


@dataclass
class HotEntry:
    """Запись в HOT_LIST с TTL."""
    symbol: str
    added_at: float  # timestamp
    expires_at: float  # timestamp
    pump_score: float
    signal_data: Dict[str, Any]

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def ttl_remaining(self) -> int:
        return max(0, int(self.expires_at - time.time()))


# ══════════════════════════════════════════════════════════════════════════════
# FAST SCANNER - Быстрая оценка pump-сигнала
# ══════════════════════════════════════════════════════════════════════════════

class FastScanner:
    """
    Быстрый сканер для оценки pump-сигналов.

    Вычисляет PUMP_SCORE на основе:
    - buys_per_sec (вес 0.35)
    - delta_pct (вес 0.35)
    - vol_raise_pct (вес 0.30)
    """

    def calculate_pump_score(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """
        Рассчитать PUMP_SCORE для сигнала.

        Args:
            signal: {
                "symbol": "BIFIUSDT",
                "buys_per_sec": 56.87,
                "delta_pct": 2.2,
                "vol_raise_pct": 150,
                "volume_24h": 1500000
            }

        Returns:
            {
                "pump_score": 0.85,
                "is_hot": True,
                "reasons": ["buys/sec=56.87>30", "vol_raise=150%>100%"],
                "recommendation": "ADD_TO_HOT"
            }
        """
        buys = signal.get("buys_per_sec", 0)
        delta = signal.get("delta_pct", 0)
        vol_raise = signal.get("vol_raise_pct", 0)

        reasons = []

        # Score components (0-1)
        buys_score = min(1.0, buys / 100)  # Max at 100 buys/sec
        delta_score = min(1.0, delta / 10)  # Max at 10%
        vol_score = min(1.0, vol_raise / 300)  # Max at 300%

        # Check thresholds
        buys_ok = buys >= HotListConfig.MIN_BUYS_PER_SEC
        delta_ok = delta >= HotListConfig.MIN_DELTA_PCT
        vol_ok = vol_raise >= HotListConfig.MIN_VOL_RAISE_PCT

        if buys_ok:
            reasons.append(f"buys/sec={buys:.1f}>={HotListConfig.MIN_BUYS_PER_SEC}")
        if delta_ok:
            reasons.append(f"delta={delta:.1f}%>={HotListConfig.MIN_DELTA_PCT}%")
        if vol_ok:
            reasons.append(f"vol_raise={vol_raise:.0f}%>={HotListConfig.MIN_VOL_RAISE_PCT}%")

        # Weighted pump score
        pump_score = (
            buys_score * 0.35 +
            delta_score * 0.35 +
            vol_score * 0.30
        )

        # Is HOT?
        is_hot = (
            pump_score >= HotListConfig.MIN_PUMP_SCORE or
            (buys_ok and delta_ok)  # Достаточно buys + delta
        )

        return {
            "pump_score": pump_score,
            "is_hot": is_hot,
            "reasons": reasons,
            "scores": {
                "buys_score": buys_score,
                "delta_score": delta_score,
                "vol_score": vol_score,
            },
            "thresholds_passed": {
                "buys": buys_ok,
                "delta": delta_ok,
                "vol_raise": vol_ok,
            },
            "recommendation": "ADD_TO_HOT" if is_hot else "SKIP",
        }


# ══════════════════════════════════════════════════════════════════════════════
# THREE-LAYER ALLOWLIST
# ══════════════════════════════════════════════════════════════════════════════

class UnifiedAllowList:
    """
    Трёхслойный AllowList.

    Слои:
    1. CORE_LIST - постоянный (BTC, ETH, SOL, BNB...)
    2. DYNAMIC_LIST - обновляется каждый час (Top 20 by volume)
    3. HOT_LIST - мгновенный для pump-сигналов (TTL 15 min)
    """

    def __init__(self):
        self.scanner = FastScanner()

        # Three layers
        self.core_list: Dict[str, AllowListEntry] = {}
        self.dynamic_list: Dict[str, AllowListEntry] = {}
        self.hot_list: Dict[str, HotEntry] = {}

        # Binance symbols cache
        self._binance_symbols: Set[str] = set()

        # Blacklist
        self.blacklist: Set[str] = set()

        # Files
        self.dynamic_file = STATE_DIR / "dynamic_list.json"
        self.hot_file = STATE_DIR / "hot_list.json"
        self.unified_file = STATE_DIR / "AllowList.txt"
        self.history_file = STATE_DIR / "allowlist_history.jsonl"

        # Initialize
        self._init_core_list()
        self._init_blacklist()
        self._load_dynamic()
        self._load_hot()

    def _init_core_list(self):
        """Инициализировать CORE_LIST."""
        now = datetime.now(timezone.utc).isoformat()

        for symbol, info in CORE_LIST.items():
            self.core_list[symbol] = AllowListEntry(
                symbol=symbol,
                list_type=ListType.CORE.value,
                added_at=now,
                position_multiplier=1.0,
                reason=f"Core: {info['name']}",
            )

        log.info(f"CORE_LIST initialized: {len(self.core_list)} symbols")

    def _init_blacklist(self):
        """Инициализировать blacklist."""
        for stable in STABLECOINS:
            self.blacklist.add(f"{stable}USDT")
        for gold in GOLD_RWA:
            self.blacklist.add(f"{gold}USDT")

    def _load_dynamic(self):
        """Загрузить DYNAMIC_LIST."""
        if self.dynamic_file.exists():
            try:
                with open(self.dynamic_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for entry in data.get("entries", []):
                    e = AllowListEntry(**entry)
                    self.dynamic_list[e.symbol] = e
                log.info(f"DYNAMIC_LIST loaded: {len(self.dynamic_list)} symbols")
            except Exception as e:
                log.error(f"Failed to load DYNAMIC_LIST: {e}")

    def _load_hot(self):
        """Загрузить HOT_LIST."""
        if self.hot_file.exists():
            try:
                with open(self.hot_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for entry in data.get("entries", []):
                    h = HotEntry(
                        symbol=entry["symbol"],
                        added_at=entry["added_at"],
                        expires_at=entry["expires_at"],
                        pump_score=entry["pump_score"],
                        signal_data=entry.get("signal_data", {}),
                    )
                    if not h.is_expired():
                        self.hot_list[h.symbol] = h
                log.info(f"HOT_LIST loaded: {len(self.hot_list)} symbols")
            except Exception as e:
                log.error(f"Failed to load HOT_LIST: {e}")

    async def load_binance_symbols(self):
        """Загрузить список символов с Binance."""
        if not HTTPX_AVAILABLE:
            log.warning("httpx not available")
            return

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{BINANCE_API}/exchangeInfo")
                if resp.status_code == 200:
                    data = resp.json()
                    for sym in data.get("symbols", []):
                        if sym.get("status") == "TRADING" and sym.get("isSpotTradingAllowed"):
                            self._binance_symbols.add(sym["symbol"])
                    log.info(f"Loaded {len(self._binance_symbols)} Binance symbols")
        except Exception as e:
            log.error(f"Failed to load Binance symbols: {e}")

    def is_on_binance(self, symbol: str) -> bool:
        """Проверить что символ торгуется на Binance."""
        if not self._binance_symbols:
            return True  # Assume yes if not loaded
        return symbol in self._binance_symbols

    def _is_excluded(self, base_asset: str) -> Tuple[bool, str]:
        """Проверить исключения."""
        if base_asset in STABLECOINS:
            return True, "stablecoin"
        if base_asset in GOLD_RWA:
            return True, "gold/rwa"
        for pattern in LEVERAGED_PATTERNS:
            if pattern in base_asset:
                return True, "leveraged"
        return False, ""

    # ══════════════════════════════════════════════════════════════════════════
    # HOT LIST OPERATIONS
    # ══════════════════════════════════════════════════════════════════════════

    def process_signal(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """
        Обработать входящий сигнал.

        Автоматически решает: добавить в HOT_LIST или нет.

        Args:
            signal: MoonBot signal

        Returns:
            {
                "action": "ADDED_TO_HOT" | "ALREADY_IN_LIST" | "SKIPPED",
                "symbol": "BIFIUSDT",
                "pump_score": 0.85,
                "trading_params": {...}
            }
        """
        symbol = signal.get("symbol", "")
        if not symbol:
            return {"action": "SKIPPED", "reason": "No symbol"}

        # Normalize
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"

        # Check blacklist
        if symbol in self.blacklist:
            return {"action": "SKIPPED", "reason": "Blacklisted"}

        # Check if already in any list
        if self.is_allowed(symbol):
            list_type = self._get_list_type(symbol)
            return {
                "action": "ALREADY_IN_LIST",
                "symbol": symbol,
                "list_type": list_type,
                "trading_params": self.get_trading_params(symbol),
            }

        # Check Binance
        if self._binance_symbols and not self.is_on_binance(symbol):
            return {"action": "SKIPPED", "reason": "Not on Binance"}

        # Calculate pump score
        scan_result = self.scanner.calculate_pump_score(signal)

        if not scan_result["is_hot"]:
            return {
                "action": "SKIPPED",
                "symbol": symbol,
                "pump_score": scan_result["pump_score"],
                "reason": f"Pump score {scan_result['pump_score']:.2f} < {HotListConfig.MIN_PUMP_SCORE}",
            }

        # ADD TO HOT LIST!
        self._add_to_hot(symbol, scan_result["pump_score"], signal)

        return {
            "action": "ADDED_TO_HOT",
            "symbol": symbol,
            "pump_score": scan_result["pump_score"],
            "reasons": scan_result["reasons"],
            "ttl_seconds": HotListConfig.TTL_SECONDS,
            "trading_params": self.get_trading_params(symbol),
        }

    def _add_to_hot(self, symbol: str, pump_score: float, signal_data: Dict):
        """Добавить символ в HOT_LIST."""
        now = time.time()
        expires = now + HotListConfig.TTL_SECONDS

        entry = HotEntry(
            symbol=symbol,
            added_at=now,
            expires_at=expires,
            pump_score=pump_score,
            signal_data=signal_data,
        )

        self.hot_list[symbol] = entry

        # Enforce max entries
        self._cleanup_hot_list()

        log.info(f"HOT_LIST += {symbol} (score={pump_score:.2f}, TTL={HotListConfig.TTL_SECONDS}s)")

        # Save
        self._save_hot()
        self._save_unified()
        self._log_history("HOT_ADD", symbol, pump_score)

    def _cleanup_hot_list(self):
        """Очистить истёкшие записи и ограничить размер."""
        # Remove expired
        expired = [s for s, e in self.hot_list.items() if e.is_expired()]
        for s in expired:
            del self.hot_list[s]
            log.info(f"HOT_LIST -= {s} (expired)")
            self._log_history("HOT_EXPIRE", s, 0)

        # Enforce max size (remove oldest)
        while len(self.hot_list) > HotListConfig.MAX_ENTRIES:
            oldest = min(self.hot_list.items(), key=lambda x: x[1].added_at)
            del self.hot_list[oldest[0]]
            log.info(f"HOT_LIST -= {oldest[0]} (max entries)")

    def remove_from_hot(self, symbol: str):
        """Удалить символ из HOT_LIST."""
        if symbol in self.hot_list:
            del self.hot_list[symbol]
            log.info(f"HOT_LIST -= {symbol} (manual)")
            self._save_hot()
            self._save_unified()

    # ══════════════════════════════════════════════════════════════════════════
    # DYNAMIC LIST OPERATIONS
    # ══════════════════════════════════════════════════════════════════════════

    async def update_dynamic_list(self):
        """Обновить DYNAMIC_LIST (топ по объёму)."""
        log.info("Updating DYNAMIC_LIST...")

        if not HTTPX_AVAILABLE:
            log.error("httpx not available")
            return

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{BINANCE_API}/ticker/24hr")
                if resp.status_code != 200:
                    return

                tickers = resp.json()

        except Exception as e:
            log.error(f"Failed to fetch tickers: {e}")
            return

        # Filter and sort
        candidates = []

        for t in tickers:
            symbol = t.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue

            base = symbol[:-4]  # Remove USDT

            # Check exclusions
            excluded, _ = self._is_excluded(base)
            if excluded:
                continue

            # Skip if in CORE
            if symbol in self.core_list:
                continue

            try:
                volume = float(t.get("quoteVolume", 0))
                price_change = float(t.get("priceChangePercent", 0))
                high = float(t.get("highPrice", 0))
                low = float(t.get("lowPrice", 0))
                volatility = ((high - low) / low * 100) if low > 0 else 0

                if volume >= DynamicListConfig.MIN_VOLUME_USD:
                    # Calculate score: 60% volume + 25% volatility + 15% momentum
                    vol_score = min(1.0, volume / 1_000_000_000)
                    vola_score = min(1.0, volatility / 20)
                    mom_score = (price_change + 20) / 40
                    mom_score = max(0, min(1, mom_score))

                    score = vol_score * 0.60 + vola_score * 0.25 + mom_score * 0.15

                    candidates.append({
                        "symbol": symbol,
                        "volume": volume,
                        "change": price_change,
                        "volatility": volatility,
                        "score": score,
                    })
            except:
                continue

        # Sort by score
        candidates.sort(key=lambda x: x["score"], reverse=True)

        # Take top N
        now = datetime.now(timezone.utc).isoformat()
        self.dynamic_list.clear()

        for c in candidates[:DynamicListConfig.MAX_COINS]:
            self.dynamic_list[c["symbol"]] = AllowListEntry(
                symbol=c["symbol"],
                list_type=ListType.DYNAMIC.value,
                added_at=now,
                position_multiplier=1.0,
                volume_24h=c["volume"],
                reason=f"Dynamic: vol=${c['volume']/1e6:.0f}M, score={c['score']:.2f}",
            )

        log.info(f"DYNAMIC_LIST updated: {len(self.dynamic_list)} symbols")

        self._save_dynamic()
        self._save_unified()
        self._log_history("DYNAMIC_UPDATE", f"count={len(self.dynamic_list)}", 0)

    # ══════════════════════════════════════════════════════════════════════════
    # UNIFIED OPERATIONS
    # ══════════════════════════════════════════════════════════════════════════

    def is_allowed(self, symbol: str) -> bool:
        """Проверить разрешена ли торговля символом."""
        # Cleanup expired HOT entries
        self._cleanup_hot_list()

        return (
            symbol in self.core_list or
            symbol in self.dynamic_list or
            symbol in self.hot_list
        )

    def _get_list_type(self, symbol: str) -> str:
        """Получить тип списка для символа."""
        if symbol in self.core_list:
            return ListType.CORE.value
        if symbol in self.dynamic_list:
            return ListType.DYNAMIC.value
        if symbol in self.hot_list:
            return ListType.HOT.value
        return "unknown"

    def get_trading_params(self, symbol: str) -> Dict[str, Any]:
        """
        Получить параметры торговли для символа.

        HOT_LIST имеет особые параметры:
        - position_multiplier: 0.5 (50%)
        - timeout_override: 30 sec
        """
        list_type = self._get_list_type(symbol)

        if list_type == ListType.HOT.value:
            hot_entry = self.hot_list.get(symbol)
            return {
                "list_type": ListType.HOT.value,
                "position_multiplier": HotListConfig.POSITION_SIZE_MULTIPLIER,
                "timeout_override": HotListConfig.TIMEOUT_SECONDS,
                "pump_score": hot_entry.pump_score if hot_entry else 0,
                "ttl_remaining": hot_entry.ttl_remaining() if hot_entry else 0,
            }

        elif list_type == ListType.CORE.value:
            entry = self.core_list.get(symbol)
            return {
                "list_type": ListType.CORE.value,
                "position_multiplier": entry.position_multiplier if entry else 1.0,
                "timeout_override": None,
            }

        elif list_type == ListType.DYNAMIC.value:
            entry = self.dynamic_list.get(symbol)
            return {
                "list_type": ListType.DYNAMIC.value,
                "position_multiplier": entry.position_multiplier if entry else 1.0,
                "timeout_override": None,
            }

        return {
            "list_type": "unknown",
            "position_multiplier": 0.5,  # Cautious
            "timeout_override": 30,
        }

    def get_all_symbols(self) -> List[str]:
        """Получить все разрешённые символы."""
        self._cleanup_hot_list()

        symbols = set()
        symbols.update(self.core_list.keys())
        symbols.update(self.dynamic_list.keys())
        symbols.update(self.hot_list.keys())

        return sorted(symbols)

    def get_symbols_set(self) -> Set[str]:
        """Получить все разрешённые символы как set."""
        self._cleanup_hot_list()

        symbols = set()
        symbols.update(self.core_list.keys())
        symbols.update(self.dynamic_list.keys())
        symbols.update(self.hot_list.keys())

        return symbols

    def get_status(self) -> Dict[str, Any]:
        """Получить статус всех списков."""
        self._cleanup_hot_list()

        return {
            "core_list": {
                "count": len(self.core_list),
                "symbols": list(self.core_list.keys()),
            },
            "dynamic_list": {
                "count": len(self.dynamic_list),
                "symbols": list(self.dynamic_list.keys()),
            },
            "hot_list": {
                "count": len(self.hot_list),
                "symbols": [
                    {
                        "symbol": s,
                        "pump_score": e.pump_score,
                        "ttl_remaining": e.ttl_remaining(),
                    }
                    for s, e in self.hot_list.items()
                ],
            },
            "total_unique": len(self.get_all_symbols()),
        }

    # ══════════════════════════════════════════════════════════════════════════
    # PERSISTENCE
    # ══════════════════════════════════════════════════════════════════════════

    def _atomic_write(self, path: Path, content: str):
        """Атомарная запись файла."""
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def _save_dynamic(self):
        """Сохранить DYNAMIC_LIST."""
        data = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "entries": [asdict(e) for e in self.dynamic_list.values()],
        }
        self._atomic_write(self.dynamic_file, json.dumps(data, indent=2))

    def _save_hot(self):
        """Сохранить HOT_LIST."""
        data = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "entries": [
                {
                    "symbol": e.symbol,
                    "added_at": e.added_at,
                    "expires_at": e.expires_at,
                    "pump_score": e.pump_score,
                    "signal_data": e.signal_data,
                }
                for e in self.hot_list.values()
            ],
        }
        self._atomic_write(self.hot_file, json.dumps(data, indent=2))

    def _save_unified(self):
        """Сохранить объединённый AllowList.txt."""
        all_symbols = self.get_all_symbols()

        lines = []
        lines.append(f"# HOPE AI Unified AllowList (3-Layer)")
        lines.append(f"# Updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
        lines.append(f"# Core: {len(self.core_list)} | Dynamic: {len(self.dynamic_list)} | Hot: {len(self.hot_list)}")
        lines.append(f"# Total: {len(all_symbols)} symbols")
        lines.append("#")

        # Core first
        lines.append("# === CORE ===")
        for s in sorted(self.core_list.keys()):
            lines.append(s)

        # Dynamic
        lines.append("# === DYNAMIC ===")
        for s in sorted(self.dynamic_list.keys()):
            if s not in self.core_list:
                lines.append(s)

        # Hot
        if self.hot_list:
            lines.append("# === HOT (pump signals) ===")
            for s, e in self.hot_list.items():
                if s not in self.core_list and s not in self.dynamic_list:
                    lines.append(f"{s}  # TTL:{e.ttl_remaining()}s score:{e.pump_score:.2f}")

        self._atomic_write(self.unified_file, "\n".join(lines) + "\n")

    def _log_history(self, action: str, symbol: str, score: float):
        """Записать в историю."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "symbol": symbol,
            "score": score,
        }
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def show(self):
        """Показать статус."""
        status = self.get_status()

        print("\n" + "=" * 70)
        print("THREE-LAYER ALLOWLIST")
        print("=" * 70)

        print(f"\nCORE_LIST ({status['core_list']['count']} symbols):")
        print(f"   {', '.join(status['core_list']['symbols'])}")

        print(f"\nDYNAMIC_LIST ({status['dynamic_list']['count']} symbols):")
        dynamic_only = [s for s in status['dynamic_list']['symbols'] if s not in self.core_list]
        print(f"   {', '.join(dynamic_only[:10])}{'...' if len(dynamic_only) > 10 else ''}")

        print(f"\nHOT_LIST ({status['hot_list']['count']} symbols):")
        for item in status['hot_list']['symbols']:
            print(f"   {item['symbol']}: score={item['pump_score']:.2f}, TTL={item['ttl_remaining']}s")

        print(f"\nTotal unique symbols: {status['total_unique']}")
        print("=" * 70)


# ══════════════════════════════════════════════════════════════════════════════
# SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_unified_allowlist: Optional[UnifiedAllowList] = None


def get_unified_allowlist() -> UnifiedAllowList:
    """Получить singleton UnifiedAllowList."""
    global _unified_allowlist
    if _unified_allowlist is None:
        _unified_allowlist = UnifiedAllowList()
    return _unified_allowlist


# ══════════════════════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def process_signal_for_allowlist(signal: dict) -> Dict[str, Any]:
    """
    Обработать сигнал и автоматически добавить в HOT_LIST если нужно.

    Вызывается из MoonBot integration при каждом сигнале.
    """
    allowlist = get_unified_allowlist()
    return allowlist.process_signal(signal)


def is_symbol_allowed(symbol: str) -> bool:
    """Проверить разрешена ли торговля символом."""
    allowlist = get_unified_allowlist()
    return allowlist.is_allowed(symbol)


def get_trading_params(symbol: str) -> Dict[str, Any]:
    """Получить параметры торговли для символа."""
    allowlist = get_unified_allowlist()
    return allowlist.get_trading_params(symbol)


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Unified Three-Layer AllowList System")
    parser.add_argument("--show", action="store_true", help="Show status")
    parser.add_argument("--update-dynamic", action="store_true", help="Update DYNAMIC_LIST")
    parser.add_argument("--test-signal", type=str, help="Test signal (JSON or SYMBOL,BUYS,DELTA,VOL)")
    parser.add_argument("--daemon", action="store_true", help="Run as daemon")

    args = parser.parse_args()

    allowlist = get_unified_allowlist()

    # Load Binance symbols
    await allowlist.load_binance_symbols()

    if args.update_dynamic:
        await allowlist.update_dynamic_list()
        allowlist.show()

    elif args.test_signal:
        try:
            signal = json.loads(args.test_signal)
        except:
            # Simple format: BIFI,56,2.2,150
            parts = args.test_signal.split(",")
            signal = {
                "symbol": parts[0],
                "buys_per_sec": float(parts[1]) if len(parts) > 1 else 50,
                "delta_pct": float(parts[2]) if len(parts) > 2 else 3,
                "vol_raise_pct": float(parts[3]) if len(parts) > 3 else 100,
            }

        result = allowlist.process_signal(signal)
        print(json.dumps(result, indent=2))
        allowlist.show()

    elif args.daemon:
        log.info("Starting daemon mode...")

        # Initial update
        await allowlist.update_dynamic_list()

        while True:
            # Cleanup HOT list
            allowlist._cleanup_hot_list()
            allowlist._save_unified()

            # Update dynamic every hour
            await asyncio.sleep(3600)
            await allowlist.update_dynamic_list()

    elif args.show:
        allowlist.show()

    else:
        parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())
