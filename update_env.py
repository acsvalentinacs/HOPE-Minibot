from pathlib import Path
import shutil
from datetime import datetime
import os

# Determine the path to the secrets .env file from an environment variable, with a safe default.
env_path_str = os.environ.get("HOPE_ENV_PATH", "C:/secrets/hope.env")
env_path = Path(env_path_str)

# Construct the backup path based on the configured env_path.
timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
backup_path = env_path.with_name(env_path.name + f".backup_{timestamp}")
# Backup
shutil.copy(env_path, backup_path)
print(f"Backup: {backup_path}")

# Read
lines = env_path.read_text().splitlines()

# Find MAINNET keys
mainnet_key = None
mainnet_secret = None
for line in lines:
print("MAINNET key found.")
        mainnet_key = line.split("=",1)[1].strip()
    if line.startswith("BINANCE_MAINNET_API_SECRET="):
        mainnet_secret = line.split("=",1)[1].strip()

print(f"MAINNET key found: {mainnet_key[:10]}...")

# Update BINANCE_API_KEY and BINANCE_API_SECRET with MAINNET values
new_lines = []
updated_key = False
updated_secret = False

for line in lines:
    if line.startswith("BINANCE_API_KEY=") and not updated_key:
        new_lines.append(f"BINANCE_API_KEY={mainnet_key}")
        updated_key = True
        print("Updated BINANCE_API_KEY")
    elif line.startswith("BINANCE_API_SECRET=") and not updated_secret:
        new_lines.append(f"BINANCE_API_SECRET={mainnet_secret}")
        updated_secret = True
        print("Updated BINANCE_API_SECRET")
    elif line.startswith("BINANCE_API_KEY=") or line.startswith("BINANCE_API_SECRET="):
        # Skip duplicates
        continue
    else:
        new_lines.append(line)

# Write
env_path.write_text("\n".join(new_lines))
print("DONE! .env updated with MAINNET keys")
