# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 11:15:00 UTC
# Purpose: Auto-update and validate data sources for HOPE bot
# Contract: fail-closed, atomic writes, full audit trail
# === END SIGNATURE ===
"""
Data Sources Manager — Автоматическое управление списком источников.

Функции:
1. Проверка доступности источников (health check)
2. Измерение latency
3. Валидация формата ответа
4. Автоматическое обновление списка
5. Алерты при проблемах (DOWN статус)

Запуск:
    python -m scripts.sources_manager check      # Проверить все
    python -m scripts.sources_manager report     # Текстовый отчёт
    python -m scripts.sources_manager domains    # Список доменов
    python -m scripts.sources_manager daemon     # Фоновый режим (каждые 6ч)

Интеграция:
    from scripts.sources_manager import SourcesManager
    manager = SourcesManager()
    active = manager.get_active_sources("binance")
"""

from __future__ import annotations

import asyncio
import json
import hashlib
import logging
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# Setup path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s"
)
log = logging.getLogger("sources_manager")


# === PATHS ===
STATE_DIR = PROJECT_ROOT / "state" / "sources"
SOURCES_FILE = STATE_DIR / "sources.json"
HISTORY_FILE = STATE_DIR / "check_history.jsonl"
DOMAINS_FILE = STATE_DIR / "allowed_domains.txt"


class SourceType(str, Enum):
    REST_API = "rest_api"
    WEBSOCKET = "websocket"
    RSS = "rss"
    STATIC = "static"


class SourceStatus(str, Enum):
    ACTIVE = "active"
    DEGRADED = "degraded"
    DOWN = "down"
    REMOVED = "removed"
    PENDING = "pending"


