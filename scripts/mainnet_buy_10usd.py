# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T15:50:00Z
# Purpose: MAINNET $10 BTC purchase - REAL MONEY with safety checks
# Security: Double confirmation, balance check, hard limit $10.50
# === END SIGNATURE ===
"""
MAINNET $10 BTC Purchase Script.

SAFETY FEATURES:
- Hard limit: $10.50 maximum
- Balance check: requires $11+ USDT
- Double confirmation: must type YES
- 3 second delay: can Ctrl+C to abort
- Full logging of every step
"""
from __future__ import annotations

import io
import sys
import os
import time
import json
import hashlib
import hmac
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

# UTF-8 for Windows
if sys.stdout and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr and hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Constants
MAX_USD = 10.50
MIN_BALANCE = 11.0
SYMBOL = "BTCUSDT"
BASE_URL = "https://api.binance.com"

# Audit log
PROJECT_ROOT = Path(__file__).resolve().parent.parent
AUDIT_FILE = PROJECT_ROOT / "state" / "trade" / "mainnet_audit.jsonl"
AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)


def audit(event: str, data: dict) -> None:
    """Append to audit log."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **data
    }
    with open(AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"  [AUDIT] {event}: {data}")


def load_secrets() -> dict:
    """Load secrets from env file."""
    secrets_path = Path(r"C:\secrets\hope.env")
    env = {}

    if not secrets_path.exists():
        raise RuntimeError(f"Secrets file not found: {secrets_path}")

    for line in secrets_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()

    return env


def get_api_keys(env: dict) -> tuple:
    """Get API keys, preferring MAINNET keys."""
    # Try MAINNET keys first
    api_key = env.get("BINANCE_MAINNET_API_KEY") or env.get("BINANCE_API_KEY")
    api_secret = env.get("BINANCE_MAINNET_API_SECRET") or env.get("BINANCE_API_SECRET")

    if not api_key or not api_secret:
        raise RuntimeError("Missing API keys in secrets")

    return api_key, api_secret


def sign_request(params: dict, api_secret: str) -> str:
    """Create HMAC SHA256 signature."""
    query = urllib.parse.urlencode(params)
    signature = hmac.new(
        api_secret.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return signature


def api_request(endpoint: str, params: dict, api_key: str, api_secret: str, method: str = "GET") -> dict:
    """Make signed API request."""
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign_request(params, api_secret)

    url = f"{BASE_URL}{endpoint}"
    if method == "GET":
        url += "?" + urllib.parse.urlencode(params)
        data = None
    else:
        data = urllib.parse.urlencode(params).encode("utf-8")

    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-MBX-APIKEY", api_key)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        raise RuntimeError(f"API Error {e.code}: {error_body}")


def get_ticker_price(symbol: str) -> float:
    """Get current price (no auth needed)."""
    url = f"{BASE_URL}/api/v3/ticker/price?symbol={symbol}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return float(data["price"])


def get_account_balance(api_key: str, api_secret: str) -> dict:
    """Get account balances."""
    result = api_request("/api/v3/account", {}, api_key, api_secret)
    balances = {}
    for b in result.get("balances", []):
        free = float(b["free"])
        if free > 0:
            balances[b["asset"]] = free
    return balances


def place_market_order(api_key: str, api_secret: str, symbol: str, side: str, quote_qty: float) -> dict:
    """Place market order with quote quantity."""
    params = {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quoteOrderQty": f"{quote_qty:.2f}",
        "newOrderRespType": "FULL",
    }
    return api_request("/api/v3/order", params, api_key, api_secret, method="POST")


def main():
    print("=" * 60)
    print("MAINNET $10 BTC PURCHASE")
    print("=" * 60)
    print()
    print("WARNING: This uses REAL MONEY on Binance MAINNET!")
    print(f"Maximum spend: ${MAX_USD}")
    print()

    audit("script_start", {"max_usd": MAX_USD, "symbol": SYMBOL})

    # Step 1: Load secrets
    print("[1] Loading API keys...")
    try:
        env = load_secrets()
        api_key, api_secret = get_api_keys(env)
        print(f"    API Key: {api_key[:8]}...{api_key[-4:]}")
        audit("keys_loaded", {"key_prefix": api_key[:8]})
    except Exception as e:
        print(f"    FAILED: {e}")
        audit("error", {"step": "load_keys", "error": str(e)})
        return 1

    # Step 2: Get current price
    print()
    print("[2] Getting BTC price...")
    try:
        btc_price = get_ticker_price(SYMBOL)
        print(f"    BTCUSDT: ${btc_price:,.2f}")
        audit("price_fetched", {"symbol": SYMBOL, "price": btc_price})
    except Exception as e:
        print(f"    FAILED: {e}")
        audit("error", {"step": "get_price", "error": str(e)})
        return 1

    # Step 3: Check balance
    print()
    print("[3] Checking USDT balance...")
    try:
        balances = get_account_balance(api_key, api_secret)
        usdt_balance = balances.get("USDT", 0)
        btc_balance = balances.get("BTC", 0)
        print(f"    USDT: {usdt_balance:.2f}")
        print(f"    BTC:  {btc_balance:.8f}")
        audit("balance_checked", {"usdt": usdt_balance, "btc": btc_balance})

        if usdt_balance < MIN_BALANCE:
            print()
            print(f"    INSUFFICIENT BALANCE!")
            print(f"    Need: ${MIN_BALANCE}, Have: ${usdt_balance:.2f}")
            audit("error", {"step": "balance_check", "error": "insufficient", "have": usdt_balance})
            return 1
    except Exception as e:
        print(f"    FAILED: {e}")
        audit("error", {"step": "get_balance", "error": str(e)})
        return 1

    # Step 4: Calculate order
    print()
    print("[4] Order details:")
    buy_amount = min(10.0, MAX_USD)  # $10 exactly
    expected_btc = buy_amount / btc_price
    print(f"    Buy: ${buy_amount:.2f} USDT worth of BTC")
    print(f"    Expected: ~{expected_btc:.8f} BTC")
    print(f"    At price: ${btc_price:,.2f}")
    audit("order_calculated", {"buy_usd": buy_amount, "expected_btc": expected_btc})

    # Step 5: Confirmation
    print()
    print("=" * 60)
    print("CONFIRMATION REQUIRED")
    print("=" * 60)
    print()
    print(f"You are about to BUY ${buy_amount:.2f} worth of BTC")
    print(f"This is REAL MONEY on MAINNET!")
    print()

    try:
        confirm = input("Type YES to confirm: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted by user.")
        audit("aborted", {"step": "confirmation", "reason": "user_interrupt"})
        return 1

    if confirm != "YES":
        print()
        print("Confirmation failed. Aborting.")
        audit("aborted", {"step": "confirmation", "reason": "not_confirmed", "input": confirm})
        return 1

    audit("confirmed", {"input": confirm})

    # Step 6: Final countdown
    print()
    print("3 seconds to abort (Ctrl+C)...")
    for i in range(3, 0, -1):
        print(f"  {i}...")
        time.sleep(1)

    # Step 7: Execute order
    print()
    print("[5] EXECUTING ORDER...")
    audit("order_submitting", {"symbol": SYMBOL, "side": "BUY", "quote_qty": buy_amount})

    try:
        result = place_market_order(api_key, api_secret, SYMBOL, "BUY", buy_amount)

        order_id = result.get("orderId")
        status = result.get("status")
        executed_qty = float(result.get("executedQty", 0))
        cummulative_quote = float(result.get("cummulativeQuoteQty", 0))

        # Calculate average price
        avg_price = cummulative_quote / executed_qty if executed_qty > 0 else 0

        print()
        print("=" * 60)
        print("ORDER EXECUTED!")
        print("=" * 60)
        print(f"    Order ID: {order_id}")
        print(f"    Status:   {status}")
        print(f"    Bought:   {executed_qty:.8f} BTC")
        print(f"    Spent:    ${cummulative_quote:.2f} USDT")
        print(f"    Avg Price: ${avg_price:,.2f}")

        audit("order_executed", {
            "order_id": order_id,
            "status": status,
            "executed_qty": executed_qty,
            "quote_qty": cummulative_quote,
            "avg_price": avg_price,
        })

        # Show fills
        fills = result.get("fills", [])
        if fills:
            print()
            print("    Fills:")
            for f in fills:
                print(f"      {f['qty']} BTC @ ${float(f['price']):,.2f} (fee: {f['commission']} {f['commissionAsset']})")

        print()
        print("SUCCESS!")
        return 0

    except Exception as e:
        print(f"    ORDER FAILED: {e}")
        audit("error", {"step": "execute_order", "error": str(e)})
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAborted by user (Ctrl+C)")
        audit("aborted", {"reason": "keyboard_interrupt"})
        sys.exit(1)
