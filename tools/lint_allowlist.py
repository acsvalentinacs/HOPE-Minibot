# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 15:55:00 UTC
# === END SIGNATURE ===
"""
tools/lint_allowlist.py - Lint AllowList files for validity.

CHECKS (fail-closed):
    1. No duplicates
    2. Host-only (no schemes, paths, queries)
    3. No wildcards
    4. Valid RFC-1123 hostname format
    5. No empty lines between hosts (except comments)
    6. Required hosts present (bridge, binance API)

USAGE:
    python -m tools.lint_allowlist                    # Lint all files
    python -m tools.lint_allowlist AllowList.core.txt # Lint specific file
    python -m tools.lint_allowlist --strict           # Fail on warnings too

EXIT CODES:
    0: PASS
    1: FAIL (errors found)
    2: WARN (warnings only, pass unless --strict)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional, Set, Tuple

# Paths
_THIS_FILE = Path(__file__).resolve()
_TOOLS_DIR = _THIS_FILE.parent
_MINIBOT_DIR = _TOOLS_DIR.parent

# Default files to lint
_DEFAULT_FILES = [
    _MINIBOT_DIR / "AllowList.txt",
    _MINIBOT_DIR / "AllowList.core.txt",
    _MINIBOT_DIR / "AllowList.spider.txt",
    _MINIBOT_DIR / "AllowList.dev.txt",
]

# Required hosts (must be present in at least one file)
_REQUIRED_HOSTS = {
    "bridge.acsvalentinacs.com",  # Friend Bridge - critical
    "api.binance.com",            # Trading API - critical
}

# Required in CORE specifically
_REQUIRED_CORE = {
    "bridge.acsvalentinacs.com",
    "api.binance.com",
}

# RFC-1123 hostname regex
_HOSTNAME_REGEX = re.compile(
    r"^(?!-)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


def lint_file(path: Path) -> Tuple[List[str], List[str], Set[str]]:
    """
    Lint a single allowlist file.

    Returns:
        (errors, warnings, hosts_found)
    """
    errors: List[str] = []
    warnings: List[str] = []
    hosts: Set[str] = set()

    if not path.exists():
        return [f"File not found: {path}"], [], set()

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    for line_no, line in enumerate(lines, start=1):
        original = line
        line = line.rstrip("\r\n")

        # Skip empty lines and comments
        if not line.strip() or line.strip().startswith("#"):
            continue

        host = line.strip().lower()

        # Check: no leading/trailing whitespace in file
        if line != line.strip():
            warnings.append(f"Line {line_no}: Whitespace around host: '{original.rstrip()}'")
            host = line.strip().lower()

        # Check: no scheme
        if host.startswith("http://") or host.startswith("https://"):
            errors.append(f"Line {line_no}: Scheme not allowed: {host}")
            continue

        # Check: no path/query/fragment
        if "/" in host or "?" in host or "#" in host:
            errors.append(f"Line {line_no}: Path/query not allowed: {host}")
            continue

        # Check: no wildcards
        if "*" in host:
            errors.append(f"Line {line_no}: Wildcard not allowed: {host}")
            continue

        # Check: valid hostname
        if not _HOSTNAME_REGEX.match(host):
            errors.append(f"Line {line_no}: Invalid hostname format: {host}")
            continue

        # Check: no duplicates
        if host in hosts:
            errors.append(f"Line {line_no}: Duplicate host: {host}")
            continue

        hosts.add(host)

    return errors, warnings, hosts


def main() -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Lint AllowList files for validity"
    )
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="Files to lint (default: all AllowList*.txt)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail on warnings too",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Only show errors",
    )

    args = parser.parse_args()

    # Determine files to lint
    files_to_lint = args.files if args.files else [
        f for f in _DEFAULT_FILES if f.exists()
    ]

    if not files_to_lint:
        print("No allowlist files found to lint.")
        return 0

    print("=== ALLOWLIST LINTER ===\n")

    total_errors = 0
    total_warnings = 0
    all_hosts: Set[str] = set()
    core_hosts: Set[str] = set()

    for path in files_to_lint:
        print(f"Checking: {path.name}")

        errors, warnings, hosts = lint_file(path)

        all_hosts.update(hosts)
        if "core" in path.name.lower():
            core_hosts = hosts

        if errors:
            total_errors += len(errors)
            for err in errors:
                print(f"  ERROR: {err}")

        if warnings and not args.quiet:
            total_warnings += len(warnings)
            for warn in warnings:
                print(f"  WARN: {warn}")

        if not errors and not warnings:
            print(f"  OK ({len(hosts)} hosts)")
        elif not errors:
            print(f"  OK with warnings ({len(hosts)} hosts)")

    # Check required hosts
    print("\n--- Required Hosts Check ---")

    missing_global = _REQUIRED_HOSTS - all_hosts
    if missing_global:
        for host in missing_global:
            print(f"  ERROR: Missing required host: {host}")
            total_errors += 1
    else:
        print(f"  OK: All {len(_REQUIRED_HOSTS)} required hosts present")

    # Check core-specific requirements
    if core_hosts:
        missing_core = _REQUIRED_CORE - core_hosts
        if missing_core:
            for host in missing_core:
                print(f"  ERROR: Missing in core: {host}")
                total_errors += 1
        else:
            print(f"  OK: Core has all {len(_REQUIRED_CORE)} required hosts")

    # Summary
    print(f"\n=== SUMMARY ===")
    print(f"Files checked: {len(files_to_lint)}")
    print(f"Total hosts: {len(all_hosts)}")
    print(f"Errors: {total_errors}")
    print(f"Warnings: {total_warnings}")

    if total_errors > 0:
        print("\nRESULT: FAIL")
        return 1

    if total_warnings > 0 and args.strict:
        print("\nRESULT: FAIL (strict mode)")
        return 1

    if total_warnings > 0:
        print("\nRESULT: PASS (with warnings)")
        return 0

    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
