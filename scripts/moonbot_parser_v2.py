# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 08:15:00 UTC
# Purpose: MoonBot Signal Parser - Full version with all patterns
# === END SIGNATURE ===
"""
MoonBot Signal Parser — Парсер логов MoonBot в реальном времени.

ЗАПУСК:
    python scripts/moonbot_parser_v2.py --input moonbot.log --watch
    python scripts/moonbot_parser_v2.py --clipboard  # Из буфера обмена

ЧТО ДЕЛАЕТ:
    1. Парсит логи MoonBot (Signal, PumpDetection, DropsDetection, Delta)
    2. Извлекает ВСЕ метрики
    3. Сохраняет в JSONL для обучения
    4. Режим --watch для real-time мониторинга
"""
import re
import json
import argparse
import time
import sys
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import Optional, List
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("moonbot_parser")

# Output directory
OUTPUT_DIR = Path("state/ai/signals")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class MoonBotSignal:
    """Структура сигнала MoonBot."""
    timestamp: str
    symbol: str
    price: float

    # Delta metrics
    delta_pct: float = 0.0
    dbtc: float = 0.0
    dbtc_5m: float = 0.0
    dbtc_1m: float = 0.0
    dbtc_24h: float = 0.0
    dbtc_72h: float = 0.0
    dmarkets: float = 0.0
    dmarkets_24h: float = 0.0

    # Volume metrics
    daily_volume: Optional[float] = None
    hourly_volume: Optional[float] = None
    vol_raise_pct: Optional[float] = None
    vol_per_sec: Optional[float] = None
    buys_per_sec: Optional[float] = None
    buyers_count: Optional[int] = None
    ppl_per_sec: Optional[int] = None

    # Strategy info
    strategy: str = ""
    signal_type: str = ""  # TopMarket, PumpDetect, DropDetect, Delta
    direction: str = "Long"

    # Flags
    auto_start: bool = False
    auto_buy: bool = False
    price_is_low: bool = False

    # For labeling later (filled by outcome_tracker)
    price_after_1m: Optional[float] = None
    price_after_5m: Optional[float] = None
    price_after_10m: Optional[float] = None
    price_after_60m: Optional[float] = None
    max_price_10m: Optional[float] = None
    min_price_10m: Optional[float] = None
    profit_pct: Optional[float] = None
    label: Optional[str] = None  # strong_pump/pump/flat/dump/strong_dump


