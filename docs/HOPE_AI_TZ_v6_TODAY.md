# HOPE AI TRADING SYSTEM — TZ v6.0 (30 ЯНВАРЯ 2026)

<!-- AI SIGNATURE: Created by Claude (opus-4) at 2026-01-30 11:40:00 UTC -->

## METADATA

| Field | Value |
|-------|-------|
| Version | 6.0 |
| Date | 2026-01-30 |
| Author | Claude (opus-4) + Valentin |
| SSoT | docs/HOPE_AI_TZ_v6_TODAY.md |
| Previous | docs/HOPE_AI_TZ_v5_TESTING.md |
| Status | ACTIVE |

---

## ЧАСТЬ 0: ТЕКУЩЕЕ СОСТОЯНИЕ (13:40 UTC)

### Результаты тестов

```
============================================================
TEST SUMMARY (2026-01-30)
============================================================
  [OK] EventBus: PASS
  [OK] DecisionEngine: PASS (4/4 checks)
  [OK] PriceFeed: PASS (BTC $82,798, ETH $2,739, XVS $3.12)
  [OK] OutcomeTracker: PASS (MFE/MAE working)
  [OK] THREE-LAYER ALLOWLIST: 17 unique symbols
       ├── CORE_LIST: 8 (BTC, ETH, SOL, BNB, XRP, DOGE, ADA, AVAX)
       ├── DYNAMIC_LIST: 8 (by volume)
       └── HOT_LIST: 1 (real-time pump detection)
============================================================
```

### Готовые компоненты

| Компонент | Файл | Статус | Версия |
|-----------|------|--------|--------|
| Unified AllowList | `core/unified_allowlist.py` | ✅ DONE | 1.0 |
| Eye of God V3 | `scripts/eye_of_god_v3.py` | ✅ DONE | 3.0 |
| Friend Bridge | `core/friend_bridge_server.py` | ✅ EXISTS | 1.6.0 |
| Live Dashboard | `scripts/live_dashboard.py` | ✅ EXISTS | 1.0 |
| Event Bus | `ai_gateway/core/event_bus.py` | ✅ PASS | - |
| Decision Engine | `ai_gateway/core/decision_engine.py` | ✅ PASS | - |
| Outcome Tracker | `ai_gateway/modules/self_improver/outcome_tracker.py` | ✅ PASS | - |
| Autotrader | `scripts/autotrader.py` | ✅ EXISTS | - |
| Order Executor | `scripts/order_executor.py` | ✅ EXISTS | - |
| Eye Trainer | `scripts/eye_trainer.py` | ✅ EXISTS | - |

---

## ЧАСТЬ 1: ПЛАН НА СЕГОДНЯ (30 ЯНВАРЯ 2026)

### Приоритеты

```
P0 — КРИТИЧНО (без этого не работает):
├── 1. Friends Chat Integration — полная интеграция Claude ↔ GPT
├── 2. Process Manager — централизованное управление всеми процессами
└── 3. Dashboard Enhancement — расширение визуализации

P1 — ВАЖНО (для production):
├── 4. Hot Reload Config — изменение параметров без рестарта
├── 5. Alert System — уведомления в Telegram при событиях
└── 6. Health Monitor — мониторинг здоровья всех компонентов

P2 — УЛУЧШЕНИЯ:
├── 7. Performance Metrics — сбор и отображение метрик
└── 8. Log Aggregation — централизованные логи
```

---

## ЧАСТЬ 2: FRIENDS CHAT INTEGRATION (P0)

### 2.1 Текущее состояние

```
Friend Bridge Server v1.6.0 — EXISTS
├── HTTP API на localhost:8765
├── Auth: X-HOPE-Token header
├── Endpoints:
│   ├── GET /healthz — health check
│   ├── POST /send — отправить сообщение Claude/GPT
│   ├── GET /inbox/{agent} — получить входящие
│   ├── GET /tail/gpt — последние ответы GPT
│   └── GET /ipc/status — статус IPC
```

### 2.2 Что нужно сделать

