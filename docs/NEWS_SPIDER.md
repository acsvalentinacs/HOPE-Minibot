<!-- AI SIGNATURE: Created by Claude (opus-4) at 2026-01-25T14:00:00Z -->

# News Spider v1.0

**Purpose:** Collect news from RSS feeds and Binance announcements with egress policy enforcement.

## Architecture

```
sources_registry.json → SourceConfig → AllowList check → http_get → Parser → Dedup → JSONL
                              ↓                              ↓
                        STRICT/LENIENT               Audit Log
```

## Key Features

1. **Egress Policy Enforcement**: All HTTP requests go through `core.net.http_client.http_get`
2. **STRICT/LENIENT Modes**: Fail-fast or continue-on-error behavior
3. **stdlib-only**: No feedparser, requests, or external dependencies
4. **Deduplication**: JSONL-based persistent dedup store with 7-day TTL
5. **Audit Trail**: All network requests logged to `staging/history/egress_audit.jsonl`

## Operating Modes

### STRICT Mode (Production/LIVE)

- Any source failure → FATAL STOP
- AllowList violation for enabled source → FATAL STOP
- Use for production where consistency is critical

```python
collector = NewsCollector(mode=CollectorMode.STRICT)
```

### LENIENT Mode (Development/DRY)

- Source failures logged, collection continues
- AllowList violations → skip source with warning
- Use for development and testing

```python
collector = NewsCollector(mode=CollectorMode.LENIENT)
```

## Source Registry

Location: `config/sources_registry.json`

```json
{
  "sources": [
    {
      "id": "coindesk_rss",
      "name": "CoinDesk RSS",
      "type": "rss",
      "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
      "enabled": true,
      "priority": 1,
      "category": "market",
      "ttl_minutes": 15
    }
  ]
}
```

### Source Types

| Type | Parser | Description |
|------|--------|-------------|
| `rss` | `parse_rss_xml` | RSS 2.0 / Atom feeds |
| `binance_announcements` | `parse_binance_announcements` | Binance API JSON |
| `json_api` | Generic JSON | Future use |

### Required Fields

- `id`: Unique identifier
- `name`: Human-readable name
- `type`: One of `rss`, `binance_announcements`, `json_api`
- `url`: Full URL to fetch

### Optional Fields

- `enabled`: Boolean (default: true)
- `priority`: 1-10, lower = higher priority (default: 5)
- `category`: market, listing, exploit, regulation (default: general)
- `language`: ISO code (default: en)
- `ttl_minutes`: Cache TTL (default: 15)

## Output Files

| File | Purpose |
|------|---------|
| `state/news_items.jsonl` | Collected news items |
| `state/news_dedup.jsonl` | Deduplication store |
| `staging/history/egress_audit.jsonl` | Network audit log |

## Usage

### Python API

```python
from core.spider.collector import NewsCollector, CollectorMode

# Create collector
collector = NewsCollector(mode=CollectorMode.LENIENT)

# Run collection
result = collector.collect(dry_run=False)

# Check results
print(f"Sources: {result.sources_success}/{result.sources_attempted}")
print(f"New items: {result.new_items}")
```

### CLI Smoke Test

```powershell
# LENIENT mode, no persistence
python tools/spider_smoke_test.py --mode lenient --dry-run

# STRICT mode, persist items
python tools/spider_smoke_test.py --mode strict

# Show sources only
python tools/spider_smoke_test.py --skip-collect
```

### Unit Tests

```powershell
python -m unittest tests.test_spider -v
```

## AllowList Integration

All source hosts MUST be in `AllowList.txt`:

```
www.coindesk.com
cointelegraph.com
decrypt.co
www.theblock.co
www.binance.com
rekt.news
```

### Behavior by Mode

| Mode | Host not in AllowList |
|------|----------------------|
| STRICT | `FatalPolicyError` raised |
| LENIENT | Source skipped with warning |

## Error Handling

### RSS Parsing Errors

- Invalid XML → `ParseError`
- Unknown format → `ParseError`
- Missing channel → `ParseError`

### HTTP Errors

- Non-200 status → `SourceResult.success = False`
- Egress denied → `EgressDeniedError` (caught, logged)
- Network error → `EgressError` (caught, logged)

## Deduplication

Items are deduplicated by `item_id` (SHA256 hash of link or title+source):

```python
# Check if duplicate
if dedup_store.contains(item_id):
    continue  # Skip

# Add new item
dedup_store.add(item_id, source_id, link)
```

Entries expire after 7 days (configurable).

## Security

1. **No secrets in URLs**: Binance announcements use public API
2. **URL hashed in audit**: Only SHA256 prefix stored
3. **Egress enforced**: Cannot fetch from unlisted hosts
4. **Redirect validation**: Redirects to different hosts blocked

## Verification

```powershell
# Unit tests
python -m unittest tests.test_spider -v

# Smoke test
python tools/spider_smoke_test.py --mode lenient --dry-run

# Check audit log
Get-Content staging/history/egress_audit.jsonl -Tail 10
```

## Dependencies

**stdlib-only:**
- `xml.etree.ElementTree` (RSS parsing)
- `json` (JSON parsing, persistence)
- `hashlib` (SHA256 for dedup IDs)
- `email.utils` (RFC 822 date parsing)
- `urllib.parse` (URL parsing)
- `dataclasses` (data structures)

**Internal:**
- `core.net.http_client` (egress-controlled HTTP)
- `core.net.net_policy` (AllowList validation)
- `core.net.audit_log` (audit trail)
