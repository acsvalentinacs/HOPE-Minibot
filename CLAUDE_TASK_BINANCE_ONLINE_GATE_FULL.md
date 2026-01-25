<!-- AI SIGNATURE: Created by Claude (opus-4) at 2026-01-25 11:00:00 UTC -->

# BINANCE ONLINE GATE — Полное ТЗ (Production-level)

## Название задачи

**BINANCE ONLINE GATE (Windows/PS5.1) — реальный онлайн-тест с evidence pack, fail-closed, без утечки ключей.**

## Цель

Реализовать и доказать работу **реального** online-gate теста Binance в репо HOPEminiBOT на Windows (PowerShell 5.1), который:

- даёт воспроизводимый **PASS/FAIL**
- создаёт **evidence pack** (JSON + sha256)
- **не печатает** и **не сохраняет** значения секретов
- не зависит от сломанного collection тестов и внутренних импортов

---

## Контекст (фиксированные пути/среда)

| Параметр | Значение |
|----------|----------|
| Repo root | `C:\Users\kirillDev\Desktop\TradingBot\minibot` |
| Python exe | `C:\Users\kirillDev\Desktop\TradingBot\.venv\Scripts\python.exe` |
| Secrets file | `C:\secrets\hope\.env` (READ-ONLY!) |
| Shell | Windows PowerShell 5.1 |
| Политика | fail-closed, Truth Gate, без разрушительных команд |

---

## Абсолютные запреты (Security/Truth Gate)

1. **НИКОГДА** не печатать значения ключей/секретов ни в консоль, ни в файлы, ни в отчёт
2. Разрешено только: `Present=True/False`, `Length=<int>`
3. **ЗАПРЕЩЕНО** модифицировать `C:\secrets\hope\.env` и любые `.bak*`
4. Любое заявление "PASS" допустимо только при наличии артефактов:
   - вывод команд запуска
   - `report.json`
   - `report.json.sha256`
   - совпадение SHA256 через `Get-FileHash`
5. Запрещены destructive команды (`rm`, `del`, `Remove-Item`, `git clean`)
6. Любые изменения файлов — только как готовое решение, не "подкрутки вручную"

---

## Структура теста

### Файл: `tests\test_binance_online_gate.py`

**Зависимости:** ТОЛЬКО стандартная библиотека Python
- `urllib.request`, `urllib.parse`, `json`, `hmac`, `hashlib`
- `time`, `os`, `pathlib`, `ssl`, `typing`, `socket`, `re`, `random`

**ЗАПРЕЩЕНО:** `requests`, `aiohttp`, любые `core.*`, `nore.*`

### Test A — Public online smoke (обязателен)

- **Endpoint:** `GET https://api.binance.com/api/v3/time`
- **PASS если:**
  - HTTP 200
  - JSON содержит `serverTime` и это `int > 0`
- **Timeout:** 10 секунд
- **Retry:** 1 повтор при сетевых ошибках (DNS/timeout/502/503/504)

### Test B — Private online smoke (условный)

- **Выполнять только если** есть пара ключей:
  - `BINANCE_MAINNET_API_KEY` + `BINANCE_MAINNET_API_SECRET`, или
  - `BINANCE_API_KEY` + `BINANCE_API_SECRET`
- **Endpoint:** `GET https://api.binance.com/api/v3/account`
- **Подпись:** HMAC-SHA256, `timestamp` + `recvWindow=5000`
- **PASS если:**
  - HTTP 200
  - В JSON есть `accountType` **или** `balances`
- **SKIP:** если ключей нет (не FAIL!)

---

## Production-фичи (реализовано)

| Фича | Описание |
|------|----------|
| Timeout 10s | Явный таймаут на каждый запрос |
| Retry с backoff | 1 повтор при 502/503/504/timeout с паузой 0.3-0.7s |
| Error classification | DNS/TLS/TIMEOUT/CONNECTION/HTTP/JSON/RATE_LIMIT/AUTH |
| Rate-limit awareness | Отдельная категория для 418/429 |
| Sanitized errors | URL с signature редактируется как `[PARAMS_REDACTED]` |
| Effective host | Фиксация хоста после редиректов |
| Attempts tracking | Количество попыток в отчёте |
| pytest markers | `@pytest.mark.network`, `@pytest.mark.private` |
| Testnet mode | Переопределение через env `BINANCE_BASE_URL` |

