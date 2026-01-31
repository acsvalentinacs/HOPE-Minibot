# HOPE P0 RULES — MANDATORY EXECUTION PROTOCOL
<!-- AI SIGNATURE: Created by Claude (opus-4.5) at 2026-01-31 02:35:00 UTC -->

> **ПРИОРИТЕТ: P0 — HIGHEST. НАРУШЕНИЕ = FAIL ГЕЙТА.**

---

## 1. ПОЛНЫЙ ТОРГОВЫЙ ЦИКЛ (NO STUBS)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    HOPE v4.0 LIVE TRADING CYCLE                         │
│                      (FAIL-CLOSED, NO BYPASS)                           │
└─────────────────────────────────────────────────────────────────────────┘

[BINANCE WEBSOCKET] ─────────────────────────────────────────────────────┐
       │                                                                  │
       ▼                                                                  │
┌─────────────────┐                                                       │
│  PUMP DETECTOR  │  ← 24h Momentum + 1m Delta + Volume Spike            │
│  + 24H TICKER   │                                                       │
└────────┬────────┘                                                       │
         │ signal_type: TRENDING/MOMENTUM_24H/PUMP                        │
         ▼                                                                │
┌─────────────────┐                                                       │
│  SIGNAL GATE    │  ← CANNOT BYPASS (bypass = SYSTEM FAIL)              │
│  (7 GUARDS)     │                                                       │
│  ├─ Schema      │                                                       │
│  ├─ TTL         │                                                       │
│  ├─ Liquidity   │                                                       │
│  ├─ Price       │                                                       │
│  ├─ Delta       │                                                       │
│  ├─ CircuitBrk  │                                                       │
│  └─ RateLimiter │                                                       │
└────────┬────────┘                                                       │
         │ GateDecision: PASS_TRADE / BLOCK                               │
         ▼                                                                │
┌─────────────────┐                                                       │
│  AI PREDICTOR   │  ← AllowList + RSI/MACD + BTC Correlation            │
│  v2 (3-Layer)   │  ← OVERRIDE for TRENDING/MOMENTUM_24H                │
└────────┬────────┘                                                       │
         │ ai_score, ai_tier, position_multiplier                         │
         ▼                                                                │
┌─────────────────┐                                                       │
│ ADAPTIVE TARGET │  ← R:R >= 3:1 REQUIRED                               │
│    ENGINE       │  ← target_pct, stop_pct, timeout_sec                 │
└────────┬────────┘                                                       │
         │                                                                │
         ▼                                                                │
┌─────────────────┐                                                       │
│ TRADING ENGINE  │  ← Real Binance API (SPOT, NOT Futures)              │
│  (LIVE/DRY)     │                                                       │
│  ├─ BUY order   │                                                       │
│  ├─ OCO (TP/SL) │                                                       │
│  └─ Position    │                                                       │
└────────┬────────┘                                                       │
         │ position_id, entry_price, quantity                             │
         ▼                                                                │
┌─────────────────┐                                                       │
│  WATCHDOG       │  ← Monitors positions, enforces exits                │
│  (LIVE MODE)    │  ← Panic threshold: 30s no price = EXIT              │
└────────┬────────┘                                                       │
         │                                                                │
         ▼                                                                │
┌─────────────────┐                                                       │
│  EVENT LEDGER   │  ← Correlation: signal_id → decision_id → position_id│
│  (JSONL)        │  ← Audit trail for every trade                       │
└────────┬────────┘                                                       │
         │                                                                │
         ▼                                                                │
┌─────────────────┐                                                       │
│  LEARNING       │  ← MFE/MAE tracking, win rate, PnL by symbol         │
│  FEEDBACK       │  ← Updates stats.json, improves thresholds           │
└─────────────────┘                                                       │
                                                                          │
[BINANCE EXCHANGE] ◄──────────────────────────────────────────────────────┘
     REAL MONEY
