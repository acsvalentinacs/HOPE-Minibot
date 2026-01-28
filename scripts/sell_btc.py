# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T16:20:00Z
# Purpose: Sell BTC back to USDT
# === END SIGNATURE ===
"""Quick BTC sell script."""
import json
import time
import hashlib
import hmac
import urllib.request
import urllib.parse
from pathlib import Path

# Load secrets
secrets = {}
env_path = Path(r"C:\secrets\hope.env")
for line in env_path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        secrets[k.strip()] = v.strip()

# Prefer MAINNET keys
API_KEY = secrets.get("BINANCE_MAINNET_API_KEY") or secrets.get("BINANCE_API_KEY")
API_SECRET = secrets.get("BINANCE_MAINNET_API_SECRET") or secrets.get("BINANCE_API_SECRET")
BASE_URL = "https://api.binance.com"

if not API_KEY or not API_SECRET:
    print("ERROR: Missing API keys!")
    exit(1)

def sign(params: dict) -> str:
    query = urllib.parse.urlencode(params)
    sig = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    return query + "&signature=" + sig

def api_get(endpoint: str, params: dict = None) -> dict:
    params = params or {}
    params["timestamp"] = int(time.time() * 1000)
    url = f"{BASE_URL}{endpoint}?{sign(params)}"
    req = urllib.request.Request(url, headers={"X-MBX-APIKEY": API_KEY})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())

def api_post(endpoint: str, params: dict) -> dict:
    params["timestamp"] = int(time.time() * 1000)
    data = sign(params).encode()
    req = urllib.request.Request(
        f"{BASE_URL}{endpoint}",
        data=data,
        headers={"X-MBX-APIKEY": API_KEY},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())

# Get BTC balance
print("Checking BTC balance...")
account = api_get("/api/v3/account")
btc_balance = 0.0
for b in account.get("balances", []):
    if b["asset"] == "BTC":
        btc_balance = float(b["free"])
        break

print(f"BTC balance: {btc_balance}")

if btc_balance < 0.00001:
    print("No BTC to sell!")
    exit(0)

# Get current price
ticker = None
req = urllib.request.Request(f"{BASE_URL}/api/v3/ticker/price?symbol=BTCUSDT")
with urllib.request.urlopen(req, timeout=5) as resp:
    ticker = json.loads(resp.read().decode())
price = float(ticker["price"])
print(f"Current price: ${price:,.2f}")

# Calculate value
value = btc_balance * price
print(f"Value: ${value:.2f}")

# Confirm
print("\n" + "=" * 50)
print(f"SELL {btc_balance} BTC @ ~${price:,.2f} = ${value:.2f}")
print("=" * 50)
confirm = input("Type YES to confirm: ").strip().upper()

if confirm != "YES":
    print("Cancelled.")
    exit(0)

# Execute market sell
print("\nExecuting SELL order...")
order = api_post("/api/v3/order", {
    "symbol": "BTCUSDT",
    "side": "SELL",
    "type": "MARKET",
    "quantity": f"{btc_balance:.8f}".rstrip('0').rstrip('.'),
})

print("\n" + "=" * 50)
print("ORDER EXECUTED")
print("=" * 50)
print(f"Order ID: {order.get('orderId')}")
print(f"Status: {order.get('status')}")
print(f"Sold: {order.get('executedQty')} BTC")
if order.get("fills"):
    total_usdt = sum(float(f["qty"]) * float(f["price"]) for f in order["fills"])
    print(f"Received: ${total_usdt:.2f} USDT")
print("=" * 50)
