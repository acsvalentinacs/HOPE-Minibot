<!-- === AI SIGNATURE ===
Created by: Claude (opus-4)
Created at (UTC): 2026-01-25T11:30:00Z
Modified by: Claude (opus-4)
Modified at (UTC): 2026-01-25T12:00:00Z
=== END SIGNATURE === -->

# Git Commit Rules v2.1 (PowerShell 5.1, fail-closed, no secrets) — SSoT

## 0) TRIGGER (allowed to commit)
- Commit is executed **ONLY** if owner (Valentin) explicitly says: "коммить".
- If not explicitly requested: **STOP** (no staging, no commit attempts).

## 1) ABSOLUTE PROHIBITIONS (unless owner explicitly orders)
Never run:
- Any push: `git push` / `push --force` (force push to main/master is forbidden).
- Destructive/history ops: `reset --hard`, `restore .`, `checkout .`, `clean -f/-fd`, `branch -D`, `rebase -i`, any interactive mode.
- Hook bypass: `--no-verify`, `--no-gpg-sign`.
- Amend: do **NOT** use `commit --amend` unless owner explicitly orders amend.

## 2) REQUIRED READ-ONLY PREFLIGHT (must show outputs)
```powershell
cd "C:\Users\kirillDev\Desktop\TradingBot\minibot"

git rev-parse --show-toplevel
git rev-parse --abbrev-ref HEAD
git status

git diff --name-only
git diff --stat
git diff --cached --name-only
git diff --cached --stat

git log --oneline -10
```

**STOP if:**
- repo root is not `minibot`,
- or there are no changes at all (diff empty) and commit was requested "by habit".

## 3) MANDATORY SECRET SCAN (fail-closed)

### 3.1 Unstaged scan (early signal; read-only)
```powershell
git diff | Select-String -SimpleMatch -Pattern "signature=", "X-MBX-APIKEY", "API_SECRET", "BINANCE_", "SECRET="
```

### 3.2 Staged scan (decisive; MUST pass)
```powershell
git diff --cached | Select-String -SimpleMatch -Pattern "signature=", "X-MBX-APIKEY", "API_SECRET", "BINANCE_", "SECRET="
```

### 3.3 Heuristic token scan (staged; MUST pass)
```powershell
git diff --cached | Select-String -Pattern "\b[a-fA-F0-9]{32,128}\b", "\b[A-Za-z0-9_\/\+\-]{40,}\b"
```

**STOP RULE:**
- If any scan returns hits that look like secrets/tokens/querystrings: **STOP immediately**.
- Do not paste full suspicious lines into chat/logs; show only file path + approximate context.

## 4) STAGING RULE (no broad adds)
- Stage **ONLY** explicit file paths.
- **Forbidden:** `git add -A` / `git add .` unless owner explicitly orders.

## 5) RECOMMENDED COMMIT PLAN: 2 commits (deterministic rollback)

### Commit A (gate/runner/docs) — stage ONLY:
- `tests\test_binance_online_gate.py`
- `tools\run_binance_gate.ps1`
- `tools\load_binance_env.ps1`
- `CLAUDE_TASK_BINANCE_ONLINE_GATE_FULL.md`
- `CLAUDE_TASK_BINANCE_ONLINE_GATE_10L.txt`

**After staging A:**
- `git diff --cached --name-only`
- re-run staged secret scans (3.2 + 3.3)
- if staged set is unexpected: **STOP**

**Commit A message:**
```
feat(gate): add Binance online gate with evidence pack and runner
```
Body: standalone stdlib-only pytest gate; creates report.json + sha256; PS runner; fail-closed.

### Commit B (policy net allowlist/baseline) — ONLY if owner explicitly approved:
- `AllowList.txt`
- `tools\baseline_locks.json`

**After staging B:**
- `git diff --cached --name-only`
- `git diff --cached` (must show exactly intended policy lines)
- re-run staged secret scans (3.2 + 3.3)

**Commit B message:**
```
chore(net-policy): baseline bump allowlist for Binance hosts (owner approved)
```
Body **MUST include:** `Owner-Approved: yes (Valentin)`

## 6) COMMIT EXECUTION RULES
- If `git commit` fails due to hooks: **STOP**, fix issues, restage, re-run scans, then commit again.
- Do **NOT** use `--amend` unless owner explicitly orders amend.
- Do **NOT** create empty commits: if `git diff --cached --name-only` is empty -> **STOP**.

## 7) POST-COMMIT READ-ONLY VERIFICATION
```powershell
git status
git log -1 --name-only
```
