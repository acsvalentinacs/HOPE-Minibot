# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T20:00:00Z
# Purpose: Network guard - detect direct network calls outside core/net/** (fail-closed)
# === END SIGNATURE ===
"""
Network Guard - Direct Network Call Detector.

Scans minibot/**/*.py for forbidden direct network calls.
Only core/net/** is allowed to make network requests.

Forbidden patterns:
- urllib.request.urlopen
- requests.(get|post|put|delete|Session)
- socket.socket
- http.client.(HTTPConnection|HTTPSConnection)
- aiohttp.ClientSession
- httpx.(Client|AsyncClient)

Exit codes:
    0 = PASS (no violations found)
    1 = FAIL (violations detected)
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

# Allowed network wrapper directory (relative to project root)
ALLOWED_NET_DIR = "core/net"

# Exclusion patterns
EXCLUDE_DIRS: Set[str] = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
}

# Forbidden patterns (compiled regex)
FORBIDDEN_PATTERNS: List[Tuple[str, re.Pattern]] = [
    # urllib
    ("urllib.request.urlopen", re.compile(r"\burllib\.request\.urlopen\b")),
    ("from urllib.request import.*urlopen", re.compile(r"from\s+urllib\.request\s+import\s+.*\burlopen\b")),

    # requests library
    ("requests.get", re.compile(r"\brequests\.get\b")),
    ("requests.post", re.compile(r"\brequests\.post\b")),
    ("requests.put", re.compile(r"\brequests\.put\b")),
    ("requests.delete", re.compile(r"\brequests\.delete\b")),
    ("requests.Session", re.compile(r"\brequests\.Session\b")),

    # socket
    ("socket.socket", re.compile(r"\bsocket\.socket\b")),

    # http.client
    ("http.client.HTTPConnection", re.compile(r"\bhttp\.client\.HTTPConnection\b")),
    ("http.client.HTTPSConnection", re.compile(r"\bhttp\.client\.HTTPSConnection\b")),
    ("HTTPConnection", re.compile(r"from\s+http\.client\s+import\s+.*\bHTTPConnection\b")),
    ("HTTPSConnection", re.compile(r"from\s+http\.client\s+import\s+.*\bHTTPSConnection\b")),

    # aiohttp
    ("aiohttp.ClientSession", re.compile(r"\baiohttp\.ClientSession\b")),
    ("ClientSession (aiohttp)", re.compile(r"from\s+aiohttp\s+import\s+.*\bClientSession\b")),

    # httpx
    ("httpx.Client", re.compile(r"\bhttpx\.Client\b")),
    ("httpx.AsyncClient", re.compile(r"\bhttpx\.AsyncClient\b")),
]


def is_allowed_path(rel_path: Path) -> bool:
    """
    Check if file is in allowed network directory.

    Args:
        rel_path: Path relative to project root

    Returns:
        True if file is allowed to make direct network calls
    """
    rel_str = str(rel_path).replace("\\", "/")
    return rel_str.startswith(ALLOWED_NET_DIR + "/") or rel_str.startswith(ALLOWED_NET_DIR + "\\")


def should_exclude(rel_path: Path) -> bool:
    """Check if path should be excluded from scan."""
    parts = rel_path.parts
    for part in parts:
        if part in EXCLUDE_DIRS:
            return True
    return False


def scan_file(filepath: Path, rel_path: Path) -> List[Tuple[int, str]]:
    """
    Scan file for forbidden network patterns.

    Args:
        filepath: Absolute path to file
        rel_path: Relative path for display

    Returns:
        List of (line_number, pattern_name) tuples
    """
    violations = []

    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return violations

    lines = content.split("\n")

    for line_num, line in enumerate(lines, 1):
        # Skip comments
        stripped = line.strip()
        if stripped.startswith("#"):
            continue

        for pattern_name, pattern_re in FORBIDDEN_PATTERNS:
            if pattern_re.search(line):
                violations.append((line_num, pattern_name))

    return violations


def scan_tree(root: Path) -> List[Tuple[Path, int, str]]:
    """
    Scan directory tree for network violations.

    Args:
        root: Root directory to scan

    Returns:
        List of (rel_path, line_number, pattern_name) tuples
    """
    all_violations = []

    for filepath in root.rglob("*.py"):
        try:
            rel_path = filepath.relative_to(root)
        except ValueError:
            continue

        # Skip excluded directories
        if should_exclude(rel_path):
            continue

        # Skip allowed network directory
        if is_allowed_path(rel_path):
            continue

        violations = scan_file(filepath, rel_path)

        for line_num, pattern_name in violations:
            all_violations.append((rel_path, line_num, pattern_name))

    return all_violations


def main() -> int:
    """Main entrypoint."""
    parser = argparse.ArgumentParser(
        description="Network Guard - detect direct network calls (fail-closed)",
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
        help="Show all scanned files",
    )

    args = parser.parse_args()

    root = args.root.resolve()

    if not root.exists():
        print(f"ERROR: Root directory not found: {root}", file=sys.stderr)
        return 2

    print(f"Scanning: {root}")
    print(f"Allowed: {ALLOWED_NET_DIR}/**")
    print()

    violations = scan_tree(root)

    if violations:
        print(f"FAIL: {len(violations)} violation(s) found")
        print()
        print("Violations (rel_path:line:pattern):")
        for rel_path, line_num, pattern_name in violations:
            # Security: only show path:line, not content
            rel_str = str(rel_path).replace("\\", "/")
            print(f"  {rel_str}:{line_num}:{pattern_name}")

        print()
        print("Fix: Use core/net/http_client.py wrapper instead of direct network calls.")
        return 1

    print("PASS: No direct network calls found outside core/net/**")
    return 0


if __name__ == "__main__":
    sys.exit(main())
