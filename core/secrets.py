# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-03T17:00:00Z
# Purpose: Centralized secrets path resolution (SSoT)
# Security: Fail-closed, cross-platform, env var override
# === END SIGNATURE ===
"""
Centralized Secrets Path Resolution.

SSoT for all secrets/credentials file paths in HOPE system.

Priority:
1. HOPE_SECRETS_PATH environment variable (highest)
2. Platform-specific default:
   - Windows: C:\\secrets\\hope\\.env
   - Linux: /etc/hope/.env

Usage:
    from core.secrets import SECRETS_PATH, load_secrets, get_secret

    # Get path
    path = SECRETS_PATH  # Path object

    # Load all secrets as dict
    secrets = load_secrets()

    # Get specific secret (fail-closed)
    api_key = get_secret("BINANCE_API_KEY")
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)

# === SECRETS PATH RESOLUTION ===

def _resolve_secrets_path() -> Path:
    """
    Resolve secrets file path with priority:
    1. HOPE_SECRETS_PATH env var
    2. Platform default

    Returns:
        Path to secrets file
    """
    env_path = os.environ.get("HOPE_SECRETS_PATH")

    if env_path:
        path = Path(env_path)
        logger.debug("Using HOPE_SECRETS_PATH: %s", path)
        return path

    if sys.platform == "win32":
        path = Path(r"C:\secrets\hope\.env")
    else:
        path = Path("/etc/hope/.env")

    logger.debug("Using platform default secrets path: %s", path)
    return path


# SSoT - Single Source of Truth for secrets path
SECRETS_PATH: Path = _resolve_secrets_path()

# Alternative paths for backwards compatibility
SECRETS_PATH_ALT: list[Path] = [
    Path(r"C:\secrets\hope.env") if sys.platform == "win32" else Path("/etc/hope.env"),
]


def get_secrets_path() -> Path:
    """
    Get the secrets file path, checking existence.

    Returns:
        Path to existing secrets file

    Raises:
        FileNotFoundError: If no secrets file found (fail-closed)
    """
    if SECRETS_PATH.exists():
        return SECRETS_PATH

    # Check alternative paths
    for alt in SECRETS_PATH_ALT:
        if alt.exists():
            logger.warning("Using alternative secrets path: %s", alt)
            return alt

    raise FileNotFoundError(
        f"FAIL-CLOSED: Secrets file not found. "
        f"Set HOPE_SECRETS_PATH env var or create {SECRETS_PATH}"
    )


def load_secrets(path: Optional[Path] = None) -> Dict[str, str]:
    """
    Load secrets from .env file.

    Args:
        path: Optional path override

    Returns:
        Dict of key=value pairs

    Raises:
        FileNotFoundError: If secrets file not found
    """
    secrets_path = path or get_secrets_path()

    secrets: Dict[str, str] = {}

    try:
        content = secrets_path.read_text(encoding="utf-8")

        for line in content.splitlines():
            line = line.strip()

            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue

            # Skip lines without =
            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")

            if key:
                secrets[key] = value

        logger.debug("Loaded %d secrets from %s", len(secrets), secrets_path)
        return secrets

    except Exception as e:
        logger.error("Failed to load secrets from %s: %s", secrets_path, e)
        raise


def get_secret(key: str, default: Optional[str] = None, required: bool = True) -> Optional[str]:
    """
    Get a specific secret value.

    Priority:
    1. Environment variable
    2. Secrets file
    3. Default value

    Args:
        key: Secret key name
        default: Default value if not found
        required: If True, raise error when not found

    Returns:
        Secret value or default

    Raises:
        ValueError: If required and not found (fail-closed)
    """
    # Check environment first
    value = os.environ.get(key)
    if value:
        return value

    # Check secrets file
    try:
        secrets = load_secrets()
        value = secrets.get(key)
        if value:
            return value
    except FileNotFoundError:
        pass

    # Use default or fail
    if default is not None:
        return default

    if required:
        raise ValueError(
            f"FAIL-CLOSED: Required secret '{key}' not found. "
            f"Set as environment variable or in {SECRETS_PATH}"
        )

    return None


def ensure_secrets_exist() -> bool:
    """
    Check that secrets file exists.

    Returns:
        True if exists

    Raises:
        FileNotFoundError: If not found
    """
    get_secrets_path()  # Will raise if not found
    return True


# === CONVENIENCE FUNCTIONS ===

def get_binance_credentials(testnet: bool = False) -> tuple[str, str]:
    """
    Get Binance API credentials.

    Args:
        testnet: If True, get testnet credentials

    Returns:
        Tuple of (api_key, api_secret)

    Raises:
        ValueError: If credentials not found
    """
    if testnet:
        key = get_secret("BINANCE_TESTNET_API_KEY")
        secret = get_secret("BINANCE_TESTNET_API_SECRET")
    else:
        key = get_secret("BINANCE_API_KEY")
        secret = get_secret("BINANCE_API_SECRET")

    return key, secret


def get_telegram_token() -> str:
    """
    Get Telegram bot token.

    Returns:
        Bot token

    Raises:
        ValueError: If not found
    """
    return get_secret("TELEGRAM_BOT_TOKEN")
