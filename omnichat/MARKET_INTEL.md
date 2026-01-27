# Market Intelligence Module

## Обзор

Market Intelligence — модуль реального времени для получения и анализа криптовалютных данных.

**Версия:** v1.8
**Горячая клавиша:** `Ctrl+M`

## Источники данных

| Источник | URL | Данные |
|----------|-----|--------|
| Binance API | api.binance.com/api/v3/ticker/24hr | Цены, объёмы, изменения 24h |
| CoinGecko API | api.coingecko.com/api/v3/global | Market Cap, Dominance, Sentiment |
| Cointelegraph RSS | cointelegraph.com/rss | Новости |
| CoinDesk RSS | coindesk.com/arc/outboundfeeds/rss/ | Новости |
| Decrypt RSS | decrypt.co/feed | Новости |

## Отслеживаемые активы

- BTCUSDT (Bitcoin)
- ETHUSDT (Ethereum)
- BNBUSDT (Binance Coin)
- SOLUSDT (Solana)
- XRPUSDT (Ripple)
- ADAUSDT (Cardano)
- DOGEUSDT (Dogecoin)
- AVAXUSDT (Avalanche)
- DOTUSDT (Polkadot)
- LINKUSDT (Chainlink)

## Структура данных

### MarketSnapshot
```python
@dataclass
class MarketSnapshot:
    snapshot_id: str        # sha256:xxxx - верификация целостности
    timestamp: datetime     # UTC
    tickers: dict[str, TickerData]
    global_metrics: GlobalMetrics
    news: list[NewsItem]
    source_urls: list[str]
    fetch_duration_ms: int
    errors: list[str]
```

### TickerData
```python
@dataclass
class TickerData:
    symbol: str
    price: float
    price_change_pct: float
    volume: float
    quote_volume: float
    high_24h: float
    low_24h: float
    timestamp: datetime
```

### GlobalMetrics
```python
@dataclass
class GlobalMetrics:
    total_market_cap_usd: float
    total_volume_24h_usd: float
    btc_dominance_pct: float
    eth_dominance_pct: float
    market_cap_change_24h_pct: float
    active_cryptocurrencies: int
    timestamp: datetime
    sentiment: Sentiment  # extreme_fear/fear/neutral/greed/extreme_greed
```

### NewsItem
```python
@dataclass
class NewsItem:
    title: str
    source: str
    url: str
    published_at: datetime
    summary: str
    impact: ImpactScore   # CRITICAL/HIGH/MEDIUM/LOW/NOISE
    keywords: tuple[str, ...]
    sentiment_score: float
```

## Impact Scoring

| Score | Значение | Примеры |
|-------|----------|---------|
| 🔴 CRITICAL (1.0) | Рынок движется | Hack, SEC lawsuit, ETF approved/rejected, Ban |
| 🟠 HIGH (0.8) | Значительное | Partnership, Listing, Protocol upgrade |
| 🟡 MEDIUM (0.5) | Заметное | Adoption news, Analyst reports |
| 🟢 LOW (0.3) | Минорное | Opinions, Minor updates |
| ⚪ NOISE (0.1) | Шум | Promotional, Repetitive |

## Файлы модуля

```
omnichat/src/market_intel/
├── __init__.py      # Экспорты
├── types.py         # Типы данных (TickerData, GlobalMetrics, NewsItem, MarketSnapshot)
├── fetcher.py       # Async HTTP fetcher (Binance, CoinGecko, RSS)
├── analyzer.py      # News analysis, impact scoring, sentiment
└── intel.py         # Main orchestrator, atomic persistence
```

## Принципы реализации

### Fail-Closed
```python
# Любая ошибка → FetchError, не partial data
if not tickers:
    raise FetchError("Critical: No ticker data available")
```

### Atomic Writes
```python
def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)  # Atomic rename
```

### SHA256 Verification
```python
snapshot_id = f"sha256:{hashlib.sha256(data).hexdigest()[:16]}"
# Пример: sha256:50ea0d6fd7c13283
```

### TTL (Time-To-Live)
- Market data: 5 минут
- News: 15 минут

## Persistence

| Файл | Формат | Назначение |
|------|--------|------------|
| state/market_intel.json | JSON | Кэш последнего snapshot |
| state/market_intel_history.jsonl | JSONL | История всех snapshot |

## Использование в коде

```python
from omnichat.src.market_intel import MarketIntel, fetch_market_snapshot

# Вариант 1: Быстрый вызов
snapshot = await fetch_market_snapshot()

# Вариант 2: С контролем
intel = MarketIntel()
snapshot = await intel.get_snapshot(max_age_seconds=60, force_refresh=True)

# Получить алерты
alerts = intel.get_alerts(snapshot)

# Получить сводку
summary = intel.get_summary(snapshot)
# {'overall_sentiment': 'neutral', 'confidence': 0.5, 'recommendation': '...'}

# Форматированный вывод
print(intel.format_snapshot(snapshot))
```

## TUI интеграция

### Горячие клавиши
- `Ctrl+M` — открыть Market Intel
- `R` — обновить данные
- `Escape` — закрыть

### Экран показывает
1. **💰 TOP ASSETS** — цены с % изменения (🟢 рост / 🔴 падение)
2. **🌍 GLOBAL** — Market Cap, Volume, BTC/ETH Dominance, Sentiment
3. **📰 NEWS** — последние новости с impact scoring
4. **⚠️ ALERTS** — значимые события (большие движения, волатильность)
5. **📈 SUMMARY** — общий sentiment + рекомендация

## Пример вывода

```
==================================================
📊 MARKET INTEL - 2026-01-27 15:20 UTC
ID: sha256:50ea0d6fd7c13283
==================================================

💰 TOP ASSETS:
  🔴 BTCUSDT: $87,820.94 (-0.88%)
  🔴 ETHUSDT: $2,925.71 (-0.46%)
  🟢 BNBUSDT: $884.36 (+0.88%)
  🔴 SOLUSDT: $123.87 (-0.48%)
  🔴 XRPUSDT: $1.88 (-2.62%)

🌍 GLOBAL:
  Market Cap: $3.06T
  24h Volume: $114.0B
  BTC Dom: 57.3%
  Change 24h: +0.17%
  Sentiment: neutral

📰 TOP NEWS (30 items):
  🔵 [cointelegraph] Bitcoin price due sub-$80K bottom...
  🔵 [coindesk] Rick Rieder, rising favorite for Fed chair...
  🔴 [coindesk] HYPE token surges 24% as silver futures...

==================================================
```

---

*Документация Market Intelligence v1.8 - HOPE OMNI-CHAT*
