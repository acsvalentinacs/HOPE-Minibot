#!/usr/bin/env python3
"""
Atomically set GPT runner cursor value.

Usage:
    python set_cursor_atomic.py "zzzz_test_cursor.json"
    python set_cursor_atomic.py "1737312000.000000_abc123.json"
    python set_cursor_atomic.py ""  # reset to empty

Writes atomically: tmp -> fsync -> os.replace
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Derive paths portably
_THIS_FILE = Path(__file__).resolve()
_SCRIPTS_DIR = _THIS_FILE.parent
_MINIBOT_DIR = _SCRIPTS_DIR.parent
_STATE_DIR = _MINIBOT_DIR / "state"
CURSOR_FILE = _STATE_DIR / "gpt_runner_cursor.txt"


def atomic_write(path: Path, content: str) -> None:
    """Atomic write: tmp -> fsync -> os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def main() -> int:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <cursor_value>")
        print(f"Cursor file: {CURSOR_FILE}")
        return 1

    cursor_value = sys.argv[1]

    # Read old value for logging
    old_value = ""
    if CURSOR_FILE.exists():
        old_value = CURSOR_FILE.read_text(encoding="utf-8").strip()

    # Write atomically
    atomic_write(CURSOR_FILE, cursor_value)

    print(f"Cursor file: {CURSOR_FILE}")
    print(f"Old value: {old_value!r}")
    print(f"New value: {cursor_value!r}")
    print("OK: Written atomically (tmp->fsync->replace)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
