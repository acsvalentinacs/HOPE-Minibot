# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T14:00:00Z
# Purpose: News Spider v1.0 - stdlib-only RSS/announcement collector
# === END SIGNATURE ===
"""
News Spider Module

Public exports for news collection using egress policy enforcement.
stdlib-only: no feedparser, requests, or external dependencies.
"""

from core.spider.sources import (
    SourceConfig,
    SourceType,
    load_sources,
    get_enabled_sources,
)
from core.spider.parser import (
    RSSItem,
    parse_rss_xml,
    parse_binance_announcements,
)
from core.spider.collector import (
    NewsCollector,
    CollectorMode,
    CollectorResult,
)
from core.spider.dedup import (
    DedupStore,
    is_duplicate,
)

__all__ = [
    # Sources
    "SourceConfig",
    "SourceType",
    "load_sources",
    "get_enabled_sources",
    # Parser
    "RSSItem",
    "parse_rss_xml",
    "parse_binance_announcements",
    # Collector
    "NewsCollector",
    "CollectorMode",
    "CollectorResult",
    # Dedup
    "DedupStore",
    "is_duplicate",
]
