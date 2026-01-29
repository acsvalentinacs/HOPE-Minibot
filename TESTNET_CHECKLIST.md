# HOPE AI - TESTNET CHECKLIST v1.0

## Перед запуском TESTNET торговли

---

## ✅ PRE-FLIGHT (обязательно)

### 1. Система работает
```powershell
# Среда: PowerShell
cd C:\Users\kirillDev\Desktop\TradingBot\minibot

# Диагностика
python hope_diagnostic.py
# Expected: 0 BROKEN, 0 MISSING

# Integration test
python integration_test.py
# Expected: "READY FOR TESTNET"
```

- [ ] `hope_diagnostic.py` → 0 BROKEN
- [ ] `integration_test.py` → READY FOR TESTNET
- [ ] AI Gateway starts without errors

### 2. Environment настроен
```powershell
# C:\secrets\hope.env должен содержать:
BINANCE_API_KEY=...
BINANCE_SECRET_KEY=...
BINANCE_TESTNET=true          # ВАЖНО: true для TESTNET
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ADMIN_CHAT_ID=...
```

- [ ] `BINANCE_TESTNET=true` установлен
- [ ] API ключи валидны (проверить на testnet.binance.vision)
- [ ] Telegram bot отвечает

### 3. Данные собраны
```powershell
# Проверить количество сигналов
python -c "
import json
with open('data/moonbot_signals/signals_20260129.jsonl') as f:
    count = sum(1 for _ in f)
print(f'Signals: {count}')
"
```

- [ ] Минимум 100 сигналов собрано
- [ ] Сигналы валидны (JSON parseable)

---

## 🚀 ЗАПУСК

### Step 1: Запустить систему
```powershell
# Вариант A: Автоматический запуск
.\start_hope_ai.ps1 -Mode TESTNET

# Вариант B: Ручной запуск (3 терминала)
# Terminal 1:
python -m ai_gateway.server

# Terminal 2:
python -m ai_gateway.integrations.moonbot_live --watch

# Terminal 3 (monitor):
Get-Content state\ai\decisions.jsonl -Wait -Tail 5
```

- [ ] AI Gateway запущен на порту 8100
- [ ] MoonBot Live слушает сигналы
- [ ] Health check: `curl http://127.0.0.1:8100/health`

### Step 2: Проверить endpoints
```powershell
# Health
Invoke-RestMethod http://127.0.0.1:8100/health

# Stats
Invoke-RestMethod http://127.0.0.1:8100/stats

# Predict (manual test)
Invoke-RestMethod http://127.0.0.1:8100/predict/BTCUSDT
```

- [ ] Health возвращает `{"status": "healthy"}`
- [ ] Stats возвращает статистику
- [ ] Predict возвращает предсказание

### Step 3: Первая сделка
```powershell
# Дождаться первого BUY сигнала
Get-Content state\ai\decisions.jsonl -Wait | Where-Object { $_ -match 'BUY' }
```

- [ ] Получен первый BUY сигнал
- [ ] Сделка выполнена на TESTNET
- [ ] Outcome записан в `state\ai\outcomes\`

---

## 📊 МОНИТОРИНГ (24 часа)

### Метрики для отслеживания

| Метрика | Минимум | Цель |
|---------|---------|------|
| Signals processed | 50 | 100+ |
| BUY decisions | 10 | 30+ |
| Outcomes tracked | 10 | 30+ |
| Win rate | 40% | 55%+ |
| Avg MFE | 0.5% | 2%+ |
| Avg MAE | -1% | -0.5% |
| Uptime | 95% | 99%+ |
| Circuit breaker trips | <5 | 0 |

### Команды мониторинга
```powershell
# Статистика
python -c "
import json
with open('state/ai/outcomes/completed_outcomes.jsonl') as f:
    outcomes = [json.loads(l) for l in f]
wins = len([o for o in outcomes if o['pnl_pct'] > 0])
print(f'Total: {len(outcomes)}, Wins: {wins}, Rate: {wins/len(outcomes)*100:.1f}%')
"

# Последние решения
Get-Content state\ai\decisions.jsonl -Tail 10 | ConvertFrom-Json | Format-Table symbol, final_action, timestamp

# Последние outcomes
Get-Content state\ai\outcomes\completed_outcomes.jsonl -Tail 10 | ConvertFrom-Json | Format-Table symbol, pnl_pct, exit_reason
```

---

## ⚠️ КРИТЕРИИ ОСТАНОВКИ

Немедленно остановить если:

1. **Circuit Breaker** открылся более 3 раз
2. **Drawdown** превысил 5%
3. **Win rate** упал ниже 30% (на 20+ trades)
4. **Системная ошибка** - любой unhandled exception
5. **Latency** > 1 секунда на P95

```powershell
# Остановить всё
.\stop_hope_ai.ps1
```

---

## ✅ GATE: TESTNET → LIVE

Перед переходом на LIVE:

- [ ] 24 часа непрерывной работы на TESTNET
- [ ] 50+ outcomes собрано
- [ ] Win rate > 50%
- [ ] Нет circuit breaker trips за последние 12 часов
- [ ] Max drawdown < 3%
- [ ] Ручная проверка 10 случайных сделок
- [ ] **HUMAN APPROVAL** (Valentin)

---

## 📝 LOG

| Дата | Событие | Результат |
|------|---------|-----------|
| | Запуск TESTNET | |
| | 24h milestone | |
| | 50 outcomes | |
| | LIVE approval | |

---

## КОНТАКТЫ

- Telegram Admin: @ValentinHOPE
- Emergency: stop_hope_ai.ps1

---

**Checksum:** sha256:testnet_checklist_v1
