# -*- coding: utf-8 -*-
"""
HEAVY COINS BLACKLIST for LIVE Trading

Эти монеты ИСКЛЮЧЕНЫ из торговли на реальные деньги:
- Слишком тяжёлые (высокая капитализация)
- Низкая волатильность внутри дня
- delta 0.01-0.10% - не подходят для скальпинга

По указанию пользователя от 30.01.2026.
"""

# ══════════════════════════════════════════════════════════════════════════════
# HEAVY COINS - НЕ ТОРГОВАТЬ НА LIVE!
# ══════════════════════════════════════════════════════════════════════════════

HEAVY_COINS_BLACKLIST = {
    # Топ-5 по капитализации - слишком тяжёлые для скальпинга
    "BTCUSDT",   # Bitcoin - $83,000+ | delta ~0.01%/мин
    "ETHUSDT",   # Ethereum - $2,750+ | delta ~0.02%/мин  
    "BNBUSDT",   # Binance Coin - $850+ | delta ~0.05%/мин
    "SOLUSDT",   # Solana - $117+ | delta ~0.05%/мин
    "XRPUSDT",   # Ripple - $0.50+ | delta ~0.05%/мин
}

# Причины исключения
EXCLUSION_REASONS = {
    "BTCUSDT": "Market cap $1.6T+ | Moves <0.1%/min | Need $10K+ for visible profit",
    "ETHUSDT": "Market cap $330B+ | Moves <0.1%/min | Too slow for scalping",
    "BNBUSDT": "Market cap $120B+ | Low intraday volatility",
    "SOLUSDT": "Market cap $55B+ | Slower than smaller alts",
    "XRPUSDT": "Market cap $25B+ | Low volatility periods",
}

# ══════════════════════════════════════════════════════════════════════════════
# GOOD FOR SCALPING - Рекомендуемые для скальпинга
# ══════════════════════════════════════════════════════════════════════════════

SCALP_FRIENDLY_COINS = {
    # Mid-cap с хорошей волатильностью
    "PEPEUSDT",   # Meme coin - высокая волатильность
    "DOGEUSDT",   # Meme coin - популярный, волатильный
    "SHIBUSDT",   # Meme coin - волатильный
    "SUIUSDT",    # New L1 - активная торговля
    "AVAXUSDT",   # L1 - хорошая волатильность
    "LINKUSDT",   # Oracle - средняя волатильность
    "ADAUSDT",    # L1 - средняя волатильность
    
    # Small-cap с высокой волатильностью (осторожно!)
    "ENSOUSDT",   # DeFi - очень волатильный
    "BIFIUSDT",   # DeFi - волатильный
}

# ══════════════════════════════════════════════════════════════════════════════
# FUNCTION: Check if coin is allowed for LIVE trading
# ══════════════════════════════════════════════════════════════════════════════

def is_allowed_for_live(symbol: str) -> tuple:
    """
    Проверить, разрешена ли монета для LIVE торговли.
    
    Returns:
        (allowed: bool, reason: str)
    """
    if not symbol.endswith("USDT"):
        symbol += "USDT"
        
    if symbol in HEAVY_COINS_BLACKLIST:
        reason = EXCLUSION_REASONS.get(symbol, "Heavy coin - excluded from LIVE")
        return False, reason
        
    return True, "OK"


def get_heavy_coins_list() -> list:
    """Получить список тяжёлых монет."""
    return list(HEAVY_COINS_BLACKLIST)


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("HEAVY COINS BLACKLIST (excluded from LIVE trading)")
    print("=" * 60)
    print()
    for coin in HEAVY_COINS_BLACKLIST:
        reason = EXCLUSION_REASONS.get(coin, "")
        print(f"  ❌ {coin}: {reason}")
    print()
    print("=" * 60)
    print("SCALP-FRIENDLY COINS (recommended)")
    print("=" * 60)
    for coin in SCALP_FRIENDLY_COINS:
        print(f"  ✅ {coin}")
