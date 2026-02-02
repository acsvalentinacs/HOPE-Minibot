# HOPE AI TRADING SYSTEM - IMPLEMENTATION TZ v1.0
# Technical Specification for Next Phase

<!-- AI SIGNATURE: Created by Claude (opus-4.5) at 2026-02-02 15:50:00 UTC -->

---

## СТАТУС ПРОЕКТА (на 2026-02-02)

```
╔═══════════════════════════════════════════════════════════════════╗
║                    HOPE SYSTEM STATUS                              ║
╠═══════════════════════════════════════════════════════════════════╣
║  Баланс:           $89.70 USDT                                    ║
║  Режим:            LIVE (Binance Mainnet)                         ║
║  Win Rate:         52% (50 trades)                                ║
║  Архитектура:      Two-Chamber (Alpha + Risk)                     ║
║  Event System:     Hybrid (File + Memory Bus) - НОВОЕ!            ║
║  Auto-Start:       Multi-Layer Control - НОВОЕ!                   ║
║  Confidence:       Three-Tier Adaptive - НОВОЕ!                   ║
╚═══════════════════════════════════════════════════════════════════╝
```

---

## ЧАСТЬ 1: ЧТО РЕАЛИЗОВАНО (Baseline)

### 1.1 Core Components

| Компонент | Файл | Статус |
|-----------|------|--------|
| Eye of God V3 | `scripts/eye_of_god_v3.py` | ✅ Production |
| AutoTrader | `scripts/autotrader.py` | ✅ Production |
| Order Executor | `scripts/order_executor.py` | ✅ Production |
| Momentum Trader | `scripts/momentum_trader.py` | ✅ Production |
| Pricefeed Gateway | `scripts/pricefeed_gateway.py` | ✅ Production |
| Unified AllowList | `core/unified_allowlist.py` | ✅ Production |

### 1.2 New Infrastructure (This Session)

| Компонент | Файл | Статус |
|-----------|------|--------|
| Event Schema | `core/events/event_schema.py` | ✅ Ready |
| Event Bus | `core/events/event_bus.py` | ✅ Ready |
| Diagnostics | `scripts/hope_diagnostics.py` | ✅ Ready |
| Health Daemon | `scripts/hope_health_daemon.py` | ✅ Ready |
| Auto-Start | `tools/hope_autostart.ps1` | ✅ Ready |

---

## ЧАСТЬ 2: УЛУЧШЕНИЯ ОТ CLAUDE (Рекомендации)

### 2.1 P0: КРИТИЧЕСКИ ВАЖНЫЕ

#### 2.1.1 Real-Time Signal Bus Integration

**Проблема:** Сигналы идут через HTTP (100ms+ latency).
**Решение:** Интегрировать Event Bus в signal flow.

```python
# В momentum_trader.py после генерации сигнала:
from core.events import get_event_bus, SignalEvent, create_correlation_id

async def emit_signal(signal: MomentumSignal):
    bus = get_event_bus()
    event = SignalEvent(
        correlation_id=create_correlation_id("mom"),
        timestamp=signal.timestamp,
        payload={
            "symbol": signal.symbol,
            "price": signal.price,
            "gain_24h": signal.gain_24h,
            "strategy": "MOMENTUM_24H",
        }
    )
    await bus.publish(event)

# В autotrader.py подписаться:
@bus.on("SIGNAL")
async def handle_signal(event):
    decision = eye_of_god.decide(event.payload)
    # ...
```

**Benefit:** Latency 1ms вместо 100ms.

#### 2.1.2 Position Watchdog as Event Consumer

**Проблема:** Watchdog работает через polling.
**Решение:** Сделать его consumer в Event Bus.

```python
# position_watchdog.py
@bus.on("FILL")
async def on_fill(event):
    """Register position when order filled."""
    register_position_for_watching(
        position_id=event.payload["order_id"],
        symbol=event.payload["symbol"],
        entry_price=event.payload["avg_price"],
        # ...
    )

@bus.on("CLOSE")
async def on_close(event):
    """Remove position when closed."""
    unregister_position(event.payload["position_id"])
```

### 2.2 P1: ВАЖНЫЕ

#### 2.2.1 Dead Letter Queue Handler

**Проблема:** Потерянные события при ошибках.
**Решение:** DLQ с retry logic.

```python
# core/events/dlq_handler.py
class DLQHandler:
    """Handles failed events from Dead Letter Queue."""

    async def process_dlq(self, max_retries: int = 3):
        bus = get_event_bus()
        events = await bus.get_dlq_events(limit=100)

        for item in events:
            event = HopeEvent.from_dict(item["event"])
            retry_count = item.get("retry_count", 0)

            if retry_count < max_retries:
                # Re-publish with incremented retry count
                await bus.publish(event)
                log.info(f"Retry {retry_count+1} for {event.event_id}")
            else:
                # Move to permanent failure log
                self._log_permanent_failure(item)
```

#### 2.2.2 Metrics & Observability

