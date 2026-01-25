# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T17:35:00Z
# Purpose: Delisting Detector - автоматическая защита от делистингов
# === END SIGNATURE ===
"""
Delisting Detector - Automated Delisting Protection.

Сканирует новости Binance на предмет делистингов.
При обнаружении делистинга символа - блокирует торговлю этим символом.

FAIL-CLOSED:
- Ошибка чтения новостей = блокировка НОВЫХ позиций
- Обнаружение делистинга = немедленный kill-switch для символа

Integration:
    from core.trade.delisting_detector import DelistingDetector

    detector = DelistingDetector()

    # Перед открытием позиции:
    if detector.is_symbol_blocked("BTCUSDT"):
        return  # STOP - symbol blocked

    # Периодически обновлять:
    detector.scan_news()
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("trade.delisting")

# SSoT paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATE_DIR = BASE_DIR / "state"
BLOCKED_SYMBOLS_PATH = STATE_DIR / "blocked_symbols.json"
DELISTING_AUDIT_PATH = STATE_DIR / "audit" / "delisting_events.jsonl"


@dataclass
class DelistingEvent:
    """Detected delisting event."""
    symbol: str
    detected_utc: str
    source: str
    title: str
    link: str
    delisting_date: Optional[str] = None
    confidence: float = 1.0
    event_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict."""
        return {
            "symbol": self.symbol,
            "detected_utc": self.detected_utc,
            "source": self.source,
            "title": self.title,
            "link": self.link,
            "delisting_date": self.delisting_date,
            "confidence": self.confidence,
            "event_id": self.event_id,
        }


