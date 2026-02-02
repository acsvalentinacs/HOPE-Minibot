# HOPE AUTO-START RULES - Multi-Layer Control System

<!-- AI SIGNATURE: Created by Claude (opus-4.5) at 2026-02-02 15:40:00 UTC -->

---

## EXECUTIVE SUMMARY

```
╔═══════════════════════════════════════════════════════════════════╗
║                HOPE AUTO-START CONTROL SYSTEM                      ║
╠═══════════════════════════════════════════════════════════════════╣
║  Layer 1: Process Check      - Avoid duplicate processes          ║
║  Layer 2: Port Check         - Verify service binding             ║
║  Layer 3: Health Check       - Verify service responding          ║
║  Layer 4: Watchdog           - Continuous monitoring + restart    ║
║  Layer 5: Supervisor         - Windows Task Scheduler             ║
╚═══════════════════════════════════════════════════════════════════╝
```

---

## КОМПОНЕНТЫ И ПОРЯДОК ЗАПУСКА

| # | Компонент | Порт | Обязательный | Проверка |
|---|-----------|------|--------------|----------|
| 1 | pricefeed_gateway | 8100 | ДА | Port + HTTP |
| 2 | autotrader | 8200 | ДА | Port + HTTP /status |
| 3 | momentum_trader | - | НЕТ | Process name |
| 4 | health_daemon | - | НЕТ | Process name |

---

## LAYER 1: MANUAL START

### Быстрый запуск (одна команда)

```powershell
cd C:\Users\kirillDev\Desktop\TradingBot\minibot
.\tools\hope_autostart.ps1
```

### Запуск с опциями

```powershell
# Пропустить momentum_trader
.\tools\hope_autostart.ps1 -SkipMomentum

# Только показать что будет запущено
.\tools\hope_autostart.ps1 -DryRun

# Принудительно перезапустить всё
.\tools\hope_autostart.ps1 -Force
```

### Ручной запуск компонентов

```powershell
cd C:\Users\kirillDev\Desktop\TradingBot\minibot

# 1. Pricefeed Gateway
Start-Process python -ArgumentList "scripts/pricefeed_gateway.py"

# 2. AutoTrader (LIVE!)
Start-Process python -ArgumentList "scripts/autotrader.py","--mode","LIVE","--yes","--confirm"

# 3. Momentum Trader
Start-Process python -ArgumentList "scripts/momentum_trader.py","--daemon"

# 4. Health Daemon
Start-Process python -ArgumentList "scripts/hope_health_daemon.py"
```

---

## LAYER 2: WATCHDOG MONITORING

### Health Daemon (встроенный)

Health Daemon автоматически:
- Проверяет все компоненты каждые 60 минут
- Перезапускает упавшие сервисы
- Логирует состояние в `state/health/`

```powershell
# Запуск Health Daemon
python scripts/hope_health_daemon.py --interval 60

# Одиночная проверка
python scripts/hope_health_daemon.py --once
```

### Диагностика (ручная)

```powershell
# Полная диагностика
python scripts/hope_diagnostics.py

# Диагностика + авто-ремонт
python scripts/hope_diagnostics.py --fix
```

---

## LAYER 3: WINDOWS TASK SCHEDULER

### Создание задачи (запуск при старте Windows)

```powershell
# Создать задачу в Task Scheduler
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -File C:\Users\kirillDev\Desktop\TradingBot\minibot\tools\hope_autostart.ps1" `
    -WorkingDirectory "C:\Users\kirillDev\Desktop\TradingBot\minibot"

$trigger = New-ScheduledTaskTrigger -AtStartup

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName "HOPE_AutoStart" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Auto-start HOPE Trading System"
```

### Создание задачи (периодическая проверка)

```powershell
# Проверка каждый час
$action = New-ScheduledTaskAction `
    -Execute "python.exe" `
    -Argument "scripts/hope_diagnostics.py --fix" `
    -WorkingDirectory "C:\Users\kirillDev\Desktop\TradingBot\minibot"

$trigger = New-ScheduledTaskTrigger `
    -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Hours 1)

Register-ScheduledTask `
    -TaskName "HOPE_HealthCheck" `
    -Action $action `
    -Trigger $trigger `
    -Description "Hourly health check for HOPE"
```

---

## LAYER 4: SUPERVISOR SCRIPT

### Непрерывный мониторинг (supervisor.ps1)

```powershell
# tools/hope_supervisor.ps1
while ($true) {
    $status = Invoke-RestMethod "http://127.0.0.1:8200/status" -ErrorAction SilentlyContinue

    if (-not $status -or -not $status.running) {
        Write-Host "$(Get-Date) | ALERT: AutoTrader not responding, restarting..."
        .\tools\hope_autostart.ps1 -Force
    }

    Start-Sleep -Seconds 300  # Check every 5 minutes
}
```

---

## LAYER 5: STARTUP CHECKLIST

### При каждом запуске проверить:

```
□ Секреты загружены (C:\secrets\hope.env)
□ Порты свободны (8100, 8200)
□ Binance API отвечает
□ Баланс > $50
□ Circuit breaker не сработал
□ Нет открытых позиций в убытке > 10%
```

### Команда проверки:

```powershell
# Полная проверка перед запуском
python scripts/hope_diagnostics.py
```

---

## ЛОГИРОВАНИЕ

### Файлы логов

| Файл | Содержимое |
|------|------------|
| `state/startup/startup_YYYYMMDD.log` | Логи автозапуска |
| `state/health/health_checks.jsonl` | История health check |
| `logs/autotrader.log` | Логи торговли |
| `logs/momentum_trader.log` | Логи momentum scanner |

---

## АЛЕРТЫ

### Telegram уведомления (TODO)

```python
# Добавить в health_daemon.py
async def send_alert(message: str):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_ADMIN_ID")
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    await httpx.post(url, json={"chat_id": chat_id, "text": f"🚨 HOPE ALERT: {message}"})
```

---

## КОМАНДЫ ДЛЯ ЗАПОМИНАНИЯ

```powershell
# === HOPE QUICK COMMANDS ===

# Запустить всё
.\tools\hope_autostart.ps1

# Проверить статус
curl http://127.0.0.1:8200/status

# Диагностика
python scripts/hope_diagnostics.py

# Health check
python scripts/hope_health_daemon.py --once

# Остановить всё
Get-Process python* | Where-Object {$_.CommandLine -like "*minibot*"} | Stop-Process
```

---

**Document Version:** 1.0
**Author:** Claude (opus-4.5)
**Date:** 2026-02-02
**Status:** ACTIVE
