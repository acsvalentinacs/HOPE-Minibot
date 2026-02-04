# TRADING CYCLE AUDIT v1 - 30.01.2026

## EXECUTIVE SUMMARY

**Цель**: Полный торговый цикл без заглушек — от сигнала до реальной прибыли на Binance.

---

## 1. ЧТО ПРАВИЛЬНО (OK)

### 1.1 Архитектура Eye of God V3
```
Alpha Committee (хочу) + Risk Committee (разрешаю) = Decision
```
- Two-chamber decision making - корректно
- Fail-closed invariants (9 причин SKIP) - корректно
- SHA256 подпись решений - корректно

### 1.2 AutoTrader
- Circuit breaker с 3 триггерами (loss, trades, consecutive)
- Position sizing на основе confidence
- Trade logging в JSONL
- Price feed из AI Gateway

### 1.3 Инфраструктура
- Friend Bridge работает (01:15:01 uptime)
- Process Registry с централизованной конфигурацией
- Atomic writes для state файлов

---

## 2. КРИТИЧЕСКИЕ ОШИБКИ / РИСКИ

### 2.1 ❌ Eye of God V3 НЕ ИНТЕГРИРОВАН в AutoTrader!
```python
# autotrader.py использует СВОЙ SignalProcessor:
self.signal_processor = SignalProcessor(config)

# А eye_of_god_v3.py - отдельный модуль без интеграции!
# Нет import eye_of_god_v3 в autotrader.py
```
**РИСК**: Двухпалатная система Eye of God не используется. AutoTrader делает решения по простым правилам (buys_per_sec > 100 = PUMP_OVERRIDE).

### 2.2 ❌ Process Registry неверные аргументы
```python
# ТЕКУЩЕЕ (НЕВЕРНО):
"eye_of_god": ProcessConfig(
    args=["scripts/eye_of_god_v3.py", "--mode", "DRY"],  # --mode не существует!
)

# eye_of_god_v3.py поддерживает только:
parser.add_argument("--test", ...)
parser.add_argument("--stats", ...)
```

### 2.3 ❌ Отсутствует источник сигналов
AutoTrader ждёт сигналы через `add_signal()`, но:
- MoonBot парсер не запущен
- AI Gateway не генерирует сигналы
- Нет WebSocket подключения к бирже

### 2.4 ❌ Order Executor может быть не найден
```python
try:
    from order_executor import OrderExecutor
except ImportError:
    logger.warning("OrderExecutor not available, using DRY mode")
    self.executor = None  # Все ордера будут пропущены!
```

### 2.5 ❌ Telegram Bot Conflict
```
telegram.error.Conflict: terminated by other getUpdates request
```
Два экземпляра бота (Windows + Linux) используют polling одновременно.

---

## 3. ЧТО ТРЕБУЕТ УТОЧНЕНИЯ

1. **Источник сигналов**: MoonBot? WebSocket Binance? AI Gateway?
2. **Режим работы**: DRY (симуляция) / TESTNET / LIVE?
3. **Бюджет на позицию**: $10 default, $50 max - корректно?
4. **Стоп-лосс**: -0.5% default - достаточно ли агрессивно?

---

## 4. РЕШЕНИЯ TELEGRAM CONFLICT (6 вариантов)

### Вариант 1: SENDER-ONLY MODE (РЕКОМЕНДУЮ)
Локальный бот только ОТПРАВЛЯЕТ сообщения, не получает команды.
```python
# tg_bot_simple.py - добавить режим
class HopeBot:
    def __init__(self, sender_only: bool = False):
        self.sender_only = sender_only

    def build_app(self):
        if self.sender_only:
            # Не регистрируем handlers, не вызываем run_polling()
            return self._build_sender_only()
```
**Плюсы**: Минимальные изменения, Linux бот продолжает работать
**Минусы**: Команды только с сервера

### Вариант 2: WEBHOOK НА СЕРВЕРЕ
Переключить Linux бот на Webhook вместо Polling:
```python
# На сервере:
application.run_webhook(
    listen="0.0.0.0",
    port=8443,
    url_path=BOT_TOKEN,
    webhook_url=f"https://yourserver.com/{BOT_TOKEN}"
)
```
**Плюсы**: Polling освобождается для локального бота
**Минусы**: Нужен HTTPS сертификат, настройка сервера

### Вариант 3: ВТОРОЙ БОТ (Dev Token)
Создать второго бота @HopeDevBot для разработки:
```
@BotFather → /newbot → HopeDevBot
Новый токен в .env.local
```
**Плюсы**: Полная изоляция
**Минусы**: Два бота в Telegram, путаница

