# HOPE AI TRADING SYSTEM v3.0 — ТЕХНИЧЕСКОЕ ЗАДАНИЕ

<!-- AI SIGNATURE: Created by Claude (opus-4) at 2026-01-29 10:30:00 UTC -->
<!-- CHECKPOINT: Phase 2.5 - Self-Improving Loop Complete -->

## 1. VISION

**ЦЕЛЬ:** Автономная торговая система с self-improving AI для Binance.

**МЕТРИКИ УСПЕХА:**
| Метрика | Текущее | Целевое |
|---------|---------|---------|
| Win Rate | ~50% | >70% |
| Profit Factor | ~1.0 | >2.0 |
| Max Drawdown | N/A | <15% |
| Signal→Order Latency | N/A | <500ms |
| Uptime | N/A | 99.5% |

**ПРИНЦИПЫ (MANDATORY):**
- **Fail-closed**: сомнение = STOP, не продолжать
- **Atomic operations**: temp → fsync → replace
- **Deterministic core**: AI = observability, не magic
- **Explicit contracts**: sha256: prefix везде
- **Human-in-the-loop**: major changes require approval

---

## 2. ТЕКУЩЕЕ СОСТОЯНИЕ (Phase 2.5 Complete)

### 2.1 Реализовано ✅

| Компонент | Файл | Статус |
|-----------|------|--------|
| Self-Improving Loop | `ai_gateway/modules/self_improver/loop.py` | ✅ Done |
| Outcome Tracker | `ai_gateway/modules/self_improver/outcome_tracker.py` | ✅ Done |
| Model Registry | `ai_gateway/modules/self_improver/model_registry.py` | ✅ Done |
| A/B Tester | `ai_gateway/modules/self_improver/ab_tester.py` | ✅ Done |
| Signal Classifier | `ai_gateway/modules/predictor/signal_classifier.py` | ✅ Done |
| MoonBot Parser | `scripts/moonbot_parser.py` | ✅ Done |
| AI Gateway Server | `ai_gateway/server.py` | ✅ Done |
| Telegram Panel | `ai_gateway/telegram_panel.py` | ✅ Done |
| OMNI-CHAT | `omnichat/app.py` | ✅ v1.8 |
| DDO System | `omnichat/src/ddo/` | ✅ Done |
| Market Intel | `omnichat/src/market_intel/` | ✅ Done |

### 2.2 Критические дефекты для исправления

```
ДЕФЕКТ 1: Нет real-time price feed
├── Проблема: OutcomeTracker требует цены, но нет WebSocket
├── Следствие: Невозможно автоматически определить WIN/LOSS
└── Решение: Binance WebSocket → PriceFeed → OutcomeTracker

ДЕФЕКТ 2: Нет Event Bus
├── Проблема: Модули работают изолированно
├── Следствие: Нет единого потока данных
└── Решение: Central Event Bus с JSONL persistence

ДЕФЕКТ 3: Model Registry без checksum validation
├── Проблема: Модель загружается без верификации
├── Следствие: Corrupted model = silent failures
└── Решение: sha256: prefix для .joblib файлов

ДЕФЕКТ 4: Нет Circuit Breaker
├── Проблема: 5 LOSS подряд = только rollback модели
├── Следствие: Продолжает терять на плохом рынке
└── Решение: HALT trading при consecutive losses

ДЕФЕКТ 5: Telegram Panel без rate limiting
├── Проблема: Нет защиты от flood
├── Следствие: Ban от Telegram API
└── Решение: Token bucket rate limiter
```

---

