# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T10:15:00Z
# Purpose: Contracts package - exchange data caching with sha256 envelopes
# === END SIGNATURE ===
"""
Contracts package.

Provides cached access to exchange contracts (exchangeInfo, etc.)
with integrity verification via sha256 envelopes.
"""

from .exchange_info_cache import ExchangeInfoCache

__all__ = ["ExchangeInfoCache"]
