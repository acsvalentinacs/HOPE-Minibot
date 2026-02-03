# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 22:45:00 UTC
# Purpose: Fail-closed Binance LIVE client loader
# === END SIGNATURE ===
"""
BINANCE LIVE CLIENT v1.0 - FAIL-CLOSED KEY LOADING

Этот модуль:
1. Загружает ключи из env ТОЛЬКО при HOPE_MODE=LIVE + HOPE_LIVE_ACK=YES_I_UNDERSTAND
2. FAIL-CLOSED: если ключей нет → исключение, не пустая строка
3. Поддерживает DRY/TESTNET/LIVE режимы
"""

import os
import logging
from typing import Optional, Tuple
from enum import Enum
from pathlib import Path

# Centralized secrets path
from core.secrets import SECRETS_PATH

log = logging.getLogger(__name__)


class TradingMode(Enum):
    DRY = "dry"
    TESTNET = "testnet"
    LIVE = "live"


class LiveModeNotAcknowledged(Exception):
    """LIVE mode requires explicit acknowledgment."""
    pass


class MissingCredentials(Exception):
    """Required credentials not found in environment."""
    pass


def load_env_file(path: Optional[Path] = None) -> None:
    """Load .env file if exists (append to os.environ, not overwrite)."""
    env_path = path or SECRETS_PATH
    if not env_path.exists():
        log.warning(f"Env file not found: {path}")
        return

    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    # Only set if not already in env (don't overwrite)
                    if key not in os.environ:
                        os.environ[key] = value
        log.info(f"Loaded env from {path}")
    except Exception as e:
        log.error(f"Failed to load env: {e}")


def get_trading_mode() -> TradingMode:
    """
    Determine trading mode from environment.

    HOPE_MODE=LIVE requires HOPE_LIVE_ACK=YES_I_UNDERSTAND
    """
    # Load secrets first
    load_env_file()

    mode_str = os.environ.get("HOPE_MODE", "DRY").upper()

    if mode_str == "LIVE":
        ack = os.environ.get("HOPE_LIVE_ACK", "")
        if ack != "YES_I_UNDERSTAND":
            raise LiveModeNotAcknowledged(
                "LIVE mode requires HOPE_LIVE_ACK=YES_I_UNDERSTAND. "
                "This is a safety measure to prevent accidental live trading."
            )
        return TradingMode.LIVE

    elif mode_str == "TESTNET":
        return TradingMode.TESTNET

    return TradingMode.DRY


def get_binance_credentials(mode: TradingMode) -> Tuple[str, str]:
    """
    Get Binance API credentials. FAIL-CLOSED.

    Returns:
        Tuple[api_key, api_secret]

    Raises:
        MissingCredentials if keys not found
    """
    if mode == TradingMode.DRY:
        return ("", "")  # DRY mode doesn't need real keys

    if mode == TradingMode.TESTNET:
        api_key = os.environ.get("BINANCE_TESTNET_API_KEY", "")
        api_secret = os.environ.get("BINANCE_TESTNET_API_SECRET", "")

        # Fallback to main keys if testnet not set
        if not api_key:
            api_key = os.environ.get("BINANCE_API_KEY", "")
            api_secret = os.environ.get("BINANCE_API_SECRET", "")

        if not api_key or not api_secret:
            raise MissingCredentials(
                "TESTNET mode requires BINANCE_TESTNET_API_KEY/SECRET or BINANCE_API_KEY/SECRET"
            )
        return (api_key, api_secret)

    # LIVE mode
    api_key = os.environ.get("BINANCE_API_KEY", "")
    api_secret = os.environ.get("BINANCE_API_SECRET", "")

    if not api_key or not api_secret:
        raise MissingCredentials(
            f"LIVE mode requires BINANCE_API_KEY and BINANCE_API_SECRET in environment. "
            f"Set them in {SECRETS_PATH}"
        )

    # Validate key format (basic check)
    if len(api_key) < 20 or len(api_secret) < 20:
        raise MissingCredentials("API key/secret appear invalid (too short)")

    log.info(f"LIVE credentials loaded: key={api_key[:8]}...")
    return (api_key, api_secret)


def create_binance_client(mode: TradingMode):
    """
    Create Binance client for given mode.

    Returns:
        - None for DRY mode
        - AsyncClient for TESTNET/LIVE

    Raises:
        ImportError if binance package not installed
        MissingCredentials if keys not found
    """
    if mode == TradingMode.DRY:
        log.info("DRY mode: no Binance client created")
        return None

    try:
        from binance import AsyncClient
    except ImportError:
        raise ImportError(
            "python-binance package required for TESTNET/LIVE. "
            "Install: pip install python-binance"
        )

    api_key, api_secret = get_binance_credentials(mode)

    if mode == TradingMode.TESTNET:
        log.info("Creating TESTNET Binance client")
        # Note: testnet=True in AsyncClient
        return AsyncClient(
            api_key=api_key,
            api_secret=api_secret,
            testnet=True
        )

    # LIVE
    log.warning("Creating LIVE Binance client - REAL MONEY")
    return AsyncClient(
        api_key=api_key,
        api_secret=api_secret,
        testnet=False
    )


async def create_async_binance_client(mode: TradingMode):
    """
    Create async Binance client.
    """
    if mode == TradingMode.DRY:
        return None

    try:
        from binance import AsyncClient
    except ImportError:
        raise ImportError("python-binance package required")

    api_key, api_secret = get_binance_credentials(mode)

    testnet = (mode == TradingMode.TESTNET)

    client = await AsyncClient.create(
        api_key=api_key,
        api_secret=api_secret,
        testnet=testnet
    )

    log.info(f"Async Binance client created: mode={mode.value}")
    return client


# ═══════════════════════════════════════════════════════════════════════════════
# LIVE BARRIER CHECK
# ═══════════════════════════════════════════════════════════════════════════════

def check_live_barrier() -> Tuple[bool, str]:
    """
    Check if LIVE trading is properly enabled.

    Returns:
        Tuple[is_live_ready, message]
    """
    load_env_file()

    mode = os.environ.get("HOPE_MODE", "DRY").upper()
    ack = os.environ.get("HOPE_LIVE_ACK", "")
    has_key = bool(os.environ.get("BINANCE_API_KEY", ""))
    has_secret = bool(os.environ.get("BINANCE_API_SECRET", ""))

    checks = {
        "HOPE_MODE=LIVE": mode == "LIVE",
        "HOPE_LIVE_ACK=YES_I_UNDERSTAND": ack == "YES_I_UNDERSTAND",
        "BINANCE_API_KEY present": has_key,
        "BINANCE_API_SECRET present": has_secret,
    }

    all_pass = all(checks.values())

    msg_parts = []
    for check, passed in checks.items():
        status = "OK" if passed else "FAIL"
        msg_parts.append(f"  [{status}] {check}")

    return (all_pass, "\n".join(msg_parts))


# ═══════════════════════════════════════════════════════════════════════════════
# TEST
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("=" * 60)
    print("BINANCE LIVE CLIENT - BARRIER CHECK")
    print("=" * 60)

    is_ready, msg = check_live_barrier()
    print(msg)
    print()

    if is_ready:
        print("[PASS] LIVE trading is enabled")
    else:
        print("[INFO] LIVE trading NOT enabled (DRY mode)")

    try:
        mode = get_trading_mode()
        print(f"\nCurrent mode: {mode.value}")
    except LiveModeNotAcknowledged as e:
        print(f"\n[BLOCKED] {e}")
    except MissingCredentials as e:
        print(f"\n[BLOCKED] {e}")