```
□ Task 2.2.1: Friend Chat UI (Telegram)
   Файл: ai_gateway/telegram/friend_chat_handler.py

   Функционал:
   ├── /chat <message> — отправить сообщение другому AI
   ├── /chat_status — показать статус чата
   ├── /chat_history — последние сообщения
   └── Inline buttons для быстрых ответов

   Интерфейс:
   ```python
   class FriendChatHandler:
       async def handle_chat(self, update: Update, context: Context):
           """Отправить сообщение другому AI."""
           message = update.message.text.replace("/chat ", "")
           result = await send_to_friend(message)
           await update.message.reply_text(f"📤 Sent: {result.id}")
   ```

□ Task 2.2.2: Auto-Dispatch System
   Файл: core/chat_auto_dispatch.py

   Функционал:
   ├── Маршрутизация по типу задачи (analysis → GPT, execution → Claude)
   ├── Queue для batch processing
   ├── Rate limiting (10 msg/min per agent)
   └── Fallback при недоступности агента

   Contract:
   ```python
   @dataclass
   class ChatTask:
       task_type: str  # "analysis" | "execution" | "research" | "review"
       message: str
       priority: int  # 0=low, 1=normal, 2=high
       timeout_sec: int = 60
       fallback_agent: Optional[str] = None

   class AutoDispatcher:
       ROUTING = {
           "analysis": "gpt",      # GPT для аналитики
           "execution": "claude",   # Claude для выполнения
           "research": "gpt",       # GPT для исследований
           "review": "claude",      # Claude для code review
       }

       async def dispatch(self, task: ChatTask) -> DispatchResult
   ```

□ Task 2.2.3: Response Aggregator
   Файл: core/chat_response_aggregator.py

   Функционал:
   ├── Объединение ответов от разных агентов
   ├── Conflict resolution (если ответы противоречат)
   ├── Confidence scoring
   └── Summary generation
```

### 2.3 Verification

```bash
# 1. Запустить Friend Bridge
python -m core.friend_bridge_server --insecure

# 2. Проверить health
curl http://localhost:8765/healthz

# 3. Отправить тестовое сообщение
curl -X POST http://localhost:8765/send \
  -H "Content-Type: application/json" \
  -d '{"to": "gpt", "message": "Test from Claude"}'

# 4. Проверить inbox
curl http://localhost:8765/inbox/claude
```

---

## ЧАСТЬ 3: PROCESS MANAGER (P0)

### 3.1 Архитектура

```
┌─────────────────────────────────────────────────────────────┐
│                    HOPE PROCESS MANAGER                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐       │
│  │ Eye of God  │   │  Dashboard  │   │Friend Bridge│       │
│  │    (EYE)    │   │   (DASH)    │   │   (CHAT)    │       │
│  └──────┬──────┘   └──────┬──────┘   └──────┬──────┘       │
│         │                 │                 │               │
│         └────────────────┼─────────────────┘               │
│                          │                                  │
│                          ▼                                  │
│              ┌─────────────────────┐                       │
│              │  Process Controller │                       │
│              │   (Supervisor)      │                       │
│              └──────────┬──────────┘                       │
│                         │                                   │
│         ┌───────────────┼───────────────┐                  │
│         ▼               ▼               ▼                  │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐          │
│  │   Signals   │ │   Trading   │ │   Monitor   │          │
│  │  Pipeline   │ │   Engine    │ │   System    │          │
│  └─────────────┘ └─────────────┘ └─────────────┘          │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Что нужно сделать

```
□ Task 3.2.1: Process Supervisor
   Файл: scripts/hope_supervisor.py (EXISTS, нужно доработать)

   Функционал:
   ├── Запуск/остановка всех компонентов
   ├── Health checks каждые 30 сек
   ├── Auto-restart при падении
   ├── Graceful shutdown (SIGTERM → cleanup → exit)
   └── State persistence (какие процессы запущены)

   CLI:
   ```bash
   python -m scripts.hope_supervisor start    # Запустить все
   python -m scripts.hope_supervisor stop     # Остановить все
   python -m scripts.hope_supervisor status   # Статус всех процессов
   python -m scripts.hope_supervisor restart eye  # Рестарт Eye of God
   ```

□ Task 3.2.2: Process Registry
   Файл: core/process_registry.py

   Контракт:
   ```python
   @dataclass
   class ProcessConfig:
       name: str              # "eye_of_god"
       command: str           # "python scripts/eye_of_god_v3.py"
       env: Dict[str, str]    # Environment variables
       depends_on: List[str]  # ["friend_bridge"]
       health_check: str      # "http://localhost:8765/healthz"
       restart_policy: str    # "always" | "on-failure" | "never"
       max_restarts: int      # 3

   PROCESS_REGISTRY: Dict[str, ProcessConfig] = {
       "friend_bridge": ProcessConfig(
           name="friend_bridge",
           command="python -m core.friend_bridge_server --insecure",
           env={},
           depends_on=[],
           health_check="http://localhost:8765/healthz",
           restart_policy="always",
           max_restarts=5,
       ),
       "dashboard": ProcessConfig(
           name="dashboard",
           command="python scripts/live_dashboard.py --port 8080",
           env={},
           depends_on=[],
           health_check="http://localhost:8080/",
           restart_policy="always",
           max_restarts=3,
       ),
       "eye_of_god": ProcessConfig(
           name="eye_of_god",
           command="python scripts/eye_of_god_v3.py --mode DRY",
           env={"TRADING_MODE": "DRY"},
           depends_on=["friend_bridge"],
           health_check=None,  # No HTTP endpoint
           restart_policy="on-failure",
           max_restarts=3,
       ),
   }
   ```

