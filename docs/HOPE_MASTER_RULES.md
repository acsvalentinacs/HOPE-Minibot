# HOPE AI TRADING SYSTEM - MASTER RULES v3.0

<!-- AI SIGNATURE: Created by Claude (opus-4.5) at 2026-02-02 12:30:00 UTC -->

**Статус:** LIVE PRODUCTION | Binance MAINNET | Real Money
**Капитал:** $99.69 USDT
**Владелец:** Валентин
**AI Partner:** Claude (opus-4.5)

---

## P0: EXECUTION LAW (ВЫСШИЙ ПРИОРИТЕТ)

```
╔═══════════════════════════════════════════════════════════════════╗
║                    HOPE EXECUTION LAW v2.0                         ║
╠═══════════════════════════════════════════════════════════════════╣
║                                                                    ║
║  НЕ "МОГУ" — СДЕЛАНО!                                             ║
║  НЕ "НАПИСАНО" — ИНТЕГРИРОВАНО!                                   ║
║  НЕ "СОЗДАНО" — РАБОТАЕТ В PRODUCTION!                            ║
║                                                                    ║
╠═══════════════════════════════════════════════════════════════════╣
║  ЦИКЛ: КОД → ТЕСТ → PASS → ИНТЕГРАЦИЯ → COMMIT → VERIFY           ║
║  FAIL-CLOSED: сомнение = FAIL = ИСПРАВЬ СЕЙЧАС                    ║
╠═══════════════════════════════════════════════════════════════════╣
║  ЗАПРЕЩЕНО:                                                        ║
║  • Слова без действий                                              ║
║  • Код без теста (py_compile)                                     ║
║  • Файл без интеграции                                            ║
║  • Модуль без коммита                                             ║
╚═══════════════════════════════════════════════════════════════════╝
```

---

## P0: HONESTY CONTRACT

```
╔═══════════════════════════════════════════════════════════════════╗
║  HONESTY CONTRACT CLAUDE <-> VALENTIN                              ║
╠═══════════════════════════════════════════════════════════════════╣
║  [+] NO fake data                                                  ║
║  [+] NO stubs without marking                                      ║
║  [+] ALWAYS real data or explicit exception                        ║
║  [+] ALWAYS verify with Binance before showing positions           ║
║  [+] REAL LOSS is better than FAKE PROFIT                          ║
╚═══════════════════════════════════════════════════════════════════╝
```

---

## АРХИТЕКТУРА СИСТЕМЫ

### Торговый цикл (ПОЛНЫЙ)

```
┌─────────────────────────────────────────────────────────────────────┐
│                    HOPE TRADING CYCLE v3.0                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  [1] SCANNER (Сканер рынка)                                         │
│      ├─ pricefeed_bridge.py      → Binance WebSocket prices         │
│      ├─ pricefeed_gateway.py     → HTTP API :8100                   │
│      └─ pump_detector.py         → Volume/Delta anomalies           │
│                          ↓                                           │
│  [2] SIGNAL GENERATOR (Генератор сигналов)                          │
│      ├─ momentum_trader.py       → 24h gainers, pullbacks           │
│      ├─ unified_allowlist.py     → 3-layer filter (CORE+DYN+HOT)    │
│      └─ MoonBot Integration      → External signals                 │
│                          ↓                                           │
│  [3] EYE OF GOD V3 (AI Decision Engine) ⭐ ГЛАВНЫЙ                  │
│      ├─ Alpha Chamber            → "Хочу купить?" (confidence)      │
│      ├─ Risk Chamber             → "Можно купить?" (risk check)     │
│      ├─ ML Predictor             → Signal classification            │
│      └─ Adaptive Targets         → Dynamic TP/SL (R:R >= 2.5:1)     │
│                          ↓                                           │
│  [4] EXECUTOR (Исполнение)                                          │
│      ├─ autotrader.py :8200      → Order management                 │
│      ├─ order_executor.py        → Binance API calls                │
│      └─ position_watchdog.py     → TP/SL/Trailing enforcement       │
│                          ↓                                           │
│  [5] RISK MANAGEMENT                                                │
│      ├─ Circuit Breaker          → Auto-stop on losses              │
│      ├─ Daily Loss Limit         → Max -$15/day                     │
│      ├─ Position Limits          → Max 2 concurrent                 │
│      └─ Cooldown System          → Symbol cooldown after close      │
│                          ↓                                           │
│  [6] LEARNING & ANALYTICS                                           │
│      ├─ eye_trainer.py           → Learn from outcomes              │
│      ├─ protocol_checker.py      → System health + position sizing  │
│      └─ live_learning.py         → Real-time adaptation             │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## EYE OF GOD V3 (Центральный AI)

### Архитектура

```
┌─────────────────────────────────────────────────────────────────────┐
│                      EYE OF GOD V3                                   │
│                   "Two-Chamber Decision System"                      │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────────────┐      ┌─────────────────────┐              │
│  │   ALPHA CHAMBER     │      │    RISK CHAMBER     │              │
│  │   "Хочу купить?"    │      │   "Можно купить?"   │              │
│  ├─────────────────────┤      ├─────────────────────┤              │
│  │ • Precursor score   │      │ • AllowList check   │              │
│  │ • Delta strength    │      │ • Blacklist check   │              │
│  │ • Volume momentum   │      │ • Liquidity check   │              │
│  │ • Strategy match    │      │ • Daily loss check  │              │
│  │ • History patterns  │      │ • Position limits   │              │
│  │                     │      │ • Market regime     │              │
│  │ Output: confidence  │      │ Output: approved    │              │
│  │ (0.0 - 1.0)        │      │ (true/false)        │              │
│  └──────────┬──────────┘      └──────────┬──────────┘              │
│             │                            │                          │
│             └────────────┬───────────────┘                          │
│                          │                                          │
│                          ▼                                          │
│             ┌─────────────────────┐                                 │
│             │   FINAL DECISION    │                                 │
│             ├─────────────────────┤                                 │
│             │ confidence >= 65%   │ → Regular trade                 │
│             │ confidence >= 40%   │ → Momentum trade (if override)  │
│             │ confidence < 40%    │ → SKIP (даже с override!)       │
│             └─────────────────────┘                                 │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### Пороги (текущие)

