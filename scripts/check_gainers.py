# -*- coding: utf-8 -*-
"""Quick script to check top gainers and specific symbols"""
import os
import sys
sys.path.insert(0, str(__file__).rsplit('\\', 2)[0])

from binance.client import Client
from dotenv import load_dotenv

load_dotenv('C:/secrets/hope.env')
client = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'))

# Check specific symbols
symbols_to_check = ['ZKCUSDT', 'XMRUSDT', 'SENTUSDT', 'DCRUSDT']
print("=== SPECIFIC SYMBOLS ===")
for sym in symbols_to_check:
    try:
        t = client.get_ticker(symbol=sym)
        print(f"{sym}: ${float(t['lastPrice']):.4f} | 24h: {float(t['priceChangePercent']):+.2f}% | Vol: ${float(t['quoteVolume'])/1e6:.1f}M")
    except:
        print(f"{sym}: not available")

# Get ALL tickers and find top gainers
print("\n=== TOP 20 GAINERS (>$5M volume) ===")
tickers = client.get_ticker()
gainers = sorted(
    [t for t in tickers if t['symbol'].endswith('USDT') and float(t['quoteVolume']) > 5_000_000],
    key=lambda x: float(x['priceChangePercent']),
    reverse=True
)[:20]

for i, t in enumerate(gainers, 1):
    print(f"{i:2}. {t['symbol']:12} {float(t['priceChangePercent']):+6.2f}% | Vol: ${float(t['quoteVolume'])/1e6:7.1f}M")

# Get most volatile (highest price range)
print("\n=== TOP 20 VOLATILE (by high-low range) ===")
volatile = sorted(
    [t for t in tickers if t['symbol'].endswith('USDT') and float(t['quoteVolume']) > 5_000_000],
    key=lambda x: (float(x['highPrice']) - float(x['lowPrice'])) / float(x['lowPrice']) if float(x['lowPrice']) > 0 else 0,
    reverse=True
)[:20]

for i, t in enumerate(volatile, 1):
    low = float(t['lowPrice'])
    high = float(t['highPrice'])
    range_pct = ((high - low) / low * 100) if low > 0 else 0
    print(f"{i:2}. {t['symbol']:12} range: {range_pct:+6.2f}% | Vol: ${float(t['quoteVolume'])/1e6:7.1f}M")

# Combined list for HOT_LIST
print("\n=== RECOMMENDED HOT_LIST (40 symbols) ===")
all_hot = set()
for t in gainers[:20]:
    all_hot.add(t['symbol'])
for t in volatile[:20]:
    all_hot.add(t['symbol'])

# Remove stablecoins
stables = {'USDCUSDT', 'FDUSDUSDT', 'TUSDUSDT', 'BUSDUSDT', 'USDPUSDT', 'EURUSDT', 'GBPUSDT'}
hot_list = sorted([s for s in all_hot if s not in stables])[:40]
print(f"Total unique: {len(hot_list)}")
print(hot_list)
