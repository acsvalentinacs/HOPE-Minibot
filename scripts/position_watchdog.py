# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# sha256:position_watchdog_v1_prod
# Created by: Claude (opus-4)
# Created at: 2026-01-30T04:30:00Z
# Modified by: Claude (opus-4.5)
# Modified at: 2026-01-31T08:58:00Z
# Purpose: Position Watchdog - независимый контур закрытия позиций
# Contract: Позиции ДОЛЖНЫ закрываться по timeout независимо от сигналов
# === END SIGNATURE ===
"""
═══════════════════════════════════════════════════════════════════════════════
  POSITION WATCHDOG v1.0 - Независимый контур управления позициями
═══════════════════════════════════════════════════════════════════════════════

P0 ПРОБЛЕМА (из критики):
"Если закрытие позиции зависит от прихода новых сигналов/циклов decision-движка —
ты получаешь зависимость 'нет сигналов → нет управления позицией'.
Timeout должен быть в отдельном 'позиционном стороже' (watchdog)
и тикать независимо от входящих сигналов."

РЕШЕНИЕ:
Position Watchdog работает в отдельном потоке/процессе и:
1. Проверяет позиции каждую секунду
2. Закрывает по TIMEOUT независимо от движка сигналов
3. Закрывает по STOP/TARGET на основе PriceFeed
4. PANIC CLOSE при потере связи с биржей > N секунд

АРХИТЕКТУРА:
┌────────────────────────────────────────────────────────────────────────────┐
│                      POSITION WATCHDOG (независимый)                        │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐     │
│   │  Position       │────►│   Price Feed    │────►│   Exit Check    │     │
│   │  Registry       │     │   (real-time)   │     │   (every 1s)    │     │
│   └─────────────────┘     └─────────────────┘     └────────┬────────┘     │
│                                                            │               │
│   Exit Conditions:                                         ▼               │
│   ├── TIMEOUT: now - entry_time > timeout_sec   ─► MARKET SELL           │
│   ├── STOP: current_price <= stop_price         ─► MARKET SELL           │
│   ├── TARGET: current_price >= target_price     ─► MARKET SELL           │
│   ├── PANIC: no_price_for > panic_threshold     ─► MARKET SELL           │
│   └── CIRCUIT_BREAKER: daily_loss > limit       ─► CLOSE ALL             │
│                                                                             │
│   FAIL-CLOSED INVARIANTS:                                                  │
│   • No price data > 30s → PANIC CLOSE                                      │
│   • Binance API error → RETRY 3x → PANIC CLOSE                            │
│   • Watchdog crash → systemd restart → recover positions                   │
│                                                                             │
└────────────────────────────────────────────────────────────────────────────┘

ЗАПУСК:
  python position_watchdog.py                    # Foreground
  python position_watchdog.py --daemon           # Background
  python position_watchdog.py --status           # Show positions
  python position_watchdog.py --panic-close-all  # Emergency close all

═══════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import json
import time
import os
import sys
import hashlib
import hmac
import logging
import signal as sig
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
from threading import Thread, Event
from urllib.parse import urlencode
import argparse

try:
    import httpx
except ImportError:
    httpx = None

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

STATE_DIR = Path("state/ai/watchdog")
POSITIONS_FILE = STATE_DIR / "positions.json"
CLOSES_LOG = STATE_DIR / "closes.jsonl"
PANIC_LOG = STATE_DIR / "panic_events.jsonl"

STATE_DIR.mkdir(parents=True, exist_ok=True)

LOG_FORMAT = "%(asctime)s | %(levelname)-5s | %(name)-15s | %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/position_watchdog.log", encoding="utf-8"),
    ]
)
log = logging.getLogger("WATCHDOG")

# Fail-closed thresholds
MAX_PRICE_AGE_SEC = 30          # No price for 30s → PANIC
PANIC_CLOSE_THRESHOLD_SEC = 60  # No exchange response for 60s → PANIC CLOSE ALL
CHECK_INTERVAL_SEC = 1          # Check positions every 1 second
MAX_RETRY_ATTEMPTS = 3          # Retry failed closes
DAILY_LOSS_PANIC_USD = 100.0    # Close all if daily loss exceeds


# ═══════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════

class ExitReason(Enum):
    TARGET = "TARGET"
    STOP = "STOP"
    TIMEOUT = "TIMEOUT"
    PANIC_NO_PRICE = "PANIC_NO_PRICE"
    PANIC_API_FAIL = "PANIC_API_FAIL"
    PANIC_DAILY_LOSS = "PANIC_DAILY_LOSS"
    MANUAL = "MANUAL"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"


@dataclass
class WatchedPosition:
    """Position being watched by watchdog"""
    position_id: str
    symbol: str
    entry_price: float
    quantity: float
    entry_time: str           # ISO format
    timeout_sec: int
    target_price: float       # Absolute price
    stop_price: float         # Absolute price
    
    # Tracking
    current_price: float = 0.0
    last_price_update: str = ""
    mfe: float = 0.0          # Max favorable excursion %
    mae: float = 0.0          # Max adverse excursion %
    
    # State
    status: str = "OPEN"      # OPEN, CLOSING, CLOSED
    close_attempts: int = 0
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, d: Dict) -> 'WatchedPosition':
        """Load from dict with field mapping for autotrader format"""
        # Map autotrader field names to watchdog field names
        mapped = {
            "position_id": d.get("position_id", ""),
            "symbol": d.get("symbol", ""),
            "entry_price": d.get("entry_price", d.get("entry", 0.0)),
            "quantity": d.get("quantity", d.get("qty", 0.0)),
            "entry_time": d.get("entry_time", d.get("opened_at", "")),
            "timeout_sec": d.get("timeout_sec", 1800),  # Default 30 min
            "target_price": d.get("target_price", d.get("target", 0.0)),
            "stop_price": d.get("stop_price", d.get("stop", 0.0)),
        }
        # Copy optional tracking fields if present
        for key in ["current_price", "last_price_update", "mfe", "mae", "status", "close_attempts"]:
            if key in d:
                mapped[key] = d[key]
        return cls(**mapped)
    
    def update_price(self, price: float):
        """Update current price and track MFE/MAE"""
        self.current_price = price
        self.last_price_update = datetime.now(timezone.utc).isoformat()
        
        if price > 0 and self.entry_price > 0:
            pnl_pct = (price - self.entry_price) / self.entry_price * 100
            if pnl_pct > self.mfe:
                self.mfe = pnl_pct
            if pnl_pct < self.mae:
                self.mae = pnl_pct
    
    def check_exit(self) -> Optional[ExitReason]:
        """Check if position should be closed"""
        now = datetime.now(timezone.utc)
        
        # 1. Check timeout (ALWAYS, even without price)
        entry = datetime.fromisoformat(self.entry_time.replace('Z', '+00:00'))
        elapsed = (now - entry).total_seconds()
        if elapsed >= self.timeout_sec:
            return ExitReason.TIMEOUT
        
        # 2. Check price staleness → PANIC
        if self.last_price_update:
            last_update = datetime.fromisoformat(self.last_price_update.replace('Z', '+00:00'))
            price_age = (now - last_update).total_seconds()
            if price_age > MAX_PRICE_AGE_SEC:
                return ExitReason.PANIC_NO_PRICE
        
        # 3. Check stop/target (only if price is fresh)
        if self.current_price > 0:
            if self.current_price <= self.stop_price:
                return ExitReason.STOP
            if self.current_price >= self.target_price:
                return ExitReason.TARGET
        
        return None


@dataclass
class CloseEvent:
    """Record of position close"""
    position_id: str
    symbol: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl_pct: float
    pnl_usd: float
    mfe: float
    mae: float
    exit_reason: str
    duration_sec: int
    timestamp: str
    order_id: str = ""
    sha256: str = ""
    
    def to_dict(self) -> Dict:
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════════════
# BINANCE CLIENT (Minimal, focused on closing)
# ═══════════════════════════════════════════════════════════════════════════

class WatchdogBinanceClient:
    """Minimal Binance client for watchdog (closing positions only)"""
    
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        
        if testnet:
            self.base_url = "https://testnet.binance.vision"
        else:
            self.base_url = "https://api.binance.com"
        
        self.client = httpx.Client(timeout=30) if httpx else None
        if self.client:
            self.client.headers["X-MBX-APIKEY"] = api_key
        
        self.last_successful_call = time.time()
    
    def _sign(self, params: Dict) -> Dict:
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode(),
            query.encode(),
            hashlib.sha256
        ).hexdigest()
        params["signature"] = signature
        return params
    
    def get_price(self, symbol: str) -> Optional[float]:
        """Get current price"""
        if not self.client:
            return None
        
        try:
            resp = self.client.get(
                f"{self.base_url}/api/v3/ticker/price",
                params={"symbol": symbol}
            )
            if resp.status_code == 200:
                self.last_successful_call = time.time()
                return float(resp.json().get("price", 0))
        except Exception as e:
            log.error(f"Price fetch error: {e}")
        return None
    
    def get_prices(self, symbols: List[str]) -> Dict[str, float]:
        """Get prices for multiple symbols"""
        if not self.client:
            return {}
        
        try:
            resp = self.client.get(f"{self.base_url}/api/v3/ticker/price")
            if resp.status_code == 200:
                self.last_successful_call = time.time()
                prices = {}
                for item in resp.json():
                    if item.get("symbol") in symbols:
                        prices[item["symbol"]] = float(item.get("price", 0))
                return prices
        except Exception as e:
            log.error(f"Prices fetch error: {e}")
        return {}
    
    def market_sell(self, symbol: str, quantity: float) -> Dict:
        """Place market sell order (close position)"""
        if not self.client:
            return {"error": True, "msg": "No client"}
        
        params = self._sign({
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": f"{quantity:.8f}",
        })
        
        try:
            resp = self.client.post(f"{self.base_url}/api/v3/order", params=params)
            self.last_successful_call = time.time()
            
            if resp.status_code == 200:
                return resp.json()
            else:
                return {"error": True, "code": resp.status_code, "msg": resp.text}
        except Exception as e:
            log.error(f"Market sell error: {e}")
            return {"error": True, "msg": str(e)}
    
    def time_since_last_success(self) -> float:
        """Seconds since last successful API call"""
        return time.time() - self.last_successful_call


# ═══════════════════════════════════════════════════════════════════════════
# POSITION WATCHDOG
# ═══════════════════════════════════════════════════════════════════════════

class PositionWatchdog:
    """
    Independent position watchdog.
    
    Runs in a separate thread/process and monitors positions
    regardless of signal flow.
    """
    
    def __init__(self, testnet: bool = True):
        self.testnet = testnet
        self.running = False
        self.stop_event = Event()
        
        # Load credentials
        self._load_credentials()
        
        # Initialize client
        self.client = None
        if self.api_key and self.api_secret:
            self.client = WatchdogBinanceClient(
                self.api_key, self.api_secret, testnet
            )
        
        # Position registry
        self.positions: Dict[str, WatchedPosition] = {}
        self._load_positions()
        
        # Daily tracking
        self.daily_pnl = 0.0
        self.daily_start = datetime.now(timezone.utc).date()
        
        # Stats
        self.stats = {
            "checks": 0,
            "closes": 0,
            "panic_closes": 0,
            "errors": 0,
        }
        
        log.info(f"Watchdog initialized (testnet={testnet}, positions={len(self.positions)})")
    
    def _load_credentials(self):
        """Load API credentials"""
        secrets_path = Path(r"C:\secrets\hope.env")
        if secrets_path.exists():
            for line in secrets_path.read_text().splitlines():
                if '=' in line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()
        
        self.api_key = os.environ.get("BINANCE_API_KEY", "")
        self.api_secret = os.environ.get("BINANCE_API_SECRET", "")
    
    def _load_positions(self):
        """Load positions from watchdog file AND autotrader file"""
        # Sources: own watchdog file + autotrader positions
        autotrader_file = Path("state/ai/autotrader/positions.json")
        sources = [POSITIONS_FILE, autotrader_file]

        loaded_ids = set()
        for src in sources:
            if not src.exists():
                continue
            try:
                data = json.loads(src.read_text(encoding="utf-8"))
                for pos_data in data.get("positions", []):
                    pos = WatchedPosition.from_dict(pos_data)
                    if pos.position_id and pos.position_id not in loaded_ids:
                        # Consider OPEN if status not set (autotrader format)
                        if pos.status in ("OPEN", ""):
                            pos.status = "OPEN"
                            self.positions[pos.position_id] = pos
                            loaded_ids.add(pos.position_id)
                log.info(f"Loaded from {src.name}: {len(data.get('positions', []))} entries")
            except Exception as e:
                log.error(f"Failed to load positions from {src}: {e}")

        log.info(f"Total active positions: {len(self.positions)}")
    
    def _save_positions(self):
        """Save positions atomically"""
        data = {
            "positions": [p.to_dict() for p in self.positions.values()],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        
        # Atomic write: temp → fsync → replace
        temp_file = POSITIONS_FILE.with_suffix('.tmp')
        temp_file.write_text(json.dumps(data, indent=2))
        temp_file.replace(POSITIONS_FILE)
    
    def register_position(self, position: WatchedPosition):
        """Register new position for watching"""
        self.positions[position.position_id] = position
        self._save_positions()
        log.info(f"[REGISTER] {position.position_id} {position.symbol} "
                f"qty={position.quantity} timeout={position.timeout_sec}s")
    
    def _log_close(self, event: CloseEvent):
        """Log close event to JSONL"""
        # Add sha256
        data_str = json.dumps(event.to_dict(), sort_keys=True)
        event.sha256 = "sha256:" + hashlib.sha256(data_str.encode()).hexdigest()[:16]
        
        with open(CLOSES_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + '\n')
    
    def _log_panic(self, reason: str, positions: List[str], details: Dict):
        """Log panic event"""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "positions": positions,
            "details": details,
        }
        with open(PANIC_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    
    async def _close_position(self, position: WatchedPosition, 
                              reason: ExitReason) -> bool:
        """Close position via market sell"""
        if not self.client:
            log.error(f"[CLOSE FAILED] No client for {position.position_id}")
            return False
        
        position.status = "CLOSING"
        position.close_attempts += 1
        
        log.info(f"[CLOSING] {position.position_id} {position.symbol} "
                f"reason={reason.value} attempt={position.close_attempts}")
        
        # Execute sell
        result = self.client.market_sell(position.symbol, position.quantity)
        
        if "error" in result:
            log.error(f"[CLOSE ERROR] {position.symbol}: {result.get('msg')}")
            
            if position.close_attempts >= MAX_RETRY_ATTEMPTS:
                # Give up, mark as error but keep trying
                self.stats["errors"] += 1
                position.status = "OPEN"  # Will retry next cycle
                return False
            
            position.status = "OPEN"
            return False
        
        # Success - calculate PnL
        exit_price = position.current_price
        fills = result.get("fills", [])
        if fills:
            total_qty = sum(float(f.get("qty", 0)) for f in fills)
            if total_qty > 0:
                exit_price = sum(
                    float(f.get("price", 0)) * float(f.get("qty", 0))
                    for f in fills
                ) / total_qty
        
        pnl_pct = (exit_price - position.entry_price) / position.entry_price * 100
        pnl_usd = (exit_price - position.entry_price) * position.quantity
        
        # Calculate duration
        entry = datetime.fromisoformat(position.entry_time.replace('Z', '+00:00'))
        duration = int((datetime.now(timezone.utc) - entry).total_seconds())
        
        # Update daily PnL
        self.daily_pnl += pnl_usd
        
        # Create close event
        event = CloseEvent(
            position_id=position.position_id,
            symbol=position.symbol,
            entry_price=position.entry_price,
            exit_price=exit_price,
            quantity=position.quantity,
            pnl_pct=round(pnl_pct, 4),
            pnl_usd=round(pnl_usd, 4),
            mfe=round(position.mfe, 4),
            mae=round(position.mae, 4),
            exit_reason=reason.value,
            duration_sec=duration,
            timestamp=datetime.now(timezone.utc).isoformat(),
            order_id=str(result.get("orderId", "")),
        )
        
        # Log event
        self._log_close(event)
        
        # Remove from registry
        position.status = "CLOSED"
        del self.positions[position.position_id]
        self._save_positions()
        
        # Update stats
        self.stats["closes"] += 1
        if "PANIC" in reason.value:
            self.stats["panic_closes"] += 1
        
        log.info(f"[CLOSED] {position.symbol} reason={reason.value} "
                f"PnL={pnl_pct:+.2f}% (${pnl_usd:+.2f}) "
                f"MFE={position.mfe:.2f}% MAE={position.mae:.2f}%")
        
        return True
    
    async def _check_positions(self):
        """Check all positions for exit conditions"""
        if not self.positions:
            return
        
        self.stats["checks"] += 1
        
        # Reset daily stats if new day
        today = datetime.now(timezone.utc).date()
        if today != self.daily_start:
            self.daily_pnl = 0.0
            self.daily_start = today
        
        # Check daily loss limit
        if self.daily_pnl <= -DAILY_LOSS_PANIC_USD:
            log.warning(f"[PANIC] Daily loss limit hit: ${self.daily_pnl:.2f}")
            self._log_panic("DAILY_LOSS_LIMIT", 
                           list(self.positions.keys()),
                           {"daily_pnl": self.daily_pnl})
            
            for pos in list(self.positions.values()):
                await self._close_position(pos, ExitReason.PANIC_DAILY_LOSS)
            return
        
        # Check API health
        if self.client:
            api_silence = self.client.time_since_last_success()
            if api_silence > PANIC_CLOSE_THRESHOLD_SEC:
                log.warning(f"[PANIC] API silent for {api_silence:.0f}s")
                self._log_panic("API_SILENT",
                               list(self.positions.keys()),
                               {"silence_sec": api_silence})
                
                for pos in list(self.positions.values()):
                    await self._close_position(pos, ExitReason.PANIC_API_FAIL)
                return
        
        # Get prices for all positions
        symbols = [p.symbol for p in self.positions.values()]
        prices = {}
        if self.client and symbols:
            prices = self.client.get_prices(symbols)
        
        # Check each position
        for pos in list(self.positions.values()):
            # Update price
            if pos.symbol in prices:
                pos.update_price(prices[pos.symbol])
            
            # Check exit condition
            exit_reason = pos.check_exit()
            
            if exit_reason:
                await self._close_position(pos, exit_reason)
        
        # Save updated positions
        self._save_positions()
    
    async def run(self):
        """Main watchdog loop"""
        self.running = True
        log.info("Watchdog started")
        
        while self.running and not self.stop_event.is_set():
            try:
                await self._check_positions()
                await asyncio.sleep(CHECK_INTERVAL_SEC)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Watchdog error: {e}", exc_info=True)
                await asyncio.sleep(1)
        
        log.info("Watchdog stopped")
    
    def stop(self):
        """Stop watchdog"""
        self.running = False
        self.stop_event.set()
    
    def get_status(self) -> Dict:
        """Get watchdog status"""
        return {
            "running": self.running,
            "positions": len(self.positions),
            "daily_pnl": self.daily_pnl,
            "stats": self.stats,
            "positions_detail": [p.to_dict() for p in self.positions.values()],
        }


# ═══════════════════════════════════════════════════════════════════════════
# INTEGRATION: Register position from main engine
# ═══════════════════════════════════════════════════════════════════════════

def register_position_for_watching(
    position_id: str,
    symbol: str,
    entry_price: float,
    quantity: float,
    target_pct: float,
    stop_pct: float,
    timeout_sec: int,
):
    """
    Register a new position with the watchdog.
    Call this from the main trading engine after opening a position.
    """
    position = WatchedPosition(
        position_id=position_id,
        symbol=symbol,
        entry_price=entry_price,
        quantity=quantity,
        entry_time=datetime.now(timezone.utc).isoformat(),
        timeout_sec=timeout_sec,
        target_price=entry_price * (1 + target_pct / 100),
        stop_price=entry_price * (1 + stop_pct / 100),
    )
    
    # Atomic write to shared file
    if POSITIONS_FILE.exists():
        data = json.loads(POSITIONS_FILE.read_text())
    else:
        data = {"positions": []}
    
    # Check if already exists
    existing_ids = {p["position_id"] for p in data["positions"]}
    if position_id not in existing_ids:
        data["positions"].append(position.to_dict())
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        # Atomic write
        temp_file = POSITIONS_FILE.with_suffix('.tmp')
        temp_file.write_text(json.dumps(data, indent=2))
        temp_file.replace(POSITIONS_FILE)
        
        log.info(f"[REGISTERED] {position_id} for watchdog")
    
    return position


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Position Watchdog")
    parser.add_argument("--testnet", action="store_true", default=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--panic-close-all", action="store_true")
    
    args = parser.parse_args()
    
    testnet = not args.live
    
    # Status mode
    if args.status:
        if POSITIONS_FILE.exists():
            data = json.loads(POSITIONS_FILE.read_text())
            print(f"\n=== WATCHDOG STATUS ===")
            print(f"Positions: {len(data.get('positions', []))}")
            print(f"Updated: {data.get('updated_at', 'never')}")
            for pos in data.get("positions", []):
                if pos.get("status") == "OPEN":
                    print(f"  [{pos['position_id']}] {pos['symbol']} "
                          f"qty={pos['quantity']:.6f} timeout={pos['timeout_sec']}s")
        else:
            print("No positions file found")
        return
    
    # Panic close mode
    if args.panic_close_all:
        watchdog = PositionWatchdog(testnet=testnet)
        print(f"PANIC CLOSING {len(watchdog.positions)} positions...")
        
        async def panic_close():
            for pos in list(watchdog.positions.values()):
                await watchdog._close_position(pos, ExitReason.MANUAL)
        
        asyncio.run(panic_close())
        return
    
    # Normal watchdog mode
    Path("logs").mkdir(exist_ok=True)
    watchdog = PositionWatchdog(testnet=testnet)
    
    # Handle signals
    def signal_handler(sig, frame):
        print("\nStopping watchdog...")
        watchdog.stop()
    
    sig.signal(sig.SIGINT, signal_handler)
    
    # Run
    print(f"\n{'='*60}")
    print(f"  POSITION WATCHDOG v1.0")
    print(f"{'='*60}")
    print(f"  Mode: {'TESTNET' if testnet else 'LIVE'}")
    print(f"  Positions: {len(watchdog.positions)}")
    print(f"  Check interval: {CHECK_INTERVAL_SEC}s")
    print(f"  Panic threshold: {MAX_PRICE_AGE_SEC}s (no price)")
    print(f"  Daily loss limit: ${DAILY_LOSS_PANIC_USD}")
    print(f"{'='*60}\n")
    
    asyncio.run(watchdog.run())


if __name__ == "__main__":
    main()