| Параметр | Значение | Назначение |
|----------|----------|------------|
| MIN_CONFIDENCE_TO_TRADE | 0.65 | Обычные сигналы |
| MIN_CONFIDENCE_MOMENTUM | 0.40 | Momentum сигналы (NEW!) |
| MAX_OPEN_POSITIONS | 2 | Лимит позиций |
| MAX_DAILY_LOSS_USD | 15.0 | Дневной лимит убытка |
| MIN_DAILY_VOLUME_M | 10.0 | Минимальный объём |

---

## КОМПОНЕНТЫ AI/ML

### 1. SCANNER (Anomaly Detection)

```python
# Файлы: pump_detector.py, pricefeed_bridge.py

Методы:
- Volume Spike Detection: vol_now > vol_avg * 3
- Delta Analysis: (buys - sells) / total > threshold
- Price Momentum: change_1h > 2% + change_24h > 8%

Выход: Raw signals для Eye of God
```

### 2. SIGNAL CLASSIFIER (ML)

```python
# Файл: ai_gateway/modules/predictor/signal_classifier.py

Методы:
- Feature extraction (RSI, MACD, Volume, BTC correlation)
- Pattern matching (historical outcomes)
- Confidence scoring

Выход: signal_confidence (0.0 - 1.0)
```

### 3. ADAPTIVE TARGET ENGINE

```python
# Файл: eye_of_god_v3.py (AdaptiveTargetEngine class)

Методы:
- Volatility-based TP/SL
- Momentum-adjusted targets
- Market regime detection

Правило: R:R >= 2.5:1 (TP должен быть в 2.5x больше SL)
```

### 4. POSITION SIZER (Dynamic)

```python
# Файл: protocol_checker.py (CyclicTradingProtocol class)

Формула:
position = balance × 20% × confidence_mult × loss_adj × compound_mult

Где:
- confidence >= 85%: × 1.25
- confidence >= 75%: × 1.00
- confidence >= 65%: × 0.75
- consecutive_losses >= 3: × 0.50
- growth >= 20%: compound bonus × 1.20
```

### 5. LEARNING MODULE

```python
# Файл: eye_trainer.py

Функции:
- --stats: показать статистику (WR, PnL, лучшие часы)
- --train: обучить на исторических данных
- --analyze: анализ конкретного сигнала

Текущие результаты:
- 50 trades, 52% Win Rate
- Best delta: 0-2% (64.5% WR)
- Best buys/sec: 50+ (100% WR)
```

---

## КОМАНДЫ УПРАВЛЕНИЯ

### Запуск системы

```powershell
cd C:\Users\kirillDev\Desktop\TradingBot\minibot

# 1. Pricefeed Gateway
Start-Process python -ArgumentList "scripts/pricefeed_gateway.py"

# 2. AutoTrader (LIVE)
Start-Process python -ArgumentList "scripts/autotrader.py","--mode","LIVE","--yes","--confirm"

# 3. Momentum Trader (по запросу)
python scripts/momentum_trader.py --once
```

