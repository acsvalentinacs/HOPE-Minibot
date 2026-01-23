# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 16:15:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 21:15:00 UTC
# === END SIGNATURE ===
"""
core/policy/bootstrap.py - HOPE Policy Bootstrap (Preflight).

HOPE-LAW-001: Запреты первичны и исполняются первыми.
HOPE-RULE-001: Guardrails-First Execution.

CRITICAL:
    bootstrap() MUST be called FIRST in every entrypoint,
    BEFORE any:
    - Logging
    - Network requests
    - File I/O (except policy loading)
    - Output to stdout/stderr

USAGE:
    # At the TOP of every entrypoint (e.g., *_runner.py)
    from core.policy.bootstrap import bootstrap
    bootstrap("component_name")  # MUST be first executable line
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path
from typing import Optional


def _setup_utf8_console() -> None:
    """
    Setup UTF-8 encoding for Windows console.

    Fixes garbled Russian text (показывается как ??????) on Windows.
    Must be called BEFORE any output.
    """
    if sys.platform != "win32":
        return

    # Set environment variable for subprocesses
    os.environ["PYTHONIOENCODING"] = "utf-8"

    # Reconfigure stdout/stderr to UTF-8
    try:
        # Python 3.7+ supports reconfigure
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        # Fallback: wrap streams
        try:
            sys.stdout = io.TextIOWrapper(
                sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
            )
            sys.stderr = io.TextIOWrapper(
                sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
            )
        except Exception:
            pass  # Best effort


# Setup UTF-8 immediately on module import (before any other imports)
_setup_utf8_console()

from core.policy.loader import Policy, PolicyError, load_policy
from core.policy.output_guard import install_output_guards
from core.policy.network_guard import install_network_guards


_POLICY_PATH = Path(__file__).parent / "policy.json"
_BOOTSTRAPPED = False
_CURRENT_POLICY: Optional[Policy] = None


class BootstrapError(RuntimeError):
    """Raised when bootstrap fails. System must stop."""
    pass


def bootstrap(
    component: str,
    *,
    network_profile: str = "default",
    skip_network: bool = False,
) -> Policy:
    """
    Initialize HOPE Policy Layer.

    CRITICAL: Must be called FIRST in every entrypoint.

    This function:
    1. Loads and validates policy.json (with SHA256 check)
    2. Installs output guards (blocks secret leaks)
    3. Installs network guards (enforces allowlist)

    Args:
        component: Component name (for logging/audit)
        network_profile: Allowlist profile (default, core, spider, dev)
        skip_network: Skip network guard (for tools that don't need network)

    Returns:
        Loaded Policy object

    Raises:
        BootstrapError: On any failure (fail-closed)
    """
    global _BOOTSTRAPPED, _CURRENT_POLICY

    if _BOOTSTRAPPED:
        if _CURRENT_POLICY is None:
            raise BootstrapError("Bootstrap state corrupted (fail-closed)")
        return _CURRENT_POLICY

    try:
        # 1. Load policy (with SHA256 verification)
        policy = load_policy(_POLICY_PATH, component=component)

        # 2. Install output guards FIRST (before any potential leaks)
        install_output_guards(policy)

        # 3. Install network guards (unless skipped)
        if not skip_network:
            install_network_guards(policy, profile=network_profile)

        _BOOTSTRAPPED = True
        _CURRENT_POLICY = policy

        # Log bootstrap success (will go through output guard)
        print(
            f"[INFO] bootstrap: HOPE Policy initialized for '{component}'",
            file=sys.stderr,
        )

        return policy

    except PolicyError as e:
        raise BootstrapError(f"Policy bootstrap failed (fail-closed): {e}") from e
    except Exception as e:
        raise BootstrapError(
            f"Bootstrap failed unexpectedly (fail-closed): {e}"
        ) from e


def is_bootstrapped() -> bool:
    """Check if bootstrap has been called."""
    return _BOOTSTRAPPED


def get_policy() -> Optional[Policy]:
    """Get current policy (None if not bootstrapped)."""
    return _CURRENT_POLICY


def require_bootstrap() -> Policy:
    """
    Require that bootstrap has been called.

    FAIL-CLOSED: Raises if not bootstrapped.

    Returns:
        Current Policy

    Raises:
        BootstrapError: If not bootstrapped
    """
    if not _BOOTSTRAPPED or _CURRENT_POLICY is None:
        raise BootstrapError(
            "HOPE-LAW-001 VIOLATION: bootstrap() not called. "
            "Policy must be initialized before any work."
        )
    return _CURRENT_POLICY
