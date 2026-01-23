# Quick test script for sending messages to Bridge
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

def main():
    # Load token
    env_path = Path(r"C:\secrets\hope\.env")
    token = ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("FRIEND_BRIDGE_TOKEN="):
            token = line.split("=", 1)[1].strip().strip('"')
            break

    if not token:
        print("ERROR: FRIEND_BRIDGE_TOKEN not found")
        return 1

    # Message to send
    message = sys.argv[1] if len(sys.argv) > 1 else "Привет! Сколько будет 7 умножить на 8?"

    # Send message
    url = "https://bridge.acsvalentinacs.com/send"
    payload = {
        "to": "gpt",
        "type": "task",
        "payload": {
            "task_type": "chat",
            "message": message,
            "context": "test_cli"
        }
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=body, method="POST")
    req.add_header("X-HOPE-Token", token)
    req.add_header("Content-Type", "application/json; charset=utf-8")

    print(f"Sending: {message}")

    with urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        print(f"Result: {result}")

    return 0

if __name__ == "__main__":
    sys.exit(main())
