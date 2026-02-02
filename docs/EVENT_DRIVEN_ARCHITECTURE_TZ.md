# EVENT-DRIVEN MICROSERVICES ARCHITECTURE - TECHNICAL SPECIFICATION
# HOPE AI Trading System Integration

<!-- AI SIGNATURE: Created by Claude (opus-4.5) at 2026-02-02 15:20:00 UTC -->

---

## EXECUTIVE SUMMARY

```
PROJECT:     Event-Driven Architecture Integration for HOPE
OBJECTIVE:   Transform file-based IPC to message-broker system
APPROACH:    Hybrid - preserve file-based for audit, add event bus for speed
TIMELINE:    2-3 weeks for MVP, 6 weeks for production
RISK LEVEL:  MEDIUM (trading system, requires careful migration)
```

---

## ЧАСТЬ 1: АНАЛИЗ ПРЕДЛАГАЕМОЙ АРХИТЕКТУРЫ

### 1.1 Что предлагают (Event-Driven Microservices)

```
КЛАССИЧЕСКАЯ АРХИТЕКТУРА:
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│  [OrderService]──┬──>[Message Broker]──┬──>[InventoryService]           │
│                  │     (Kafka/RabbitMQ) │                                │
│  [PaymentService]┴──────────────────────┴──>[NotificationService]       │
│                                                                          │
│  Events: OrderCreated → InventoryReserved → PaymentProcessed → Shipped  │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘

ПРЕИМУЩЕСТВА:
+ Слабая связанность (сервисы не знают друг о друге)
+ Масштабируемость (каждый сервис скейлится отдельно)
+ Отказоустойчивость (события копятся в очереди при падении)
+ Audit trail из коробки (Event Log = журнал всех действий)
+ Replay capability (можно переиграть события)
```

### 1.2 Что старое в этом подходе?

| Концепция | Возраст | Статус | Проблемы |
|-----------|---------|--------|----------|
| Message Queue (MQ) | 1980s | Зрелая | Оверхед для малых систем |
| Pub/Sub pattern | 1987 | Зрелая | Сложность отладки |
| Kafka | 2011 | Современная | Требует инфраструктуры |
| Event Sourcing | 2005 | Зрелая | Complexity в реконструкции |

**ВЕРДИКТ:** Архитектура проверенная, но требует правильной адаптации под торговый контекст.

### 1.3 Где HOPE уже ОБОГНАЛА классику?

```
╔═══════════════════════════════════════════════════════════════════╗
║        HOPE INNOVATIONS vs CLASSIC EVENT-DRIVEN                   ║
╠═══════════════════════════════════════════════════════════════════╣
║                                                                    ║
║  ✅ FILE-BASED EVENT LOG (decisions.jsonl)                        ║
║     → Классика: Kafka Log                                         ║
║     → HOPE: JSONL файлы с sha256 подписями                       ║
║     → Преимущество: Читаемость + Git-совместимость               ║
║                                                                    ║
║  ✅ TWO-CHAMBER DECISION SYSTEM                                   ║
║     → Классика: Single service decision                           ║
║     → HOPE: Alpha Committee + Risk Committee                      ║
║     → Преимущество: Separation of concerns в одном процессе       ║
║                                                                    ║
║  ✅ FAIL-CLOSED BY DEFAULT                                        ║
║     → Классика: Retry with backoff                                ║
║     → HOPE: Immediate SKIP on any doubt                           ║
║     → Преимущество: Капитал защищён, no silent failures          ║
║                                                                    ║
║  ✅ CORRELATION ID (signal_id → decision_id → order_id)          ║
║     → Классика: Distributed tracing (Jaeger, Zipkin)              ║
║     → HOPE: sha256 chain в JSONL                                  ║
║     → Преимущество: Простота + полная трассировка                ║
║                                                                    ║
║  ✅ ATOMIC WRITES                                                  ║
║     → Классика: Database transactions                             ║
║     → HOPE: temp → fsync → rename pattern                         ║
║     → Преимущество: Нет внешних зависимостей                     ║
║                                                                    ║
╚═══════════════════════════════════════════════════════════════════╝
```

