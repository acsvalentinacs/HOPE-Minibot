# === AI SIGNATURE ===
# Created by: Claude
# Created at: 2026-01-21 12:00:00 UTC
# Modified by: Claude
# Modified at: 2026-01-21 16:10:00 UTC
# === END SIGNATURE ===
"""
Fix ALL .claude/settings*.json files to use bypassPermissions.

IMPORTANT: Format WITHOUT wildcards as per official docs!
- "Bash" NOT "Bash(*)"
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# Official format from Claude Code docs - NO wildcards!
CANONICAL_CONFIG = {
    "permissions": {
        "defaultMode": "bypassPermissions",
        "allow": [
            "Bash",
            "Read",
            "Write",
            "Edit",
            "Glob",
            "Grep",
            "WebFetch",
            "WebSearch",
            "Task",
            "TodoWrite",
            "NotebookEdit",
            "TaskOutput",
            "KillShell"
        ],
        "deny": []
    }
}

def find_claude_settings(root: Path) -> list[Path]:
    """Find all .claude/settings*.json files recursively."""
    results = []
    for dirpath, dirnames, filenames in os.walk(root):
        if ".claude" in dirpath:
            for fn in filenames:
                if fn.startswith("settings") and fn.endswith(".json"):
                    results.append(Path(dirpath) / fn)
    return results

def fix_settings_file(path: Path) -> tuple[bool, str]:
    """Fix a single settings file. Returns (success, message)."""
    try:
        # Read current content
        try:
            with open(path, "r", encoding="utf-8") as f:
                current = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            current = {}

        # Check if already canonical
        if current == CANONICAL_CONFIG:
            return True, "already correct"

        # Write canonical config
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(CANONICAL_CONFIG, f, indent=2, ensure_ascii=False)
            f.write("\n")

        return True, "fixed"
    except Exception as e:
        return False, f"error: {e}"

def main() -> int:
    # Search paths
    search_roots = [
        Path(r"C:\Users\kirillDev\.claude"),
        Path(r"C:\Users\kirillDev\.claude-worktrees"),
        Path(r"C:\Users\kirillDev\Desktop\TradingBot"),
    ]

    all_files: list[Path] = []
    for root in search_roots:
        if root.exists():
            if root.name == ".claude":
                # Direct .claude folder
                for fn in root.glob("settings*.json"):
                    all_files.append(fn)
            else:
                # Search recursively
                all_files.extend(find_claude_settings(root))

    # Remove duplicates
    all_files = list(set(all_files))
    all_files.sort()

    print(f"Found {len(all_files)} settings files\n")

    fixed = 0
    already_ok = 0
    errors = 0

    for path in all_files:
        success, msg = fix_settings_file(path)
        status = "[OK]" if success else "[FAIL]"

        if success and msg == "fixed":
            fixed += 1
            print(f"{status} {path} -> {msg}")
        elif success and msg == "already correct":
            already_ok += 1
        else:
            errors += 1
            print(f"{status} {path} -> {msg}")

    print(f"\n=== SUMMARY ===")
    print(f"Total files:     {len(all_files)}")
    print(f"Fixed:           {fixed}")
    print(f"Already correct: {already_ok}")
    print(f"Errors:          {errors}")

    if errors == 0:
        print("\n[SUCCESS] All settings files are now configured for bypassPermissions!")
        print("Format: WITHOUT wildcards (official docs format)")
    else:
        print(f"\n[WARNING] {errors} files could not be fixed")

    return 0 if errors == 0 else 1

if __name__ == "__main__":
    raise SystemExit(main())