### Вариант 4: IPC BRIDGE
Локальный бот отправляет команды на Linux через Friend Bridge:
```
[Windows Бот] --HTTP--> [Friend Bridge] --IPC--> [Linux Бот]
```
**Плюсы**: Единая точка входа
**Минусы**: Сложнее реализация

### Вариант 5: ROLE-BASED SPLITTING
Linux: только получение команд
Windows: только отправка уведомлений (через Bot API напрямую)
```python
# Windows - прямой API вызов без polling:
import httpx
async def send_notification(chat_id, text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    await httpx.post(url, json={"chat_id": chat_id, "text": text})
```
**Плюсы**: Просто, работает
**Минусы**: Потеря части функциональности

### Вариант 6: TIME-SLICED POLLING (не рекомендую)
Боты чередуются: Linux днём, Windows ночью.
**Минусы**: Сложно синхронизировать, gaps в покрытии

---

## 5. ФИЧИ ДЛЯ PRODUCTION-УРОВНЯ (fail-closed)

### 5.1 Интеграция Eye of God V3 в AutoTrader
```python
# autotrader.py - заменить SignalProcessor на EyeOfGodV3
from eye_of_god_v3 import EyeOfGodV3

class AutoTrader:
    def __init__(self, config):
        self.eye = EyeOfGodV3(base_position_size=config.default_position_usdt)

    def _process_signals(self):
        for raw_signal in self.signal_queue:
            decision = self.eye.decide(raw_signal)  # Two-chamber decision
            if decision.action == "BUY":
                self._execute_trade(decision)
```

### 5.2 Real-time Signal Source (WebSocket)
```python
# binance_signal_feed.py
import websockets
import json

async def signal_stream(symbols: list, on_signal: callable):
    streams = "/".join([f"{s.lower()}@trade" for s in symbols])
    url = f"wss://stream.binance.com:9443/stream?streams={streams}"

    async with websockets.connect(url) as ws:
        async for msg in ws:
            data = json.loads(msg)
            if detect_pump(data):
                on_signal(data)
```

### 5.3 Health Check Loop
```python
async def health_loop():
    while True:
        checks = {
            "ai_gateway": await check_url("http://127.0.0.1:8100/health"),
            "friend_bridge": await check_url("http://127.0.0.1:8765/healthz"),
            "binance_api": await check_binance_ping(),
            "positions": len(open_positions) < MAX_POSITIONS,
        }
        if not all(checks.values()):
            CIRCUIT_BREAKER.trip(f"Health failed: {checks}")
        await asyncio.sleep(30)
```

### 5.4 Position Reconciliation
```python
async def reconcile_positions():
    """Сверка локального state с Binance"""
    local = load_positions_from_state()
    remote = await binance.get_open_positions()

    for symbol in local:
        if symbol not in remote:
            log.error(f"ORPHAN POSITION: {symbol} in local but not on Binance!")
            # FAIL-CLOSED: не открывать новые позиции
            CIRCUIT_BREAKER.trip("Position mismatch")
```

### 5.5 Profit Lock
```python
def should_take_profit(position: Position, current_price: float) -> bool:
    pnl_pct = (current_price - position.entry_price) / position.entry_price * 100

    # Trailing stop after +0.5%
    if pnl_pct >= 0.5:
        position.trailing_stop = max(position.trailing_stop, current_price * 0.997)

    # Lock profit at +1%
    if pnl_pct >= 1.0:
        return True  # Take profit

    return False
```

---

## 6. ПЛАН ДЕЙСТВИЙ (EXECUTION)

### Phase 1: Исправить немедленно
1. [x] Fix Telegram bot NameError (decorators)
2. [ ] Fix process_registry args for eye_of_god
3. [ ] Implement sender-only mode for local bot

### Phase 2: Интеграция (сегодня)
4. [ ] Интегрировать EyeOfGodV3 в AutoTrader
5. [ ] Добавить источник сигналов (AI Gateway WebSocket)
6. [ ] Проверить OrderExecutor import

### Phase 3: Production hardening
7. [ ] Health check loop
8. [ ] Position reconciliation
9. [ ] Proper logging с ротацией
10. [ ] Telegram alerts для критических событий

---

## 7. VERIFICATION COMMANDS

```powershell
# Check syntax
python -m py_compile scripts/autotrader.py
python -m py_compile scripts/eye_of_god_v3.py

# Test Eye of God
python scripts/eye_of_god_v3.py --test

# Check process status
python scripts/hope_process_manager.py status

# Test AI Gateway
curl http://127.0.0.1:8100/health
```

---

**Автор**: Claude (opus-4)
**Дата**: 2026-01-30 14:15:00 UTC