□ Task 3.2.3: Telegram Process Control
   Файл: ai_gateway/telegram/process_control_handler.py

   Команды:
   ├── /processes — список всех процессов и статус
   ├── /start_process <name> — запустить процесс
   ├── /stop_process <name> — остановить процесс
   ├── /restart_process <name> — перезапустить
   └── /logs <name> <lines> — последние N строк логов
```

### 3.3 State File

```json
// state/processes/supervisor_state.json
{
  "started_at": "2026-01-30T11:00:00Z",
  "processes": {
    "friend_bridge": {
      "pid": 12345,
      "status": "running",
      "started_at": "2026-01-30T11:00:01Z",
      "restarts": 0,
      "last_health_check": "2026-01-30T11:40:00Z",
      "health_status": "healthy"
    },
    "dashboard": {
      "pid": 12346,
      "status": "running",
      "started_at": "2026-01-30T11:00:02Z",
      "restarts": 1,
      "last_health_check": "2026-01-30T11:40:00Z",
      "health_status": "healthy"
    },
    "eye_of_god": {
      "pid": 12347,
      "status": "running",
      "started_at": "2026-01-30T11:00:05Z",
      "restarts": 0,
      "last_health_check": null,
      "health_status": "unknown"
    }
  }
}
```

---

## ЧАСТЬ 4: DASHBOARD ENHANCEMENT (P0)

### 4.1 Текущее состояние

```
Live Dashboard v1.0 — EXISTS
├── FastAPI + WebSocket
├── Порт: 8080
├── Компоненты:
│   ├── Price Chart (BTC/USDT)
│   ├── AI Confidence Bars
│   ├── P&L Metrics
│   └── Signal Feed
```

### 4.2 Что нужно добавить

```
□ Task 4.2.1: Process Status Panel
   Показывать:
   ├── Список всех процессов (supervisor data)
   ├── Статус: 🟢 running / 🔴 stopped / 🟡 restarting
   ├── Uptime каждого процесса
   └── Quick actions: Start/Stop/Restart

□ Task 4.2.2: AllowList Visualizer
   Показывать:
   ├── CORE_LIST (синий) — постоянные монеты
   ├── DYNAMIC_LIST (зелёный) — по объёму
   ├── HOT_LIST (красный) — пампы
   └── Timeline когда монета добавлена/удалена

□ Task 4.2.3: Friend Chat Widget
   Показывать:
   ├── Последние сообщения Claude ↔ GPT
   ├── Input для отправки сообщений
   └── Статус подключения к Friend Bridge

□ Task 4.2.4: Trading Log
   Показывать:
   ├── Все сигналы (BUY/SKIP с причинами)
   ├── Открытые позиции
   ├── История сделок (MFE/MAE/PnL)
   └── Circuit Breaker status

□ Task 4.2.5: Multi-Symbol Charts
   Добавить:
   ├── Выбор символа из AllowList
   ├── Multiple charts (до 4 одновременно)
   └── Comparison mode (overlay charts)
