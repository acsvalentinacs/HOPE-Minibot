# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 14:30:00 UTC
# Purpose: HOPE AI MoonBot-AutoTrader Integration - connects signals to real trading
# sha256: moonbot_integration_v1.0
# === END SIGNATURE ===
"""
HOPE AI - MoonBot to AutoTrader Integration v1.0

Connects MoonBot signal detection to AutoTrader for real trading.

COMPLETE FLOW:
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                              │
│   MOONBOT                      INTEGRATION                    AUTOTRADER    │
│  (Telegram)                    (this script)                  (executor)    │
│      │                              │                              │        │
│      │  Signal detected             │                              │        │
│      │────────────────────────────>│                              │        │
│      │  "ENJUSDT buys/sec=32.91"   │                              │        │
│      │                              │                              │        │
│      │                              │  Parse & validate           │        │
│      │                              │  Check thresholds           │        │
│      │                              │  Calculate confidence       │        │
│      │                              │                              │        │
│      │                              │  POST /signal                │        │
│      │                              │────────────────────────────>│        │
│      │                              │                              │        │
│      │                              │                              │ BUY    │
│      │                              │                              │──────> │
│      │                              │                              │Binance │
│      │                              │                              │        │
└─────────────────────────────────────────────────────────────────────────────┘

Usage:
    # Watch MoonBot logs and send to AutoTrader
    python moonbot_autotrader.py --watch-file moonbot.log
    
    # Or pipe from MoonBot process
    moonbot.exe | python moonbot_autotrader.py --stdin
    
    # Test single signal
    python moonbot_autotrader.py --test "ENJUSDT PumpDetection buys/sec: 32.91"
"""

import json
import re
import sys
import time
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional
from dataclasses import dataclass, asdict

try:
    import httpx
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "httpx", "-q"])
    import httpx

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

AUTOTRADER_URL = "http://127.0.0.1:8200"  # AutoTrader API
GATEWAY_URL = "http://127.0.0.1:8100"     # AI Gateway

# Signal thresholds for filtering
MIN_BUYS_SEC = 10      # Minimum buys/sec to consider
MIN_DELTA = 1.0        # Minimum delta % to consider


# ═══════════════════════════════════════════════════════════════════════════════
# MOONBOT PARSER
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class MoonBotSignal:
    """Parsed MoonBot signal"""
    timestamp: str
    symbol: str
    strategy: str
    direction: str
    price: float
    buys_per_sec: float
    vol_per_sec: float
    delta_pct: float
    vol_raise_pct: float
    daily_volume: float
    raw_line: str