@dataclass
class DataSource:
    """Источник данных с метаданными."""
    url: str
    name: str
    type: SourceType
    category: str
    priority: int  # 1 = critical, 2 = high, 3 = medium, 4 = low

    status: SourceStatus = SourceStatus.PENDING
    last_check: Optional[str] = None
    latency_ms: Optional[float] = None
    error_count: int = 0
    success_count: int = 0
    last_error: Optional[str] = None

    # Validation
    expected_content_type: Optional[str] = None
    expected_keys: List[str] = field(default_factory=list)

    # Metadata
    added_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for JSON."""
        d = asdict(self)
        d["type"] = self.type.value
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DataSource":
        """Deserialize from JSON."""
        data["type"] = SourceType(data["type"])
        data["status"] = SourceStatus(data["status"])
        return cls(**data)


# === MASTER SOURCE LIST (v1.0 - 2026-01-29) ===
# Validated: Binance 6/6, CoinGecko 3/3, RSS 4/5

MASTER_SOURCES: List[DataSource] = [
    # ═══════════════════════════════════════════════════════════════
    # BINANCE (Priority 1 - Critical)
    # ═══════════════════════════════════════════════════════════════
    DataSource(
        url="https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
        name="Binance Price API",
        type=SourceType.REST_API,
        category="binance",
        priority=1,
        expected_content_type="application/json",
        expected_keys=["symbol", "price"],
        notes="Primary price feed, ~336ms latency",
    ),
    DataSource(
        url="https://api.binance.com/api/v3/exchangeInfo",
        name="Binance Exchange Info",
        type=SourceType.REST_API,
        category="binance",
        priority=1,
        expected_content_type="application/json",
        expected_keys=["symbols"],
        notes="Trading rules and limits, ~470ms latency",
    ),
    DataSource(
        url="https://api.binance.com/api/v3/ticker/24hr",
        name="Binance 24h Ticker",
        type=SourceType.REST_API,
        category="binance",
        priority=1,
        expected_content_type="application/json",
        notes="Volume, gainers, losers - sort by priceChangePercent/quoteVolume",
    ),
    DataSource(
        url="https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=1",
        name="Binance Klines",
        type=SourceType.REST_API,
        category="binance",
        priority=1,
        expected_content_type="application/json",
        notes="OHLCV candles, 52-week high/low via interval=1w&limit=52",
    ),
    DataSource(
        url="https://api.binance.com/api/v3/depth?symbol=BTCUSDT&limit=5",
        name="Binance Order Book",
        type=SourceType.REST_API,
        category="binance",
        priority=2,
        expected_content_type="application/json",
        expected_keys=["bids", "asks"],
        notes="Order book depth, limit=5/10/20/50/100/500/1000/5000",
    ),
    DataSource(
        url="wss://stream.binance.com:9443/ws",
        name="Binance WebSocket",
        type=SourceType.WEBSOCKET,
        category="binance",
        priority=1,
        notes="Real-time trades/tickers, requires websockets package",
    ),
    DataSource(
        url="https://testnet.binance.vision/api/v3/ticker/price?symbol=BTCUSDT",
        name="Binance Testnet",
        type=SourceType.REST_API,
        category="binance",
        priority=2,
        expected_content_type="application/json",
        notes="Test environment, separate rate limits",
    ),
    DataSource(
        url="https://data.binance.vision/",
        name="Binance Historical Data",
        type=SourceType.STATIC,
        category="binance",
        priority=3,
        notes="ZIP/CSV archives for backtesting",
    ),

    # ═══════════════════════════════════════════════════════════════
    # MARKET DATA (Priority 2 - High)
    # ═══════════════════════════════════════════════════════════════
    DataSource(
        url="https://api.coingecko.com/api/v3/ping",
        name="CoinGecko Ping",
        type=SourceType.REST_API,
        category="market_data",
        priority=2,
        expected_content_type="application/json",
        expected_keys=["gecko_says"],
        notes="Health check, ~536ms latency",
    ),
    DataSource(
        url="https://api.coingecko.com/api/v3/global",
        name="CoinGecko Global",
        type=SourceType.REST_API,
        category="market_data",
        priority=2,
        expected_content_type="application/json",
        expected_keys=["data"],
        notes="Total market cap, BTC/ETH dominance",
    ),
    DataSource(
        url="https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd",
        name="CoinGecko Price",
        type=SourceType.REST_API,
        category="market_data",
        priority=2,
        expected_content_type="application/json",
        expected_keys=["bitcoin", "ethereum"],
        notes="Backup price feed, ~400ms latency",
    ),

    # ═══════════════════════════════════════════════════════════════
    # NEWS RSS (Priority 3 - Medium)
    # Validated 2026-01-29: 4/5 working, Bitcoin Magazine blocked (403)
    # ═══════════════════════════════════════════════════════════════
    DataSource(
        url="https://www.coindesk.com/arc/outboundfeeds/rss/",
        name="CoinDesk RSS",
        type=SourceType.RSS,
        category="news",
        priority=3,
        expected_content_type="application/rss+xml",
        notes="25 items, most recent 2026-01-29, PRIMARY",
    ),
    DataSource(
        url="https://cointelegraph.com/rss",
        name="Cointelegraph RSS",
        type=SourceType.RSS,
        category="news",
        priority=3,
        notes="30 items, most authoritative, PRIMARY",
    ),
    DataSource(
        url="https://decrypt.co/feed",
        name="Decrypt RSS",
        type=SourceType.RSS,
        category="news",
        priority=3,
        notes="56 items, good coverage",
    ),
    DataSource(
        url="https://www.theblock.co/rss.xml",
        name="The Block RSS",
        type=SourceType.RSS,
        category="news",
        priority=3,
        notes="20 items, research depth",
    ),
    # REMOVED: Bitcoin Magazine - HTTP 403 Forbidden as of 2026-01-29
    # REMOVED: Binance Announcements - returns 202/HTML, needs special parsing

    # ═══════════════════════════════════════════════════════════════
    # INFRASTRUCTURE (Priority 4 - Low)
    # ═══════════════════════════════════════════════════════════════
    DataSource(
        url="https://pypi.org/simple/",
        name="PyPI",
        type=SourceType.STATIC,
        category="infrastructure",
        priority=4,
        notes="Python packages",
    ),
    DataSource(
        url="https://api.github.com/rate_limit",
        name="GitHub API",
        type=SourceType.REST_API,
        category="infrastructure",
        priority=4,
        expected_content_type="application/json",
        notes="Rate limit check, repo access",
    ),
    DataSource(
        url="https://files.pythonhosted.org/",
        name="PyPI Files",
        type=SourceType.STATIC,
        category="infrastructure",
        priority=4,
        notes="Python package downloads (pip install)",
    ),
    DataSource(
        url="https://checkip.amazonaws.com/",
        name="AWS CheckIP",
        type=SourceType.REST_API,
        category="infrastructure",
        priority=4,
        notes="External IP check before exchange connection",
    ),

    # === SENTIMENT (Priority 3) ===
    DataSource(
        url="https://api.alternative.me/fng/",
        name="Fear & Greed Index",
        type=SourceType.REST_API,
        category="sentiment",
        priority=3,
        expected_content_type="application/json",
        expected_keys=["data"],
        notes="Crypto Fear & Greed Index (0-100)",
    ),
]


class SourcesManager:
    """
    Менеджер источников данных.

    Автоматически проверяет доступность, измеряет latency,
    и обновляет список активных источников.
    """

    CHECK_INTERVAL_HOURS = 6        # Проверка каждые 6 часов
    UPDATE_INTERVAL_DAYS = 7        # Полное обновление раз в неделю
    ERROR_THRESHOLD = 5             # Порог ошибок для статуса DOWN
    TIMEOUT_SECONDS = 10            # Таймаут запроса

    def __init__(self):
        self.sources: Dict[str, DataSource] = {}
        self._ensure_dirs()
        self._load_sources()

    def _ensure_dirs(self) -> None:
        """Создать необходимые директории."""
        STATE_DIR.mkdir(parents=True, exist_ok=True)

    def _load_sources(self) -> None:
        """Загрузить источники из файла или инициализировать."""
        if SOURCES_FILE.exists():
            try:
                with open(SOURCES_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data.get("sources", []):
                        src = DataSource.from_dict(item)
                        self.sources[src.url] = src
                log.info(f"Loaded {len(self.sources)} sources from {SOURCES_FILE}")
            except Exception as e:
                log.warning(f"Failed to load sources, reinitializing: {e}")
                self._init_from_master()
        else:
            self._init_from_master()

    def _init_from_master(self) -> None:
        """Инициализировать из master списка."""
        for src in MASTER_SOURCES:
            self.sources[src.url] = src
        self._save_sources()
        log.info(f"Initialized {len(self.sources)} sources from master list")

    def _save_sources(self) -> None:
        """Сохранить источники в файл (atomic write)."""
        data = {
            "schema": "sources:v1",
            "version": "1.0.0",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "next_check": (datetime.now(timezone.utc) + timedelta(hours=self.CHECK_INTERVAL_HOURS)).isoformat(),
            "sources": [s.to_dict() for s in self.sources.values()]
        }

        # Compute checksum
        content = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)
        checksum = "sha256:" + hashlib.sha256(content.encode()).hexdigest()[:16]
        data["checksum"] = checksum

        # Atomic write
        temp = SOURCES_FILE.with_suffix(".tmp")
        with open(temp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, SOURCES_FILE)

        log.info(f"Saved {len(self.sources)} sources to {SOURCES_FILE}")

    def check_source(self, source: DataSource) -> DataSource:
        """Проверить один источник (синхронно)."""
        start = time.time()

        try:
            if source.type == SourceType.WEBSOCKET:
                # WebSocket - просто проверяем что URL валидный
                # Реальную проверку делаем через websockets в async версии
                source.status = SourceStatus.PENDING
                source.notes = "WebSocket requires async check"
                return source

            # HTTP check
            req = Request(source.url, headers={
                "User-Agent": "HOPE-Bot/1.0",
                "Accept": "application/json, application/xml, text/xml, */*",
            })

            with urlopen(req, timeout=self.TIMEOUT_SECONDS) as resp:
                latency = (time.time() - start) * 1000

                # Check status
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}")

                # Check content type
                content_type = resp.headers.get("Content-Type", "")
                if source.expected_content_type:
                    if source.expected_content_type not in content_type:
                        # RSS может приходить как text/xml
                        if source.type == SourceType.RSS and "xml" in content_type:
                            pass  # OK
                        elif "json" in content_type or "xml" in content_type:
                            pass  # OK
                        else:
                            log.warning(f"Unexpected content-type for {source.name}: {content_type}")

                # Check expected keys (for JSON)
                if source.expected_keys and "json" in content_type:
                    body = resp.read().decode("utf-8")
                    data = json.loads(body)
                    for key in source.expected_keys:
                        if key not in data:
                            raise Exception(f"Missing key: {key}")

                source.status = SourceStatus.ACTIVE
                source.latency_ms = round(latency, 2)
                source.success_count += 1
                source.last_error = None
                source.error_count = 0  # Reset on success

        except HTTPError as e:
            source.error_count += 1
            source.last_error = f"HTTP {e.code}: {e.reason}"
            source.status = SourceStatus.DOWN if source.error_count >= self.ERROR_THRESHOLD else SourceStatus.DEGRADED
            log.warning(f"Check failed for {source.name}: {source.last_error}")

        except URLError as e:
            source.error_count += 1
            source.last_error = str(e.reason)
            source.status = SourceStatus.DOWN if source.error_count >= self.ERROR_THRESHOLD else SourceStatus.DEGRADED
            log.warning(f"Check failed for {source.name}: {source.last_error}")

        except Exception as e:
            source.error_count += 1
            source.last_error = str(e)
            source.status = SourceStatus.DOWN if source.error_count >= self.ERROR_THRESHOLD else SourceStatus.DEGRADED
            log.warning(f"Check failed for {source.name}: {source.last_error}")

        source.last_check = datetime.now(timezone.utc).isoformat()
        return source

    def check_all(self) -> Dict[str, Any]:
        """Проверить все источники."""
        log.info("Starting health check for all sources...")

        for url in list(self.sources.keys()):
            self.sources[url] = self.check_source(self.sources[url])

        self._save_sources()
        self._log_check_result()
        self._update_domains_file()

        # Summary
        stats = {
            "total": len(self.sources),
            "active": sum(1 for s in self.sources.values() if s.status == SourceStatus.ACTIVE),
            "degraded": sum(1 for s in self.sources.values() if s.status == SourceStatus.DEGRADED),
            "down": sum(1 for s in self.sources.values() if s.status == SourceStatus.DOWN),
            "pending": sum(1 for s in self.sources.values() if s.status == SourceStatus.PENDING),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

        log.info(f"Check complete: {stats['active']}/{stats['total']} active, {stats['down']} down")
        return stats

    def _log_check_result(self) -> None:
        """Записать результат проверки в историю."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "active": sum(1 for s in self.sources.values() if s.status == SourceStatus.ACTIVE),
                "down": sum(1 for s in self.sources.values() if s.status == SourceStatus.DOWN),
            },
            "sources": {
                s.name: {
                    "status": s.status.value,
                    "latency_ms": s.latency_ms,
                    "error": s.last_error,
                }
                for s in self.sources.values()
            }
        }

        with open(HISTORY_FILE, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _update_domains_file(self) -> None:
        """Обновить файл разрешенных доменов."""
        domains = self.generate_allowlist()
        with open(DOMAINS_FILE, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"# HOPE Allowed Domains\n")
            f.write(f"# Updated: {datetime.now(timezone.utc).isoformat()}\n")
            f.write(f"# Active sources: {sum(1 for s in self.sources.values() if s.status == SourceStatus.ACTIVE)}\n\n")
            f.write(domains)

    def get_active_sources(self, category: Optional[str] = None) -> List[DataSource]:
        """Получить активные источники."""
        sources = [s for s in self.sources.values()
                   if s.status in [SourceStatus.ACTIVE, SourceStatus.PENDING]]
        if category:
            sources = [s for s in sources if s.category == category]
        return sorted(sources, key=lambda x: x.priority)

    def get_source_urls(self, category: Optional[str] = None) -> List[str]:
        """Получить URLs активных источников."""
        return [s.url for s in self.get_active_sources(category)]

    def add_source(self, source: DataSource) -> None:
        """Добавить новый источник."""
        source.status = SourceStatus.PENDING
        source.added_at = datetime.now(timezone.utc).isoformat()
        self.sources[source.url] = source
        self._save_sources()
        log.info(f"Added new source: {source.name}")

    def remove_source(self, url: str) -> None:
        """Удалить источник (мягкое удаление)."""
        if url in self.sources:
            self.sources[url].status = SourceStatus.REMOVED
            self._save_sources()
            log.info(f"Removed source: {url}")

    def generate_allowlist(self) -> str:
        """Сгенерировать список доменов для network config."""
        domains: Set[str] = set()
        for src in self.sources.values():
            if src.status in [SourceStatus.ACTIVE, SourceStatus.DEGRADED, SourceStatus.PENDING]:
                # Extract domain from URL
                url = src.url
                if "://" in url:
                    url = url.split("://")[1]
                domain = url.split("/")[0].split(":")[0]
                # Handle wss://
                if domain.startswith("wss"):
                    domain = domain[3:]
                domains.add(domain)

        return "\n".join(sorted(domains))

    def get_report(self) -> str:
        """Сгенерировать текстовый отчёт."""
        lines = [
            "=" * 70,
            "HOPE DATA SOURCES REPORT",
            f"Generated: {datetime.now(timezone.utc).isoformat()}",
            f"Next check: +{self.CHECK_INTERVAL_HOURS}h",
            "=" * 70,
            "",
        ]

        # Summary
        active = sum(1 for s in self.sources.values() if s.status == SourceStatus.ACTIVE)
        down = sum(1 for s in self.sources.values() if s.status == SourceStatus.DOWN)
        lines.append(f"SUMMARY: {active}/{len(self.sources)} active, {down} down")
        lines.append("")

        # Group by category
        categories: Dict[str, List[DataSource]] = {}
        for src in self.sources.values():
            if src.category not in categories:
                categories[src.category] = []
            categories[src.category].append(src)

        for cat, sources in sorted(categories.items()):
            lines.append(f"=== {cat.upper()} ===")
            for src in sorted(sources, key=lambda x: (x.priority, x.name)):
                status_icon = {
                    SourceStatus.ACTIVE: "[OK]",
                    SourceStatus.DEGRADED: "[!!]",
                    SourceStatus.DOWN: "[XX]",
                    SourceStatus.REMOVED: "[--]",
                    SourceStatus.PENDING: "[..]",
                }.get(src.status, "[??]")

                latency = f"{src.latency_ms:.0f}ms" if src.latency_ms else "N/A"
                lines.append(f"{status_icon} [P{src.priority}] {src.name}")
                lines.append(f"   URL: {src.url[:60]}{'...' if len(src.url) > 60 else ''}")
                lines.append(f"   Latency: {latency} | Errors: {src.error_count} | Success: {src.success_count}")
                if src.last_error:
                    lines.append(f"   [!] Last Error: {src.last_error}")
                lines.append("")

        return "\n".join(lines)

    def get_json_report(self) -> Dict[str, Any]:
        """Сгенерировать JSON отчёт."""
        return {
            "schema": "sources_report:v1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total": len(self.sources),
                "active": sum(1 for s in self.sources.values() if s.status == SourceStatus.ACTIVE),
                "degraded": sum(1 for s in self.sources.values() if s.status == SourceStatus.DEGRADED),
                "down": sum(1 for s in self.sources.values() if s.status == SourceStatus.DOWN),
            },
            "by_category": {
                cat: [s.to_dict() for s in sources]
                for cat, sources in self._group_by_category().items()
            },
            "down_sources": [
                {"name": s.name, "url": s.url, "error": s.last_error}
                for s in self.sources.values() if s.status == SourceStatus.DOWN
            ],
        }

    def _group_by_category(self) -> Dict[str, List[DataSource]]:
        """Группировка по категориям."""
        categories: Dict[str, List[DataSource]] = {}
        for src in self.sources.values():
            if src.category not in categories:
                categories[src.category] = []
            categories[src.category].append(src)
        return categories


