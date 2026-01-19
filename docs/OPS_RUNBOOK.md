# HOPE Ops Runbook

Operational guide for running HOPE on Windows.

## Non-negotiables (Policy)

1. **IPC v2.1 is FROZEN** - no core changes without reproducible defect
2. **State/IPC/journal files** - atomic write only (temp -> fsync -> replace)
3. **No financial promises** - all performance claims backed by outcome tracking
4. **Fail-closed by default** - partial/stale/missing data blocks publication

## Quick Health Checks

### Smoke Gate (One Command)
```cmd
scripts\smoke_gate.cmd
```
PASS = py_compile OK + IPC healthy + ready for operation.

### IPC Health (Passive)
```cmd
python -m core.ipc_tools health --passive
```
PASS:
- pending_acks == 0 for both agents
- deadletter == 0

### Telegram Bot
Send `/panel` to bot from allowed admin account.

PASS: Bot responds with panel info.

### Publisher (Fail-closed Test)
```cmd
python -m core.telegram_publisher --dry-run
```
PASS with fresh data: "Published intel to Telegram (dry_run=True)"
PASS with stale data: "FAIL-CLOSED: intel validation failed: stale_data"

## Typical Incidents

### "Bot is silent"

Likely causes:
1. Bot process not running
2. Wrong Python interpreter (WindowsApps/system Python)
3. Missing dependency in venv
4. User not in allowed_ids
5. Wrong TELEGRAM_CHAT_ID

Immediate steps:
1. Check bot console for errors
2. Verify correct python: `where python` should show venv path
3. Check env loaded: token present in logs
4. Verify allowed_ids includes your Telegram user ID

### "IPC pending_acks growing"

Causes:
1. Peer agent not running
2. Network/filesystem issue
3. Malformed message

Steps:
1. Verify both agents running: `Get-Process python*`
2. Check logs in `logs/ipc.log`
3. Check deadletter folder for stuck messages

### "Publisher fails with stale_data"

Normal behavior if scan hasn't run recently.

Fix:
```cmd
python -m core.ipc_tools scan --trigger="chat_friends"
python -m core.telegram_publisher --dry-run
```

## Daily Operations

### Morning (10:00 AM)
Morning scan runs automatically via Task Scheduler:
- Fetches market data from Binance
- Fetches news from 4 RSS sources
- Publishes to Telegram channel if data is complete

Manual trigger:
```cmd
scripts\run_morning_scan.cmd
```

### After Any Code Change
1. `py_compile` on modified modules
2. `scripts\smoke_gate.cmd`
3. `/panel` manual check if Telegram touched

## Process Management

### Start Agents
```cmd
scripts\run_claude_agent.cmd
scripts\run_gpt_agent.cmd
```

Or both:
```cmd
scripts\run_both_agents.cmd
```

### Task Scheduler Setup
```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_task_scheduler.ps1
```

Creates tasks:
- HOPE-Claude-Agent (at logon)
- HOPE-GPT-Agent (at logon)
- HOPE-Morning-Scan (daily 10:00 AM)

## Rollback Principle

1. Prefer revert-by-git or restore-from-archive
2. Archive folder: `C:\Users\kirillDev\Desktop\TradingBot\Старые файлы от проекта НОРЕ 2025-11-23`
3. NEVER delete production files - only archive

## Contact

Owner: Valentin
Channel: https://t.me/hope_vip_signals

---
*Last updated: 2026-01-17*
