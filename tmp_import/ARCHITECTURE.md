# HOPE CORE v2.0 - Command Bus + State Machine Architecture

## 🎯 ЦЕЛЬ

Единая защищённая оболочка для торговли, где:
- Все команды проходят через Command Bus с валидацией
- Все состояния контролируются State Machine
- Guardian независимо мониторит и перезапускает
- Event Journal позволяет replay и audit

---

## 📐 АРХИТЕКТУРА

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           GUARDIAN                                       │
│                    (Независимый процесс)                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
│  │ Heartbeat   │  │ State       │  │ Health      │  │ Auto        │    │
│  │ Monitor     │  │ Validator   │  │ Checker     │  │ Recovery    │    │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
                              ↑ monitor
┌─────────────────────────────────────────────────────────────────────────┐
│                         HOPE CORE                                        │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                      COMMAND BUS                                 │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │   │
│  │  │ Validate │→ │ Authorize│→ │  Route   │→ │ Execute  │        │   │
│  │  │ Contract │  │  Check   │  │ Command  │  │ Handler  │        │   │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘        │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                              ↓                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    STATE MACHINE                                 │   │
│  │                                                                  │   │
│  │   IDLE ──→ SCANNING ──→ SIGNAL_RECEIVED ──→ DECIDING            │   │
│  │    ↑                                            ↓                │   │
│  │    │         ┌──────────────────────────────────┘                │   │
│  │    │         ↓                                                   │   │
│  │    │     ORDERING ──→ PENDING_FILL ──→ POSITION_OPEN            │   │
│  │    │                                        ↓                    │   │
│  │    │                               MONITORING ──→ CLOSING        │   │
│  │    │                                                 ↓           │   │
│  │    └─────────────────────────────────────────── CLOSED           │   │
│  │                                                                  │   │
│  │   INVALID TRANSITION → ROLLBACK → ALERT                         │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                              ↓                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    EVENT JOURNAL                                 │   │
│  │  ┌──────────────────────────────────────────────────────────┐   │   │
│  │  │ timestamp | correlation_id | event_type | payload | hash │   │   │
│  │  └──────────────────────────────────────────────────────────┘   │   │
│  │  • Append-only log                                              │   │
│  │  • Every state transition logged                                │   │
│  │  • Replay capability for recovery                               │   │
│  │  • Hash chain for integrity                                     │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                         EXECUTION LAYER                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
│  │ Eye of God  │  │   Order     │  │  Position   │  │  Binance    │    │
│  │ (Decision)  │  │  Executor   │  │   Manager   │  │   Client    │    │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 🔐 COMMAND BUS

### Принцип работы

Каждая команда проходит 4 этапа:

1. **Validate** - проверка JSON Schema контракта
2. **Authorize** - проверка прав (rate limits, circuit breaker)
3. **Route** - маршрутизация к нужному handler
4. **Execute** - выполнение с логированием

### Команды

| Command | Payload | Handler | Side Effects |
|---------|---------|---------|--------------|
| `SIGNAL` | {symbol, score, source} | SignalHandler | → SIGNAL_RECEIVED |
| `DECIDE` | {signal_id} | DecisionHandler | → DECIDING → ORDERING/IDLE |
| `ORDER` | {symbol, side, qty, price} | OrderHandler | → Binance API |
| `CANCEL` | {order_id} | CancelHandler | → Binance API |
| `CLOSE` | {position_id} | CloseHandler | → SELL order |
| `SYNC` | {} | SyncHandler | → Binance state sync |
| `HEALTH` | {} | HealthHandler | → Status response |

### Contract Example

```python
SIGNAL_CONTRACT = {
    "type": "object",
    "required": ["symbol", "score", "source", "timestamp"],
    "properties": {
        "symbol": {"type": "string", "pattern": "^[A-Z]+USDT$"},
        "score": {"type": "number", "minimum": 0, "maximum": 100},
        "source": {"enum": ["MOMENTUM", "PUMP", "EXTERNAL", "MANUAL"]},
        "timestamp": {"type": "string", "format": "date-time"}
    }
}
```

