# HOPE AI TRADING SYSTEM — TZ v5.0 (TESTING & DEVELOPMENT)

<!-- AI SIGNATURE: Created by Claude (opus-4) at 2026-01-29 17:30:00 UTC -->

## METADATA

| Field | Value |
|-------|-------|
| Version | 5.0 |
| Date | 2026-01-29 |
| Author | Claude (opus-4) + Valentin |
| SSoT | docs/HOPE_AI_TZ_v5_TESTING.md |
| Previous | docs/HOPE_AI_TRADING_TZ_v4.md |
| Status | ACTIVE |

---

## PART 0: ТЕКУЩЕЕ СОСТОЯНИЕ (ACTUAL)

### Диагностика от 2026-01-29

```
SUMMARY:
├── Total Components: 38
├── OK:           30 (79%)
├── BROKEN:        0 (0%)
├── MISSING:       0 (0%)
├── NOT IMPL:      8 (21%) - опциональные

PHASE COMPLETION:
├── base                 [####################] 100.0%  ✅
├── 3.1                  [#################---]  85.7%  ✅
├── secret_ideas_p1      [##########----------]  50.0%  🔄
├── secret_ideas_p2-p6   [--------------------]   0.0%  ⏳
```

### Что РАБОТАЕТ (проверено тестами)

| Компонент | Файл | Статус | Тест |
|-----------|------|--------|------|
| Event Bus | `ai_gateway/core/event_bus.py` | ✅ PASS | `test_ai_gateway.py` |
| Decision Engine | `ai_gateway/core/decision_engine.py` | ✅ PASS | 4/4 checks |
| Mode Router | `ai_gateway/core/mode_router.py` | ✅ PASS | 5/5 routes |
| Signal Processor | `ai_gateway/core/signal_processor.py` | ✅ PASS | async |
| Circuit Breaker | `ai_gateway/core/circuit_breaker.py` | ✅ PASS | state machine |
| Pump Precursor | `ai_gateway/patterns/pump_precursor_detector.py` | ✅ PASS | 3/4 signals |
| MoonBot Live | `ai_gateway/integrations/moonbot_live.py` | ✅ PASS | 5/5 pipeline |
| Binance WS | `ai_gateway/feeds/binance_ws.py` | ✅ PASS | REST fallback |
| Outcome Tracker | `ai_gateway/modules/self_improver/outcome_tracker.py` | ✅ PASS | MFE/MAE |
| Sources Manager | `scripts/sources_manager.py` | ✅ PASS | 19/20 active |
| Diagnostic | `hope_diagnostic.py` | ✅ PASS | 72 checks |

### Данные

| Тип | Путь | Количество |
|-----|------|------------|
| MoonBot Signals | `data/moonbot_signals/signals_20260129.jsonl` | 227 |
| AI Model | `ai_gateway/models/hope_model_v1.json` | 136 samples |
| Decisions | `state/ai/decisions.jsonl` | 10 records |
| Sources | `state/sources/sources.json` | 20 endpoints |

---

## PART 1: ЧТО ПРАВИЛЬНО

### 1.1 Архитектура (CORRECT)

```
✅ Fail-closed design — все проверки должны PASS для BUY
✅ Atomic writes — temp → fsync → replace
✅ SHA256 checksums — контракты данных
✅ Mode Router — SUPER_SCALP/SCALP/SWING/SKIP
✅ Circuit Breaker — 3/5 losses → OPEN
✅ Self-Improving Loop infrastructure
```

### 1.2 Pipeline (CORRECT)

```
MoonBot Signal
     │
     ▼
PumpPrecursorDetector (4 signals: vol_raise, buys/sec, accelerating, delta_seq)
     │
     ▼
ModeRouter (classify → SUPER_SCALP/SCALP/SWING/SKIP)
     │
     ▼
DecisionEngine (8 checks: regime, anomaly, prediction, circuit, volume, news, cooldown, positions)
     │
     ▼
decisions.jsonl + EventBus
```

### 1.3 Тесты (CORRECT)

```bash
# Все тесты проходят
python -m scripts.test_ai_gateway        # 4/4 PASS
python hope_diagnostic.py                 # 0 FAIL
python -m ai_gateway.integrations.moonbot_live --test  # 5/5 PASS
```

---

## PART 2: ГДЕ ОШИБКИ/РИСКИ

