# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T00:30:00Z
# Purpose: Deterministic clientOrderId generation for idempotent order submission
# Security: SHA256 hash of canonical payload, max 36 chars for Binance
# === END SIGNATURE ===
"""
Idempotency Module - Deterministic Order ID Generation.

CRITICAL: clientOrderId MUST be deterministic from order parameters.
This ensures that retrying the same order produces the same ID,
making duplicate detection possible on exchange side.

Binance constraint: clientOrderId max 36 characters.
Format: "H" + first 35 chars of SHA256 hex (total 36 chars).

The "H" prefix indicates HOPE system orders for easy identification.
"""
import hashlib
import json
from typing import Dict, Any, Optional
from datetime import datetime, timezone


def canonical_payload(
    symbol: str,
    side: str,
    order_type: str,
    quantity: float,
    price: Optional[float] = None,
    time_in_force: str = "GTC",
    session_id: str = "",
    nonce: str = "",
) -> str:
    """
    Create canonical payload string for hashing.

    The payload is a deterministic JSON string with sorted keys.
    Same inputs ALWAYS produce same output.

    Args:
        symbol: Trading pair (e.g., "BTCUSDT")
        side: "BUY" or "SELL"
        order_type: "MARKET", "LIMIT", etc.
        quantity: Amount to trade (will be stringified to 8 decimals)
        price: Limit price (None for MARKET)
        time_in_force: Order time in force
        session_id: Session identifier (cmdline_sha256)
        nonce: Optional nonce for uniqueness (use sparingly)

    Returns:
        Canonical JSON string for hashing.
    """
    payload: Dict[str, Any] = {
        "s": symbol.upper(),
        "d": side.upper(),
        "t": order_type.upper(),
        "q": f"{quantity:.8f}",  # Fixed precision for determinism
    }

    if price is not None:
        payload["p"] = f"{price:.8f}"

    if time_in_force and time_in_force != "GTC":
        payload["f"] = time_in_force.upper()

    if session_id:
        payload["i"] = session_id[:16]  # Truncate to save space

    if nonce:
        payload["n"] = nonce[:8]  # Truncate nonce

    # Sort keys for determinism
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def generate_client_order_id(
    symbol: str,
    side: str,
    order_type: str,
    quantity: float,
    price: Optional[float] = None,
    time_in_force: str = "GTC",
    session_id: str = "",
    nonce: str = "",
) -> str:
    """
    Generate deterministic clientOrderId for Binance.

    CRITICAL: Same inputs ALWAYS produce same output.
    Format: "H" + first 35 chars of SHA256 hex = 36 chars total.

    Args:
        symbol: Trading pair
        side: "BUY" or "SELL"
        order_type: Order type
        quantity: Trade quantity
        price: Limit price (optional)
        time_in_force: Order TIF
        session_id: Session identifier
        nonce: Optional uniqueness nonce

    Returns:
        36-character clientOrderId starting with "H".

    Example:
        >>> generate_client_order_id("BTCUSDT", "BUY", "MARKET", 0.001)
        'Ha7b3f9c8d2e1f4a5b6c7d8e9f0a1b2c3d4'
    """
    payload = canonical_payload(
        symbol=symbol,
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=price,
        time_in_force=time_in_force,
        session_id=session_id,
        nonce=nonce,
    )

    # SHA256 of canonical payload
    hash_hex = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # "H" prefix + first 35 chars = 36 total (Binance limit)
    return "H" + hash_hex[:35]


def generate_client_order_id_from_intent(
    intent_dict: Dict[str, Any],
) -> str:
    """
    Generate clientOrderId from OrderIntentV1 dict.

    Args:
        intent_dict: OrderIntentV1 as dict

    Returns:
        36-character clientOrderId.
    """
    return generate_client_order_id(
        symbol=intent_dict["symbol"],
        side=intent_dict["side"],
        order_type=intent_dict["order_type"],
        quantity=intent_dict["quantity"],
        price=intent_dict.get("price"),
        time_in_force=intent_dict.get("time_in_force", "GTC"),
        session_id=intent_dict.get("session_id", ""),
        nonce=intent_dict.get("metadata", {}).get("nonce", ""),
    )


def verify_idempotency_key(
    client_order_id: str,
    symbol: str,
    side: str,
    order_type: str,
    quantity: float,
    price: Optional[float] = None,
    time_in_force: str = "GTC",
    session_id: str = "",
    nonce: str = "",
) -> bool:
    """
    Verify that clientOrderId matches expected hash.

    Use this to detect tampering or corruption.

    Args:
        client_order_id: ID to verify
        ... other args same as generate_client_order_id

    Returns:
        True if ID matches expected hash.
    """
    expected = generate_client_order_id(
        symbol=symbol,
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=price,
        time_in_force=time_in_force,
        session_id=session_id,
        nonce=nonce,
    )
    return client_order_id == expected


def generate_nonce_from_timestamp() -> str:
    """
    Generate nonce from current timestamp.

    Use when you need multiple orders with same params
    in the same session (rare case).

    Returns:
        8-character timestamp-based nonce.
    """
    ts = datetime.now(timezone.utc)
    # HHMMSSFF format (hours, minutes, seconds, centiseconds)
    return ts.strftime("%H%M%S") + f"{ts.microsecond // 10000:02d}"
