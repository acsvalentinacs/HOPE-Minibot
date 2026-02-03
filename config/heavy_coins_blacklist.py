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
    # Топ по капитализации - слишком тяжёлые для скальпинга
    "BTCUSDT",   # Bitcoin - $83,000+ | delta ~0.01%/мин
    "ETHUSDT",   # Ethereum - $2,750+ | delta ~0.02%/мин
    "BNBUSDT",   # Binance Coin - $850+ | delta ~0.05%/мин | NOTIONAL проблемы
    "SOLUSDT",   # Solana - $117+ | delta ~0.05%/мин | Проблемы с балансом
    "AVAXUSDT",  # Avalanche - Исключён по указанию пользователя 2026-02-04
}

# Причины исключения
EXCLUSION_REASONS = {
    "BTCUSDT": "Market cap $1.6T+ | Moves <0.1%/min | Need $10K+ for visible profit",
    "ETHUSDT": "Market cap $330B+ | Moves <0.1%/min | Too slow for scalping",
    "BNBUSDT": "Market cap $120B+ | Low intraday volatility | NOTIONAL filter issues",
    "SOLUSDT": "Market cap $55B+ | Balance sync issues on Binance",
    "AVAXUSDT": "Excluded per user request 2026-02-04",
}

# ══════════════════════════════════════════════════════════════════════════════
# GOOD FOR SCALPING - Рекомендуемые для скальпинга
# ══════════════════════════════════════════════════════════════════════════════

SCALP_FRIENDLY_COINS = {
    # Мем-коины (высокая волатильность)
    "PEPEUSDT",   # Meme coin - высокая волатильность
    "DOGEUSDT",   # Meme coin - популярный, волатильный
    "SHIBUSDT",   # Meme coin - волатильный
    "WIFUSDT",    # Meme coin - новый
    "FLOKIUSDT",  # Meme coin
    "BONKUSDT",   # Meme coin

    # Mid-cap L1/L2 с хорошей волатильностью
    "SUIUSDT",    # New L1 - активная торговля
    "SEIUSDT",    # New L1 - волатильный
    "APTUSDT",    # L1 - хорошая волатильность
    "ARBUSDT",    # L2 - активная торговля
    "OPUSDT",     # L2 Optimism
    "INJUSDT",    # DeFi - волатильный
    "LINKUSDT",   # Oracle - средняя волатильность
    "ADAUSDT",    # L1 - средняя волатильность
    "XRPUSDT",    # Высокая ликвидность

    # DeFi (осторожно!)
    "ENSOUSDT",   # DeFi - очень волатильный
    "JUPUSDT",    # Jupiter DEX
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
