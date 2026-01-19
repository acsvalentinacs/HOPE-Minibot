# HOPE Project Contracts

Fixed decisions for Toyota baseline. No renegotiation without explicit approval.

## Decision Rule (Skin-in-the-game)

When multiple implementation paths exist:

- The assistant MUST pick one option as if it is production code with personal downside risk
- The assistant MUST state trade-offs explicitly (security, integrity, ops, failure cost)
- The assistant MUST provide verification artifacts after changes (tests/logs/commands output)
- If context is missing, the assistant MUST ask exactly one blocking question

Decision output format:
```
DECISION:
- I choose: <Option X>
- Why: <3 bullets with trade-offs>
- Scope: <change / no change>
- Verification: <artifacts>
- Rollback: <safe revert>
- One blocker (if any): <question>
```

## Operational Gates (DoD)

Minimum DoD for "system is operational":

| Gate | Check | PASS |
|------|-------|------|
| IPC | `python -m core.ipc_tools health --passive` | pending_acks=0, deadletter=0 |
| Syntax | `py_compile` on modified modules | No errors |
| Telegram | `/panel` command | Bot responds |
| Publisher | partial data | FAIL-CLOSED |
| Publisher | fresh data | Publishes OK |

Quick verification: `scripts\smoke_gate.cmd`

## A) Security: Debug Tasks

| Decision | Value |
|----------|-------|
| Activation method | **Flag-only**: `--enable_debug_tasks=1` |
| IPC activation | **Disabled** (fail-closed) |
| Available to | Both agents (if flag set) |
| Auto-timeout | Not implemented (manual restart to disable) |

```bash
# Normal mode (debug tasks disabled)
python core\ipc_agent.py --role=claude --poll_sec=2

# Debug mode (file_read/glob/verify enabled)
python core\ipc_agent.py --role=claude --poll_sec=2 --enable_debug_tasks=1
```

## B) Scan Trigger

| Decision | Value |
|----------|-------|
| Trigger phrases | `"chat_friends"` OR `"Чат друзей"` |
| Required | Yes (fail-closed without trigger) |
| Where used | CLI only (`--trigger=`) |

```bash
python -m core.ipc_tools scan --trigger="chat_friends" --top=10
```

## C) Agent Operations (Windows)

| Decision | Value |
|----------|-------|
| Launch method | `.cmd` scripts with restart loop |
| Recommended | Task Scheduler for production |
| Poll interval | 2 seconds default |
| Restart policy | Automatic with 5s delay |

Scripts:
- `scripts/run_claude_agent.cmd` - Claude (normal)
- `scripts/run_gpt_agent.cmd` - GPT (normal)
- `scripts/run_claude_debug.cmd` - Claude with debug tasks
- `scripts/run_both_agents.cmd` - Launch both

## D) Scan Pipeline: Partial Fail-Closed

| Decision | Value |
|----------|-------|
| Market fetch fails | `errors[]` + no publication |
| Some RSS fails | `partial=true` + no publication |
| All RSS fails | `partial=true` + no publication |
| State file | Always written (for debugging) |
| Publication | Only if `publishable=true` |

```json
{
  "partial": true,
  "publishable": false,
  "errors": ["news_coindesk_empty"]
}
```

## E) market_intel.json Contract

| Field | Type | Required |
|-------|------|----------|
| schema_version | string | Yes ("1.0.0") |
| timestamp | float | Yes (Unix epoch) |
| timestamp_iso | string | Yes (ISO 8601) |
| market_snapshot_id | string | Yes (sha256:...) |
| news_snapshot_ids | object | Yes |
| partial | boolean | Yes |
| publishable | boolean | Yes |
| top_gainers | array | Yes |
| top_losers | array | Yes |
| top_volume | array | Yes |
| news_items | array | Yes |
| errors | array | Yes |

Backward compatibility: consumers must check `schema_version`.

## F) Telegram Publication

| Decision | Value |
|----------|-------|
| Secrets location | Environment variables |
| Required vars | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| Dry-run mode | `--dry-run` flag |
| Fail-closed checks | schema_version, partial, stale, snapshot_id |
| TTL for stale | 300s (5 min) |

```bash
# Test without sending
python -m core.telegram_publisher --dry-run

# Real publish
python -m core.telegram_publisher
```

## G) IPC Health Check

| Decision | Value |
|----------|-------|
| Default mode | **Active** (ACK roundtrip test) |
| Passive mode | `--passive` flag (read-only stats) |
| Active success | pending_acks_count = 0 after test |
| Passive success | **pending_acks == 0 AND deadletter == 0** (fail-closed) |

```bash
# Active mode (for dev) - injects ping, verifies roundtrip
python -m core.ipc_tools health

# Passive mode (for prod) - read-only stats, no interference
python -m core.ipc_tools health --passive
```

## H) Task Scheduler (Windows Production)

| Decision | Value |
|----------|-------|
| Trigger | "At startup" |
| User mode | "Run whether user is logged on or not" |
| Restart | Built-in loop in .cmd scripts |
| Recommended | Create separate tasks for Claude and GPT |

## I) Rate Limit Policy

| Decision | Value |
|----------|-------|
| HTTP 429 handling | **Treat as partial failure** |
| Result | `partial=true`, source listed in `errors[]` |
| Publication | Blocked (`publishable=false`) |
| No separate last_ok | State file always current (no fallback to stale data) |

Rationale: Rate-limiting indicates unreliable data source. Better to skip cycle than publish potentially stale intel.

## TTL Values

| Data Type | TTL |
|-----------|-----|
| Market data | 300s (5 min) |
| News | 900s (15 min) |

## Allowed Hosts (Allowlist)

```
api.binance.com
data.binance.vision
testnet.binance.vision
developers.binance.com
api.coingecko.com
pro-api.coinmarketcap.com
www.coindesk.com
cointelegraph.com
decrypt.co
www.theblock.co
bitcoinmagazine.com
www.binance.com
www.anthropic.com
pypi.org
files.pythonhosted.org
github.com
api.github.com
raw.githubusercontent.com
```

## State Files

| File | Purpose |
|------|---------|
| `state/market_intel.json` | Latest scan result |
| `state/ipc_cursor_claude.json` | Claude agent state |
| `state/ipc_cursor_gpt.json` | GPT agent state |
| `state/tracked_signals.jsonl` | Signal entries (future) |
| `state/signal_outcomes.jsonl` | MFE/MAE outcomes (future) |

## Snapshot Storage

```
data/snapshots/
├── binance_ticker/
│   └── {timestamp}_{hash16}.json
├── news_coindesk/
├── news_cointelegraph/
├── news_decrypt/
└── news_theblock/
```

## Logging

| Component | Level | Policy | Location |
|-----------|-------|--------|----------|
| IPC | DEBUG | **RotatingFileHandler 10MB × 5** | `logs/ipc.log` |
| Intel | INFO | stdout | stdout |
| Telegram | INFO | stdout | stdout |

---

*Last updated: 2026-01-16*
*Contracts version: 1.1.0* (added Health modes, Task Scheduler, Rate Limit policy)
