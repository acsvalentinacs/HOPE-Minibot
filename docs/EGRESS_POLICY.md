<!-- === AI SIGNATURE ===
Created by: Claude (opus-4)
Created at (UTC): 2026-01-25T12:30:00Z
Purpose: Egress Policy documentation (SSoT reference)
=== END SIGNATURE === -->

# HOPE Egress Policy

## Overview

All outbound HTTP(S) requests from HOPE components MUST go through the egress policy layer.
This ensures:

1. **Fail-closed security**: Only explicitly allowed hosts can be contacted
2. **Auditability**: Every request attempt is logged (ALLOW/DENY)
3. **No secret leakage**: URLs are hashed in logs, no sensitive data stored

## Single Source of Truth (SSoT)

| Component | Location | Purpose |
|-----------|----------|---------|
| **AllowList.txt** | `./AllowList.txt` (repo root) | List of allowed hostnames |
| **HTTP Wrapper** | `core/net/http_client.py` | Enforced egress point |
| **Audit Log** | `staging/history/egress_audit.jsonl` | Request audit trail |

## AllowList.txt Format

```
# Comments start with #
# One hostname per line
# NO schemes (http://), ports (:8080), paths (/api), wildcards (*)

api.binance.com
api.coingecko.com
www.example.com
```

### Validation Rules

- **Allowed**: lowercase hostnames, digits, hyphens, dots
- **Forbidden**: schemes (`://`), ports (`:`), paths (`/`), wildcards (`*`), query (`?`), fragment (`#`)
- **Normalization**: uppercase → lowercase, trailing dot removed

### Fail-Closed Behavior

- Missing AllowList.txt → **FatalPolicyError** (application stops)
- Empty AllowList.txt → **FatalPolicyError**
- Invalid entry → **PolicyValidationError** (shows line number)

## Usage

### Making HTTP Requests

```python
from core.net import http_get

# Simple GET request
status, body, final_url = http_get(
    "https://api.binance.com/api/v3/time",
    timeout_sec=10,
    process="my_module"  # For audit log
)

# Handle response
if status == 200:
    data = json.loads(body)
```

### Handling Denials

```python
from core.net import http_get, EgressDeniedError

try:
    status, body, url = http_get("https://unknown-host.com/")
except EgressDeniedError as e:
    print(f"Blocked: {e.host} - {e.reason.value}")
    print(f"Audit ID: {e.request_id}")
```

## Audit Log Format

Each request generates a JSONL record:

```json
{
  "ts_utc": "2026-01-25T12:30:00.123Z",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "process": "news_spider",
  "action": "ALLOW",
  "host": "api.binance.com",
  "reason": "host_in_allowlist",
  "latency_ms": 150,
  "url_sha256": "a1b2c3d4e5f67890"
}
```

### Fields

| Field | Description |
|-------|-------------|
| `ts_utc` | ISO8601 timestamp (UTC) |
| `request_id` | UUID for tracing |
| `process` | Caller identifier |
| `action` | `ALLOW` or `DENY` |
| `host` | Target hostname |
| `reason` | Standardized reason code |
| `latency_ms` | Request duration |
| `url_sha256` | Hash of full URL (no secrets in log) |

### Reason Codes

**ALLOW reasons:**
- `host_in_allowlist` - Normal allowed request

**DENY reasons:**
- `host_not_in_allowlist` - Host not in AllowList.txt
- `redirect_to_different_host` - Redirect to unauthorized host
- `policy_load_failed` - AllowList.txt couldn't be loaded
- `invalid_url` - Malformed URL
- `network_error` - Connection/DNS failure
- `timeout` - Request timeout

## Verification Commands

### Run Unit Tests

```powershell
cd "C:\Users\kirillDev\Desktop\TradingBot\minibot"
& "..\.venv\Scripts\python.exe" -m unittest tests.test_net_policy -v
```

### Run Smoke Test

```powershell
.\tools\run_egress_smoke.ps1
```

### Check for Policy Bypasses

```powershell
.\tools\net_policy_grep_guard.ps1
```

### View Audit Log

```powershell
Get-Content .\staging\history\egress_audit.jsonl | ConvertFrom-Json | Select-Object -Last 10
```

### One-Liner Verification

```powershell
cd "C:\Users\kirillDev\Desktop\TradingBot\minibot"; & "..\.venv\Scripts\python.exe" -m unittest tests.test_net_policy -v; .\tools\net_policy_grep_guard.ps1; .\tools\run_egress_smoke.ps1; Get-Content .\staging\history\egress_audit.jsonl -Tail 5
```

## Adding New Hosts

1. Edit `AllowList.txt` in repo root
2. Add hostname (one per line, no scheme/port/path)
3. Run smoke test to verify
4. Commit with message: `chore(net-policy): add <hostname> to allowlist`

**Example:**

```diff
# AllowList.txt
 api.binance.com
 api.coingecko.com
+api.newservice.com
```

## Security Considerations

### What IS logged

- Timestamp
- Hostname
- Action (ALLOW/DENY)
- Latency
- URL hash (sha256)

### What is NOT logged

- Full URL (may contain tokens in query)
- Request headers (may contain API keys)
- Request/response body
- Environment variables

### Redirect Safety

If a request to `allowed.com` redirects to `evil.com`:
- `evil.com` is checked against AllowList
- If not allowed → **DENY** (redirect blocked)
- Audit log shows: `reason: redirect_to_different_host`

## Troubleshooting

### "FatalPolicyError: AllowList.txt not found"

AllowList.txt must exist in repo root. Check:
```powershell
Test-Path ".\AllowList.txt"
```

### "EgressDeniedError: host_not_in_allowlist"

Add the host to AllowList.txt:
```powershell
Add-Content -Path ".\AllowList.txt" -Value "new.host.com"
```

### Audit log not appearing

Ensure `staging/history/` directory exists:
```powershell
New-Item -ItemType Directory -Force -Path ".\staging\history"
```

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                      Application Code                       │
│  (news_spider, trading_bot, telegram_bot, etc.)            │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      │ http_get(url, ...)
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                  core/net/http_client.py                    │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ Parse URL    │→ │ Check Allow  │→ │ urlopen() or     │  │
│  │ Extract Host │  │ List.txt     │  │ DENY             │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
│         │                 │                    │            │
│         │                 │                    │            │
│         ▼                 ▼                    ▼            │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              Audit Log (JSONL)                       │  │
│  │         staging/history/egress_audit.jsonl           │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```
