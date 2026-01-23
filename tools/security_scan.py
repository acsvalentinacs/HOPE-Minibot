# === AI SIGNATURE ===
# Created by: Claude
# Created at: 2026-01-20 14:10:00 UTC
# === END SIGNATURE ===
"""
Security Scanner v1.0

Scans for potential token/secret leaks in:
1. PowerShell history
2. Project files
3. Environment variables

Usage:
    python -m tools.security_scan [--fix]
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import List, Tuple

# Patterns to detect potential leaks
# Note: patterns with {variable} are safe (f-strings), only hardcoded values are leaks
LEAK_PATTERNS = [
    # Hardcoded token patterns (actual leaks)
    (r"api\.telegram\.org/bot\d{8,}:", "Hardcoded Telegram token in URL"),
    (r"bot\d{10}:[A-Za-z0-9_-]{35}", "Full Telegram bot token"),
    (r"sk-[a-zA-Z0-9]{20,}", "OpenAI API key"),
    (r"sk-ant-[a-zA-Z0-9-]{20,}", "Anthropic API key"),
    (r"TELEGRAM_BOT_TOKEN\s*=\s*['\"]?\d{10}:", "Hardcoded Telegram token assignment"),
    (r"OPENAI_API_KEY\s*=\s*['\"]?sk-", "Hardcoded OpenAI key assignment"),
]

# Patterns that are FALSE POSITIVES (safe code patterns)
SAFE_PATTERNS = [
    r"\{.*token.*\}",        # f-string with token variable
    r"\{.*TOKEN.*\}",        # f-string with TOKEN variable
    r"\{self\.token\}",      # f-string with self.token
    r"\{config\.",           # f-string with config.xxx
    r"\.telegram\.org/bot\"", # String literal ending (for documentation)
    r"# Redact",             # Comment about redaction
]

# Files where secrets are ALLOWED (won't flag)
ALLOWED_FILES = {
    ".env",
    "secrets.py",
    "migrate_secrets.py",
    "security_scan.py",  # This file
}

# Extensions to scan
SCAN_EXTENSIONS = {".py", ".ps1", ".txt", ".log", ".json", ".md", ".cmd", ".bat"}


def get_ps_history_path() -> Path:
    """Get PowerShell history file path."""
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        return Path(appdata) / "Microsoft" / "Windows" / "PowerShell" / "PSReadLine" / "ConsoleHost_history.txt"
    return Path()


def _is_safe_line(line: str) -> bool:
    """Check if line matches safe patterns (false positives)."""
    for safe_pattern in SAFE_PATTERNS:
        if re.search(safe_pattern, line, re.IGNORECASE):
            return True
    return False


def scan_file(filepath: Path) -> List[Tuple[int, str, str]]:
    """
    Scan a single file for leak patterns.

    Returns:
        List of (line_number, pattern_name, redacted_line)
    """
    findings = []

    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
        lines = content.split("\n")

        for i, line in enumerate(lines, 1):
            # Skip safe patterns (f-strings with variables, etc.)
            if _is_safe_line(line):
                continue

            for pattern, name in LEAK_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    # Redact the actual secret
                    redacted = re.sub(r"(\d{10}:[A-Za-z0-9_-]{35})", "[REDACTED_TOKEN]", line)
                    redacted = re.sub(r"(sk-[a-zA-Z0-9]{5})[a-zA-Z0-9]+", r"\1...[REDACTED]", redacted)
                    findings.append((i, name, redacted[:100]))
                    break  # One finding per line

    except Exception as e:
        pass  # Skip unreadable files

    return findings


def scan_powershell_history() -> List[Tuple[int, str, str]]:
    """Scan PowerShell history for leaks."""
    hist_path = get_ps_history_path()

    if not hist_path.exists():
        print(f"PowerShell history not found at: {hist_path}")
        return []

    print(f"Scanning PowerShell history: {hist_path}")
    return scan_file(hist_path)


def scan_project(root: Path) -> dict:
    """
    Scan project directory for leaks.

    Returns:
        Dict of {filepath: [(line, pattern, content), ...]}
    """
    results = {}

    for filepath in root.rglob("*"):
        if not filepath.is_file():
            continue

        # Skip allowed files
        if filepath.name in ALLOWED_FILES:
            continue

        # Skip non-scannable extensions
        if filepath.suffix.lower() not in SCAN_EXTENSIONS:
            continue

        # Skip backup/temp files
        if ".bak" in filepath.name or "tmpclaude" in filepath.name:
            continue

        # Skip GOLDEN backups
        if "GOLDEN" in str(filepath):
            continue

        findings = scan_file(filepath)
        if findings:
            results[filepath] = findings

    return results


def main():
    print("=" * 60)
    print("HOPE Security Scanner v1.0")
    print("=" * 60)

    total_leaks = 0

    # 1. Scan PowerShell history
    print("\n[1/3] Scanning PowerShell history...")
    ps_findings = scan_powershell_history()
    if ps_findings:
        print(f"  [!] FOUND {len(ps_findings)} potential leaks in PS history!")
        for line, pattern, content in ps_findings[:5]:  # Show max 5
            print(f"    Line {line}: {pattern}")
        total_leaks += len(ps_findings)
        print(f"\n  Recommendation: Delete history file or review carefully")
    else:
        print("  [OK] PowerShell history clean")

    # 2. Scan project files
    print("\n[2/3] Scanning project files...")
    project_root = Path(__file__).parent.parent
    project_findings = scan_project(project_root)

    if project_findings:
        for filepath, findings in project_findings.items():
            rel_path = filepath.relative_to(project_root)
            print(f"  [!] {rel_path}:")
            for line, pattern, content in findings[:3]:
                print(f"      Line {line}: {pattern}")
            total_leaks += len(findings)
    else:
        print("  [OK] Project files clean")

    # 3. Check environment variables
    print("\n[3/3] Checking environment variables...")
    env_secrets = []
    for key, value in os.environ.items():
        if any(kw in key.upper() for kw in ["TOKEN", "KEY", "SECRET", "PASSWORD"]):
            if value and len(value) > 10:
                env_secrets.append(key)

    if env_secrets:
        print(f"  [i] Found {len(env_secrets)} secret-related env vars (expected if using env-based secrets):")
        for key in env_secrets[:5]:
            print(f"      {key} = [PRESENT]")
    else:
        print("  [OK] No secrets in environment (using keyring or .env)")

    # Summary
    print("\n" + "=" * 60)
    if total_leaks > 0:
        print(f"[WARNING] TOTAL POTENTIAL LEAKS: {total_leaks}")
        print("Review findings above and take action if needed.")
    else:
        print("[PASS] SECURITY SCAN PASSED - No leaks detected")
    print("=" * 60)

    return 0 if total_leaks == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
