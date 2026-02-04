from pathlib import Path
import shutil
from datetime import datetime
import os

env_path_str = os.getenv("HOPE_ENV_PATH")
if not env_path_str:
    raise RuntimeError("Environment variable HOPE_ENV_PATH is not set; cannot locate secrets file.")

env_path = Path(env_path_str)
backup = env_path.with_name(
    f"{env_path.name}.backup_limits_{datetime.now().strftime('%H%M%S')}"
)
shutil.copy(env_path, backup)
print(f"Backup: {backup}")

content = env_path.read_text()
lines = content.splitlines()
new_lines = []

updates = {
    "MAX_POSITION_USDT": "5",
    "MAX_CONCURRENT_POSITIONS": "20",
    "MAX_DAILY_LOSS_USDT": "50",
}

updated = set()
for line in lines:
    if "=" in line and not line.strip().startswith("#"):
        key = line.split("=")[0]
        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            updated.add(key)
            print(f"SET: {key}={updates[key]}")
        else:
            new_lines.append(line)
    else:
        new_lines.append(line)

# Add missing keys
for key, val in updates.items():
    if key not in updated:
        new_lines.append(f"{key}={val}")
        print(f"ADD: {key}={val}")

env_path.write_text("\n".join(new_lines))
print("\nLimits updated!")