class MoonBotParser:
    """Парсер логов MoonBot."""

    # Regex patterns
    SIGNAL_PATTERN = re.compile(
        r"(\d{2}:\d{2}:\d{2})\s+Signal\s+(\w+-\w+)\s+Ask:([\d.]+)\s+"
        r"dBTC:\s*([-\d.]+)\s+dBTC5m:\s*([-\d.]+)\s+dBTC1m:\s*([-\d.]+)\s+"
        r"24hBTC:\s*([-\d.]+)\s+72hBTC:\s*([-\d.]+)\s+"
        r"dMarkets:\s*([-\d.]+)\s+dMarkets24:\s*([-\d.]+)"
    )

    PUMP_PATTERN = re.compile(
        r"PumpDetection:.*?<([^>]+)>\s*(\w+-\w+)\s+DailyVol:\s*([\d.]+)\s*([mk])?\s+"
        r"PPL/sec:\s*(\d+)\s+Buys/sec:\s*([\d.]+)\s+"
        r"Vol/sec:\s*([\d.]+)\s*k?\s+PriceDelta:\s*([\d.]+)%",
        re.IGNORECASE
    )

    DROP_PATTERN = re.compile(
        r"DropsDetection:.*?<([^>]+)>\s*(\w+-\w+)\s+DailyVol:\s*([\d.]+)\s*([mk])?\s+"
        r"PriceIsLow:\s*(\w+)\s+xPriceDelta:\s*([\d.]+)",
        re.IGNORECASE
    )

    DELTA_PATTERN = re.compile(
        r"Delta:\s+(\w+-\w+)\s+DailyVol:\s*([\d.]+)\s*([mk])?\s+"
        r"HourlyVol:\s*([\d.]+)\s*k?\s+Delta:\s*([\d.]+)%\s+"
        r"LastDelta:\s*([\d.]+)%?\s+Vol:\s*([\d.]+)\s*k?\s*BTC\s+"
        r"VolRaise:\s*([\d.]+)%\s+Buyers:\s*(\d+)\s+Vol/Sec:\s*([\d.]+)",
        re.IGNORECASE
    )

    TOPMARKET_PATTERN = re.compile(
        r"TopMarket.*?Delta:\s*([\d.]+)%\s+(Long|Short)",
        re.IGNORECASE
    )

    STRATEGY_PATTERN = re.compile(r"\(strategy\s+<([^>]+)>\)")

    def __init__(self):
        self.signals: List[MoonBotSignal] = []
        self.current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.signal_count = 0

    def parse_volume(self, value: str, unit: Optional[str]) -> float:
        """Parse volume with unit (k/m)."""
        vol = float(value)
        if unit:
            if unit.lower() == 'm':
                vol *= 1_000_000
            elif unit.lower() == 'k':
                vol *= 1_000
        return vol

    def parse_line(self, line: str) -> Optional[MoonBotSignal]:
        """Parse single log line."""
        line = line.strip()
        if not line:
            return None

        # Try to match Signal pattern
        signal_match = self.SIGNAL_PATTERN.search(line)
        if not signal_match:
            return None

        time_str, symbol, price, dbtc, dbtc5m, dbtc1m, dbtc24h, dbtc72h, dmarkets, dmarkets24 = signal_match.groups()

        # Clean symbol
        clean_symbol = symbol.replace("USDT-", "").replace("-USDT", "")
        if not clean_symbol.endswith("USDT"):
            clean_symbol = clean_symbol + "USDT"

        # Create base signal
        signal = MoonBotSignal(
            timestamp=f"{self.current_date}T{time_str}Z",
            symbol=clean_symbol,
            price=float(price),
            dbtc=float(dbtc),
            dbtc_5m=float(dbtc5m),
            dbtc_1m=float(dbtc1m),
            dbtc_24h=float(dbtc24h),
            dbtc_72h=float(dbtc72h),
            dmarkets=float(dmarkets),
            dmarkets_24h=float(dmarkets24),
            auto_start="AutoStart: TRUE" in line or "AutoStart:TRUE" in line,
            auto_buy="AutoBuy is off" not in line and "AutoBuy:off" not in line,
        )

        # Extract strategy name
        strategy_match = self.STRATEGY_PATTERN.search(line)
        if strategy_match:
            signal.strategy = strategy_match.group(1)

        # Try PumpDetection
        pump_match = self.PUMP_PATTERN.search(line)
        if pump_match:
            strategy, _, vol, vol_unit, ppl, buys, vol_sec, delta = pump_match.groups()
            signal.signal_type = "PumpDetect"
            signal.strategy = strategy
            signal.daily_volume = self.parse_volume(vol, vol_unit)
            signal.ppl_per_sec = int(ppl)
            signal.buys_per_sec = float(buys)
            signal.vol_per_sec = float(vol_sec) * 1000
            signal.delta_pct = float(delta)

        # Try DropsDetection
        drop_match = self.DROP_PATTERN.search(line)
        if drop_match:
            strategy, _, vol, vol_unit, price_low, x_delta = drop_match.groups()
            signal.signal_type = "DropDetect"
            signal.strategy = strategy
            signal.daily_volume = self.parse_volume(vol, vol_unit)
            signal.price_is_low = price_low.lower() == "true"
            signal.delta_pct = float(x_delta)

        # Try Delta signal
        delta_match = self.DELTA_PATTERN.search(line)
        if delta_match:
            _, vol, vol_unit, hvol, delta, last_delta, btc_vol, vol_raise, buyers, vol_sec = delta_match.groups()
            signal.signal_type = "Delta"
            signal.daily_volume = self.parse_volume(vol, vol_unit)
            signal.hourly_volume = float(hvol) * 1000
            signal.delta_pct = float(delta)
            signal.vol_raise_pct = float(vol_raise)
            signal.buyers_count = int(buyers)
            signal.vol_per_sec = float(vol_sec) * 1000

        # Try TopMarket
        top_match = self.TOPMARKET_PATTERN.search(line)
        if top_match:
            delta, direction = top_match.groups()
            signal.signal_type = "TopMarket"
            signal.delta_pct = float(delta)
            signal.direction = direction
            if direction.lower() == "short":
                signal.delta_pct = -signal.delta_pct

        # If no specific type detected, mark as unknown
        if not signal.signal_type:
            signal.signal_type = "Unknown"

        self.signal_count += 1
        return signal

    def parse_text(self, text: str) -> List[MoonBotSignal]:
        """Parse text with multiple lines."""
        signals = []
        for line in text.split("\n"):
            signal = self.parse_line(line)
            if signal:
                signals.append(signal)
        return signals

    def parse_file(self, filepath: Path) -> List[MoonBotSignal]:
        """Parse entire log file."""
        signals = []

        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                signal = self.parse_line(line)
                if signal:
                    signals.append(signal)
                    self._log_signal(signal)

        return signals

    def _log_signal(self, signal: MoonBotSignal):
        """Log parsed signal."""
        emoji = "🔥" if signal.delta_pct > 5 else "📈" if signal.delta_pct > 2 else "📊"
        logger.info(
            f"{emoji} {signal.symbol} | {signal.signal_type} | "
            f"Delta: {signal.delta_pct:+.2f}% | "
            f"Price: ${signal.price:.6f}"
        )

    def watch_file(self, filepath: Path, output_file: Path):
        """Watch log file for new signals (tail -f style)."""
        logger.info(f"👁️ Watching {filepath} for new signals...")

        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            # Go to end of file
            f.seek(0, 2)

            while True:
                line = f.readline()
                if line:
                    signal = self.parse_line(line)
                    if signal:
                        # Save to JSONL
                        self._save_signal(signal, output_file)
                        self._log_signal(signal)
                else:
                    time.sleep(0.1)

    def _save_signal(self, signal: MoonBotSignal, output_file: Path):
        """Save signal to JSONL file."""
        with open(output_file, "a", encoding="utf-8") as out:
            out.write(json.dumps(asdict(signal), ensure_ascii=False) + "\n")