**Проблема:** Нет real-time метрик.
**Решение:** Prometheus-style metrics.

```python
# core/metrics.py
from dataclasses import dataclass, field
from datetime import datetime, timezone

@dataclass
class HopeMetrics:
    """Trading system metrics."""
    signals_received: int = 0
    signals_traded: int = 0
    signals_skipped: int = 0
    positions_opened: int = 0
    positions_closed: int = 0
    total_pnl_usdt: float = 0.0
    win_count: int = 0
    loss_count: int = 0

    # Latency tracking
    signal_to_decision_ms: list = field(default_factory=list)
    decision_to_order_ms: list = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        total = self.win_count + self.loss_count
        return self.win_count / total if total > 0 else 0.0

    def to_prometheus(self) -> str:
        """Export as Prometheus format."""
        return f"""
# HELP hope_signals_total Total signals received
# TYPE hope_signals_total counter
hope_signals_total{{status="received"}} {self.signals_received}
hope_signals_total{{status="traded"}} {self.signals_traded}
hope_signals_total{{status="skipped"}} {self.signals_skipped}

# HELP hope_pnl_usdt Total PnL in USDT
# TYPE hope_pnl_usdt gauge
hope_pnl_usdt {self.total_pnl_usdt}

# HELP hope_win_rate Current win rate
# TYPE hope_win_rate gauge
hope_win_rate {self.win_rate}
"""
```

#### 2.2.3 Circuit Breaker Per Service

**Проблема:** Один circuit breaker на всю систему.
**Решение:** Per-service circuit breakers.

```python
# core/circuit_breaker.py
class ServiceCircuitBreaker:
    """Circuit breaker for individual service."""

    def __init__(self, name: str, threshold: int = 3, reset_sec: int = 300):
        self.name = name
        self.threshold = threshold
        self.reset_sec = reset_sec
        self.failures = 0
        self.last_failure = None
        self.is_open = False

    def record_failure(self):
        self.failures += 1
        self.last_failure = time.time()
        if self.failures >= self.threshold:
            self.is_open = True
            log.warning(f"Circuit breaker OPEN for {self.name}")

    def record_success(self):
        self.failures = 0
        self.is_open = False

    def can_execute(self) -> bool:
        if not self.is_open:
            return True
        # Check if reset time passed
        if time.time() - self.last_failure > self.reset_sec:
            self.is_open = False
            self.failures = 0
            return True
        return False

# Usage:
breakers = {
    "binance_api": ServiceCircuitBreaker("binance_api"),
    "momentum_trader": ServiceCircuitBreaker("momentum_trader"),
    "pricefeed": ServiceCircuitBreaker("pricefeed"),
}
```

### 2.3 P2: УЛУЧШЕНИЯ

#### 2.3.1 Signal Quality Scoring (ML Enhancement)

**Идея:** Использовать ML для улучшения scoring.

```python
# ai_gateway/modules/signal_scorer.py
class MLSignalScorer:
    """ML-based signal quality scoring."""

    def __init__(self, model_path: str):
        self.model = self._load_model(model_path)
        self.feature_names = [
            "gain_24h", "gain_1h", "volume_ratio",
            "position_in_range", "btc_correlation",
            "hour_of_day", "day_of_week"
        ]

    def score(self, signal: Dict) -> float:
        """Score signal quality 0.0 - 1.0."""
        features = self._extract_features(signal)
        return float(self.model.predict_proba([features])[0][1])

    def _extract_features(self, signal: Dict) -> list:
        return [
            signal.get("gain_24h", 0) / 100,
            signal.get("gain_1h", 0) / 10,
            signal.get("volume_usd", 0) / 100_000_000,
            signal.get("position_in_range", 50) / 100,
            signal.get("btc_correlation", 0.5),
            datetime.now().hour / 24,
            datetime.now().weekday() / 7,
        ]
```

#### 2.3.2 Adaptive Position Sizing (Kelly Criterion)

**Идея:** Динамический размер позиции по Kelly.

```python
# core/position_sizer.py
class KellyPositionSizer:
    """Kelly Criterion based position sizing."""

    def __init__(self, win_rate: float, avg_win: float, avg_loss: float):
        self.win_rate = win_rate
        self.avg_win = avg_win
        self.avg_loss = avg_loss

    def calculate_fraction(self) -> float:
        """Calculate Kelly fraction (0.0 - 1.0)."""
        if self.avg_loss == 0:
            return 0.0

        # Kelly formula: f = (bp - q) / b
        # where b = avg_win/avg_loss, p = win_rate, q = 1-p
        b = self.avg_win / abs(self.avg_loss)
        p = self.win_rate
        q = 1 - p

        kelly = (b * p - q) / b

        # Half-Kelly for safety
        return max(0, min(0.5, kelly / 2))

    def size_position(self, balance: float, max_pct: float = 0.25) -> float:
        """Calculate position size in USDT."""
        kelly_pct = self.calculate_fraction()
        actual_pct = min(kelly_pct, max_pct)
        return balance * actual_pct
```

