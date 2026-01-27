# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T01:00:00Z
# Purpose: Intel module - external data collection with provenance
# === END SIGNATURE ===
"""
HOPE Intel Module.

Provides external data collection with provenance tracking:
- changelog_monitor: Binance API contract changes detection
- snapshot: Raw data persistence with SHA256

All external data must have:
- Raw snapshot saved before parsing
- SHA256 hash for provenance
- Timestamp of fetch
"""

from .changelog_monitor import (
    ChangelogMonitor,
    ChangelogEvent,
    ContractBreakingChange,
    check_binance_changelog,
)

__all__ = [
    "ChangelogMonitor",
    "ChangelogEvent",
    "ContractBreakingChange",
    "check_binance_changelog",
]
