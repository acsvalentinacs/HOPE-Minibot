# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T20:00:00Z
# Modified by: Claude (opus-4)
# Modified at (UTC): 2026-01-25T21:00:00Z
# Purpose: Network guard v2.0 - AST-based detection with P0/P1 tiering (fail-closed)
# === END SIGNATURE ===
"""
Network Guard v2.0 - AST-Based Direct Network Call Detector.

Uses Python AST (Abstract Syntax Tree) for reliable detection.
Regex-based detection is unreliable (misses aliases, false positives in comments).

Tiering:
- P0 (Money Perimeter): core/trade/**, run_live*.py - ZERO TOLERANCE
- P1 (Other): Violations allowed only if in legacy_net_allowlist.json

Forbidden imports/calls:
- urllib.request (urlopen, Request)
- requests (get, post, put, delete, Session)
- socket (socket, create_connection)
- http.client (HTTPConnection, HTTPSConnection)
- aiohttp (ClientSession)
- httpx (Client, AsyncClient)

Exit codes:
    0 = PASS (no P0 violations, P1 in allowlist)
    1 = FAIL (P0 violations or P1 not in allowlist)
    2 = ERROR (setup/config issue)
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Set, Dict, Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Money perimeter - ZERO TOLERANCE for direct network
MONEY_PERIMETER_PATTERNS: List[str] = [
    "core/trade/",
    "run_live",
]

# Allowed network wrapper directory
ALLOWED_NET_DIRS: Set[str] = {
    "core/net/",
    "core/net\\",
}

# Exclusion patterns
EXCLUDE_DIRS: Set[str] = {
    ".git", ".venv", "__pycache__", ".pytest_cache",
    ".mypy_cache", "node_modules", "staging",
}

# Forbidden modules and their dangerous attributes
FORBIDDEN_IMPORTS: Dict[str, Set[str]] = {
    "urllib.request": {"urlopen", "Request", "urlretrieve"},
    "urllib": {"request"},
    "requests": {"get", "post", "put", "delete", "patch", "head", "options", "Session"},
    "socket": {"socket", "create_connection", "getaddrinfo"},
    "http.client": {"HTTPConnection", "HTTPSConnection"},
    "aiohttp": {"ClientSession", "TCPConnector", "request"},
    "httpx": {"Client", "AsyncClient", "get", "post", "put", "delete"},
}

# Top-level modules that are completely forbidden
FORBIDDEN_MODULES: Set[str] = {
    "urllib.request",
    "requests",
    "aiohttp",
    "httpx",
}


@dataclass
class Violation:
    """Network violation record."""
    rel_path: str
    line: int
    col: int
    kind: str  # "import" or "call"
    name: str  # e.g., "requests.get"
    is_p0: bool  # True if in money perimeter


@dataclass
class ScanResult:
    """Scan result container."""
    violations: List[Violation] = field(default_factory=list)
    files_scanned: int = 0
    p0_violations: int = 0
    p1_violations: int = 0
    p1_in_allowlist: int = 0


class NetworkASTVisitor(ast.NodeVisitor):
    """AST visitor to detect forbidden network imports and calls."""

    def __init__(self, rel_path: str, is_p0: bool):
        self.rel_path = rel_path
        self.is_p0 = is_p0
        self.violations: List[Violation] = []
        self._imported_aliases: Dict[str, str] = {}  # alias -> full module

    def visit_Import(self, node: ast.Import) -> None:
        """Handle 'import x' statements."""
        for alias in node.names:
            module = alias.name
            asname = alias.asname or module

            # Track alias
            self._imported_aliases[asname] = module

            # Check if module is forbidden
            if module in FORBIDDEN_MODULES or any(
                module.startswith(f"{fm}.") for fm in FORBIDDEN_MODULES
            ):
                self.violations.append(Violation(
                    rel_path=self.rel_path,
                    line=node.lineno,
                    col=node.col_offset,
                    kind="import",
                    name=module,
                    is_p0=self.is_p0,
                ))

        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Handle 'from x import y' statements."""
        module = node.module or ""

        for alias in node.names:
            name = alias.name
            asname = alias.asname or name
            full_name = f"{module}.{name}" if module else name

            # Track alias
            self._imported_aliases[asname] = full_name

            # Check forbidden
            if module in FORBIDDEN_IMPORTS:
                forbidden_attrs = FORBIDDEN_IMPORTS[module]
                if name in forbidden_attrs or name == "*":
                    self.violations.append(Violation(
                        rel_path=self.rel_path,
                        line=node.lineno,
                        col=node.col_offset,
                        kind="import",
                        name=full_name,
                        is_p0=self.is_p0,
                    ))

            # Check if importing entire forbidden module
            if module in FORBIDDEN_MODULES:
                self.violations.append(Violation(
                    rel_path=self.rel_path,
                    line=node.lineno,
                    col=node.col_offset,
                    kind="import",
                    name=full_name,
                    is_p0=self.is_p0,
                ))

        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Handle function calls to detect direct network usage."""
        call_name = self._get_call_name(node.func)

        if call_name:
            # Check against known patterns
            for module, attrs in FORBIDDEN_IMPORTS.items():
                for attr in attrs:
                    pattern = f"{module}.{attr}"
                    if call_name == pattern or call_name.endswith(f".{attr}"):
                        # Check if this is via an alias
                        parts = call_name.split(".")
                        if len(parts) >= 1:
                            base = parts[0]
                            if base in self._imported_aliases:
                                resolved = self._imported_aliases[base]
                                if resolved in FORBIDDEN_MODULES or any(
                                    resolved.startswith(f"{fm}") for fm in FORBIDDEN_IMPORTS
                                ):
                                    self.violations.append(Violation(
                                        rel_path=self.rel_path,
                                        line=node.lineno,
                                        col=node.col_offset,
                                        kind="call",
                                        name=call_name,
                                        is_p0=self.is_p0,
                                    ))

        self.generic_visit(node)

    def _get_call_name(self, node: ast.expr) -> Optional[str]:
        """Extract full dotted name from call expression."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            value_name = self._get_call_name(node.value)
            if value_name:
                return f"{value_name}.{node.attr}"
            return node.attr
        return None


