# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 16:30:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-29 11:35:00 UTC
# Purpose: Centralized configuration with ALLOWED_DOMAINS whitelist
# Security: Fail-closed defaults, explicit contracts
# === END SIGNATURE ===
"""
AI-Gateway Configuration — Единый источник истины для конфигурации.

All network, timing, and threshold constants in one place.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Final


# ============================================================================
# NETWORK WHITELIST — Единственный источник истины
# ============================================================================

ALLOWED_DOMAINS: Final[frozenset[str]] = frozenset({
    # === AI APIs (КРИТИЧНО) ===
    "api.anthropic.com",          # Claude API для Sentiment, Doctor

    # === BINANCE (Market Data) ===
    "api.binance.com",            # REST API для Regime, Anomaly
    "stream.binance.com",         # WebSocket real-time data
    "testnet.binance.vision",     # Testnet trading
    "data.binance.vision",        # Historical data archives

    # === RSS FEEDS (News) ===
    # Validated 2026-01-29: 4/4 working
    "www.coindesk.com",
    "coindesk.com",
    "cointelegraph.com",
    "www.theblock.co",
    "theblock.co",
    "decrypt.co",
    # REMOVED: bitcoinmagazine.com - HTTP 403 Forbidden

    # === CRYPTO DATA ===
    "api.coingecko.com",

    # === INFRASTRUCTURE ===
    "pypi.org",
    "files.pythonhosted.org",
    "github.com",
    "api.github.com",
    "raw.githubusercontent.com",
    "checkip.amazonaws.com",

    # === SENTIMENT ===
    "api.alternative.me",         # Fear & Greed Index
})


def is_domain_allowed(domain: str) -> bool:
    """
    Check if domain is in whitelist.

    FAIL-CLOSED: Unknown domain = rejected.
    """
    domain = domain.lower().strip()

    # Exact match
    if domain in ALLOWED_DOMAINS:
        return True

    # Subdomain match (e.g., api.binance.com for binance.com)
    for allowed in ALLOWED_DOMAINS:
        if domain.endswith("." + allowed):
            return True

    return False


# ============================================================================
# PATHS
# ============================================================================

STATE_DIR: Final[Path] = Path("state/ai")
LOGS_DIR: Final[Path] = Path("logs")


# ============================================================================
# MODULE INTERVALS (seconds)
# ============================================================================

INTERVALS: Final[dict[str, int]] = {
    "sentiment": 900,    # 15 min (RSS feeds don't update faster)
    "regime": 300,       # 5 min (market changes, but not instantly)
    "doctor": 0,         # on-demand only (triggered by trades)
    "anomaly": 60,       # 1 min (need fast reaction)
}


# ============================================================================
# THRESHOLDS
# ============================================================================

# Status Manager
HEARTBEAT_WARNING_SECONDS: Final[int] = 300     # 5 minutes
HEARTBEAT_ERROR_SECONDS: Final[int] = 900       # 15 minutes
SUCCESS_RATE_WARNING: Final[float] = 0.90       # 90%
SUCCESS_RATE_ERROR: Final[float] = 0.70         # 70%
MAX_CONSECUTIVE_ERRORS: Final[int] = 5          # Auto-stop after this

# Regime Detector
TREND_THRESHOLD: Final[float] = 0.3             # |trend| > 0.3 = trending
VOLATILITY_HIGH_PERCENTILE: Final[int] = 80
VOLATILITY_LOW_PERCENTILE: Final[int] = 20

# Decay Detector (CUSUM)
CUSUM_BASELINE_RETURN: Final[float] = 0.002     # 0.2% expected
CUSUM_K_FACTOR: Final[float] = 0.001            # Allowable deviation
CUSUM_THRESHOLD: Final[float] = 0.05            # Signal threshold


# ============================================================================
# API CONFIGURATION
# ============================================================================

ANTHROPIC_API_URL: Final[str] = "https://api.anthropic.com/v1/messages"
ANTHROPIC_DEFAULT_MODEL: Final[str] = "claude-3-haiku-20240307"
ANTHROPIC_SONNET_MODEL: Final[str] = "claude-sonnet-4-20250514"
API_TIMEOUT_SECONDS: Final[float] = 30.0

BINANCE_KLINES_URL: Final[str] = "https://api.binance.com/api/v3/klines"
BINANCE_TIMEOUT_SECONDS: Final[float] = 10.0


# ============================================================================
# TELEGRAM
# ============================================================================

TELEGRAM_ADMIN_CHAT_ID: int = int(os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "0"))


# ============================================================================
# SOURCES MANAGER INTEGRATION
# ============================================================================

def get_active_sources(category: str = None) -> list[str]:
    """
    Получить активные источники данных.

    Args:
        category: Категория (binance, market_data, news, infrastructure)
                  None = все категории

    Returns:
        List of URLs
    """
    try:
        from scripts.sources_manager import SourcesManager
        manager = SourcesManager()
        return manager.get_source_urls(category)
    except ImportError:
        # Fallback to static list
        return []


def get_binance_endpoints() -> list[str]:
    """Получить активные Binance эндпоинты."""
    return get_active_sources("binance")


def get_news_feeds() -> list[str]:
    """Получить активные RSS feeds."""
    return get_active_sources("news")


# ============================================================================
# VALIDATION
# ============================================================================

def validate_config() -> list[str]:
    """
    Validate configuration.

    Returns:
        List of errors (empty = OK)
    """
    errors = []

    # Check API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        errors.append("ANTHROPIC_API_KEY not set - Sentiment/Doctor will not work")

    # Check Telegram admin
    if TELEGRAM_ADMIN_CHAT_ID == 0:
        errors.append("TELEGRAM_ADMIN_CHAT_ID not set - Telegram control disabled")

    return errors
