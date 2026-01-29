# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 13:40:00 UTC
# Purpose: Auto-watch MoonBot log and register signals for tracking
# === END SIGNATURE ===
"""
MoonBot Log Watcher - Automatic Signal Registration

Watches MoonBot log file for new signals and automatically
registers them with the AI Gateway for outcome tracking.

Usage:
    # Watch default log location
    python scripts/moonbot_watcher.py

    # Watch specific file
    python scripts/moonbot_watcher.py --file C:\\path\\to\\moonbot.log

    # Dry run (parse only, don't register)
    python scripts/moonbot_watcher.py --dry-run
"""

import re
import sys
import os
import time
import json
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Set

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

try:
    import requests
except ImportError:
    logger.error("requests not installed. Run: pip install requests")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

GATEWAY_URL = "http://127.0.0.1:8100"
TIMEOUT = 10

# Default MoonBot log locations
DEFAULT_LOG_PATHS = [
    Path("C:/Users/kirillDev/Desktop/MoonBot/logs/moonbot.log"),
    Path("C:/MoonBot/logs/moonbot.log"),
    Path("logs/moonbot.log"),
    Path("moonbot.log"),
]

# Signal patterns
SIGNAL_PATTERN = re.compile(
    r"Signal\s+USDT-(\w+)\s+Ask:([0-9.]+)\s+"
    r"dBTC:\s*([0-9.-]+)\s+"
    r"dBTC5m:\s*([0-9.-]+)\s+"
    r"dBTC1m:\s*([0-9.-]+)"
)

# Minimum price to filter out garbage
MIN_PRICE = 0.0000001

# Cooldown per symbol (seconds)
SYMBOL_COOLDOWN = 120


# ═══════════════════════════════════════════════════════════════════════════
# SIGNAL PARSER
# ═══════════════════════════════════════════════════════════════════════════

def parse_signal_line(line: str) -> Optional[Dict[str, Any]]:
    """Parse a single MoonBot signal line."""
    match = SIGNAL_PATTERN.search(line)
    if not match:
        return None

    symbol = match.group(1)
    price = float(match.group(2))

    if price < MIN_PRICE:
        return None

    dBTC = float(match.group(3))
    dBTC5m = float(match.group(4))
    dBTC1m = float(match.group(5))

    # Determine direction
    direction = "Short" if "Short" in line else "Long"

    # Determine strategy
    strategy = "unknown"
    if "Pumpdetect" in line:
        strategy = "pump"
    elif "Delta_" in line:
        strategy = "delta"
    elif "Dropdetect" in line:
        strategy = "drop"
    elif "Top Market" in line:
        strategy = "top_market"
    elif "VOLUMES" in line:
        strategy = "volume"

    return {
        "symbol": f"{symbol}USDT",
        "price": price,
        "direction": direction,
        "source": "moonbot_watcher",
        "strategy": strategy,
        "dBTC": dBTC,
        "dBTC5m": dBTC5m,
        "dBTC1m": dBTC1m,
        "raw_line": line[:200],
    }


# ═══════════════════════════════════════════════════════════════════════════
# GATEWAY CLIENT
# ═══════════════════════════════════════════════════════════════════════════

