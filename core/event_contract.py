"""
HOPE/NORE Event Contract v1.0

Unified event format for all sources (Binance/CoinGecko/RSS/Announcements).
All events have canonical serialization and deterministic sha256 IDs.

Contract principles:
- event_id = sha256(canonical_json(core_fields))[:16]
- self-documenting format with sha256: prefix
- immutable after creation
- all sources normalize to this format

Usage:
    from core.event_contract import Event, EventType, create_event

    event = create_event(
        event_type=EventType.REGULATION,
        title="SEC Approves Bitcoin ETF",
        source="coindesk",
        impact_score=0.95,
        sentiment="bullish",
        assets=["BTC"],
    )
    print(event.event_id)  # sha256:a1b2c3d4e5f6...
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Optional, Dict, Any


class EventType(str, Enum):
    """Event classification types per Opinion1."""
    MARKET = "market"           # price moves, volume spikes, liquidations
    REGULATION = "regulation"   # SEC, bans, approvals, legal
    LISTING = "listing"         # exchange listings/delistings
    EXPLOIT = "exploit"         # hacks, vulnerabilities, rug pulls
    MACRO = "macro"             # Fed rates, inflation, geopolitical
    INSTITUTIONAL = "institutional"  # ETF, corporate treasury, fund moves
    SIGNAL = "signal"           # trading signals (internal)
    SYSTEM = "system"           # system events (errors, status)


class Sentiment(str, Enum):
    """Sentiment classification."""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass(frozen=True)
class Event:
    """
    Immutable event with deterministic ID.

    Core fields (used for ID generation):
    - event_type, title, source, timestamp_unix

    Extended fields (not in ID):
    - impact_score, sentiment, assets, source_url, metadata
    """
    # Core fields (immutable, used for ID)
    event_type: str
    title: str
    source: str
    timestamp_unix: float

    # Extended fields
    impact_score: float = 0.3  # 0.0-1.0, default = low impact
    sentiment: str = "neutral"
    assets: tuple = field(default_factory=tuple)  # ("BTC", "ETH", ...)
    source_url: str = ""
    keywords: tuple = field(default_factory=tuple)

    # Computed field (set by create_event)
    event_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "title": self.title,
            "source": self.source,
            "timestamp_unix": self.timestamp_unix,
            "impact_score": self.impact_score,
            "sentiment": self.sentiment,
            "assets": list(self.assets),
            "source_url": self.source_url,
            "keywords": list(self.keywords),
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        """Create Event from dictionary."""
        return cls(
            event_id=data.get("event_id", ""),
            event_type=data.get("event_type", "market"),
            title=data.get("title", ""),
            source=data.get("source", ""),
            timestamp_unix=data.get("timestamp_unix", 0.0),
            impact_score=data.get("impact_score", 0.3),
            sentiment=data.get("sentiment", "neutral"),
            assets=tuple(data.get("assets", [])),
            source_url=data.get("source_url", ""),
            keywords=tuple(data.get("keywords", [])),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "Event":
        """Deserialize from JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)


def compute_event_id(
    event_type: str,
    title: str,
    source: str,
    timestamp_unix: float,
) -> str:
    """
    Compute deterministic event ID from core fields.

    Returns: sha256:<first 16 hex chars>
    """
    # Canonical JSON for core fields only
    canonical = json.dumps({
        "event_type": event_type,
        "title": title,
        "source": source,
        "timestamp_unix": round(timestamp_unix, 3),  # 1ms precision
    }, sort_keys=True, ensure_ascii=True)

    hash_hex = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"sha256:{hash_hex}"


def create_event(
    event_type: str | EventType,
    title: str,
    source: str,
    impact_score: float = 0.3,
    sentiment: str | Sentiment = "neutral",
    assets: List[str] | None = None,
    source_url: str = "",
    keywords: List[str] | None = None,
    timestamp_unix: float | None = None,
) -> Event:
    """
    Factory function to create Event with computed ID.

    Args:
        event_type: Event classification
        title: Event title/headline
        source: Source name (coindesk, binance, etc.)
        impact_score: 0.0-1.0 impact score
        sentiment: bullish/bearish/neutral
        assets: List of affected assets ["BTC", "ETH"]
        source_url: URL to original source
        keywords: Matched keywords for debugging
        timestamp_unix: Unix timestamp (defaults to now)

    Returns:
        Immutable Event with computed event_id
    """
    # Normalize enum values
    if isinstance(event_type, EventType):
        event_type = event_type.value
    if isinstance(sentiment, Sentiment):
        sentiment = sentiment.value

    # Default timestamp
    if timestamp_unix is None:
        timestamp_unix = time.time()

    # Compute deterministic ID
    event_id = compute_event_id(event_type, title, source, timestamp_unix)

    return Event(
        event_id=event_id,
        event_type=event_type,
        title=title,
        source=source,
        timestamp_unix=timestamp_unix,
        impact_score=min(1.0, max(0.0, impact_score)),
        sentiment=sentiment,
        assets=tuple(assets or []),
        source_url=source_url,
        keywords=tuple(keywords or []),
    )


def normalize_classified_event(classified: Any) -> Event:
    """
    Convert ClassifiedEvent to unified Event contract.

    Args:
        classified: ClassifiedEvent from event_classifier.py

    Returns:
        Event with computed ID
    """
    return create_event(
        event_type=getattr(classified, 'event_type', 'market'),
        title=getattr(classified, 'title', ''),
        source=getattr(classified, 'source', 'unknown'),
        impact_score=getattr(classified, 'impact_score', 0.3),
        sentiment=getattr(classified, 'sentiment', 'neutral'),
        assets=list(getattr(classified, 'affected_assets', [])),
        source_url=getattr(classified, 'link', ''),
        keywords=list(getattr(classified, 'keywords_matched', [])),
    )


def is_high_impact(event: Event, threshold: float = 0.6) -> bool:
    """Check if event meets high-impact threshold."""
    return event.impact_score >= threshold


def filter_high_impact(events: List[Event], threshold: float = 0.6) -> List[Event]:
    """Filter events by impact threshold, sorted descending."""
    filtered = [e for e in events if is_high_impact(e, threshold)]
    filtered.sort(key=lambda e: e.impact_score, reverse=True)
    return filtered


if __name__ == "__main__":
    # Test event creation
    print("=== EVENT CONTRACT TEST ===\n")

    event1 = create_event(
        event_type=EventType.REGULATION,
        title="SEC Approves First Spot Bitcoin ETF",
        source="coindesk",
        impact_score=0.95,
        sentiment=Sentiment.BULLISH,
        assets=["BTC"],
        source_url="https://coindesk.com/...",
    )

    print(f"Event ID: {event1.event_id}")
    print(f"Type: {event1.event_type}")
    print(f"Title: {event1.title}")
    print(f"Impact: {event1.impact_score}")
    print(f"Sentiment: {event1.sentiment}")
    print(f"Assets: {event1.assets}")
    print()

    # Test serialization
    json_str = event1.to_json()
    print(f"JSON: {json_str[:100]}...")
    print()

    # Test deserialization
    event2 = Event.from_json(json_str)
    print(f"Restored ID: {event2.event_id}")
    print(f"IDs match: {event1.event_id == event2.event_id}")
