<!-- AI SIGNATURE: Created by Claude (opus-4) at 2026-01-25T16:45:00Z -->
<!-- AI SIGNATURE: Modified by Claude (opus-4) at 2026-01-25T17:45:00Z -->

# LIVE Trading System

## Overview

LIVE Trading System - fail-closed торговый контур для HOPE.

**КРИТИЧЕСКИ ВАЖНО:**
- MAINNET по умолчанию ЗАБЛОКИРОВАН
- Требуется явное подтверждение через env vars
- Любая ошибка = REJECT (fail-closed)

## Архитектура

```
run_live_trading.py (entrypoint)
    │
    ├── policy_preflight  ─┐
    ├── verify_stack      ─┼── Commit Gates
    ├── runtime_smoke     ─┘
    │
    ├── core/trade/live_gate.py      ─── MAINNET Access Control
    ├── core/trade/risk_engine.py    ─── Risk Validation
    ├── core/trade/order_audit.py    ─── Append-only Audit
    ├── core/trade/order_router.py   ─── Order Execution
    └── core/trade/position_tracker.py ─ Portfolio Snapshot
```

## Включение LIVE Trading

### Шаг 1: Проверка готовности

```powershell
cd C:\Users\kirillDev\Desktop\TradingBot\minibot
python tools/live_smoke_gate.py --mode DRY
```

Все 7 проверок должны быть PASS.

### Шаг 2: Тест на TESTNET

```powershell
python run_live_trading.py --mode TESTNET --symbol BTCUSDT --side BUY --size-usd 10
```

### Шаг 3: Включение MAINNET

Установите environment variables:

```powershell
# PowerShell
$env:HOPE_LIVE_ENABLE = "YES"
$env:HOPE_LIVE_ACK = "I_KNOW_WHAT_I_AM_DOING"
```

Или в cmd:
```cmd
set HOPE_LIVE_ENABLE=YES
set HOPE_LIVE_ACK=I_KNOW_WHAT_I_AM_DOING
```

### Шаг 4: Выполнение MAINNET ордера

```powershell
python run_live_trading.py --mode MAINNET --symbol BTCUSDT --side BUY --size-usd 50 --once
```

## Режимы работы

| Режим | Описание | Реальные деньги |
|-------|----------|-----------------|
| DRY | Только расчёты и логирование | Нет |
| TESTNET | Ордера на testnet.binance.vision | Нет |
| MAINNET | Ордера на api.binance.com | **ДА** |

## Гейты безопасности

### 1. policy_preflight
Проверяет наличие SSoT evidence:
- `schema_version` = "spider_health_v1"
- `cmdline_ssot.sha256` присутствует
- `run_id` содержит `__cmd=` binding

### 2. verify_stack
Проверяет SSoT cmdline модуль.

### 3. runtime_smoke
Проверяет синтаксис всех .py файлов в core/.

### 4. live_gate
Финальный барьер MAINNET:
- `HOPE_LIVE_ENABLE == "YES"`
- `HOPE_LIVE_ACK == "I_KNOW_WHAT_I_AM_DOING"`
- Credentials present
- Evidence valid
- Kill-switch not active
- Target host in allowlist

## Risk Engine Лимиты

Дефолтные лимиты (fail-closed):

| Параметр | Значение | Описание |
|----------|----------|----------|
| MAX_DAILY_LOSS_PCT | 0.5% | Дневной лимит убытка |
| RISK_PER_TRADE_PCT | 0.10% | Риск на сделку |
| MAX_OPEN_POSITIONS | 1 | Макс. открытых позиций |
| MAX_ORDERS_PER_MIN | 6 | Rate limit |
| MAX_CONSECUTIVE_LOSSES | 3 | Макс. подряд убытков |
| MAX_NOTIONAL_PCT | 5% | Макс. размер ордера |
| MIN_EQUITY_USD | 50 | Мин. баланс |

## Delisting Protection

Автоматическая защита от делистингов. Система сканирует новости Binance и блокирует торговлю символами, которые объявлены к делистингу.

### Как работает

1. DelistingDetector анализирует заголовки новостей на ключевые слова:
   - "will delist", "delisting of", "remove trading pair"
   - "suspension trading", "cease trading"

2. При обнаружении делистинга символ автоматически блокируется

3. Order Router проверяет блокировку перед каждым ордером

