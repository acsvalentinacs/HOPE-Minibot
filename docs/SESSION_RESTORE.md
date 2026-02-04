# HOPE AI Trading System - Session Restore

<!-- AI SIGNATURE: Modified by Claude (opus-4.5) at 2026-02-04 09:30:00 UTC -->

## КРИТИЧЕСКАЯ ИНФОРМАЦИЯ ДЛЯ CLAUDE

При начале новой сессии — ПРОЧИТАЙ ЭТОТ ФАЙЛ ПЕРВЫМ!

---

## 🚀 БЫСТРЫЙ СТАРТ (копируй в новую сессию)

```
Прочитай docs/SESSION_RESTORE.md и CLAUDE.md. Затем выполни команды проверки VPS:

ssh -i ~/.ssh/id_ed25519_hope root@46.62.232.161 "curl -s http://127.0.0.1:8200/api/health | python3 -m json.tool"
ssh -i ~/.ssh/id_ed25519_hope root@46.62.232.161 "journalctl -u hope-autotrader -n 20 --no-pager"
git log --oneline -10
git status
```

---

## 1. ПРОЕКТ

```
Название:     HOPE AI Trading System
Владелец:     Валентин (kirillDev - это username Windows, не имя)
Статус:       LIVE PRODUCTION
Биржа:        Binance (РЕАЛЬНЫЕ ДЕНЬГИ)
Капитал:      ~$100
Режим:        24/7 автоматическая торговля
```

---

## 2. VPS (ОСНОВНОЙ СЕРВЕР)

```
IP:           46.62.232.161 (Hetzner)
SSH ключ:     ~/.ssh/id_ed25519_hope
User:         root
Проект:       /opt/hope/minibot
Python:       /opt/hope/venv/bin/python
```

**SSH подключение:**
```bash
ssh -i ~/.ssh/id_ed25519_hope root@46.62.232.161
```

**Сервисы systemd:**
| Сервис | Порт | Статус |
|--------|------|--------|
| hope-autotrader | 8200 | ✅ Active |
| hope-core | 8100 | ✅ Active |
| hope-signal-loop | - | ✅ Active |
| hope-watchdog | - | ✅ Active |
| hope-tgbot | - | ✅ Active |
| hope-dashboard | 8080 | ❌ Failed |

---

## 3. ПУТИ

### Локальный (Windows)
```
КОРЕНЬ:        C:\Users\kirillDev\Desktop\TradingBot\minibot
SECRETS:       C:\secrets\hope.env
STATE:         minibot\state\
SCRIPTS:       minibot\scripts\
```

### VPS (Linux)
```
КОРЕНЬ:        /opt/hope/minibot
SECRETS:       /opt/hope/secrets/hope.env
VENV:          /opt/hope/venv
```

---

## 4. КЛЮЧЕВЫЕ ФАЙЛЫ

| Файл | Порт | Назначение |
|------|------|------------|
| `scripts/autotrader.py` | 8200 | Главный торговый loop, API |
| `scripts/eye_of_god_v3.py` | - | AI Decision Engine (two-chamber) |
| `scripts/order_executor.py` | - | Binance order execution |
| `scripts/position_watchdog.py` | - | Position monitoring |
| `scripts/auto_signal_loop.py` | - | Signal generator |
| `scripts/pricefeed_gateway.py` | 8100 | Price feed HTTP gateway |

---

## 5. АРХИТЕКТУРА

```
┌─────────────────────────────────────────────────────────────────┐
│                    HOPE TRADING SYSTEM                           │
├─────────────────────────────────────────────────────────────────┤
│  LOCAL (Windows)           │  VPS (46.62.232.161)               │
│  ├── minibot/              │  ├── /opt/hope/minibot/            │
│  │   ├── scripts/          │  │   ├── scripts/autotrader.py     │
│  │   ├── core/             │  │   ├── scripts/eye_of_god_v3.py  │
│  │   └── docs/             │  │   └── scripts/order_executor.py │
│                            │                                     │
│  SSH Key:                  │  Services:                          │
│  ~/.ssh/id_ed25519_hope    │  ├── hope-autotrader (8200)        │
│                            │  ├── hope-core (8100)              │
│                            │  ├── hope-signal-loop              │
│                            │  └── hope-watchdog                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 6. ТЕКУЩИЕ THRESHOLDS

```python
# eye_of_god_v3.py (2026-02-04)
MIN_CONFIDENCE_TO_TRADE = 0.50      # Regular signals
MIN_CONFIDENCE_AI_OVERRIDE = 0.35   # AI override signals
MIN_CONFIDENCE_MOMENTUM = 0.25      # Momentum signals