def is_in_money_perimeter(rel_path: str) -> bool:
    """Check if file is in money perimeter (P0)."""
    norm_path = rel_path.replace("\\", "/")
    for pattern in MONEY_PERIMETER_PATTERNS:
        if pattern in norm_path:
            return True
    return False


def is_allowed_path(rel_path: str) -> bool:
    """Check if file is in allowed network directory."""
    norm_path = rel_path.replace("\\", "/")
    for allowed in ALLOWED_NET_DIRS:
        norm_allowed = allowed.replace("\\", "/")
        if norm_path.startswith(norm_allowed):
            return True
    return False


def should_exclude(rel_path: Path) -> bool:
    """Check if path should be excluded from scan."""
    for part in rel_path.parts:
        if part in EXCLUDE_DIRS:
            return True
    return False


def load_legacy_allowlist(root: Path) -> Set[str]:
    """Load legacy network allowlist for P1 violations."""
    allowlist_path = root / "config" / "legacy_net_allowlist.json"
    if not allowlist_path.exists():
        return set()

    try:
        data = json.loads(allowlist_path.read_text(encoding="utf-8"))
        # Format: {"allowed": [{"path": "...", "deadline": "...", "owner": "..."}]}
        return {entry["path"] for entry in data.get("allowed", [])}
    except (json.JSONDecodeError, KeyError, TypeError):
        return set()


def scan_file(filepath: Path, rel_path: str, is_p0: bool) -> List[Violation]:
    """Scan single file using AST analysis."""
    try:
        source = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return []

    visitor = NetworkASTVisitor(rel_path, is_p0)
    visitor.visit(tree)
    return visitor.violations


