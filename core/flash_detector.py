"""
HOPE/NORE Flash Alert Detector v1.0

Detects conditions that trigger "молния" (flash) alerts.

Flash triggers (deterministic, not "feelings"):
1. Market shock:
   - BTC/ETH: |Δ| >= 2.0% in 15min OR >= 4.0% in 60min
   - Top-20: |Δ| >= 6.0% in 60min
2. Volume shock:
   - volume_ratio >= 4.0x vs rolling baseline
3. Critical news:
   - impact_score >= 0.85
4. Binance announcements:
   - Listing/delisting/maintenance = always flash

Rate limits enforced by PublicationScheduler (90s min, 6/hour max).

Usage:
    from core.flash_detector import FlashDetector, get_flash_detector

    detector = get_flash_detector()
    alerts = detector.check_market_flash(snapshot, price_history)
    alerts += detector.check_news_flash(events)
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

STATE_DIR = Path(r"C:\Users\kirillDev\Desktop\TradingBot\minibot\state")
PRICE_HISTORY_FILE = STATE_DIR / "price_history.json"


class FlashType(str, Enum):
    """Flash alert types."""
    MARKET_SHOCK_15M = "market_shock_15m"
    MARKET_SHOCK_60M = "market_shock_60m"
    VOLUME_SHOCK = "volume_shock"
    CRITICAL_NEWS = "critical_news"
    BINANCE_ANNOUNCEMENT = "binance_announcement"


CRITICAL_PAIRS = ["BTCUSDT", "ETHUSDT"]
TOP20_PAIRS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
    "MATICUSDT", "LTCUSDT", "SHIBUSDT", "TRXUSDT", "ATOMUSDT",
    "UNIUSDT", "XLMUSDT", "NEARUSDT", "APTUSDT", "OPUSDT",
]

BTC_ETH_THRESHOLD_15M = 2.0
BTC_ETH_THRESHOLD_60M = 4.0
TOP20_THRESHOLD_60M = 6.0
VOLUME_SHOCK_RATIO = 4.0
CRITICAL_NEWS_IMPACT = 0.85


@dataclass
class FlashAlert:
    """A flash alert to be published."""
    flash_type: FlashType
    symbol: Optional[str]
    title: str
    details: str
    severity: float  # 0.0-1.0
    timestamp: float
    dedup_key: str  # for preventing duplicates

    def to_dict(self) -> dict:
        return {
            "flash_type": self.flash_type.value,
            "symbol": self.symbol,
            "title": self.title,
            "details": self.details,
            "severity": self.severity,
            "timestamp": self.timestamp,
            "dedup_key": self.dedup_key,
        }


@dataclass
class PricePoint:
    """Single price point."""
    symbol: str
    price: float
    timestamp: float


class FlashDetector:
    """
    Detects flash alert conditions.

    Uses price history for time-based comparisons.
    """

    def __init__(self):
        self._price_history: Dict[str, List[PricePoint]] = {}
        self._volume_baseline: Dict[str, float] = {}
        self._seen_alerts: set = set()
        self._load_price_history()

    def _load_price_history(self) -> None:
        """Load price history from file."""
        if not PRICE_HISTORY_FILE.exists():
            return

        try:
            content = PRICE_HISTORY_FILE.read_text(encoding="utf-8")
            data = json.loads(content)

            for symbol, points in data.get("prices", {}).items():
                self._price_history[symbol] = [
                    PricePoint(symbol=symbol, price=p["price"], timestamp=p["ts"])
                    for p in points
                ]

            self._volume_baseline = data.get("volume_baseline", {})
            logger.info("Loaded price history for %d symbols", len(self._price_history))

        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to load price history: %s", e)

    def _save_price_history(self) -> None:
        """Save price history to file."""
        cutoff = time.time() - 3600

        data = {
            "prices": {
                symbol: [
                    {"price": p.price, "ts": p.timestamp}
                    for p in points if p.timestamp > cutoff
                ]
                for symbol, points in self._price_history.items()
            },
            "volume_baseline": self._volume_baseline,
            "last_update": time.time(),
        }

        try:
            content = json.dumps(data, indent=2)
            PRICE_HISTORY_FILE.write_text(content, encoding="utf-8")
        except OSError as e:
            logger.error("Failed to save price history: %s", e)

    def record_prices(self, prices: Dict[str, float]) -> None:
        """Record current prices for history."""
        now = time.time()

        for symbol, price in prices.items():
            if symbol not in self._price_history:
                self._price_history[symbol] = []

            self._price_history[symbol].append(
                PricePoint(symbol=symbol, price=price, timestamp=now)
            )

            cutoff = now - 3700
            self._price_history[symbol] = [
                p for p in self._price_history[symbol]
                if p.timestamp > cutoff
            ]

        self._save_price_history()

    def record_volumes(self, volumes: Dict[str, float]) -> None:
        """Update rolling volume baseline."""
        for symbol, volume in volumes.items():
            if symbol not in self._volume_baseline:
                self._volume_baseline[symbol] = volume
            else:
                self._volume_baseline[symbol] = (
                    0.9 * self._volume_baseline[symbol] + 0.1 * volume
                )

    def _get_price_change(self, symbol: str, lookback_sec: int) -> Optional[float]:
        """Get price change percentage over lookback period."""
        if symbol not in self._price_history:
            return None

        points = self._price_history[symbol]
        if len(points) < 2:
            return None

        now = time.time()
        cutoff = now - lookback_sec

        current = points[-1].price
        historical = None

        for p in reversed(points):
            if p.timestamp <= cutoff:
                historical = p.price
                break

        if historical is None:
            oldest = min(points, key=lambda x: x.timestamp)
            if now - oldest.timestamp >= lookback_sec * 0.8:
                historical = oldest.price

        if historical is None or historical == 0:
            return None

        return (current - historical) / historical * 100

    def check_market_flash(self, snapshot: Any) -> List[FlashAlert]:
        """
        Check for market-based flash alerts.

        Args:
            snapshot: MarketSnapshot with tickers

        Returns:
            List of FlashAlert objects
        """
        alerts: List[FlashAlert] = []
        now = time.time()
        tickers = getattr(snapshot, 'tickers', {})

        if not tickers:
            return alerts

        prices = {s: t.price for s, t in tickers.items() if hasattr(t, 'price')}
        self.record_prices(prices)

        volumes = {s: t.volume_24h for s, t in tickers.items() if hasattr(t, 'volume_24h')}
        self.record_volumes(volumes)

        for symbol in CRITICAL_PAIRS:
            change_15m = self._get_price_change(symbol, 900)
            if change_15m is not None and abs(change_15m) >= BTC_ETH_THRESHOLD_15M:
                dedup_key = f"{symbol}:15m:{int(now // 600)}"
                if dedup_key not in self._seen_alerts:
                    self._seen_alerts.add(dedup_key)
                    direction = "UP" if change_15m > 0 else "DOWN"
                    alerts.append(FlashAlert(
                        flash_type=FlashType.MARKET_SHOCK_15M,
                        symbol=symbol,
                        title=f"⚡ {symbol.replace('USDT', '')} {direction} {abs(change_15m):.1f}% in 15min",
                        details=f"Price: ${tickers[symbol].price:,.2f}",
                        severity=min(1.0, abs(change_15m) / 10),
                        timestamp=now,
                        dedup_key=dedup_key,
                    ))

            change_60m = self._get_price_change(symbol, 3600)
            if change_60m is not None and abs(change_60m) >= BTC_ETH_THRESHOLD_60M:
                dedup_key = f"{symbol}:60m:{int(now // 1800)}"
                if dedup_key not in self._seen_alerts:
                    self._seen_alerts.add(dedup_key)
                    direction = "UP" if change_60m > 0 else "DOWN"
                    alerts.append(FlashAlert(
                        flash_type=FlashType.MARKET_SHOCK_60M,
                        symbol=symbol,
                        title=f"⚡ {symbol.replace('USDT', '')} {direction} {abs(change_60m):.1f}% in 1h",
                        details=f"Price: ${tickers[symbol].price:,.2f}",
                        severity=min(1.0, abs(change_60m) / 10),
                        timestamp=now,
                        dedup_key=dedup_key,
                    ))

        for symbol in TOP20_PAIRS:
            if symbol in CRITICAL_PAIRS:
                continue
            if symbol not in tickers:
                continue

            change_60m = self._get_price_change(symbol, 3600)
            if change_60m is not None and abs(change_60m) >= TOP20_THRESHOLD_60M:
                dedup_key = f"{symbol}:top20:{int(now // 1800)}"
                if dedup_key not in self._seen_alerts:
                    self._seen_alerts.add(dedup_key)
                    direction = "UP" if change_60m > 0 else "DOWN"
                    alerts.append(FlashAlert(
                        flash_type=FlashType.MARKET_SHOCK_60M,
                        symbol=symbol,
                        title=f"⚡ {symbol.replace('USDT', '')} {direction} {abs(change_60m):.1f}%",
                        details=f"1h move, Price: ${tickers[symbol].price:,.4f}",
                        severity=min(1.0, abs(change_60m) / 15),
                        timestamp=now,
                        dedup_key=dedup_key,
                    ))

        for symbol, ticker in tickers.items():
            if symbol not in self._volume_baseline:
                continue

            baseline = self._volume_baseline[symbol]
            if baseline <= 0:
                continue

            current_vol = getattr(ticker, 'volume_24h', 0)
            ratio = current_vol / baseline

            if ratio >= VOLUME_SHOCK_RATIO:
                dedup_key = f"{symbol}:vol:{int(now // 1800)}"
                if dedup_key not in self._seen_alerts:
                    self._seen_alerts.add(dedup_key)
                    alerts.append(FlashAlert(
                        flash_type=FlashType.VOLUME_SHOCK,
                        symbol=symbol,
                        title=f"📊 {symbol.replace('USDT', '')} Volume Spike {ratio:.1f}x",
                        details=f"24h volume: ${current_vol/1e9:.2f}B",
                        severity=min(1.0, ratio / 10),
                        timestamp=now,
                        dedup_key=dedup_key,
                    ))

        return alerts

    def check_news_flash(self, events: List[Any]) -> List[FlashAlert]:
        """
        Check for news-based flash alerts.

        Args:
            events: List of Event objects with impact_score

        Returns:
            List of FlashAlert objects
        """
        alerts: List[FlashAlert] = []
        now = time.time()

        for event in events:
            impact = getattr(event, 'impact_score', 0)
            if impact < CRITICAL_NEWS_IMPACT:
                continue

            event_id = getattr(event, 'event_id', '')
            dedup_key = f"news:{event_id}"

            if dedup_key in self._seen_alerts:
                continue

            self._seen_alerts.add(dedup_key)

            title = getattr(event, 'title', '')[:80]
            event_type = getattr(event, 'event_type', 'news')
            source = getattr(event, 'source', 'unknown')

            if 'listing' in title.lower() or 'delisting' in title.lower():
                flash_type = FlashType.BINANCE_ANNOUNCEMENT
            else:
                flash_type = FlashType.CRITICAL_NEWS

            alerts.append(FlashAlert(
                flash_type=flash_type,
                symbol=None,
                title=f"🔥 {title}",
                details=f"Source: {source}, Impact: {impact:.0%}",
                severity=impact,
                timestamp=now,
                dedup_key=dedup_key,
            ))

        return alerts

    def clear_old_dedup(self) -> None:
        """Clear old dedup entries (call periodically)."""
        now = time.time()
        cutoff = now - 3600

        self._seen_alerts = {
            key for key in self._seen_alerts
            if not key.startswith("news:")
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get detector statistics."""
        return {
            "tracked_symbols": len(self._price_history),
            "dedup_entries": len(self._seen_alerts),
            "volume_baselines": len(self._volume_baseline),
        }


def get_flash_detector() -> FlashDetector:
    """Get singleton detector instance."""
    global _detector_instance
    if "_detector_instance" not in globals():
        _detector_instance = FlashDetector()
    return _detector_instance


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("=== FLASH DETECTOR TEST ===\n")

    detector = FlashDetector()
    stats = detector.get_stats()

    print("Detector Stats:")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    print("\nFlash thresholds:")
    print(f"  BTC/ETH 15min: {BTC_ETH_THRESHOLD_15M}%")
    print(f"  BTC/ETH 60min: {BTC_ETH_THRESHOLD_60M}%")
    print(f"  Top-20 60min: {TOP20_THRESHOLD_60M}%")
    print(f"  Volume ratio: {VOLUME_SHOCK_RATIO}x")
    print(f"  Critical news impact: {CRITICAL_NEWS_IMPACT}")
