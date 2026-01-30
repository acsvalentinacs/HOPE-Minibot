# HOPE EYE OF GOD HARDENING v1.0

## Критические исправления P0

### 1. Position Watchdog (P0.4)

**Проблема:**
"Если закрытие позиции зависит от прихода новых сигналов — ты получаешь зависимость 'нет сигналов → нет управления позицией'."

**Решение:**
`position_watchdog.py` - независимый контур закрытия позиций:
- Работает в отдельном потоке/процессе
- Проверяет позиции каждую 1 секунду
- Закрывает по TIMEOUT независимо от сигналов
- PANIC CLOSE при потере связи > 60s

```
Exit Conditions:
├── TIMEOUT: now - entry_time > timeout_sec   → MARKET SELL
├── STOP: current_price <= stop_price         → MARKET SELL
├── TARGET: current_price >= target_price     → MARKET SELL
├── PANIC: no_price_for > 30s                 → MARKET SELL
└── CIRCUIT_BREAKER: daily_loss > $100        → CLOSE ALL
```

### 2. Signal Schema V1 (P0.1)

**Проблема:**
"Нет строгого контракта входного сигнала (schema). Если поля отсутствуют/NaN — скоринг даёт ложный confidence."

**Решение:**
`signal_schema.py` - строгая валидация:
- REQUIRED поля: symbol, timestamp
- TYPE CHECK: каждое поле проверяется
- RANGE CHECK: значения в допустимых диапазонах
- TTL CHECK: сигнал не старше 60 секунд

**Правило:** schema-invalid ⇒ SKIP (SIGNAL_SCHEMA_INVALID)

### 3. Signal TTL (P0.2)

**Проблема:**
"Сигнал может быть 'старым'. Система будет торговать по отработавшему импульсу."

**Решение:**
- `MAX_SIGNAL_AGE_SEC = 60` (в signal_schema.py)
- Любой сигнал старше 60 сек → SKIP (TTL_EXPIRED)

### 4. Eye of God V3 - Two-Chamber Architecture

**Проблема:**
"Whitelist как 'божественный пропуск' может обходить другие проверки."

**Решение:**
Двухпалатная архитектура:

```
┌─────────────────────────────────────────────────────────┐
│  ALPHA COMMITTEE (Хочу торговать?)                      │
│  • Precursor detection                                  │
│  • Multi-factor scoring                                 │
│  • Whitelist bonus (+15%, не override!)                │
│  OUTPUT: AlphaDecision(BUY/SKIP, confidence)           │
├─────────────────────────────────────────────────────────┤
│  RISK COMMITTEE (Разрешаю торговать?)                   │
│  • Price validity (not null, not stale)                │
│  • Liquidity check                                      │
│  • Position limits                                      │
│  • Daily loss limits                                    │
│  • Market regime                                        │
│  OUTPUT: RiskDecision(ALLOW/VETO, reasons)             │
├─────────────────────────────────────────────────────────┤
│  FINAL: Alpha=BUY AND Risk=ALLOW → BUY                 │
│         Otherwise → SKIP                                │
└─────────────────────────────────────────────────────────┘
```

**Ключевое:** Risk Committee может ВЕТО даже если Alpha говорит BUY.

---

## Файлы пакета

| Файл | Строк | Назначение |
|------|-------|------------|
| position_watchdog.py | 718 | Независимый контур закрытия |
| signal_schema.py | 590 | Валидация входных сигналов |
| eye_of_god_v3.py | 773 | Hardened Oracle с двухпалатной архитектурой |
| **TOTAL** | **2081** | |

---

## Установка

```powershell
# 1. Скопировать файлы
Copy-Item position_watchdog.py, signal_schema.py, eye_of_god_v3.py `
    -Destination C:\Users\kirillDev\Desktop\TradingBot\minibot\scripts\

# 2. Проверить синтаксис
cd C:\Users\kirillDev\Desktop\TradingBot\minibot
python -m py_compile scripts\position_watchdog.py scripts\signal_schema.py scripts\eye_of_god_v3.py
```

---

## Запуск

### Position Watchdog (отдельный процесс!)

```powershell
# Запустить watchdog (должен работать ВСЕГДА когда есть открытые позиции)
python scripts\position_watchdog.py --testnet

# Проверить статус
python scripts\position_watchdog.py --status

# Паника - закрыть все позиции
python scripts\position_watchdog.py --panic-close-all
```

### Eye of God V3

```powershell
# Тестовое решение
python scripts\eye_of_god_v3.py --test

# Статистика
python scripts\eye_of_god_v3.py --stats
```

### Signal Schema Audit

```powershell
# Аудит сигналов из JSONL
python scripts\signal_schema.py --audit state\ai\signals.jsonl --n 100

# Валидация одного сигнала
python scripts\signal_schema.py --validate '{"symbol":"BTCUSDT","timestamp":"..."}'
```

---

## Интеграция с основным движком

### 1. Регистрация позиции для watchdog

```python
from position_watchdog import register_position_for_watching

# После открытия позиции
register_position_for_watching(
    position_id=f"pos_{order_id}",
    symbol="BTCUSDT",
    entry_price=85000.0,
    quantity=0.001,
    target_pct=1.0,    # +1%
    stop_pct=-0.5,     # -0.5%
    timeout_sec=120,   # 2 минуты
)
```

### 2. Использование Eye of God V3

```python
from eye_of_god_v3 import EyeOfGodV3

eye = EyeOfGodV3(base_position_size=10.0)

# Обновить цены
eye.update_prices({"BTCUSDT": 85000, "ETHUSDT": 3000})

# Получить решение
decision = eye.decide(signal_dict)

if decision.action == "BUY":
    # Открыть позицию
    # Зарегистрировать для watchdog
else:
    # SKIP с причинами в decision.reasons
```

---

## Fail-Closed Invariants

| Условие | Результат |
|---------|-----------|
| Signal schema invalid | SKIP (SIGNAL_SCHEMA_INVALID) |
| Signal TTL > 60s | SKIP (TTL_EXPIRED) |
| Price = null | SKIP (PRICE_MISSING) |
| Price age > 30s | SKIP (PRICE_STALE) |
| Daily volume < $5M | SKIP (LOW_LIQUIDITY) |
| Open positions ≥ 3 | SKIP (MAX_POSITIONS) |
| Daily loss > $100 | SKIP (DAILY_LOSS_LIMIT) |
| Symbol in blacklist | SKIP (BLACKLIST) |
| Direction = Short | SKIP (SHORT_DISABLED) |
| No price > 30s (watchdog) | PANIC CLOSE |
| API silent > 60s (watchdog) | PANIC CLOSE ALL |

---

## State файлы

```
state/ai/
├── watchdog/
│   ├── positions.json      # Позиции под контролем watchdog
│   ├── closes.jsonl        # Лог закрытий
│   └── panic_events.jsonl  # Лог паник
└── eye_v3/
    └── decisions.jsonl     # Лог решений Eye of God V3
```

---

## Рекомендации

1. **ВСЕГДА** запускать Position Watchdog вместе с основным движком
2. Watchdog должен работать как отдельный процесс/systemd service
3. При перезапуске watchdog автоматически загружает открытые позиции
4. Использовать `--panic-close-all` в экстренных случаях

---

**Версия:** 1.0
**Автор:** Claude (opus-4)
**Дата:** 2026-01-30
**sha256:** hope_eye_hardening_v1
