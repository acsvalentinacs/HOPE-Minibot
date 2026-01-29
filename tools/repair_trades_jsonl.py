# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 02:15:00 UTC
# Purpose: Repair and validate trades.jsonl with sha256 contracts
# Contract: TRADES_JSONL_V1 - all entries must have valid sha256
# === END SIGNATURE ===
"""
TRADES.JSONL REPAIR TOOL

Ensures all entries in trades.jsonl have valid sha256 contracts.
- Adds sha256 to entries missing it
- Moves corrupt/invalid entries to trades_corrupt.jsonl
- Atomic write with fsync

Usage:
    python tools/repair_trades_jsonl.py              # Repair mode
    python tools/repair_trades_jsonl.py --check-only # Audit only (exit 0=PASS, 2=FAIL)
    python tools/repair_trades_jsonl.py --dry-run    # Show what would be done
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root in path
sys.path.insert(0, str(Path(__file__).parent.parent))

TRADES_FILE = Path("state/ai/autotrader/trades.jsonl")
CORRUPT_FILE = Path("state/ai/autotrader/trades_corrupt.jsonl")
BACKUP_DIR = Path("state/ai/autotrader/backups")


def canonical_json(obj: dict) -> bytes:
    """
    Canonical JSON for sha256 computation.
    Removes 'sha256' field, sorts keys, no spaces.
    """
    obj_copy = dict(obj)
    obj_copy.pop("sha256", None)
    return json.dumps(
        obj_copy,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":")
    ).encode("utf-8")


def compute_sha256(obj: dict) -> str:
    """Compute sha256 hash for an object."""
    canonical = canonical_json(obj)
    h = hashlib.sha256(canonical).hexdigest()[:16]
    return f"sha256:{h}"


def validate_sha256(obj: dict) -> tuple[bool, str]:
    """
    Validate sha256 field.
    Returns (is_valid, expected_sha).
    """
    sha = obj.get("sha256")
    expected = compute_sha256(obj)

    if not sha:
        return False, expected
    if not str(sha).startswith("sha256:"):
        return False, expected
    if sha != expected:
        return False, expected

    return True, expected


def atomic_write(path: Path, content: str) -> None:
    """Atomic write with fsync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")

    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp, path)


def audit_file(trades_file: Path) -> dict:
    """Audit trades.jsonl and return statistics."""
    stats = {
        "total": 0,
        "valid": 0,
        "no_sha": 0,
        "bad_sha": 0,
        "bad_json": 0,
        "entries": []  # (line_num, obj, status, expected_sha)
    }

    if not trades_file.exists():
        return stats

    lines = trades_file.read_text(encoding="utf-8").splitlines()

    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue

        stats["total"] += 1

        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            stats["bad_json"] += 1
            stats["entries"].append((i, line, "BAD_JSON", str(e)))
            continue

        sha = obj.get("sha256")
        is_valid, expected = validate_sha256(obj)

        if not sha or not str(sha).startswith("sha256:"):
            stats["no_sha"] += 1
            stats["entries"].append((i, obj, "NO_SHA", expected))
        elif not is_valid:
            stats["bad_sha"] += 1
            stats["entries"].append((i, obj, "BAD_SHA", expected))
        else:
            stats["valid"] += 1
            stats["entries"].append((i, obj, "VALID", expected))

    return stats


