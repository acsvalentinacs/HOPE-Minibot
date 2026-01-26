# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T22:45:00Z
# Modified by: Claude (opus-4)
# Modified at (UTC): 2026-01-26T04:35:00Z
# Purpose: Binance Spot API thin client - HMAC-signed REST (fail-closed)
# P0 FIX: Uses core.net.http_client for egress policy enforcement
# P0 FIX: Added newClientOrderId for order idempotency
# === END SIGNATURE ===
"""
Binance Spot API Client v1.0.

Thin client for Binance Spot trading operations.
Uses HMAC-SHA256 signing for authenticated endpoints.

Features:
- Market buy/sell orders
- Limit orders (GTC, IOC, FOK)
- Order cancellation
- Account balance queries
- Fail-closed on all errors

Environment:
- TESTNET: https://testnet.binance.vision/api
- MAINNET: https://api.binance.com/api

Credentials from C:\\secrets\\hope\\.env:
- TESTNET: BINANCE_TESTNET_API_KEY, BINANCE_TESTNET_API_SECRET
- MAINNET: BINANCE_API_KEY, BINANCE_API_SECRET (requires explicit --mainnet flag)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, Any, Optional, List
from urllib.parse import urlencode

# P0 FIX: Use egress-safe HTTP client (AllowList enforcement)
from core.net.http_client import (
    http_get,
    http_post,
    http_delete,
    EgressDeniedError,
    EgressError,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class BinanceEnv(Enum):
    """Binance environment."""
    TESTNET = "testnet"
    MAINNET = "mainnet"


@dataclass
class BinanceCredentials:
    """API credentials."""
    api_key: str
    api_secret: str
    env: BinanceEnv


@dataclass
class OrderResult:
    """Order execution result."""
    order_id: int
    symbol: str
    side: str
    type: str
    status: str
    executed_qty: str
    price: str
    fills: List[Dict[str, Any]]
    raw: Dict[str, Any]


class BinanceSpotClient:
    """
    Binance Spot API Client.

    Supports TESTNET and MAINNET with fail-closed error handling.
    """

    TESTNET_BASE = "https://testnet.binance.vision/api"
    MAINNET_BASE = "https://api.binance.com/api"

    def __init__(
        self,
        credentials: BinanceCredentials,
        timeout_ms: int = 10000,
    ):
        """
        Initialize client.

        Args:
            credentials: API credentials with env
            timeout_ms: Request timeout in milliseconds
        """
        self.credentials = credentials
        self.timeout = timeout_ms / 1000.0

        if credentials.env == BinanceEnv.TESTNET:
            self.base_url = self.TESTNET_BASE
        else:
            self.base_url = self.MAINNET_BASE

    def _sign(self, params: Dict[str, Any]) -> str:
        """
        Generate HMAC-SHA256 signature.

        Args:
            params: Query parameters

        Returns:
            Hex signature
        """
        query = urlencode(params)
        signature = hmac.new(
            self.credentials.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signature

    def _generate_client_order_id(self, symbol: str, side: str) -> str:
        """
        Generate unique client order ID for idempotency (P0 FIX).

        Format: hope_{symbol}_{side}_{timestamp_ms}_{nonce_6}
        Max length: 36 chars (Binance limit)

        Args:
            symbol: Trading pair (e.g., BTCUSDT)
            side: BUY or SELL

        Returns:
            Unique client order ID
        """
        ts = int(time.time() * 1000)
        nonce = secrets.token_hex(3)  # 6 hex chars
        # Truncate symbol if needed to fit 36 char limit
        # hope_ = 5, _BUY = 4, _ts = 14, _nonce = 7 = 30 chars for base
        # leaves 6 chars for symbol
        sym = symbol[:6]
        return f"hope_{sym}_{side[:1]}_{ts}_{nonce}"

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        signed: bool = False,
    ) -> Dict[str, Any]:
        """
        Make API request using egress-safe HTTP client.

        P0 FIX: All requests now go through core.net.http_client
        which enforces AllowList policy (fail-closed).

        Args:
            method: HTTP method (GET, POST, DELETE)
            endpoint: API endpoint (e.g., /v3/account)
            params: Query parameters
            signed: Whether to sign the request

        Returns:
            JSON response

        Raises:
            ValueError: On API error (fail-closed)
            EgressDeniedError: If host not in AllowList
        """
        params = params or {}

        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["signature"] = self._sign(params)

        url = f"{self.base_url}{endpoint}"

        headers = {
            "X-MBX-APIKEY": self.credentials.api_key,
        }

        process_name = f"binance_{method.lower()}_{endpoint.split('/')[-1]}"

        try:
            if method == "GET":
                if params:
                    url = f"{url}?{urlencode(params)}"
                status, body_bytes, _ = http_get(
                    url,
                    timeout_sec=int(self.timeout),
                    extra_headers=headers,
                    process=process_name,
                )
            elif method == "POST":
                data = urlencode(params).encode("utf-8") if params else None
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                status, body_bytes, _ = http_post(
                    url,
                    data=data,
                    timeout_sec=int(self.timeout),
                    extra_headers=headers,
                    process=process_name,
                )
            elif method == "DELETE":
                data = urlencode(params).encode("utf-8") if params else None
                if data:
                    headers["Content-Type"] = "application/x-www-form-urlencoded"
                status, body_bytes, _ = http_delete(
                    url,
                    data=data,
                    timeout_sec=int(self.timeout),
                    extra_headers=headers,
                    process=process_name,
                )
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            body = body_bytes.decode("utf-8")

            # Check for HTTP errors (4xx, 5xx)
            if status >= 400:
                try:
                    error = json.loads(body)
                    raise ValueError(f"Binance API error {status}: {error.get('msg', body)}")
                except json.JSONDecodeError:
                    raise ValueError(f"Binance API error {status}: {body}")

            return json.loads(body)

        except EgressDeniedError as e:
            # Egress policy violation - fail-closed
            raise ValueError(f"EGRESS DENIED: {e.host} not in AllowList. request_id={e.request_id}")
        except EgressError as e:
            # Network error after allow
            raise ValueError(f"Network error ({e.reason.value}): {e.original_error}")

    # === Public endpoints ===

    def get_server_time(self) -> int:
        """
        Get server time.

        Returns:
            Server time in milliseconds
        """
        resp = self._request("GET", "/v3/time")
        return resp["serverTime"]

    def get_exchange_info(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """
        Get exchange info.

        Args:
            symbol: Optional symbol filter

        Returns:
            Exchange info dict
        """
        params = {}
        if symbol:
            params["symbol"] = symbol
        return self._request("GET", "/v3/exchangeInfo", params)

    def get_ticker_price(self, symbol: str) -> Dict[str, str]:
        """
        Get current ticker price.

        Args:
            symbol: Trading pair symbol

        Returns:
            {"symbol": "BTCUSDT", "price": "50000.00"}
        """
        return self._request("GET", "/v3/ticker/price", {"symbol": symbol})

    def get_ticker_24h(self, symbol: str) -> Dict[str, Any]:
        """
        Get 24h ticker statistics.

        Args:
            symbol: Trading pair symbol

        Returns:
            24h stats including volume, price changes
        """
        return self._request("GET", "/v3/ticker/24hr", {"symbol": symbol})

    # === Signed endpoints (trading) ===

    def get_account(self) -> Dict[str, Any]:
        """
        Get account information.

        Returns:
            Account info with balances
        """
        return self._request("GET", "/v3/account", signed=True)

    def get_balance(self, asset: str) -> Dict[str, str]:
        """
        Get balance for specific asset.

        Args:
            asset: Asset symbol (e.g., "USDT", "BTC")

        Returns:
            {"asset": "USDT", "free": "100.0", "locked": "0.0"}

        Raises:
            ValueError: If asset not found
        """
        account = self.get_account()
        for balance in account.get("balances", []):
            if balance["asset"] == asset:
                return balance
        raise ValueError(f"Asset {asset} not found in account")

    def market_buy(
        self,
        symbol: str,
        quote_qty: float,
        client_order_id: Optional[str] = None,
    ) -> OrderResult:
        """
        Market buy using quote asset amount.

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            quote_qty: Amount to spend in quote asset (e.g., 10 USDT)
            client_order_id: Optional custom idempotency key (auto-generated if None)

        Returns:
            Order result
        """
        params = {
            "symbol": symbol,
            "side": "BUY",
            "type": "MARKET",
            "quoteOrderQty": f"{quote_qty:.8f}",
            # P0 FIX: newClientOrderId for idempotency
            "newClientOrderId": client_order_id or self._generate_client_order_id(symbol, "BUY"),
        }
        resp = self._request("POST", "/v3/order", params, signed=True)
        return self._parse_order_result(resp)

    def market_sell(
        self,
        symbol: str,
        quantity: float,
        client_order_id: Optional[str] = None,
    ) -> OrderResult:
        """
        Market sell.

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            quantity: Amount to sell in base asset
            client_order_id: Optional custom idempotency key (auto-generated if None)

        Returns:
            Order result
        """
        params = {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": f"{quantity:.8f}",
            # P0 FIX: newClientOrderId for idempotency
            "newClientOrderId": client_order_id or self._generate_client_order_id(symbol, "SELL"),
        }
        resp = self._request("POST", "/v3/order", params, signed=True)
        return self._parse_order_result(resp)

    def limit_sell(
        self,
        symbol: str,
        quantity: float,
        price: float,
        time_in_force: str = "GTC",
        client_order_id: Optional[str] = None,
    ) -> OrderResult:
        """
        Limit sell order.

        Args:
            symbol: Trading pair
            quantity: Amount to sell
            price: Limit price
            time_in_force: GTC (Good Till Cancel), IOC, FOK
            client_order_id: Optional custom idempotency key (auto-generated if None)

        Returns:
            Order result
        """
        params = {
            "symbol": symbol,
            "side": "SELL",
            "type": "LIMIT",
            "timeInForce": time_in_force,
            "quantity": f"{quantity:.8f}",
            "price": f"{price:.8f}",
            # P0 FIX: newClientOrderId for idempotency
            "newClientOrderId": client_order_id or self._generate_client_order_id(symbol, "SELL"),
        }
        resp = self._request("POST", "/v3/order", params, signed=True)
        return self._parse_order_result(resp)

    def cancel_order(
        self,
        symbol: str,
        order_id: int,
    ) -> Dict[str, Any]:
        """
        Cancel an open order.

        Args:
            symbol: Trading pair
            order_id: Order ID to cancel

        Returns:
            Cancellation result
        """
        params = {
            "symbol": symbol,
            "orderId": order_id,
        }
        return self._request("DELETE", "/v3/order", params, signed=True)

    def get_order(
        self,
        symbol: str,
        order_id: int,
    ) -> Dict[str, Any]:
        """
        Query order status.

        Args:
            symbol: Trading pair
            order_id: Order ID

        Returns:
            Order info
        """
        params = {
            "symbol": symbol,
            "orderId": order_id,
        }
        return self._request("GET", "/v3/order", params, signed=True)

    def get_open_orders(
        self,
        symbol: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get open orders.

        Args:
            symbol: Optional symbol filter

        Returns:
            List of open orders
        """
        params = {}
        if symbol:
            params["symbol"] = symbol
        return self._request("GET", "/v3/openOrders", params, signed=True)

    def _parse_order_result(self, resp: Dict[str, Any]) -> OrderResult:
        """Parse order response into OrderResult."""
        return OrderResult(
            order_id=resp["orderId"],
            symbol=resp["symbol"],
            side=resp["side"],
            type=resp["type"],
            status=resp["status"],
            executed_qty=resp.get("executedQty", "0"),
            price=resp.get("price", "0"),
            fills=resp.get("fills", []),
            raw=resp,
        )


