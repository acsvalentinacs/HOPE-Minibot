# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T10:15:00Z
# Purpose: ExchangeInfo cache with sha256 contract envelope and TTL
# Security: Fail-closed, integrity verified, atomic writes
# === END SIGNATURE ===
"""
ExchangeInfo Cache - Binance Contract Data with Integrity.

Features:
- SHA256 envelope for integrity verification
- TTL-based refresh (1 hour default)
- Atomic writes (temp → fsync → replace)
- Fail-closed: corrupted/missing cache = RuntimeError

Usage:
    cache = ExchangeInfoCache(client)
    filters = cache.get_filters("BTCUSDT")
    # {'tick_size': 0.01, 'step_size': 0.00001, 'min_notional': 10.0}
"""
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("contracts.exchange_info")

# SSoT paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONTRACTS_DIR = BASE_DIR / "state" / "contracts"
CACHE_FILE = CONTRACTS_DIR / "binance_exchange_info.json"

# Cache TTL
CACHE_TTL_SECONDS = 3600  # 1 hour


def _atomic_write(path: Path, content: str) -> None:
    """Atomic write: temp → fsync → replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class ExchangeInfoCache:
    """
    Cache for Binance exchangeInfo with SHA256 integrity.

    Fail-closed:
    - No cache + no client = RuntimeError
    - Expired cache + no client = RuntimeError
    - Corrupted cache = RuntimeError
    - Symbol not found = RuntimeError
    """

    def __init__(self, client=None, ttl_seconds: int = CACHE_TTL_SECONDS):
        """
        Initialize ExchangeInfo cache.

        Args:
            client: Exchange client with get_exchange_info() method
            ttl_seconds: Cache TTL in seconds (default 1 hour)
        """
        self._client = client
        self._ttl = ttl_seconds
        self._cache: Optional[Dict] = None

        CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)

    def get_filters(self, symbol: str) -> Dict[str, Any]:
        """
        Get trading filters for symbol.

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")

        Returns:
            Dict with keys:
            - tick_size: Price tick size
            - min_price: Minimum price
            - max_price: Maximum price
            - step_size: Quantity step size
            - min_qty: Minimum quantity
            - max_qty: Maximum quantity
            - min_notional: Minimum order value in quote asset

        Raises:
            RuntimeError: If cache unavailable or symbol not found
        """
        cache = self._get_valid_cache()

        # Find symbol
        for sym in cache.get("symbols", []):
            if sym["symbol"] == symbol:
                return self._parse_filters(sym.get("filters", []))

        raise RuntimeError(f"Symbol {symbol} not found in exchangeInfo")

    def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get full symbol info.

        Args:
            symbol: Trading pair

        Returns:
            Full symbol dict from exchangeInfo

        Raises:
            RuntimeError: If symbol not found
        """
        cache = self._get_valid_cache()

        for sym in cache.get("symbols", []):
            if sym["symbol"] == symbol:
                return sym

        raise RuntimeError(f"Symbol {symbol} not found in exchangeInfo")

    def get_all_symbols(self) -> list:
        """Get list of all trading symbols."""
        cache = self._get_valid_cache()
        return [s["symbol"] for s in cache.get("symbols", [])]

    def get_cache_info(self) -> Dict[str, Any]:
        """Get cache metadata."""
        cache = self._get_valid_cache()
        return {
            "symbol_count": len(cache.get("symbols", [])),
            "fetched_at": cache.get("fetched_at"),
            "timestamp": cache.get("timestamp"),
            "age_seconds": time.time() - cache.get("timestamp", 0),
            "ttl_seconds": self._ttl,
        }

    def refresh(self) -> Dict[str, Any]:
        """
        Force refresh cache from exchange.

        Returns:
            Cache metadata

        Raises:
            RuntimeError: If client not available or fetch fails
        """
        if self._client is None:
            raise RuntimeError("Cannot refresh: no exchange client")

        self._cache = self._refresh_cache()
        return self.get_cache_info()

    def _get_valid_cache(self) -> Dict:
        """
        Get valid cache, refreshing if needed.

        Fail-closed: returns cache or raises RuntimeError.
        """
        # Try memory cache first
        if self._cache is not None:
            cache_age = time.time() - self._cache.get("timestamp", 0)
            if cache_age <= self._ttl:
                return self._cache

        # Try disk cache
        disk_cache = self._load_cache()
        if disk_cache is not None:
            cache_age = time.time() - disk_cache.get("timestamp", 0)
            if cache_age <= self._ttl:
                self._cache = disk_cache
                return disk_cache

        # Cache expired or missing - need to refresh
        if self._client is None:
            if disk_cache is not None:
                raise RuntimeError(
                    f"ExchangeInfo cache expired ({cache_age:.0f}s old, TTL={self._ttl}s) "
                    "and no client to refresh"
                )
            raise RuntimeError(
                "No exchangeInfo cache and no client to fetch"
            )

        # Refresh from exchange
        self._cache = self._refresh_cache()
        return self._cache

    def _parse_filters(self, filters: list) -> Dict[str, Any]:
        """Parse exchange filters to convenient format."""
        result: Dict[str, Any] = {
            "tick_size": 0.0,
            "min_price": 0.0,
            "max_price": 0.0,
            "step_size": 0.0,
            "min_qty": 0.0,
            "max_qty": 0.0,
            "min_notional": 0.0,
        }

        for f in filters:
            filter_type = f.get("filterType", "")

            if filter_type == "PRICE_FILTER":
                result["tick_size"] = float(f.get("tickSize", 0))
                result["min_price"] = float(f.get("minPrice", 0))
                result["max_price"] = float(f.get("maxPrice", 0))

            elif filter_type == "LOT_SIZE":
                result["step_size"] = float(f.get("stepSize", 0))
                result["min_qty"] = float(f.get("minQty", 0))
                result["max_qty"] = float(f.get("maxQty", 0))

            elif filter_type == "MIN_NOTIONAL":
                # Handle both old and new API formats
                result["min_notional"] = float(
                    f.get("minNotional", 0) or f.get("notional", 0)
                )

            elif filter_type == "NOTIONAL":
                # New format
                result["min_notional"] = float(f.get("minNotional", 0))

        return result

    def _load_cache(self) -> Optional[Dict]:
        """Load and verify cache from disk."""
        if not CACHE_FILE.exists():
            logger.debug("No cache file at %s", CACHE_FILE)
            return None

        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                envelope = json.load(f)

            # Verify SHA256
            stored_hash = envelope.get("sha256")
            content = envelope.get("content", {})
            content_json = json.dumps(content, sort_keys=True, ensure_ascii=False)
            computed_hash = hashlib.sha256(content_json.encode("utf-8")).hexdigest()

            if stored_hash != computed_hash:
                logger.error(
                    "Cache integrity check FAILED: stored=%s, computed=%s",
                    stored_hash[:16], computed_hash[:16],
                )
                raise RuntimeError(
                    f"ExchangeInfo cache corrupted: hash mismatch "
                    f"(stored={stored_hash[:16]}..., computed={computed_hash[:16]}...)"
                )

            logger.debug(
                "Cache loaded: %d symbols, age=%.0fs",
                len(content.get("symbols", [])),
                time.time() - content.get("timestamp", 0),
            )

            return content

        except json.JSONDecodeError as e:
            logger.error("Cache JSON decode error: %s", e)
            raise RuntimeError(f"ExchangeInfo cache corrupted: invalid JSON") from e

    def _refresh_cache(self) -> Dict:
        """Fetch exchangeInfo from exchange and save to disk."""
        logger.info("Refreshing exchangeInfo from exchange...")

        try:
            info = self._client.get_exchange_info()

            if info is None:
                raise RuntimeError("Exchange returned None for exchangeInfo")

            content = {
                "symbols": info.get("symbols", []),
                "timestamp": time.time(),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "server_time": info.get("serverTime"),
            }

            # Compute SHA256
            content_json = json.dumps(content, sort_keys=True, ensure_ascii=False)
            sha256_hash = hashlib.sha256(content_json.encode("utf-8")).hexdigest()

            envelope = {
                "sha256": sha256_hash,
                "content": content,
            }

            # Atomic write
            envelope_json = json.dumps(envelope, indent=2, ensure_ascii=False)
            _atomic_write(CACHE_FILE, envelope_json)

            logger.info(
                "ExchangeInfo cached: %d symbols, sha256=%s",
                len(content["symbols"]), sha256_hash[:16],
            )

            return content

        except Exception as e:
            logger.error("Failed to refresh exchangeInfo: %s", e)
            raise RuntimeError(f"Failed to fetch exchangeInfo: {e}") from e


# Convenience function
def get_symbol_filters(symbol: str, client=None) -> Dict[str, Any]:
    """
    Get trading filters for symbol (convenience function).

    Args:
        symbol: Trading pair
        client: Exchange client (optional, uses cached data if available)

    Returns:
        Filter dict

    Raises:
        RuntimeError: If cache unavailable and no client
    """
    cache = ExchangeInfoCache(client)
    return cache.get_filters(symbol)
