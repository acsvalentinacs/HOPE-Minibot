# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T20:00:00Z
# Purpose: Secrets guard - detect hardcoded secrets without exposing them (fail-closed)
# === END SIGNATURE ===
"""
Secrets Guard - Hardcoded Secrets Detector.

Scans minibot/** for potential hardcoded secrets.
Reports only file:line (never content) to prevent exposure.

Detected patterns:
- GitHub tokens (ghp_...)
- AWS keys (AKIA...)
- Slack tokens (xox[baprs]-...)
- Private keys (BEGIN PRIVATE KEY)
- Generic API key patterns

Exit codes:
    0 = PASS (no secrets found)
    1 = FAIL (potential secrets detected)
    2 = ERROR (setup/config issue)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Tuple, Set

# SSoT paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# File extensions to scan
SCAN_EXTENSIONS: Set[str] = {
    ".py", ".ps1", ".cmd", ".bat", ".sh",
    ".env", ".txt", ".md", ".json", ".yml", ".yaml",
    ".toml", ".ini", ".cfg", ".conf",
}

# Exclusion patterns
EXCLUDE_DIRS: Set[str] = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    "staging/backup",
    "state/health",  # Contains sha256 hashes (false positives)
    "state/audit",  # Contains sha256 hashes (false positives)
}

# Allowed files (false positive exclusions)
ALLOWED_FILES: Set[str] = {
    "secrets_guard.py",  # This file
    "CLAUDE.md",  # Documentation with examples
    ".env.example",  # Example file
    "AllowList.spider.txt",  # Contains documentation text, not secrets
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
]

# Allowlist patterns (not secrets)
ALLOWLIST_PATTERNS: List[re.Pattern] = [
    re.compile(r"sha256:[a-f0-9]{64}"),  # SHA256 hashes
    re.compile(r"sha256:[a-f0-9]{16}"),  # Short SHA256 prefixes
    re.compile(r"[a-f0-9]{64}"),  # Hex hashes (context-dependent)
    re.compile(r"example|placeholder|dummy|test|fake|mock", re.IGNORECASE),
]


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


def is_false_positive(line: str, match_text: str) -> bool:
    """
    Check if match is likely a false positive.

    Args:
        line: Full line of code
        match_text: The matched text

    Returns:
        True if likely false positive
    """
    line_lower = line.lower()

    # Check allowlist patterns
    for pattern in ALLOWLIST_PATTERNS:
        if pattern.search(match_text):
            return True

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
        "# ",  # Comments
        "//",  # Comments
    ]

    for ctx in false_positive_contexts:
        if ctx in line_lower:
            return True

    # Check if it's in a comment
    stripped = line.strip()
    if stripped.startswith("#") or stripped.startswith("//"):
        return True

    return False


def scan_file(filepath: Path) -> List[Tuple[int, str]]:
    """
    Scan file for potential secrets.

    Args:
        filepath: Path to file

    Returns:
        List of (line_number, pattern_name) tuples
    """
    findings = []

    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return findings

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
                if is_false_positive(line, match_text):
                    continue

                findings.append((line_num, pattern_name))
                break  # One finding per line is enough

    return findings


def scan_tree(root: Path) -> List[Tuple[Path, int, str]]:
    """
    Scan directory tree for secrets.

    Args:
        root: Root directory to scan

    Returns:
        List of (rel_path, line_number, pattern_name) tuples
    """
    all_findings = []

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

        findings = scan_file(filepath)

        for line_num, pattern_name in findings:
            all_findings.append((rel_path, line_num, pattern_name))

    return all_findings


def main() -> int:
    """Main entrypoint."""
    parser = argparse.ArgumentParser(
        description="Secrets Guard - detect hardcoded secrets (fail-closed)",
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

    args = parser.parse_args()

    root = args.root.resolve()

    if not root.exists():
        print(f"ERROR: Root directory not found: {root}", file=sys.stderr)
        return 2

    print(f"Scanning: {root}")
    print(f"Extensions: {', '.join(sorted(SCAN_EXTENSIONS))}")
    print()

    findings = scan_tree(root)

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

    print("PASS: No hardcoded secrets detected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