## 3. АРХИТЕКТУРА v3.0

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         HOPE AI v3.0 ARCHITECTURE                       │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │
│  │  MoonBot    │  │  Binance    │  │   News RSS  │  │  Telegram   │   │
│  │  Signals    │  │  WebSocket  │  │   Feeds     │  │  Commands   │   │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘   │
│         │                │                │                │          │
│         ▼                ▼                ▼                ▼          │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │                      INGESTION LAYER                            │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │  │
│  │  │ SignalParser │  │ PriceFeed    │  │ NewsAnalyzer │          │  │
│  │  │ (MoonBot)    │  │ (Binance WS) │  │ (RSS + AI)   │          │  │
│  │  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘          │  │
│  └─────────┼─────────────────┼─────────────────┼──────────────────┘  │
│            │                 │                 │                      │
│            ▼                 ▼                 ▼                      │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │                         EVENT BUS                               │  │
│  │  Channels: signals | prices | predictions | trades | outcomes  │  │
│  │  Format: JSONL + sha256 checksum                                │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│            │                 │                 │                      │
│            ▼                 ▼                 ▼                      │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │                      AI GATEWAY LAYER                           │  │
│  │                                                                 │  │
│  │  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐   │  │
│  │  │  Regime    │ │  Anomaly   │ │ Sentiment  │ │  Predictor │   │  │
│  │  │  Detector  │ │  Scanner   │ │  Analyzer  │ │  (XGBoost) │   │  │
│  │  └────────────┘ └────────────┘ └────────────┘ └────────────┘   │  │
│  │                                                                 │  │
│  │  ┌────────────────────────────────────────────────────────┐    │  │
│  │  │              SELF-IMPROVING LOOP                       │    │  │
│  │  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐   │    │  │
│  │  │  │Outcome  │→ │ Model   │→ │  A/B    │→ │ Circuit │   │    │  │
│  │  │  │Tracker  │  │ Trainer │  │ Tester  │  │ Breaker │   │    │  │
│  │  │  └─────────┘  └─────────┘  └─────────┘  └─────────┘   │    │  │
│  │  └────────────────────────────────────────────────────────┘    │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│            │                                                          │
│            ▼                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │                      DECISION ENGINE                            │  │
│  │                                                                 │  │
│  │  IF regime == TRENDING                                          │  │
│  │     AND anomaly_score < 0.3                                     │  │
│  │     AND prediction_prob > 0.65                                  │  │
│  │     AND circuit_breaker == CLOSED                               │  │
│  │  THEN → BUY                                                     │  │
│  │  ELSE → SKIP                                                    │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│            │                                                          │
│            ▼                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │                      EXECUTION LAYER                            │  │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐                │  │
│  │  │   HOPE     │→ │   Risk     │→ │  Binance   │                │  │
│  │  │  ENGINE    │  │  Manager   │  │   API      │                │  │
│  │  └────────────┘  └────────────┘  └────────────┘                │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│            │                                                          │
│            ▼                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │                      OBSERVABILITY LAYER                        │  │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐                │  │
│  │  │  Telegram  │  │ Dashboard  │  │   Alerts   │                │  │
│  │  │   Panel    │  │  (Web UI)  │  │  (Metrics) │                │  │
│  │  └────────────┘  └────────────┘  └────────────┘                │  │
│  └─────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────┘
```

---

## 4. МОДУЛИ ДЛЯ РЕАЛИЗАЦИИ

### 4.1 EVENT BUS (CRITICAL)

**Файл:** `ai_gateway/core/event_bus.py`

```python
class Event:
    id: str           # UUID v4
    type: EventType   # SIGNAL | PRICE | PREDICTION | TRADE | OUTCOME
    timestamp: str    # ISO 8601
    payload: dict     # Typed per event type
    checksum: str     # sha256:...

class EventBus:
    def publish(event: Event) -> None: ...
    def subscribe(type: EventType, callback: Callable) -> Subscription: ...
    def replay(from_ts: str, to_ts: str) -> Iterator[Event]: ...
```

**Invariants:**
- Все события персистятся в `state/events/{type}.jsonl`
- Atomic append (temp → fsync → rename)
- Checksum validation при чтении

---

### 4.2 BINANCE PRICE FEED (CRITICAL)

**Файл:** `ai_gateway/feeds/binance_ws.py`

```python
class PriceUpdate:
    symbol: str
    price: Decimal
    volume: Decimal
    timestamp: str

class BinancePriceFeed:
    async def connect() -> None: ...
    async def subscribe(symbols: List[str]) -> None: ...
    def on_price(callback: Callable[[PriceUpdate], None]) -> None: ...
