# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-01-30T02:50:00Z
# Purpose: Parse MoonBot logs and convert to HOPE signal format
# === END SIGNATURE ===
"""
MoonBot Log Parser - Конвертирует логи MoonBot в формат сигналов HOPE

Парсит строки типа:
23:21:19   PumpDetection: <Pumpdetect1_USDT> USDT-SPELL  DailyVol: 1.1m PPL/sec: 7  Buys/sec: 35.86 Vol/sec: 2.13 k PriceDelta: 2.0%
23:21:19   Signal USDT-SPELL Ask:0.00026110  dBTC: 0.19 ...

Usage:
    python scripts/moonbot_log_parser.py --input moonbot.log --output data/moonbot_signals/signals_today.jsonl
    python scripts/moonbot_log_parser.py --watch C:/MoonBot/logs/ --output data/moonbot_signals/
"""

import re
import json
import hashlib
import argparse
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict

# Regex patterns for MoonBot log parsing
PATTERNS = {
    # PumpDetection: <Pumpdetect1_USDT> USDT-SPELL  DailyVol: 1.1m PPL/sec: 7  Buys/sec: 35.86 Vol/sec: 2.13 k PriceDelta: 2.0%
    'pump': re.compile(
        r'PumpDetection:\s*<[^>]+>\s*USDT-(\w+)\s+DailyVol:\s*([\d.]+)([mk]?)\s+'
        r'PPL/sec:\s*(\d+)\s+Buys/sec:\s*([\d.]+)\s+Vol/sec:\s*([\d.]+)\s*([kmKM]?)\s+'
        r'PriceDelta:\s*([\d.]+)%',
        re.IGNORECASE
    ),

    # DropsDetection: <Dropdetect1_USDT> USDT-SPELL  DailyVol: 1.1m PriceIsLow: false xPriceDelta: 3.6
    'drops': re.compile(
        r'DropsDetection:\s*<[^>]+>\s*USDT-(\w+)\s+DailyVol:\s*([\d.]+)([mk]?)\s+'
        r'PriceIsLow:\s*(\w+)\s+xPriceDelta:\s*([\d.]+)',
        re.IGNORECASE
    ),

    # Delta: USDT-SPELL  DailyVol: 2.0m  HourlyVol: 1753 k  Delta: 2.9%  LastDelta: 0.4%  Vol: 93.3 k BTC  VolRaise: 145.5%  Buyers: 100   Vol/Sec: 0.17 k USDT
    'delta': re.compile(
        r'Delta:\s*USDT-(\w+)\s+DailyVol:\s*([\d.]+)([mk]?)\s+'
        r'HourlyVol:\s*([\d.]+)\s*([kmKM]?)\s+Delta:\s*([\d.]+)%\s+'
        r'LastDelta:\s*([\d.]+)%\s+Vol:\s*([\d.]+)\s*([kmKM]?)\s+BTC\s+'
        r'VolRaise:\s*([\d.]+)%\s+Buyers:\s*(\d+)\s+Vol/Sec:\s*([\d.]+)\s*([kmKM]?)',
        re.IGNORECASE
    ),

    # TopMarket ... Delta: 7.73%  Long
    'topmarket': re.compile(
        r'TopMarket\s*<[^>]+>\s*Delta:\s*([\d.]+)%\s+(Long|Short)',
        re.IGNORECASE
    ),

    # Signal line with price: Signal USDT-SPELL Ask:0.00026110  dBTC: 0.19 dBTC5m: 0.15 ...
    'signal': re.compile(
        r'Signal\s+USDT-(\w+)\s+Ask:([\d.]+)\s+'
        r'dBTC:\s*([-\d.]+)\s+dBTC5m:\s*([-\d.]+)\s+dBTC1m:\s*([-\d.]+)\s+'
        r'24hBTC:\s*([-\d.]+)\s+72hBTC:\s*([-\d.]+)\s+'
        r'dMarkets:\s*([-\d.]+)',
        re.IGNORECASE
    ),

    # Time at start of line: 23:21:19
    'time': re.compile(r'^(\d{2}:\d{2}:\d{2})'),

    # VolDetection
    'volumes': re.compile(
        r'VolDetection\s*<[^>]+>\s*USDT-(\w+)',
        re.IGNORECASE
    ),
}


def parse_volume(value: str, suffix: str) -> float:
    """Parse volume with k/m suffix"""
    val = float(value)
    suffix = suffix.lower()
    if suffix == 'k':
        return val * 1000
    elif suffix == 'm':
        return val * 1000000
    return val


