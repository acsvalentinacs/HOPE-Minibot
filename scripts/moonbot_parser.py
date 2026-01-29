# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 07:35:00 UTC
# Purpose: Parse MoonBot logs and extract signals for AI training
# === END SIGNATURE ===
"""
MoonBot Log Parser — Extract signals for AI training.

Usage:
    # Parse from clipboard
    python scripts/moonbot_parser.py --clipboard

    # Parse from file
    python scripts/moonbot_parser.py --file moonbot.log

    # Parse from stdin
    cat moonbot.log | python scripts/moonbot_parser.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Output directory
OUTPUT_DIR = Path("data/moonbot_signals")


def parse_signal_line(line: str) -> Optional[Dict[str, Any]]:
    """
    Parse a single MoonBot signal line.

    Supported formats:
    - Signal USDT-XXX Ask:0.123 ...
    - PumpDetection: <strategy> USDT-XXX ...
    - DropsDetection: <strategy> USDT-XXX ...
    """
    signal = {}

    # Extract timestamp
    time_match = re.match(r"(\d{2}:\d{2}:\d{2})", line)
    if time_match:
        time_str = time_match.group(1)
        today = datetime.now().strftime("%Y-%m-%d")
        signal["timestamp"] = f"{today}T{time_str}"

    # Extract symbol
    symbol_match = re.search(r"USDT-([A-Z0-9]+)", line)
    if symbol_match:
        signal["symbol"] = symbol_match.group(1)
    else:
        return None  # No symbol = not a signal

    # Extract price
    price_match = re.search(r"Ask:(\d+\.\d+)", line)
    if price_match:
        signal["price"] = float(price_match.group(1))

    # Extract delta metrics
    for metric, pattern in [
        ("dBTC", r"dBTC:\s*(-?\d+\.\d+)"),
        ("dBTC5m", r"dBTC5m:\s*(-?\d+\.\d+)"),
        ("dBTC1m", r"dBTC1m:\s*(-?\d+\.\d+)"),
        ("24hBTC", r"24hBTC:\s*(-?\d+\.\d+)"),
        ("72hBTC", r"72hBTC:\s*(-?\d+\.\d+)"),
        ("dMarkets", r"dMarkets:\s*(-?\d+\.\d+)"),
        ("dMarkets24", r"dMarkets24:\s*(-?\d+\.\d+)"),
    ]:
        match = re.search(pattern, line)
        if match:
            signal[metric] = float(match.group(1))

    # Extract strategy type
    if "PumpDetection" in line:
        signal["signal_type"] = "pump"
        signal["strategy"] = _extract_strategy(line, "PumpDetection")

        # Pump-specific metrics
        for metric, pattern in [
            ("buys_per_sec", r"Buys/sec:\s*(\d+\.\d+)"),
            ("vol_per_sec_k", r"Vol/sec:\s*(\d+\.\d+)\s*k"),
            ("ppl_per_sec", r"PPL/sec:\s*(\d+)"),
            ("delta_pct", r"PriceDelta:\s*(\d+\.\d+)%"),
        ]:
            match = re.search(pattern, line)
            if match:
                signal[metric] = float(match.group(1))

    elif "DropsDetection" in line:
        signal["signal_type"] = "drop"
        signal["strategy"] = _extract_strategy(line, "DropsDetection")

        # Drop-specific metrics
        delta_match = re.search(r"xPriceDelta:\s*(\d+\.\d+)", line)
        if delta_match:
            signal["delta_pct"] = float(delta_match.group(1))

        low_match = re.search(r"PriceIsLow:\s*(true|false)", line)
        if low_match:
            signal["price_is_low"] = low_match.group(1) == "true"

    elif "TopMarket" in line:
        signal["signal_type"] = "topmarket"
        signal["strategy"] = "TopMarketDetect"

        delta_match = re.search(r"Delta:\s*(\d+\.\d+)%", line)
        if delta_match:
            signal["delta_pct"] = float(delta_match.group(1))

        if "Long" in line:
            signal["direction"] = "Long"
        elif "Short" in line:
            signal["direction"] = "Short"

    elif "Delta:" in line and "Delta_" in line:
        signal["signal_type"] = "delta"
        signal["strategy"] = _extract_strategy(line, "Delta")

        for metric, pattern in [
            ("delta_pct", r"Delta:\s*(\d+\.\d+)%"),
            ("last_delta", r"LastDelta:\s*(\d+\.\d+)%?"),
            ("vol_raise_pct", r"VolRaise:\s*(\d+\.\d+)%"),
            ("buyers", r"Buyers:\s*(\d+)"),
            ("hourly_vol_k", r"HourlyVol:\s*(\d+)\s*k"),
        ]:
            match = re.search(pattern, line)
            if match:
                signal[metric] = float(match.group(1))

    # Extract daily volume
    vol_match = re.search(r"DailyVol:\s*(\d+\.?\d*)\s*([mk])?", line, re.IGNORECASE)
    if vol_match:
        vol = float(vol_match.group(1))
        unit = vol_match.group(2)
        if unit and unit.lower() == "k":
            vol /= 1000  # Convert to millions
        signal["daily_vol_m"] = vol

    return signal if "signal_type" in signal else None


def _extract_strategy(line: str, prefix: str) -> str:
    """Extract strategy name from line."""
    match = re.search(rf"<([^>]+)>.*{prefix}", line)
    if match:
        return match.group(1)
    match = re.search(rf"{prefix}.*<([^>]+)>", line)
    if match:
        return match.group(1)
    return prefix


def parse_log(text: str) -> List[Dict[str, Any]]:
    """Parse multiple lines of MoonBot log."""
    signals = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or "Signal" not in line and "Detection" not in line:
            continue

        signal = parse_signal_line(line)
        if signal:
            signals.append(signal)

    return signals


def save_signals(signals: List[Dict[str, Any]], output_path: Optional[Path] = None) -> Path:
    """Save signals to JSONL file."""
    if output_path is None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d")
        output_path = OUTPUT_DIR / f"signals_{date_str}.jsonl"

    # Append to existing file
    with open(output_path, "a", encoding="utf-8") as f:
        for signal in signals:
            f.write(json.dumps(signal, ensure_ascii=False) + "\n")

    return output_path


def main():
    parser = argparse.ArgumentParser(description="Parse MoonBot logs")
    parser.add_argument("--file", "-f", help="Input log file")
    parser.add_argument("--clipboard", "-c", action="store_true", help="Read from clipboard")
    parser.add_argument("--output", "-o", help="Output JSONL file")

    args = parser.parse_args()

    # Get input text
    if args.clipboard:
        try:
            import pyperclip
            text = pyperclip.paste()
        except ImportError:
            print("ERROR: pip install pyperclip for clipboard support")
            sys.exit(1)
    elif args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()

    # Parse
    signals = parse_log(text)

    if not signals:
        print("No signals found in input")
        sys.exit(0)

    # Save
    output_path = Path(args.output) if args.output else None
    saved_path = save_signals(signals, output_path)

    # Report
    print(f"Parsed {len(signals)} signals:")
    for s in signals:
        symbol = s.get("symbol", "?")
        sig_type = s.get("signal_type", "?")
        delta = s.get("delta_pct", 0)
        print(f"  {symbol}: {sig_type} delta={delta}%")

    print(f"\nSaved to: {saved_path}")


if __name__ == "__main__":
    main()
