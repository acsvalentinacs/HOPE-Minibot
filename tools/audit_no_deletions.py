# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 18:00:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 18:00:00 UTC
# === END SIGNATURE ===
"""
Audit that no files are deleted or renamed in git diff (LAW 3: NO DELETIONS).

Fail-closed:
- Git error -> exit 1
- Any D (deleted) status -> exit 1
- Any R (renamed) status -> exit 1

Usage:
    python tools/audit_no_deletions.py --root .
    python tools/audit_no_deletions.py --root . --staged
    python tools/audit_no_deletions.py --root . --allow .gitignore --allow tmp/
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple


def get_git_diff_status(root: Path, staged: bool = False) -> Tuple[bool, List[Tuple[str, str]]]:
    """
    Get file status from git diff (FAIL-CLOSED on errors).

    Returns:
        (success, list of (status, path) tuples)
        status codes: A=added, M=modified, D=deleted, R=renamed, C=copied, etc.
    """
    try:
        # First verify we're in a git repo
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=True,
        )
        if result.stdout.strip().lower() != "true":
            print("FAIL-CLOSED: not inside git work tree", file=sys.stderr)
            return False, []
    except FileNotFoundError:
        print("FAIL-CLOSED: git not found in PATH", file=sys.stderr)
        return False, []
    except subprocess.CalledProcessError as e:
        msg = (e.stderr or e.stdout or "").strip()
        print(f"FAIL-CLOSED: git rev-parse failed: {msg}", file=sys.stderr)
        return False, []

    try:
        cmd = ["git", "diff", "--name-status"]
        if staged:
            cmd.append("--cached")

        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            check=True,
        )

        items = []
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            # Format: STATUS<tab>PATH (or STATUS<tab>OLD<tab>NEW for renames)
            parts = line.split("\t")
            if len(parts) >= 2:
                status = parts[0]
                path = parts[1]
                # Normalize path
                path = path.replace("\\", "/")
                items.append((status, path))

        return True, items

    except subprocess.CalledProcessError as e:
        msg = (e.stderr or e.stdout or "").strip()
        print(f"FAIL-CLOSED: git diff failed: {msg}", file=sys.stderr)
        return False, []


def is_path_allowed(path: str, allow_patterns: List[str]) -> bool:
    """Check if path matches any allow pattern."""
    for pattern in allow_patterns:
        # Simple prefix/exact match
        if path == pattern or path.startswith(pattern.rstrip("/") + "/"):
            return True
        # Check if pattern is a suffix (e.g., ".gitignore" matches "foo/.gitignore")
        if pattern.startswith(".") and path.endswith(pattern):
            return True
    return False


def audit_no_deletions(
    root: Path,
    staged: bool = False,
    allow_patterns: Optional[List[str]] = None
) -> Tuple[int, List[Tuple[str, str]]]:
    """
    Audit that no files are deleted/renamed.

    Returns:
        (exit_code, list of violations as (status, path))
    """
    allow_patterns = allow_patterns or []

    success, items = get_git_diff_status(root, staged=staged)
    if not success:
        return 1, []

    violations = []
    for status, path in items:
        # Check for deletion (D) or rename (R*)
        if status.startswith("D") or status.startswith("R"):
            if not is_path_allowed(path, allow_patterns):
                violations.append((status, path))

    return (1 if violations else 0), violations


def main() -> int:
    """CLI entrypoint."""
    ap = argparse.ArgumentParser(description="Audit no deletions/renames in git diff (LAW 3)")
    ap.add_argument("--root", type=Path, required=True, help="Project root")
    ap.add_argument("--staged", action="store_true", help="Check staged changes instead of working tree")
    ap.add_argument("--allow", action="append", default=[], dest="allow_patterns",
                    help="Allow deletion/rename of specific paths (can repeat)")
    ns = ap.parse_args()

    root = ns.root.resolve()
    if not root.exists():
        print(f"FAIL-CLOSED: root not found: {root}", file=sys.stderr)
        return 1

    mode = "staged" if ns.staged else "working-tree"
    print(f"NO_DELETIONS_AUDIT mode={mode} root={root}")

    exit_code, violations = audit_no_deletions(root, staged=ns.staged, allow_patterns=ns.allow_patterns)

    if violations:
        print(f"\nFAIL-CLOSED: {len(violations)} deletion/rename violations found:")
        for status, path in violations:
            status_name = "DELETED" if status.startswith("D") else "RENAMED"
            print(f"  {status_name}: {path}")
        print("\nLAW 3: NO DELETIONS / NO RENAMES")
        print("If deletion is required, it must be explicitly approved in SSoT scope.")
        return 1

    print(f"PASS: No deletions or renames detected (mode={mode})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
