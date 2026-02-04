import os, hmac, hashlib, time, httpx
from pathlib import Path

env_file = Path('C:/secrets/hope.env')
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ[k.strip()] = v.strip()

api_key = os.environ.get('BINANCE_API_KEY', '')
api_secret = os.environ.get('BINANCE_API_SECRET', '')

print(f'API Key: {api_key[:10]}...{api_key[-4:]}' if len(api_key) > 14 else 'NOT FOUND')

ts = int(time.time() * 1000)
params = f'timestamp={ts}'
sig = hmac.new(api_secret.encode(), params.encode(), hashlib.sha256).hexdigest()

client = httpx.Client()
client.headers['X-MBX-APIKEY'] = api_key
r = client.get(f'https://api.binance.com/api/v3/account?{params}&signature={sig}')

print(f'Status: {r.status_code}')
if r.status_code == 200:
    data = r.json()
    print(f'OK! Assets: {len(data.get("balances", []))}')
    for b in [x for x in data.get("balances", []) if float(x["free"]) > 0][:5]:
        print(f'  {b["asset"]}: {b["free"]}')
else:
    print(f'Error: {r.text}')