def parse_from_clipboard() -> List[MoonBotSignal]:
    """Parse signals from clipboard."""
    try:
        import pyperclip
        text = pyperclip.paste()
        parser = MoonBotParser()
        signals = parser.parse_text(text)
        return signals
    except ImportError:
        logger.error("Install pyperclip: pip install pyperclip")
        return []


def main():
    parser = argparse.ArgumentParser(description="MoonBot Signal Parser v2")
    parser.add_argument("--input", "-i", type=Path, help="Input log file")
    parser.add_argument("--output", "-o", type=Path, default=OUTPUT_DIR / "moonbot_signals.jsonl")
    parser.add_argument("--watch", "-w", action="store_true", help="Watch mode (tail -f)")
    parser.add_argument("--clipboard", "-c", action="store_true", help="Parse from clipboard")
    parser.add_argument("--text", "-t", type=str, help="Parse from text string")
    args = parser.parse_args()

    moonbot = MoonBotParser()

    # Ensure output directory exists
    args.output.parent.mkdir(parents=True, exist_ok=True)

    if args.clipboard:
        signals = parse_from_clipboard()
        if signals:
            for signal in signals:
                moonbot._save_signal(signal, args.output)
                moonbot._log_signal(signal)
            logger.info(f"✅ Saved {len(signals)} signals to {args.output}")
        else:
            logger.warning("No signals found in clipboard")

    elif args.text:
        signals = moonbot.parse_text(args.text)
        for signal in signals:
            moonbot._save_signal(signal, args.output)
        logger.info(f"✅ Saved {len(signals)} signals to {args.output}")

    elif args.watch and args.input:
        moonbot.watch_file(args.input, args.output)

    elif args.input:
        signals = moonbot.parse_file(args.input)

        # Save to JSONL
        with open(args.output, "w", encoding="utf-8") as f:
            for signal in signals:
                f.write(json.dumps(asdict(signal), ensure_ascii=False) + "\n")

        logger.info(f"✅ Saved {len(signals)} signals to {args.output}")

    else:
        # Interactive mode - read from stdin
        logger.info("📝 Paste MoonBot logs (Ctrl+D to finish):")
        try:
            text = sys.stdin.read()
            signals = moonbot.parse_text(text)
            for signal in signals:
                moonbot._save_signal(signal, args.output)
                moonbot._log_signal(signal)
            logger.info(f"✅ Saved {len(signals)} signals to {args.output}")
        except KeyboardInterrupt:
            logger.info("Interrupted")


if __name__ == "__main__":
    main()
