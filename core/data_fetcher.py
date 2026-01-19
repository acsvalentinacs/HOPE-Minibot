"""
Data Fetcher - Allowlist-based HTTP client with fail-closed semantics.

All external data access MUST go through this module.
Unauthorized hosts are rejected at protocol level.

Usage:
    from core.data_fetcher import fetch_bytes, fetch_json

    result = await fetch_bytes("https://api.binance.com/api/v3/ticker/24hr")
    data = await fetch_json("https://api.coingecko.com/api/v3/ping")
"""
from __future__ import annotations

import asyncio
import ssl
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import aiohttp
import certifi

ALLOWED_HOSTS = frozenset({
    # Binance API
    "api.binance.com",
    "data.binance.vision",
    "testnet.binance.vision",
    "developers.binance.com",
    "www.binance.com",
    # Crypto data
    "api.coingecko.com",
    "pro-api.coinmarketcap.com",
    # News RSS
    "www.coindesk.com",
    "cointelegraph.com",
    "decrypt.co",
    "www.theblock.co",
    "bitcoinmagazine.com",
    # Dependencies
    "pypi.org",
    "files.pythonhosted.org",
    "github.com",
    "api.github.com",
    "raw.githubusercontent.com",
    # AI/ML
    "www.anthropic.com",
})

DEFAULT_TIMEOUT_SEC = 15.0
MAX_RESPONSE_SIZE = 10 * 1024 * 1024  # 10MB


class FetchError(Exception):
    """Fetch operation failed (fail-closed)."""
    pass


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    body: bytes
    content_type: str
    headers: Dict[str, str]


def validate_url(url: str) -> None:
    """
    Validate URL against allowlist. Fail-closed on any violation.

    Raises FetchError if URL is not allowed.
    """
    parsed = urlparse(url)

    if parsed.scheme != "https":
        raise FetchError(f"FAIL-CLOSED: only https allowed, got {parsed.scheme}")

    if not parsed.netloc:
        raise FetchError("FAIL-CLOSED: empty host")

    host = parsed.netloc.lower()
    if ":" in host:
        host = host.split(":")[0]

    if host not in ALLOWED_HOSTS:
        raise FetchError(f"FAIL-CLOSED: host not in allowlist: {host}")


def _create_ssl_context() -> ssl.SSLContext:
    """Create SSL context with certifi CA bundle."""
    ctx = ssl.create_default_context(cafile=certifi.where())
    return ctx


async def fetch_bytes(
    url: str,
    *,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    headers: Optional[Dict[str, str]] = None,
    max_size: int = MAX_RESPONSE_SIZE,
) -> FetchResult:
    """
    Fetch URL and return raw bytes. Fail-closed on any error.

    Args:
        url: Target URL (must be https and in allowlist)
        timeout_sec: Request timeout
        headers: Optional HTTP headers
        max_size: Maximum response size in bytes

    Returns:
        FetchResult with status, body, content_type

    Raises:
        FetchError: On any failure (network, timeout, status, size)
    """
    validate_url(url)

    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    ssl_ctx = _create_ssl_context()

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers, ssl=ssl_ctx) as resp:
                if resp.status != 200:
                    raise FetchError(f"FAIL-CLOSED: {url} returned status={resp.status}")

                content_length = resp.headers.get("Content-Length")
                if content_length and int(content_length) > max_size:
                    raise FetchError(f"FAIL-CLOSED: response too large ({content_length} > {max_size})")

                body = await resp.read()

                if len(body) > max_size:
                    raise FetchError(f"FAIL-CLOSED: response too large ({len(body)} > {max_size})")

                return FetchResult(
                    url=url,
                    status=resp.status,
                    body=body,
                    content_type=resp.headers.get("Content-Type", ""),
                    headers=dict(resp.headers),
                )

    except aiohttp.ClientError as e:
        raise FetchError(f"FAIL-CLOSED: network error: {e}") from e
    except asyncio.TimeoutError:
        raise FetchError(f"FAIL-CLOSED: timeout after {timeout_sec}s") from None


async def fetch_json(
    url: str,
    *,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Fetch URL and parse as JSON. Fail-closed on any error.

    Returns:
        Parsed JSON as dict

    Raises:
        FetchError: On fetch or parse failure
    """
    import json

    result = await fetch_bytes(url, timeout_sec=timeout_sec, headers=headers)

    try:
        return json.loads(result.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise FetchError(f"FAIL-CLOSED: JSON parse error: {e}") from e


async def fetch_text(
    url: str,
    *,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    headers: Optional[Dict[str, str]] = None,
    encoding: str = "utf-8",
) -> str:
    """
    Fetch URL and decode as text.

    Returns:
        Response body as string

    Raises:
        FetchError: On fetch or decode failure
    """
    result = await fetch_bytes(url, timeout_sec=timeout_sec, headers=headers)

    try:
        return result.body.decode(encoding)
    except UnicodeDecodeError as e:
        raise FetchError(f"FAIL-CLOSED: decode error: {e}") from e


def fetch_sync(url: str, **kwargs) -> FetchResult:
    """Synchronous wrapper for fetch_bytes."""
    return asyncio.get_event_loop().run_until_complete(fetch_bytes(url, **kwargs))


def fetch_json_sync(url: str, **kwargs) -> Dict[str, Any]:
    """Synchronous wrapper for fetch_json."""
    return asyncio.get_event_loop().run_until_complete(fetch_json(url, **kwargs))
