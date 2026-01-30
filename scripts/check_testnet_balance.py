# -*- coding: utf-8 -*-
"""Quick script to check Binance Testnet balance."""
import os
import time
import hmac
import hashlib
import sys
from pathlib import Path

# Fix Windows console encoding
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

try:
    import httpx
except ImportError:
    print("Installing httpx...")
    os.system(f"{sys.executable} -m pip install httpx -q")
    import httpx

# Load testnet credentials
API_KEY = ""
API_SECRET = ""

env_file = Path("C:/secrets/hope.env")
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if line.startswith("BINANCE_TESTNET_API_KEY="):
            API_KEY = line.split("=", 1)[1].strip()
        elif line.startswith("BINANCE_TESTNET_API_SECRET="):
            API_SECRET = line.split("=", 1)[1].strip()

if not API_KEY or not API_SECRET:
    print("ERROR: Testnet credentials not found in C:/secrets/hope.env")
    sys.exit(1)

# Try Spot Testnet first, then Futures
SPOT_URL = "https://testnet.binance.vision"
FUTURES_URL = "https://testnet.binancefuture.com"

# Create signature
timestamp = int(time.time() * 1000)
query = f"timestamp={timestamp}"
signature = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

headers = {"X-MBX-APIKEY": API_KEY}

# Try Spot first
url = f"{SPOT_URL}/api/v3/account?{query}&signature={signature}"

with httpx.Client(timeout=10) as client:
    resp = client.get(url, headers=headers)
    if resp.status_code == 200:
        data = resp.json()
        print("=" * 60)
        print("BINANCE SPOT TESTNET BALANCE")
        print("=" * 60)

        # Spot format
        balances = data.get("balances", [])
        total_btc = 0
        print("BALANCES:")
        for b in balances:
            free = float(b.get("free", 0))
            locked = float(b.get("locked", 0))
            total = free + locked
            if total > 0:
                asset = b["asset"]
                print(f"  {asset}: {total:,.8f} (free: {free:,.8f}, locked: {locked:,.8f})")

        print("=" * 60)
    else:
        print(f"Spot Testnet Error: {resp.status_code}")
        # Try Futures
        print("Trying Futures Testnet...")
        url2 = f"{FUTURES_URL}/fapi/v2/account?{query}&signature={signature}"
        resp2 = client.get(url2, headers=headers)
        if resp2.status_code == 200:
            data = resp2.json()
            print("=" * 60)
            print("BINANCE FUTURES TESTNET BALANCE")
            print("=" * 60)
            print(f"Total Wallet Balance: ${float(data.get('totalWalletBalance', 0)):,.2f}")
            print(f"Available Balance:    ${float(data.get('availableBalance', 0)):,.2f}")
            print("=" * 60)
        else:
            print(f"Futures Error: {resp2.status_code}")
            print(resp2.text[:500])
