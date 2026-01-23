# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-20 12:00:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 14:00:00 UTC
# === END SIGNATURE ===
"""
HOPE/NORE Secrets Scanner v1.0

Standalone scanner for detecting hardcoded secrets in repository.

Fail-closed design:
- Exit code 0: No secrets found
- Exit code 2: Secrets detected (STOP)
- Exit code 3: Scanner error

Usage:
    python tools/scan_secrets.py
    python tools/scan_secrets.py --fix  # Show remediation steps

Based on Opinion1 recommendations.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple

ROOT = Path(r"C:\Users\kirillDev\Desktop\TradingBot\minibot")

SKIP_DIR_NAMES = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "venv",
    ".venv",
    "node_modules",
    "Старые файлы от проекта НОРЕ 2025-11-23",
}

SKIP_FILE_SUFFIXES = {
    ".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif",
    ".zip", ".7z", ".exe", ".dll", ".so", ".ico",
}

MAX_FILE_BYTES = 2_000_000  # 2MB safety

# Secret patterns to detect
PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("tg_bot_token", re.compile(
        r"(TG_BOT_TOKEN|TELEGRAM_BOT_TOKEN|TELEGRAM_TOKEN)\s*[:=]\s*['\"]?([^\s'\"\n]{20,})",
        re.I
    )),
    ("telegram_api", re.compile(
        r"\b(api_id|api_hash)\b\s*[:=]\s*['\"]?([^\s'\"\n]+)",
        re.I
    )),
    ("telegram_phone", re.compile(
        r"\bphone\b\s*[:=]\s*['\"]?(\+?[0-9]{10,})",
        re.I
    )),
    ("binance_key", re.compile(
        r"\b(BINANCE_API_KEY|API_KEY)\b\s*[:=]\s*['\"]?([A-Za-z0-9]{20,})",
        re.I
    )),
    ("binance_secret", re.compile(
        r"\b(BINANCE_API_SECRET|API_SECRET|SECRET_KEY)\b\s*[:=]\s*['\"]?([A-Za-z0-9]{20,})",
        re.I
    )),
    ("private_key_block", re.compile(
        r"BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY",
        re.I
    )),
    ("generic_token", re.compile(
        r"\b(access_token|refresh_token|bearer_token)\b\s*[:=]\s*['\"]?([^\s'\"\n]{20,})",
        re.I
    )),
    ("password_literal", re.compile(
        r"\bpassword\b\s*[:=]\s*['\"]([^'\"]{4,})['\"]",
        re.I
    )),
    ("long_hex_string", re.compile(
        r"['\"]([a-fA-F0-9]{32,})['\"]"
    )),
    ("long_base64", re.compile(
        r"['\"]([A-Za-z0-9+/]{40,}={0,2})['\"]"
    )),
]

# Placeholder patterns (must trigger STOP if combined with secret context)
PLACEHOLDERS = re.compile(r"\b(your_|placeholder|changeme|xxx|todo)\b", re.I)

# Safe patterns to ignore
SAFE_PATTERNS = [
    r"os\.getenv",
    r"os\.environ",
    r"\.get\(",
    r"secrets_loader",
    r"load_dotenv",
    r"# example",
    r"# placeholder",
    r"\.env\.template",
]


@dataclass(frozen=True)
class Finding:
    """Single secret finding."""
    path: Path
    line_no: int
    kind: str
    snippet: str
    is_placeholder: bool = False


def iter_files(root: Path) -> Iterable[Path]:
    """Iterate over scannable files."""
    for p in root.rglob("*"):
        if p.is_dir():
            continue
        if any(part in SKIP_DIR_NAMES for part in p.parts):
            continue
        if p.suffix.lower() in SKIP_FILE_SUFFIXES:
            continue
        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        yield p


def mask_value(text: str) -> str:
    """Mask potential secret values in output."""
    # Mask anything that looks like key=value or "key":"value"
    text = re.sub(
        r"([:=]\s*['\"]?)([A-Za-z0-9_\-+/]{8,})(['\"]?)",
        r"\1***MASKED***\3",
        text
    )
    return text[:100]  # Truncate for display


def is_safe_line(line: str) -> bool:
    """Check if line is safe (env lookup, not hardcoded)."""
    for pattern in SAFE_PATTERNS:
        if re.search(pattern, line, re.I):
            return True
    return False


def scan_file(path: Path) -> List[Finding]:
    """Scan single file for secrets."""
    findings: List[Finding] = []

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return findings

    for line_no, line in enumerate(content.splitlines(), start=1):
        # Skip safe patterns
        if is_safe_line(line):
            continue

        for kind, pattern in PATTERNS:
            if pattern.search(line):
                is_placeholder = bool(PLACEHOLDERS.search(line))

                # Skip if it's clearly a template/example
                if ".template" in str(path) or "example" in str(path).lower():
                    continue

                findings.append(Finding(
                    path=path,
                    line_no=line_no,
                    kind=kind,
                    snippet=mask_value(line.strip()),
                    is_placeholder=is_placeholder,
                ))
                break  # One finding per line is enough

    return findings


def print_remediation() -> None:
    """Print remediation steps."""
    print("""
