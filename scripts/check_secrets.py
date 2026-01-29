# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T13:10:00Z
# Purpose: Check available secrets keys
# === END SIGNATURE ===
"""Check what keys are available in secrets file."""
from pathlib import Path

secrets_path = Path(r"C:\secrets\hope.env")

if not secrets_path.exists():
    print(f"File not found: {secrets_path}")
else:
    print(f"Keys in {secrets_path}:")
    print("-" * 40)
    for line in secrets_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key = line.split("=")[0].strip()
            value = line.split("=", 1)[1].strip() if "=" in line else ""
            # Mask value
            if value:
                masked = value[:4] + "..." + value[-4:] if len(value) > 10 else "****"
            else:
                masked = "(empty)"
            print(f"  {key} = {masked}")
