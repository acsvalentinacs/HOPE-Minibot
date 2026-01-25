# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T14:00:00Z
# Purpose: News source configuration loader (stdlib-only)
# === END SIGNATURE ===
"""
News Source Configuration

Loads and validates source definitions from sources_registry.json.
Validates hosts against AllowList.txt before enabling sources.
"""

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse

from core.net.net_policy import get_allowlist, FatalPolicyError


class SourceType(Enum):
    """Type of news source."""
    RSS = "rss"
    BINANCE_ANN = "binance_announcements"
    JSON_API = "json_api"


@dataclass(frozen=True)
class SourceConfig:
    """
    Immutable source configuration.

    Attributes:
        id: Unique source identifier (e.g., "coindesk_rss")
        name: Human-readable name
        source_type: Type of source (RSS, API, etc.)
        url: Full URL to fetch
        host: Extracted host for allowlist checking (no scheme/port)
        enabled: Whether source is enabled in config
        priority: Fetch priority (1=highest)
        category: News category (market, regulation, etc.)
        language: Content language (en, ru, etc.)
        ttl_minutes: Cache TTL before refetch
    """
    id: str
    name: str
    source_type: SourceType
    url: str
    host: str
    enabled: bool = True
    priority: int = 5
    category: str = "general"
    language: str = "en"
    ttl_minutes: int = 15


class SourceLoadError(Exception):
    """Error loading source configuration."""
    pass


class SourceValidationError(Exception):
    """Source configuration validation error."""
    pass


def _extract_host(url: str) -> str:
    """
    Extract host from URL for AllowList matching.

    Returns lowercase hostname without scheme, port, or path.

    Raises:
        SourceValidationError: If URL is malformed
    """
    if not url:
        raise SourceValidationError("URL cannot be empty")

    try:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            raise SourceValidationError(f"Cannot extract host from URL: {url}")
        return host.lower()
    except Exception as e:
        raise SourceValidationError(f"Invalid URL '{url}': {e}")


def _validate_source_dict(src: Dict[str, Any], index: int) -> SourceConfig:
    """
    Validate and convert source dict to SourceConfig.

    Args:
        src: Raw source dictionary from JSON
        index: Source index for error messages

    Returns:
        Validated SourceConfig

    Raises:
        SourceValidationError: If validation fails
    """
    # Required fields
    required = ["id", "name", "type", "url"]
    for field_name in required:
        if field_name not in src:
            raise SourceValidationError(
                f"Source #{index}: missing required field '{field_name}'"
            )

    # Validate type
    type_str = src["type"]
    try:
        source_type = SourceType(type_str)
    except ValueError:
        valid_types = [t.value for t in SourceType]
        raise SourceValidationError(
            f"Source #{index} ({src['id']}): invalid type '{type_str}', "
            f"must be one of: {valid_types}"
        )

    # Extract and validate host
    url = src["url"]
    host = _extract_host(url)

    # Validate host format (no scheme, port, path)
    if "://" in host or ":" in host or "/" in host:
        raise SourceValidationError(
            f"Source #{index} ({src['id']}): host contains invalid chars: {host}"
        )

    return SourceConfig(
        id=src["id"],
        name=src["name"],
        source_type=source_type,
        url=url,
        host=host,
        enabled=src.get("enabled", True),
        priority=src.get("priority", 5),
        category=src.get("category", "general"),
        language=src.get("language", "en"),
        ttl_minutes=src.get("ttl_minutes", 15),
    )


def load_sources(
    registry_path: Optional[Path] = None,
    project_root: Optional[Path] = None,
) -> List[SourceConfig]:
    """
    Load all sources from registry file.

    Args:
        registry_path: Path to sources_registry.json (default: config/sources_registry.json)
        project_root: Project root for resolving paths

    Returns:
        List of SourceConfig objects

    Raises:
        SourceLoadError: If file cannot be loaded
        SourceValidationError: If validation fails
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent.parent

    if registry_path is None:
        registry_path = project_root / "config" / "sources_registry.json"

    if not registry_path.exists():
        raise SourceLoadError(f"Sources registry not found: {registry_path}")

    try:
        content = registry_path.read_text(encoding="utf-8")
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise SourceLoadError(f"Invalid JSON in {registry_path}: {e}")
    except Exception as e:
        raise SourceLoadError(f"Cannot read {registry_path}: {e}")

    # Expect {"sources": [...]} structure
    if "sources" not in data:
        raise SourceValidationError(
            f"Registry must have 'sources' array at top level"
        )

    sources = []
    for i, src_dict in enumerate(data["sources"], start=1):
        config = _validate_source_dict(src_dict, i)
        sources.append(config)

    if not sources:
        raise SourceValidationError("No sources defined in registry")

    return sources


def get_enabled_sources(
    sources: Optional[List[SourceConfig]] = None,
    strict_mode: bool = True,
) -> List[SourceConfig]:
    """
    Filter sources to enabled ones with hosts in AllowList.

    Args:
        sources: Source list (loads from registry if None)
        strict_mode: If True (STRICT), raises error for enabled sources
                    with hosts not in AllowList. If False (LENIENT),
                    skips them with warning.

    Returns:
        List of enabled sources with allowed hosts

    Raises:
        FatalPolicyError: In strict mode, if any enabled source host
                         is not in AllowList
    """
    if sources is None:
        sources = load_sources()

    allowlist = get_allowlist()
    enabled = []
    denied_sources = []

    for src in sources:
        if not src.enabled:
            continue

        if allowlist.is_allowed(src.host):
            enabled.append(src)
        else:
            denied_sources.append(src)

    # Handle denied sources based on mode
    if denied_sources:
        denied_info = ", ".join(
            f"{s.id}({s.host})" for s in denied_sources
        )

        if strict_mode:
            raise FatalPolicyError(
                f"STRICT MODE: {len(denied_sources)} enabled source(s) have "
                f"hosts not in AllowList: {denied_info}. "
                f"Either add hosts to AllowList.txt or disable sources."
            )
        else:
            # LENIENT mode: warn and skip
            import sys
            print(
                f"[WARN] LENIENT MODE: Skipping {len(denied_sources)} source(s) "
                f"with hosts not in AllowList: {denied_info}",
                file=sys.stderr
            )

    return enabled
