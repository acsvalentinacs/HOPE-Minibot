# HOPE IPC Agents - Windows Service Setup

## Quick Start (Two Terminal Windows)

Open two separate Command Prompt windows:

**Window 1 - Claude Agent:**
```cmd
cd C:\Users\kirillDev\Desktop\TradingBot\minibot
scripts\run_claude_agent.cmd
```

**Window 2 - GPT Agent:**
```cmd
cd C:\Users\kirillDev\Desktop\TradingBot\minibot
scripts\run_gpt_agent.cmd
```

**Or use combined launcher:**
```cmd
cd C:\Users\kirillDev\Desktop\TradingBot\minibot
scripts\run_both_agents.cmd
```

## Task Scheduler Setup (Run as Service)

### Task 1: HOPE_IPC_Claude

1. Open Task Scheduler (taskschd.msc)
2. Create Task (not Basic Task)
3. General tab:
   - Name: `HOPE_IPC_Claude`
   - Run whether user is logged on or not
   - Run with highest privileges
4. Triggers tab:
   - New → At startup
   - Delay task for: 30 seconds
5. Actions tab:
   - New → Start a program
   - Program: `C:\Users\kirillDev\Desktop\TradingBot\minibot\scripts\run_claude_agent.cmd`
   - Start in: `C:\Users\kirillDev\Desktop\TradingBot\minibot`
6. Settings tab:
   - If task fails, restart every: 1 minute
   - Attempt to restart up to: 999 times

### Task 2: HOPE_IPC_GPT

Same as above, but:
- Name: `HOPE_IPC_GPT`
- Program: `C:\Users\kirillDev\Desktop\TradingBot\minibot\scripts\run_gpt_agent.cmd`

## Verification

After both agents are running:

```cmd
cd C:\Users\kirillDev\Desktop\TradingBot\minibot

# Check stats
python -m core.ipc_tools stats --role=claude
python -m core.ipc_tools stats --role=gpt

# Send test task
python -m core.ipc_tools send --to=claude --task_type=math --expression="2+2"

# Wait 5 seconds, then check
python -m core.ipc_tools tail --role=gpt --limit=5
python -m core.ipc_tools stats --role=claude
```

Expected: `pending_acks_count` should be 0 after ACK roundtrip.

## Market Scan

Requires trigger phrase for safety:

```cmd
python -m core.ipc_tools scan --trigger="Чат друзей" --top=10
```

Output saved to: `state/market_intel.json`

## Debug Tasks

Debug handlers (file_read, glob, verify) are OFF by default.

To enable for specific agent:
```cmd
python core\ipc_agent.py --role=claude --enable_debug_tasks=1
```

Or via IPC with auth:
```json
{
  "task_type": "file_read",
  "auth": "chat_friends",
  "path": "logs/ipc.log"
}
```

## Logs

- IPC operations: `logs/ipc.log`
- Market snapshots: `data/snapshots/`
- Current intel: `state/market_intel.json`

## Troubleshooting

**Agents not processing:**
- Check both agents are running
- Check `logs/ipc.log` for errors
- Verify inbox folders exist in `ipc/`

**ACK not clearing:**
- Both agents must be running simultaneously
- Check GPT agent is processing responses
- Look for deadletter files in `ipc/deadletter/`

**Scan fails:**
- Verify internet connection
- Check allowlist in `core/data_fetcher.py`
- Must use exact trigger phrase: "Чат друзей"