class DelistingDetector:
    """
    Delisting Detector - scans news for delisting announcements.

    FAIL-CLOSED: Any detected delisting = immediate block.
    """

    # Keywords indicating delisting (case-insensitive)
    DELISTING_KEYWORDS = [
        r"will\s+delist",
        r"delisting\s+of",
        r"remove\s+.*trading\s+pair",
        r"delist.*spot\s+trading",
        r"suspension.*trading",
        r"cease\s+trading",
        r"trading\s+termination",
        r"removal\s+of\s+spot",
    ]

    # Keywords for deposit/withdrawal suspension (early warning)
    SUSPENSION_KEYWORDS = [
        r"suspend.*deposit",
        r"suspend.*withdrawal",
        r"halt.*deposit",
        r"halt.*withdrawal",
    ]

    # Extract symbol patterns from titles
    SYMBOL_PATTERNS = [
        r"\b([A-Z]{2,10})USDT\b",
        r"\b([A-Z]{2,10})BTC\b",
        r"\b([A-Z]{2,10})ETH\b",
        r"\b([A-Z]{2,10})BNB\b",
        r"\(([A-Z]{2,10})\)",  # Symbol in parentheses
    ]

    def __init__(
        self,
        blocked_path: Optional[Path] = None,
        audit_path: Optional[Path] = None,
    ):
        """
        Initialize Delisting Detector.

        Args:
            blocked_path: Path to blocked symbols state
            audit_path: Path to delisting events audit
        """
        self.blocked_path = blocked_path or BLOCKED_SYMBOLS_PATH
        self.audit_path = audit_path or DELISTING_AUDIT_PATH

        # Ensure directories exist
        self.blocked_path.parent.mkdir(parents=True, exist_ok=True)
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)

        # Load blocked symbols
        self._blocked_symbols: Dict[str, DelistingEvent] = self._load_blocked()

        logger.info("DelistingDetector initialized: %d symbols blocked", len(self._blocked_symbols))

    def _load_blocked(self) -> Dict[str, DelistingEvent]:
        """Load blocked symbols from state."""
        if not self.blocked_path.exists():
            return {}

        try:
            data = json.loads(self.blocked_path.read_text(encoding="utf-8"))
            blocked = {}
            for symbol, event_data in data.get("blocked_symbols", {}).items():
                blocked[symbol] = DelistingEvent(
                    symbol=event_data.get("symbol", symbol),
                    detected_utc=event_data.get("detected_utc", ""),
                    source=event_data.get("source", ""),
                    title=event_data.get("title", ""),
                    link=event_data.get("link", ""),
                    delisting_date=event_data.get("delisting_date"),
                    confidence=event_data.get("confidence", 1.0),
                    event_id=event_data.get("event_id", ""),
                )
            return blocked
        except Exception as e:
            logger.error("Failed to load blocked symbols: %s", e)
            return {}

    def _save_blocked(self) -> None:
        """Save blocked symbols to state (atomic)."""
        data = {
            "schema_version": "blocked_symbols_v1",
            "updated_utc": datetime.now(timezone.utc).isoformat(),
            "blocked_symbols": {
                symbol: event.to_dict()
                for symbol, event in self._blocked_symbols.items()
            },
        }

        # Atomic write
        tmp = self.blocked_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.blocked_path)

    def _log_event(self, event: DelistingEvent) -> None:
        """Log delisting event to audit trail."""
        record = {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            **event.to_dict(),
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"

        with open(self.audit_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def _extract_symbols(self, text: str) -> Set[str]:
        """Extract potential trading symbols from text."""
        symbols = set()
        for pattern in self.SYMBOL_PATTERNS:
            matches = re.findall(pattern, text.upper())
            for match in matches:
                if len(match) >= 2 and match not in {"THE", "FOR", "AND", "ALL"}:
                    symbols.add(match)
        return symbols

    def _check_delisting_keywords(self, text: str) -> tuple[bool, float]:
        """Check if text contains delisting keywords. Returns (is_delisting, confidence)."""
        text_lower = text.lower()

        # Strong indicators
        for pattern in self.DELISTING_KEYWORDS:
            if re.search(pattern, text_lower):
                return True, 1.0

        # Weak indicators (suspension)
        for pattern in self.SUSPENSION_KEYWORDS:
            if re.search(pattern, text_lower):
                return True, 0.5

        return False, 0.0

    def is_symbol_blocked(self, symbol: str) -> bool:
        """
        Check if symbol is blocked due to delisting.

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")

        Returns:
            True if symbol should NOT be traded
        """
        # Normalize symbol
        symbol = symbol.upper().strip()

        # Check exact match
        if symbol in self._blocked_symbols:
            return True

        # Extract base currency and check
        for base in self._extract_symbols(symbol):
            if base in self._blocked_symbols:
                return True
            # Check if base+USDT is blocked
            if f"{base}USDT" in self._blocked_symbols:
                return True

        return False

    def get_blocked_reason(self, symbol: str) -> Optional[str]:
        """Get reason why symbol is blocked."""
        symbol = symbol.upper().strip()

        if symbol in self._blocked_symbols:
            event = self._blocked_symbols[symbol]
            return f"Delisting detected: {event.title} (source: {event.source})"

        for base in self._extract_symbols(symbol):
            if base in self._blocked_symbols:
                event = self._blocked_symbols[base]
                return f"Base currency delisting: {event.title}"

        return None

    def block_symbol(
        self,
        symbol: str,
        source: str,
        title: str,
        link: str = "",
        confidence: float = 1.0,
    ) -> None:
        """
        Block a symbol from trading.

        Args:
            symbol: Symbol to block (e.g., "LUNA" or "LUNAUSDT")
            source: Source of detection
            title: Title of announcement
            link: URL to announcement
            confidence: Confidence level (0.0-1.0)
        """
        symbol = symbol.upper().strip()

        event = DelistingEvent(
            symbol=symbol,
            detected_utc=datetime.now(timezone.utc).isoformat(),
            source=source,
            title=title,
            link=link,
            confidence=confidence,
            event_id=hashlib.sha256(f"{symbol}{title}{time.time()}".encode()).hexdigest()[:16],
        )

        self._blocked_symbols[symbol] = event
        self._save_blocked()
        self._log_event(event)

        logger.warning(
            "SYMBOL BLOCKED: %s (source=%s, confidence=%.2f)",
            symbol, source, confidence
        )

    def unblock_symbol(self, symbol: str, reason: str = "manual") -> bool:
        """
        Unblock a symbol (manual override only).

        Args:
            symbol: Symbol to unblock
            reason: Reason for unblocking

        Returns:
            True if symbol was unblocked
        """
        symbol = symbol.upper().strip()

        if symbol not in self._blocked_symbols:
            return False

        del self._blocked_symbols[symbol]
        self._save_blocked()

        logger.info("SYMBOL UNBLOCKED: %s (reason=%s)", symbol, reason)
        return True

    def analyze_news_item(
        self,
        title: str,
        link: str,
        source_id: str,
    ) -> List[DelistingEvent]:
        """
        Analyze a single news item for delisting signals.

        Args:
            title: News title
            link: News URL
            source_id: Source identifier

        Returns:
            List of detected delisting events
        """
        events = []

        # Check for delisting keywords
        is_delisting, confidence = self._check_delisting_keywords(title)

        if not is_delisting:
            return events

        # Extract affected symbols
        symbols = self._extract_symbols(title)

        if not symbols:
            # Generic delisting mention without specific symbol
            logger.debug("Delisting keywords found but no symbol extracted: %s", title)
            return events

        for symbol in symbols:
            event = DelistingEvent(
                symbol=symbol,
                detected_utc=datetime.now(timezone.utc).isoformat(),
                source=source_id,
                title=title,
                link=link,
                confidence=confidence,
                event_id=hashlib.sha256(f"{symbol}{title}".encode()).hexdigest()[:16],
            )
            events.append(event)

        return events

    def scan_news(self, items: Optional[List[dict]] = None) -> List[DelistingEvent]:
        """
        Scan news items for delisting signals.

        Args:
            items: List of news items (if None, loads from state/news_items.jsonl)

        Returns:
            List of newly detected delisting events
        """
        if items is None:
            items = self._load_recent_news()

        detected = []

        for item in items:
            title = item.get("title", "")
            link = item.get("link", "")
            source_id = item.get("source_id", "unknown")

            events = self.analyze_news_item(title, link, source_id)

            for event in events:
                # Skip if already blocked
                if event.symbol in self._blocked_symbols:
                    continue

                # Block the symbol
                self.block_symbol(
                    symbol=event.symbol,
                    source=event.source,
                    title=event.title,
                    link=event.link,
                    confidence=event.confidence,
                )
                detected.append(event)

        if detected:
            logger.warning("DELISTING SCAN: %d new events detected", len(detected))

        return detected

    def _load_recent_news(self, max_items: int = 100) -> List[dict]:
        """Load recent news from spider state."""
        news_path = STATE_DIR / "news_items.jsonl"

        if not news_path.exists():
            return []

        items = []
        try:
            with open(news_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            items.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            logger.error("Failed to load news: %s", e)
            return []

        # Return most recent
        return items[-max_items:]

    def get_status(self) -> Dict[str, Any]:
        """Get detector status."""
        return {
            "blocked_count": len(self._blocked_symbols),
            "blocked_symbols": list(self._blocked_symbols.keys()),
            "last_scan": None,  # TODO: track last scan time
        }


# === CLI Interface ===
def main() -> int:
    """CLI entrypoint."""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if len(sys.argv) < 2:
        print("Usage: python -m core.trade.delisting_detector <command>")
        print("Commands:")
        print("  status              - Show detector status")
        print("  scan                - Scan news for delistings")
        print("  check <symbol>      - Check if symbol is blocked")
        print("  block <symbol>      - Manually block a symbol")
        print("  unblock <symbol>    - Manually unblock a symbol")
        return 1

    command = sys.argv[1]
    detector = DelistingDetector()

    if command == "status":
        status = detector.get_status()
        print(json.dumps(status, indent=2))
        return 0

    elif command == "scan":
        events = detector.scan_news()
        print(f"Detected {len(events)} delisting events")
        for event in events:
            print(f"  - {event.symbol}: {event.title}")
        return 0

    elif command == "check":
        symbol = sys.argv[2] if len(sys.argv) > 2 else "BTCUSDT"
        blocked = detector.is_symbol_blocked(symbol)
        reason = detector.get_blocked_reason(symbol) or "Not blocked"
        print(f"Symbol: {symbol}")
        print(f"Blocked: {blocked}")
        print(f"Reason: {reason}")
        return 1 if blocked else 0

    elif command == "block":
        if len(sys.argv) < 3:
            print("Usage: block <symbol>")
            return 1
        symbol = sys.argv[2]
        detector.block_symbol(
            symbol=symbol,
            source="manual",
            title="Manual block by operator",
        )
        print(f"Blocked: {symbol}")
        return 0

    elif command == "unblock":
        if len(sys.argv) < 3:
            print("Usage: unblock <symbol>")
            return 1
        symbol = sys.argv[2]
        if detector.unblock_symbol(symbol, reason="manual"):
            print(f"Unblocked: {symbol}")
            return 0
        else:
            print(f"Symbol not blocked: {symbol}")
            return 1

    else:
        print(f"Unknown command: {command}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
