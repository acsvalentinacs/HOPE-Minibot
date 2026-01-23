#!/usr/bin/env python3
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-20 12:00:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 14:00:00 UTC
# === END SIGNATURE ===
"""
IPC ACK Drain Tool

Drains stale pending_acks from IPC cursor files.
These are messages that were sent but never acknowledged, causing
"Resent unacked" log spam in IPC agent.

Usage:
    # Dry run (default) - show what would be drained
    python -m tools.ipc_ack_drain

    # Actually drain stale entries
    python -m tools.ipc_ack_drain --execute

    # Drain entries older than 1 hour (default: 5 minutes)
    python -m tools.ipc_ack_drain --max-age 3600 --execute

    # Drain specific cursor file
    python -m tools.ipc_ack_drain --cursor state/ipc_cursor_gpt-5.2.json --execute

Exit codes:
    0: Success (or dry run completed)
    1: No stale entries found
    2: Error
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

# Constants
_MINIBOT_DIR = Path(__file__).resolve().parent.parent
_STATE_DIR = _MINIBOT_DIR / "state"

# Default cursor files to check
DEFAULT_CURSORS = [
    _STATE_DIR / "ipc_cursor_gpt-5.2.json",
    _STATE_DIR / "ipc_cursor_claude.json",
]

# Default max age for stale entries (5 minutes)
DEFAULT_MAX_AGE_SECONDS = 300


def _atomic_write(path: Path, content: str) -> None:
    """Atomic write: temp -> fsync -> replace."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def find_stale_pending_acks(
    cursor_file: Path, max_age_seconds: float
) -> List[Tuple[str, float, float]]:
    """
    Find pending_acks older than max_age_seconds.

    Returns:
        List of tuples: (message_id, timestamp, age_seconds)
    """
    if not cursor_file.exists():
        return []

    try:
        data = json.loads(cursor_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    pending_acks = data.get("pending_acks", {})
    if not pending_acks:
        return []

    now = time.time()
    stale = []

    for msg_id, ts in pending_acks.items():
        age = now - ts
        if age > max_age_seconds:
            stale.append((msg_id, ts, age))

    # Sort by age (oldest first)
    stale.sort(key=lambda x: x[2], reverse=True)
    return stale


def drain_pending_acks(
    cursor_file: Path, message_ids: List[str]
) -> Tuple[int, str]:
    """
    Remove specified message IDs from pending_acks.

    Returns:
        Tuple of (count_removed, error_message_or_empty)
    """
    if not cursor_file.exists():
        return 0, f"File not found: {cursor_file}"

    try:
        data = json.loads(cursor_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return 0, f"Failed to read: {e}"

    pending_acks = data.get("pending_acks", {})
    original_count = len(pending_acks)

    for msg_id in message_ids:
        pending_acks.pop(msg_id, None)

    removed = original_count - len(pending_acks)

    if removed > 0:
        data["pending_acks"] = pending_acks
        data["last_drain"] = time.time()
        data["last_drain_count"] = removed
        try:
            _atomic_write(cursor_file, json.dumps(data, indent=2, ensure_ascii=False))
        except OSError as e:
            return 0, f"Failed to write: {e}"

    return removed, ""


def format_age(seconds: float) -> str:
    """Format age in human-readable format."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds / 60:.1f}m"
    elif seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    else:
        return f"{seconds / 86400:.1f}d"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Drain stale pending_acks from IPC cursor files"
    )
    parser.add_argument(
        "--cursor",
        type=Path,
        help="Specific cursor file to drain (default: check all known cursors)",
    )
    parser.add_argument(
        "--max-age",
        type=int,
        default=DEFAULT_MAX_AGE_SECONDS,
        help=f"Max age in seconds before entry is considered stale (default: {DEFAULT_MAX_AGE_SECONDS})",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually drain entries (default: dry run)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Drain ALL pending_acks regardless of age",
    )
    args = parser.parse_args()

    cursors = [args.cursor] if args.cursor else DEFAULT_CURSORS
    max_age = 0 if args.all else args.max_age

    print("=" * 60)
    print("IPC ACK Drain Tool")
    print("=" * 60)
    print(f"Mode: {'EXECUTE' if args.execute else 'DRY RUN'}")
    print(f"Max age: {'ALL' if args.all else f'{max_age}s ({format_age(max_age)})'}")
    print()

    total_stale = 0
    total_drained = 0

    for cursor_file in cursors:
        if not cursor_file.exists():
            print(f"[SKIP] {cursor_file.name} - not found")
            continue

        stale = find_stale_pending_acks(cursor_file, max_age)
        if not stale:
            print(f"[OK] {cursor_file.name} - no stale entries")
            continue

        total_stale += len(stale)
        print(f"\n[FOUND] {cursor_file.name} - {len(stale)} stale entries:")

        for msg_id, ts, age in stale[:10]:  # Show first 10
            print(f"  {msg_id[:50]}... (age: {format_age(age)})")
        if len(stale) > 10:
            print(f"  ... and {len(stale) - 10} more")

        if args.execute:
            ids_to_drain = [s[0] for s in stale]
            removed, error = drain_pending_acks(cursor_file, ids_to_drain)
            if error:
                print(f"  [ERROR] {error}")
            else:
                print(f"  [DRAINED] {removed} entries removed")
                total_drained += removed

    print()
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"Stale entries found: {total_stale}")
    if args.execute:
        print(f"Entries drained: {total_drained}")
        if total_drained > 0:
            print("\n[SUCCESS] 'Resent unacked' log spam should stop")
    else:
        if total_stale > 0:
            print("\nRun with --execute to drain these entries")
            print(f"Command: python -m tools.ipc_ack_drain --execute")

    return 0 if total_stale > 0 or total_drained > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