def load_credentials(env: BinanceEnv) -> BinanceCredentials:
    """
    Load credentials from environment.

    Args:
        env: TESTNET or MAINNET

    Returns:
        BinanceCredentials

    Raises:
        ValueError: If credentials not found (fail-closed)
    """
    # Try environment variables first
    if env == BinanceEnv.TESTNET:
        key_var = "BINANCE_TESTNET_API_KEY"
        secret_var = "BINANCE_TESTNET_API_SECRET"
    else:
        key_var = "BINANCE_API_KEY"
        secret_var = "BINANCE_API_SECRET"

    api_key = os.environ.get(key_var)
    api_secret = os.environ.get(secret_var)

    if api_key and api_secret:
        return BinanceCredentials(api_key, api_secret, env)

    # Try .env file
    env_paths = [
        Path(r"C:\secrets\hope\.env"),
        Path(r"C:\secrets\hope.env"),
    ]

    for env_path in env_paths:
        if not env_path.exists():
            continue

        try:
            secrets = {}
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                secrets[key] = value

            if key_var in secrets and secret_var in secrets:
                return BinanceCredentials(
                    secrets[key_var],
                    secrets[secret_var],
                    env,
                )
        except Exception:
            continue

    raise ValueError(
        f"FAIL-CLOSED: Credentials not found for {env.value}. "
        f"Set {key_var} and {secret_var} in environment or C:\\secrets\\hope\\.env"
    )


def create_client(env: BinanceEnv, timeout_ms: int = 10000) -> BinanceSpotClient:
    """
    Create Binance client with credentials.

    Args:
        env: TESTNET or MAINNET
        timeout_ms: Request timeout

    Returns:
        Configured BinanceSpotClient
    """
    credentials = load_credentials(env)
    return BinanceSpotClient(credentials, timeout_ms)