---

## ЧАСТЬ 3: ПЛАН РЕАЛИЗАЦИИ

### Phase 1: Event Bus Integration (Week 1)

```
Задачи:
□ Интегрировать Event Bus в momentum_trader
□ Интегрировать Event Bus в autotrader
□ Добавить DecisionEvent после Eye of God
□ Добавить OrderEvent после executor
□ Тесты

Критерии завершения:
- События проходят через bus
- File logging сохраняется (dual-write)
- Latency < 10ms для signal→decision
```

### Phase 2: Health & Monitoring (Week 2)

```
Задачи:
□ DLQ Handler с retry logic
□ Per-service circuit breakers
□ Metrics endpoint (/metrics)
□ Grafana dashboard (опционально)

Критерии завершения:
- DLQ обрабатывает failed events
- Circuit breakers изолируют отказы
- Метрики доступны по HTTP
```

### Phase 3: ML Enhancements (Week 3-4)

```
Задачи:
□ MLSignalScorer training на historical data
□ Kelly position sizer integration
□ A/B testing framework
□ Model deployment pipeline

Критерии завершения:
- ML scorer увеличивает WR на 5%+
- Position sizing адаптивный
- Модель обновляется автоматически
```

---

## ЧАСТЬ 4: АРХИТЕКТУРНЫЕ ДИАГРАММЫ

### 4.1 Current State

```
┌──────────────────────────────────────────────────────────────────────┐
│                         CURRENT ARCHITECTURE                          │
├──────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  [MoonBot] ──HTTP──> [momentum_trader] ──HTTP──> [AutoTrader]        │
│                                                      │                │
│                                                      v                │
│                                              [Eye of God V3]          │
│                                                      │                │
│                                                      v                │
│                                              [Order Executor]         │
│                                                      │                │
│                                                      v                │
│                                                  [Binance]            │
│                                                                       │
│  Latency: ~100-200ms signal→order                                    │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘
```

### 4.2 Target State

```
┌──────────────────────────────────────────────────────────────────────┐
│                         TARGET ARCHITECTURE                           │
├──────────────────────────────────────────────────────────────────────┤
│                                                                       │
│                    ┌─────────────────────┐                           │
│                    │     EVENT BUS       │                           │
│                    │   (asyncio.Queue)   │                           │
│                    └──────────┬──────────┘                           │
│                               │                                       │
│    ┌──────────────────────────┼──────────────────────────────┐       │
│    │                          │                               │       │
│    v                          v                               v       │
│ ┌──────────┐           ┌──────────┐                    ┌──────────┐  │
│ │ SCANNERS │ publish   │ DECISION │ publish            │ EXECUTOR │  │
│ │          │───────────│  ENGINE  │────────────────────│          │  │
│ │ momentum │ SIGNAL    │ (Eye V3) │ DECISION           │ orders   │  │
│ │ pump     │           │ + ML     │                    │ fills    │  │
│ │ moonbot  │           │          │                    │          │  │
│ └──────────┘           └──────────┘                    └──────────┘  │
│                                                               │       │
│                                                               v       │
│                    ┌─────────────────────────────────────────────┐   │
│                    │           FILE-BASED EVENT LOG              │   │
│                    │     (audit + replay + disaster recovery)    │   │
│                    └─────────────────────────────────────────────┘   │
│                                                                       │
│  Latency: ~5-10ms signal→order                                       │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘
```

---

## ЧАСТЬ 5: МЕТРИКИ УСПЕХА

| Метрика | Текущее | Цель | Критическое |
|---------|---------|------|-------------|
| Win Rate | 52% | 60%+ | < 45% = STOP |
| Profit Factor | N/A | 2.0+ | < 1.2 = Review |
| Signal→Order Latency | 100ms | 10ms | > 500ms = Alert |
| System Uptime | N/A | 99.5% | < 95% = Alert |
| DLQ Size | N/A | < 10 | > 100 = Alert |

---

## APPENDIX: FILES TO MODIFY

```
# Phase 1: Event Bus Integration
scripts/momentum_trader.py    - Add event publishing
scripts/autotrader.py         - Subscribe to events
scripts/eye_of_god_v3.py      - Emit DecisionEvent
scripts/order_executor.py     - Emit Order/FillEvent

# Phase 2: Health & Monitoring
core/circuit_breaker.py       - NEW: Per-service breakers
core/events/dlq_handler.py    - NEW: DLQ processing
core/metrics.py               - NEW: Prometheus metrics
scripts/hope_health_daemon.py - Add DLQ processing

# Phase 3: ML Enhancements
ai_gateway/modules/signal_scorer.py - NEW: ML scoring
core/position_sizer.py              - NEW: Kelly sizing
scripts/eye_of_god_v3.py            - Integrate ML scorer
```

---

**Document Version:** 1.0
**Author:** Claude (opus-4.5)
**Date:** 2026-02-02
**Status:** READY FOR REVIEW
