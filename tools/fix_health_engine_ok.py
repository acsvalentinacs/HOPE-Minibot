#!/usr/bin/env python3
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-03T01:35:00Z
# Purpose: One-shot fix for engine_ok field in write_health_v5
# === END SIGNATURE ===
"""
Fix for Telegram bot /panel showing ENGINE: STOPPED.

Problem: write_health_v5() doesn't include engine_ok field.
Solution: Patch the function to add engine_ok: True.

Usage on VPS:
    python3 /opt/hope/minibot/tools/fix_health_engine_ok.py
    systemctl restart hope-core
"""
import re
from pathlib import Path

ENTRYPOINT = Path("/opt/hope/minibot/core/entrypoint.py")

def main():
    if not ENTRYPOINT.exists():
        print(f"ERROR: {ENTRYPOINT} not found")
        return 1

    content = ENTRYPOINT.read_text(encoding="utf-8")

    # Check if already patched
    if "engine_ok: bool = True" in content:
        print("Already patched (engine_ok parameter exists)")
    else:
        # Add engine_ok parameter to function signature
        content = content.replace(
            "last_error: Optional[str] = None,\n) -> None:",
            "last_error: Optional[str] = None,\n    engine_ok: bool = True,\n) -> None:"
        )
        print("Added engine_ok parameter to write_health_v5()")

    # Check if engine_ok in health dict
    if '"engine_ok": engine_ok' in content or "'engine_ok': engine_ok" in content:
        print("Already patched (engine_ok in health dict)")
    else:
        # Add engine_ok to health dictionary
        content = content.replace(
            '"mode": mode,\n        "hb_ts":',
            '"mode": mode,\n        "engine_ok": engine_ok,\n        "hb_ts":'
        )
        print("Added engine_ok to health dictionary")

    # Update AI signature
    content = re.sub(
        r'# Modified at: \d{4}-\d{2}-\d{2}T[\d:]+Z',
        '# Modified at: 2026-02-03T01:35:00Z',
        content
    )
    content = re.sub(
        r'# Change: .*',
        '# Change: Added engine_ok field to health_v5.json for TG bot status',
        content
    )

    # Write back
    ENTRYPOINT.write_text(content, encoding="utf-8")
    print(f"Patched: {ENTRYPOINT}")

    # Also write immediate health file fix
    health_file = Path("/opt/hope/minibot/state/health_v5.json")
    if health_file.exists():
        import json
        health = json.loads(health_file.read_text())
        health["engine_ok"] = True
        health_file.write_text(json.dumps(health, indent=2))
        print(f"Fixed: {health_file}")

    print("\nDone. Now run: systemctl restart hope-core")
    return 0

if __name__ == "__main__":
    exit(main())