### CLI команды

```powershell
# Статус детектора
python -m core.trade.delisting_detector status

# Сканировать новости
python -m core.trade.delisting_detector scan

# Проверить символ
python -m core.trade.delisting_detector check LUNAUSDT

# Ручная блокировка
python -m core.trade.delisting_detector block LUNA

# Ручная разблокировка
python -m core.trade.delisting_detector unblock LUNA
```

### Файлы

| Файл | Назначение |
|------|------------|
| `state/blocked_symbols.json` | Заблокированные символы |
| `state/audit/delisting_events.jsonl` | Аудит событий делистинга |

## Kill Switch

### Активация (блокирует ВСЕ ордера)

```powershell
python -m core.trade.risk_engine kill "Причина"
```

### Деактивация

```powershell
python -m core.trade.risk_engine unkill
```

### Статус

```powershell
python -m core.trade.risk_engine status
```

## Audit Логи

Все события записываются в:

| Файл | Содержимое |
|------|------------|
| `state/audit/orders.jsonl` | ORDER_INTENT, ORDER_REJECT, ORDER_SUBMIT, ORDER_ACK, ORDER_FILL, ORDER_ERROR |
| `state/audit/risk_decisions.jsonl` | RISK_CHECK, KILL_ON, KILL_OFF |

### Формат записи

```json
{
  "event": "ORDER_INTENT",
  "ts_utc": "2026-01-25T16:00:00+00:00",
  "schema_version": "order_audit_v1",
  "run_id": "live_v1__ts=20260125T160000Z__pid=12345__nonce=abc123__cmd=def456",
  "cmdline_sha256": "abc123...",
  "symbol": "BTCUSDT",
  "side": "BUY",
  "mode": "MAINNET",
  "dry_run": false,
  "size_usd": 100.0
}
```

## Fail-Closed Правила

1. **Нет данных = REJECT**
   - Не удалось получить equity
   - Не удалось получить цену
   - Не удалось записать audit

2. **Любая ошибка = REJECT**
   - Exception в любом гейте
   - Network timeout
   - Exchange rejection

3. **Audit failure = STOP**
   - Если не удалось записать в audit, торговля останавливается

4. **Kill-switch = REJECT всех ордеров**
   - Активируется автоматически при превышении лимитов
   - Деактивируется только вручную

## Команды CLI

```powershell
# Smoke test
python tools/live_smoke_gate.py --mode DRY

# Статус торговой системы
python -m core.trade.order_router status

# Статус риск-менеджера
python -m core.trade.risk_engine status

# Статус LIVE gate
python -m core.trade.live_gate status

# DRY тест ордера
python run_live_trading.py --mode DRY --symbol BTCUSDT --side BUY --size-usd 100

# TESTNET ордер
python run_live_trading.py --mode TESTNET --symbol BTCUSDT --side BUY --size-usd 50

# MAINNET ордер (требует env vars!)
python run_live_trading.py --mode MAINNET --symbol BTCUSDT --side BUY --size-usd 50 --once
```

## Troubleshooting

### "REJECTED_NO_LIVE_ENABLE"
Установите `HOPE_LIVE_ENABLE=YES`

### "REJECTED_NO_LIVE_ACK"
Установите `HOPE_LIVE_ACK=I_KNOW_WHAT_I_AM_DOING`

### "REJECTED_INVALID_EVIDENCE"
Запустите spider для генерации evidence:
```powershell
python -m core.spider collect --mode lenient --dry-run
```

### "REJECT_KILL_SWITCH"
Kill-switch активен. Деактивируйте:
```powershell
python -m core.trade.risk_engine unkill
```

### "REJECT_MIN_EQUITY"
Баланс ниже минимального лимита (50 USD по умолчанию).

## Evidence файлы

| Файл | Назначение |
|------|------------|
| `state/health/spider_health.json` | Spider evidence (SSoT) |
| `state/health/live_trade.json` | Trade evidence |
| `state/trade_risk_state.json` | Risk engine state |

## Безопасность

1. **Секреты**: `C:\secrets\hope\.env` (append-only!)
2. **Allowlist**: `config/AllowList.spider.txt`
3. **Прямые сетевые вызовы**: ЗАПРЕЩЕНЫ в core/trade/
4. **Логирование секретов**: ЗАПРЕЩЕНО
