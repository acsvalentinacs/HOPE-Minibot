"""
SPOT Testnet Client for HOPE.

Uses testnet.binance.vision/api (in allowlist per CONTRACTS.md).
NO FUTURES support - only SPOT operations.

Implements atomic writes for state files per CRITICAL RULE: FILE WRITING.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("spot_testnet")

# Allowlisted domain per CONTRACTS.md
TESTNET_BASE_URL = "https://testnet.binance.vision/api"

# State directory
BASE_DIR = Path(r"C:\Users\kirillDev\Desktop\TradingBot\minibot")
STATE_DIR = BASE_DIR / "state"
SECRETS_DIR = Path(r"C:\secrets\hope")


def _atomic_write(path: Path, content: str) -> None:
    """Atomic write: temp -> fsync -> replace."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _load_env() -> Dict[str, str]:
    """Load environment from secrets."""
    env_file = SECRETS_DIR / ".env"
    result = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                result[key.strip()] = value.strip()
    return result


@dataclass
class SpotBalance:
    """Account balance for an asset."""
    asset: str
    free: float
    locked: float

    @property
    def total(self) -> float:
        return self.free + self.locked


@dataclass
class SpotOrderResult:
    """Result of order placement."""
    success: bool
    order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    symbol: str = ""
    side: str = ""  # BUY or SELL
    qty: float = 0.0
    price: float = 0.0
    status: str = ""  # NEW, FILLED, CANCELED, etc.
    error: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