---

## Evidence Pack

### Директория

```
state\audit\binance_online_gate\<YYYYMMDD_HHMMSS>\
├── report.json
└── report.json.sha256
```

### Схема report.json

```json
{
  "utc": "ISO_UTC_STRING",
  "python_exe": "PATH",
  "base_url": "https://api.binance.com",
  "public": {
    "url": "https://api.binance.com/api/v3/time",
    "ok": true,
    "status_code": 200,
    "latency_ms": 123,
    "serverTime_present": true,
    "timeout_s": 10,
    "attempts": 1,
    "effective_host": "api.binance.com",
    "error": null,
    "error_class": null
  },
  "private": {
    "attempted": true,
    "skipped_reason": null,
    "ok": true,
    "status_code": 200,
    "latency_ms": 456,
    "timeout_s": 10,
    "attempts": 1,
    "effective_host": "api.binance.com",
    "key_present": true,
    "secret_present": true,
    "key_length": 64,
    "secret_length": 64,
    "top_level_fields": ["accountType", "balances", "..."],
    "error": null,
    "error_class": null
  },
  "verdict": "PASS"
}
```

---

## Команды запуска

### 1. Проверка Python

```powershell
cd "C:\Users\kirillDev\Desktop\TradingBot\minibot"
& "C:\Users\kirillDev\Desktop\TradingBot\.venv\Scripts\python.exe" -V
```

### 2. Загрузка env (опционально)

```powershell
powershell -ExecutionPolicy Bypass -File ".\tools\load_binance_env.ps1"
```

### 3. Запуск теста

```powershell
cd "C:\Users\kirillDev\Desktop\TradingBot\minibot"
& "C:\Users\kirillDev\Desktop\TradingBot\.venv\Scripts\python.exe" -m pytest -q .\tests\test_binance_online_gate.py -vv
```

### 4. Запуск только public теста (без private)

```powershell
& "..\.venv\Scripts\python.exe" -m pytest .\tests\test_binance_online_gate.py -v -m "not private"
```

### 5. Gate-only runner (всё в одном)

```powershell
powershell -ExecutionPolicy Bypass -File ".\tools\run_binance_gate.ps1"
```

### 6. Верификация evidence

```powershell
$last = Get-ChildItem .\state\audit\binance_online_gate -Directory | Sort-Object Name -Descending | Select-Object -First 1
Get-FileHash (Join-Path $last.FullName "report.json") -Algorithm SHA256
Get-Content (Join-Path $last.FullName "report.json.sha256")
```

---

## Acceptance Criteria

| Критерий | Описание |
|----------|----------|
| pytest exit 0 | `pytest test_binance_online_gate.py` возвращает 0 при рабочем интернете |
| Evidence created | `report.json` и `report.json.sha256` в timestamped папке |
| SHA256 match | `Get-FileHash report.json` совпадает с `.sha256` |
| No secrets leaked | В консоль/файлы не попали значения ключей |
| Verdict in report | `verdict: "PASS"` или `verdict: "FAIL"` |

---

## Failure Criteria

| Условие | Результат |
|---------|-----------|
| Public endpoint fail | FAIL |
| Private attempted + fail | FAIL |
| No report.json | FAIL |
| SHA256 mismatch | FAIL |
| Signature leaked | FAIL (security violation) |

---

## Файлы проекта

| Файл | Назначение |
|------|------------|
| `tests/test_binance_online_gate.py` | Основной pytest тест |
| `tools/load_binance_env.ps1` | Загрузка BINANCE_* в Process env |
| `tools/run_binance_gate.ps1` | Gate-only runner (всё в одном) |
| `CLAUDE_TASK_BINANCE_ONLINE_GATE_FULL.md` | Это ТЗ |
| `CLAUDE_TASK_BINANCE_ONLINE_GATE_10L.txt` | Краткий чек-лист |
