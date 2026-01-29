# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 02:22:00 UTC
# Purpose: Single Source of Truth for PriceFeed subscriptions
# Contract: Atomic persistence, deterministic state
# === END SIGNATURE ===
"""
PRICEFEED SSoT (Single Source of Truth)

Manages subscribed symbols with:
- Atomic persistence
- Deterministic startup
- Subscription history

Invariants:
1. subscribed_count == len(subscribed)
2. All subscribed symbols have price entries
3. stale=True if price is null or age > MAX_AGE_SEC
"""

import json
import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set, Optional, Any
import logging

log = logging.getLogger("PRICEFEED-SSOT")

SSOT_PATH = Path("state/pricefeed/subscribed.json")
HISTORY_PATH = Path("state/pricefeed/subscription_history.jsonl")

# Price staleness threshold
MAX_AGE_SEC = 60.0


class PriceFeedSSoT:
    """
    Single Source of Truth for PriceFeed subscriptions.

    All subscription operations go through this class.
    State is persisted atomically after each change.
    """

    def __init__(self, ssot_path: Path = None):
        self.ssot_path = ssot_path or SSOT_PATH
        self.ssot_path.parent.mkdir(parents=True, exist_ok=True)
        self._subscribed: Set[str] = set()
        self._load()

    def _load(self) -> None:
        """Load subscriptions from disk."""
        if self.ssot_path.exists():
            try:
                data = json.loads(self.ssot_path.read_text(encoding="utf-8"))
                self._subscribed = set(data.get("subscribed", []))
                log.info(f"Loaded {len(self._subscribed)} subscribed symbols from SSoT")
            except Exception as e:
                log.error(f"Failed to load SSoT: {e}")
                self._subscribed = set()
        else:
            self._subscribed = set()

    def _save(self, reason: str = "") -> None:
        """Atomic save with sha256."""
        data = {
            "subscribed": sorted(self._subscribed),
            "count": len(self._subscribed),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason
        }

        # Add sha256
        canonical = json.dumps(
            {k: v for k, v in data.items() if k != "sha256"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":")
        ).encode("utf-8")
        data["sha256"] = "sha256:" + hashlib.sha256(canonical).hexdigest()[:16]

        content = json.dumps(data, indent=2, ensure_ascii=False)

        # Atomic write
        tmp = self.ssot_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.ssot_path)

        # Log to history
        self._log_history(reason)

    def _log_history(self, reason: str) -> None:
        """Append to subscription history."""
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "subscribed": sorted(self._subscribed),
            "count": len(self._subscribed),
            "reason": reason
        }
        canonical = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        entry["sha256"] = "sha256:" + hashlib.sha256(canonical).hexdigest()[:16]

        with open(HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    @property
    def subscribed(self) -> Set[str]:
        """Get current subscribed symbols (immutable copy)."""
        return set(self._subscribed)

    @property
    def count(self) -> int:
        """Get count of subscribed symbols."""
        return len(self._subscribed)

    def subscribe(self, symbols: List[str], reason: str = "") -> List[str]:
        """
        Subscribe to symbols.

        Returns list of newly subscribed symbols.
        """
        symbols = [s.upper().strip() for s in symbols if s]
        new_symbols = [s for s in symbols if s not in self._subscribed]

        if new_symbols:
            self._subscribed.update(new_symbols)
            self._save(reason or f"subscribe:{','.join(new_symbols)}")
            log.info(f"Subscribed to {len(new_symbols)} new symbols: {new_symbols}")

        return new_symbols

    def unsubscribe(self, symbols: List[str], reason: str = "") -> List[str]:
        """
        Unsubscribe from symbols.

        Returns list of actually unsubscribed symbols.
        """
        symbols = [s.upper().strip() for s in symbols if s]
        removed = [s for s in symbols if s in self._subscribed]

        if removed:
            self._subscribed -= set(removed)
            self._save(reason or f"unsubscribe:{','.join(removed)}")
            log.info(f"Unsubscribed from {len(removed)} symbols: {removed}")

        return removed

    def set_subscriptions(self, symbols: List[str], reason: str = "") -> None:
        """Replace all subscriptions with new list."""
        new_set = set(s.upper().strip() for s in symbols if s)

        if new_set != self._subscribed:
            self._subscribed = new_set
            self._save(reason or "set_subscriptions")
            log.info(f"Set subscriptions to {len(self._subscribed)} symbols")

    def clear(self, reason: str = "") -> None:
        """Clear all subscriptions."""
        if self._subscribed:
            self._subscribed.clear()
            self._save(reason or "clear")
            log.info("Cleared all subscriptions")

    def validate_prices(self, prices: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate prices against SSoT invariants.

        Returns validation result with any violations.
        """
        result = {
            "valid": True,
            "violations": [],
            "subscribed_count": len(self._subscribed),
            "prices_count": len(prices)
        }

        # Invariant 1: All subscribed symbols should have price entries
        for symbol in self._subscribed:
            if symbol not in prices:
                result["valid"] = False
                result["violations"].append(f"MISSING_PRICE:{symbol}")

        # Invariant 2: Check price structure
        for symbol, price_data in prices.items():
            if isinstance(price_data, dict):
                required_fields = ["price", "stale", "subscribed"]
                for field in required_fields:
                    if field not in price_data:
                        result["violations"].append(f"MISSING_FIELD:{symbol}.{field}")

                # Invariant 3: stale consistency
                price = price_data.get("price")
                age = price_data.get("age_sec", float("inf"))
                stale = price_data.get("stale")

                should_be_stale = price is None or age > MAX_AGE_SEC
                if stale != should_be_stale:
                    result["violations"].append(
                        f"STALE_MISMATCH:{symbol} (is:{stale}, should:{should_be_stale})"
                    )

        if result["violations"]:
            result["valid"] = False

        return result


# Singleton instance
_ssot_instance: Optional[PriceFeedSSoT] = None


def get_pricefeed_ssot() -> PriceFeedSSoT:
    """Get singleton SSoT instance."""
    global _ssot_instance
    if _ssot_instance is None:
        _ssot_instance = PriceFeedSSoT()
    return _ssot_instance
