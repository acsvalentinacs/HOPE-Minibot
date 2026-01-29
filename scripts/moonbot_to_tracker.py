# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 13:30:00 UTC
# Purpose: Parse MoonBot log signals and register them for outcome tracking
# === END SIGNATURE ===
"""
MoonBot Signal Parser -> Outcome Tracker Integration

Parses MoonBot log output and registers signals with AI Gateway
for real-time MFE/MAE tracking.

Usage:
    # From clipboard/paste:
    python scripts/moonbot_to_tracker.py --paste

    # From log file:
    python scripts/moonbot_to_tracker.py --file moonbot.log

    # Single line:
    python scripts/moonbot_to_tracker.py --signal "Signal USDT-ARPA Ask:0.015350 ..."
"""

import re
import sys
import json
import argparse
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

GATEWAY_URL = "http://127.0.0.1:8100"
TIMEOUT = 10

# Signal patterns from MoonBot log
SIGNAL_PATTERN = re.compile(
    r"Signal\s+USDT-(\w+)\s+Ask:([0-9.]+)\s+"
    r"dBTC:\s*([0-9.-]+)\s+"
    r"dBTC5m:\s*([0-9.-]+)\s+"
    r"dBTC1m:\s*([0-9.-]+)\s+"
    r"24hBTC:\s*([0-9.-]+)"
)

PUMP_PATTERN = re.compile(
    r"PumpDetection:.*?USDT-(\w+).*?"
    r"DailyVol:\s*([0-9.]+[kmKM]?).*?"
    r"Buys/sec:\s*([0-9.]+).*?"
    r"Vol/sec:\s*([0-9.]+)\s*k.*?"
    r"PriceDelta:\s*([0-9.]+)%"
)

DELTA_PATTERN = re.compile(
    r"Delta:\s+USDT-(\w+).*?"
    r"Delta:\s*([0-9.]+)%.*?"
    r"VolRaise:\s*([0-9.]+)%.*?"
    r"Buyers:\s*(\d+)"
)


# ═══════════════════════════════════════════════════════════════════════════
# SIGNAL PARSER
# ═══════════════════════════════════════════════════════════════════════════

def parse_signal_line(line: str) -> Optional[Dict[str, Any]]:
    """Parse a single MoonBot signal line."""

    signal = None

    # Try Signal pattern
    match = SIGNAL_PATTERN.search(line)
    if match:
        symbol = match.group(1)
        price = float(match.group(2))
        dBTC = float(match.group(3))
        dBTC5m = float(match.group(4))
        dBTC1m = float(match.group(5))

        signal = {
            "symbol": f"{symbol}USDT",
            "price": price,
            "direction": "Long",  # Default, will adjust based on context
            "source": "moonbot",
            "dBTC": dBTC,
            "dBTC5m": dBTC5m,
            "dBTC1m": dBTC1m,
        }

        # Determine direction from context
        if "Short" in line:
            signal["direction"] = "Short"

        # Add strategy info if present
        if "Pumpdetect" in line:
            signal["strategy"] = "pump"
        elif "Delta_" in line:
            signal["strategy"] = "delta"
        elif "Dropdetect" in line:
            signal["strategy"] = "drop"
        elif "Top Market" in line:
            signal["strategy"] = "top_market"
        elif "VOLUMES" in line:
            signal["strategy"] = "volume"

    # Try Pump pattern for additional context
    pump_match = PUMP_PATTERN.search(line)
    if pump_match and signal is None:
        symbol = pump_match.group(1)
        buys_sec = float(pump_match.group(3))
        price_delta = float(pump_match.group(5))

        signal = {
            "symbol": f"{symbol}USDT",
            "price": 0,  # Will need to fetch
            "direction": "Long",
            "source": "moonbot_pump",
            "buys_per_sec": buys_sec,
            "price_delta_pct": price_delta,
            "strategy": "pump",
        }

    return signal