@dataclass
class MoonBotSignal:
    """Parsed MoonBot signal"""
    timestamp: str
    symbol: str
    strategy: str
    direction: str = "Long"
    price: float = 0.0
    delta_pct: float = 0.0
    buys_per_sec: float = 0.0
    vol_per_sec: float = 0.0
    vol_raise_pct: float = 0.0
    daily_volume_m: float = 0.0
    dBTC: float = 0.0
    dBTC5m: float = 0.0
    dBTC1m: float = 0.0
    dMarkets: float = 0.0
    buyers_count: int = 0
    price_is_low: bool = False

    def to_hope_signal(self) -> Dict:
        """Convert to HOPE signal format"""
        # Generate signal_id
        raw = f"{self.symbol}:{self.timestamp}:{self.strategy}"
        sig_hash = hashlib.sha256(raw.encode()).hexdigest()[:8]

        signal = {
            "timestamp": self.timestamp,
            "symbol": f"{self.symbol}USDT",
            "price": self.price,
            "delta_pct": self.delta_pct,
            "buys_per_sec": self.buys_per_sec,
            "vol_per_sec": self.vol_per_sec,
            "vol_raise_pct": self.vol_raise_pct,
            "daily_volume_m": self.daily_volume_m,
            "dBTC": self.dBTC,
            "dBTC5m": self.dBTC5m,
            "dBTC1m": self.dBTC1m,
            "dMarkets": self.dMarkets,
            "strategy": self.strategy,
            "direction": self.direction,
            "source": "moonbot",
            "signal_id": f"mb:{self.symbol}USDT:{sig_hash}",
        }

        # Add sha256 checksum
        canonical = json.dumps(signal, sort_keys=True, separators=(',', ':'))
        signal["sha256"] = hashlib.sha256(canonical.encode()).hexdigest()[:16]

        return signal