=== REMEDIATION STEPS ===

1. Create secrets directory (outside repo):
   mkdir C:\\secrets\\hope

2. Move secrets to .env file:
   C:\\secrets\\hope\\.env

3. Update code to use secrets_loader:
   from core.secrets_loader import SecretsLoader
   secrets = SecretsLoader.load()
   token = secrets.get_required('TELEGRAM_TOKEN')

4. Remove secrets from git history:
   git filter-branch --force --index-filter \\
     'git rm --cached --ignore-unmatch path/to/secret/file' \\
     --prune-empty --tag-name-filter cat -- --all

5. Force push (DANGER - coordinate with team):
   git push origin --force --all

6. Rotate ALL exposed credentials immediately!
""")


def main() -> int:
    """Main scanner entry point."""
    fix_mode = "--fix" in sys.argv

    if not ROOT.exists():
        print(f"[ERR] Root not found: {ROOT}", file=sys.stderr)
        return 3

    findings: List[Finding] = []
    scanned_files = 0

    print(f"Scanning: {ROOT}")
    print("=" * 60)

    for file_path in iter_files(ROOT):
        scanned_files += 1
        file_findings = scan_file(file_path)
        findings.extend(file_findings)

    print(f"Scanned {scanned_files} files")
    print()

    if not findings:
        print("[OK] No secret-like patterns found.")
        return 0

    # Separate real secrets from placeholders
    real_secrets = [f for f in findings if not f.is_placeholder]
    placeholders = [f for f in findings if f.is_placeholder]

    print(f"[STOP] Found {len(findings)} potential issues:")
    print(f"  - Real secrets: {len(real_secrets)}")
    print(f"  - Placeholders: {len(placeholders)}")
    print()

    if real_secrets:
        print("=== REAL SECRETS (CRITICAL) ===")
        for finding in real_secrets:
            rel = finding.path.relative_to(ROOT)
            print(f"  {rel}:{finding.line_no} [{finding.kind}]")
            print(f"    {finding.snippet}")
        print()

    if placeholders:
        print("=== PLACEHOLDERS (must be replaced) ===")
        for finding in placeholders[:10]:  # Limit output
            rel = finding.path.relative_to(ROOT)
            print(f"  {rel}:{finding.line_no} [{finding.kind}]")
        if len(placeholders) > 10:
            print(f"  ... and {len(placeholders) - 10} more")
        print()

    if fix_mode:
        print_remediation()

    print("Action: Move secrets to C:\\secrets\\hope\\.env")
    print("        Replace hardcoded values with env lookups")
    print("        Purge from git history if committed")

    return 2  # Fail-closed: secrets found


if __name__ == "__main__":
    raise SystemExit(main())
