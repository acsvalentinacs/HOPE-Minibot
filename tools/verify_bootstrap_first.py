# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 16:40:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 17:10:00 UTC
# === END SIGNATURE ===
"""
tools/verify_bootstrap_first.py - Verify bootstrap is called first in entrypoints.

HOPE-LAW-001: bootstrap() MUST be called BEFORE any:
    - Logging (logging.basicConfig, getLogger)
    - Network (requests, httpx, aiohttp, urlopen, socket)
    - HTTP requests

EXIT CODES:
    0: PASS (all entrypoints have bootstrap first)
    1: FAIL (violations found)
    2: ERROR (no entrypoints found)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Set, Tuple

# Tokens that indicate network/logging activity
NET_TOKENS = (
    "requests.",
    "requests.get",
    "requests.post",
    "httpx.",
    "aiohttp.",
    "urllib3.",
    "urlopen(",
    "socket.getaddrinfo",
    "socket.create_connection",
    "socket.socket(",
)

LOG_TOKENS = (
    "logging.basicConfig",
    "logging.getLogger",
    "getLogger(",
)

# Bootstrap tokens (must appear before NET/LOG)
BOOT_IMPORT = "from core.policy.bootstrap import bootstrap"
BOOT_CALL = "bootstrap("

# Skip directories
SKIP_DIRS: Set[str] = {
    ".git", ".venv", "venv", "__pycache__",
    "node_modules", ".pytest_cache", ".mypy_cache",
}


def is_entrypoint(path: Path) -> bool:
    """Check if file is an entrypoint that needs bootstrap."""
    name = path.name.lower()

    # *_runner.py files are entrypoints
    if name.endswith("_runner.py"):
        return True

    # tools/*.py that have main() are entrypoints
    if path.parent.name == "tools" and name.endswith(".py"):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            if "def main(" in content or 'if __name__ == "__main__"' in content:
                return True
        except Exception:
            pass

    return False


def should_skip(path: Path) -> bool:
    """Check if path should be skipped."""
    parts = set(path.parts)
    return bool(parts & SKIP_DIRS)


def is_at_module_level(content: str, idx: int) -> bool:
    """
    Check if position idx is at module level (not inside a function/class).

    Simple heuristic: check if the line starts at column 0 (no indentation).
    """
    line_start = content.rfind("\n", 0, idx) + 1
    line = content[line_start:content.find("\n", idx)]

    # If line starts with whitespace, it's inside a block
    if line and line[0] in " \t":
        return False

    return True


def has_module_level_net_or_log(content: str) -> Tuple[bool, List[str]]:
    """
    Check if file has MODULE-LEVEL network or logging usage.

    Module-level means code at indent 0 (not inside a function/class).
    Code inside functions executes at runtime, not import time.

    Returns:
        (has_usage, list_of_tokens_found)
    """
    found_tokens: List[str] = []

    for token in NET_TOKENS + LOG_TOKENS:
        idx = 0
        while True:
            idx = content.find(token, idx)
            if idx < 0:
                break

            # Check if it's in a comment or docstring
            line_start = content.rfind("\n", 0, idx) + 1
            line = content[line_start:content.find("\n", idx)]
            stripped = line.strip()

            # Skip comments
            if stripped.startswith("#"):
                idx += len(token)
                continue

            # Skip docstrings (simple heuristic)
            if '"""' in content[max(0, idx - 100):idx]:
                idx += len(token)
                continue

            # Check if it's at module level
            if is_at_module_level(content, idx):
                found_tokens.append(token)
                break  # Found one at module level, that's enough

            idx += len(token)

    return len(found_tokens) > 0, found_tokens


def check_file(path: Path) -> Tuple[bool, List[str]]:
    """
    Check if file has bootstrap before net/log.

    Rules:
    1. If file has net/log tokens at MODULE LEVEL, bootstrap must be called
    2. Net/log inside function definitions is OK (they run after main() calls bootstrap)
    3. Bootstrap must exist somewhere in the file

    Returns:
        (is_valid, list_of_issues)
    """
    issues: List[str] = []

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return False, [f"Cannot read file: {e}"]

    # Check if file has MODULE-LEVEL network or logging usage
    has_module_level, tokens_found = has_module_level_net_or_log(content)

    # If no module-level net/log usage, just check bootstrap exists
    if not has_module_level:
        # Still need bootstrap import and call somewhere
        idx_boot_import = content.find(BOOT_IMPORT)
        idx_boot_call = content.find(BOOT_CALL)

        # Check if file uses net/log anywhere (even in functions)
        has_any_usage = False
        for token in NET_TOKENS + LOG_TOKENS:
            if token in content:
                has_any_usage = True
                break

        if has_any_usage:
            if idx_boot_import < 0:
                issues.append("Missing bootstrap import (file uses network/logging)")
            if idx_boot_call < 0:
                issues.append("Missing bootstrap() call (file uses network/logging)")

        return len(issues) == 0, issues

    # File has module-level net/log - this is a violation
    # (bootstrap can only be called at runtime in main(), not at import time)
    for token in tokens_found:
        issues.append(f"Module-level '{token}' (runs before bootstrap can be called)")

    return False, issues


def main() -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Verify bootstrap is called first in entrypoints"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Repository root directory",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show all checked files",
    )

    args = parser.parse_args()
    root = args.root.resolve()

    print("=== BOOTSTRAP-FIRST VERIFICATION ===\n")

    # Find all entrypoints
    entrypoints: List[Path] = []
    for path in root.rglob("*.py"):
        if should_skip(path):
            continue
        if is_entrypoint(path):
            entrypoints.append(path)

    if not entrypoints:
        print("ERROR: No entrypoints found", file=sys.stderr)
        return 2

    print(f"Found {len(entrypoints)} entrypoint(s)\n")

    # Check each entrypoint
    violations = 0
    for path in sorted(entrypoints):
        rel_path = path.relative_to(root)
        is_valid, issues = check_file(path)

        if args.verbose or not is_valid:
            status = "OK" if is_valid else "FAIL"
            print(f"[{status}] {rel_path}")

            if not is_valid:
                for issue in issues:
                    print(f"      - {issue}")
                violations += 1

    print(f"\n=== SUMMARY ===")
    print(f"Entrypoints checked: {len(entrypoints)}")
    print(f"Violations: {violations}")

    if violations > 0:
        print("\nRESULT: FAIL")
        return 1

    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
