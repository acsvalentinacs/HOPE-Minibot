# ТЗ (внутреннее) на разработку бота NORE
Путь проекта: `C:\Users\kirillDev\Desktop\TradingBot\minibot`

Документ предназначен как инженерная спецификация для реализации бота **NORE** в рамках HOPEminiBOT.
Язык документа: RU. Имена полей/контрактов/ключей конфигов: EN. Код и комментарии в коде: EN.

---

## 0) Принципы и ограничения (не обсуждаются)
### 0.1 Fail-closed
Любая неоднозначность, неполная проверка, отсутствие доказательств корректности — **STOP** с записью причины в журнал (event `stop_journal`).

### 0.2 Детерминизм торгового ядра
Торговое ядро (принятие решения на исполнение) — детерминированно.
AI может:
- предлагать гипотезы/оценки/параметры,
- формировать рекомендацию,
но **не** может напрямую инициировать ордера без прохождения валидаций и риск-интерлоков.

### 0.3 Защита файлов от порчи
- критичные файлы: atomic write (temp → flush → fsync → replace)
- конкурентная запись: lock обязателен
- JSONL: append-only
- для любой записи важного артефакта: sha256-контракт

### 0.4 SSoT командной строки
`cmdline_hash` рассчитывается как `sha256(GetCommandLineW())` (hex lowercase) и включается в события/журналы.

### 0.5 Секреты
`.env` и секреты не хранятся в репозитории; используется внешний путь (например `C:\secrets\...`).
Логи и дампы — без утечки ключей.

---

## 1) Цель NORE
NORE — автономный режим “ночной надёжности” и “виртуальной торговли” (paper trading) на Binance, который:
1) поддерживает живую проверку доступности Binance (ping/health),
2) прогоняет всю цепочку систем бота (market ingest → signal → risk → execution sandbox → journaling),
3) прогоняет AI-подсистему (строго изолированно),
4) ведёт непрерывное обучение/обновление “обучалки” (без влияния на торговое ядро),
5) работает непрерывно **7 часов** с метриками, ротацией логов и жёсткими STOP-условиями.

---

## 2) Область работ (Scope)
### 2.1 В режиме NORE должны работать:
- Market data ingestion (Binance)
- Health checks (Binance reachability, latency)
- Paper trading execution (virtual fills)
- Risk layer (лимиты, интерлоки, аварийные остановки)
- Event journaling (JSONL + sha256)
- Watchdog/роль-менеджмент (если применимо в текущем проекте)
- AI pipeline: inference + pattern collection + continuous trainer (изолированно)

### 2.2 Вне области работ (Non-scope)
- Реальная торговля (LIVE) в рамках NORE-ночного теста запрещена
- Изменения существующих “боевых” файлов без staging/deploy протокола
- Веб-сканирование новостей (если нет локальных источников/фидов)

---

## 3) Термины
- **Event**: JSON объект в JSONL-очереди с envelope+payload.
- **Envelope**: строгая обвязка события (additionalProperties=false).
- **Payload**: доменная часть события (additionalProperties=true + min required).
- **Queue**: файловая очередь `minibot/var/queues/<queue_name>/...`.
- **NORE Run**: один 7-часовой прогон с `run_id`.

---

## 4) Архитектура (логическая)
### 4.1 Процессы/роли (рекомендуемо)
1) `market_role`:
   - читает Binance, пишет `market_tick`
2) `strategy_role` (NORE strategy):
   - потребляет `market_tick`, пишет `signal`
3) `risk_role`:
   - потребляет `signal`, пишет `risk_action` и/или “пропускает” сигнал дальше
4) `execution_role` (paper executor):
   - потребляет `order_intent`, пишет `order_result` + `fill`
5) `ai_role` (изолированный):
   - потребляет события/фичи, пишет рекомендации/фичи/обновления моделей в отдельный артефакт-каталог
6) `watchdog_role`:
   - контролирует живучесть и инициирует STOP при нарушениях

> Допускается вариант, где часть ролей объединены в один процесс, но изоляция AI остаётся обязательной.

