# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T14:00:00Z
# Modified by: Claude (opus-4)
# Modified at (UTC): 2026-01-25T18:00:00Z
# Purpose: News Spider v1.2 - stdlib-only, fail-closed, atomic I/O
# === END SIGNATURE ===
"""
News Spider Module v1.2

Public exports for news collection using egress policy enforcement.
stdlib-only: no feedparser, requests, or external dependencies.

IMPORTANT: This module ONLY exports symbols. NO SIDE-EFFECTS on import.
Use `python -m core.spider` as the canonical entrypoint.

v1.2 Changes:
- Atomic I/O with sha256 JSONL format
- Policy engine (strict/lenient)
- SSoT evidence (cmdline/allowlist sha256)
- No RuntimeWarning (proper __main__.py)
"""

# NOTE: Lazy imports to avoid side-effects
# Actual imports happen on first use

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
    "run_collection",
    # Dedup
    "DedupStore",
    "is_duplicate",
    # Health
    "HealthTracker",
    "HealthStatus",
    "ErrorCategory",
    "categorize_error",
    # Policy
    "PolicyMode",
    "PolicyConfig",
    "PolicyVerdict",
    "evaluate_policy",
    # Telegram Bridge
    "SpiderTelegramBridge",
    "PublishResult",
]


def __getattr__(name):
    """Lazy import to avoid side-effects on package import."""
    if name in ("SourceConfig", "SourceType", "load_sources", "get_enabled_sources"):
        from core.spider.sources import SourceConfig, SourceType, load_sources, get_enabled_sources
        return locals()[name]

    if name in ("RSSItem", "parse_rss_xml", "parse_binance_announcements"):
        from core.spider.parser import RSSItem, parse_rss_xml, parse_binance_announcements
        return locals()[name]

    if name in ("NewsCollector", "CollectorMode", "CollectorResult", "run_collection"):
        from core.spider.collector import NewsCollector, CollectorMode, CollectorResult, run_collection
        return locals()[name]

    if name in ("DedupStore", "is_duplicate"):
        from core.spider.dedup import DedupStore, is_duplicate
        return locals()[name]

    if name in ("HealthTracker", "HealthStatus"):
        from core.spider.health import HealthTracker, HealthStatus
        return locals()[name]

    if name == "ErrorCategory":
        from core.spider.health import ErrorCategory
        return ErrorCategory

    if name == "categorize_error":
        from core.spider.health import categorize_error
        return categorize_error

    if name in ("PolicyMode", "PolicyConfig", "PolicyVerdict", "evaluate_policy"):
        from core.spider.policy import PolicyMode, PolicyConfig, PolicyVerdict, evaluate_policy
        return locals()[name]

    if name in ("SpiderTelegramBridge", "PublishResult"):
        from core.spider.telegram_bridge import SpiderTelegramBridge, PublishResult
        return locals()[name]

    raise AttributeError(f"module 'core.spider' has no attribute '{name}'")
