# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T12:30:00Z
# Purpose: Egress AllowList SSoT - load, validate, match (fail-closed)
# === END SIGNATURE ===
"""
Net Policy Module - AllowList enforcement (stdlib-only, fail-closed)

SSoT: AllowList.txt in repo root
Format: HOST-ONLY lines (no schemes, ports, paths, wildcards)

Fail-closed behavior:
- Missing AllowList.txt -> FatalPolicyError
- Invalid line format -> PolicyValidationError
- Host not in list -> is_allowed() returns False
"""

import re
from pathlib import Path
from typing import Set, Optional


class FatalPolicyError(Exception):
    """Raised when policy cannot be loaded - MUST stop execution."""
    pass


class PolicyValidationError(Exception):
    """Raised when AllowList.txt contains invalid entries."""
    pass


# Regex for valid hostname (RFC 1123 compliant, simplified)
# Allows: lowercase letters, digits, hyphens, dots
# Disallows: schemes (://), ports (:), paths (/), wildcards (*),
#            query (?), fragment (#), userinfo (@), whitespace
_VALID_HOST_PATTERN = re.compile(
    r'^[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)*$'
)

# Characters that MUST NOT appear in a host entry
_FORBIDDEN_CHARS = frozenset('/:*?#@\\')


class AllowList:
    """
    Immutable set of allowed hosts for egress.

    Thread-safe for reads (frozenset internally).
    """

    __slots__ = ('_hosts', '_source_path', '_load_time_utc')

    def __init__(self, hosts: Set[str], source_path: str, load_time_utc: str):
        self._hosts: frozenset = frozenset(hosts)
        self._source_path: str = source_path
        self._load_time_utc: str = load_time_utc

    def is_allowed(self, host: str) -> bool:
        """
        Check if host is in allowlist.

        Args:
            host: Hostname to check (will be normalized to lowercase)

        Returns:
            True if allowed, False if denied
        """
        if not host:
            return False
        normalized = _normalize_host(host)
        return normalized in self._hosts

    @property
    def hosts(self) -> frozenset:
        """Return immutable copy of allowed hosts."""
        return self._hosts

    @property
    def count(self) -> int:
        """Number of hosts in allowlist."""
        return len(self._hosts)

    @property
    def source_path(self) -> str:
        """Path to AllowList.txt that was loaded."""
        return self._source_path

    def __repr__(self) -> str:
        return f"AllowList(count={self.count}, source={self._source_path})"


def _normalize_host(host: str) -> str:
    """
    Normalize hostname to canonical form.

    - Lowercase
    - Strip trailing dot (DNS root)
    - Strip leading/trailing whitespace
    """
    result = host.strip().lower()
    if result.endswith('.'):
        result = result[:-1]
    return result


def validate_host(host: str) -> str:
    """
    Validate and normalize a single host entry.

    Args:
        host: Raw host string from AllowList.txt

    Returns:
        Normalized hostname

    Raises:
        PolicyValidationError: If host format is invalid
    """
    if not host or not host.strip():
        raise PolicyValidationError("Empty host entry")

    normalized = _normalize_host(host)

    # Check for scheme prefix FIRST (http://, https://, etc.)
    if '://' in host:
        raise PolicyValidationError(
            f"Host must not contain scheme (://): {host!r}"
        )

    # Check for port (colon without scheme)
    if ':' in normalized:
        raise PolicyValidationError(
            f"Host must not contain port: {host!r}"
        )

    # Check for other forbidden characters
    for char in _FORBIDDEN_CHARS:
        if char == ':':
            continue  # Already checked above with specific message
        if char in normalized:
            raise PolicyValidationError(
                f"Invalid character '{char}' in host: {host!r}"
            )

    # Check hostname pattern
    if not _VALID_HOST_PATTERN.match(normalized):
        raise PolicyValidationError(
            f"Invalid hostname format: {host!r}"
        )

    # Sanity check length
    if len(normalized) > 253:
        raise PolicyValidationError(
            f"Hostname too long (>253 chars): {host!r}"
        )

    return normalized


def load_allowlist(path: Optional[Path] = None) -> AllowList:
    """
    Load and validate AllowList.txt (fail-closed).

    Args:
        path: Path to AllowList.txt. If None, uses repo root default.

    Returns:
        AllowList instance with validated hosts

    Raises:
        FatalPolicyError: If file missing, unreadable, or empty
        PolicyValidationError: If any line is invalid
    """
    from datetime import datetime, timezone

    if path is None:
        # Default: repo root / AllowList.txt
        # Detect repo root by walking up from this file
        current = Path(__file__).resolve()
        repo_root = None
        for parent in [current] + list(current.parents):
            if (parent / "AllowList.txt").exists():
                repo_root = parent
                break
            if (parent / ".git").exists():
                repo_root = parent
                break

        if repo_root is None:
            raise FatalPolicyError(
                "Cannot locate repo root (no .git or AllowList.txt found)"
            )
        path = repo_root / "AllowList.txt"

    path = Path(path)

    # Fail-closed: file must exist
    if not path.exists():
        raise FatalPolicyError(
            f"AllowList.txt not found: {path} (fail-closed)"
        )

    if not path.is_file():
        raise FatalPolicyError(
            f"AllowList.txt is not a file: {path}"
        )

    # Read file
    try:
        content = path.read_text(encoding='utf-8')
    except Exception as e:
        raise FatalPolicyError(
            f"Cannot read AllowList.txt: {path}: {e}"
        )

    # Parse and validate
    hosts: Set[str] = set()
    errors: list = []

    for line_num, line in enumerate(content.splitlines(), start=1):
        line = line.strip()

        # Skip empty lines and comments
        if not line or line.startswith('#'):
            continue

        try:
            normalized = validate_host(line)
            hosts.add(normalized)
        except PolicyValidationError as e:
            errors.append(f"Line {line_num}: {e}")

    # Fail-closed: any validation errors -> raise
    if errors:
        raise PolicyValidationError(
            f"AllowList.txt validation failed:\n" + "\n".join(errors)
        )

    # Fail-closed: empty allowlist is suspicious
    if not hosts:
        raise FatalPolicyError(
            f"AllowList.txt is empty (no valid hosts): {path}"
        )

    load_time = datetime.now(timezone.utc).isoformat()

    return AllowList(
        hosts=hosts,
        source_path=str(path),
        load_time_utc=load_time
    )


# Module-level singleton (lazy-loaded)
_cached_allowlist: Optional[AllowList] = None


def get_allowlist() -> AllowList:
    """
    Get cached AllowList (singleton pattern).

    Loads on first call, then returns cached instance.

    Returns:
        AllowList instance

    Raises:
        FatalPolicyError: If loading fails
    """
    global _cached_allowlist
    if _cached_allowlist is None:
        _cached_allowlist = load_allowlist()
    return _cached_allowlist


def reload_allowlist() -> AllowList:
    """
    Force reload of AllowList (clears cache).

    Returns:
        Fresh AllowList instance
    """
    global _cached_allowlist
    _cached_allowlist = load_allowlist()
    return _cached_allowlist