```

### 4.3 Новая структура Dashboard

```html
┌──────────────────────────────────────────────────────────────────┐
│ 🤖 HOPE AI Trading Dashboard                    [WS] [AI] [BIN] │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌────────────────────────────┐  ┌────────────────────────────┐  │
│  │     PRICE CHARTS (4x)      │  │    PROCESS STATUS          │  │
│  │  ┌────────┐  ┌────────┐   │  │  🟢 friend_bridge  00:40:12│  │
│  │  │  BTC   │  │  ETH   │   │  │  🟢 dashboard      00:40:10│  │
│  │  └────────┘  └────────┘   │  │  🟢 eye_of_god     00:35:22│  │
│  │  ┌────────┐  ┌────────┐   │  │  🔴 autotrader     STOPPED  │  │
│  │  │  SOL   │  │  XVS   │   │  │                             │  │
│  │  └────────┘  └────────┘   │  │  [Start All] [Stop All]    │  │
│  └────────────────────────────┘  └────────────────────────────┘  │
│                                                                   │
│  ┌────────────────────────────┐  ┌────────────────────────────┐  │
│  │      ALLOWLIST STATUS      │  │     AI CONFIDENCE          │  │
│  │  CORE:     8 symbols 🔵    │  │  Regime:    ████████░░ 85% │  │
│  │  DYNAMIC:  8 symbols 🟢    │  │  Anomaly:   ██████░░░░ 62% │  │
│  │  HOT:      1 symbols 🔴    │  │  Pump:      ███████░░░ 78% │  │
│  │  ─────────────────────     │  │  Risk:      ██░░░░░░░░ 25% │  │
│  │  Total:   17 unique        │  │                             │  │
│  └────────────────────────────┘  └────────────────────────────┘  │
│                                                                   │
│  ┌────────────────────────────┐  ┌────────────────────────────┐  │
│  │      FRIEND CHAT           │  │     SIGNAL FEED            │  │
│  │  Claude: Analyzing XVS...  │  │  🟢 XVS +9.51% BUY 13:35   │  │
│  │  GPT: Pattern detected     │  │  🟡 DODO +1.9% WATCH 13:34 │  │
│  │  Claude: Confirmed, buy    │  │  🔴 BTC -0.2% SKIP 13:33   │  │
│  │  ────────────────────────  │  │  🟢 SOL +3.2% BUY 13:32    │  │
│  │  [Type message...]  [Send] │  │                             │  │
│  └────────────────────────────┘  └────────────────────────────┘  │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  💰 P&L: +$127.50  │  Win Rate: 68%  │  Trades: 24  │ DD: 3% │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                   │
└──────────────────────────────────────────────────────────────────┘
```

---

## ЧАСТЬ 5: ФАЙЛОВАЯ СТРУКТУРА

### 5.1 Новые файлы (создать)

```
minibot/
├── ai_gateway/
│   └── telegram/
│       ├── friend_chat_handler.py     🆕 Friends Chat commands
│       └── process_control_handler.py 🆕 Process control commands
│
├── core/
│   ├── chat_auto_dispatch.py          🆕 Auto-routing messages
│   ├── chat_response_aggregator.py    🆕 Combining AI responses
│   └── process_registry.py            🆕 Process configurations
│
├── scripts/
│   └── hope_supervisor.py             📝 ENHANCE (exists)
│
└── state/
    └── processes/
        └── supervisor_state.json      🆕 Process state
```

### 5.2 Существующие файлы (обновить)

```
├── core/
│   └── friend_bridge_server.py        📝 v1.6.0 → v1.7.0
│
├── scripts/
│   └── live_dashboard.py              📝 Add new panels
│
└── tg_bot_simple.py                   📝 Add new handlers
```

---

## ЧАСТЬ 6: IMPLEMENTATION ORDER

### День 1 (30 января) — Утро

```
□ Step 1: Process Registry (30 min)
   Создать core/process_registry.py
   - Определить все процессы
   - Указать зависимости
   - Настроить health checks

   Verification:
   python -c "from core.process_registry import PROCESS_REGISTRY; print(PROCESS_REGISTRY)"

□ Step 2: Supervisor Enhancement (1 hour)
   Обновить scripts/hope_supervisor.py
   - CLI interface (start/stop/status/restart)
   - Health monitoring loop
   - Auto-restart logic
   - State persistence

   Verification:
   python -m scripts.hope_supervisor status
```

### День 1 (30 января) — День

```
□ Step 3: Dashboard Process Panel (1 hour)
   Обновить scripts/live_dashboard.py
   - Добавить WebSocket endpoint для process status
   - Создать UI панель с процессами
   - Кнопки Start/Stop/Restart

   Verification:
   python scripts/live_dashboard.py --port 8080
   # Открыть http://localhost:8080

□ Step 4: Dashboard AllowList Panel (45 min)
   Обновить scripts/live_dashboard.py
   - Подключиться к unified_allowlist
   - Визуализация трёх слоёв
   - Real-time обновление

   Verification:
   # Проверить что AllowList отображается в dashboard
```

### День 1 (30 января) — Вечер

```
□ Step 5: Friend Chat Widget (1 hour)
   Обновить scripts/live_dashboard.py
   - Подключиться к Friend Bridge
   - Chat UI компонент
   - Send/receive messages

   Verification:
   # Отправить сообщение через dashboard
   # Получить ответ

