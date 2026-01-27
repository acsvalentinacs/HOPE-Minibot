# Async Workers в Textual - Паттерны HOPE OMNI-CHAT

## Проблема SignalError

```
SignalError: Node must be running to subscribe to a signal (has DDOScreen() been mounted)?
```

**Причина:** `on_mount()` вызывается до полной инициализации Screen в Textual.

**Решение:** Использовать `call_after_refresh()` для отложенной инициализации:

```python
def on_mount(self) -> None:
    """Defer initialization until screen is fully ready."""
    self.call_after_refresh(self._initialize_screen)

def _initialize_screen(self) -> None:
    """Initialize screen after it's fully mounted."""
    self.query_one("#input", TextArea).focus()
```

## Проблема call_from_thread

```
AttributeError: 'MarketIntelScreen' object has no attribute 'call_from_thread'
```

**Причина:** `call_from_thread()` существует только для thread-workers.

### Типы workers в Textual

| Тип | Создание | UI обновления |
|-----|----------|---------------|
| Async Worker | `run_worker(coro)` | Напрямую |
| Thread Worker | `run_worker(coro, thread=True)` | `call_from_thread()` |

### Async Worker (по умолчанию)

```python
def start_work(self) -> None:
    self.run_worker(self._do_work(), name="worker")

async def _do_work(self) -> None:
    # Напрямую обновляем UI - мы в том же event loop
    self._update_status("Working...")
    result = await some_async_operation()
    self._display_result(result)
```

### Thread Worker (для blocking I/O)

```python
def start_work(self) -> None:
    self.run_worker(self._do_blocking_work(), name="worker", thread=True)

async def _do_blocking_work(self) -> None:
    # Используем call_from_thread - мы в отдельном потоке
    self.call_from_thread(self._update_status, "Working...")
    result = await blocking_operation()
    self.call_from_thread(self._display_result, result)
```

## Применение в HOPE OMNI-CHAT

### MarketIntelScreen

```python
async def _load_data(self) -> None:
    """Load market data (async worker - direct UI updates)."""
    self._loading = True
    self._update_status("⏳ Загрузка данных с Binance...")  # Напрямую

    try:
        self._snapshot = await self._intel.get_snapshot(max_age_seconds=60)
        self._display_data()  # Напрямую
    except Exception as e:
        self._update_status(f"❌ Ошибка: {e}")

    self._loading = False
```

### DDOScreen

```python
def _process_event(self, event: DDOEvent) -> None:
    """Process DDO event (async worker - direct calls)."""
    if isinstance(event, PhaseStartEvent):
        self._set_phase(f"📍 {event.phase.display_name}")  # Напрямую
        self._set_status("🟡 ДУМАЕТ")  # Напрямую

def _add_log(self, text: str) -> None:
    """Add text to log (async worker - direct update)."""
    self._log_text += text
    self._update_log_display()  # Напрямую
```

## Обработка завершения Worker

```python
def on_worker_state_changed(self, event) -> None:
    """Handle worker state changes (Textual standard method)."""
    if event.worker.name == "my_worker":
        if event.worker.state.name in ("SUCCESS", "ERROR", "CANCELLED"):
            self._finish_work()
```

## Резюме

| Ситуация | Решение |
|----------|---------|
| SignalError в on_mount | `call_after_refresh()` |
| UI update в async worker | Напрямую вызывать методы |
| UI update в thread worker | `call_from_thread()` |
| Завершение worker | `on_worker_state_changed()` |

---

*Документация Async Workers v1.0 - HOPE OMNI-CHAT*
