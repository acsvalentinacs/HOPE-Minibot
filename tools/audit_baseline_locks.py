# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 15:00:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 15:00:00 UTC
# === END SIGNATURE ===
"""
Audit baseline locks (BASELINE FREEZE enforcement).

Fail-closed:
- Missing baseline_locks.json -> exit 1
- Invalid JSON -> exit 1
- Missing locked file -> exit 1
- SHA256 mismatch -> exit 1
- Missing marker (for baseline-before-marker mode) -> exit 1
- Path outside repo -> exit 1

Usage:
    python tools/audit_baseline_locks.py --root .
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def _sha256_bytes(data: bytes) -> str:
    """Compute sha256 hex digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def _compute_baseline_sha256(file_path: Path, mode: str, marker: str | None) -> tuple[str, str | None]:
    """
    Compute SHA256 for baseline portion of file.

    Args:
        file_path: Path to file
        mode: "whole-file" or "baseline-before-marker"
        marker: Marker string (required for baseline-before-marker mode)

    Returns:
        (sha256_hex, error_message) - error_message is None on success
    """
    if not file_path.exists():
        return "", f"file_not_found:{file_path}"

    try:
        raw_bytes = file_path.read_bytes()
    except Exception as e:
        return "", f"cannot_read:{file_path}:{e}"

    if mode == "whole-file":
        return _sha256_bytes(raw_bytes), None

    elif mode == "baseline-before-marker":
        if not marker:
            return "", "marker_required_for_baseline-before-marker_mode"

        # Find marker in file (decode to find position, then slice bytes)
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            # Try with errors='replace' to find marker
            text = raw_bytes.decode("utf-8", errors="replace")

        # Find marker line
        lines = text.splitlines(keepends=True)
        marker_end_pos = 0
        found = False
        for i, line in enumerate(lines):
            marker_end_pos += len(line.encode("utf-8"))
            if marker in line:
                found = True
                break

        if not found:
            return "", f"marker_not_found:{marker}"

        # Hash bytes from start to end of marker line
        baseline_bytes = raw_bytes[:marker_end_pos]
        return _sha256_bytes(baseline_bytes), None

    else:
        return "", f"unknown_mode:{mode}"


def audit_locks(root: Path, locks_file: Path) -> tuple[bool, list[str]]:
    """
    Audit all baseline locks.

    Returns:
        (all_pass, list_of_errors)
    """
    errors: list[str] = []

    if not locks_file.exists():
        return False, [f"locks_file_not_found:{locks_file}"]

    try:
        with open(locks_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return False, [f"invalid_json:{locks_file}:{e}"]
    except Exception as e:
        return False, [f"cannot_read:{locks_file}:{e}"]

    if not isinstance(data, dict):
        return False, ["locks_file_must_be_object"]

    locks = data.get("locks", [])
    if not isinstance(locks, list):
        return False, ["locks_must_be_array"]

    if len(locks) == 0:
        # Empty locks file is valid (no locks yet)
        return True, []

    for i, lock in enumerate(locks):
        if not isinstance(lock, dict):
            errors.append(f"lock[{i}]:must_be_object")
            continue

        path_str = lock.get("path")
        mode = lock.get("mode", "whole-file")
        expected_sha = lock.get("baseline_sha256", "")
        marker = lock.get("marker")

        if not path_str:
            errors.append(f"lock[{i}]:missing_path")
            continue

        # Resolve path relative to root
        file_path = (root / path_str).resolve()

        # Security check: path must be inside root
        try:
            file_path.relative_to(root.resolve())
        except ValueError:
            errors.append(f"lock[{i}]:{path_str}:path_outside_repo")
            continue

        # Compute actual SHA256
        actual_sha, err = _compute_baseline_sha256(file_path, mode, marker)
        if err:
            errors.append(f"lock[{i}]:{path_str}:{err}")
            continue

        # Compare with expected
        # Normalize: expected may have "sha256:" prefix
        expected_hex = expected_sha.replace("sha256:", "").lower()
        actual_hex = actual_sha.lower()

        if expected_hex != actual_hex:
            errors.append(
                f"lock[{i}]:{path_str}:sha256_mismatch:"
                f"expected={expected_hex[:16]}...,actual={actual_hex[:16]}..."
            )

    return len(errors) == 0, errors


def main() -> int:
    """CLI entrypoint."""
    ap = argparse.ArgumentParser(description="Audit baseline locks (BASELINE FREEZE)")
    ap.add_argument("--root", type=Path, required=True, help="Project root")
    ap.add_argument("--file", type=str, default="tools/baseline_locks.json",
                    help="Path to baseline_locks.json relative to root")
    ns = ap.parse_args()

    root = ns.root.resolve()
    locks_file = (root / ns.file).resolve()

    print(f"BASELINE_LOCKS_AUDIT root={root}")

    is_valid, errors = audit_locks(root, locks_file)

    if not is_valid:
        print(f"\nFAIL-CLOSED: Baseline locks audit failed", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        return 1

    # Count locks
    try:
        with open(locks_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        lock_count = len(data.get("locks", []))
    except Exception:
        lock_count = 0

    print(f"\nPASS: Baseline locks audit OK (locks={lock_count})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
