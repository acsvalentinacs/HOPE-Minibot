import os, hmac, hashlib, time, httpx
from pathlib import Path

for line in Path("C:/secrets/hope.env").read_text().splitlines():
    if line.startswith("BINANCE_MAINNET_API_KEY="):
        api_key = line.split("=",1)[1].strip()
    if line.startswith("BINANCE_MAINNET_API_SECRET="):
        api_secret = line.split("=",1)[1].strip()

print(f"Testing MAINNET key: {api_key[:10]}...")
ts = int(time.time() * 1000)
params = f"timestamp={ts}"
sig = hmac.new(api_secret.encode(), params.encode(), hashlib.sha256).hexdigest()
client = httpx.Client()
client.headers["X-MBX-APIKEY"] = api_key
r = client.get(f"https://api.binance.com/api/v3/account?{params}&signature={sig}")
print(f"Status: {r.status_code}")
if r.status_code == 200:
    print("SUCCESS! This key works!")
else:
    print(f"Error: {r.text}")