def parse_log_content(content: str) -> List[Dict[str, Any]]:
    """Parse multiple lines of MoonBot log."""
    signals = []
    seen_symbols = set()  # Dedupe by symbol within same parse

    for line in content.strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        signal = parse_signal_line(line)
        if signal:
            symbol = signal["symbol"]
            # Only add if we have a price and haven't seen this symbol yet
            if signal.get("price", 0) > 0 and symbol not in seen_symbols:
                signals.append(signal)
                seen_symbols.add(symbol)

    return signals


# ═══════════════════════════════════════════════════════════════════════════
# GATEWAY INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

def register_signal(signal: Dict[str, Any]) -> Dict[str, Any]:
    """Register signal with AI Gateway for outcome tracking."""
    url = f"{GATEWAY_URL}/outcomes/track"

    try:
        resp = requests.post(url, json=signal, timeout=TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
        else:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
    except Exception as e:
        return {"error": str(e)}


def subscribe_symbol(symbol: str) -> bool:
    """Subscribe to price updates for symbol."""
    url = f"{GATEWAY_URL}/price-feed/subscribe"

    try:
        resp = requests.post(url, json=[symbol], timeout=TIMEOUT)
        return resp.status_code == 200
    except:
        return False


def check_gateway_health() -> bool:
    """Check if gateway is responding."""
    try:
        resp = requests.get(f"{GATEWAY_URL}/health", timeout=5)
        return resp.status_code == 200 and resp.json().get("status") == "ok"
    except:
        return False


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="MoonBot Signal -> Outcome Tracker")
    parser.add_argument("--file", "-f", type=str, help="Log file to parse")
    parser.add_argument("--signal", "-s", type=str, help="Single signal line")
    parser.add_argument("--paste", "-p", action="store_true", help="Read from stdin (paste)")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, don't register")
    parser.add_argument("--url", type=str, default=GATEWAY_URL, help="Gateway URL")

    args = parser.parse_args()

    gateway_url = args.url

    # Check gateway health
    if not args.dry_run:
        if not check_gateway_health():
            print("[ERROR] AI Gateway not responding at", gateway_url)
            print("Start the gateway first: powershell -File start_simple.ps1 -Mode TESTNET")
            sys.exit(1)
        print(f"[OK] Gateway healthy at {gateway_url}")

    # Get content to parse
    content = ""

    if args.file:
        with open(args.file, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        print(f"[OK] Read {len(content)} bytes from {args.file}")

    elif args.signal:
        content = args.signal

    elif args.paste:
        print("Paste MoonBot log lines (Ctrl+D or Ctrl+Z to finish):")
        try:
            content = sys.stdin.read()
        except KeyboardInterrupt:
            print("\nCancelled.")
            sys.exit(0)

    else:
        parser.print_help()
        sys.exit(1)

    # Parse signals
    signals = parse_log_content(content)

    if not signals:
        print("[WARN] No valid signals found in input")
        sys.exit(0)

    print(f"\n[PARSED] {len(signals)} signals found:")
    print("-" * 60)

    for sig in signals:
        symbol = sig["symbol"]
        price = sig.get("price", 0)
        direction = sig.get("direction", "Long")
        strategy = sig.get("strategy", "unknown")

        print(f"  {symbol:12} @ {price:<12.6f} | {direction:5} | {strategy}")

    print("-" * 60)

    if args.dry_run:
        print("[DRY-RUN] Would register above signals")
        sys.exit(0)

    # Register signals
    print("\n[REGISTERING] Signals with Outcome Tracker...")

    registered = 0
    errors = 0

    for sig in signals:
        symbol = sig["symbol"]

        # Subscribe to symbol first
        subscribe_symbol(symbol)

        # Register signal
        result = register_signal(sig)

        if "error" in result:
            print(f"  [FAIL] {symbol}: {result.get('error')}")
            errors += 1
        else:
            signal_id = result.get("signal_id", "unknown")[:16]
            print(f"  [OK] {symbol} -> {signal_id}")
            registered += 1

    print("-" * 60)
    print(f"[DONE] Registered: {registered}, Errors: {errors}")

    if registered > 0:
        print(f"\nMonitor outcomes: python hope_monitor.py --loop")


if __name__ == "__main__":
    main()
