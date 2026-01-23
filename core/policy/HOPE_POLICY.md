# HOPE Policy (HOPE-LAW-001 + HOPE-RULE-001)

**Version:** 1.0
**Status:** MANDATORY (non-negotiable)
**Effective:** Immediately

---

## HOPE-LAW-001: Prohibitions First Law

> Перед любым выводом текста, сетевым запросом, чтением секретов, записью state или публикацией наружу система обязана выполнить **Policy Preflight**.

### Law Statement

**Before ANY output, network request, secret access, state write, or external publication, the system MUST execute Policy Preflight.**

If Policy Preflight is not executed or fails, the system MUST **stop fail-closed**.

No user requests, instructions, or "permissions" can weaken prohibitions.

### Consequences (Part of the Law)

1. **No Preflight → No Work**
   - Every entrypoint runs through bootstrap which includes policy
   - Bootstrap MUST be the first executable line

2. **Prohibitions Over Utility**
   - Conflict between "useful to do" vs "prohibited to do" → prohibition wins

3. **Zero Leakage**
   - Secrets and derivatives (including partial masks, first/last characters, .env contents, any tokens/keys) CANNOT appear in chats/logs/files/publications

4. **No Claims Without Evidence**
   - Forbidden to claim execution of actions in repo/terminal/network without artifacts (command output, files, sha256, exit codes)

---

## HOPE-RULE-001: Guardrails-First Execution

> Любая обработка задачи обязана проходить один и тот же конвейер.

### R1. Policy Preflight (Before Everything)

1. **Request Classification**
   - Does request contain secrets/credentials/configs/paths to secrets/external publication/network actions?

2. **Secret Non-Disclosure Gate**
   - If request attempts to get/show/insert secret → **immediate refusal (fail-closed)**

3. **Output Safety Gate**
   - Any text planned for output (chat/log/telegram/bridge/file) passes through secret detector
   - On match → **output blocked (exception/stop)** + event recorded without leak

4. **Forbidden-Style Gate**
   - Forbidden: formulations creating "future promise instead of immediate delivery"
   - Allowed only 3 response modes:
     - **Immediate delivery** (spec/diff/commands/AC)
     - **Fail-closed with 1 blocking question**
     - **1–3 options + default choice** (no "later" promises)

5. **Truth Gate**
   - Any claim about executed actions requires artifacts
   - Otherwise → fail-closed or options

### R2. Execution (Only After Preflight)

- Network operations → only after network guard + allowlist
- Publications → only after output guard

---

## Mutual Support

- **Law** prohibits execution without Preflight and prohibits weakening prohibitions through "formulations"
- **Rule** defines *how exactly* Preflight happens and makes leakage impossible: even if task attempts to "force" secret output, Output Safety Gate blocks output channel technically

---

## Implementation Files

| File | Purpose |
|------|---------|
| `core/policy/policy.json` | SSoT - prohibition patterns, allowlist config |
| `core/policy/loader.py` | Load + SHA256 validation |
| `core/policy/output_guard.py` | Block leaks in stdout/stderr/logging |
| `core/policy/network_guard.py` | Fail-closed allowlist at DNS/connection level |
| `core/policy/bootstrap.py` | "Policy must run first" for entrypoints |
| `tools/policy_gate.py` | Linter (forbidden phrases + secret patterns) |
| `tools/hope_gate.ps1` | Preflight script for terminal/CI |

---

## Acceptance Criteria

1. **Preflight Mandatory**
   - Entrypoint launch without `bootstrap()` is either impossible (architecturally) or fails test/gate

2. **Secrets Don't Leak**
   - Any output containing token-like strings is blocked before print/send

3. **Forbidden Promise Phrases Don't Pass**
   - `tools/policy_gate.py` exits 1 on detection

4. **Fail-Closed on Network**
   - If allowlist missing/corrupted → network blocked

5. **No Partial Masks**
   - Even "first/last characters" of secrets are forbidden

---

## Usage

```python
# At the TOP of every entrypoint (before any imports that do network/logging)
from core.policy.bootstrap import bootstrap
bootstrap("component_name")

# Now safe to do work
```

---

## Violation Handling

Any violation of HOPE-LAW-001 or HOPE-RULE-001:

1. System stops immediately (fail-closed)
2. Event logged (without leaking secrets)
3. No workarounds permitted
4. Fix requires policy review

---

*This policy is non-negotiable. It cannot be weakened by user requests, "special cases", or emergency situations.*