class SpotTestnetClient:
    """
    Binance SPOT Testnet client.

    API docs: https://testnet.binance.vision/
    Base URL: https://testnet.binance.vision/api

    IMPORTANT: This is TESTNET only. No real funds involved.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        timeout: int = 10,
    ):
        """
        Initialize SPOT testnet client.

        Args:
            api_key: Testnet API key (or from env BINANCE_TESTNET_API_KEY)
            api_secret: Testnet API secret (or from env BINANCE_TESTNET_API_SECRET)
            timeout: Request timeout in seconds
        """
        env = _load_env()

        self.api_key = api_key or env.get("BINANCE_TESTNET_API_KEY", "")
        self.api_secret = api_secret or env.get("BINANCE_TESTNET_API_SECRET", "")
        self.timeout = timeout
        self.base_url = TESTNET_BASE_URL

        self._session = requests.Session()
        self._session.headers.update({
            "X-MBX-APIKEY": self.api_key,
            "Content-Type": "application/x-www-form-urlencoded",
        })

        if not self.api_key or not self.api_secret:
            logger.warning(
                "SPOT Testnet credentials not configured. "
                "Set BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET in .env"
            )

    def _sign(self, params: Dict[str, Any]) -> str:
        """
        Generate HMAC SHA256 signature with RFC 3986 percent-encoding.

        As of 2026-01-15, Binance requires strict percent-encoding.
        """
        # RFC 3986: use quote() instead of quote_plus()
        query_string = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signature

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        signed: bool = False,
    ) -> Dict[str, Any]:
        """
        Make API request.

        Args:
            method: HTTP method (GET, POST, DELETE)
            endpoint: API endpoint (e.g., /v3/account)
            params: Query/body parameters
            signed: Whether to add signature

        Returns:
            Response JSON as dict
        """
        url = f"{self.base_url}{endpoint}"
        params = params or {}

        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["signature"] = self._sign(params)

        try:
            if method == "GET":
                resp = self._session.get(url, params=params, timeout=self.timeout)
            elif method == "POST":
                resp = self._session.post(url, data=params, timeout=self.timeout)
            elif method == "DELETE":
                resp = self._session.delete(url, params=params, timeout=self.timeout)
            else:
                raise ValueError(f"Unsupported method: {method}")

            resp.raise_for_status()
            return resp.json()

        except requests.exceptions.HTTPError as e:
            error_data = {}
            try:
                error_data = e.response.json()
            except Exception:
                pass
            logger.error(
                "HTTP error %s %s: %s - %s",
                method, endpoint, e.response.status_code, error_data
            )
            raise
        except requests.exceptions.RequestException as e:
            logger.error("Request error %s %s: %s", method, endpoint, e)
            raise

    # =========================================================================
    # PUBLIC ENDPOINTS (no signature required)
    # =========================================================================

    def ping(self) -> bool:
        """Test connectivity to API."""
        try:
            self._request("GET", "/v3/ping")
            return True
        except Exception as e:
            logger.error("Ping failed: %s", e)
            return False

    def get_server_time(self) -> int:
        """Get server time in milliseconds."""
        data = self._request("GET", "/v3/time")
        return data.get("serverTime", 0)

    def get_exchange_info(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """
        Get exchange information.

        Args:
            symbol: Optional symbol to filter (e.g., BTCUSDT)

        Returns:
            Exchange info with symbols, filters, etc.
        """
        params = {}
        if symbol:
            params["symbol"] = symbol
        return self._request("GET", "/v3/exchangeInfo", params)

    def get_ticker_price(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get current price(s).

        Args:
            symbol: Optional symbol (returns single dict if specified)

        Returns:
            List of {symbol, price} dicts
        """
        params = {}
        if symbol:
            params["symbol"] = symbol

        data = self._request("GET", "/v3/ticker/price", params)

        if isinstance(data, dict):
            return [data]
        return data

    def get_ticker_24hr(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get 24hr price change statistics.

        Args:
            symbol: Optional symbol

        Returns:
            List of 24hr stats dicts
        """
        params = {}
        if symbol:
            params["symbol"] = symbol

        data = self._request("GET", "/v3/ticker/24hr", params)

        if isinstance(data, dict):
            return [data]
        return data

    # =========================================================================
    # ACCOUNT ENDPOINTS (signature required)
    # =========================================================================

    def get_account(self) -> Dict[str, Any]:
        """
        Get account information including balances.

        Returns:
            Account info with balances, permissions, etc.
        """
        return self._request("GET", "/v3/account", signed=True)

    def get_balances(self) -> List[SpotBalance]:
        """
        Get non-zero balances.

        Returns:
            List of SpotBalance objects with free/locked amounts
        """
        account = self.get_account()
        balances = []

        for b in account.get("balances", []):
            free = float(b.get("free", 0))
            locked = float(b.get("locked", 0))
            if free > 0 or locked > 0:
                balances.append(SpotBalance(
                    asset=b["asset"],
                    free=free,
                    locked=locked,
                ))

        return balances

    def get_balance(self, asset: str) -> SpotBalance:
        """
        Get balance for specific asset.

        Args:
            asset: Asset symbol (e.g., BTC, USDT)

        Returns:
            SpotBalance object
        """
        account = self.get_account()

        for b in account.get("balances", []):
            if b["asset"] == asset:
                return SpotBalance(
                    asset=asset,
                    free=float(b.get("free", 0)),
                    locked=float(b.get("locked", 0)),
                )

        return SpotBalance(asset=asset, free=0.0, locked=0.0)

    # =========================================================================
    # ORDER ENDPOINTS
    # =========================================================================

    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        client_order_id: Optional[str] = None,
    ) -> SpotOrderResult:
        """
        Place market order.

        Args:
            symbol: Trading pair (e.g., BTCUSDT)
            side: BUY or SELL
            quantity: Amount to buy/sell
            client_order_id: Optional custom order ID

        Returns:
            SpotOrderResult with order details
        """
        params = {
            "symbol": symbol,
            "side": side.upper(),
            "type": "MARKET",
            "quantity": f"{quantity:.8f}".rstrip("0").rstrip("."),
        }

        if client_order_id:
            params["newClientOrderId"] = client_order_id

        try:
            data = self._request("POST", "/v3/order", params, signed=True)

            # Calculate avg price from fills
            fills = data.get("fills", [])
            total_qty = sum(float(f["qty"]) for f in fills)
            total_cost = sum(float(f["qty"]) * float(f["price"]) for f in fills)
            avg_price = total_cost / total_qty if total_qty > 0 else 0

            return SpotOrderResult(
                success=True,
                order_id=str(data.get("orderId")),
                client_order_id=data.get("clientOrderId"),
                symbol=data.get("symbol", symbol),
                side=data.get("side", side),
                qty=float(data.get("executedQty", quantity)),
                price=avg_price,
                status=data.get("status", ""),
                raw=data,
            )

        except Exception as e:
            return SpotOrderResult(
                success=False,
                symbol=symbol,
                side=side,
                qty=quantity,
                error=str(e),
            )

    def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        time_in_force: str = "GTC",
        client_order_id: Optional[str] = None,
    ) -> SpotOrderResult:
        """
        Place limit order.

        Args:
            symbol: Trading pair
            side: BUY or SELL
            quantity: Amount
            price: Limit price
            time_in_force: GTC, IOC, FOK
            client_order_id: Optional custom order ID

        Returns:
            SpotOrderResult with order details
        """
        params = {
            "symbol": symbol,
            "side": side.upper(),
            "type": "LIMIT",
            "timeInForce": time_in_force,
            "quantity": f"{quantity:.8f}".rstrip("0").rstrip("."),
            "price": f"{price:.8f}".rstrip("0").rstrip("."),
        }

        if client_order_id:
            params["newClientOrderId"] = client_order_id

        try:
            data = self._request("POST", "/v3/order", params, signed=True)

            return SpotOrderResult(
                success=True,
                order_id=str(data.get("orderId")),
                client_order_id=data.get("clientOrderId"),
                symbol=data.get("symbol", symbol),
                side=data.get("side", side),
                qty=float(data.get("origQty", quantity)),
                price=float(data.get("price", price)),
                status=data.get("status", ""),
                raw=data,
            )

        except Exception as e:
            return SpotOrderResult(
                success=False,
                symbol=symbol,
                side=side,
                qty=quantity,
                price=price,
                error=str(e),
            )

    def cancel_order(
        self,
        symbol: str,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
    ) -> SpotOrderResult:
        """
        Cancel an open order.

        Args:
            symbol: Trading pair
            order_id: Exchange order ID
            client_order_id: Custom order ID

        Returns:
            SpotOrderResult with cancellation result
        """
        params = {"symbol": symbol}

        if order_id:
            params["orderId"] = order_id
        elif client_order_id:
            params["origClientOrderId"] = client_order_id
        else:
            return SpotOrderResult(
                success=False,
                symbol=symbol,
                error="Either order_id or client_order_id required",
            )

        try:
            data = self._request("DELETE", "/v3/order", params, signed=True)

            return SpotOrderResult(
                success=True,
                order_id=str(data.get("orderId")),
                client_order_id=data.get("clientOrderId"),
                symbol=data.get("symbol", symbol),
                side=data.get("side", ""),
                qty=float(data.get("executedQty", 0)),
                price=float(data.get("price", 0)),
                status=data.get("status", "CANCELED"),
                raw=data,
            )

        except Exception as e:
            return SpotOrderResult(
                success=False,
                symbol=symbol,
                error=str(e),
            )

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all open orders.

        Args:
            symbol: Optional symbol filter

        Returns:
            List of open orders
        """
        params = {}
        if symbol:
            params["symbol"] = symbol

        return self._request("GET", "/v3/openOrders", params, signed=True)

    def get_order(
        self,
        symbol: str,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Query order status.

        Args:
            symbol: Trading pair
            order_id: Exchange order ID
            client_order_id: Custom order ID

        Returns:
            Order details dict
        """
        params = {"symbol": symbol}

        if order_id:
            params["orderId"] = order_id
        elif client_order_id:
            params["origClientOrderId"] = client_order_id

        return self._request("GET", "/v3/order", params, signed=True)

    # =========================================================================
    # TRADE HISTORY
    # =========================================================================

    def get_my_trades(
        self,
        symbol: str,
        limit: int = 500,
        from_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get account trade history.

        Args:
            symbol: Trading pair
            limit: Max records (default 500, max 1000)
            from_id: Trade ID to fetch from

        Returns:
            List of trade records
        """
        params = {
            "symbol": symbol,
            "limit": min(limit, 1000),
        }

        if from_id:
            params["fromId"] = from_id

        return self._request("GET", "/v3/myTrades", params, signed=True)

    # =========================================================================
    # UTILITY
    # =========================================================================

    def save_snapshot(self, data: Dict[str, Any], prefix: str = "spot") -> str:
        """
        Save data snapshot with hash ID.

        Args:
            data: Data to snapshot
            prefix: Snapshot prefix (e.g., "spot", "balances")

        Returns:
            snapshot_id (sha256:...)
        """
        # Add metadata
        snapshot = {
            "ts_utc": time.time(),
            "source": "testnet.binance.vision",
            "data": data,
        }

        # Compute hash
        canonical = json.dumps(snapshot, sort_keys=True, ensure_ascii=False)
        hash_hex = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        snapshot_id = f"sha256:{hash_hex}"
        snapshot["snapshot_id"] = snapshot_id

        # Save to snapshots dir
        snapshots_dir = BASE_DIR / "data" / "snapshots" / f"{prefix}_testnet"
        snapshots_dir.mkdir(parents=True, exist_ok=True)

        ts_str = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
        filename = f"{ts_str}_{hash_hex[:16]}.json"
        filepath = snapshots_dir / filename

        _atomic_write(filepath, json.dumps(snapshot, indent=2, ensure_ascii=False))
        logger.debug("Saved snapshot: %s", snapshot_id[:32])

        return snapshot_id

    def health_check(self) -> Dict[str, Any]:
        """
        Perform health check.

        Returns:
            Dict with connectivity status, server time diff, etc.
        """
        result = {
            "ok": False,
            "ping": False,
            "time_diff_ms": None,
            "credentials": bool(self.api_key and self.api_secret),
            "account_access": False,
            "error": None,
        }

        try:
            # Test ping
            result["ping"] = self.ping()

            if result["ping"]:
                # Test time sync
                server_time = self.get_server_time()
                local_time = int(time.time() * 1000)
                result["time_diff_ms"] = local_time - server_time

                # Test account access (if credentials present)
                if result["credentials"]:
                    try:
                        self.get_account()
                        result["account_access"] = True
                    except Exception as e:
                        result["error"] = f"Account access failed: {e}"

            result["ok"] = result["ping"] and (
                not result["credentials"] or result["account_access"]
            )

        except Exception as e:
            result["error"] = str(e)

        return result


# CLI interface
def main() -> int:
    """CLI entrypoint for SPOT testnet client."""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if len(sys.argv) < 2:
        print("Usage: python -m core.spot_testnet_client <command>")
        print("Commands:")
        print("  health    - Check connectivity and credentials")
        print("  balance   - Show account balances")
        print("  price     - Get BTC price")
        print("  ticker    - Get 24hr stats for BTC")
        return 1

    command = sys.argv[1]
    client = SpotTestnetClient()

    if command == "health":
        result = client.health_check()
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1

    elif command == "balance":
        try:
            balances = client.get_balances()
            print(f"Found {len(balances)} non-zero balances:")
            for b in balances:
                print(f"  {b.asset}: free={b.free:.8f}, locked={b.locked:.8f}")
            return 0
        except Exception as e:
            print(f"Error: {e}")
            return 1

    elif command == "price":
        try:
            tickers = client.get_ticker_price("BTCUSDT")
            for t in tickers:
                print(f"{t['symbol']}: {t['price']}")
            return 0
        except Exception as e:
            print(f"Error: {e}")
            return 1

    elif command == "ticker":
        try:
            stats = client.get_ticker_24hr("BTCUSDT")
            for s in stats:
                print(f"{s['symbol']}:")
                print(f"  Price: {s['lastPrice']}")
                print(f"  Change: {s['priceChangePercent']}%")
                print(f"  High: {s['highPrice']}")
                print(f"  Low: {s['lowPrice']}")
                print(f"  Volume: {s['volume']}")
            return 0
        except Exception as e:
            print(f"Error: {e}")
            return 1

    else:
        print(f"Unknown command: {command}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