### 4.2 IPC через очереди (файловые)
Базовые очереди (пример):
- `market_ticks`
- `signals`
- `risk_actions`
- `order_intents`
- `order_results`
- `fills`
- `health`
- `stop_journal`

Каждая очередь: `inbox.jsonl`, `acks.jsonl`, `deadletter.jsonl`, `cursor.json`.

---

## 5) Контракты данных
### 5.1 Формат JSONL строки
`sha256:<hash>:<json>`
- `<hash>` = sha256(JSON bytes UTF-8), hex lowercase
- `<json>` = объект события

### 5.2 Envelope (обязательные поля)
- `schema` (string)
- `v` (int)
- `ts` (int, unix ms)
- `ts_iso` (string, optional)
- `id` (uuidv4)
- `source` (string)
- `type` (string)
- `run_id` (string `YYYYMMDD_HHMMSS`)
- `cmdline_hash` (sha256(GetCommandLineW()) hex lowercase)
- `payload` (object)

### 5.3 Минимальные payload-поля по типам
- `market_tick`: `symbol`, `price`
- `signal`: `symbol`, `side`, `score`, `confidence`, `horizon_ms`
- `risk_action`: `action`, `reason_code`
- `order_intent`: `symbol`, `side`, `qty`, `order_type`
- `order_result`: `client_order_id`, `result`
- `fill`: `order_id`, `symbol`, `side`, `qty`, `price`
- `health`: `role`, `state`, `uptime_s`
- `stop_journal`: `component`, `reason_code`, `detail`

---

## 6) NORE: алгоритм поведения (функциональные требования)
### 6.1 Binance connectivity
NORE обязан каждые N секунд (configurable):
- измерять latency (HTTP ping / exchange info / time endpoint),
- фиксировать результат в `health` event,
- при деградации (таймауты/ошибки) действовать fail-closed (см. STOP-условия).

### 6.2 Paper trading (виртуальное исполнение)
Paper executor обязан:
- принимать `order_intent`
- валидировать базовые поля
- симулировать `order_result` и `fill` (в т.ч. комиссии опционально)
- сохранять “виртуальный портфель” в отдельном хранилище (atomic write) или через события.

Запрещено:
- отправлять реальные ордера в Binance в NORE-режиме

### 6.3 Strategy (NORE strategy)
Требования к стратегии:
- работает на `market_tick` + (опционально) агрегированных барах
- выдаёт `signal` с полями `score/confidence/horizon_ms`
- обязана быть детерминированной на одинаковом входе
- обязана протоколировать причины (в payload поле `reason`)

Рекомендуемый минимальный набор:
- простая импульсная/mean-reversion логика на RSI/моментуме (если в проекте уже есть индикаторы — переиспользовать)
- фильтры ликвидности/спрэда (если доступны)

### 6.4 Risk layer
Risk layer обязан:
- применять дневные/ночные лимиты
- применять лимиты на max drawdown виртуального equity
- применять лимиты на частоту сделок
- при нарушении → `risk_action.action = STOP` и запись `stop_journal`

### 6.5 AI subsystem (изолированная)
AI subsystem обязан:
- работать в отдельном процессе/роли
- иметь отдельный каталог артефактов (`minibot/var/ai/...` или иной, но под atomic write)
- не менять торговое ядро напрямую
- выдавать только “recommendation events” или файлы “feature pack” (строго версионированные и с sha256)

Continuous trainer (обучалка):
- собирает датасет из событий (только из разрешённых очередей)
- пишет “training snapshots” (sha256 + manifest)
- обязателен режим безопасного отката: новый снапшот не активен, пока не пройдёт локальный тест-гейт

---

## 7) Ночной тест NORE (7 часов) — обязательный сценарий
### 7.1 Длительность
- 7 часов непрерывной работы (один run)

### 7.2 Наблюдаемость (метрики и журналы)
Обязательные метрики (минимум):
- uptime по ролям
- latency до Binance (p50/p95)
- количество market ticks
- количество сигналов
- количество order intents / results / fills
- виртуальный PnL, max drawdown, win rate, R:R (если применимо)
- доля и причины STOP

