# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 20:00:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 20:00:00 UTC
# === END SIGNATURE ===
"""
Audit: .env policy enforcement (LAW 4 - FAIL-CLOSED).

.env is immutable by assistant (append-only by owner).
Fails if .env appears in git diff as modified/deleted/renamed.

Usage:
    python tools/audit_env_policy.py --root .
    python tools/audit_env_policy.py --root . --staged
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import List

# Matches .env at end of path (with optional leading path components)
ENV_RE = re.compile(r"(^|/)\.env$")


def _run_git(cwd: Path, args: List[str]) -> str:
    """Run git command and return stdout (FAIL-CLOSED on errors)."""
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=True,
        )
        return r.stdout
    except FileNotFoundError:
        print("FAIL-CLOSED: git_not_found_in_PATH", file=sys.stderr)
        raise
    except subprocess.CalledProcessError as e:
        msg = (e.stderr or e.stdout or "").strip()
        print(f"FAIL-CLOSED: git_failed:{msg}", file=sys.stderr)
        raise


def main() -> int:
    """CLI entrypoint."""
    ap = argparse.ArgumentParser(description="Audit: .env policy (LAW 4, fail-closed)")
    ap.add_argument("--root", type=Path, required=True, help="Project root")
    ap.add_argument("--staged", action="store_true", help="Check staged changes instead of working tree")
    ns = ap.parse_args()

    root = ns.root.resolve()
    if not root.exists():
        print(f"FAIL-CLOSED: root_not_found:{root}", file=sys.stderr)
        return 1

    args = ["diff", "--name-status"]
    mode = "working-tree"
    if ns.staged:
        args.append("--cached")
        mode = "staged"

    try:
        out = _run_git(root, args)
    except Exception:
        return 1

    # Find any .env appearances in diff
    hits: List[str] = []
    for line in out.splitlines():
        s = line.strip()
        if not s:
            continue
        # Format: STATUS<tab>PATH or STATUS<tab>OLD<tab>NEW for renames
        parts = s.split("\t")
        # Check all path components (for renames, both old and new paths)
        for p in parts[1:]:
            p = p.replace("\\", "/").strip()
            if ENV_RE.search(p):
                hits.append(s)
                break

    print(f"ENV_POLICY_AUDIT mode={mode} hits={len(hits)}")

    if hits:
        print("\nFAIL-CLOSED: env_mutation_forbidden (LAW 4)")
        print(".env is immutable by assistant. Only owner can append manually.")
        for h in hits[:50]:
            print(f"  VIOLATION: {h}")
        return 1

    print("PASS: .env not present in git diff")
    return 0


if __name__ == "__main__":
    sys.exit(main())