```

**Invariants:**
- Reconnect with exponential backoff
- Heartbeat every 30s
- Publish to EventBus channel: prices

---

### 4.3 CIRCUIT BREAKER (CRITICAL)

**Файл:** `ai_gateway/core/circuit_breaker.py`

```
State Machine:
CLOSED ──[5 losses]──► OPEN
   ▲                      │
   │                      │ [cooldown 5min]
   │                      ▼
   └──[3 wins]──── HALF_OPEN

States:
  CLOSED: Normal trading
  OPEN: No trading, only logging
  HALF_OPEN: Allow 1 trade, evaluate
```

---

### 4.4 DECISION ENGINE (HIGH)

**Файл:** `ai_gateway/core/decision_engine.py`

```python
class Decision:
    signal_id: str
    action: str           # BUY | SKIP | SELL
    confidence: float
    reasons: List[str]
    checks_passed: Dict[str, bool]

def evaluate(signal: MoonBotSignal) -> Decision:
    checks = {
        "regime_ok": regime in [TRENDING, VOLATILE_UP],
        "anomaly_ok": anomaly_score < 0.3,
        "prediction_ok": prediction.probability > 0.65,
        "circuit_ok": circuit_breaker == CLOSED,
        "volume_ok": signal.volume_24h > 5_000_000,
        "time_ok": not in_blackout_period(),
    }

    if all(checks.values()):
        return Decision(action=BUY)
    else:
        return Decision(action=SKIP, reasons=[k for k,v in checks.items() if not v])
```

---

### 4.5 AI CHAT BRIDGE ("Чат друзей")

**Файл:** `ai_gateway/chat/bridge.py`

**Концепция:** Прямая связь Human ↔ AI через файловый протокол.

```
state/chat/
├── inbox.jsonl       # Human → AI
├── outbox.jsonl      # AI → Human
└── thoughts.jsonl    # AI internal reasoning

Message format:
{
    "id": "uuid",
    "timestamp": "ISO8601",
    "from": "human" | "ai",
    "type": "command" | "question" | "idea" | "alert",
    "content": "...",
    "context": {}
}
```

**Интеграция:**
- Claude Code мониторит `inbox.jsonl`
- Ответы пишет в `outbox.jsonl`
- Telegram бот может читать/писать в чат файлы
- DDO может использовать для multi-AI дискуссий

---

## 5. DATA CONTRACTS

### 5.1 SIGNAL (MoonBot → EventBus)

```json
{
    "schema": "signal:v1",
    "checksum": "sha256:abc123...",
    "data": {
        "id": "sig:20260129:091543:SENTUSDT",
        "timestamp": "2026-01-29T09:15:43Z",
        "symbol": "SENTUSDT",
        "price": "0.030010",
        "delta_pct": 1.92,
        "strategy": "TopMarketDetect",
        "volume_24h": 46000000,
        "dbtc": 0.02,
        "dbtc_5m": 0.03,
        "dbtc_1m": 0.00,
        "dmarkets": 0.12,
        "buys_per_sec": null,
        "vol_raise_pct": null
    }
}
```

### 5.2 PREDICTION (Predictor → EventBus)

```json
{
    "schema": "prediction:v1",
    "checksum": "sha256:def456...",
    "data": {
        "id": "pred:20260129:091543:SENTUSDT",
        "signal_id": "sig:20260129:091543:SENTUSDT",
        "probability": 0.72,
        "recommendation": "BUY",
        "confidence": 0.85,
        "model_version": 3
    }
}
```

### 5.3 OUTCOME (OutcomeTracker → EventBus)

```json
{
    "schema": "outcome:v1",
    "checksum": "sha256:ghi789...",
    "data": {
        "id": "out:20260129:091543:SENTUSDT:5m",
        "signal_id": "sig:20260129:091543:SENTUSDT",
        "horizon": "5m",
        "entry_price": "0.030010",
        "exit_price": "0.030850",
        "mfe": 3.2,
        "mae": -0.5,
        "profit_pct": 2.8,
        "label": "WIN"
    }
}
```

---

## 6. IMPLEMENTATION PHASES

### PHASE 3: INFRASTRUCTURE (Current)

```
□ Event Bus с atomic writes
□ Binance WebSocket Price Feed
□ Circuit Breaker implementation
□ Integration tests

