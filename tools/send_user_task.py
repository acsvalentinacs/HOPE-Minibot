#!/usr/bin/env python3
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-20 12:00:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 14:00:00 UTC
# === END SIGNATURE ===
"""
Send User Task CLI

Sends a user task to GPT orchestrator via IPC.

Usage:
    # Send chat task with natural language
    python -m tools.send_user_task "сколько будет 1+1?"

    # Send compute task with expression
    python -m tools.send_user_task --type compute "2 * 3 + 4"

Output:
    Prints message ID for correlation.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from core.sha256_id import sha256_id
from core.ipc_fs import get_ipc


def send_chat_task(message: str) -> str:
    """
    Send chat task to GPT orchestrator.

    Args:
        message: User message (can be natural language)

    Returns:
        Message ID
    """
    # IPC v2.1 strict - timestamp (not timestamp_unix)
    msg = {
        "from": "user",
        "to": "gpt",
        "type": "task",
        "timestamp": time.time(),
        "payload": {
            "task_type": "chat",
            "message": message,
        },
    }
    msg["id"] = sha256_id(msg)

    ipc = get_ipc()
    ipc.write_message("gpt_inbox", msg)

    return msg["id"]


def send_compute_task(expression: str) -> str:
    """
    Send compute task to GPT orchestrator.

    Args:
        expression: Arithmetic expression

    Returns:
        Message ID
    """
    # IPC v2.1 strict - timestamp (not timestamp_unix)
    msg = {
        "from": "user",
        "to": "gpt",
        "type": "task",
        "timestamp": time.time(),
        "payload": {
            "task_type": "compute",
            "expression": expression,
        },
    }
    msg["id"] = sha256_id(msg)

    ipc = get_ipc()
    ipc.write_message("gpt_inbox", msg)

    return msg["id"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send user task to GPT orchestrator"
    )
    parser.add_argument(
        "message",
        help="Message or expression (e.g., 'сколько 1+1?' or '2 * 3')",
    )
    parser.add_argument(
        "--type",
        choices=["chat", "compute"],
        default="chat",
        help="Task type (default: chat)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON",
    )
    args = parser.parse_args()

    try:
        if args.type == "compute":
            msg_id = send_compute_task(args.message)
        else:
            msg_id = send_chat_task(args.message)

        if args.json:
            print(json.dumps({
                "ok": True,
                "message_id": msg_id,
                "message": args.message,
                "task_type": args.type,
            }))
        else:
            print(f"Task sent: {msg_id}")
            print(f"Message: {args.message}")
            print(f"Type: {args.type}")
            print(f"Monitor: python -m core.gpt_orchestrator_runner --once")

        return 0

    except Exception as e:
        if args.json:
            print(json.dumps({"ok": False, "error": str(e)}))
        else:
            print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