### 2.1 Критические (P0)

| Риск | Описание | Impact | Митигация |
|------|----------|--------|-----------|
| **No Real Prices** | OutcomeTracker использует симуляцию | MFE/MAE неточные | Binance WS real-time |
| **No Persistence** | PrecursorDetector теряет историю при restart | Потеря контекста | Сохранять в JSONL |
| **Single Thread** | Pipeline не масштабируется | Latency при нагрузке | asyncio + queue |
| **datetime.utcnow()** | Deprecated в Python 3.12+ | Warnings в логах | Заменить везде |

### 2.2 Высокие (P1)

| Риск | Описание | Impact | Митигация |
|------|----------|--------|-----------|
| **No Retry** | Binance WS disconnect = потеря | Пропуск сигналов | Exponential backoff |
| **Orphaned Files** | 16 файлов не в spec | Путаница | Обновить spec или удалить |
| **No Rate Limit** | Binance может забанить | Service down | Token bucket |
| **Hardcoded Thresholds** | vol_raise > 50% etc. | Suboptimal | Auto-tuning |

### 2.3 Средние (P2)

| Риск | Описание | Impact | Митигация |
|------|----------|--------|-----------|
| **No Backtest** | Нельзя проверить на истории | Blind trading | Replay engine |
| **No Metrics** | Нет Prometheus/StatsD | No observability | Add metrics |
| **No Alerts** | Только логи | Late detection | Telegram alerts |

---

## PART 3: ЧТО НАДО УТОЧНИТЬ

### 3.1 Бизнес-вопросы

1. **Режим торговли:**
   - DRY (только логи) → TESTNET → LIVE?
   - Какой капитал на TESTNET?

2. **Пороги:**
   - `prediction_min: 0.65` — откуда?
   - `volume_min_24h: 5M` — для всех монет?

3. **MoonBot source:**
   - Файл или Telegram forward?
   - Latency от сигнала до нас?

### 3.2 Технические вопросы

1. **Binance WS:**
   - Testnet или Mainnet для prices?
   - Какие streams: trade, kline, depth?

2. **Model:**
   - rule-based достаточно или нужен ML?
   - Когда retrain: 100 outcomes? 500?

---

## PART 4: ФИЧИ ДЛЯ PRODUCTION

### 4.1 P0 — КРИТИЧНО (без этого не деплоим)

```python
# 1. HEARTBEAT MONITOR
class HeartbeatMonitor:
    """Detect stuck pipelines, auto-restart."""
    MAX_SILENCE_SEC = 60

    async def monitor(self):
        while True:
            if time.time() - self.last_activity > self.MAX_SILENCE_SEC:
                self.alert("Pipeline stuck")
                await self.restart_pipeline()
            await asyncio.sleep(10)

# 2. STATE PERSISTENCE
class StatePersister:
    """Save detector state to disk."""

    def save_detector_state(self, detector: PumpPrecursorDetector):
        state = {
            "signal_history": detector.signal_history,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        self._atomic_write("state/ai/detector_state.json", state)

# 3. DEAD LETTER QUEUE
class DeadLetterQueue:
    """Don't lose signals on processing errors."""
    DLQ_PATH = Path("state/ai/dlq.jsonl")

    def enqueue(self, signal: Dict, error: str):
        record = {
            "signal": signal,
            "error": error,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._append_jsonl(self.DLQ_PATH, record)
```

### 4.2 P1 — ВАЖНО (для надёжности)

```python
# 4. RATE LIMITER
class RateLimiter:
    """Prevent API ban and signal flood."""

    def __init__(self, max_per_second: int = 10):
        self.tokens = max_per_second
        self.last_refill = time.time()

    async def acquire(self) -> bool:
        self._refill()
        if self.tokens > 0:
            self.tokens -= 1
            return True
        return False

# 5. METRICS EXPORTER
class MetricsExporter:
    """Prometheus-compatible metrics."""

    def record_signal(self, symbol: str, mode: str, action: str):
        self.signals_total.labels(symbol=symbol, mode=mode, action=action).inc()
        self.signal_latency.observe(latency_ms)

# 6. TELEGRAM ALERTER
class TelegramAlerter:
    """Real-time alerts to Telegram."""

    async def alert(self, level: str, message: str):
        if level in ["ERROR", "CRITICAL"]:
            await self.bot.send_message(
                self.admin_chat_id,
                f"🚨 {level}: {message}"
            )
```

