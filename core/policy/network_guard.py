# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 16:15:00 UTC
# === END SIGNATURE ===
"""
core/policy/network_guard.py - Network Allowlist Enforcement.

HOPE-LAW-001 / HOPE-RULE-001:
    Blocks all network connections BEFORE DNS resolution unless:
    - Host is in allowlist
    - Host is localhost/loopback

FAIL-CLOSED:
    - Allowlist missing -> All network blocked
    - Host not in list -> Connection blocked (before DNS)
    - Raw IP (non-loopback) -> Connection blocked
"""
from __future__ import annotations

import ipaddress
import socket
import sys
from pathlib import Path
from typing import Any, Optional, Set, Tuple

from core.policy.loader import Policy, PolicyError


class NetworkBlocked(RuntimeError):
    """
    Raised when network connection is blocked by policy.

    Connection is blocked BEFORE any network activity.
    """
    pass


def _load_hosts_from_allowlist(path: Path) -> Set[str]:
    """
    Load allowed hosts from allowlist file.

    FAIL-CLOSED: Returns empty set if file missing (all blocked).

    Args:
        path: Path to allowlist file

    Returns:
        Set of lowercase hostnames
    """
    if not path.exists():
        print(
            f"[CRITICAL] network_guard: AllowList not found: {path}",
            file=sys.__stderr__,
        )
        print(
            f"[CRITICAL] network_guard: ALL NETWORK CONNECTIONS BLOCKED",
            file=sys.__stderr__,
        )
        return set()

    hosts: Set[str] = set()

    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Validate: host-only
            if "://" in line or "/" in line or "*" in line:
                print(
                    f"[WARN] network_guard: Invalid allowlist entry (skipped): {line}",
                    file=sys.__stderr__,
                )
                continue

            hosts.add(line.lower())

    except Exception as e:
        print(
            f"[CRITICAL] network_guard: Failed to read allowlist: {e}",
            file=sys.__stderr__,
        )
        return set()

    return hosts


def _is_ip_literal(host: str) -> bool:
    """Check if host is an IP address literal."""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _is_loopback(host: str) -> bool:
    """Check if host is loopback (localhost or 127.x.x.x or ::1)."""
    if host.lower() in ("localhost", "127.0.0.1", "::1"):
        return True

    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback
    except ValueError:
        return False


# Store original functions
_orig_getaddrinfo: Optional[Any] = None
_orig_create_connection: Optional[Any] = None
_INSTALLED = False


def install_network_guards(policy: Policy, profile: str = "default") -> None:
    """
    Install network guards by patching socket functions.

    Patches:
        - socket.getaddrinfo (blocks DNS for non-allowed hosts)
        - socket.create_connection (blocks direct IP connections)

    Args:
        policy: Loaded policy configuration
        profile: Allowlist profile to use (default, core, spider, dev)
    """
    global _orig_getaddrinfo, _orig_create_connection, _INSTALLED

    if _INSTALLED:
        return  # Idempotent

    # Get allowlist path
    repo_root = Path(__file__).resolve().parents[2]
    allowlist_rel = policy.allowlist.get(profile, policy.allowlist.get("default", "AllowList.txt"))
    allowlist_path = (repo_root / allowlist_rel).resolve()

    # Load allowed hosts
    allowed_hosts = _load_hosts_from_allowlist(allowlist_path)

    # Always allow localhost
    allowed_hosts.add("localhost")
    allowed_hosts.add("127.0.0.1")
    allowed_hosts.add("::1")

    print(
        f"[INFO] network_guard: Loaded {len(allowed_hosts)} allowed hosts from {allowlist_path.name}",
        file=sys.__stderr__,
    )

    # Save originals
    _orig_getaddrinfo = socket.getaddrinfo
    _orig_create_connection = socket.create_connection

    def guarded_getaddrinfo(
        host: Any,
        port: Any,
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> Any:
        """Guarded getaddrinfo - blocks DNS for non-allowed hosts."""
        if host is None:
            raise NetworkBlocked("DNS blocked: host is None (fail-closed)")

        host_str = str(host).lower()

        # Allow loopback
        if _is_loopback(host_str):
            return _orig_getaddrinfo(host, port, family, type, proto, flags)

        # Block raw IP (non-loopback)
        if _is_ip_literal(host_str):
            raise NetworkBlocked(
                f"DNS blocked: Raw IP not allowed: {host_str}. "
                f"Use hostname from allowlist."
            )

        # Check allowlist (BEFORE DNS)
        if host_str not in allowed_hosts:
            raise NetworkBlocked(
                f"DNS blocked: Host not in allowlist: {host_str}. "
                f"Add to allowlist if legitimate."
            )

        return _orig_getaddrinfo(host, port, family, type, proto, flags)

    def guarded_create_connection(
        address: Tuple[str, int],
        timeout: Any = socket._GLOBAL_DEFAULT_TIMEOUT,
        source_address: Optional[Tuple[str, int]] = None,
        **kwargs: Any,
    ) -> socket.socket:
        """Guarded create_connection - blocks connections to non-allowed hosts."""
        host, port = address[0], address[1]
        host_str = str(host).lower()

        # Allow loopback
        if _is_loopback(host_str):
            return _orig_create_connection(address, timeout, source_address, **kwargs)

        # Block raw IP
        if _is_ip_literal(host_str):
            raise NetworkBlocked(
                f"Connect blocked: Raw IP not allowed: {host_str}:{port}"
            )

        # Check allowlist
        if host_str not in allowed_hosts:
            raise NetworkBlocked(
                f"Connect blocked: Host not in allowlist: {host_str}:{port}"
            )

        return _orig_create_connection(address, timeout, source_address, **kwargs)

    # Patch socket module
    socket.getaddrinfo = guarded_getaddrinfo  # type: ignore
    socket.create_connection = guarded_create_connection  # type: ignore

    _INSTALLED = True


def is_installed() -> bool:
    """Check if network guards are installed."""
    return _INSTALLED


def uninstall_network_guards() -> None:
    """
    Uninstall network guards (for testing only).

    WARNING: Should never be called in production.
    """
    global _orig_getaddrinfo, _orig_create_connection, _INSTALLED

    if not _INSTALLED:
        return

    if _orig_getaddrinfo is not None:
        socket.getaddrinfo = _orig_getaddrinfo
    if _orig_create_connection is not None:
        socket.create_connection = _orig_create_connection

    _INSTALLED = False