### 1.4 Где HOPE ОТСТАЁТ (срочно внедрять!)

```
╔═══════════════════════════════════════════════════════════════════╗
║        GAPS TO CLOSE (Priority Order)                              ║
╠═══════════════════════════════════════════════════════════════════╣
║                                                                    ║
║  ❌ P0: REAL-TIME EVENT BUS                                       ║
║     Текущее: HTTP polling / file watching                         ║
║     Нужно: In-memory event queue (asyncio.Queue)                  ║
║     Зачем: Latency 1ms вместо 100ms+                             ║
║                                                                    ║
║  ❌ P1: SERVICE DISCOVERY                                         ║
║     Текущее: Hardcoded ports (8100, 8200)                        ║
║     Нужно: Registry (consul-lite или файловый)                   ║
║     Зачем: Динамическое масштабирование                          ║
║                                                                    ║
║  ❌ P1: DEAD LETTER QUEUE                                         ║
║     Текущее: Потерянные события при падении                       ║
║     Нужно: DLQ файл + retry daemon                                ║
║     Зачем: Гарантия доставки                                      ║
║                                                                    ║
║  ❌ P2: EVENT VERSIONING                                          ║
║     Текущее: Implicit schema                                      ║
║     Нужно: schema_version в каждом event                         ║
║     Зачем: Backwards compatibility                                ║
║                                                                    ║
║  ❌ P2: CIRCUIT BREAKER PER SERVICE                               ║
║     Текущее: Global circuit breaker в AutoTrader                  ║
║     Нужно: Per-component circuit breakers                         ║
║     Зачем: Изоляция отказов                                       ║
║                                                                    ║
╚═══════════════════════════════════════════════════════════════════╝
```

---

## ЧАСТЬ 2: ЦЕЛЕВАЯ АРХИТЕКТУРА HOPE EVENT-DRIVEN

### 2.1 Hybrid Architecture (рекомендация)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    HOPE EVENT-DRIVEN ARCHITECTURE v2                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│                        ┌────────────────────┐                            │
│                        │   EVENT BUS        │                            │
│                        │  (asyncio.Queue)   │                            │
│                        └─────────┬──────────┘                            │
│                                  │                                       │
│    ┌─────────────────────────────┼─────────────────────────────────┐    │
│    │                             │                                  │    │
│    ▼                             ▼                                  ▼    │
│ ┌──────────┐              ┌──────────┐                       ┌──────────┐│
│ │ SCANNER  │  publish     │DECISION  │  publish              │ EXECUTOR ││
│ │ SERVICE  │──────────────│ ENGINE   │──────────────────────│ SERVICE  ││
│ │          │ SignalEvent  │(Eye of   │ DecisionEvent        │          ││
│ │ - pump   │              │ God V3)  │                       │ - orders ││
│ │ - trend  │              │          │                       │ - manage ││
│ │ - moonbot│              │ Alpha +  │                       │          ││
│ └────┬─────┘              │ Risk     │                       └────┬─────┘│
│      │                    └──────────┘                            │      │
│      │                                                            │      │
│      ▼                                                            ▼      │
│ ┌──────────────────────────────────────────────────────────────────────┐│
│ │                     FILE-BASED EVENT LOG (AUDIT)                      ││
│ │  state/events/signals.jsonl | decisions.jsonl | orders.jsonl          ││
│ │  + sha256 signatures + correlation_id chain                           ││
│ └──────────────────────────────────────────────────────────────────────┘│
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘

HYBRID APPROACH:
├─ In-memory Queue: для скорости (ms latency)
├─ File-based Log: для аудита и восстановления
└─ HTTP API: для внешней интеграции (Telegram, MoonBot)
```

### 2.2 Event Schema (Canon)

```python
# core/events/event_schema.py

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import hashlib
import json