### 4.3 P2 — УЛУЧШЕНИЯ (для качества)

```python
# 7. REPLAY ENGINE
class ReplayEngine:
    """Backtest on historical data."""

    async def replay(self, from_date: str, to_date: str):
        signals = self._load_historical_signals(from_date, to_date)
        for signal in signals:
            decision = await self.pipeline.process(signal)
            self._record_backtest_result(signal, decision)

# 8. A/B ROUTER
class ABRouter:
    """Route % of signals to experimental model."""

    def route(self, signal: Dict) -> str:
        if random.random() < self.experiment_ratio:
            return "experimental"
        return "production"

# 9. AUTO THRESHOLD TUNER
class ThresholdTuner:
    """Optimize thresholds based on outcomes."""

    def tune(self, outcomes: List[Outcome]) -> Dict[str, float]:
        # Grid search for optimal thresholds
        best_params = self._grid_search(outcomes)
        return best_params
```

---

## PART 5: ПЛАН ТЕСТИРОВАНИЯ

### 5.1 Unit Tests (существующие)

```bash
# Запустить все
python -m scripts.test_ai_gateway

# Ожидаемый результат:
# [OK] EventBus: PASS
# [OK] DecisionEngine: PASS
# [OK] PriceFeed: PASS
# [OK] OutcomeTracker: PASS
```

### 5.2 Integration Tests (новые)

```python
# tests/test_full_pipeline.py

async def test_signal_to_decision():
    """Signal flows through entire pipeline."""
    signal = create_test_signal(delta=10, buys=50)

    # Process
    result = await pipeline.process(signal)

    # Assert
    assert result.precursor_prediction == "BUY"
    assert result.mode == "super_scalp"
    assert result.final_action == "BUY"

async def test_circuit_breaker_triggers():
    """5 losses trigger circuit breaker."""
    for i in range(5):
        await record_loss()

    assert circuit_breaker.state == CircuitState.OPEN
    assert not pipeline.can_trade()

async def test_persistence_survives_restart():
    """State persists across restarts."""
    await detector.add_signal(signal)
    await persister.save()

    # Simulate restart
    new_detector = PumpPrecursorDetector.load_state()
    assert signal in new_detector.signal_history
```

### 5.3 Load Tests

```bash
# Simulate 100 signals/second
python -m scripts.load_test --rps 100 --duration 60

# Expected:
# - No dropped signals
# - Latency p99 < 100ms
# - Memory stable
```

### 5.4 Chaos Tests

```python
# tests/test_chaos.py

async def test_binance_disconnect():
    """Pipeline survives WS disconnect."""
    await ws_feed.connect()
    await ws_feed.simulate_disconnect()
    await asyncio.sleep(5)

    assert ws_feed.is_connected  # Auto-reconnected

async def test_partial_data():
    """Handles missing fields gracefully."""
    signal = {"symbol": "BTCUSDT"}  # Missing delta, volume

    result = await pipeline.process(signal)
    assert result.final_action == "SKIP"  # Fail-closed
```

---

## PART 6: ROADMAP

### Phase 2: Binance WS Enrichment (3 дня)

```
Цель: Real-time цены для точного MFE/MAE

Задачи:
□ Подключить binance_ws.py к EventBus
□ Создать PriceFeed singleton с кэшем
□ Обновить OutcomeTracker для real prices
□ Тест: цена обновляется < 100ms

Файлы:
├── ai_gateway/feeds/binance_ws.py (update)
├── ai_gateway/core/price_cache.py (create)
└── tests/test_realtime_prices.py (create)

Verification:
python -c "from ai_gateway.feeds import get_price; print(get_price('BTCUSDT'))"
```

### Phase 3: Outcome Tracking MFE/MAE (2 дня)

```
Цель: Реальные метрики для обучения модели

Задачи:
□ Интеграция с PriceFeed
□ Отслеживание на горизонтах: 1m, 5m, 15m, 60m
□ Автоматическая маркировка: WIN/LOSS/FLAT
□ JSONL export для training

Файлы:
├── ai_gateway/modules/self_improver/outcome_tracker.py (update)
└── state/ai/outcomes.jsonl (auto-created)

Verification:
python -c "from ai_gateway.modules.self_improver import get_win_rate; print(get_win_rate())"
```

