# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 15:00:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 15:00:00 UTC
# === END SIGNATURE ===
"""
Audit for banned "next-step offer" phrases in assistant outputs.

Fail-closed:
- Any banned phrase found -> exit 1

This enforces the NO NEXT-STEP OFFER rule by scanning for forbidden patterns.

Usage:
    python tools/audit_banned_phrases.py --root .
    python tools/audit_banned_phrases.py --root . --file output.md
    python tools/audit_banned_phrases.py --root . --scan-logs
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterator

# Banned phrase patterns (case-insensitive)
# These indicate "next-step offer" behavior which is forbidden
BANNED_PATTERNS = [
    r"если хотите[,.]?\s*(следующим шагом|я )?могу",
    r"могу сделать это потом",
    r"могу сделать это в следующем",
    r"могу подготовить позже",
    r"давайте в следующем сообщении",
    r"если нужно[,.]?\s*могу",
    r"следующим шагом могу",
    r"в следующем сообщении могу",
    r"дальше можно было бы",
    r"при желании можно",
    r"скажите[,.]?\s*и я добавлю",
    r"нужно ли вам[,.]?\s*чтобы я",
    r"хотите[,.]?\s*чтобы я сделал",
    r"if you want[,.]?\s*i can",
    r"i can do this later",
    r"in the next message[,.]?\s*i",
    r"let me know if you",
    r"would you like me to",
    r"shall i proceed",
    r"should i continue",
]

# Escape pattern for allowed occurrences (in documentation/examples)
ALLOW_ESCAPE = "# allow-banned-phrase"


def compile_patterns() -> list[re.Pattern]:
    """Compile all banned patterns (case-insensitive)."""
    return [re.compile(p, re.IGNORECASE) for p in BANNED_PATTERNS]


def scan_file(file_path: Path, patterns: list[re.Pattern]) -> Iterator[tuple[int, str, str]]:
    """
    Scan file for banned phrases.

    Yields:
        (line_number, matched_text, line_content)
    """
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return

    for i, line in enumerate(lines, start=1):
        # Skip lines with allow escape
        if ALLOW_ESCAPE in line:
            continue

        for pattern in patterns:
            match = pattern.search(line)
            if match:
                yield i, match.group(), line.strip()[:100]


def scan_directory(root: Path, patterns: list[re.Pattern], extensions: set[str]) -> list[dict]:
    """
    Scan directory for banned phrases.

    Returns:
        List of violations: [{file, line, match, context}]
    """
    violations = []

    for ext in extensions:
        for file_path in root.rglob(f"*{ext}"):
            # Skip .venv, __pycache__, etc.
            if any(part.startswith(".") or part == "__pycache__" for part in file_path.parts):
                continue

            for line_num, matched, context in scan_file(file_path, patterns):
                violations.append({
                    "file": str(file_path.relative_to(root)),
                    "line": line_num,
                    "match": matched,
                    "context": context,
                })

    return violations


def main() -> int:
    """CLI entrypoint."""
    ap = argparse.ArgumentParser(description="Audit for banned 'next-step offer' phrases")
    ap.add_argument("--root", type=Path, required=True, help="Project root")
    ap.add_argument("--file", type=str, help="Specific file to scan")
    ap.add_argument("--scan-logs", action="store_true", help="Include .log files")
    ap.add_argument("--extensions", type=str, default=".md,.txt",
                    help="Comma-separated file extensions to scan")
    ns = ap.parse_args()

    root = ns.root.resolve()
    patterns = compile_patterns()

    print(f"BANNED_PHRASES_AUDIT root={root}")
    print(f"  patterns={len(BANNED_PATTERNS)}")

    if ns.file:
        # Scan specific file
        file_path = root / ns.file
        if not file_path.exists():
            print(f"FAIL-CLOSED: file not found: {file_path}", file=sys.stderr)
            return 1

        violations = []
        for line_num, matched, context in scan_file(file_path, patterns):
            violations.append({
                "file": ns.file,
                "line": line_num,
                "match": matched,
                "context": context,
            })
    else:
        # Scan directory
        extensions = set(ns.extensions.split(","))
        if ns.scan_logs:
            extensions.add(".log")
        violations = scan_directory(root, patterns, extensions)

    if violations:
        print(f"\nFAIL-CLOSED: Found {len(violations)} banned phrase(s)", file=sys.stderr)
        for v in violations[:20]:  # Show first 20
            print(f"  {v['file']}:{v['line']}: '{v['match']}'", file=sys.stderr)
            print(f"    context: {v['context']}", file=sys.stderr)
        if len(violations) > 20:
            print(f"  ... and {len(violations) - 20} more", file=sys.stderr)
        return 1

    print(f"\nPASS: No banned phrases found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