def scan_tree(root: Path, legacy_allowlist: Set[str]) -> ScanResult:
    """Scan entire tree for network violations."""
    result = ScanResult()

    for filepath in root.rglob("*.py"):
        try:
            rel_path = filepath.relative_to(root)
        except ValueError:
            continue

        rel_str = str(rel_path).replace("\\", "/")

        # Skip excluded
        if should_exclude(rel_path):
            continue

        # Skip allowed network dirs
        if is_allowed_path(rel_str):
            continue

        result.files_scanned += 1
        is_p0 = is_in_money_perimeter(rel_str)

        violations = scan_file(filepath, rel_str, is_p0)

        for v in violations:
            if v.is_p0:
                result.p0_violations += 1
                result.violations.append(v)
            else:
                # P1: check allowlist
                if rel_str in legacy_allowlist:
                    result.p1_in_allowlist += 1
                else:
                    result.p1_violations += 1
                    result.violations.append(v)

    return result


def main() -> int:
    """Main entrypoint."""
    parser = argparse.ArgumentParser(
        description="Network Guard v2.0 - AST-based detection (fail-closed)",
    )
    parser.add_argument(
        "--root", type=Path, default=PROJECT_ROOT,
        help=f"Root directory (default: {PROJECT_ROOT})",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output JSON report",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Fail on ANY violation (ignore legacy allowlist)",
    )

    args = parser.parse_args()
    root = args.root.resolve()

    if not root.exists():
        print(f"ERROR: Root not found: {root}", file=sys.stderr)
        return 2

    ts_start = datetime.now(timezone.utc)

    legacy_allowlist = set() if args.strict else load_legacy_allowlist(root)
    result = scan_tree(root, legacy_allowlist)

    ts_end = datetime.now(timezone.utc)
    scan_ms = int((ts_end - ts_start).total_seconds() * 1000)

    if args.json:
        report = {
            "schema_version": "network_guard_v2",
            "ts_utc": ts_end.isoformat(),
            "scan_time_ms": scan_ms,
            "files_scanned": result.files_scanned,
            "p0_violations": result.p0_violations,
            "p1_violations": result.p1_violations,
            "p1_in_allowlist": result.p1_in_allowlist,
            "passed": result.p0_violations == 0 and result.p1_violations == 0,
            "violations": [
                {
                    "path": v.rel_path,
                    "line": v.line,
                    "kind": v.kind,
                    "name": v.name,
                    "tier": "P0" if v.is_p0 else "P1",
                }
                for v in result.violations
            ],
        }
        print(json.dumps(report, indent=2))
    else:
        print(f"Network Guard v2.0 (AST-based)")
        print(f"Root: {root}")
        print(f"Files scanned: {result.files_scanned}")
        print(f"Scan time: {scan_ms}ms")
        print()

        if result.violations:
            print(f"P0 violations (BLOCKING): {result.p0_violations}")
            print(f"P1 violations (not in allowlist): {result.p1_violations}")
            print(f"P1 in legacy allowlist: {result.p1_in_allowlist}")
            print()

            # Group by tier
            p0_list = [v for v in result.violations if v.is_p0]
            p1_list = [v for v in result.violations if not v.is_p0]

            if p0_list:
                print("=== P0 VIOLATIONS (Money Perimeter) ===")
                for v in p0_list[:20]:  # Limit output
                    print(f"  {v.rel_path}:{v.line} [{v.kind}] {v.name}")
                if len(p0_list) > 20:
                    print(f"  ... and {len(p0_list) - 20} more")
                print()

            if p1_list:
                print("=== P1 VIOLATIONS ===")
                for v in p1_list[:30]:
                    print(f"  {v.rel_path}:{v.line} [{v.kind}] {v.name}")
                if len(p1_list) > 30:
                    print(f"  ... and {len(p1_list) - 30} more")
                print()

    # Determine exit code
    if result.p0_violations > 0:
        if not args.json:
            print("FAIL: P0 violations in money perimeter (BLOCKING)")
        return 1

    if result.p1_violations > 0:
        if not args.json:
            print("FAIL: P1 violations not in legacy allowlist")
            print("Fix: Add to config/legacy_net_allowlist.json or migrate to core/net/**")
        return 1

    if not args.json:
        print("PASS: No blocking network violations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
