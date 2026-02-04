# -*- coding: utf-8 -*-
"""
LIVE TRADE POLICY v1.0 - Blacklist и safety rules
"""

from typing import Set, Tuple

HEAVY_COINS: Set[str] = {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"}
STABLECOINS: Set[str] = {"USDTUSDT", "USDCUSDT", "BUSDUSDT", "DAIUSDT", "FDUSDUSDT"}
DELISTED: Set[str] = {"LUNAUSDT", "USTUSDT", "FTTUSDT"}
PERFORMANCE_BLACKLIST: Set[str] = set()

MAX_POSITION_USDT = 50.0
MAX_DAILY_LOSS_USDT = 100.0
MAX_CONCURRENT_POSITIONS = 3
MIN_EFFECTIVE_RR = 2.5
MIN_TRADE_DELTA_PCT = 2.0
MIN_TELEGRAM_DELTA_PCT = 10.0

def check_symbol_allowed(symbol: str) -> Tuple[bool, str]:
    symbol = symbol.upper().strip()
    if symbol in HEAVY_COINS:
        return False, f"heavy_coin:{symbol}"
    if symbol in STABLECOINS:
        return False, f"stablecoin:{symbol}"
    if symbol in DELISTED:
        return False, f"delisted:{symbol}"
    if symbol in PERFORMANCE_BLACKLIST:
        return False, f"blacklist:{symbol}"
    return True, "allowed"

if __name__ == "__main__":
    for s in ["BTCUSDT", "PEPEUSDT", "USDTUSDT", "ENSOUSDT"]:
        ok, r = check_symbol_allowed(s)
        print(f"{'✅' if ok else '❌'} {s}: {r}")
    print("[PASS]")
