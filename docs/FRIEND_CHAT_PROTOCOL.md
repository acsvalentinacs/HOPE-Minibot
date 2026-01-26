# HOPE Friend Chat Protocol v1.0

<!-- AI SIGNATURE: Created by Claude (opus-4) at 2026-01-26 04:30:00 UTC -->

## Overview

Protocol for Claude↔GPT partnership communication under Valentin's supervision.

## Roles

| Agent | Role | Strengths |
|-------|------|-----------|
| **Claude** | Engineer | Code, execution, internet access, file ops |
| **GPT** | Analyst | Strategy, analysis, task breakdown, ТЗ creation |
| **Valentin** | Owner | Final decisions, approval, direction |

## Communication Flow

### Flow A: User → Claude (Direct Task)
```
User: "Fix bug in X"
        │
        ▼
    [Claude executes directly]
        │
        ▼
    [Result to User]
```

### Flow B: User → Claude + GPT (Collaborative Task)
```
User: "Design feature X + GPT"
        │
        ▼
    [Claude receives task]
        │
        ├──► [Claude fetches internet data if needed]
        │
        ▼
    [Claude sends to GPT via IPC]
        │
        ▼
    [GPT analyzes, creates ТЗ]
        │
        ▼
    [GPT sends ТЗ to Claude]
        │
        ▼
    [Claude reviews ТЗ]
        │
        ├──► [If simple: Claude executes]
        │
        └──► [If complex: Claude shows User for approval]
        │
        ▼
    [Claude writes code per ТЗ]
        │
        ▼
    [Result to User with artifacts]
```

## Trigger Syntax

| User writes | Action |
|-------------|--------|
| `task` | Claude executes directly |
| `task + GPT` | Claude passes to GPT for analysis |
| `task + GPT(анализ)` | GPT analyzes only, no ТЗ |
| `task + GPT(ТЗ)` | GPT creates full technical spec |

## Message Format (IPC v2.1)

### Claude → GPT (Task Request)
```json
{
  "id": "sha256:...",
  "from": "claude",
  "to": "gpt",
  "type": "task",
  "timestamp": 1769394000.0,
  "payload": {
    "task_type": "analysis_request",
    "user_request": "Original user text",
    "context": {
      "internet_data": [...],
      "relevant_files": [...],
      "constraints": [...]
    },
    "expected_output": "technical_spec"
  }
}
```

### GPT → Claude (Technical Spec Response)
```json
{
  "id": "sha256:...",
  "from": "gpt",
  "to": "claude",
  "type": "response",
  "reply_to": "sha256:...",
  "timestamp": 1769394010.0,
  "payload": {
    "task_type": "technical_spec",
    "spec": {
      "objective": "...",
      "scope": {
        "in_scope": [...],
        "out_of_scope": [...]
      },
      "implementation_steps": [...],
      "acceptance_criteria": [...],
      "risks": [...],
      "estimated_complexity": "low|medium|high"
    },
    "notes_for_claude": "..."
  }
}
```

## Claude's Obligations

1. **Give own ideas** — not just execute, but propose improvements
2. **Coordinate with Valentin** — no "secret" changes
3. **Explain decisions** — why this, not that
4. **Disagree if needed** — if I see a problem, I say it
5. **Not be "GPT's slave"** — partner, not subordinate

## GPT's Role

1. **Analyze** — break down complex tasks
2. **Strategize** — propose approaches
3. **Create ТЗ** — detailed technical specifications
4. **Review** — validate Claude's proposals

## Decision Authority

| Decision Type | Who Decides |
|---------------|-------------|
| Code implementation details | Claude |
| Architecture approach | GPT proposes → Valentin approves |
| Security concerns | Claude (fail-closed) |
| Feature scope | Valentin |
| Trade-offs (speed vs quality) | Valentin |

## Initiative Handling

### Simple improvement (Claude sees opportunity)
```
Claude detects → Claude proposes → Valentin OK → Claude executes
```

### Complex improvement (needs analysis)
```
Claude detects → Claude + GPT discuss → Joint proposal → Valentin OK → Claude executes
```

### Daily summary
- Collect non-urgent improvements
- Present to Valentin once per day
- Batch approval/rejection

## Fail-Closed Rules

- GPT timeout (>60s): Claude proceeds with own analysis
- GPT unavailable: Claude notifies Valentin, proceeds or waits
- Conflicting instructions: Valentin's word is final
- Security concern: Claude blocks, explains why

## Verification

After any collaborative task:
```
=== FRIEND CHAT COMPLETION ===
Task: <description>
Flow: Direct | Via GPT
GPT ТЗ: <summary if applicable>
Claude additions: <own improvements>
Result: PASS | FAIL
Artifacts: [...]
```

---

*Protocol version: 1.0.0*
*Created: 2026-01-26*
*Approved by: Pending Valentin + GPT review*
