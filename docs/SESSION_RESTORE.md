# HOPE AI Trading System - Session Restore

<!-- AI SIGNATURE: Created by Claude (opus-4.5) at 2026-01-31 14:30:00 UTC -->

## КРИТИЧЕСКАЯ ИНФОРМАЦИЯ ДЛЯ CLAUDE

При начале новой сессии — ПРОЧИТАЙ ЭТОТ ФАЙЛ ПЕРВЫМ!

---

## 1. ПРОЕКТ

```
Название:     HOPE AI Trading System
Владелец:     Валентин (kirillDev - это username Windows, не имя)
Статус:       LIVE PRODUCTION
Биржа:        Binance (РЕАЛЬНЫЕ ДЕНЬГИ)
Капитал:      $100
Режим:        24/7 автоматическая торговля
```

---

## 2. ПУТИ (КРИТИЧЕСКИ ВАЖНО)

```
КОРЕНЬ ПРОЕКТА:    C:\Users\kirillDev\Desktop\TradingBot\minibot
SECRETS (.env):    C:\secrets\hope.env
STATE FILES:       C:\Users\kirillDev\Desktop\TradingBot\minibot\state\
CONFIG:            C:\Users\kirillDev\Desktop\TradingBot\minibot\config\
SCRIPTS:           C:\Users\kirillDev\Desktop\TradingBot\minibot\scripts\
CORE:              C:\Users\kirillDev\Desktop\TradingBot\minibot\core\
АРХИВ:             C:\Users\kirillDev\Desktop\TradingBot\Старые файлы от проекта НОРЕ 2025-11-23
```

**ПЕРЕД ЛЮБОЙ КОМАНДОЙ:**
```powershell
cd C:\Users\kirillDev\Desktop\TradingBot\minibot
```

---

## 3. КЛЮЧЕВЫЕ ФАЙЛЫ

### Конфигурация
| Файл | Назначение |
|------|------------|
| `config/scalping_100.json` | Настройки скальпинга для $100 |
| `data/AllowList.txt` | Разрешённые символы (старый формат) |
| `state/allowlist/hot_list.json` | HOT_LIST для pump-сигналов |
| `state/hot_list.json` | Горячий список монет |

### Основные скрипты
| Файл | Порт | Назначение |
|------|------|------------|
| `scripts/autotrader.py` | 8200 | Исполнение сделок |
| `scripts/pricefeed_bridge.py` | - | Получение цен с Binance |
| `scripts/pricefeed_gateway.py` | 8100 | HTTP gateway для цен |
| `scripts/momentum_trader.py` | - | Momentum-сигналы |
| `scripts/eye_of_god_v3.py` | - | Классификация сигналов |
| `scripts/position_watchdog.py` | - | Мониторинг позиций |

### Core модули
| Файл | Назначение |
|------|------------|
| `core/unified_allowlist.py` | Трёхслойный AllowList (CORE+DYNAMIC+HOT) |
| `core/io_atomic.py` | Атомарная запись файлов |
| `core/secrets.py` | Загрузка секретов |

---

## 4. АРХИТЕКТУРА

```
┌─────────────────────────────────────────────────────────────┐
│                     HOPE TRADING SYSTEM                      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  [Binance API] ──► [pricefeed_bridge] ──► state/ai/pricefeed.json
│                           │                                 │
│                           ▼                                 │
│                   [pricefeed_gateway:8100]                  │
│                           │                                 │
│                           ▼                                 │
│  [momentum_trader] ──► [unified_allowlist] ──► HOT_LIST     │
│         │                                                   │
│         ▼                                                   │
│  [AutoTrader:8200] ◄── [eye_of_god_v3] ◄── Сигналы          │
│         │                                                   │
│         ▼                                                   │
│  [Binance] ──► Реальные сделки                              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. ТРЁХСЛОЙНЫЙ ALLOWLIST

```
UNIFIED ALLOWLIST = CORE_LIST + DYNAMIC_LIST + HOT_LIST

CORE_LIST (8 символов):
  BTC, ETH, SOL, BNB, XRP, DOGE, ADA, AVAX
  - Постоянный список
  - 100% размер позиции

DYNAMIC_LIST (до 20 символов):
  - Топ по объёму ($50M+ 24h)
  - Обновляется каждый час
  - 100% размер позиции

HOT_LIST (до 10 символов):
  - Pump-сигналы и momentum
  - TTL: 15 минут
  - 50% размер позиции
```

---

## 6. ТЕКУЩИЕ НАСТРОЙКИ ($100)

```json
{
  "capital": 100,
  "position_size": "$10-25 (по confidence)",
  "max_positions": 2,
  "max_exposure": "$50 (50%)",
  "stop_loss": "3%",
  "take_profit": "6%",
  "fees": {
    "taker": "0.10%",
    "round_trip": "0.20%"
  }
}
```

---

## 7. ПРОВЕРКА СТАТУСА

```powershell
# Статус AutoTrader
curl http://127.0.0.1:8200/status

# Статус портов
netstat -an | findstr ":8100 :8200"

# Проверка синтаксиса
python -m py_compile scripts/autotrader.py

# Git статус
git status
```

---

## 8. ЗАПУСК СИСТЕМЫ

```powershell
cd C:\Users\kirillDev\Desktop\TradingBot\minibot

# 1. Pricefeed Gateway
Start-Process python -ArgumentList "scripts/pricefeed_gateway.py"

# 2. AutoTrader (LIVE MODE - REAL MONEY!)
Start-Process python -ArgumentList "scripts/autotrader.py","--mode","LIVE","--yes","--confirm"

# 3. Momentum Trader (опционально)
python scripts/momentum_trader.py --once
```

**ВАЖНО**: AutoTrader теперь синхронизирует состояние с Binance при старте и каждую минуту.

---

## 9. ПРАВИЛА (из CLAUDE.md)

1. **FAIL-CLOSED**: сомнение = безопасность
2. **HONESTY CONTRACT**: никаких фейков, только реальные данные
3. **EXECUTION LAW**: не "могу" — СДЕЛАНО
4. **AI SIGNATURE**: все файлы подписывать
5. **ATOMIC WRITES**: для state файлов только атомарная запись
6. **NO DELETIONS**: файлы не удалять, только в архив

---

## 10. ПОСЛЕДНИЕ ИЗМЕНЕНИЯ

**2026-02-02:**
- CRITICAL FIX: AutoTrader теперь синхронизирует позиции с Binance
- Добавлен _sync_with_binance() - вызывается при старте и каждую минуту
- Исправлена проблема с фейковыми/устаревшими позициями в state
- Добавлен IGNORE_ASSETS фильтр (SLF, USDT, USDC, etc.)

**2026-01-31:**
- Интеграция momentum_trader.py с unified_allowlist
- Символы автоматически добавляются в HOT_LIST при momentum-сигнале
- Исправлена проблема с STRAXUSDT (не был в AllowList)

---

## 11. КОМАНДА ВОССТАНОВЛЕНИЯ

Скопируй и вставь в начале новой сессии:

```
Прочитай файл docs/SESSION_RESTORE.md и CLAUDE.md для восстановления контекста проекта HOPE. После прочтения:
1. Подтверди что понял структуру проекта
2. Проверь статус системы (порты 8100, 8200)
3. Покажи текущий PnL и открытые позиции
```

---

**Этот файл — точка входа для любой новой сессии Claude.**