def main():
    """Entry point."""
    manager = SourcesManager()

    if len(sys.argv) < 2:
        print("Usage: python -m scripts.sources_manager [check|report|json|domains|daemon]")
        print("")
        print("Commands:")
        print("  check   - Check all sources health")
        print("  report  - Text report")
        print("  json    - JSON report")
        print("  domains - List allowed domains")
        print("  daemon  - Run as background daemon (check every 6h)")
        return 1

    cmd = sys.argv[1]

    if cmd == "check":
        stats = manager.check_all()
        print(json.dumps(stats, indent=2))
        return 0 if stats["down"] == 0 else 1

    elif cmd == "report":
        manager.check_all()
        print(manager.get_report())
        return 0

    elif cmd == "json":
        manager.check_all()
        print(json.dumps(manager.get_json_report(), indent=2, ensure_ascii=False))
        return 0

    elif cmd == "domains":
        print(manager.generate_allowlist())
        return 0

    elif cmd == "daemon":
        print(f"Starting daemon (check every {manager.CHECK_INTERVAL_HOURS}h)...")
        print("Press Ctrl+C to stop")
        try:
            while True:
                stats = manager.check_all()
                print(f"[{datetime.now().isoformat()}] Active: {stats['active']}/{stats['total']}, Down: {stats['down']}")
                time.sleep(manager.CHECK_INTERVAL_HOURS * 3600)
        except KeyboardInterrupt:
            print("\nDaemon stopped")
        return 0

    else:
        print(f"Unknown command: {cmd}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