```

---

## 2. SIGNAL GATE — CANNOT BYPASS

**ЗАКОН: Любой сигнал ОБЯЗАН пройти через Signal Gate. Bypass = SYSTEM FAIL.**

```python
# ПРАВИЛЬНО:
from core.pretrade_pipeline import pretrade_check, PipelineConfig
result = pretrade_check(signal, PipelineConfig())
if not result.ok:
    log.info(f"[BLOCKED] {signal['symbol']}: {result.reason}")
    return  # STOP HERE

# ЗАПРЕЩЕНО:
# - Прямой вызов Trading Engine без pretrade_check
# - Любые "if DEBUG: skip_gate()"
# - Комментирование gate checks
# - "Временное" отключение для тестов
```

**7 Guards (все обязательны):**
1. **Schema** — сигнал имеет все required fields
2. **TTL** — сигнал не старше 30 секунд
3. **Liquidity** — daily_volume_m >= 5M USDT
4. **Price** — price > 0, not stale
5. **Delta Gate** — delta check OR 24h_momentum bypass
6. **Circuit Breaker** — max 5 consecutive losses, max 5% daily loss
7. **Rate Limiter** — cooldown between trades per symbol

---

## 3. ADAPTIVE TP ENGINE — R:R >= 3:1

**ЗАКОН: Risk/Reward ratio МИНИМУМ 3:1. Меньше = REJECT.**

```python
# Пример расчёта:
stop_pct = -1.0   # Риск: 1%
target_pct = 3.0  # Reward: 3%
# R:R = 3.0 / 1.0 = 3:1 ✅ PASS

# MOMENTUM сигналы (24h trend):
target_pct = 1.5  # Conservative
stop_pct = -1.0   # Tight stop
# R:R = 1.5 / 1.0 = 1.5:1 — ALLOWED для MOMENTUM (исключение)
```

**Tiers:**
| Tier | Delta | Target | Stop | Timeout |
|------|-------|--------|------|---------|
| STRONG | >= 5% | 3-5% | -1% | 5 min |
| MEDIUM | >= 2% | 2-3% | -1% | 10 min |
| WEAK | >= 0.5% | 1-2% | -0.8% | 15 min |
| MOMENTUM | 24h >= 5% | 1.5% | -1% | 30 min |
| NOISE | < 0.5% | — | — | REJECT |

---

## 4. АВТОПРОВЕРКА ПРИ ПРАВКЕ КОДА

**ЗАКОН: После ЛЮБОЙ правки файла — автоматическая проверка.**

### Шаги автопроверки (MANDATORY):

```powershell
# 1. Синтаксис изменённого файла
python -m py_compile <modified_file.py>

# 2. Import test
python -c "import <module_name>"

# 3. Проверка ВЫЗЫВАЮЩИХ модулей (кто импортирует этот файл)
grep -r "from <module> import" --include="*.py" .
grep -r "import <module>" --include="*.py" .
# Для каждого найденного → py_compile

# 4. Проверка логики flow
# Если изменён pump_detector.py → проверить:
#   - pretrade_pipeline.py
#   - ai_predictor_v2.py
#   - trading_engine.py
#   - watchdog.py

# 5. Unused imports check
grep -E "^import |^from .* import" <file> | while read imp; do
    # Проверить что импорт используется
done

# 6. Коммит ТОЛЬКО после ALL PASS
```

### Граф зависимостей (проверять при правке):

```
pump_detector.py
├── pretrade_pipeline.py (ПРОВЕРИТЬ)
├── ai_predictor_v2.py (ПРОВЕРИТЬ)
├── adaptive_target.py (ПРОВЕРИТЬ)
└── trading_engine.py (ПРОВЕРИТЬ)
    └── binance_oco_executor.py (ПРОВЕРИТЬ)
        └── position_watchdog.py (ПРОВЕРИТЬ)
```

---

## 5. ФОРМАТ TASK COMPLETION

**ЗАКОН: Каждая задача завершается блоком TASK COMPLETION.**

```
=== TASK COMPLETION ===
Task: <краткое описание>
Result: PASS | FAIL