SCHEMA_VERSION = "2.0"

@dataclass
class HopeEvent:
    """Base event for HOPE Event-Driven system."""
    event_type: str              # SIGNAL, DECISION, ORDER, FILL, CLOSE
    event_id: str                # sha256:xxxx (unique)
    correlation_id: str          # Links signal → decision → order
    timestamp: str               # ISO 8601 UTC
    schema_version: str = SCHEMA_VERSION
    source: str = ""             # Which service produced this
    payload: Dict[str, Any] = field(default_factory=dict)

    def compute_id(self) -> str:
        """Compute sha256 event_id."""
        data = f"{self.event_type}:{self.correlation_id}:{self.timestamp}:{json.dumps(self.payload, sort_keys=True)}"
        return "sha256:" + hashlib.sha256(data.encode()).hexdigest()[:16]


@dataclass
class SignalEvent(HopeEvent):
    """Signal detected by scanner."""
    event_type: str = "SIGNAL"
    source: str = "scanner"
    # payload: {symbol, strategy, price, buys_per_sec, delta_pct, ...}


@dataclass
class DecisionEvent(HopeEvent):
    """Decision from Eye of God."""
    event_type: str = "DECISION"
    source: str = "eye_of_god"
    # payload: {action, confidence, reasons, position_size, targets, ...}


@dataclass
class OrderEvent(HopeEvent):
    """Order sent to exchange."""
    event_type: str = "ORDER"
    source: str = "executor"
    # payload: {order_id, symbol, side, quantity, status, ...}


@dataclass
class FillEvent(HopeEvent):
    """Order filled on exchange."""
    event_type: str = "FILL"
    source: str = "executor"
    # payload: {order_id, filled_qty, avg_price, commission, ...}
```

### 2.3 Event Bus Implementation

```python
# core/events/event_bus.py

import asyncio
from typing import Dict, List, Callable, Any
from collections import defaultdict
import logging

log = logging.getLogger("EVENT_BUS")


class HopeEventBus:
    """
    In-memory event bus for HOPE.

    - Pub/Sub pattern
    - Async handlers
    - Dead Letter Queue for failures
    """

    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._dlq: asyncio.Queue = asyncio.Queue(maxsize=100)  # Dead Letter Queue
        self._running = False

    def subscribe(self, event_type: str, handler: Callable):
        """Subscribe handler to event type."""
        self._subscribers[event_type].append(handler)
        log.info(f"Subscribed {handler.__name__} to {event_type}")

    async def publish(self, event: 'HopeEvent'):
        """Publish event to bus."""
        await self._queue.put(event)
        log.debug(f"Published {event.event_type}: {event.event_id}")

    async def _dispatch(self, event: 'HopeEvent'):
        """Dispatch event to all subscribers."""
        handlers = self._subscribers.get(event.event_type, [])
        handlers.extend(self._subscribers.get("*", []))  # Wildcard subscribers

        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except Exception as e:
                log.error(f"Handler {handler.__name__} failed: {e}")
                await self._dlq.put((event, str(e)))

    async def run(self):
        """Run event loop."""
        self._running = True
        log.info("Event Bus started")

        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._dispatch(event)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                log.error(f"Event Bus error: {e}")

    def stop(self):
        """Stop event bus."""
        self._running = False


# Singleton instance
_event_bus: Optional[HopeEventBus] = None

def get_event_bus() -> HopeEventBus:
    global _event_bus
    if _event_bus is None:
        _event_bus = HopeEventBus()
    return _event_bus
```

---

## ЧАСТЬ 3: MIGRATION PLAN

### 3.1 Phases

```
PHASE 1 (Week 1-2): Foundation
├─ Implement event_schema.py
├─ Implement event_bus.py
├─ Add EventLogger (dual write: bus + file)
└─ Unit tests

PHASE 2 (Week 3-4): Integration
├─ Refactor Scanner → publish SignalEvent
├─ Refactor Eye of God → subscribe to SIGNAL, publish DECISION
├─ Refactor Executor → subscribe to DECISION, publish ORDER/FILL
└─ Integration tests