class GatewayClient:
    """Client for AI Gateway API."""

    def __init__(self, base_url: str = GATEWAY_URL):
        self.base_url = base_url

    def is_healthy(self) -> bool:
        """Check if gateway is responding."""
        try:
            resp = requests.get(f"{self.base_url}/health", timeout=5)
            return resp.status_code == 200 and resp.json().get("status") == "ok"
        except:
            return False

    def subscribe_symbol(self, symbol: str) -> bool:
        """Subscribe to price updates."""
        try:
            resp = requests.post(
                f"{self.base_url}/price-feed/subscribe",
                json=[symbol],
                timeout=TIMEOUT,
            )
            return resp.status_code == 200
        except:
            return False

    def register_signal(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """Register signal for tracking."""
        try:
            resp = requests.post(
                f"{self.base_url}/outcomes/track",
                json=signal,
                timeout=TIMEOUT,
            )
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"error": str(e)}

    def get_active_symbols(self) -> Set[str]:
        """Get currently tracked symbols."""
        try:
            resp = requests.get(f"{self.base_url}/outcomes/stats", timeout=TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                return set(data.get("active_symbols", []))
            return set()
        except:
            return set()


# ═══════════════════════════════════════════════════════════════════════════
# FILE WATCHER
# ═══════════════════════════════════════════════════════════════════════════

class MoonBotWatcher:
    """
    Watches MoonBot log file for new signals.

    Uses tail-like behavior to only process new lines.
    """

    def __init__(
        self,
        log_path: Path,
        gateway: GatewayClient,
        dry_run: bool = False,
    ):
        self.log_path = log_path
        self.gateway = gateway
        self.dry_run = dry_run

        # Track last position in file
        self._last_position = 0
        self._last_size = 0

        # Cooldowns per symbol
        self._symbol_cooldowns: Dict[str, float] = {}

        # Statistics
        self.stats = {
            "lines_processed": 0,
            "signals_found": 0,
            "signals_registered": 0,
            "errors": 0,
        }

        logger.info(f"Watching: {self.log_path}")

    def _check_cooldown(self, symbol: str) -> bool:
        """Check if symbol is within cooldown period."""
        last_time = self._symbol_cooldowns.get(symbol, 0)
        return (time.time() - last_time) >= SYMBOL_COOLDOWN

    def _record_signal(self, symbol: str) -> None:
        """Record signal time for cooldown."""
        self._symbol_cooldowns[symbol] = time.time()

    def process_new_lines(self) -> List[Dict[str, Any]]:
        """
        Read and process new lines from log file.

        Returns list of registered signals.
        """
        if not self.log_path.exists():
            logger.warning(f"Log file not found: {self.log_path}")
            return []

        registered = []

        try:
            current_size = self.log_path.stat().st_size

            # File was truncated/rotated
            if current_size < self._last_size:
                logger.info("Log file rotated, starting from beginning")
                self._last_position = 0

            self._last_size = current_size

            with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                # Seek to last position
                f.seek(self._last_position)

                # Read new lines
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    self.stats["lines_processed"] += 1

                    # Parse signal
                    signal = parse_signal_line(line)
                    if not signal:
                        continue

                    self.stats["signals_found"] += 1
                    symbol = signal["symbol"]

                    # Check cooldown
                    if not self._check_cooldown(symbol):
                        logger.debug(f"Cooldown active for {symbol}, skipping")
                        continue

                    # Register signal
                    if self.dry_run:
                        logger.info(f"[DRY] Would register: {symbol} @ {signal['price']}")
                        registered.append(signal)
                    else:
                        # Subscribe to symbol
                        self.gateway.subscribe_symbol(symbol)

                        # Register for tracking
                        result = self.gateway.register_signal(signal)

                        if "error" in result:
                            logger.error(f"Failed to register {symbol}: {result['error']}")
                            self.stats["errors"] += 1
                        else:
                            signal_id = result.get("signal_id", "unknown")[:16]
                            logger.info(f"Registered: {symbol} @ {signal['price']} -> {signal_id}")
                            self.stats["signals_registered"] += 1
                            registered.append(signal)

                    self._record_signal(symbol)

                # Update position
                self._last_position = f.tell()

        except Exception as e:
            logger.error(f"Error processing log: {e}")
            self.stats["errors"] += 1

        return registered

    def run_once(self) -> List[Dict[str, Any]]:
        """Process new lines once and return."""
        return self.process_new_lines()

    def run_forever(self, interval: float = 2.0) -> None:
        """
        Watch file continuously.

        Args:
            interval: Seconds between checks
        """
        logger.info(f"Starting continuous watch (interval={interval}s)")
        logger.info("Press Ctrl+C to stop")

        try:
            while True:
                self.process_new_lines()
                time.sleep(interval)

        except KeyboardInterrupt:
            logger.info("\nWatcher stopped")

        finally:
            self._print_stats()

    def _print_stats(self) -> None:
        """Print final statistics."""
        print("\n" + "=" * 50)
        print("MOONBOT WATCHER STATS")
        print("=" * 50)
        print(f"Lines processed:    {self.stats['lines_processed']}")
        print(f"Signals found:      {self.stats['signals_found']}")
        print(f"Signals registered: {self.stats['signals_registered']}")
        print(f"Errors:            {self.stats['errors']}")
        print("=" * 50)


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def find_log_file() -> Optional[Path]:
    """Find MoonBot log file from default locations."""
    for path in DEFAULT_LOG_PATHS:
        if path.exists():
            return path
    return None


def main():
    parser = argparse.ArgumentParser(description="MoonBot Log Watcher")
    parser.add_argument("--file", "-f", type=str, help="Log file path")
    parser.add_argument("--url", type=str, default=GATEWAY_URL, help="Gateway URL")
    parser.add_argument("--interval", type=float, default=2.0, help="Check interval (seconds)")
    parser.add_argument("--once", action="store_true", help="Process once and exit")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, don't register")
    parser.add_argument("--from-start", action="store_true", help="Process from beginning of file")

    args = parser.parse_args()

    # Find log file
    if args.file:
        log_path = Path(args.file)
    else:
        log_path = find_log_file()

    if not log_path or not log_path.exists():
        logger.error("MoonBot log file not found")
        logger.info("Searched in:")
        for p in DEFAULT_LOG_PATHS:
            logger.info(f"  - {p}")
        logger.info("\nUse --file to specify path")
        sys.exit(1)

    # Create gateway client
    gateway = GatewayClient(base_url=args.url)

    # Check gateway health
    if not args.dry_run:
        if not gateway.is_healthy():
            logger.error(f"Gateway not responding at {args.url}")
            logger.info("Start gateway: powershell -File start_simple.ps1 -Mode TESTNET")
            sys.exit(1)
        logger.info(f"Gateway healthy at {args.url}")

    # Create watcher
    watcher = MoonBotWatcher(
        log_path=log_path,
        gateway=gateway,
        dry_run=args.dry_run,
    )

    # Start from beginning if requested
    if not args.from_start:
        # Skip to end of file
        if log_path.exists():
            watcher._last_position = log_path.stat().st_size
            watcher._last_size = watcher._last_position
            logger.info(f"Starting from end of file (position {watcher._last_position})")

    # Run
    if args.once:
        watcher.run_once()
        watcher._print_stats()
    else:
        watcher.run_forever(interval=args.interval)


if __name__ == "__main__":
    main()
