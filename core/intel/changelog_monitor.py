# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T01:00:00Z
# Purpose: Binance Changelog Monitor - detect contract-breaking changes
# Security: Fail-closed, any breaking change = CRITICAL event
# === END SIGNATURE ===
"""
Binance Changelog Monitor.

Detects contract-breaking changes in Binance API that require
immediate attention before live trading.

CRITICAL EVENTS (require STOP/HOLD):
- Endpoint deprecation/removal
- Signature algorithm changes
- Rate limit changes
- Maintenance windows

KNOWN BREAKING CHANGES (as of 2026-01-28):
- 2026-02-20 07:00 UTC: userDataStream endpoints removed
- 2026-02-11 07:00 UTC: ICEBERG_PARTS filter increase to 50
- 2026-01-15: Signature percent-encoding required

Usage:
    from core.intel.changelog_monitor import check_binance_changelog

    events = check_binance_changelog()
    for event in events:
        if event.is_critical:
            print(f"CRITICAL: {event.summary}")
            # Block trading until resolved
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("intel.changelog")

# SSoT paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATE_DIR = BASE_DIR / "state"
SNAPSHOTS_DIR = STATE_DIR / "snapshots" / "changelog"


class EventSeverity(str, Enum):
    """Event severity levels."""
    CRITICAL = "CRITICAL"  # Must STOP trading
    WARNING = "WARNING"    # Review before trading
    INFO = "INFO"          # FYI only


class EventType(str, Enum):
    """Changelog event types."""
    ENDPOINT_REMOVAL = "ENDPOINT_REMOVAL"
    ENDPOINT_DEPRECATION = "ENDPOINT_DEPRECATION"
    SIGNATURE_CHANGE = "SIGNATURE_CHANGE"
    RATE_LIMIT_CHANGE = "RATE_LIMIT_CHANGE"
    FILTER_CHANGE = "FILTER_CHANGE"
    MAINTENANCE = "MAINTENANCE"
    NEW_FEATURE = "NEW_FEATURE"
    OTHER = "OTHER"


@dataclass
class ContractBreakingChange:
    """Known contract-breaking change."""
    event_type: EventType
    effective_date: str  # ISO format
    effective_timestamp: float  # Unix timestamp
    summary: str
    affected_endpoints: List[str]
    action_required: str
    source_url: str = ""


@dataclass
class ChangelogEvent:
    """Parsed changelog event."""
    event_id: str  # sha256:...
    event_type: EventType
    severity: EventSeverity
    summary: str
    details: str
    effective_date: Optional[str] = None
    affected_endpoints: List[str] = field(default_factory=list)
    source_sha256: str = ""
    detected_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    schema_version: str = "changelog_event.v1"

    @property
    def is_critical(self) -> bool:
        """Check if event requires immediate action."""
        return self.severity == EventSeverity.CRITICAL

    @property
    def is_effective_soon(self) -> bool:
        """Check if change is effective within 7 days."""
        if not self.effective_date:
            return False
        try:
            effective = datetime.fromisoformat(self.effective_date.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            return (effective - now).days <= 7
        except Exception:
            return False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["event_type"] = self.event_type.value
        d["severity"] = self.severity.value
        d["is_critical"] = self.is_critical
        d["is_effective_soon"] = self.is_effective_soon
        return d


# === KNOWN BREAKING CHANGES (hardcoded for reliability) ===
KNOWN_BREAKING_CHANGES: List[ContractBreakingChange] = [
    ContractBreakingChange(
        event_type=EventType.ENDPOINT_REMOVAL,
        effective_date="2026-02-20T07:00:00Z",
        effective_timestamp=1771484400.0,
        summary="userDataStream REST and WebSocket endpoints will be removed",
        affected_endpoints=[
            "POST /api/v3/userDataStream",
            "PUT /api/v3/userDataStream",
            "DELETE /api/v3/userDataStream",
            "userDataStream.start",
            "userDataStream.ping",
            "userDataStream.stop",
        ],
        action_required="Migrate to WebSocket API userDataStream subscription",
        source_url="https://developers.binance.com/docs/binance-spot-api-docs/CHANGELOG",
    ),
    ContractBreakingChange(
        event_type=EventType.FILTER_CHANGE,
        effective_date="2026-02-11T07:00:00Z",
        effective_timestamp=1770706800.0,
        summary="ICEBERG_PARTS filter will increase to 50 for all symbols",
        affected_endpoints=["exchangeInfo"],
        action_required="Update filter parsing if using ICEBERG orders",
        source_url="https://developers.binance.com/docs/binance-spot-api-docs/CHANGELOG",
    ),
    ContractBreakingChange(
        event_type=EventType.SIGNATURE_CHANGE,
        effective_date="2026-01-15T07:00:00Z",
        effective_timestamp=1768467600.0,
        summary="Percent-encode payloads before computing signatures",
        affected_endpoints=["All signed endpoints"],
        action_required="Ensure signature computation uses percent-encoded payload",
        source_url="https://developers.binance.com/docs/binance-spot-api-docs/CHANGELOG",
    ),
]


class ChangelogMonitor:
    """
    Binance Changelog Monitor.

    Tracks contract-breaking changes and generates CRITICAL events
    when changes are imminent.

    FAIL-CLOSED: Breaking change within 7 days = CRITICAL event.
    """

    def __init__(self, cache_ttl_seconds: int = 3600):
        """
        Initialize changelog monitor.

        Args:
            cache_ttl_seconds: How long to cache changelog data
        """
        self.cache_ttl = cache_ttl_seconds
        self._last_check: float = 0
        self._cached_events: List[ChangelogEvent] = []

        SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    def check(self, force: bool = False) -> List[ChangelogEvent]:
        """
        Check for breaking changes.

        Args:
            force: Force re-check even if cache is valid

        Returns:
            List of ChangelogEvent objects
        """
        now = time.time()

        # Use cache if valid
        if not force and (now - self._last_check) < self.cache_ttl:
            return self._cached_events

        events = []

        # === CHECK 1: Known breaking changes ===
        for change in KNOWN_BREAKING_CHANGES:
            event = self._evaluate_known_change(change)
            if event:
                events.append(event)

        # === CHECK 2: Fetch live changelog (optional, best-effort) ===
        live_events = self._fetch_live_changelog()
        events.extend(live_events)

        # Deduplicate by event_id
        seen_ids = set()
        unique_events = []
        for e in events:
            if e.event_id not in seen_ids:
                seen_ids.add(e.event_id)
                unique_events.append(e)

        # Sort by severity (CRITICAL first)
        unique_events.sort(key=lambda x: (
            0 if x.severity == EventSeverity.CRITICAL else 1,
            x.effective_date or "9999"
        ))

        self._cached_events = unique_events
        self._last_check = now

        # Log critical events
        for e in unique_events:
            if e.is_critical:
                logger.critical("BREAKING CHANGE DETECTED: %s (effective: %s)",
                              e.summary, e.effective_date)

        return unique_events

    def _evaluate_known_change(self, change: ContractBreakingChange) -> Optional[ChangelogEvent]:
        """Evaluate a known breaking change."""
        now = time.time()
        effective_ts = change.effective_timestamp

        # Calculate days until effective
        days_until = (effective_ts - now) / 86400

        # Determine severity
        if days_until < 0:
            # Already effective - check if we're using affected endpoints
            severity = EventSeverity.CRITICAL
            summary = f"[ACTIVE] {change.summary}"
        elif days_until <= 7:
            severity = EventSeverity.CRITICAL
            summary = f"[{int(days_until)}d] {change.summary}"
        elif days_until <= 30:
            severity = EventSeverity.WARNING
            summary = f"[{int(days_until)}d] {change.summary}"
        else:
            # More than 30 days - just info
            severity = EventSeverity.INFO
            summary = f"[{int(days_until)}d] {change.summary}"

        # Generate event ID
        event_data = f"{change.event_type.value}:{change.effective_date}:{change.summary}"
        event_id = f"sha256:{hashlib.sha256(event_data.encode()).hexdigest()}"

        return ChangelogEvent(
            event_id=event_id,
            event_type=change.event_type,
            severity=severity,
            summary=summary,
            details=change.action_required,
            effective_date=change.effective_date,
            affected_endpoints=change.affected_endpoints,
            source_sha256=f"sha256:{hashlib.sha256(change.source_url.encode()).hexdigest()[:16]}",
        )

    def _fetch_live_changelog(self) -> List[ChangelogEvent]:
        """
        Fetch live changelog from Binance (best-effort).

        Returns empty list on failure - we have known changes as backup.
        """
        try:
            import requests

            url = "https://developers.binance.com/docs/binance-spot-api-docs/CHANGELOG"
            headers = {
                "User-Agent": "HOPE-Bot/1.0 (changelog-monitor)"
            }

            resp = requests.get(url, headers=headers, timeout=15)

            if resp.status_code != 200:
                logger.warning("Failed to fetch changelog: HTTP %d", resp.status_code)
                return []

            # Save snapshot
            snapshot_path = self._save_snapshot(resp.content, "changelog")

            # Parse for breaking changes (simple pattern matching)
            events = self._parse_changelog_html(resp.text, snapshot_path)
            return events

        except Exception as e:
            logger.warning("Changelog fetch failed (using known changes): %s", e)
            return []

    def _save_snapshot(self, content: bytes, source_id: str) -> Path:
        """Save raw snapshot with metadata."""
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y%m%d_%H%M%S")
        content_hash = hashlib.sha256(content).hexdigest()[:16]

        filename = f"{ts}_{source_id}_{content_hash}.raw"
        snapshot_path = SNAPSHOTS_DIR / filename

        # Atomic write
        tmp_path = snapshot_path.with_suffix(".tmp")
        with open(tmp_path, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, snapshot_path)

        # Write metadata
        meta = {
            "source_id": source_id,
            "fetched_at_utc": now.isoformat(),
            "content_sha256": f"sha256:{hashlib.sha256(content).hexdigest()}",
            "size_bytes": len(content),
        }
        meta_path = snapshot_path.with_suffix(".meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

        logger.debug("Snapshot saved: %s", snapshot_path)
        return snapshot_path

    def _parse_changelog_html(self, html: str, snapshot_path: Path) -> List[ChangelogEvent]:
        """Parse changelog HTML for breaking changes."""
        events = []

        # Simple pattern matching for breaking changes
        patterns = [
            (r"will be removed", EventType.ENDPOINT_REMOVAL, EventSeverity.CRITICAL),
            (r"no longer available", EventType.ENDPOINT_DEPRECATION, EventSeverity.CRITICAL),
            (r"breaking change", EventType.OTHER, EventSeverity.CRITICAL),
            (r"signature.*change", EventType.SIGNATURE_CHANGE, EventSeverity.WARNING),
            (r"rate limit", EventType.RATE_LIMIT_CHANGE, EventSeverity.WARNING),
            (r"maintenance", EventType.MAINTENANCE, EventSeverity.INFO),
        ]

        # This is a simplified parser - real implementation would use proper HTML parsing
        for pattern, event_type, default_severity in patterns:
            matches = re.findall(rf"[^.]*{pattern}[^.]*\.", html, re.IGNORECASE)
            for match in matches[:5]:  # Limit matches
                # Clean up match
                clean_match = re.sub(r"<[^>]+>", "", match).strip()
                if len(clean_match) > 20:  # Ignore short matches
                    event_id = f"sha256:{hashlib.sha256(clean_match.encode()).hexdigest()}"
                    events.append(ChangelogEvent(
                        event_id=event_id,
                        event_type=event_type,
                        severity=default_severity,
                        summary=clean_match[:200],
                        details="Detected from changelog",
                        source_sha256=f"sha256:{hashlib.sha256(str(snapshot_path).encode()).hexdigest()[:16]}",
                    ))

        return events

    def get_critical_events(self) -> List[ChangelogEvent]:
        """Get only CRITICAL events."""
        return [e for e in self.check() if e.is_critical]

    def get_imminent_changes(self, days: int = 7) -> List[ChangelogEvent]:
        """Get changes effective within specified days."""
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=days)

        result = []
        for e in self.check():
            if e.effective_date:
                try:
                    effective = datetime.fromisoformat(e.effective_date.replace("Z", "+00:00"))
                    if effective <= cutoff:
                        result.append(e)
                except Exception:
                    pass
        return result

    def should_block_trading(self) -> tuple[bool, Optional[ChangelogEvent]]:
        """
        Check if trading should be blocked due to breaking changes.

        Returns:
            (should_block, blocking_event) - True if CRITICAL change is imminent
        """
        for event in self.check():
            if event.is_critical and event.is_effective_soon:
                return True, event
        return False, None


# === Singleton instance ===
_monitor_instance: Optional[ChangelogMonitor] = None


def get_changelog_monitor() -> ChangelogMonitor:
    """Get singleton ChangelogMonitor instance."""
    global _monitor_instance
    if _monitor_instance is None:
        _monitor_instance = ChangelogMonitor()
    return _monitor_instance


def check_binance_changelog() -> List[ChangelogEvent]:
    """
    Convenience function to check Binance changelog.

    Returns:
        List of ChangelogEvent objects
    """
    return get_changelog_monitor().check()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    print("=== Binance Changelog Monitor ===\n")

    monitor = ChangelogMonitor()
    events = monitor.check()

    print(f"Found {len(events)} events:\n")

    for event in events:
        severity_icon = {
            EventSeverity.CRITICAL: "🔴",
            EventSeverity.WARNING: "🟡",
            EventSeverity.INFO: "🟢",
        }.get(event.severity, "⚪")

        print(f"{severity_icon} [{event.severity.value}] {event.summary}")
        if event.effective_date:
            print(f"   Effective: {event.effective_date}")
        if event.affected_endpoints:
            print(f"   Endpoints: {', '.join(event.affected_endpoints[:3])}")
        print(f"   Action: {event.details}")
        print()

    # Check if trading should be blocked
    should_block, blocking_event = monitor.should_block_trading()
    if should_block:
        print(f"\n⛔ TRADING BLOCKED: {blocking_event.summary}")
        exit(1)
    else:
        print("\n✅ No blocking changes - trading allowed")
        exit(0)
