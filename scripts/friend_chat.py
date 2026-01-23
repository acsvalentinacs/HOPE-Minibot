# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 21:30:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 21:30:00 UTC
# === END SIGNATURE ===
"""
Friend Chat - Direct Claude <-> GPT communication script.

This script allows Claude Code CLI to:
1. Send messages/tasks to GPT
2. Poll inbox for responses from GPT
3. Execute received tasks and send results

Usage:
    python scripts/friend_chat.py healthz
    python scripts/friend_chat.py inbox gpt
    python scripts/friend_chat.py inbox claude
    python scripts/friend_chat.py send "Hello GPT!"
    python scripts/friend_chat.py send-task "Create a function to calculate fibonacci"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Paths
_THIS_FILE = Path(__file__).resolve()
_SCRIPTS_DIR = _THIS_FILE.parent
_MINIBOT_DIR = _SCRIPTS_DIR.parent
_SECRETS_PATH = Path(r"C:\secrets\hope\.env")

# Defaults
DEFAULT_BRIDGE_URL = "https://bridge.acsvalentinacs.com"


def _load_secret(key: str) -> str:
    """Load secret from .env file."""
    if not _SECRETS_PATH.exists():
        return ""
    try:
        text = _SECRETS_PATH.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            k, _, v = stripped.partition("=")
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def _http_request(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    body: Optional[bytes] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Execute HTTP request, return parsed JSON."""
    req = Request(url, data=body, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)

    with urlopen(req, timeout=timeout) as resp:
        data = resp.read().decode("utf-8")
        return json.loads(data)


def cmd_healthz(base_url: str, token: str) -> int:
    """Check Friend Bridge health."""
    headers = {"X-HOPE-Token": token}
    result = _http_request(f"{base_url}/healthz", headers=headers)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def cmd_inbox(base_url: str, token: str, agent: str, limit: int = 10) -> int:
    """Show inbox messages for agent (gpt or claude)."""
    headers = {"X-HOPE-Token": token}
    url = f"{base_url}/inbox/{agent}?limit={limit}&order=desc"
    result = _http_request(url, headers=headers)

    print(f"=== {agent.upper()} INBOX ===")
    print(f"Count: {result.get('count', 0)}")
    print()

    for msg in result.get("messages", []):
        msg_id = msg.get("id", "?")[:24]
        msg_type = msg.get("type", "?")
        reply_to = msg.get("reply_to", "")[:24] if msg.get("reply_to") else "-"
        payload = msg.get("payload", {})

        if isinstance(payload, dict):
            task_type = payload.get("task_type", "")
            message = payload.get("message", "")[:60]
        else:
            task_type = ""
            message = str(payload)[:60]

        print(f"ID: {msg_id}")
        print(f"  Type: {msg_type}/{task_type}")
        print(f"  Reply-To: {reply_to}")
        print(f"  Message: {message}...")
        print()

    return 0


