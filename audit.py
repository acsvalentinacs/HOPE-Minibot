import sys, os, json, py_compile, subprocess, hmac, hashlib, time, httpx, dataclasses
from pathlib import Path
from datetime import datetime

os.chdir("C:/Users/kirillDev/Desktop/TradingBot/minibot")
sys.path.insert(0, ".")

print("=" * 60)
print("HOPE P0 AUDIT")
print("=" * 60)

errors, warnings, passes = [], [], []

# Files
for f in ["core/pretrade_pipeline.py", "scripts/pump_detector.py", "scripts/autotrader.py", "scripts/eye_of_god_v3.py", "scripts/signal_schema.py"]:
    if Path(f).exists():
        try:
            py_compile.compile(f, doraise=True)
            print(f"[OK] {f}")
        except Exception as e:
            errors.append(f"{f}: {e}")
            print(f"[FAIL] {f}")

# Imports
try:
    from scripts.signal_schema import ValidatedSignal
    fields = [f.name for f in dataclasses.fields(ValidatedSignal)]
    for r in ["signal_type", "ai_override"]:
        if r in fields: print(f"[OK] ValidatedSignal.{r}")
        else: errors.append(f"MISSING: {r}"); print(f"[FAIL] {r}")
except Exception as e:
    errors.append(str(e))

# ENV
env = Path("C:/secrets/hope.env").read_text()
testnet = [l.split("=")[1] for l in env.splitlines() if l.startswith("BINANCE_TESTNET=")]
print(f"BINANCE_TESTNET: {testnet}")
if len(set(testnet)) > 1: warnings.append("Multiple BINANCE_TESTNET values!")

# API Test
for line in env.splitlines():
    if line.startswith("BINANCE_API_KEY="): api_key = line.split("=",1)[1].strip()
    if line.startswith("BINANCE_API_SECRET="): api_secret = line.split("=",1)[1].strip()

ts = int(time.time() * 1000)
sig = hmac.new(api_secret.encode(), f"timestamp={ts}".encode(), hashlib.sha256).hexdigest()
r = httpx.get(f"https://api.binance.com/api/v3/account?timestamp={ts}&signature={sig}", headers={"X-MBX-APIKEY": api_key})
if r.status_code == 200:
    data = r.json()
    print(f"[OK] API Status: 200")
    for b in [x for x in data["balances"] if float(x["free"]) > 0][:5]:
        print(f"  {b['asset']}: {b['free']}")
else:
    errors.append(f"API: {r.status_code}")
    print(f"[FAIL] API: {r.text}")

# Port
if ":8200" in subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout:
    print("[OK] AutoTrader on 8200")
else:
    warnings.append("AutoTrader not on 8200")

print(f"\nPASS:{len(passes)} WARN:{len(warnings)} ERR:{len(errors)}")
if errors: print("ERRORS:", errors)
if warnings: print("WARNINGS:", warnings)
