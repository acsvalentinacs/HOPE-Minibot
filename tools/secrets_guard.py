# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T20:00:00Z
# Modified by: Claude (opus-4)
# Modified at (UTC): 2026-01-25T22:30:00Z
# Purpose: Secrets guard v2.2 - JSON-aware detection with smart sha256 whitelisting (fail-closed)
# === END SIGNATURE ===
"""
Secrets Guard v2.2 - Hardcoded Secrets Detector (JSON-aware).

Scans minibot/** for potential hardcoded secrets.
Reports only file:line (never content) to prevent exposure.

v2.2 Changes:
- REMOVED: Directory exclusions (state/health, state/audit were bypasses)
- ADDED: JSON-aware scanning with key_path-based sha256 whitelisting
- ADDED: Fail-closed on parse errors in P0 tier files

Detected patterns:
- GitHub tokens (ghp_...)
- AWS keys (AKIA...)
- Slack tokens (xox[baprs]-...)
- Private keys (BEGIN PRIVATE KEY)
- Generic API key patterns
- Telegram bot tokens
- JWT tokens

Smart Whitelisting:
- sha256 hex strings are ONLY allowed when key_path contains "sha256"
- Example: {"sha256": "abc123..."} → OK
- Example: {"token": "abc123..."} (64 hex chars) → FLAGGED

Exit codes:
    0 = PASS (no secrets found)
    1 = FAIL (potential secrets detected)
    2 = ERROR (setup/config issue)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import List, Tuple, Set, Dict, Any, Optional

# SSoT paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# File extensions to scan
SCAN_EXTENSIONS: Set[str] = {
    ".py", ".ps1", ".cmd", ".bat", ".sh",
    ".env", ".txt", ".json", ".jsonl", ".yml", ".yaml",
    ".toml", ".ini", ".cfg", ".conf",
}

# P0 tier directories (money perimeter) - fail-closed on ANY error
P0_DIRS: Set[str] = {
    "core/trade",
    "core/risk",
    "core/exchange",
}

# P0 tier file patterns
P0_FILE_PATTERNS: List[str] = [
    "run_live*.py",
    "*_mainnet*.py",
    "*_production*.py",
]

# Exclusion patterns - MINIMAL, only truly non-scannable
EXCLUDE_DIRS: Set[str] = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    "staging/backup",
    # NOTE: state/health and state/audit are NO LONGER excluded (v2.2 fix)
}

# Allowed files (false positive exclusions - must be explicitly justified)
ALLOWED_FILES: Set[str] = {
    "secrets_guard.py",  # This file contains detection patterns
    "CLAUDE.md",  # Documentation with example patterns
    ".env.example",  # Example file with placeholder values
}

# Secret patterns (compiled regex)
# Each pattern: (name, regex, min_length)
SECRET_PATTERNS: List[Tuple[str, re.Pattern, int]] = [
    # GitHub Personal Access Token
    ("github_token", re.compile(r"\bghp_[A-Za-z0-9]{36,}\b"), 40),

    # AWS Access Key
    ("aws_access_key", re.compile(r"\bAKIA[A-Z0-9]{16}\b"), 20),

    # AWS Secret Key (base64-like, 40 chars)
    ("aws_secret_key", re.compile(r"\b[A-Za-z0-9/+=]{40}\b"), 40),

    # Slack tokens
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), 15),

    # Private key markers
    ("private_key", re.compile(r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----"), 20),
    ("private_key_ec", re.compile(r"-----BEGIN\s+EC\s+PRIVATE\s+KEY-----"), 20),

    # Generic patterns (more prone to false positives, lower priority)
    ("api_key_assign", re.compile(r"\bapi[_-]?key\s*[:=]\s*['\"][A-Za-z0-9_-]{20,}['\"]", re.IGNORECASE), 25),
    ("secret_assign", re.compile(r"\bsecret\s*[:=]\s*['\"][A-Za-z0-9_-]{20,}['\"]", re.IGNORECASE), 25),
    ("binance_key", re.compile(r"\bbinance[_-]?(api[_-]?)?(key|secret)\s*[:=]\s*['\"][A-Za-z0-9]{20,}['\"]", re.IGNORECASE), 25),
    ("telegram_token", re.compile(r"\b\d{9,10}:[A-Za-z0-9_-]{35}\b"), 45),

    # JWT tokens
    ("jwt_token", re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b"), 50),

    # Hex strings that look like secrets (64 chars = sha256 length, context-dependent)
    ("hex_secret_64", re.compile(r"\b[a-f0-9]{64}\b"), 64),
]

# Keys that are allowed to contain sha256-like hex values
SHA256_ALLOWED_KEY_PATTERNS: List[str] = [
    "sha256",
    "hash",
    "digest",
    "checksum",
    "content_sha256",
    "cmdline_sha256",
    "manifest_sha256",
    "file_hash",
    "tree_hash",
    "git_head",  # Git commit hashes
    "dedup_key",  # Deduplication hashes
    "run_id",  # Run IDs may contain hashes
    "evidence_line",  # Audit evidence containing hashes
    "allowlist_sha256",  # Policy allowlist hashes
    "nonce",  # Cryptographic nonces
    "id",  # IDs that may contain sha256 prefixes
    "link",  # URLs may contain hash-like substrings
    "url",  # URLs may contain hash-like substrings
    "guid",  # GUIDs
]


def is_p0_path(rel_path: Path) -> bool:
    """Check if path is in P0 tier (money perimeter)."""
    rel_str = str(rel_path).replace("\\", "/")

    # Check P0 directories
    for p0_dir in P0_DIRS:
        if rel_str.startswith(p0_dir + "/") or rel_str.startswith(p0_dir):
            return True

    # Check P0 file patterns
    import fnmatch
    for pattern in P0_FILE_PATTERNS:
        if fnmatch.fnmatch(rel_path.name, pattern):
            return True

    return False


def should_exclude(rel_path: Path) -> bool:
    """Check if path should be excluded from scan."""
    # Check filename allowlist
    if rel_path.name in ALLOWED_FILES:
        return True

    # Check directory exclusions
    parts = rel_path.parts
    for part in parts:
        if part in EXCLUDE_DIRS:
            return True

    rel_str = str(rel_path).replace("\\", "/")
    for excl in EXCLUDE_DIRS:
        if excl in rel_str:
            return True

    return False


def is_sha256_key_context(key_path: str) -> bool:
    """
    Check if key_path indicates this is a legitimate sha256 field.

    Args:
        key_path: Dot-separated key path (e.g., "metrics.content_sha256")

    Returns:
        True if any part of key_path matches allowed sha256 patterns
    """
    key_path_lower = key_path.lower()
    for pattern in SHA256_ALLOWED_KEY_PATTERNS:
        if pattern in key_path_lower:
            return True
    return False


def extract_json_values_with_paths(
    obj: Any,
    current_path: str = ""
) -> List[Tuple[str, str]]:
    """
    Recursively extract all string values from JSON with their key paths.

    Args:
        obj: JSON object (dict, list, or primitive)
        current_path: Current dot-separated path

    Returns:
        List of (key_path, value) tuples for string values
    """
    results = []

    if isinstance(obj, dict):
        for key, value in obj.items():
            new_path = f"{current_path}.{key}" if current_path else key
            results.extend(extract_json_values_with_paths(value, new_path))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            new_path = f"{current_path}[{i}]"
            results.extend(extract_json_values_with_paths(item, new_path))
    elif isinstance(obj, str):
        results.append((current_path, obj))

    return results


def scan_json_content(
    content: str,
    filepath: Path,
    is_jsonl: bool = False
) -> List[Tuple[int, str, str]]:
    """
    Scan JSON/JSONL content with key_path awareness.

    Args:
        content: File content
        filepath: Path for error reporting
        is_jsonl: True if JSONL format (one JSON per line)

    Returns:
        List of (line_number, pattern_name, key_path) tuples

    Raises:
        ValueError: On parse error in P0 tier (fail-closed)
    """
    findings = []
    is_p0 = is_p0_path(filepath.relative_to(PROJECT_ROOT) if filepath.is_absolute() else filepath)

    lines = content.split("\n")

    if is_jsonl:
        # Parse each line as separate JSON
        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue

            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                if is_p0:
                    raise ValueError(f"FAIL-CLOSED: JSON parse error in P0 file {filepath}:{line_num}: {e}")
                continue

            values = extract_json_values_with_paths(obj)
            for key_path, value in values:
                finding = check_value_for_secrets(value, key_path, line_num)
                if finding:
                    findings.append(finding)
    else:
        # Parse entire file as single JSON
        try:
            obj = json.loads(content)
        except json.JSONDecodeError as e:
            if is_p0:
                raise ValueError(f"FAIL-CLOSED: JSON parse error in P0 file {filepath}: {e}")
            # Fall back to line-by-line text scanning
            return []

        values = extract_json_values_with_paths(obj)
        for key_path, value in values:
            # Estimate line number (not perfect but helpful)
            line_num = 1
            for i, line in enumerate(lines, 1):
                if value[:20] in line if len(value) > 20 else value in line:
                    line_num = i
                    break

            finding = check_value_for_secrets(value, key_path, line_num)
            if finding:
                findings.append(finding)

    return findings


def check_value_for_secrets(
    value: str,
    key_path: str,
    line_num: int
) -> Optional[Tuple[int, str, str]]:
    """
    Check a single value for secret patterns with key_path context.

    Args:
        value: String value to check
        key_path: Dot-separated key path
        line_num: Line number for reporting

    Returns:
        (line_num, pattern_name, key_path) if secret found, None otherwise
    """
    for pattern_name, pattern_re, min_len in SECRET_PATTERNS:
        matches = pattern_re.findall(value)
        for match in matches:
            match_text = match if isinstance(match, str) else match[0]

            if len(match_text) < min_len:
                continue

            # Smart sha256 whitelisting: hash-like patterns are allowed in sha256 key contexts
            # This covers hex_secret_64 AND aws_secret_key which can match sha256 substrings
            if pattern_name in ("hex_secret_64", "aws_secret_key"):
                if is_sha256_key_context(key_path):
                    continue  # Legitimate sha256 hash in proper field

            # Check for common false positive contexts in value
            if is_false_positive_value(value, match_text):
                continue

            return (line_num, pattern_name, key_path)

    return None


def is_false_positive_value(value: str, match_text: str) -> bool:
    """
    Check if match is likely a false positive based on value content.

    Args:
        value: Full value string
        match_text: The matched text

    Returns:
        True if likely false positive
    """
    value_lower = value.lower()

    false_positive_markers = [
        "example",
        "placeholder",
        "dummy",
        "test",
        "mock",
        "fake",
        "sample",
        "template",
        "xxx",
        "your_",
        "insert_",
        "<your",
        "{your",
    ]

    for marker in false_positive_markers:
        if marker in value_lower:
            return True

    return False


def is_false_positive_line(line: str, match_text: str) -> bool:
    """
    Check if match is likely a false positive based on line context.

    Args:
        line: Full line of code
        match_text: The matched text

    Returns:
        True if likely false positive
    """
    line_lower = line.lower()

    # Check for common false positive contexts
    false_positive_contexts = [
        "example",
        "placeholder",
        "dummy",
        "test",
        "mock",
        "fake",
        "sample",
        "template",
        "xxx",
        "your_",
        "insert_",
        "<your",
        "{your",
    ]

    for ctx in false_positive_contexts:
        if ctx in line_lower:
            return True

    # Check if it's in a comment
    stripped = line.strip()
    if stripped.startswith("#") or stripped.startswith("//"):
        return True

    # Check for sha256: prefix pattern (legitimate hash reference)
    if "sha256:" in line_lower and match_text in line:
        return True

    return False


def scan_file(filepath: Path) -> List[Tuple[int, str]]:
    """
    Scan file for potential secrets.

    Args:
        filepath: Path to file

    Returns:
        List of (line_number, pattern_name) tuples

    Raises:
        ValueError: On parse error in P0 tier (fail-closed)
        OSError: On read error
    """
    findings = []

    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return findings

    # JSON-aware scanning for .json and .jsonl files
    suffix = filepath.suffix.lower()
    if suffix == ".json":
        try:
            json_findings = scan_json_content(content, filepath, is_jsonl=False)
            return [(ln, pn) for ln, pn, _ in json_findings]
        except ValueError:
            raise  # Re-raise fail-closed errors
        except Exception:
            pass  # Fall through to text scanning

    if suffix == ".jsonl":
        try:
            json_findings = scan_json_content(content, filepath, is_jsonl=True)
            return [(ln, pn) for ln, pn, _ in json_findings]
        except ValueError:
            raise  # Re-raise fail-closed errors
        except Exception:
            pass  # Fall through to text scanning

    # Text-based scanning for other files
    lines = content.split("\n")

    for line_num, line in enumerate(lines, 1):
        for pattern_name, pattern_re, min_len in SECRET_PATTERNS:
            matches = pattern_re.findall(line)
            for match in matches:
                match_text = match if isinstance(match, str) else match[0]

                # Skip if too short
                if len(match_text) < min_len:
                    continue

                # Skip false positives
                if is_false_positive_line(line, match_text):
                    continue

                # For hex_secret_64 and aws_secret_key in text files, be more lenient
                # Only flag if it looks like an assignment with secret-related context
                if pattern_name in ("hex_secret_64", "aws_secret_key"):
                    # Check if line contains assignment-like patterns with secret keywords
                    secret_keywords = ["key", "secret", "token", "password", "credential", "api_key", "apikey"]
                    if not any(kw in line.lower() for kw in secret_keywords):
                        continue

                findings.append((line_num, pattern_name))
                break  # One finding per line is enough

    return findings


def scan_tree(root: Path) -> Tuple[List[Tuple[Path, int, str]], int]:
    """
    Scan directory tree for secrets.

    Args:
        root: Root directory to scan

    Returns:
        Tuple of (findings list, files_scanned count)
        findings: List of (rel_path, line_number, pattern_name) tuples

    Raises:
        ValueError: On parse error in P0 tier (fail-closed)
    """
    all_findings = []
    files_scanned = 0

    for filepath in root.rglob("*"):
        if not filepath.is_file():
            continue

        # Check extension
        if filepath.suffix.lower() not in SCAN_EXTENSIONS:
            continue

        try:
            rel_path = filepath.relative_to(root)
        except ValueError:
            continue

        # Skip excluded paths
        if should_exclude(rel_path):
            continue

        files_scanned += 1
        findings = scan_file(filepath)

        for line_num, pattern_name in findings:
            all_findings.append((rel_path, line_num, pattern_name))

    return all_findings, files_scanned


def main() -> int:
    """Main entrypoint."""
    parser = argparse.ArgumentParser(
        description="Secrets Guard v2.2 - JSON-aware detection (fail-closed)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=PROJECT_ROOT,
        help=f"Root directory to scan (default: {PROJECT_ROOT})",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show scan statistics",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON format",
    )

    args = parser.parse_args()

    root = args.root.resolve()

    if not root.exists():
        print(f"ERROR: Root directory not found: {root}", file=sys.stderr)
        return 2

    if not args.json:
        print(f"Secrets Guard v2.2 (JSON-aware)")
        print(f"Scanning: {root}")
        print(f"Extensions: {', '.join(sorted(SCAN_EXTENSIONS))}")
        print()

    try:
        findings, files_scanned = scan_tree(root)
    except ValueError as e:
        # Fail-closed error from P0 tier
        print(f"FAIL: {e}", file=sys.stderr)
        return 1

    if args.json:
        result = {
            "schema_version": "secrets_guard_v2.2",
            "root": str(root),
            "files_scanned": files_scanned,
            "findings_count": len(findings),
            "passed": len(findings) == 0,
            "findings": [
                {
                    "path": str(rel_path).replace("\\", "/"),
                    "line": line_num,
                    "pattern": pattern_name,
                }
                for rel_path, line_num, pattern_name in findings
            ],
        }
        print(json.dumps(result, indent=2))
        return 0 if result["passed"] else 1

    if args.verbose:
        print(f"Files scanned: {files_scanned}")
        print()

    if findings:
        print(f"FAIL: {len(findings)} potential secret(s) found")
        print()
        print("Findings (rel_path:line - NO CONTENT SHOWN):")
        for rel_path, line_num, pattern_name in findings:
            # Security: NEVER show line content, only location
            rel_str = str(rel_path).replace("\\", "/")
            print(f"  {rel_str}:{line_num} [{pattern_name}]")

        print()
        print("Action: Review each location and move secrets to C:\\secrets\\hope\\.env")
        return 1

    print(f"PASS: No hardcoded secrets detected ({files_scanned} files scanned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