PHASE 3 (Week 5-6): Production
├─ Add Dead Letter Queue handler
├─ Add Service Health Monitor
├─ Add Metrics/Observability
├─ Canary deployment (parallel with old system)
└─ Full cutover
```

### 3.2 Backwards Compatibility

```python
# core/events/event_logger.py

class HybridEventLogger:
    """
    Dual-write logger: Event Bus + File.
    Ensures backwards compatibility during migration.
    """

    def __init__(self, bus: HopeEventBus, log_dir: Path):
        self.bus = bus
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)

    async def log_event(self, event: HopeEvent):
        """Log to both bus and file."""
        # 1. Publish to bus (fast path)
        await self.bus.publish(event)

        # 2. Write to file (audit trail)
        log_file = self.log_dir / f"{event.event_type.lower()}s.jsonl"
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(asdict(event), ensure_ascii=False) + '\n')
```

---

## ЧАСТЬ 4: КАК МОНЕТИЗИРОВАТЬ

### 4.1 Для HOPE (Internal Value)

| Benefit | Impact |
|---------|--------|
| Lower latency | Faster fills, better prices |
| Better observability | Faster debugging |
| Horizontal scaling | More symbols, more strategies |
| Replay capability | Testing strategies on historical events |

### 4.2 Коммерческие возможности

```
1. SaaS-платформа для алготрейдеров
   - Event-driven trading engine as a service
   - Подписка $99-999/мес по объёму

2. White-label решение
   - Лицензировать ядро HOPE другим командам
   - Они ставят свой бренд, платят роялти

3. Консалтинг
   - Помогать другим мигрировать на event-driven
   - $150-300/час

4. Transaction fee модель
   - % с каждого успешного трейда
   - Модель как у Stripe/Shopify
```

---

## ЧАСТЬ 5: IMMEDIATE ACTION ITEMS (P0)

### 5.1 Создать немедленно

```bash
# 1. Event Schema
touch core/events/__init__.py
touch core/events/event_schema.py
touch core/events/event_bus.py
touch core/events/event_logger.py

# 2. Миграция SignalEvent
# В momentum_trader.py после генерации сигнала:
# await event_bus.publish(SignalEvent(...))

# 3. Dead Letter Queue
touch state/events/dlq.jsonl
```

### 5.2 Тесты для верификации

```python
# tests/test_event_bus.py

import pytest
import asyncio
from core.events.event_bus import HopeEventBus, get_event_bus
from core.events.event_schema import SignalEvent

@pytest.mark.asyncio
async def test_pub_sub():
    """Test basic pub/sub."""
    bus = HopeEventBus()
    received = []

    def handler(event):
        received.append(event)

    bus.subscribe("SIGNAL", handler)

    event = SignalEvent(
        event_id="sha256:test123",
        correlation_id="corr_1",
        timestamp="2026-02-02T15:00:00Z",
        payload={"symbol": "BTCUSDT"}
    )

    await bus.publish(event)
    await asyncio.sleep(0.1)

    assert len(received) == 1
    assert received[0].payload["symbol"] == "BTCUSDT"
```

---

## APPENDIX A: GLOSSARY

| Term | Definition |
|------|------------|
| Event Bus | In-memory message queue for async communication |
| DLQ | Dead Letter Queue - storage for failed events |
| Correlation ID | ID linking related events across services |
| Event Sourcing | Storing state as sequence of events |
| CQRS | Command Query Responsibility Segregation |

---

## APPENDIX B: REFERENCES

- Martin Fowler: Event-Driven Architecture
- Apache Kafka Documentation
- HOPE MASTER_RULES.md (internal)
- HOPE SESSION_RESTORE.md (internal)

---

**Document Version:** 1.0
**Author:** Claude (opus-4.5)
**Date:** 2026-02-02
**Status:** APPROVED FOR IMPLEMENTATION
