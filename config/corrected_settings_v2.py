# -*- coding: utf-8 -*-
"""
CORRECTED SETTINGS v2.0

Корректировки на основе анализа убытков TESTNET:
- Убыток: -$56.05
- Причина: 97% сделок закрылись по TIMEOUT без профита
- Проблема: delta была 0.01-0.22% - слишком маленькая

═══════════════════════════════════════════════════════════════════════════════
ИЗМЕНЕНИЯ:
═══════════════════════════════════════════════════════════════════════════════

1. HEAVY COINS BLACKLIST (LIVE only):
   ❌ BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT
   Причина: delta 0.01-0.10% - не подходят для скальпинга

2. PROBLEMATIC COINS BLACKLIST:
   ❌ XVSUSDT  - постоянные стопы (-0.29% до -0.60%)
   ❌ DUSKUSDT - 8 таймаутов подряд
   ❌ KITEUSDT - слабые сигналы
   ❌ PAXGUSDT - stablecoin (gold)

3. RAISED THRESHOLDS:
   - AI Score >= 0.70 для BUY (было 0.55)
   - delta >= 0.5% минимум (было 0.1%)
   - buys >= 30/s минимум (было 10)

4. TIMEOUT увеличен:
   - SCALP: 45s (было 30s)
   - STRONG: 60s (было 45s)
   Причина: даём больше времени для достижения target

═══════════════════════════════════════════════════════════════════════════════
"""

# ══════════════════════════════════════════════════════════════════════════════
# COMPLETE BLACKLIST FOR LIVE TRADING
# ══════════════════════════════════════════════════════════════════════════════

LIVE_BLACKLIST = {
    # Heavy coins - слишком медленные для скальпинга
    "BTCUSDT",   # Bitcoin
    "ETHUSDT",   # Ethereum  
    "BNBUSDT",   # Binance Coin
    "SOLUSDT",   # Solana
    "XRPUSDT",   # Ripple
    
    # Problematic coins - показали убытки на TESTNET
    "XVSUSDT",   # -0.29% до -0.60% за сделку
    "DUSKUSDT",  # 8 таймаутов подряд
    "KITEUSDT",  # слабые сигналы, без профита
    
    # Stablecoins - не торгуем
    "USDCUSDT",
    "PAXGUSDT",  # Gold-backed
    "TUSDUSDT",
    "BUSDUSDT",
    "DAIUSDT",
    "FDUSDUSDT",
}

# ══════════════════════════════════════════════════════════════════════════════
# CORRECTED THRESHOLDS
# ══════════════════════════════════════════════════════════════════════════════

CORRECTED_THRESHOLDS = {
    # Pump Detector
    "PUMP_OVERRIDE": {
        "buys_min": 100,
        "delta_min": 2.0,
        "confidence": 0.95,
    },
    "SUPER_SCALP": {
        "buys_min": 50,
        "delta_min": 1.0,
        "confidence": 0.85,
    },
    "SCALP": {
        "buys_min": 30,
        "delta_min": 0.5,
        "buy_dom_min": 60,
        "confidence": 0.75,
    },
    "VOLUME_SPIKE": {
        "volume_min": 100000,  # $100K
        "ratio_min": 2.0,
        "delta_min": 0.5,
        "confidence": 0.70,
    },
    
    # AI Predictor
    "AI_BUY_THRESHOLD": 0.70,     # Минимум для BUY
    "AI_WAIT_THRESHOLD": 0.60,    # Минимум для WAIT
    
    # HOT List
    "HOT_MIN_SCORE": 0.75,
    "HOT_MIN_BUYS": 50,
    "HOT_MIN_DELTA": 1.0,
    
    # Timeouts (увеличены)
    "TIMEOUT_SCALP": 45,          # было 30
    "TIMEOUT_STRONG": 60,         # было 45
    "TIMEOUT_EXPLOSION": 90,      # было 60
}

# ══════════════════════════════════════════════════════════════════════════════
# RECOMMENDED COINS FOR SCALPING
# ══════════════════════════════════════════════════════════════════════════════

SCALP_WHITELIST = {
    # Meme coins - высокая волатильность
    "PEPEUSDT",
    "DOGEUSDT",
    "SHIBUSDT",
    "FLOKIUSDT",
    "BONKUSDT",
    
    # Mid-cap alts - хорошая волатильность
    "SUIUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "ADAUSDT",
    "DOTUSDT",
    "MATICUSDT",
    
    # DeFi - волатильные
    "AAVEUSDT",
    "UNIUSDT",
    "CRVUSDT",
    
    # New/Hot - очень волатильные (осторожно!)
    "ENSOUSDT",
    "BIFIUSDT",
}

# ══════════════════════════════════════════════════════════════════════════════
# FUNCTION: Check if allowed for LIVE
# ══════════════════════════════════════════════════════════════════════════════

def is_allowed_for_live_trading(symbol: str) -> tuple:
    """
    Проверка для LIVE торговли.
    
    Returns:
        (allowed: bool, reason: str)
    """
    if not symbol.endswith("USDT"):
        symbol += "USDT"
        
    if symbol in LIVE_BLACKLIST:
        return False, f"Blacklisted: {symbol}"
        
    return True, "OK"


def get_all_blacklisted() -> list:
    """Получить полный blacklist."""
    return sorted(list(LIVE_BLACKLIST))


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("CORRECTED SETTINGS v2.0 - Based on TESTNET Loss Analysis")
    print("=" * 70)
    print()
    print("BLACKLIST FOR LIVE TRADING:")
    print("-" * 70)
    for coin in sorted(LIVE_BLACKLIST):
        print(f"  ❌ {coin}")
    print()
    print("WHITELIST FOR SCALPING:")
    print("-" * 70)
    for coin in sorted(SCALP_WHITELIST):
        print(f"  ✅ {coin}")
    print()
    print("CORRECTED THRESHOLDS:")
    print("-" * 70)
    for key, value in CORRECTED_THRESHOLDS.items():
        print(f"  {key}: {value}")