class MoonBotParser:
    """Parses MoonBot log output"""
    
    # Regex patterns (flexible order - handles both strategy before/after symbol)
    # Format: "08:19:51 Signal USDT-DUSK Ask:0.10950 [Pumpdetect1_USDT] DailyVol: 12.4m Buys/sec: 25.71 Vol/sec: 14.2k PriceDelta: 1.89%"
    PUMP_PATTERN = re.compile(
        r'(\d{2}:\d{2}:\d{2}).*USDT-(\w+).*(?:PumpDetection|Pumpdetect\d*_USDT|\[Pumpdetect).*'
        r'DailyVol:\s*([\d.]+)m.*Buys/sec:\s*([\d.]+).*'
        r'Vol/sec:\s*([\d.]+)\s*k.*PriceDelta:\s*([\d.]+)%',
        re.IGNORECASE
    )

    DROP_PATTERN = re.compile(
        r'(\d{2}:\d{2}:\d{2}).*USDT-(\w+).*(?:DropsDetection|Dropdetect\d*_USDT|\[Dropdetect).*'
        r'DailyVol:\s*([\d.]+)m.*(?:x)?PriceDelta:\s*([\d.]+)',
        re.IGNORECASE
    )
    
    TOPMARKET_PATTERN = re.compile(
        r'(\d{2}:\d{2}:\d{2}).*Signal USDT-(\w+)\s+Ask:([\d.]+).*'
        r'TopMarket.*Delta:\s*([\d.]+)%\s+(\w+)'
    )
    
    DELTA_PATTERN = re.compile(
        r'(\d{2}:\d{2}:\d{2}).*Signal USDT-(\w+)\s+Ask:([\d.]+).*'
        r'Delta:\s*USDT-\w+.*DailyVol:\s*([\d.]+)m.*'
        r'Delta:\s*([\d.]+)%.*VolRaise:\s*([\d.]+)%.*Buyers:\s*(\d+)'
    )
    
    PRICE_PATTERN = re.compile(r'Ask:([\d.]+)')
    
    def parse(self, line: str) -> Optional[MoonBotSignal]:
        """Parse a single MoonBot log line"""
        line = line.strip()
        if not line:
            return None
        
        # Skip non-signal lines
        if 'Signal' not in line and 'Detection' not in line:
            return None
        
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        # Try PumpDetection
        match = self.PUMP_PATTERN.search(line)
        if match:
            time_str, symbol, daily_vol, buys_sec, vol_sec, delta = match.groups()
            price = self._extract_price(line)
            
            return MoonBotSignal(
                timestamp=f"{today}T{time_str}Z",
                symbol=f"{symbol}USDT",
                strategy="PumpDetection",
                direction="Long",
                price=price,
                buys_per_sec=float(buys_sec),
                vol_per_sec=float(vol_sec) * 1000,
                delta_pct=float(delta),
                vol_raise_pct=0,
                daily_volume=float(daily_vol) * 1_000_000,
                raw_line=line,
            )
        
        # Try DropsDetection
        match = self.DROP_PATTERN.search(line)
        if match:
            time_str, symbol, daily_vol, delta = match.groups()
            price = self._extract_price(line)
            
            return MoonBotSignal(
                timestamp=f"{today}T{time_str}Z",
                symbol=f"{symbol}USDT",
                strategy="DropsDetection",
                direction="Long",
                price=price,
                buys_per_sec=0,
                vol_per_sec=0,
                delta_pct=float(delta),
                vol_raise_pct=0,
                daily_volume=float(daily_vol) * 1_000_000,
                raw_line=line,
            )
        
        # Try TopMarket
        match = self.TOPMARKET_PATTERN.search(line)
        if match:
            time_str, symbol, price, delta, direction = match.groups()
            
            return MoonBotSignal(
                timestamp=f"{today}T{time_str}Z",
                symbol=f"{symbol}USDT",
                strategy="TopMarket",
                direction=direction,
                price=float(price),
                buys_per_sec=0,
                vol_per_sec=0,
                delta_pct=float(delta),
                vol_raise_pct=0,
                daily_volume=0,
                raw_line=line,
            )
        
        # Try Delta
        match = self.DELTA_PATTERN.search(line)
        if match:
            time_str, symbol, price, daily_vol, delta, vol_raise, buyers = match.groups()
            
            return MoonBotSignal(
                timestamp=f"{today}T{time_str}Z",
                symbol=f"{symbol}USDT",
                strategy="Delta",
                direction="Long",
                price=float(price),
                buys_per_sec=int(buyers) / 60,  # Approximate
                vol_per_sec=0,
                delta_pct=float(delta),
                vol_raise_pct=float(vol_raise),
                daily_volume=float(daily_vol) * 1_000_000,
                raw_line=line,
            )
        
        return None
    
    def _extract_price(self, line: str) -> float:
        """Extract price from line"""
        match = self.PRICE_PATTERN.search(line)
        return float(match.group(1)) if match else 0


# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL SENDER
# ═══════════════════════════════════════════════════════════════════════════════

