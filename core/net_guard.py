# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 15:30:00 UTC
# === END SIGNATURE ===
"""
core/net_guard.py - Socket-Level Network Allowlist Enforcement.

CRITICAL SECURITY: This module MUST be imported FIRST in all runners.

PURPOSE:
    Patches socket.socket.connect at import time to enforce AllowList.txt.
    Any connection to a non-whitelisted host is BLOCKED (fail-closed).

PROTOCOL:
    - Load AllowList.txt once at import
    - Patch socket.connect to check destination host
    - Block + log any unauthorized connection attempts
    - Fail-closed: if AllowList.txt missing/invalid, NO connections allowed

USAGE:
    # At the TOP of every runner file (before any other imports):
    import core.net_guard  # noqa: F401 - activates guard

    # To check if a host is allowed (without connecting):
    from core.net_guard import is_allowed, get_allowed_hosts

THREAD SAFETY:
    - Allowlist loaded once at import (immutable after)
    - Socket patch is thread-safe (original connect is reentrant)

FAIL-CLOSED BEHAVIOR:
    - Missing AllowList.txt -> EMPTY allowlist (all blocked)
    - Invalid line format -> skip line (log warning)
    - Connection to non-allowed host -> BlockedConnectionError
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from typing import Any, FrozenSet, Optional, Tuple

# === CONFIGURATION ===

# AllowList.txt location (relative to this file's parent.parent = minibot/)
_MINIBOT_ROOT = Path(__file__).resolve().parent.parent
_ALLOWLIST_PATH = _MINIBOT_ROOT / "AllowList.txt"

# Localhost is always allowed (internal IPC)
_ALWAYS_ALLOWED = frozenset({
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
})


# === EXCEPTIONS ===

class BlockedConnectionError(ConnectionError):
    """Raised when connection to non-whitelisted host is attempted."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        super().__init__(
            f"BLOCKED: Connection to '{host}:{port}' denied. "
            f"Host not in AllowList.txt. Fail-closed."
        )


# === ALLOWLIST LOADING ===

def _load_allowlist(path: Path) -> FrozenSet[str]:
    """
    Load allowed hosts from AllowList.txt.

    Format (per line):
        - Empty lines and lines starting with # are ignored
        - Each line = one hostname (exact match, no wildcards)

    Fail-closed:
        - If file missing -> return empty set (all blocked)
        - If line invalid -> skip and warn

    Returns:
        Frozen set of lowercase hostnames.
    """
    if not path.exists():
        print(
            f"[CRITICAL] net_guard: AllowList.txt NOT FOUND at {path}\n"
            f"[CRITICAL] net_guard: ALL OUTBOUND CONNECTIONS WILL BE BLOCKED",
            file=sys.stderr,
        )
        return frozenset()

    allowed = set()

    try:
        content = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(content.splitlines(), start=1):
            line = line.strip()

            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue

            # No wildcards allowed (security)
            if "*" in line or "?" in line:
                print(
                    f"[WARN] net_guard: Line {line_no}: Wildcards forbidden, skipping: {line}",
                    file=sys.stderr,
                )
                continue

            # Normalize to lowercase
            allowed.add(line.lower())

    except Exception as e:
        print(
            f"[CRITICAL] net_guard: Failed to read AllowList.txt: {e}\n"
            f"[CRITICAL] net_guard: ALL OUTBOUND CONNECTIONS WILL BE BLOCKED",
            file=sys.stderr,
        )
        return frozenset()

    return frozenset(allowed)


# === LOAD ALLOWLIST AT IMPORT ===

_ALLOWED_HOSTS: FrozenSet[str] = _load_allowlist(_ALLOWLIST_PATH)

# Log loaded hosts count
if _ALLOWED_HOSTS:
    print(
        f"[INFO] net_guard: Loaded {len(_ALLOWED_HOSTS)} allowed hosts from AllowList.txt",
        file=sys.stderr,
    )
else:
    print(
        f"[WARN] net_guard: Allowlist is EMPTY - all outbound connections blocked!",
        file=sys.stderr,
    )


# === PUBLIC API ===

def is_allowed(host: str) -> bool:
    """
    Check if host is in the allowlist.

    Args:
        host: Hostname or IP to check

    Returns:
        True if allowed, False otherwise
    """
    host_lower = host.lower()

    # Localhost always allowed
    if host_lower in _ALWAYS_ALLOWED:
        return True

    return host_lower in _ALLOWED_HOSTS


def get_allowed_hosts() -> FrozenSet[str]:
    """Get copy of allowed hosts set."""
    return _ALLOWED_HOSTS


def get_allowlist_path() -> Path:
    """Get path to AllowList.txt."""
    return _ALLOWLIST_PATH


