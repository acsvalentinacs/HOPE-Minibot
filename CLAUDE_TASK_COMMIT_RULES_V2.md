<!--
AI SIGNATURE (meta-only, no secrets):
Created: 2026-01-25T00:00:00Z
Purpose: SSoT commit protocol for HOPEminiBOT (PowerShell-safe, fail-closed)
-->

# HOPEminiBOT — Git Commit Rules v2 (PowerShell 5.1, fail-closed, no secrets)

## 0) Scope and Authority (SSoT)
This document is the **single source of truth** for how commits are created in this repo on Windows / PowerShell 5.1.

**Primary goals:**
1) Prevent secret leakage into git history (fail-closed).
2) Prevent accidental destructive actions.
3) Produce reproducible, reviewable commits with explicit scope.

**Non-goals:**
- No pushing or rewriting history unless explicitly requested by the owner.
- No "clever" shortcuts (Bash heredocs, interactive modes).

---

## 1) Trigger (when committing is allowed)
A commit is performed **only** when the owner explicitly instructs: **"commit"** / "коммить".

If unclear or implicit — STOP. No commit.

---

## 2) Absolute Prohibitions (no exceptions unless owner explicitly demands)
### 2.1 Git destructive / history rewrite
Do **NOT** run:
- `git push` (any), `git push --force`, `git push --force-with-lease`
- `git reset --hard`, `git checkout .`, `git restore .`
- `git clean -f` / `git clean -fd`
- `git branch -D`
- `git rebase -i` or any interactive rewrite

### 2.2 Hook bypass / signature bypass
Do **NOT** use:
- `--no-verify`
- `--no-gpg-sign`
- Any hook bypass flag unless explicitly requested by owner (rare).

### 2.3 Amend policy
Do **NOT** use:
- `git commit --amend`
unless owner explicitly requests amend.
Default: always create a **new** commit.

