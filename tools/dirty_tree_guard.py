# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T18:00:00Z
# Purpose: Dirty Tree Guard - fail-closed check for untracked/modified files
# === END SIGNATURE ===
"""
Dirty Tree Guard - Fail-Closed Git Working Tree Validation.

Ensures git working tree is clean before release operations.
ANY untracked or modified file = FAIL (no exceptions by default).

Usage:
    python tools/dirty_tree_guard.py
    python tools/dirty_tree_guard.py --allow-state  # Allow state/** untracked

Exit codes:
    0 = CLEAN (no untracked, no modified)
    1 = DIRTY (has untracked or modified files)
    2 = ERROR (git command failed)
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# SSoT paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def get_git_status() -> tuple[int, list[str]]:
    """
    Get git status --porcelain output.

    Returns:
        (exit_code, list of status lines)
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        lines = [line for line in result.stdout.strip().split("\n") if line]
        return result.returncode, lines
    except subprocess.TimeoutExpired:
        return 2, ["ERROR: git status timed out"]
    except Exception as e:
        return 2, [f"ERROR: {e}"]


def parse_status_line(line: str) -> tuple[str, str]:
    """
    Parse git status --porcelain line.

    Format: XY PATH or XY ORIG -> PATH (for renames)

    Returns:
        (status_code, file_path)
    """
    if len(line) < 3:
        return "??", line

    status = line[:2]
    path = line[3:].strip()

    # Handle renames: "R  old -> new"
    if " -> " in path:
        path = path.split(" -> ")[-1]

    return status, path


def check_dirty_tree(
    allow_state: bool = False,
    allow_patterns: list[str] | None = None,
) -> tuple[bool, list[str]]:
    """
    Check if git working tree is dirty.

    Args:
        allow_state: If True, allow untracked files in state/**
        allow_patterns: Additional regex patterns to allow (untracked only)

    Returns:
        (is_clean, list of violation descriptions)
    """
    exit_code, lines = get_git_status()

    if exit_code == 2:
        return False, lines  # Git error

    if not lines:
        return True, []  # Clean

    # Build allow patterns
    patterns = []
    if allow_state:
        patterns.append(r"^state/")
    if allow_patterns:
        patterns.extend(allow_patterns)

    violations = []

    for line in lines:
        status, path = parse_status_line(line)

        # Check if path matches any allow pattern (only for untracked)
        if status == "??" and patterns:
            allowed = False
            for pattern in patterns:
                if re.match(pattern, path):
                    allowed = True
                    break
            if allowed:
                continue

        # Modified/staged files are NEVER allowed
        if status != "??":
            violations.append(f"MODIFIED: [{status}] {path}")
        else:
            violations.append(f"UNTRACKED: {path}")

    return len(violations) == 0, violations


def main() -> int:
    """Main entrypoint."""
    parser = argparse.ArgumentParser(
        description="Dirty Tree Guard - fail-closed git working tree check",
    )
    parser.add_argument(
        "--allow-state",
        action="store_true",
        help="Allow untracked files in state/** (runtime artifacts)",
    )
    parser.add_argument(
        "--allow-pattern",
        action="append",
        dest="allow_patterns",
        help="Regex pattern for allowed untracked paths (can repeat)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    args = parser.parse_args()

    print("=== DIRTY TREE GUARD ===")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print(f"Root: {PROJECT_ROOT}")
    print()

    is_clean, violations = check_dirty_tree(
        allow_state=args.allow_state,
        allow_patterns=args.allow_patterns,
    )

    if args.json:
        import json
        output = {
            "passed": is_clean,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "violations": violations,
            "allow_state": args.allow_state,
        }
        print(json.dumps(output, indent=2))
    else:
        if is_clean:
            print("Status: CLEAN")
            print("Result: PASS")
        else:
            print("Status: DIRTY")
            print(f"Violations ({len(violations)}):")
            for v in violations:
                print(f"  - {v}")
            print()
            print("Result: FAIL")
            print()
            print("Resolution:")
            print("  1. Commit or stash modified files")
            print("  2. Add untracked files to .gitignore or delete them")
            print("  3. Use --allow-state for runtime state/** artifacts")

    return 0 if is_clean else 1


if __name__ == "__main__":
    sys.exit(main())