Все метрики должны быть записаны:
- либо в JSONL events,
- либо в отдельный metrics.json (atomic write) + периодические снапшоты.

### 7.3 STOP-условия (fail-closed)
NORE должен останавливать запуск при:
- невозможности валидировать события по схемам
- нарушении sha256 JSONL формата
- обнаружении неатомарной записи критичных файлов
- потере соединения с Binance дольше порога (например 2-5 минут) или частых ошибок
- превышении лимитов риска
- обнаружении рассинхронизации очередей/курсора
- несоответствии `cmdline_hash` формату или отсутствию

Каждый STOP:
- пишет `stop_journal` (component, reason_code, detail)
- создаёт STOP.flag (если в проекте принято)
- прекращает “исполнение” (paper executor) немедленно

---

## 8) Конфигурация
### 8.1 Файл конфигурации NORE
Требуется отдельный конфиг: `minibot/config/nore.v1.json` (или аналог), поля:
- `mode`: "NORE"
- `duration_ms`: 7h
- `binance`: endpoints/timeouts
- `queues`: base dir + names
- `risk`: лимиты
- `strategy`: параметры
- `ai`: on/off + paths + limits
- `logging`: ротация

### 8.2 Реестр схем
Использовать `minibot/config/contracts.v1.json` (или иной существующий контрактный конфиг) как SSoT для mapping type→schema path.

---

## 9) Тестирование и гейты приёмки
### 9.1 Минимальные гейты (обязательны)
- `runtime_smoke`: запускается NORE в “коротком” режиме (например 2-5 минут) и проверяет:
  - создание очередей
  - запись корректного JSONL (sha256 валиден)
  - генерация хотя бы одного market_tick и одного health
  - отсутствие реальных ордеров
- `schema_gate`: все события валидируются против JSON Schema
- `ai_isolation_gate`: AI не имеет доступа к ключам/экзекьюшену; любые “order_*” из AI запрещены
- `atomic_io_gate`: проверка, что cursor/config/metrics пишутся атомарно

### 9.2 Приёмка 7-часового прогона
PASS если:
- все роли живы 7 часов или корректно восстанавливаются watchdog’ом
- latency и ошибки Binance в пределах порогов
- нет нарушений контрактов/sha256
- PnL-метрики не являются критерием PASS, но должны быть корректно рассчитаны и логированы
- доля STOP = 0, либо STOP только по заранее определённым “инъекциям ошибок” (если тестирует отказоустойчивость)

---

## 10) План поставки (deployment discipline)
Изменения в проект вносятся только через staged deployment:
- `staging/pending/files/...`
- `staging/pending/manifest.json` (sha256, size, created_ts, cmdline_hash)
- запрет перезаписи существующих файлов на первом шаге интеграции NORE (если это условие действует)

---

## 11) Deliverables (артефакты реализации)
### 11.1 Код
- `minibot/nore/` или `minibot/strategies/nore_strategy_v1.py`
- `minibot/execution/paper_executor_nore_v1.py` (или интеграция в sandbox executor)
- `minibot/ai/nore_ai_worker_v1.py` (изолировано)
- `minibot/tools/nore_run_v1.py` (CLI/entrypoint)
- обновления watchdog/roles_registry (если нужно)

### 11.2 Контракты/схемы/документация
- JSON Schemas по типам событий (если ещё не добавлены)
- `minibot/contracts/NORE_TZ_RU.md` (этот файл)
- `minibot/config/nore.v1.json`
- README по запуску ночного теста

---

## 12) Команда запуска (целевая)
Пример (финальный вид может отличаться):
`python -m minibot.nore_run_v1 --config minibot/config/nore.v1.json`

---

## 13) Критерии “готово к следующему шагу”
- Все гейты PASS
- 7-часовой прогон PASS
- Логи/метрики/журналы полностью воспроизводимы
- Никаких прямых интеграций AI→execution
- Файловая целостность подтверждена (atomic + sha256)