---

## 🔄 STATE MACHINE

### States

| State | Description | Valid Transitions |
|-------|-------------|-------------------|
| `IDLE` | Ждём сигналов | → SCANNING |
| `SCANNING` | Сканируем рынок | → SIGNAL_RECEIVED, IDLE |
| `SIGNAL_RECEIVED` | Получен сигнал | → DECIDING |
| `DECIDING` | Eye of God анализирует | → ORDERING, IDLE |
| `ORDERING` | Отправляем ордер | → PENDING_FILL, IDLE |
| `PENDING_FILL` | Ждём исполнения | → POSITION_OPEN, IDLE |
| `POSITION_OPEN` | Позиция открыта | → MONITORING |
| `MONITORING` | Мониторим TP/SL | → CLOSING |
| `CLOSING` | Закрываем позицию | → CLOSED |
| `CLOSED` | Позиция закрыта | → IDLE |

### Transition Rules

```python
VALID_TRANSITIONS = {
    "IDLE": ["SCANNING"],
    "SCANNING": ["SIGNAL_RECEIVED", "IDLE"],
    "SIGNAL_RECEIVED": ["DECIDING"],
    "DECIDING": ["ORDERING", "IDLE"],  # IDLE if rejected
    "ORDERING": ["PENDING_FILL", "IDLE"],  # IDLE if order failed
    "PENDING_FILL": ["POSITION_OPEN", "IDLE"],  # IDLE if timeout
    "POSITION_OPEN": ["MONITORING"],
    "MONITORING": ["CLOSING"],
    "CLOSING": ["CLOSED"],
    "CLOSED": ["IDLE"],
}
```

### Invalid Transition Handling

```
INVALID TRANSITION DETECTED:
1. Log to Event Journal with ALERT level
2. Attempt rollback to last valid state
3. If rollback fails → EMERGENCY_STOP
4. Notify Guardian
5. Guardian decides: restart or escalate
```

---

## 📜 EVENT JOURNAL

### Schema

```
| Field          | Type     | Description                    |
|----------------|----------|--------------------------------|
| id             | UUID     | Unique event ID                |
| timestamp      | DateTime | Event time (UTC)               |
| correlation_id | UUID     | Links related events           |
| event_type     | Enum     | STATE_CHANGE, COMMAND, ERROR   |
| from_state     | String   | Previous state (if applicable) |
| to_state       | String   | New state (if applicable)      |
| payload        | JSON     | Event-specific data            |
| hash           | String   | SHA256 of previous + current   |
```

### Event Types

- `STATE_CHANGE` - State Machine transition
- `COMMAND_RECEIVED` - Command Bus received command
- `COMMAND_EXECUTED` - Command completed
- `COMMAND_REJECTED` - Command failed validation
- `ORDER_SENT` - Order sent to Binance
- `ORDER_FILLED` - Order executed
- `POSITION_OPENED` - New position
- `POSITION_CLOSED` - Position closed
- `HEARTBEAT` - Periodic health signal
- `ERROR` - Error occurred
- `ALERT` - Critical issue

---

## 🛡️ GUARDIAN

### Responsibilities

1. **Heartbeat Monitor** - Check HOPE Core every 10s
2. **State Validator** - Verify state transitions are valid
3. **Health Checker** - Check Binance connection, balance
4. **Auto Recovery** - Restart on failure

### Recovery Actions

| Issue | Detection | Action |
|-------|-----------|--------|
| No heartbeat | 30s timeout | Restart HOPE Core |
| Invalid state | State validation | Rollback + restart |
| API failure | 3 consecutive errors | Pause trading |
| Circuit breaker | Daily loss > 5% | Stop all trading |
| Memory leak | > 500MB RSS | Restart |

---

## 🔮 SECRET SAUCE (Мои тайные идеи)

### 1. Predictive State Preloading

```python
# Предзагрузка следующего вероятного состояния
if current_state == "SIGNAL_RECEIVED":
    # 80% вероятность что пойдём в DECIDING
    preload_eye_of_god_context()
    warm_binance_connection()
```