def repair_file(trades_file: Path, dry_run: bool = False) -> dict:
    """
    Repair trades.jsonl:
    - Add sha256 to entries without it
    - Move corrupt entries to trades_corrupt.jsonl
    - Write repaired file atomically
    """
    stats = audit_file(trades_file)

    if stats["total"] == 0:
        print("No entries to repair")
        return stats

    repaired_lines = []
    corrupt_lines = []

    for line_num, entry, status, expected_sha in stats["entries"]:
        if status == "BAD_JSON":
            # Move raw line to corrupt file
            corrupt_lines.append(json.dumps({
                "original_line": entry,
                "error": expected_sha,
                "line_num": line_num,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }))
            print(f"  Line {line_num}: BAD_JSON -> corrupt")

        elif status == "BAD_SHA":
            # Corrupt sha256 - move to corrupt file (data integrity issue)
            corrupt_lines.append(json.dumps({
                "original": entry,
                "expected_sha": expected_sha,
                "actual_sha": entry.get("sha256"),
                "line_num": line_num,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }, ensure_ascii=False))
            print(f"  Line {line_num}: BAD_SHA -> corrupt (was: {entry.get('sha256')}, expected: {expected_sha})")

        elif status == "NO_SHA":
            # Add sha256
            entry["sha256"] = expected_sha
            repaired_lines.append(json.dumps(entry, ensure_ascii=False))
            print(f"  Line {line_num}: NO_SHA -> added {expected_sha}")

        else:  # VALID
            repaired_lines.append(json.dumps(entry, ensure_ascii=False))

    if dry_run:
        print(f"\n[DRY-RUN] Would write {len(repaired_lines)} repaired entries")
        print(f"[DRY-RUN] Would move {len(corrupt_lines)} corrupt entries")
        return stats

    # Create backup
    if trades_file.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup_name = f"trades_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        backup_path = BACKUP_DIR / backup_name
        atomic_write(backup_path, trades_file.read_text(encoding="utf-8"))
        print(f"Backup created: {backup_path}")

    # Write repaired file
    if repaired_lines:
        content = "\n".join(repaired_lines) + "\n"
        atomic_write(trades_file, content)
        print(f"Repaired file written: {len(repaired_lines)} entries")

    # Append corrupt entries
    if corrupt_lines:
        CORRUPT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CORRUPT_FILE, "a", encoding="utf-8") as f:
            for line in corrupt_lines:
                f.write(line + "\n")
        print(f"Corrupt entries appended to: {CORRUPT_FILE}")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Repair trades.jsonl sha256 contracts")
    parser.add_argument("--check-only", action="store_true", help="Audit only, no changes")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    parser.add_argument("--file", type=Path, default=TRADES_FILE, help="Path to trades.jsonl")
    args = parser.parse_args()

    print("=" * 50)
    print("TRADES.JSONL INTEGRITY CHECK")
    print("=" * 50)
    print(f"File: {args.file}")
    print()

    stats = audit_file(args.file)

    print(f"Total entries:  {stats['total']}")
    print(f"Valid (sha OK): {stats['valid']}")
    print(f"No sha256:      {stats['no_sha']}")
    print(f"Bad sha256:     {stats['bad_sha']}")
    print(f"Bad JSON:       {stats['bad_json']}")
    print()

    is_clean = (stats["no_sha"] == 0 and stats["bad_sha"] == 0 and stats["bad_json"] == 0)

    if args.check_only:
        if is_clean:
            print("RESULT: PASS (all entries have valid sha256)")
            sys.exit(0)
        else:
            print("RESULT: FAIL (integrity issues found)")
            sys.exit(2)

    if is_clean:
        print("No repairs needed - all entries valid")
        sys.exit(0)

    print("=" * 50)
    print("REPAIRING...")
    print("=" * 50)

    repair_file(args.file, dry_run=args.dry_run)

    # Verify after repair
    if not args.dry_run:
        print()
        print("=" * 50)
        print("VERIFICATION AFTER REPAIR")
        print("=" * 50)

        verify_stats = audit_file(args.file)
        is_clean_now = (
            verify_stats["no_sha"] == 0 and
            verify_stats["bad_sha"] == 0 and
            verify_stats["bad_json"] == 0
        )

        print(f"Valid entries: {verify_stats['valid']}/{verify_stats['total']}")

        if is_clean_now:
            print("RESULT: PASS")
            sys.exit(0)
        else:
            print("RESULT: FAIL (some issues remain)")
            sys.exit(2)


if __name__ == "__main__":
    main()
