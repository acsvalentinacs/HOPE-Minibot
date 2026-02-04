#!/usr/bin/env python3
"""Quick check of Binance Futures positions."""

from binance import Client
import os
from dotenv import load_dotenv

load_dotenv('C:/secrets/hope.env')

# Use MAINNET specific keys (not generic BINANCE_API_KEY)
key = os.getenv('BINANCE_MAINNET_API_KEY')
secret = os.getenv('BINANCE_MAINNET_API_SECRET')

print(f'Using MAINNET key: {key[:12]}...')

try:
    client = Client(key, secret, testnet=False)
    account = client.futures_account()
    positions = [p for p in account['positions'] if float(p['positionAmt']) != 0]

    print()
    print('=== BINANCE MAINNET FUTURES ===')
    print(f'Open positions: {len(positions)}')

    if positions:
        for p in positions:
            symbol = p['symbol']
            amt = float(p['positionAmt'])
            entry = float(p['entryPrice'])
            pnl = float(p['unrealizedProfit'])
            leverage = p.get('leverage', '?')
            side = 'LONG' if amt > 0 else 'SHORT'
            print(f'  {symbol}: {side} {abs(amt)} @ {entry:.6f} | Lev: {leverage}x | PnL: ${pnl:.2f}')
    else:
        print('  [NO OPEN POSITIONS]')

    print()
    print(f'Wallet Balance:    ${float(account["totalWalletBalance"]):.2f}')
    print(f'Available Balance: ${float(account["availableBalance"]):.2f}')
    print(f'Unrealized PnL:    ${float(account["totalUnrealizedProfit"]):.2f}')

except Exception as e:
    print(f'[ERROR] {e}')