### Phase 4: Telegram Commands (2 дня)

```
Цель: Мониторинг и управление через Telegram

Команды:
/predict SYMBOL  - Запросить предсказание
/status          - Состояние системы
/stats           - Статистика (win rate, P&L)
/circuit         - Circuit breaker status
/stop            - Экстренная остановка

Файлы:
├── ai_gateway/telegram/commands.py (create)
└── ai_gateway/telegram/__init__.py (create)

Verification:
# В Telegram отправить /predict BTCUSDT
```

### Phase 5: Live Trading (3 дня)

```
Цель: Интеграция с HOPE Engine

Задачи:
□ Подключение к run_live_v5.py
□ Order execution через Binance API
□ Position tracking
□ P&L calculation

Файлы:
├── ai_gateway/execution/order_manager.py (create)
├── ai_gateway/execution/position_tracker.py (create)
└── core/run_live_v6.py (update)

Verification:
# TESTNET: execute 3 trades, verify fills
```

### Phase 6: ML Model v2 (5 дней)

```
Цель: Улучшить accuracy с 68% до 75%+

Задачи:
□ Feature engineering (lag features, technicals)
□ XGBoost/LightGBM training
□ A/B testing framework
□ Auto-retrain on 100 outcomes

Файлы:
├── ai_gateway/modules/predictor/ml_classifier.py (create)
├── ai_gateway/modules/predictor/features.py (create)
└── ai_gateway/modules/self_improver/ab_tester.py (update)

Verification:
python -c "from ai_gateway.modules.predictor import train_model; train_model()"
```

---

## PART 7: VERIFICATION COMMANDS

```bash
# ═══════════════════════════════════════════════════════════════
# DAILY CHECKS
# ═══════════════════════════════════════════════════════════════

# 1. System health
python hope_diagnostic.py

# 2. Sources status
python -m scripts.sources_manager check

# 3. Integration tests
python -m scripts.test_ai_gateway

# 4. Pipeline test
python -m ai_gateway.integrations.moonbot_live --test

# ═══════════════════════════════════════════════════════════════
# BEFORE DEPLOY
# ═══════════════════════════════════════════════════════════════

# 1. Syntax check all
python -m py_compile ai_gateway/**/*.py

# 2. Full test suite
python -m pytest tests/ -v

# 3. Market intel fresh
python -m scripts.update_market_intel

# 4. Git status clean
git status

# ═══════════════════════════════════════════════════════════════
# MONITORING
# ═══════════════════════════════════════════════════════════════

# Watch decisions in real-time
Get-Content state/ai/decisions.jsonl -Wait -Tail 10

# Check circuit breaker
python -c "from ai_gateway.core import get_circuit_breaker; print(get_circuit_breaker().get_status())"

# Signal count
python -c "print(len(open('data/moonbot_signals/signals_20260129.jsonl').readlines()))"
```

---

## PART 8: ACCEPTANCE CRITERIA

### Для перехода в TESTNET:

- [ ] `hope_diagnostic.py` → 0 FAIL
- [ ] `test_ai_gateway.py` → 4/4 PASS
- [ ] `moonbot_live --test` → 5/5 PASS
- [ ] Binance WS connected (real prices)
- [ ] Circuit breaker tested (5 losses → OPEN)
- [ ] 24h без ошибок в логах

### Для перехода в LIVE:

- [ ] 7 дней TESTNET без критических ошибок
- [ ] Win Rate > 60% на TESTNET
- [ ] Max Drawdown < 15%
- [ ] Manual sign-off от Valentin
- [ ] Rollback plan готов

---

## PART 9: QUICK START

```bash
# 1. Проверить систему
cd C:\Users\kirillDev\Desktop\TradingBot\minibot
python hope_diagnostic.py

# 2. Запустить тесты
python -m scripts.test_ai_gateway
python -m ai_gateway.integrations.moonbot_live --test

# 3. Обновить market intel
python -m scripts.update_market_intel

# 4. Watch mode (real-time мониторинг)
python -m ai_gateway.integrations.moonbot_live --watch
```

---

## CHECKSUM

```
Document: HOPE_AI_TZ_v5_TESTING.md
Version: 5.0
Generated: 2026-01-29T17:30:00Z
```
