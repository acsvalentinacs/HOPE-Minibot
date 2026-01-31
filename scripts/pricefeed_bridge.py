# -*- coding: utf-8 -*-
"""
HOPE v4.0 PRICEFEED BRIDGE
==========================

Записывает цены с Binance в state/ai/pricefeed.json
для использования Eye of God и pretrade_pipeline.

ЗАПУСК:
    python scripts/pricefeed_bridge.py

РЕЖИМ ДЕМОНА:
    python scripts/pricefeed_bridge.py --daemon

ПРОВЕРКА:
    python scripts/pricefeed_bridge.py --check
"""

import os
import sys
import json
import time
import logging
import argparse
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)

# Пути
STATE_DIR = Path("state/ai")
PRICEFEED_PATH = STATE_DIR / "pricefeed.json"
HEALTH_PATH = STATE_DIR / "pricefeed_health.json"

# Конфигурация
UPDATE_INTERVAL_SEC = 5  # Обновление каждые 5 секунд
MAX_PRICE_AGE_SEC = 30   # Максимальный возраст цены

# Символы для отслеживания (из signal_filter_rules.json + AI recommendations)
TRACKED_SYMBOLS = [
    # Core allowed
    "PEPEUSDT", "DOGEUSDT", "SHIBUSDT", "SUIUSDT", "AVAXUSDT",
    "ADAUSDT", "LINKUSDT", "AAVEUSDT", "NEARUSDT",
    "ENSOUSDT", "WLDUSDT", "ZECUSDT", "ARBUSDT", "OPUSDT", "APTUSDT",
    # AI Tier 1
    "FLOWUSDT", "SYNUSDT", "SOMIUSDT", "FTTUSDT", "FIDAUSDT", "BIFIUSDT",
    # AI Tier 2
    "0GUSDT", "DUSDT", "ZKCUSDT", "INITUSDT", "USTCUSDT", "NOMUSDT", "VANRYUSDT", "MANTAUSDT",
    # Pump detector signals (dynamically added)
    "SENTUSDT", "DUSKUSDT", "UXLINKUSDT", "AXLUSDT", "TRUMPUSDT",
    # Heavy (для логирования, не торгуем)
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
]