class SignalSender:
    """Sends signals to AutoTrader"""
    
    def __init__(self, autotrader_url: str = AUTOTRADER_URL):
        self.autotrader_url = autotrader_url
        self.client = httpx.Client(timeout=5)
        self.signals_sent = 0
        self.signals_filtered = 0
    
    def should_send(self, signal: MoonBotSignal) -> bool:
        """Check if signal meets criteria"""
        
        # Filter SHORT
        if signal.direction == "Short":
            logger.debug(f"Filtered SHORT: {signal.symbol}")
            return False
        
        # Filter DropsDetection alone
        if signal.strategy == "DropsDetection" and signal.buys_per_sec < MIN_BUYS_SEC:
            logger.debug(f"Filtered DROP: {signal.symbol}")
            return False
        
        # Filter low buys/sec (except TopMarket/Delta with high delta)
        if signal.buys_per_sec < MIN_BUYS_SEC:
            if signal.delta_pct < MIN_DELTA * 2:  # Require higher delta for non-pump
                logger.debug(f"Filtered weak: {signal.symbol} buys={signal.buys_per_sec}")
                return False
        
        return True
    
    def send(self, signal: MoonBotSignal) -> bool:
        """Send signal to AutoTrader"""
        
        if not self.should_send(signal):
            self.signals_filtered += 1
            return False
        
        payload = {
            "symbol": signal.symbol,
            "strategy": signal.strategy,
            "direction": signal.direction,
            "price": signal.price,
            "buys_per_sec": signal.buys_per_sec,
            "delta_pct": signal.delta_pct,
            "vol_raise_pct": signal.vol_raise_pct,
        }
        
        try:
            resp = self.client.post(
                f"{self.autotrader_url}/signal",
                json=payload,
            )
            
            if resp.status_code == 200:
                self.signals_sent += 1
                logger.info(f"✅ Signal sent: {signal.symbol} | {signal.strategy}")
                return True
            else:
                logger.warning(f"Send failed: {resp.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"Send error: {e}")
            return False
    
    def get_stats(self) -> Dict:
        """Get sender statistics"""
        return {
            "signals_sent": self.signals_sent,
            "signals_filtered": self.signals_filtered,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# FILE WATCHER
# ═══════════════════════════════════════════════════════════════════════════════

class FileWatcher:
    """Watches MoonBot log file for new signals"""
    
    def __init__(self, filepath: str, parser: MoonBotParser, sender: SignalSender):
        self.filepath = Path(filepath)
        self.parser = parser
        self.sender = sender
        self.last_position = 0
        self.running = False
    
    def watch(self, from_start: bool = False):
        """Watch file for new lines"""
        self.running = True
        
        if not self.filepath.exists():
            logger.error(f"File not found: {self.filepath}")
            return
        
        # Start position
        if not from_start:
            self.last_position = self.filepath.stat().st_size
        
        logger.info(f"Watching: {self.filepath}")
        
        while self.running:
            try:
                current_size = self.filepath.stat().st_size
                
                if current_size > self.last_position:
                    with open(self.filepath, 'r', encoding='utf-8', errors='ignore') as f:
                        f.seek(self.last_position)
                        new_lines = f.readlines()
                        self.last_position = f.tell()
                    
                    for line in new_lines:
                        signal = self.parser.parse(line)
                        if signal:
                            self.sender.send(signal)
                
                elif current_size < self.last_position:
                    # File was truncated
                    self.last_position = 0
                
                time.sleep(0.5)
                
            except KeyboardInterrupt:
                self.running = False
            except Exception as e:
                logger.error(f"Watch error: {e}")
                time.sleep(1)
    
    def stop(self):
        self.running = False


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="MoonBot to AutoTrader Integration")
    parser.add_argument("--watch-file", type=str, help="Watch MoonBot log file")
    parser.add_argument("--stdin", action="store_true", help="Read from stdin")
    parser.add_argument("--test", type=str, help="Test single signal line")
    parser.add_argument("--from-start", action="store_true", help="Process file from beginning")
    parser.add_argument("--autotrader-url", type=str, default=AUTOTRADER_URL)
    parser.add_argument("--dry-run", action="store_true", help="Don't send to AutoTrader")
    
    args = parser.parse_args()
    
    moonbot_parser = MoonBotParser()
    sender = SignalSender(args.autotrader_url)
    
    if args.test:
        # Test single line
        signal = moonbot_parser.parse(args.test)
        if signal:
            print(f"Parsed: {json.dumps(asdict(signal), indent=2)}")
            if not args.dry_run:
                sender.send(signal)
        else:
            print("Failed to parse signal")
    
    elif args.watch_file:
        # Watch file
        watcher = FileWatcher(args.watch_file, moonbot_parser, sender)
        
        try:
            watcher.watch(from_start=args.from_start)
        except KeyboardInterrupt:
            pass
        finally:
            stats = sender.get_stats()
            print(f"\nStats: {stats}")
    
    elif args.stdin:
        # Read from stdin
        try:
            for line in sys.stdin:
                signal = moonbot_parser.parse(line)
                if signal:
                    if not args.dry_run:
                        sender.send(signal)
                    else:
                        print(f"Would send: {signal.symbol}")
        except KeyboardInterrupt:
            pass
    
    else:
        parser.print_help()
        print("\nExamples:")
        print("  python moonbot_autotrader.py --watch-file moonbot.log")
        print("  python moonbot_autotrader.py --test \"ENJUSDT PumpDetection buys/sec: 32.91\"")


if __name__ == "__main__":
    main()
