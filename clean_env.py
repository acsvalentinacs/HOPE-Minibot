from pathlib import Path
import shutil
from datetime import datetime

env_path = Path("C:/secrets/hope.env")
backup = Path(f"C:/secrets/hope.env.backup_clean_{datetime.now().strftime('%H%M%S')}")
shutil.copy(env_path, backup)
print(f"Backup: {backup}")

lines = env_path.read_text().splitlines()
seen = {}
clean_lines = []

# Приоритет LIVE значений
live_priority = {
    "BINANCE_TESTNET": "false",
    "HOPE_DRY_RUN": "0",
    "HOPE_MODE": "LIVE",
}

for line in lines:
    if "=" in line and not line.strip().startswith("#"):
        key = line.split("=")[0]
        if key in seen:
            # Дубликат - пропускаем
            continue
        # Если это критичный параметр - ставим LIVE значение
        if key in live_priority:
            clean_lines.append(f"{key}={live_priority[key]}")
            seen[key] = True
            print(f"SET: {key}={live_priority[key]}")
        else:
            clean_lines.append(line)
            seen[key] = True
    else:
        clean_lines.append(line)

env_path.write_text("\n".join(clean_lines))
print(f"\nCleaned! Removed {len(lines) - len(clean_lines)} duplicate lines")

# Verify
testnet = [l for l in clean_lines if l.startswith("BINANCE_TESTNET=")]
print(f"BINANCE_TESTNET now: {testnet}")