class MoonBotLogParser:
    """Parser for MoonBot log files"""

    def __init__(self, date_str: str = None):
        """
        Args:
            date_str: Date string like "2026-01-30" for timestamp construction
        """
        if date_str:
            self.base_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            self.base_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        self.signals: List[MoonBotSignal] = []
        self.current_time = None
        self.pending_signal: Optional[MoonBotSignal] = None

    def parse_time(self, line: str) -> Optional[str]:
        """Extract time from line start"""
        match = PATTERNS['time'].match(line)
        if match:
            time_str = match.group(1)
            h, m, s = map(int, time_str.split(':'))
            dt = self.base_date.replace(hour=h, minute=m, second=s)
            # Handle day rollover (times after midnight)
            if self.current_time and h < 12 and self.current_time.hour > 20:
                dt = dt + timedelta(days=1)
            self.current_time = dt
            return dt.isoformat()
        return None

    def parse_line(self, line: str) -> Optional[MoonBotSignal]:
        """Parse a single log line"""
        line = line.strip()
        if not line:
            return None

        # Get timestamp
        ts = self.parse_time(line) or (self.current_time.isoformat() if self.current_time else datetime.now(timezone.utc).isoformat())

        # Try PumpDetection
        match = PATTERNS['pump'].search(line)
        if match:
            symbol = match.group(1)
            daily_vol = parse_volume(match.group(2), match.group(3))
            ppl_sec = int(match.group(4))
            buys_sec = float(match.group(5))
            vol_sec = parse_volume(match.group(6), match.group(7))
            delta_pct = float(match.group(8))

            sig = MoonBotSignal(
                timestamp=ts,
                symbol=symbol,
                strategy="PumpDetection",
                delta_pct=delta_pct,
                buys_per_sec=buys_sec,
                vol_per_sec=vol_sec,
                daily_volume_m=daily_vol / 1_000_000,
                buyers_count=ppl_sec,
            )
            self.pending_signal = sig
            return None  # Wait for Signal line with price

        # Try DropsDetection
        match = PATTERNS['drops'].search(line)
        if match:
            symbol = match.group(1)
            daily_vol = parse_volume(match.group(2), match.group(3))
            price_low = match.group(4).lower() == 'true'
            delta_pct = float(match.group(5))

            sig = MoonBotSignal(
                timestamp=ts,
                symbol=symbol,
                strategy="DropsDetection",
                delta_pct=delta_pct,
                daily_volume_m=daily_vol / 1_000_000,
                price_is_low=price_low,
            )
            self.pending_signal = sig
            return None

        # Try Delta
        match = PATTERNS['delta'].search(line)
        if match:
            symbol = match.group(1)
            daily_vol = parse_volume(match.group(2), match.group(3))
            delta_pct = float(match.group(6))
            vol_raise = float(match.group(10))
            buyers = int(match.group(11))
            vol_sec = parse_volume(match.group(12), match.group(13))

            sig = MoonBotSignal(
                timestamp=ts,
                symbol=symbol,
                strategy="Delta",
                delta_pct=delta_pct,
                vol_per_sec=vol_sec,
                vol_raise_pct=vol_raise,
                daily_volume_m=daily_vol / 1_000_000,
                buyers_count=buyers,
            )
            self.pending_signal = sig
            return None

        # Try TopMarket
        match = PATTERNS['topmarket'].search(line)
        if match:
            delta_pct = float(match.group(1))
            direction = match.group(2)

            # TopMarket usually follows another strategy, update pending if exists
            if self.pending_signal:
                self.pending_signal.strategy = "TopMarket"
                self.pending_signal.direction = direction
            return None

        # Try Signal line (contains price and market data)
        match = PATTERNS['signal'].search(line)
        if match:
            symbol = match.group(1)
            price = float(match.group(2))
            dBTC = float(match.group(3))
            dBTC5m = float(match.group(4))
            dBTC1m = float(match.group(5))
            dMarkets = float(match.group(8))

            if self.pending_signal and self.pending_signal.symbol == symbol:
                # Update pending signal with price data
                self.pending_signal.price = price
                self.pending_signal.dBTC = dBTC
                self.pending_signal.dBTC5m = dBTC5m
                self.pending_signal.dBTC1m = dBTC1m
                self.pending_signal.dMarkets = dMarkets

                result = self.pending_signal
                self.pending_signal = None
                return result
            else:
                # Standalone signal
                sig = MoonBotSignal(
                    timestamp=ts,
                    symbol=symbol,
                    strategy="Unknown",
                    price=price,
                    dBTC=dBTC,
                    dBTC5m=dBTC5m,
                    dBTC1m=dBTC1m,
                    dMarkets=dMarkets,
                )
                return sig

        return None

    def parse_file(self, path: Path) -> List[Dict]:
        """Parse entire log file"""
        signals = []

        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                sig = self.parse_line(line)
                if sig:
                    signals.append(sig.to_hope_signal())

        # Don't forget pending signal at end
        if self.pending_signal:
            signals.append(self.pending_signal.to_hope_signal())

        return signals

    def parse_text(self, text: str) -> List[Dict]:
        """Parse log text directly"""
        signals = []

        for line in text.split('\n'):
            sig = self.parse_line(line)
            if sig:
                signals.append(sig.to_hope_signal())

        if self.pending_signal:
            signals.append(self.pending_signal.to_hope_signal())

        return signals


def write_signals(signals: List[Dict], output_path: Path, append: bool = True):
    """Write signals to JSONL file"""
    mode = 'a' if append else 'w'
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, mode, encoding='utf-8') as f:
        for sig in signals:
            f.write(json.dumps(sig, ensure_ascii=False) + '\n')

    print(f"Written {len(signals)} signals to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="MoonBot Log Parser")
    parser.add_argument("--input", "-i", type=str, help="Input log file")
    parser.add_argument("--text", "-t", type=str, help="Parse text directly")
    parser.add_argument("--output", "-o", type=str, default="data/moonbot_signals/signals_today.jsonl")
    parser.add_argument("--date", "-d", type=str, help="Date for timestamps (YYYY-MM-DD)")
    parser.add_argument("--append", action="store_true", default=True)
    parser.add_argument("--overwrite", action="store_true")

    args = parser.parse_args()

    date_str = args.date or datetime.now().strftime("%Y-%m-%d")
    parser_instance = MoonBotLogParser(date_str)

    if args.text:
        signals = parser_instance.parse_text(args.text)
    elif args.input:
        signals = parser_instance.parse_file(Path(args.input))
    else:
        print("Provide --input or --text")
        sys.exit(1)

    print(f"Parsed {len(signals)} signals")

    # Preview
    for sig in signals[:5]:
        print(f"  {sig['symbol']} {sig['strategy']} delta={sig['delta_pct']}% buys={sig.get('buys_per_sec', 0)}")

    if len(signals) > 5:
        print(f"  ... and {len(signals) - 5} more")

    if signals:
        output = Path(args.output)
        write_signals(signals, output, append=not args.overwrite)


if __name__ == "__main__":
    main()
