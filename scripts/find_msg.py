# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Modified at (UTC): 2026-01-25T19:30:00Z
# Purpose: Find message in GPT inbox (token moved to env)
# === END SIGNATURE ===
"""
Find message in GPT inbox.

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

    # Find my message in GPT inbox
    my_id = 'sha256:a21239ed'

    req = Request(f'{base_url}/inbox/gpt?limit=200&order=desc')
    req.add_header('X-HOPE-Token', token)
    with urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode('utf-8'))

    found = False
    for msg in data.get('messages', []):
        if my_id in msg.get('id', ''):
            print('FOUND MY MESSAGE!')
            print(json.dumps(msg, indent=2, ensure_ascii=False))
            found = True
            break

    if not found:
        print(f'Message not found. Total: {len(data.get("messages", []))}')
        # Show first message to see format
        if data.get('messages'):
            print('First message:', data['messages'][0].get('id', '?')[:30])


if __name__ == "__main__":
    main()
