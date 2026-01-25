# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Modified at (UTC): 2026-01-25T19:30:00Z
# Purpose: Get latest response from Claude inbox (token moved to env)
# === END SIGNATURE ===
"""
Get latest message from Claude inbox.

Requires: HOPE_BRIDGE_TOKEN in environment or C:\\secrets\\hope\\.env
"""
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen


def load_bridge_token() -> str:
    """
    Load HOPE Bridge token from environment (fail-closed).

    Returns:
        Token string

    Raises:
        ValueError: If token not found
    """
    # Try environment first
    token = os.environ.get("HOPE_BRIDGE_TOKEN")
    if token:
        return token

    # Try .env file
    env_paths = [
        Path(r"C:\secrets\hope\.env"),
        Path(r"C:\secrets\hope.env"),
    ]

    for env_path in env_paths:
        if not env_path.exists():
            continue
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip() == "HOPE_BRIDGE_TOKEN":
                    return value.strip().strip('"').strip("'")
        except Exception:
            continue

    raise ValueError(
        "FAIL-CLOSED: HOPE_BRIDGE_TOKEN not found. "
        "Set in environment or add to C:\\secrets\\hope\\.env"
    )


def main():
    token = load_bridge_token()
    base_url = 'https://bridge.acsvalentinacs.com'

    # Get latest message from claude inbox
    req = Request(f'{base_url}/inbox/claude?limit=1&order=desc')
    req.add_header('X-HOPE-Token', token)
    with urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode('utf-8'))

    if data.get('messages'):
        msg = data['messages'][0]
        payload = msg.get('payload', {})
        message = payload.get('message', '') if isinstance(payload, dict) else str(payload)

        print(f"Reply-To: {msg.get('reply_to', '?')[:32]}...")
        print(f"Type: {msg.get('type')}")
        print()
        print("=== GPT RESPONSE ===")
        print(message)
    else:
        print("No messages found")


if __name__ == "__main__":
    main()
