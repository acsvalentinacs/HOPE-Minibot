# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 16:20:00 UTC
# === END SIGNATURE ===
"""
tools/policy_gate.py - HOPE Policy Gate (Linter).

HOPE-LAW-001 / HOPE-RULE-001:
    Scans repository for:
    - Secret patterns (tokens, keys, credentials)
    - Forbidden promise phrases

EXIT CODES:
    0: PASS (no violations)
    1: FAIL (violations found)
    2: ERROR (policy loading failed)

USAGE:
    python -m tools.policy_gate           # Scan all text files
    python -m tools.policy_gate --verbose # Show scanned files
    python -m tools.policy_gate --fix     # (future: auto-fix)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Text file extensions to scan
_TEXT_EXTENSIONS: Set[str] = {
    ".py", ".md", ".txt", ".json", ".yml", ".yaml",
    ".toml", ".ini", ".cfg", ".conf", ".sh", ".ps1",
    ".bat", ".cmd", ".html", ".css", ".js", ".ts",
}

# Directories to skip
_SKIP_DIRS: Set[str] = {
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    "state", ".claude", ".cursor", ".idea", ".vscode",
    "dist", "build", "eggs", ".eggs",
}

# Files to skip
_SKIP_FILES: Set[str] = {
    "policy.json",  # Policy file itself contains patterns
    "policy_gate.py",  # This file contains patterns
    "output_guard.py",  # Contains pattern definitions
    "HOPE_POLICY.md",  # Documentation contains pattern examples
}


def load_policy_patterns(policy_path: Path) -> Tuple[List[re.Pattern], List[re.Pattern]]:
    """
    Load patterns from policy.json.

    Returns:
        (secret_patterns, forbidden_phrase_patterns)
    """
    import json

    if not policy_path.exists():
        raise FileNotFoundError(f"Policy file not found: {policy_path}")

    raw = json.loads(policy_path.read_text(encoding="utf-8"))

    secret_patterns = [
        re.compile(p, re.IGNORECASE | re.MULTILINE)
        for p in raw.get("secret_patterns", [])
    ]

    forbidden_patterns = [
        re.compile(p, re.IGNORECASE | re.MULTILINE)
        for p in raw.get("forbidden_phrases", [])
    ]

    return secret_patterns, forbidden_patterns


def should_skip_path(path: Path) -> bool:
    """Check if path should be skipped."""
    parts = set(path.parts)

    # Skip directories
    if parts & _SKIP_DIRS:
        return True

    # Skip specific files
    if path.name in _SKIP_FILES:
        return True

    return False


def scan_file(
    path: Path,
    secret_patterns: List[re.Pattern],
    forbidden_patterns: List[re.Pattern],
) -> List[str]:
    """
    Scan a single file for policy violations.

    Returns:
        List of violation messages
    """
    violations: List[str] = []

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return [f"Cannot read {path}: {e}"]

    # Check secrets
    for rx in secret_patterns:
        matches = list(rx.finditer(content))
        if matches:
            for m in matches[:3]:  # Limit to first 3 matches
                line_no = content[:m.start()].count("\n") + 1
                # Don't show the actual match (could be a secret)
                violations.append(
                    f"SECRET PATTERN in {path}:{line_no} - pattern: {rx.pattern[:40]}..."
                )
            if len(matches) > 3:
                violations.append(f"  ... and {len(matches) - 3} more matches")

    # Check forbidden phrases
    for rx in forbidden_patterns:
        matches = list(rx.finditer(content))
        if matches:
            for m in matches[:3]:
                line_no = content[:m.start()].count("\n") + 1
                # Show partial match for debugging (phrases aren't secrets)
                match_text = m.group(0)[:30]
                violations.append(
                    f"FORBIDDEN PHRASE in {path}:{line_no} - '{match_text}...'"
                )
            if len(matches) > 3:
                violations.append(f"  ... and {len(matches) - 3} more matches")

    return violations


def main() -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="HOPE Policy Gate - Scan for policy violations"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show scanned files",
    )
    parser.add_argument(
        "--secrets-only",
        action="store_true",
        help="Only check for secrets (skip forbidden phrases)",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Specific paths to scan (default: repo root)",
    )

    args = parser.parse_args()

    # Determine repo root
    repo_root = Path(__file__).resolve().parents[1]
    policy_path = repo_root / "core" / "policy" / "policy.json"

    print("=== HOPE POLICY GATE ===\n")

    # Load patterns
    try:
        secret_patterns, forbidden_patterns = load_policy_patterns(policy_path)
        print(f"Loaded {len(secret_patterns)} secret patterns")
        print(f"Loaded {len(forbidden_patterns)} forbidden phrase patterns")
    except Exception as e:
        print(f"ERROR: Cannot load policy: {e}", file=sys.stderr)
        return 2

    if args.secrets_only:
        forbidden_patterns = []
        print("(Scanning secrets only)")

    # Determine paths to scan
    if args.paths:
        scan_paths = args.paths
    else:
        scan_paths = [repo_root]

    # Collect files
    files_to_scan: List[Path] = []
    for scan_path in scan_paths:
        if scan_path.is_file():
            files_to_scan.append(scan_path)
        else:
            for p in scan_path.rglob("*"):
                if p.is_file() and p.suffix.lower() in _TEXT_EXTENSIONS:
                    if not should_skip_path(p.relative_to(repo_root)):
                        files_to_scan.append(p)

    print(f"\nScanning {len(files_to_scan)} files...")

    # Scan
    all_violations: List[str] = []
    files_with_violations: Set[Path] = set()

    for path in files_to_scan:
        if args.verbose:
            print(f"  Scanning: {path.relative_to(repo_root)}")

        violations = scan_file(path, secret_patterns, forbidden_patterns)
        if violations:
            all_violations.extend(violations)
            files_with_violations.add(path)

    # Report
    print(f"\n=== RESULTS ===")
    print(f"Files scanned: {len(files_to_scan)}")
    print(f"Files with violations: {len(files_with_violations)}")
    print(f"Total violations: {len(all_violations)}")

    if all_violations:
        print(f"\n--- VIOLATIONS ---")
        for v in all_violations:
            print(f"  {v}")
        print(f"\nRESULT: FAIL")
        return 1

    print(f"\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