### Мониторинг

```powershell
# Статус AutoTrader
curl http://127.0.0.1:8200/status

# Protocol Check
python scripts/protocol_checker.py --once

# Eye Trainer статистика
python scripts/eye_trainer.py --stats

# Обучение
python scripts/eye_trainer.py --train
```

### Проверки (ОБЯЗАТЕЛЬНЫЕ)

```powershell
# Синтаксис
python -m py_compile scripts/autotrader.py

# Импорты
python -c "from scripts.eye_of_god_v3 import EyeOfGodV3; print('OK')"

# Git
git status
git add <files>
git commit -m "type(scope): description"
git push
```

---

## СТРУКТУРА ФАЙЛОВ

```
minibot/
├── scripts/                    # Исполняемые модули
│   ├── autotrader.py          # Главный торговый движок :8200
│   ├── eye_of_god_v3.py       # AI Decision Engine
│   ├── momentum_trader.py     # Momentum сигналы
│   ├── pricefeed_gateway.py   # HTTP API :8100
│   ├── protocol_checker.py    # Health + sizing
│   ├── eye_trainer.py         # ML обучение
│   └── position_watchdog.py   # TP/SL enforcement
│
├── core/                       # Ядро системы
│   ├── unified_allowlist.py   # 3-layer AllowList
│   ├── io_atomic.py           # Атомарная запись
│   └── secrets.py             # Загрузка ключей
│
├── config/
│   └── scalping_100.json      # Конфиг для $100
│
├── state/                      # Состояние
│   ├── ai/                    # AI состояние
│   │   ├── eye_v3/decisions.jsonl
│   │   ├── autotrader/positions.json
│   │   └── models/            # Обученные модели
│   └── allowlist/             # AllowList состояние
│
├── docs/
│   ├── SESSION_RESTORE.md     # Восстановление контекста
│   ├── CYCLIC_TRADING_PROTOCOL.md
│   └── HOPE_MASTER_RULES.md   # ЭТОТ ФАЙЛ
│
└── tools/
    └── start_hope_trading.ps1 # Скрипт запуска
```

---

## TASK COMPLETION FORMAT (ОБЯЗАТЕЛЬНЫЙ)

После каждой задачи:

```markdown
=== TASK COMPLETION ===

Task: [краткое описание]
Result: PASS | FAIL

✅ СДЕЛАНО:
- [конкретный результат с файлом/строкой]

❌ ОШИБКИ:
- [если есть]

❓ ТРЕБУЕТ УТОЧНЕНИЯ:
- [если есть]

COMMITS:
- abc1234 type(scope): description

VERIFICATION:
- py_compile: PASS
- import test: PASS
- curl status: PASS
```

---

## CHECKLIST ПЕРЕД ЛЮБЫМ ИЗМЕНЕНИЕМ

```
□ Прочитал файл перед редактированием
□ Понимаю что делает существующий код
□ py_compile PASS
□ import test PASS
□ Проверил вызывающие модули (grep)
□ Протестировал в реальном окружении
□ git commit с правильным форматом
□ git push
□ Система РАБОТАЕТ после изменений
```

---

## ЗАПРЕЩЁННЫЕ ДЕЙСТВИЯ

| Действие | Почему запрещено |
|----------|------------------|
| `os.remove()` на AI файлы | Потеря работы других AI |
| Изменение `.env` | Только append-only |
| `git push --force` | Потеря истории |
| Код без `py_compile` | Может сломать production |
| Показ данных без верификации | Фейковые позиции |
| ai_override без min confidence | Убыточные сделки |

---

## МЕТРИКИ КАЧЕСТВА (KPIs)

| Метрика | Target | Critical | Action |
|---------|--------|----------|--------|
| Win Rate | >= 55% | < 45% | STOP + Review |
| R:R Ratio | >= 2.5:1 | < 2:1 | Adjust targets |
| Daily PnL | > $0 | < -$15 | STOP trading |
| Max Drawdown | < 15% | > 20% | STOP + Review |
| Confidence accuracy | >= 60% | < 50% | Retrain model |

---

**Этот документ — ЕДИНЫЙ ИСТОЧНИК ПРАВДЫ для проекта HOPE.**
**Все участники (Claude, Валентин) следуют этим правилам.**

**Дата обновления:** 2026-02-02
**Версия:** 3.0