### 2.4 Secrets and sensitive files
Never commit:
- `.env`, `*.env`, `*.env.*`, `*.bak*`
- anything under `C:\secrets\hope\` (or any absolute secrets path)
- credential/token exports, raw dumps, logs containing secrets

If any of these appear in the candidate change list — STOP.

---

## 3) Allowed Commands (read-only allowed; no file modifications except staging/commit)
Allowed:
- `git status`, `git diff`, `git diff --cached`, `git log`, `git rev-parse`
- PowerShell read-only inspection: `Select-String`, `Get-FileHash`, `Get-Content -TotalCount`, `Get-Item`
- **No** filesystem deletion. **No** mass staging (`git add -A`) unless owner explicitly approves.

---

## 4) Pre-Commit Checklist (mandatory, fail-closed)
Run in repo root:
- `C:\Users\kirillDev\Desktop\TradingBot\minibot`

### 4.1 Confirm repo root and branch
Commands:
- `git rev-parse --show-toplevel`
- `git rev-parse --abbrev-ref HEAD`

STOP if top-level is not expected or branch is unexpected.

### 4.2 Inventory changes
Commands:
- `git status`
- `git diff --name-only`
- `git diff --stat`

STOP if:
- unexpected paths appear
- large binary files appear
- secret-like files appear

### 4.3 Hard Secret Scan (MANDATORY)
Rationale: **Only staged content matters** for leakage into history. Scan both unstaged and staged, but staged is decisive.

#### 4.3.1 Scan unstaged (early signal)
PowerShell:
- `git diff | Select-String -SimpleMatch -Pattern "signature=", "X-MBX-APIKEY", "API_SECRET", "SECRET=", "PRIVATE KEY", "BEGIN RSA", "BEGIN OPENSSH", "BINANCE_"`

#### 4.3.2 Scan staged (DECISIVE; must be clean)
PowerShell:
- `git diff --cached | Select-String -SimpleMatch -Pattern "signature=", "X-MBX-APIKEY", "API_SECRET", "SECRET=", "PRIVATE KEY", "BEGIN RSA", "BEGIN OPENSSH"`

#### 4.3.3 Heuristic token scan (DECISIVE; fail-closed)
PowerShell (regex heuristics):
- `git diff --cached | Select-String -Pattern "\b[a-fA-F0-9]{32,128}\b", "\b[A-Za-z0-9_\/\+\-]{40,}\b"`

**STOP RULE (non-negotiable):**
If any of the staged scans matches suspicious data:
- STOP immediately
- do not commit
- do not paste suspicious lines into chat if they look like real secrets
- report only filenames + line counts + which pattern triggered

---

## 5) Staging Policy (default is explicit-file staging)
Default: stage only explicit paths.

Forbidden by default:
- `git add -A`
- `git add .`

Allowed:
- `git add <explicit file list>`

Reason: prevent accidental inclusion of secrets, caches, logs, state directories.

---

## 6) Recommended Commit Topology (2 commits, not 1)
Reason: functional changes and network/security policy changes must be independently reviewable and revertible.

### Commit A: Feature / Gate / Runner / Docs
Stage only:
- `tests\test_binance_online_gate.py`
- `tools\run_binance_gate.ps1`
- `tools\load_binance_env.ps1`
- `CLAUDE_TASK_BINANCE_ONLINE_GATE_FULL.md`
- `CLAUDE_TASK_BINANCE_ONLINE_GATE_10L.txt`
- `CLAUDE_TASK_COMMIT_RULES_V2.md` (this doc)

Verify staged:
- `git diff --cached --name-only`
- `git diff --cached --stat`
- Run staged secret scan again (Section 4.3.2 + 4.3.3)

Commit message (PowerShell-safe):
- Use multi `-m` lines (no heredoc, no Bash).
Example:
`git commit -m "feat(gate): add Binance online gate with evidence pack and runner" -m "Stdlib-only pytest gate for Binance public/private endpoints; atomic report.json + sha256; PowerShell gate runner; fail-closed secret handling."`

Optional footer (ONLY if repo policy requires it; otherwise omit):
- `-m "Co-Authored-By: <name> <email>"`

### Commit B: Network Policy / Baseline Lock (only with explicit owner approval)
Stage only:
- `AllowList.txt`
- `tools\baseline_locks.json`

Verify staged:
- `git diff --cached --name-only`
- `git diff --cached --stat`
- `git diff --cached` (review actual content)
- Run staged secret scan again (Section 4.3.2 + 4.3.3)

Commit message example:
`git commit -m "chore(net-policy): baseline bump allowlist for Binance hosts (owner approved)" -m "Adds Binance api1-4/stream/fapi/dapi (and other explicitly approved hosts); updates baseline lock accordingly."`

STOP if approval is not explicit or if the change scope is broader than requested.

---

## 7) Handling Hook Failures (pre-commit / CI gate)
If hooks fail:
1) Do not bypass hooks.
2) Fix the issue.
3) Re-stage required files explicitly.
4) Create a **new** commit (do not amend) unless owner explicitly requests amend.

---

## 8) Post-Commit Verification (read-only)
Commands:
- `git status`
- `git log -1 --name-only`

STOP if anything unexpected appears.

---

## 9) Owner-Facing Deliverables (what to report after commit)
After successful commits, provide:
- commit hashes
- list of files per commit (from `git log -1 --name-only`)
- confirmation that staged secret scans were clean (no matched patterns)

Do not paste any sensitive content.

---

## 10) PowerShell Reference Snippets (copy-ready)
### 10.1 Minimal staged scan pack
- `git diff --cached | Select-String -SimpleMatch -Pattern "signature=", "X-MBX-APIKEY", "API_SECRET", "SECRET=", "PRIVATE KEY", "BEGIN RSA", "BEGIN OPENSSH"`
- `git diff --cached | Select-String -Pattern "\b[a-fA-F0-9]{32,128}\b", "\b[A-Za-z0-9_\/\+\-]{40,}\b"`

### 10.2 Safe commit message (no heredoc)
Example:
`git commit -m "feat(gate): add Binance online gate with evidence pack and runner" -m "Body line 1" -m "Body line 2"`