def cmd_send(base_url: str, token: str, message: str, to: str = "gpt") -> int:
    """Send chat message to agent."""
    headers = {
        "X-HOPE-Token": token,
        "Content-Type": "application/json",
    }

    payload = {
        "to": to,
        "type": "task",
        "payload": {
            "task_type": "chat",
            "message": message,
            "context": "claude_code_cli",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }

    result = _http_request(
        f"{base_url}/send",
        method="POST",
        headers=headers,
        body=json.dumps(payload).encode("utf-8"),
    )

    if result.get("ok"):
        ipc_id = result.get("ipc_id", "?")
        print(f"[OK] Message sent to {to}")
        print(f"IPC ID: {ipc_id}")
        return 0
    else:
        print(f"[FAIL] {result.get('error', 'Unknown error')}")
        return 1


def cmd_send_task(base_url: str, token: str, description: str, to: str = "gpt") -> int:
    """Send task_request to GPT for task generation."""
    headers = {
        "X-HOPE-Token": token,
        "Content-Type": "application/json",
    }

    correlation_id = str(uuid.uuid4())

    payload = {
        "to": to,
        "type": "task_request",
        "payload": {
            "message": description,
            "correlation_id": correlation_id,
            "context": "claude_code_cli",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }

    result = _http_request(
        f"{base_url}/send",
        method="POST",
        headers=headers,
        body=json.dumps(payload).encode("utf-8"),
    )

    if result.get("ok"):
        ipc_id = result.get("ipc_id", "?")
        print(f"[OK] Task request sent to {to}")
        print(f"IPC ID: {ipc_id}")
        print(f"Correlation ID: {correlation_id}")
        return 0
    else:
        print(f"[FAIL] {result.get('error', 'Unknown error')}")
        return 1


def cmd_poll(base_url: str, token: str, timeout_sec: int = 30, interval_sec: int = 2) -> int:
    """Poll claude inbox for new messages from GPT."""
    headers = {"X-HOPE-Token": token}

    print(f"Polling claude inbox (timeout={timeout_sec}s, interval={interval_sec}s)...")

    start = time.time()
    seen_ids = set()

    while time.time() - start < timeout_sec:
        url = f"{base_url}/inbox/claude?limit=20&order=desc"
        result = _http_request(url, headers=headers)

        for msg in result.get("messages", []):
            msg_id = msg.get("id", "")
            if msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)

            msg_type = msg.get("type", "?")
            payload = msg.get("payload", {})

            print(f"\n=== NEW MESSAGE ===")
            print(f"ID: {msg_id[:32]}...")
            print(f"Type: {msg_type}")

            if isinstance(payload, dict):
                if "message" in payload:
                    print(f"Message: {payload['message']}")
                if "description" in payload:
                    print(f"Task Description: {payload['description']}")
                if "acceptance_criteria" in payload:
                    print(f"Criteria: {payload['acceptance_criteria']}")
            else:
                print(f"Payload: {payload}")

            print("=" * 40)

        time.sleep(interval_sec)

    print(f"\nPolling complete. Seen {len(seen_ids)} messages.")
    return 0


def main() -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Friend Chat - Claude <-> GPT communication")
    subparsers = parser.add_subparsers(dest="command", help="Command")

    # healthz
    subparsers.add_parser("healthz", help="Check Friend Bridge health")

    # inbox
    p_inbox = subparsers.add_parser("inbox", help="Show inbox messages")
    p_inbox.add_argument("agent", choices=["gpt", "claude"], help="Agent inbox to show")
    p_inbox.add_argument("--limit", type=int, default=10, help="Max messages")

    # send
    p_send = subparsers.add_parser("send", help="Send chat message")
    p_send.add_argument("message", help="Message text")
    p_send.add_argument("--to", default="gpt", choices=["gpt", "claude"], help="Recipient")

    # send-task
    p_task = subparsers.add_parser("send-task", help="Send task request")
    p_task.add_argument("description", help="Task description")
    p_task.add_argument("--to", default="gpt", choices=["gpt", "claude"], help="Recipient")

    # poll
    p_poll = subparsers.add_parser("poll", help="Poll for new messages")
    p_poll.add_argument("--timeout", type=int, default=30, help="Timeout in seconds")
    p_poll.add_argument("--interval", type=int, default=2, help="Poll interval in seconds")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Load secrets
    token = _load_secret("FRIEND_BRIDGE_TOKEN")
    base_url = _load_secret("FRIEND_BRIDGE_URL") or DEFAULT_BRIDGE_URL

    if not token:
        print("[FAIL] FRIEND_BRIDGE_TOKEN not found in secrets")
        return 1

    # Execute command
    if args.command == "healthz":
        return cmd_healthz(base_url, token)
    elif args.command == "inbox":
        return cmd_inbox(base_url, token, args.agent, args.limit)
    elif args.command == "send":
        return cmd_send(base_url, token, args.message, args.to)
    elif args.command == "send-task":
        return cmd_send_task(base_url, token, args.description, args.to)
    elif args.command == "poll":
        return cmd_poll(base_url, token, args.timeout, args.interval)

    return 1


if __name__ == "__main__":
    sys.exit(main())
