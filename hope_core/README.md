# HOPE Core v2.0

## 🎯 Что это?

**Единая защищённая оболочка для торговли**, где:
- ✅ Все команды проходят через **Command Bus** с валидацией
- ✅ Все состояния контролируются **State Machine**
- ✅ **Guardian** независимо мониторит и перезапускает
- ✅ **Event Journal** позволяет replay и audit

---

## 📁 Структура

```
hope_core/
├── hope_core.py           # Главный Core с Command Bus + State Machine
├── api_server.py          # HTTP API (FastAPI)
├── integration_bridge.py  # Bridge к существующим модулям
├── ARCHITECTURE.md        # Детальная архитектура
│
├── bus/
│   ├── command_bus.py     # Command Bus реализация
│   └── contracts.py       # JSON Schema контракты
│
├── state/
│   └── machine.py         # State Machine реализация
│
├── journal/
│   └── event_journal.py   # Event Journal с hash chain
│
├── guardian/
│   └── watchdog.py        # Guardian watchdog
│
└── deploy/
    ├── hope-core.service     # Systemd сервис
    ├── hope-guardian.service # Systemd сервис для Guardian
    ├── guardian.json         # Конфигурация Guardian
    └── deploy_to_vps.sh      # Скрипт деплоя
```

---

## 🚀 Быстрый старт

### 1. Локальное тестирование

```bash
cd hope_core

# Проверить синтаксис
python -m py_compile hope_core.py api_server.py

# Тест импортов
python -c "from hope_core import HopeCore; print('OK')"

# Запуск в DRY режиме
python api_server.py --mode DRY --port 8200
```

### 2. Деплой на VPS

```bash
# Из директории hope_core/deploy
chmod +x deploy_to_vps.sh
./deploy_to_vps.sh
```

### 3. Проверка

```bash
# Health check
curl http://127.0.0.1:8200/api/health | jq

# Status
curl http://127.0.0.1:8200/status | jq

# Логи
journalctl -u hope-core -f
```

---

## 🔧 API Endpoints

| Endpoint | Method | Описание |
|----------|--------|----------|
| `/` | GET | Информация о сервисе |
| `/status` | GET | Статус торговли |
| `/api/health` | GET | Health check (P0) |
| `/signal` | POST | Отправить сигнал |
| `/positions` | GET | Открытые позиции |
| `/positions/{id}/close` | POST | Закрыть позицию |
| `/emergency-stop` | POST | Экстренная остановка |
| `/circuit-breaker` | GET | Статус circuit breaker |
| `/circuit-breaker/reset` | POST | Сбросить circuit breaker |
| `/state` | GET | Статус State Machine |
| `/journal/recent` | GET | Последние события |
| `/guardian/heartbeat` | GET | Для Guardian |

---

## 🛡️ Безопасность

### Command Bus
- Каждая команда валидируется по JSON Schema
- Rate limiting предотвращает флуд
- Circuit breaker останавливает при ошибках

### State Machine
- Только валидные переходы разрешены
- INVALID → автоматический rollback
- Все переходы логируются

### Guardian
- Независимый процесс мониторинга
- Автоматический restart при падении
- Exponential backoff при повторных падениях
- Telegram алерты

### Event Journal
- Append-only log
- Hash chain для целостности
- Replay capability

---

## 📊 Мониторинг

### Systemd

```bash
# Статус
systemctl status hope-core
systemctl status hope-guardian

# Логи
journalctl -u hope-core -n 100
journalctl -u hope-guardian -n 50

# Перезапуск
systemctl restart hope-core
```

### Health Check

```bash
# Простой
curl -s http://127.0.0.1:8200/api/health | jq .status

# Полный
curl -s http://127.0.0.1:8200/api/health | jq
```

---

## ⚙️ Конфигурация

### hope_core_config.yaml

```yaml
core:
  mode: LIVE          # DRY, TESTNET, LIVE
  heartbeat_interval: 60s
  
command_bus:
  max_queue_size: 100
  command_timeout: 30s
  
state_machine:
  transition_timeout: 5s
  max_rollback_attempts: 3
  
trading:
  min_confidence: 0.35
  max_positions: 3
  position_size: $20
  daily_loss_limit: 5%
```

### guardian.json

```json
{
  "heartbeat_interval_sec": 10,
  "heartbeat_timeout_sec": 30,
  "max_restarts_per_hour": 5,
  "telegram_enabled": true
}
```

---

## 🔮 Secret Sauce (уникальные фичи)

1. **Idempotency Keys** - дубли ордеров невозможны
2. **Correlation ID** - один ID от сигнала до закрытия
3. **Event Sourcing** - replay любого дня
4. **Adaptive Rate Limiting** - при убытках замедляется
5. **Graceful Shutdown** - SIGTERM → закрыть позиции → exit
6. **Shadow Mode** - параллельное DRY тестирование

---

## 🆚 Отличие от старой архитектуры

| Аспект | Старая | HOPE Core v2.0 |
|--------|--------|----------------|
| Процессы | 17 отдельных | 2 (Core + Guardian) |
| Коммуникация | Event Bus (isolated) | Internal (single process) |
| Валидация | Нет | JSON Schema |
| State | Файлы | State Machine |
| Recovery | Manual | Auto (Guardian) |
| Audit | Логи | Event Journal |
| Latency | 200-500ms | <50ms |

---

## 📞 Команды для новой сессии Claude

```
Прочитай /home/claude/hope_core/README.md и ARCHITECTURE.md.

Затем проверь:
1. py_compile для всех файлов
2. Import test
3. Если VPS доступен - проверь health endpoint

Статус: В разработке / Готов к деплою / Deployed
```

---

*Created: 2026-02-04 by Claude (opus-4.5)*
