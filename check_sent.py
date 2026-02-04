import os, hmac, hashlib, time, httpx
from pathlib import Path

for line in Path("C:/secrets/hope.env").read_text().splitlines():
    if line.startswith("BINANCE_API_KEY="): api_key = line.split("=",1)[1].strip()
    if line.startswith("BINANCE_API_SECRET="): api_secret = line.split("=",1)[1].strip()

ts = int(time.time() * 1000)
params = f"symbol=SENTUSDT&timestamp={ts}"
sig = hmac.new(api_secret.encode(), params.encode(), hashlib.sha256).hexdigest()

client = httpx.Client()
client.headers["X-MBX-APIKEY"] = api_key

# История ордеров
r = client.get(f"https://api.binance.com/api/v3/myTrades?{params}&signature={sig}")
if r.status_code == 200:
    trades = r.json()
    print(f"=== SENT TRADES ({len(trades)}) ===")
    for t in trades[-5:]:
        side = "BUY" if t["isBuyer"] else "SELL"
        print(f"{t['time']} | {side} | {t['qty']} @ {t['price']} | total: {t['quoteQty']}")
else:
    print(f"Error: {r.text}")

# Текущий баланс SENT
ts2 = int(time.time() * 1000)
params2 = f"timestamp={ts2}"
sig2 = hmac.new(api_secret.encode(), params2.encode(), hashlib.sha256).hexdigest()
r2 = client.get(f"https://api.binance.com/api/v3/account?{params2}&signature={sig2}")
if r2.status_code == 200:
    for b in r2.json()["balances"]:
        if b["asset"] == "SENT":
            print(f"\nSENT Balance: {b['free']} (locked: {b['locked']})")
            break
    else:
        print("\nSENT Balance: 0 (not found)")
