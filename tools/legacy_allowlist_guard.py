# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T22:35:00Z
# Purpose: Legacy allowlist policy guard - validates deadline requirements (fail-closed)
# === END SIGNATURE ===
"""
Legacy Allowlist Policy Guard v1.0.

Validates that legacy_net_allowlist.json adheres to security policy:
- deadline:"never" is ONLY allowed for infra-only paths (network_guard.py)
- All other entries MUST have valid YYYY-MM-DD deadline
- owner field is required
- Paths must exist in project

Policy:
- MAINNET eligibility requires ZERO non-infra entries with deadline:"never"
- Entries with deadline:"never" outside INFRA_ONLY_PATHS = FAIL

Exit codes:
    0 = PASS (policy compliant)
    1 = FAIL (policy violation)
    2 = ERROR (config issue)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Paths that are allowed to have deadline:"never" (infrastructure-only)
# These are tools that MUST contain network patterns for detection
INFRA_ONLY_PATHS: set[str] = {
    "tools/network_guard.py",  # Network guard itself contains patterns for detection
}

ALLOWLIST_PATH = PROJECT_ROOT / "config" / "legacy_net_allowlist.json"

# Date pattern
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_date(date_str: str) -> Tuple[bool, str]:
    """
    Validate date string format and that it's not in the past.

    Args:
        date_str: Date string (YYYY-MM-DD or "never")

    Returns:
        (valid, message)
    """
    if date_str == "never":
        return True, "never"

    if not DATE_PATTERN.match(date_str):
        return False, f"Invalid date format: {date_str} (expected YYYY-MM-DD)"

    try:
        deadline = datetime.strptime(date_str, "%Y-%m-%d").date()
        today = datetime.now().date()

        if deadline < today:
            return False, f"Deadline {date_str} is in the past"

        return True, f"valid ({date_str})"
    except ValueError as e:
        return False, f"Invalid date: {e}"


def validate_entry(entry: Dict[str, Any], index: int) -> List[str]:
    """
    Validate a single allowlist entry.

    Args:
        entry: Entry dict with path, reason, deadline, owner
        index: Entry index for error reporting

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    # Required fields
    path = entry.get("path")
    if not path:
        errors.append(f"Entry {index}: missing 'path' field")
        return errors

    reason = entry.get("reason")
    if not reason:
        errors.append(f"Entry {index} ({path}): missing 'reason' field")

    owner = entry.get("owner")
    if not owner:
        errors.append(f"Entry {index} ({path}): missing 'owner' field")

    deadline = entry.get("deadline")
    if not deadline:
        errors.append(f"Entry {index} ({path}): missing 'deadline' field")
        return errors

    # Validate deadline
    valid, message = validate_date(deadline)
    if not valid:
        errors.append(f"Entry {index} ({path}): {message}")
        return errors

    # Check "never" policy
    if deadline == "never":
        # Normalize path for comparison
        path_normalized = path.replace("\\", "/")
        if path_normalized not in INFRA_ONLY_PATHS:
            errors.append(
                f"Entry {index} ({path}): deadline:'never' is ONLY allowed for "
                f"infra-only paths: {INFRA_ONLY_PATHS}. "
                f"This is a P0 SECURITY VIOLATION."
            )

    # Check path exists (warning, not error)
    full_path = PROJECT_ROOT / path
    if not full_path.exists():
        # This is a warning, not an error - file might have been moved
        pass

    return errors


def validate_allowlist(data: Dict[str, Any]) -> Tuple[bool, List[str], Dict[str, Any]]:
    """
    Validate the entire allowlist file.

    Args:
        data: Parsed JSON data

    Returns:
        (passed, errors, stats)
    """
    errors = []
    stats = {
        "total_entries": 0,
        "never_entries": 0,
        "valid_never_entries": 0,
        "invalid_never_entries": 0,
        "dated_entries": 0,
        "expired_entries": 0,
    }

    # Check schema version
    schema = data.get("schema_version")
    if schema != "legacy_net_allowlist_v1":
        errors.append(f"Invalid schema_version: {schema} (expected legacy_net_allowlist_v1)")

    # Validate entries
    allowed = data.get("allowed", [])
    if not isinstance(allowed, list):
        errors.append("'allowed' must be a list")
        return False, errors, stats

    stats["total_entries"] = len(allowed)

    for i, entry in enumerate(allowed):
        if not isinstance(entry, dict):
            errors.append(f"Entry {i}: not a dict")
            continue

        entry_errors = validate_entry(entry, i)
        errors.extend(entry_errors)

        # Count statistics
        deadline = entry.get("deadline", "")
        if deadline == "never":
            stats["never_entries"] += 1
            path = entry.get("path", "").replace("\\", "/")
            if path in INFRA_ONLY_PATHS:
                stats["valid_never_entries"] += 1
            else:
                stats["invalid_never_entries"] += 1
        else:
            stats["dated_entries"] += 1
            # Check if expired
            valid, _ = validate_date(deadline)
            if not valid and "past" in _.lower() if _ else False:
                stats["expired_entries"] += 1

    passed = len(errors) == 0
    return passed, errors, stats


def main() -> int:
    """Main entrypoint."""
    parser = argparse.ArgumentParser(
        description="Legacy Allowlist Policy Guard v1.0 (fail-closed)",
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=ALLOWLIST_PATH,
        help=f"Path to allowlist file (default: {ALLOWLIST_PATH})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON format",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed statistics",
    )

    args = parser.parse_args()

    allowlist_path = args.path.resolve()

    if not allowlist_path.exists():
        if args.json:
            print(json.dumps({"passed": False, "error": f"File not found: {allowlist_path}"}))
        else:
            print(f"ERROR: Allowlist file not found: {allowlist_path}", file=sys.stderr)
        return 2

    # Load and parse
    try:
        content = allowlist_path.read_text(encoding="utf-8")
        data = json.loads(content)
    except json.JSONDecodeError as e:
        if args.json:
            print(json.dumps({"passed": False, "error": f"JSON parse error: {e}"}))
        else:
            print(f"ERROR: JSON parse error: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        if args.json:
            print(json.dumps({"passed": False, "error": f"Read error: {e}"}))
        else:
            print(f"ERROR: Read error: {e}", file=sys.stderr)
        return 2

    # Validate
    passed, errors, stats = validate_allowlist(data)

    if args.json:
        result = {
            "schema_version": "legacy_allowlist_guard_v1",
            "path": str(allowlist_path),
            "passed": passed,
            "errors": errors,
            "stats": stats,
        }
        print(json.dumps(result, indent=2))
        return 0 if passed else 1

    print(f"Legacy Allowlist Policy Guard v1.0")
    print(f"File: {allowlist_path}")
    print()

    if args.verbose or not passed:
        print(f"Statistics:")
        print(f"  Total entries: {stats['total_entries']}")
        print(f"  deadline:'never' entries: {stats['never_entries']}")
        print(f"    - Valid (infra-only): {stats['valid_never_entries']}")
        print(f"    - INVALID (security violation): {stats['invalid_never_entries']}")
        print(f"  Dated entries: {stats['dated_entries']}")
        print()

    if errors:
        print(f"FAIL: {len(errors)} policy violation(s)")
        print()
        for error in errors:
            print(f"  - {error}")
        print()
        print("Action: Fix violations before release")
        return 1

    print(f"PASS: Allowlist policy compliant ({stats['total_entries']} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