□ Step 6: Telegram Integration (1 hour)
   Создать новые handlers
   - /processes command
   - /chat command
   - /allowlist command

   Verification:
   # В Telegram отправить /processes
```

---

## ЧАСТЬ 7: SAFETY INVARIANTS

### 7.1 Process Manager Safety

```python
# 1. Never kill processes without cleanup
def stop_process(name: str) -> bool:
    proc = get_process(name)
    proc.send_signal(signal.SIGTERM)  # Graceful
    try:
        proc.wait(timeout=30)
    except TimeoutExpired:
        proc.kill()  # Force only after timeout
    return True

# 2. Dependency order on startup
def start_all():
    for proc in topological_sort(PROCESS_REGISTRY):
        start_process(proc.name)
        wait_for_healthy(proc.name, timeout=30)

# 3. Never restart if max_restarts exceeded
def should_restart(proc: ProcessState) -> bool:
    if proc.restarts >= proc.config.max_restarts:
        alert(f"CRITICAL: {proc.name} exceeded max restarts")
        return False
    return proc.config.restart_policy in ["always", "on-failure"]
```

### 7.2 Chat Safety

```python
# 1. Rate limiting
RATE_LIMIT = 10  # messages per minute per agent

# 2. Message size limit
MAX_MESSAGE_SIZE = 10000  # chars

# 3. No sensitive data in messages
FORBIDDEN_PATTERNS = [
    r"BINANCE_.*_KEY",
    r"TELEGRAM_BOT_TOKEN",
    r"password",
]

def validate_message(msg: str) -> bool:
    if len(msg) > MAX_MESSAGE_SIZE:
        return False
    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, msg, re.IGNORECASE):
            return False
    return True
```

---

## ЧАСТЬ 8: VERIFICATION COMMANDS

```bash
# ═══════════════════════════════════════════════════════════════
# FULL SYSTEM TEST
# ═══════════════════════════════════════════════════════════════

# 1. Process Manager
python -m scripts.hope_supervisor status
python -m scripts.hope_supervisor start friend_bridge
python -m scripts.hope_supervisor start dashboard
python -m scripts.hope_supervisor status

# 2. Friend Bridge
curl http://localhost:8765/healthz
curl -X POST http://localhost:8765/send -d '{"to":"gpt","message":"test"}'

# 3. Dashboard
curl http://localhost:8080/
# Open in browser

# 4. AI Gateway Tests
python -m scripts.test_ai_gateway

# 5. AllowList Test
python -c "from core.unified_allowlist import get_unified_allowlist; al = get_unified_allowlist(); print(f'Total: {len(al.get_symbols_set())} symbols')"

# ═══════════════════════════════════════════════════════════════
# QUICK HEALTH CHECK
# ═══════════════════════════════════════════════════════════════

python -c "
import requests
import json

checks = {
    'friend_bridge': 'http://localhost:8765/healthz',
    'dashboard': 'http://localhost:8080/',
}

for name, url in checks.items():
    try:
        r = requests.get(url, timeout=5)
        status = 'OK' if r.status_code == 200 else f'FAIL ({r.status_code})'
    except Exception as e:
        status = f'DOWN ({e})'
    print(f'{name}: {status}')
"
```

---

## ЧАСТЬ 9: SUCCESS CRITERIA

### End of Day Checklist

- [ ] Process Manager работает (`hope_supervisor status` показывает все процессы)
- [ ] Friend Bridge отвечает на `/healthz`
- [ ] Dashboard показывает:
  - [ ] Process status panel
  - [ ] AllowList visualization
  - [ ] Friend Chat widget
- [ ] Telegram команды работают:
  - [ ] `/processes`
  - [ ] `/chat <message>`
  - [ ] `/allowlist`
- [ ] All tests pass (`test_ai_gateway.py`)
- [ ] No crashes за 1 hour continuous running

---

## ЧАСТЬ 10: ROLLBACK PLAN

```bash
# Если что-то сломалось:

# 1. Остановить все процессы
python -m scripts.hope_supervisor stop

# 2. Откат к последнему рабочему коммиту
git log --oneline -5
git checkout <LAST_WORKING_COMMIT>

# 3. Проверить базовый функционал
python -m scripts.test_ai_gateway
python -m py_compile core/*.py scripts/*.py

# 4. Если всё ок, вернуться к разработке
git checkout master
```

---

## CHECKSUM

```
Document: HOPE_AI_TZ_v6_TODAY.md
Version: 6.0
Generated: 2026-01-30T11:40:00Z
```