### 2. Adaptive Rate Limiting

```python
# Динамический rate limit на основе PnL
if daily_pnl < -2%:
    rate_limit = 1 trade / 10 min
elif daily_pnl < 0:
    rate_limit = 1 trade / 5 min
else:
    rate_limit = 1 trade / 1 min
```

### 3. Shadow Mode Testing

```python
# Параллельное выполнение без реальных ордеров
async def execute_with_shadow(command):
    real_result = await execute_real(command)
    shadow_result = await execute_shadow(command)  # DRY mode
    compare_and_log(real_result, shadow_result)
```

### 4. Correlation Chain

```python
# Связывание всех событий одной сделки
correlation_id = generate_uuid()
# Signal → Decision → Order → Fill → Position → Close
# Все события имеют один correlation_id
# Позволяет полный audit trail
```

### 5. Replay Recovery

```python
# При краше - восстановление из Event Journal
def recover_from_journal():
    last_valid_state = find_last_valid_checkpoint()
    events_to_replay = get_events_after(last_valid_state)
    for event in events_to_replay:
        replay_event(event)  # Deterministic replay
```

### 6. Smart Throttling

```python
# Throttling на основе волатильности
volatility = calculate_volatility(symbol)
if volatility > 5%:
    execution_delay = 0  # Fast execution
elif volatility < 1%:
    execution_delay = 5000  # Wait for better entry
```

---

## 📁 FILE STRUCTURE

```
hope_core/
├── __init__.py
├── ARCHITECTURE.md          # This file
│
├── bus/
│   ├── __init__.py
│   ├── command_bus.py       # Command Bus implementation
│   ├── contracts.py         # JSON Schema contracts
│   └── handlers/
│       ├── __init__.py
│       ├── signal_handler.py
│       ├── decision_handler.py
│       ├── order_handler.py
│       └── sync_handler.py
│
├── state/
│   ├── __init__.py
│   ├── machine.py           # State Machine
│   ├── transitions.py       # Valid transitions
│   └── rollback.py          # Rollback logic
│
├── journal/
│   ├── __init__.py
│   ├── event_journal.py     # Event logging
│   ├── replay.py            # Replay from journal
│   └── hash_chain.py        # Integrity verification
│
├── guardian/
│   ├── __init__.py
│   ├── watchdog.py          # Main guardian process
│   ├── health_check.py      # Health monitoring
│   └── recovery.py          # Auto recovery
│
├── execution/
│   ├── __init__.py
│   ├── eye_of_god.py        # Decision engine (imported)
│   ├── order_executor.py    # Binance execution (imported)
│   └── position_manager.py  # Position tracking
│
└── main.py                  # Entry point
```

---

## 🚀 STARTUP SEQUENCE

```
1. Guardian starts first
2. Guardian spawns HOPE Core
3. HOPE Core:
   a. Load Event Journal
   b. Recover state from journal
   c. Initialize Command Bus
   d. Initialize State Machine
   e. Connect to Binance
   f. Start heartbeat
   g. Enter IDLE state
4. Guardian confirms health
5. Trading begins
```

---

## 🔧 CONFIGURATION

```yaml
# hope_core_config.yaml
core:
  mode: LIVE  # DRY, TESTNET, LIVE
  heartbeat_interval: 10s
  
command_bus:
  max_queue_size: 100
  command_timeout: 30s
  
state_machine:
  transition_timeout: 5s
  max_rollback_attempts: 3
  
journal:
  path: state/events/journal.jsonl
  max_size: 100MB
  rotation: daily
  
guardian:
  heartbeat_timeout: 30s
  restart_delay: 5s
  max_restarts: 5
  
trading:
  min_confidence: 0.35
  max_positions: 3
  position_size: $20
  daily_loss_limit: 5%
```

---

*Created: 2026-02-04 by Claude (opus-4.5)*
*Purpose: HOPE AI Trading System v2.0 Architecture*