✅ ЧТО РАБОТАЕТ:
  - <конкретный пункт с артефактом>
  - <команда проверки и результат>

❌ ОШИБКИ/РИСКИ:
  - <проблема>: <причина> → <план исправления>

❓ НУЖНО УТОЧНИТЬ:
  - <вопрос к владельцу>

💡 ФИЧИ ДЛЯ PRODUCTION:
  - <предложение улучшения>
  - <обоснование почему это важно>

Verification:
  <команды для проверки>

Files:
  - created: <list>
  - modified: <list>
  - INTEGRATED: <yes/no> ← КРИТИЧНО!
```

---

## 6. ЗАПРЕЩЁННЫЕ ДЕЙСТВИЯ

| # | Действие | Почему запрещено |
|---|----------|------------------|
| 1 | Создать файл без интеграции | Мёртвый код, не работает |
| 2 | "Могу сделать X" без делания | Пустые обещания |
| 3 | Bypass Signal Gate | Торговля без защиты |
| 4 | R:R < 3:1 (кроме MOMENTUM) | Математическое преимущество теряется |
| 5 | Коммит без py_compile | Сломанный код в репо |
| 6 | Править код без проверки соседей | Сломанные импорты/вызовы |
| 7 | LIVE mode без verify.ps1 | Неподтверждённая система |
| 8 | Заглушки в production | "return True" без логики |
| 9 | Silent except: pass | Скрытые ошибки |
| 10 | delta < threshold без bypass reason | Пропуск слабых сигналов |

---

## 7. LIVE CHECKLIST (ПЕРЕД КАЖДЫМ ЗАПУСКОМ)

```powershell
# === ОБЯЗАТЕЛЬНО ПЕРЕД LIVE ===

# 1. Проверка системы
python -m py_compile core/*.py scripts/*.py
python -c "from core.pretrade_pipeline import pretrade_check; print('OK')"
python -c "from execution.binance_oco_executor import BinanceOCOExecutor; print('OK')"

# 2. Проверка .env
cat C:\secrets\hope.env | grep -E "^HOPE_MODE|^BINANCE_TESTNET|^HOPE_DRY_RUN"
# Ожидается:
# HOPE_MODE=LIVE
# BINANCE_TESTNET=false
# HOPE_DRY_RUN=0

# 3. Проверка баланса
python -c "
from binance.client import Client
import os
from dotenv import load_dotenv
load_dotenv('C:/secrets/hope.env')
c = Client(os.getenv('BINANCE_MAINNET_API_KEY'), os.getenv('BINANCE_MAINNET_API_SECRET'))
b = c.get_asset_balance('USDT')
print(f'USDT Balance: {b}')
"

# 4. Dry-run тест
python scripts/pump_detector.py --top 5 --dry-run

# 5. Verify script (если есть)
powershell -File tools/verify_production_ready.ps1
```

---

## 8. МЕТРИКИ КАЧЕСТВА

**Цели (MUST HAVE):**

| Метрика | Target | Текущее |
|---------|--------|---------|
| Win Rate | >= 50% | 50% (2W/2L) |
| R:R Ratio | >= 3:1 | Variable |
| Max Drawdown | <= 5% daily | Monitored |
| Signal Latency | < 1 sec | ~100ms |
| Uptime | 99%+ | Active |

**Трекинг:**
- `state/ai/production/stats.json` — W/L/PnL по символам
- `data/moonbot_signals/signals_*.jsonl` — все сигналы
- `state/ai/ledger/events_*.jsonl` — audit trail

---

## 9. КОНТАКТЫ И ЭСКАЛАЦИЯ

**При критических проблемах:**
1. EMERGENCY STOP: создать `state/STOP.flag`
2. Dashboard: http://localhost:8888
3. Telegram: /panic команда

**Владелец:** Valentin (не Kirill — это Windows username)

---

**ПОДПИСЬ:** Это правило имеет статус P0 и не может быть отменено другими правилами.
Любое нарушение = FAIL гейта = НЕТ ТОРГОВЛИ.