# autotrader.py
min_confidence: float = 0.35        # Fallback processor
```

---

## 7. КОМАНДЫ ПРОВЕРКИ

### VPS статус
```bash
# Health check (P0 endpoint)
ssh -i ~/.ssh/id_ed25519_hope root@46.62.232.161 "curl -s http://127.0.0.1:8200/api/health | python3 -m json.tool"

# Trading status
ssh -i ~/.ssh/id_ed25519_hope root@46.62.232.161 "curl -s http://127.0.0.1:8200/status | python3 -m json.tool"

# Логи (последние 30 строк)
ssh -i ~/.ssh/id_ed25519_hope root@46.62.232.161 "journalctl -u hope-autotrader -n 30 --no-pager"

# Логи в реальном времени
ssh -i ~/.ssh/id_ed25519_hope root@46.62.232.161 "journalctl -u hope-autotrader -f"

# Статус всех сервисов
ssh -i ~/.ssh/id_ed25519_hope root@46.62.232.161 "systemctl list-units | grep hope"
```

### Локальная проверка
```bash
# Git история
git log --oneline -15

# Незакоммиченные изменения
git status && git diff --stat

# Синтаксис
python -m py_compile scripts/autotrader.py
```

---

## 8. ДЕПЛОЙ НА VPS

```bash
# 1. Скопировать файл
scp -i ~/.ssh/id_ed25519_hope scripts/autotrader.py root@46.62.232.161:/opt/hope/minibot/scripts/

# 2. Перезапустить сервис
ssh -i ~/.ssh/id_ed25519_hope root@46.62.232.161 "systemctl restart hope-autotrader"

# 3. Проверить логи
ssh -i ~/.ssh/id_ed25519_hope root@46.62.232.161 "journalctl -u hope-autotrader -n 20 --no-pager"
```

---

## 9. БЫСТРЫЕ ФИКСЫ

### Сбросить circuit breaker
```bash
ssh -i ~/.ssh/id_ed25519_hope root@46.62.232.161 "curl -X POST http://127.0.0.1:8200/circuit-breaker/reset"
```

### Сбросить daily trades
```bash
ssh -i ~/.ssh/id_ed25519_hope root@46.62.232.161 "cd /opt/hope/minibot && python3 -c \"
import json
from pathlib import Path
state_file = Path('state/ai/autotrader/state.json')
state = json.loads(state_file.read_text())
state['daily_trades'] = 0
state_file.write_text(json.dumps(state, indent=2))
print('Daily trades reset to 0')
\""
```

### Перезапустить все HOPE сервисы
```bash
ssh -i ~/.ssh/id_ed25519_hope root@46.62.232.161 "systemctl restart hope-autotrader hope-signal-loop hope-watchdog"
```

---

## 10. ИСТОРИЯ ИЗМЕНЕНИЙ

### 2026-02-04
- ✅ Health endpoint `/api/health` (P0)
- ✅ Startup validation `_validate_startup()` (P0)
- ✅ Event Bus heartbeat `_emit_heartbeat()` (P0)
- ✅ EyeOfGodV3 import fix
- ✅ Lowered confidence thresholds

### 2026-02-02
- CRITICAL FIX: AutoTrader синхронизирует позиции с Binance
- Добавлен `_sync_with_binance()` - при старте и каждую минуту

### 2026-01-31
- Интеграция momentum_trader с unified_allowlist

---

## 11. ЗАДАЧИ (TODO)

### ✅ ВЫПОЛНЕНО (P0)
- [x] Health endpoint `/api/health`
- [x] Startup validation
- [x] Event Bus heartbeat
- [x] EyeOfGodV3 two-chamber decisions

### 🔄 В РАБОТЕ (P1)
- [ ] Guardian watchdog (независимый процесс)
- [ ] Telegram alerts при критических событиях
- [ ] Консолидация процессов в единое "облако"

### 📋 BACKLOG (P2)
- [ ] Event Journal с correlation IDs
- [ ] ML model training (100+ trades)
- [ ] Backtest validation

---

## 12. ПРАВИЛА (из CLAUDE.md)

1. **FAIL-CLOSED**: сомнение = безопасность
2. **HONESTY CONTRACT**: никаких фейков, только реальные данные
3. **EXECUTION LAW**: не "могу" — СДЕЛАНО
4. **AI SIGNATURE**: все файлы подписывать
5. **ATOMIC WRITES**: для state файлов только атомарная запись
6. **NO DELETIONS**: файлы не удалять, только в архив

---

**Этот файл — точка входа для любой новой сессии Claude.**
*Last updated: 2026-02-04 09:30 UTC*
