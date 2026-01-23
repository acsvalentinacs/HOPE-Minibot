# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-22 23:15:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-22 23:15:00 UTC
# === END SIGNATURE ===
"""
JSONL Verifier - Validate sha256 JSONL file integrity.

Checks every line for:
1. Format: sha256:<64hex>:<json>
2. Hash correctness: sha256 matches recomputed hash of JSON payload
3. JSON validity: payload is valid single-line JSON
4. No partial/corrupted lines

Usage:
    python tools/jsonl_verify.py --in state/stress/out.jsonl
    python tools/jsonl_verify.py --in state/stress/out.jsonl --verbose
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

# SSoT: compute paths from __file__
BASE_DIR = Path(__file__).resolve().parent.parent

# Canon B format: sha256:<64hex>:<json>
CANON_B_PATTERN = re.compile(r"^sha256:([0-9a-f]{64}):(.+)$")


def verify_line(line: str, line_num: int) -> tuple[bool, str]:
    """
    Verify a single JSONL line.

    Returns (is_valid, reason).
    """
    # Strip trailing newline but preserve content
    line = line.rstrip("\n\r")

    if not line:
        return False, "empty_line"

    # Check Canon B format
    match = CANON_B_PATTERN.match(line)
    if not match:
        return False, "format_mismatch"

    claimed_hash = match.group(1)
    json_part = match.group(2)

    # Verify JSON is valid
    try:
        obj = json.loads(json_part)
    except json.JSONDecodeError as e:
        return False, f"json_invalid:{e}"

    # Recompute hash
    # Canon B: hash is computed on UTF-8 bytes of JSON payload
    computed_hash = hashlib.sha256(json_part.encode("utf-8")).hexdigest()

    if computed_hash != claimed_hash:
        return False, f"hash_mismatch:claimed={claimed_hash[:16]}...,computed={computed_hash[:16]}..."

    return True, "ok"


def verify_file(input_path: Path, verbose: bool = False) -> dict:
    """
    Verify entire JSONL file.

    Returns summary dict.
    """
    if not input_path.exists():
        return {
            "path": str(input_path),
            "exists": False,
            "total_lines": 0,
            "valid_lines": 0,
            "invalid_lines": 0,
            "errors": ["file_not_found"],
        }

    total = 0
    valid = 0
    invalid = 0
    errors = []

    with open(input_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            total += 1
            is_valid, reason = verify_line(line, line_num)

            if is_valid:
                valid += 1
                if verbose:
                    print(f"  [OK] line {line_num}")
            else:
                invalid += 1
                error_msg = f"line_{line_num}:{reason}"
                errors.append(error_msg)
                if verbose or invalid <= 10:  # Show first 10 errors
                    print(f"  [BAD] line {line_num}: {reason}")

    summary = {
        "path": str(input_path),
        "exists": True,
        "total_lines": total,
        "valid_lines": valid,
        "invalid_lines": invalid,
        "errors": errors[:50],  # Limit error list
        "pass": invalid == 0,
    }

    return summary


def main() -> int:
    """CLI entrypoint."""
    ap = argparse.ArgumentParser(description="Verify sha256 JSONL file integrity")
    ap.add_argument("--in", dest="input", type=Path, required=True,
                    help="Input JSONL file to verify")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="Show each line result")
    ns = ap.parse_args()

    print(f"JSONL Verify: {ns.input}")
    print()

    summary = verify_file(ns.input, ns.verbose)

    print()
    print("=== VERIFICATION SUMMARY ===")
    print(f"File:    {summary['path']}")
    print(f"Total:   {summary['total_lines']}")
    print(f"Valid:   {summary['valid_lines']}")
    print(f"Invalid: {summary['invalid_lines']}")

    if summary.get("pass"):
        print("\nPASS: All lines valid")
        return 0
    else:
        print(f"\nFAIL: {summary['invalid_lines']} invalid lines")
        if summary["errors"] and not ns.verbose:
            print("Errors (first 10):")
            for e in summary["errors"][:10]:
                print(f"  - {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
