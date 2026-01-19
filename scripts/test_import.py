#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test spot_testnet_client import."""

import sys
import traceback
from pathlib import Path

# Add minibot to path
BASE_DIR = Path(r"C:\Users\kirillDev\Desktop\TradingBot\minibot")
sys.path.insert(0, str(BASE_DIR))

print("Python:", sys.executable)
print("Base dir:", BASE_DIR)

try:
    print("Importing spot_testnet_client...")
    from core.spot_testnet_client import SpotTestnetClient
    print("Import OK")

    print("Creating client...")
    client = SpotTestnetClient()
    print("Client created")

    print("Running health check...")
    result = client.health_check()
    print("Health result:", result)

    import json
    with open("state/spot_health.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print("Saved to state/spot_health.json")

except Exception as e:
    print("ERROR:", e)
    traceback.print_exc()
