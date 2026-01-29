# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 10:30:00 UTC
# Purpose: Parse MoonBot log and append signals to JSONL
# === END SIGNATURE ===
"""
Parse MoonBot signal log and convert to JSONL format.

Usage:
    python scripts/parse_moonbot_log.py < log.txt
    python scripts/parse_moonbot_log.py --file log.txt
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path


def parse_vol(vol_str: str) -> float:
    """Parse volume string like '2.5m' or '543 k'."""
    vol_str = vol_str.strip().lower()
    if 'm' in vol_str:
        return float(vol_str.replace('m', '')) * 1_000_000
    elif 'k' in vol_str:
        return float(vol_str.replace('k', '').strip()) * 1_000
    return float(vol_str)


def parse_moonbot_log(log_data: str, date_str: str = None) -> list:
    """Parse MoonBot log and return list of signal dicts."""
    if date_str is None:
        date_str = datetime.now().strftime('%Y-%m-%d')

    parsed = []

    signal_pattern = re.compile(
        r'(\d{2}:\d{2}:\d{2})\s+Signal\s+USDT-(\w+)\s+Ask:([\d.]+)\s+'
        r'dBTC:\s*([-\d.]+)\s+dBTC5m:\s*([-\d.]+)\s+dBTC1m:\s*([-\d.]+)\s+'
        r'24hBTC:\s*([-\d.]+)\s+72hBTC:\s*([-\d.]+)\s+'
        r'dMarkets:\s*([-\d.]+)\s+dMarkets24:\s*([-\d.]+)'
    )

    # Strategy patterns
    pump_pattern = re.compile(
        r'PumpDetection.*?DailyVol:\s*([\d.]+[mk]?).*?PPL/sec:\s*(\d+).*?'
        r'Buys/sec:\s*([\d.]+).*?Vol/sec:\s*([\d.]+\s*k?).*?PriceDelta:\s*([\d.]+)%'
    )
    drop_pattern = re.compile(
        r'DropsDetection.*?DailyVol:\s*([\d.]+[mk]?).*?PriceIsLow:\s*(\w+).*?xPriceDelta:\s*([\d.]+)'
    )
    top_pattern = re.compile(r'TopMarket.*?Delta:\s*([\d.]+)%\s+(\w+)')
    delta_pattern = re.compile(
        r'Delta:.*?DailyVol:\s*([\d.]+[mk]?).*?HourlyVol:\s*([\d.]+\s*k?).*?'
        r'Delta:\s*([\d.]+)%.*?LastDelta:\s*([\d.]+)%.*?VolRaise:\s*([\d.]+)%.*?Buyers:\s*(\d+)'
    )
    vol_pattern = re.compile(r'VolDetection.*?BidToAsk:\s*([\d.]+).*?Bids:\s*([\d.]+\s*k?)')

    for line in log_data.strip().split('\n'):
        match = signal_pattern.search(line)
        if not match:
            continue

        time_str, symbol, price, d_btc, d_btc5m, d_btc1m, h24_btc, h72_btc, d_markets, d_markets24 = match.groups()

        signal = {
            'timestamp': f'{date_str}T{time_str}+02:00',
            'symbol': f'{symbol}USDT',
            'price': float(price),
            'dBTC': float(d_btc),
            'dBTC5m': float(d_btc5m),
            'dBTC1m': float(d_btc1m),
            '24hBTC': float(h24_btc),
            '72hBTC': float(h72_btc),
            'dMarkets': float(d_markets),
            'dMarkets24': float(d_markets24),
            'source': 'moonbot',
        }

        # Detect signal type and extract extra data
        if 'PumpDetection' in line:
            signal['signal_type'] = 'pump'
            signal['direction'] = 'Long'
            pm = pump_pattern.search(line)
            if pm:
                signal['daily_vol'] = pm.group(1)
                signal['ppl_sec'] = int(pm.group(2))
                signal['buys_sec'] = float(pm.group(3))
                signal['delta_pct'] = float(pm.group(5))

        elif 'DropsDetection' in line:
            signal['signal_type'] = 'drop'
            signal['direction'] = 'Long'  # Drop = buy opportunity
            dm = drop_pattern.search(line)
            if dm:
                signal['daily_vol'] = dm.group(1)
                signal['price_is_low'] = dm.group(2).lower() == 'true'
                signal['delta_pct'] = float(dm.group(3))

        elif 'TopMarket' in line:
            signal['signal_type'] = 'topmarket'
            tm = top_pattern.search(line)
            if tm:
                signal['delta_pct'] = float(tm.group(1))
                signal['direction'] = tm.group(2)

        elif 'Delta:' in line and 'DailyVol:' in line:
            signal['signal_type'] = 'delta'
            signal['direction'] = 'Long'
            dm = delta_pattern.search(line)
            if dm:
                signal['daily_vol'] = dm.group(1)
                signal['hourly_vol'] = dm.group(2)
                signal['delta_pct'] = float(dm.group(3))
                signal['vol_raise'] = float(dm.group(5))
                signal['buyers'] = int(dm.group(6))

        elif 'VolDetection' in line:
            signal['signal_type'] = 'volume'
            signal['direction'] = 'Long'
            vm = vol_pattern.search(line)
            if vm:
                signal['bid_to_ask'] = float(vm.group(1))

        parsed.append(signal)

    return parsed


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Parse MoonBot log')
    parser.add_argument('--file', '-f', help='Log file to parse')
    parser.add_argument('--date', '-d', help='Date for signals (YYYY-MM-DD)')
    parser.add_argument('--output', '-o', help='Output JSONL file')
    parser.add_argument('--append', '-a', action='store_true', help='Append to output')

    args = parser.parse_args()

    # Read input
    if args.file:
        with open(args.file, 'r', encoding='utf-8') as f:
            log_data = f.read()
    else:
        log_data = sys.stdin.read()

    # Parse
    date_str = args.date or datetime.now().strftime('%Y-%m-%d')
    signals = parse_moonbot_log(log_data, date_str)

    print(f"Parsed {len(signals)} signals", file=sys.stderr)
    print(f"Symbols: {set(s['symbol'] for s in signals)}", file=sys.stderr)
    print(f"Types: {set(s.get('signal_type', 'unknown') for s in signals)}", file=sys.stderr)

    # Output
    if args.output:
        mode = 'a' if args.append else 'w'
        with open(args.output, mode, encoding='utf-8') as f:
            for s in signals:
                f.write(json.dumps(s, ensure_ascii=False) + '\n')
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        for s in signals:
            print(json.dumps(s, ensure_ascii=False))


if __name__ == '__main__':
    main()