Deliverables:
├── ai_gateway/core/event_bus.py
├── ai_gateway/feeds/binance_ws.py
├── ai_gateway/core/circuit_breaker.py
└── tests/test_phase3.py

Exit Criteria:
├── События персистятся в JSONL
├── Цены приходят в реальном времени
├── Circuit breaker работает
└── 100% test coverage для core
```

### PHASE 4: DECISION ENGINE

```
□ Decision Engine с rule-based logic
□ Risk Manager integration
□ Telegram Panel обновление
□ AI Chat Bridge

Exit Criteria:
├── BUY/SKIP решения логируются
├── Telegram показывает все статусы
├── Chat bridge функционирует
└── No trades without all checks PASS
```

### PHASE 5: OBSERVABILITY

```
□ Web Dashboard (Streamlit/React)
□ Real-time metrics
□ Alerting система

Exit Criteria:
├── Dashboard показывает live data
├── Алерты приходят в Telegram
├── < 100ms latency UI updates
```

### PHASE 6: PRODUCTION

```
□ Full integration с HOPE ENGINE
□ TESTNET прогон 24h
□ Audit всех компонентов

Exit Criteria:
├── 24h без errors на TESTNET
├── Win Rate > 60% на test data
├── Human approval для LIVE
```

---

## 7. TELEGRAM COMMANDS

```
/ai          - Dashboard всех модулей
/signal      - Последние 5 сигналов
/predict     - Ручной запрос предсказания
/trade       - Статус торговли
/circuit     - Статус circuit breaker
/retrain     - Форсировать ретрейн
/rollback    - Откатить модель
/stop        - Аварийная остановка
/start       - Возобновить торговлю

Inline Buttons:
[🟢 Start] [🔴 Stop] [♻️ Restart]
[📊 Regime] [🚨 Anomaly] [🧠 Predictor]
[⚙️ Settings] [📈 Stats] [📋 Logs]
```

---

## 8. SAFETY INVARIANTS

### INVARIANT 1: No Trade Without All Checks

```python
def execute_trade(decision: Decision) -> bool:
    required_checks = [
        "regime_ok", "anomaly_ok", "prediction_ok",
        "circuit_ok", "volume_ok", "time_ok"
    ]

    if not all(decision.checks_passed.get(c) for c in required_checks):
        return False  # FAIL-CLOSED

    return execute_order(decision)
```

### INVARIANT 2: Atomic Model Updates

```python
def deploy_model(new_model: Path, version: int) -> bool:
    temp_path = new_model.with_suffix('.tmp')
    # 1. Write to temp
    # 2. Verify checksum
    # 3. Atomic replace
    # 4. Update registry
```

### INVARIANT 3: Circuit Breaker Protection

```
5 consecutive losses → OPEN (no trading)
3 consecutive wins in HALF_OPEN → CLOSED
```

---

## 9. ФАЙЛЫ ДЛЯ РЕАЛИЗАЦИИ (по приоритету)

### CRITICAL (Phase 3):
```
├── ai_gateway/core/event_bus.py
├── ai_gateway/feeds/binance_ws.py
├── ai_gateway/core/circuit_breaker.py
└── ai_gateway/core/decision_engine.py
```

### HIGH (Phase 4):
```
├── ai_gateway/chat/bridge.py
├── ai_gateway/telegram/commands.py (upgrade)
└── dashboard/app.py
```

### MEDIUM (Phase 5):
```
├── ai_gateway/metrics.py
├── deploy/docker-compose.yml
└── docs/RUNBOOK.md
```

---

## 10. NEXT ACTIONS

1. **Накопление данных:** Собрать 100+ MoonBot сигналов с outcomes
2. **Price Feed:** Реализовать Binance WebSocket
3. **Circuit Breaker:** Защита от серийных потерь
4. **Event Bus:** Унификация потока данных

---

**SSoT:** Этот документ является единственным источником истины для ТЗ проекта HOPE.

**Обновлено:** 2026-01-29 10:30:00 UTC
**Автор:** Claude (opus-4)
**Checkpoint:** Phase 2.5 Complete - Self-Improving Loop
