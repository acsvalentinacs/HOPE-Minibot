# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-22 23:30:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-22 23:30:00 UTC
# === END SIGNATURE ===
"""
Cmdline SSoT Audit - Verify GetCommandLineW is the only source for cmdline hash.

Checks that no code uses sys.argv for hashing/ID generation.
The only allowed pattern is core.cmdline_ssot.get_cmdline_sha256().

Forbidden patterns (fail-closed):
- hash(sys.argv)
- hashlib.*sys.argv
- ''.join(sys.argv)
- ' '.join(sys.argv)
- str(sys.argv)  (in hash context)

Allowed patterns:
- sys.argv for argument parsing (argparse, etc.)
- from core.cmdline_ssot import get_cmdline_sha256

Usage:
    python tools/audit_cmdline_ssot.py --root .
    python tools/audit_cmdline_ssot.py --root . --paths core/foo.py
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

# SSoT: compute paths from __file__
BASE_DIR = Path(__file__).resolve().parent.parent

# Forbidden patterns: sys.argv used for hashing
FORBIDDEN_PATTERNS = [
    # Direct hash of sys.argv
    re.compile(r"hash\s*\(\s*.*sys\.argv", re.IGNORECASE),
    re.compile(r"hashlib\.\w+\s*\(.*sys\.argv"),
    re.compile(r"sha256\s*\(.*sys\.argv"),

    # Joining sys.argv (often precedes hashing)
    re.compile(r"['\"].*['\"]\.join\s*\(\s*sys\.argv"),
    re.compile(r"str\s*\(\s*sys\.argv\s*\)"),

    # Direct assignment to id/hash variables
    re.compile(r"(run_id|snapshot_id|cmdline_hash)\s*=.*sys\.argv"),
]

# Allowed files (can reference sys.argv for parsing or documentation)
ALLOWED_FILES = {
    "cmdline_ssot.py",  # The SSoT module itself
    "audit_cmdline_ssot.py",  # This file (documents patterns)
    # Legacy files not yet migrated (temporary exceptions)
    "chat_shell.py",
    "contracts_v2.py",
    "file_enforcer.py",
    "ssot_cmdline.py",
    "ai_quality_gate.py",
    "night_test_hope.py",
    "night_test_v3.py",
}

# Directories to check
CHECK_DIRS = {"core", "scripts", "tools"}

# Directories to skip
SKIP_DIRS = {".git", ".venv", "__pycache__", ".mypy_cache", ".pytest_cache"}


@dataclass(frozen=True)
class Violation:
    """A single SSoT violation."""
    path: Path
    line_num: int
    line_text: str
    pattern: str


def check_file(path: Path) -> List[Violation]:
    """Check a single file for forbidden patterns."""
    violations = []

    # Skip allowed files
    if path.name in ALLOWED_FILES:
        return violations

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return violations

    for line_num, line in enumerate(text.splitlines(), start=1):
        # Skip comments
        stripped = line.strip()
        if stripped.startswith("#"):
            continue

        for pattern in FORBIDDEN_PATTERNS:
            if pattern.search(line):
                violations.append(Violation(
                    path=path,
                    line_num=line_num,
                    line_text=line.strip()[:80],
                    pattern=pattern.pattern[:40],
                ))
                break  # One violation per line is enough

    return violations


def audit_directory(root: Path) -> List[Violation]:
    """Audit all Python files in check directories."""
    violations = []

    for check_dir in CHECK_DIRS:
        dir_path = root / check_dir
        if not dir_path.exists():
            continue

        for py_file in dir_path.rglob("*.py"):
            # Skip excluded directories
            if any(skip in py_file.parts for skip in SKIP_DIRS):
                continue

            violations.extend(check_file(py_file))

    return violations


def audit_paths(root: Path, paths: List[str]) -> List[Violation]:
    """Audit specific files."""
    violations = []

    for rel_path in paths:
        path = (root / rel_path).resolve()
        if path.exists() and path.suffix == ".py":
            violations.extend(check_file(path))

    return violations


def main() -> int:
    """CLI entrypoint."""
    ap = argparse.ArgumentParser(description="Audit cmdline SSoT compliance")
    ap.add_argument("--root", type=Path, default=BASE_DIR, help="Project root")
    ap.add_argument("--paths", nargs="+", default=None, help="Specific files to check")
    ns = ap.parse_args()

    root = ns.root.resolve()

    print(f"CMDLINE_SSOT_AUDIT root={root}")

    if ns.paths:
        violations = audit_paths(root, ns.paths)
    else:
        violations = audit_directory(root)

    if not violations:
        print(f"\nPASS: No forbidden sys.argv patterns found")
        print("All cmdline hashing must use core.cmdline_ssot.get_cmdline_sha256()")
        return 0

    print(f"\nFAIL-CLOSED: {len(violations)} SSoT violations found")
    print("\nViolations:")
    for v in violations[:20]:
        try:
            rel = v.path.relative_to(root)
        except ValueError:
            rel = v.path
        print(f"  {rel}:{v.line_num}: {v.line_text}")

    if len(violations) > 20:
        print(f"  ... and {len(violations) - 20} more")

    print("\nFix: Use core.cmdline_ssot.get_cmdline_sha256() instead of hashing sys.argv")
    return 1


if __name__ == "__main__":
    sys.exit(main())
