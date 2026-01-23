# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-22 23:40:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 16:00:00 UTC
# === END SIGNATURE ===
"""
Audit that HOPE EXECUTION PROTOCOL is present at the top of CLAUDE.md.

Fail-closed:
- Missing file -> exit 1
- Protocol missing or not first H1 -> exit 1
- Required sentence missing -> exit 1

Usage:
    python tools/audit_execution_protocol.py --root .
    python tools/audit_execution_protocol.py --root . --file CLAUDE.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The required first H1 header
REQUIRED_FIRST_HEADER = "# HOPE EXECUTION PROTOCOL (MANDATORY)"

# Required key sentence (must be present in first 100 lines)
REQUIRED_SENTENCE = "Поставленную задачу надо выполнять в полном объеме"

# Alternative spelling (typo tolerance)
REQUIRED_SENTENCE_ALT = "Поставленную задачу надо выполнять в полном обьеме"

# Required execute-now rule (must be present in first 200 lines)
REQUIRED_EXECUTE_NOW_MARKER = "NEXT-STEP OFFER"
REQUIRED_EXECUTE_NOW_HEADER = '## RULE: NO "NEXT-STEP OFFER" — EXECUTE NOW'


def audit_protocol(text: str) -> tuple[bool, str]:
    """
    Audit that the execution protocol is present and correctly positioned.

    Returns:
        (is_valid, reason)
    """
    # Strip BOM if present
    stripped = text.lstrip("\ufeff")
    lines = stripped.splitlines()
    head = "\n".join(lines[:100])

    # Check for required header
    if REQUIRED_FIRST_HEADER not in head:
        return False, f"missing_required_header:{REQUIRED_FIRST_HEADER}"

    # Check for required sentence (either spelling)
    if REQUIRED_SENTENCE not in head and REQUIRED_SENTENCE_ALT not in head:
        return False, "missing_required_sentence"

    # Ensure first H1 is exactly REQUIRED_FIRST_HEADER
    for line in lines[:200]:
        line_stripped = line.strip()
        if line_stripped.startswith("# "):
            if line_stripped != REQUIRED_FIRST_HEADER:
                return False, f"protocol_header_not_first_h1:found:{line_stripped[:50]}"
            break
    else:
        return False, "no_h1_found_in_first_200_lines"

    # Check for NO NEXT-STEP OFFER rule (must be H2 header in first 200 lines)
    # More robust: look for H2 header containing both "NEXT-STEP OFFER" and "EXECUTE NOW"
    execute_now_found = False
    for line in lines[:200]:
        line_stripped = line.strip()
        if line_stripped.startswith("## ") and "RULE" in line_stripped:
            # Check if this H2 contains the required tokens
            if "NEXT-STEP OFFER" in line_stripped and "EXECUTE NOW" in line_stripped:
                execute_now_found = True
                break

    if not execute_now_found:
        return False, "missing_execute_now_rule_header:expected H2 with NEXT-STEP OFFER and EXECUTE NOW"

    return True, "ok"


def main() -> int:
    """CLI entrypoint."""
    ap = argparse.ArgumentParser(description="Audit HOPE execution protocol in CLAUDE.md")
    ap.add_argument("--root", type=Path, required=True, help="Project root")
    ap.add_argument("--file", type=str, default="CLAUDE.md", help="Target file")
    ns = ap.parse_args()

    # Check both minibot root and parent (TradingBot)
    path = (ns.root.resolve() / ns.file).resolve()

    # If not found in root, try parent directory
    if not path.exists():
        parent_path = (ns.root.resolve().parent / ns.file).resolve()
        if parent_path.exists():
            path = parent_path

    if not path.exists():
        print(f"FAIL-CLOSED: missing {ns.file} at {path}", file=sys.stderr)
        return 1

    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except Exception as e:
        print(f"FAIL-CLOSED: cannot read {path}: {type(e).__name__}:{e}", file=sys.stderr)
        return 1

    is_valid, reason = audit_protocol(text)

    if not is_valid:
        print(f"FAIL-CLOSED: execution protocol check failed: {reason}", file=sys.stderr)
        return 1

    print(f"EXECUTION_PROTOCOL_AUDIT: PASS file={path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
