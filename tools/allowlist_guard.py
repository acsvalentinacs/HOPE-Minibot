# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T21:00:00Z
# Purpose: AllowList guard - strict format validation (fail-closed)
# === END SIGNATURE ===
"""
AllowList Guard - Strict Format Validator.

Validates that allowlist files contain ONLY:
- Comments (lines starting with #)
- Valid domain names (lowercase, no wildcards)
- Empty lines

Any other content = FAIL (fail-closed).

This prevents "mixed content" corruption where documentation
or logs accidentally get appended to allowlists.

Exit codes:
    0 = PASS (all lines valid)
    1 = FAIL (invalid lines detected)
    2 = ERROR (file not found, setup issue)
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Strict domain regex: letters, numbers, dots, hyphens
# No wildcards, no ports, no paths
DOMAIN_REGEX = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)

# Maximum line length (arbitrary but reasonable)
MAX_LINE_LENGTH = 255

# Known allowlist files
DEFAULT_ALLOWLISTS = [
    "config/AllowList.spider.txt",
    "config/AllowList.trade.txt",
]


@dataclass
class ValidationError:
    """Validation error record."""
    line_num: int
    content_preview: str  # First 50 chars, sanitized
    reason: str


def sanitize_preview(line: str, max_len: int = 50) -> str:
    """Sanitize line for safe display (no secrets, truncated)."""
    # Remove potential secrets patterns
    sanitized = re.sub(r"[A-Za-z0-9+/=]{20,}", "[REDACTED]", line)
    if len(sanitized) > max_len:
        return sanitized[:max_len] + "..."
    return sanitized


def validate_line(line: str, line_num: int) -> Optional[ValidationError]:
    """
    Validate single allowlist line.

    Valid formats:
    - Empty line
    - Comment (starts with #)
    - Valid domain (lowercase, no wildcards)

    Args:
        line: Raw line content
        line_num: 1-based line number

    Returns:
        ValidationError if invalid, None if valid
    """
    # Check length first
    if len(line) > MAX_LINE_LENGTH:
        return ValidationError(
            line_num=line_num,
            content_preview=sanitize_preview(line),
            reason=f"Line too long ({len(line)} > {MAX_LINE_LENGTH})",
        )

    stripped = line.strip()

    # Empty line - OK
    if not stripped:
        return None

    # Comment - OK
    if stripped.startswith("#"):
        return None

    # Check for forbidden characters
    if "\t" in line:
        return ValidationError(
            line_num=line_num,
            content_preview=sanitize_preview(line),
            reason="Tab character not allowed (use spaces for alignment)",
        )

    # Check for wildcards
    if "*" in stripped:
        return ValidationError(
            line_num=line_num,
            content_preview=sanitize_preview(line),
            reason="Wildcards (*) not allowed in allowlist",
        )

    # Check for URL-like content (should be domain only)
    if "://" in stripped or "/" in stripped:
        return ValidationError(
            line_num=line_num,
            content_preview=sanitize_preview(line),
            reason="URLs not allowed - use domain only (e.g., 'example.com' not 'https://example.com/path')",
        )

    # Check for port numbers
    if ":" in stripped:
        return ValidationError(
            line_num=line_num,
            content_preview=sanitize_preview(line),
            reason="Port numbers not allowed - use domain only",
        )

    # Check for uppercase
    if stripped != stripped.lower():
        return ValidationError(
            line_num=line_num,
            content_preview=sanitize_preview(line),
            reason=f"Uppercase not allowed - use '{stripped.lower()}'",
        )

    # Validate domain format
    if not DOMAIN_REGEX.match(stripped):
        return ValidationError(
            line_num=line_num,
            content_preview=sanitize_preview(line),
            reason="Invalid domain format",
        )

    return None


def validate_allowlist(filepath: Path) -> List[ValidationError]:
    """
    Validate entire allowlist file.

    Args:
        filepath: Path to allowlist file

    Returns:
        List of validation errors (empty = valid)
    """
    errors = []

    try:
        content = filepath.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [ValidationError(
            line_num=0,
            content_preview="[binary content]",
            reason="File is not valid UTF-8 text",
        )]

    lines = content.split("\n")

    for line_num, line in enumerate(lines, 1):
        error = validate_line(line, line_num)
        if error:
            errors.append(error)

    return errors


def main() -> int:
    """Main entrypoint."""
    parser = argparse.ArgumentParser(
        description="AllowList Guard - strict format validation (fail-closed)",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help=f"Allowlist files to validate (default: {DEFAULT_ALLOWLISTS})",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=PROJECT_ROOT,
        help=f"Project root (default: {PROJECT_ROOT})",
    )

    args = parser.parse_args()
    root = args.root.resolve()

    # Determine files to check
    if args.files:
        files = [Path(f) if Path(f).is_absolute() else root / f for f in args.files]
    else:
        files = [root / f for f in DEFAULT_ALLOWLISTS]

    # Filter to existing files
    existing = [f for f in files if f.exists()]

    if not existing:
        print("No allowlist files found to validate")
        print(f"Looked for: {[str(f) for f in files]}")
        return 2

    print("AllowList Guard - Strict Format Validation")
    print(f"Root: {root}")
    print()

    total_errors = 0
    all_passed = True

    for filepath in existing:
        try:
            rel_path = filepath.relative_to(root)
        except ValueError:
            rel_path = filepath

        errors = validate_allowlist(filepath)

        if errors:
            all_passed = False
            total_errors += len(errors)
            print(f"[FAIL] {rel_path}: {len(errors)} error(s)")
            for err in errors[:10]:  # Limit output
                print(f"  Line {err.line_num}: {err.reason}")
                print(f"    Preview: {err.content_preview}")
            if len(errors) > 10:
                print(f"  ... and {len(errors) - 10} more errors")
            print()
        else:
            # Count valid domains
            content = filepath.read_text(encoding="utf-8")
            domains = [
                line.strip()
                for line in content.split("\n")
                if line.strip() and not line.strip().startswith("#")
            ]
            print(f"[PASS] {rel_path}: {len(domains)} domains")

    print()

    if all_passed:
        print("PASS: All allowlists are valid")
        return 0
    else:
        print(f"FAIL: {total_errors} validation error(s)")
        return 1


if __name__ == "__main__":
    sys.exit(main())
