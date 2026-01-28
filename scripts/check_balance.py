# Quick balance check
import json, time, hashlib, hmac, urllib.request, urllib.parse
from pathlib import Path

secrets = {}
for line in Path(r"C:\secrets\hope.env").read_text().splitlines():
    if line.strip() and "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        secrets[k.strip()] = v.strip()

api_key = secrets.get("BINANCE_MAINNET_API_KEY") or secrets.get("BINANCE_API_KEY")
api_secret = secrets.get("BINANCE_MAINNET_API_SECRET") or secrets.get("BINANCE_API_SECRET")

params = {"timestamp": int(time.time() * 1000)}
query = urllib.parse.urlencode(params)
params["signature"] = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()

url = "https://api.binance.com/api/v3/account?" + urllib.parse.urlencode(params)
req = urllib.request.Request(url)
req.add_header("X-MBX-APIKEY", api_key)

with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read())
    print("Current Balances:")
    for b in data["balances"]:
        free = float(b["free"])
        if free > 0:
            print(f"  {b['asset']}: {free}")
