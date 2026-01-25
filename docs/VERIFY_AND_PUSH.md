<!-- AI SIGNATURE: Created by Claude (opus-4) at 2026-01-25T18:00:00Z -->
<!-- AI SIGNATURE: Modified by Claude (opus-4) at 2026-01-25T20:00:00Z -->

# Release Verification Protocol v2.0

## Принцип

**Нет "опционально". Каждый шаг обязателен.**

Любое отклонение от протокола = FAIL.

## Порядок верификации (фиксированный, 9 гейтов)

```
1. commit_gate       → Manifest + policy validation
2. dirty_tree_guard  → No untracked/modified files
3. verify_tree       → Deterministic tree manifest (sha256)
4. network_guard     → No direct network outside core/net/**
5. secrets_guard     → No hardcoded secrets
6. live_smoke_gate   → Trading smoke test (DRY)
7. evidence_guard    → Schema validation for health files
8. testnet_gate      → Read-only API verification
9. git push --dry-run → Verify push will succeed
```

## Команда верификации

**Одна команда выполняет весь протокол:**

```powershell
cd C:\Users\kirillDev\Desktop\TradingBot\minibot
python tools/push_gate.py
```

При PASS:
```powershell
python tools/push_gate.py --execute
```

## Детали каждого гейта

### 1. commit_gate

**Проверяет:**
- Manifest valid
- policy_preflight PASS
- runtime_smoke PASS
- verify_stack PASS
- AI signatures present

**FAIL если:** любая проверка не прошла

### 2. dirty_tree_guard

**Проверяет:** `git status --porcelain` пустой

**FAIL если:**
- Есть untracked файлы в minibot/**
- Есть modified/staged файлы

**Исключение:** `--allow-state` разрешает untracked в state/** (runtime артефакты)

### 3. verify_tree

**Проверяет:** Создаёт детерминированный manifest дерева

**Записывает:** `state/health/tree_manifest.json`

**Содержит:**
- `schema_version`: "tree_manifest_v1"
- `cmdline_sha256`: SSoT binding
- `files[]`: {rel_path, size, mtime_utc, sha256}
- `counts`: {total_files, total_bytes}

**FAIL если:** ошибка чтения/хэширования любого файла

### 4. network_guard

**Проверяет:** Нет прямых сетевых вызовов вне `core/net/**`

**Запрещённые паттерны:**
- `urllib.request.urlopen`
- `requests.(get|post|put|delete|Session)`
- `socket.socket`
- `http.client.HTTP(S)Connection`
- `aiohttp.ClientSession`
- `httpx.(Client|AsyncClient)`

**FAIL если:** найден любой прямой вызов

**Вывод:** только `file:line:pattern` (БЕЗ содержимого строк)

### 5. secrets_guard

**Проверяет:** Нет хардкод секретов в коде

**Ищет:**
- GitHub tokens (`ghp_...`)
- AWS keys (`AKIA...`)
- Slack tokens (`xox[baprs]-...`)
- Private keys (`BEGIN PRIVATE KEY`)
- API key patterns

**FAIL если:** найден потенциальный секрет

**Вывод:** только `file:line` (НИКОГДА содержимое)

### 6. live_smoke_gate

**Проверяет (7 шагов):**
1. Syntax (py_compile core/trade/*.py)
2. Imports (trading modules)
3. policy_preflight
4. verify_stack
5. risk_engine self-test
6. order_router DRY test
7. no_direct_network in core/trade/

**FAIL если:** любой шаг не PASS

### 7. evidence_guard

**Проверяет:** state/health/live_trade.json против схемы

**Обязательные поля:**
- `schema_version` = "live_trade_v1"
- `ts_utc` (ISO format)
- `mode` ∈ {DRY, TESTNET, MAINNET}
- `run_id` (≥20 символов)
- `cmdline_ssot.sha256` (hex, 64 chars)
- `gates.live_gate.passed` = true/false
- `gates.live_gate.decision`

**FAIL если:** поле отсутствует, неверный тип, неверное значение

### 8. testnet_gate

**Проверяет (read-only, NO ORDERS):**
1. testnet.binance.vision в AllowList
2. Ping API отвечает
3. exchangeInfo возвращает данные

**FAIL если:** API недоступен, host не в allowlist

**Пишет evidence:** state/health/testnet_gate.json

### 9. git push --dry-run

**Проверяет:** push будет успешен

**FAIL если:** remote отклоняет (конфликты, permissions)

## Критерии PASS

Все 9 гейтов = PASS.

Нет "частичного PASS". Нет "PASS с предупреждениями".

## Устранение FAIL

### dirty_tree_guard FAIL

```powershell
# Вариант 1: Закоммитить изменения
git add <files>
git commit -m "..."

# Вариант 2: Удалить/игнорировать untracked
rm <file>
# или добавить в .gitignore

# Вариант 3: Stash изменения
git stash
```

### evidence_guard FAIL

```powershell
# Сгенерировать свежий evidence
python run_live_trading.py --mode DRY --symbol BTCUSDT --side BUY --size-usd 100 --once
```

### testnet_gate FAIL

1. Проверить сетевое подключение
2. Убедиться что `testnet.binance.vision` в AllowList
3. Проверить egress wrapper

### live_smoke_gate FAIL

Исправить код согласно выводу гейта.

## Последовательность для нового релиза

```powershell
# 1. Перейти в директорию
cd C:\Users\kirillDev\Desktop\TradingBot\minibot

# 2. Убедиться что working tree чистый
git status

# 3. Сгенерировать DRY evidence
python run_live_trading.py --mode DRY --symbol BTCUSDT --side BUY --size-usd 100 --once

# 4. Запустить полную верификацию
python tools/push_gate.py

# 5. При PASS - push
python tools/push_gate.py --execute
```

## Запрещённые действия

- Использовать `--skip-*` флаги в production верификации
- Игнорировать FAIL и пушить вручную
- Модифицировать гейты чтобы они "проходили"
- Использовать `git push --force`

## Evidence файлы

| Файл | Гейт | Схема |
|------|------|-------|
| state/health/live_trade.json | run_live_trading.py | live_trade_v1 |
| state/health/tree_manifest.json | verify_tree.py | tree_manifest_v1 |
| state/health/spider_health.json | spider | spider_health_v1 |
| state/health/testnet_gate.json | testnet_gate.py | testnet_gate_v1 |
| state/news_run.json | spider | news_run_v1 |

## Схемы версий

При изменении структуры evidence:
1. Инкрементировать версию (v1 → v2)
2. Обновить SCHEMAS в evidence_guard.py
3. Обновить эту документацию