def atomic_write_json(path: Path, data: Dict[str, Any]) -> bool:
    """
    Атомарная запись JSON: temp → fsync → replace
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        
        # Записываем во временный файл
        fd, tmp_path = tempfile.mkstemp(
            suffix=".tmp",
            prefix="pricefeed_",
            dir=path.parent
        )
        
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            
            # Атомарная замена
            os.replace(tmp_path, path)
            return True
            
        except Exception as e:
            # Cleanup temp file
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise e
            
    except Exception as e:
        log.error(f"Atomic write failed: {e}")
        return False


def fetch_prices_rest(symbols: List[str]) -> Dict[str, float]:
    """
    Получить цены через REST API (fallback).
    """
    try:
        import httpx
        
        prices = {}
        
        # Batch request для всех символов
        url = "https://api.binance.com/api/v3/ticker/price"
        
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            
            all_prices = resp.json()
            price_map = {p["symbol"]: float(p["price"]) for p in all_prices}
            
            for sym in symbols:
                if sym in price_map:
                    prices[sym] = price_map[sym]
        
        return prices
        
    except ImportError:
        log.warning("httpx not installed, trying requests...")
        try:
            import requests
            
            prices = {}
            url = "https://api.binance.com/api/v3/ticker/price"
            
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            
            all_prices = resp.json()
            price_map = {p["symbol"]: float(p["price"]) for p in all_prices}
            
            for sym in symbols:
                if sym in price_map:
                    prices[sym] = price_map[sym]
            
            return prices
            
        except Exception as e:
            log.error(f"REST fallback failed: {e}")
            return {}
            
    except Exception as e:
        log.error(f"Price fetch failed: {e}")
        return {}


def build_pricefeed_json(prices: Dict[str, float]) -> Dict[str, Any]:
    """
    Построить структуру pricefeed.json
    """
    now = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()
    
    prices_dict = {}
    for symbol, price in prices.items():
        prices_dict[symbol] = {
            "price": price,
            "ts_unix": now,
            "updated": now_iso,
        }
    
    return {
        "schema": "pricefeed_v1",
        "produced_unix": now,
        "produced": now_iso,
        "count": len(prices),
        "max_age_sec": MAX_PRICE_AGE_SEC,
        "prices": prices_dict,
    }


def update_pricefeed() -> bool:
    """
    Обновить pricefeed.json
    """
    log.debug(f"Fetching prices for {len(TRACKED_SYMBOLS)} symbols...")
    
    prices = fetch_prices_rest(TRACKED_SYMBOLS)
    
    if not prices:
        log.warning("No prices fetched!")
        return False
    
    pricefeed = build_pricefeed_json(prices)
    
    if atomic_write_json(PRICEFEED_PATH, pricefeed):
        log.info(f"Updated pricefeed: {len(prices)} prices")
        return True
    else:
        log.error("Failed to write pricefeed!")
        return False


def update_health(status: str, prices_count: int = 0):
    """
    Обновить health файл
    """
    health = {
        "component": "pricefeed_bridge",
        "status": status,
        "ts_unix": time.time(),
        "updated": datetime.now(timezone.utc).isoformat(),
        "prices_count": prices_count,
        "pid": os.getpid(),
    }
    atomic_write_json(HEALTH_PATH, health)


def check_pricefeed() -> bool:
    """
    Проверить состояние pricefeed.json
    """
    print("=" * 60)
    print("PRICEFEED CHECK")
    print("=" * 60)
    
    if not PRICEFEED_PATH.exists():
        print(f"[FAIL] File not found: {PRICEFEED_PATH}")
        return False
    
    try:
        data = json.loads(PRICEFEED_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[FAIL] JSON parse error: {e}")
        return False
    
    schema = data.get("schema")
    if schema != "pricefeed_v1":
        print(f"[FAIL] Wrong schema: {schema}")
        return False
    
    produced = data.get("produced_unix", 0)
    age = time.time() - produced
    
    if age > MAX_PRICE_AGE_SEC:
        print(f"[WARN] Stale data: {age:.1f}s old (max {MAX_PRICE_AGE_SEC}s)")
    else:
        print(f"[OK] Fresh data: {age:.1f}s old")
    
    prices = data.get("prices", {})
    print(f"[OK] Prices count: {len(prices)}")
    
    # Sample prices
    print("\nSample prices:")
    for sym in ["BTCUSDT", "ETHUSDT", "PEPEUSDT", "ENSOUSDT"][:4]:
        if sym in prices:
            p = prices[sym]
            print(f"  {sym}: ${p['price']:.8f}")
    
    print("\n[PASS] Pricefeed OK")
    return True


def daemon_loop():
    """
    Основной цикл демона
    """
    log.info("Starting pricefeed bridge daemon...")
    log.info(f"Output: {PRICEFEED_PATH}")
    log.info(f"Interval: {UPDATE_INTERVAL_SEC}s")
    log.info(f"Tracking: {len(TRACKED_SYMBOLS)} symbols")
    
    consecutive_failures = 0
    
    while True:
        try:
            if update_pricefeed():
                consecutive_failures = 0
                update_health("OK", len(TRACKED_SYMBOLS))
            else:
                consecutive_failures += 1
                update_health("DEGRADED")
                
                if consecutive_failures >= 5:
                    log.critical("Too many failures, but continuing...")
            
            time.sleep(UPDATE_INTERVAL_SEC)
            
        except KeyboardInterrupt:
            log.info("Shutdown requested...")
            update_health("STOPPED")
            break
            
        except Exception as e:
            log.error(f"Loop error: {e}")
            consecutive_failures += 1
            update_health("ERROR")
            time.sleep(UPDATE_INTERVAL_SEC)


def main():
    parser = argparse.ArgumentParser(description="HOPE PriceFeed Bridge")
    parser.add_argument("--daemon", action="store_true", help="Run as daemon")
    parser.add_argument("--check", action="store_true", help="Check pricefeed status")
    parser.add_argument("--once", action="store_true", help="Update once and exit")
    
    args = parser.parse_args()
    
    if args.check:
        sys.exit(0 if check_pricefeed() else 1)
    
    if args.once:
        if update_pricefeed():
            print(f"[OK] Updated {PRICEFEED_PATH}")
            sys.exit(0)
        else:
            print("[FAIL] Update failed")
            sys.exit(1)
    
    if args.daemon:
        daemon_loop()
    else:
        # Default: update once and show status
        update_pricefeed()
        check_pricefeed()


if __name__ == "__main__":
    main()