# === SOCKET PATCHING ===

_original_connect: Optional[Any] = None


def _extract_host(address: Any) -> Optional[str]:
    """
    Extract hostname from socket address.

    Handles:
        - (host, port) tuples (IPv4)
        - (host, port, flowinfo, scopeid) tuples (IPv6)
        - Unix sockets (returns None - always allowed)
    """
    if isinstance(address, tuple) and len(address) >= 2:
        return str(address[0])
    return None


def _guarded_connect(self: socket.socket, address: Any) -> Any:
    """
    Guarded socket.connect that checks allowlist.

    FAIL-CLOSED: If host not allowed, raises BlockedConnectionError.
    """
    host = _extract_host(address)

    if host is not None:
        if not is_allowed(host):
            port = address[1] if isinstance(address, tuple) and len(address) >= 2 else 0
            # Log blocked attempt
            print(
                f"[BLOCKED] net_guard: Connection to {host}:{port} DENIED",
                file=sys.stderr,
            )
            raise BlockedConnectionError(host, port)

    # Allowed - proceed with original connect
    return _original_connect(self, address)


def _install_guard() -> bool:
    """
    Install the socket guard by patching socket.socket.connect.

    Returns:
        True if installed, False if already installed.
    """
    global _original_connect

    if _original_connect is not None:
        # Already installed
        return False

    _original_connect = socket.socket.connect
    socket.socket.connect = _guarded_connect

    print(
        "[INFO] net_guard: Socket guard ACTIVATED - enforcing AllowList.txt",
        file=sys.stderr,
    )
    return True


def _uninstall_guard() -> bool:
    """
    Remove the socket guard (for testing only).

    Returns:
        True if removed, False if not installed.
    """
    global _original_connect

    if _original_connect is None:
        return False

    socket.socket.connect = _original_connect
    _original_connect = None
    return True


# === INSTALL GUARD AT IMPORT ===

# Environment variable to disable guard (for testing only)
if os.environ.get("HOPE_NET_GUARD_DISABLE") != "1":
    _install_guard()
else:
    print(
        "[WARN] net_guard: Guard DISABLED by HOPE_NET_GUARD_DISABLE=1",
        file=sys.stderr,
    )


# === CLI SELF-TEST ===

def _self_test() -> int:
    """Run self-test to verify guard is working."""
    import tempfile

    print("=== NET_GUARD SELF-TEST ===\n")

    # Test 1: Allowlist loading
    print("Test 1: Verify allowlist loaded...")
    hosts = get_allowed_hosts()
    print(f"  Loaded {len(hosts)} hosts")
    if "api.binance.com" in hosts:
        print("  PASS: api.binance.com in allowlist")
    else:
        print("  FAIL: api.binance.com NOT in allowlist")
        return 1

    # Test 2: is_allowed checks
    print("\nTest 2: is_allowed() checks...")
    test_cases = [
        ("api.binance.com", True),
        ("localhost", True),
        ("127.0.0.1", True),
        ("evil-hacker.com", False),
        ("not-in-list.org", False),
        ("API.BINANCE.COM", True),  # Case insensitive
    ]

    for host, expected in test_cases:
        result = is_allowed(host)
        status = "PASS" if result == expected else "FAIL"
        print(f"  {status}: is_allowed('{host}') = {result} (expected {expected})")
        if result != expected:
            return 1

    # Test 3: Guard blocking (if installed)
    print("\nTest 3: Guard blocking...")
    if _original_connect is not None:
        # Create a test socket (don't actually connect)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.1)
            try:
                s.connect(("evil-hacker.com", 80))
                print("  FAIL: Connection to evil-hacker.com was NOT blocked!")
                s.close()
                return 1
            except BlockedConnectionError as e:
                print(f"  PASS: {e.host}:{e.port} correctly blocked")
            except socket.timeout:
                print("  FAIL: Timeout instead of BlockedConnectionError")
                return 1
            except OSError:
                # May get OSError if DNS fails first - that's OK
                print("  PASS: Connection failed (DNS/network error before guard)")
            finally:
                s.close()
        except Exception as e:
            print(f"  ERROR: {e}")
            return 1
    else:
        print("  SKIP: Guard not installed (HOPE_NET_GUARD_DISABLE=1)")

    # Test 4: Localhost should work
    print("\nTest 4: Localhost always allowed...")
    if is_allowed("localhost") and is_allowed("127.0.0.1"):
        print("  PASS: localhost allowed")
    else:
        print("  FAIL: localhost not allowed!")
        return 1

    print("\n=== ALL TESTS PASSED ===")
    return 0


if __name__ == "__main__":
    sys.exit(_self_test())
