# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-22 23:40:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-22 23:40:00 UTC
# === END SIGNATURE ===
"""
Fail-closed audit for AllowList.txt.

Rules:
- Hostnames only (one per line).
- Comments allowed with '#'.
- Forbidden: '*', schemes (http/https), paths '/', whitespace in hostname, uppercase.

Usage:
    python tools/audit_allowlist.py --root .
    python tools/audit_allowlist.py --root . --file AllowList.txt
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Valid hostname regex (lowercase, no scheme, no path)
HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)


def audit_allowlist(path: Path) -> tuple[bool, list[str], int]:
    """
    Audit allowlist file for strict host-only format.

    Returns:
        (is_valid, errors, entry_count)
    """
    if not path.exists():
        return False, [f"allowlist_not_found:{path}"], 0

    try:
        # UTF-8 with BOM support
        lines = path.read_text(encoding="utf-8-sig", errors="strict").splitlines()
    except Exception as e:
        return False, [f"allowlist_read_error:{type(e).__name__}:{e}"], 0

    errors: list[str] = []
    entries = 0

    for i, raw in enumerate(lines, start=1):
        s = raw.strip()

        # Skip empty lines and comments
        if not s or s.startswith("#"):
            continue

        entries += 1

        # Check for forbidden patterns
        if s == "*":
            errors.append(f"{path.name}:{i}: wildcard_forbidden")
            continue

        if "://" in s:
            errors.append(f"{path.name}:{i}: scheme_forbidden:{s}")
            continue

        if "/" in s:
            errors.append(f"{path.name}:{i}: path_forbidden:{s}")
            continue

        if any(ch.isspace() for ch in s):
            errors.append(f"{path.name}:{i}: whitespace_forbidden:{s}")
            continue

        if s.lower() != s:
            errors.append(f"{path.name}:{i}: must_be_lowercase:{s}")
            continue

        if not HOST_RE.match(s):
            errors.append(f"{path.name}:{i}: invalid_hostname:{s}")
            continue

    # Empty allowlist is forbidden (fail-closed means we need explicit allow)
    if entries == 0:
        errors.append(f"{path.name}: no_entries (empty allowlist is forbidden)")

    return len(errors) == 0, errors, entries


def main() -> int:
    """CLI entrypoint."""
    ap = argparse.ArgumentParser(description="Audit HTTP allowlist (fail-closed)")
    ap.add_argument("--root", type=Path, required=True, help="Project root")
    ap.add_argument("--file", type=str, default="AllowList.txt", help="Allowlist filename")
    ns = ap.parse_args()

    path = (ns.root.resolve() / ns.file).resolve()

    is_valid, errors, entries = audit_allowlist(path)

    if not is_valid:
        print("FAIL-CLOSED: AllowList invalid", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 1

    print(f"ALLOWLIST_AUDIT: PASS entries={entries} file={path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
