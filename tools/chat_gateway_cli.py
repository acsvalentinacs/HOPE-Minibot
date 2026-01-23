# === AI SIGNATURE ===
# Created by: Claude
# Created at: 2026-01-21 14:00:00 UTC
# Modified by: Claude
# Modified at: 2026-01-21 18:50:00 UTC
# === END SIGNATURE ===
"""
Chat Gateway CLI v1.2

Interactive CLI for testing GPT->Claude IPC pipeline.
Sends user tasks to gpt_inbox, waits for responses in claude_inbox.

v1.2 changes:
- Added source attribution [GPT] / [Claude] in responses

v1.1 changes:
- Fixed reply_to correlation (cache-based matching)
- Support multiple response formats (message/text/answer/result)
- Drain inbox to cache to avoid stale responses

Usage:
    python -m tools.chat_gateway_cli

    > 1+1
    2

    > привет
    Привет! Как я могу помочь?

    > exit
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

# Add parent to path for imports
_MINIBOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_MINIBOT_DIR))

from core.ipc_fs import get_ipc, IPCError, IPCFileSystem
from core.sha256_id import sha256_id


# Constants
INPUT_INBOX = "gpt_inbox"        # Where we send user tasks
OUTPUT_INBOX = "claude_inbox"    # Where we receive responses
DEFAULT_TIMEOUT_S = 30.0

# Response cache: reply_to -> message
_response_cache: Dict[str, Dict[str, Any]] = {}


def _now_ts() -> float:
    return time.time()


def _make_user_task(message: str) -> dict:
    """Create user task message for gpt_inbox."""
    msg = {
        "from": "user",
        "to": "gpt",
        "type": "task",
        "timestamp": _now_ts(),
        "payload": {
            "task_type": "chat",
            "message": message,
            "context": "cli",
        },
    }
    msg["id"] = sha256_id(msg)
    return msg


def _extract_text(payload: Dict[str, Any]) -> str:
    """
    Extract text from response payload.

    Supports multiple schemas:
    - {"ok": True, "message": "..."}
    - {"ok": True, "text": "..."}
    - {"ok": True, "answer": "..."}
    - {"ok": True, "result": "..."}
    - {"ok": False, "error": "..."}
    """
    ok = bool(payload.get("ok", False))

    if ok:
        # Try multiple fields for success response
        for key in ("message", "text", "answer", "result", "result_text"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            # Handle numeric results
            if isinstance(val, (int, float)):
                return str(val)
        return ""
    else:
        # Error response
        for key in ("error", "message", "text"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return "Unknown error"


def _drain_inbox_to_cache(ipc: IPCFileSystem, inbox: str, cache: Dict[str, Dict[str, Any]], limit: int = 50) -> None:
    """
    Read all messages from inbox and store in cache by reply_to.

    ACK (delete) immediately to avoid stale message buildup.
    """
    batch = ipc.read_inbox(inbox, limit=limit)

    for msg in batch:
        msg_id = msg.get("id", "")
        payload = msg.get("payload", {})

        if isinstance(payload, dict):
            reply_to = payload.get("reply_to", "")
            if reply_to:
                cache[reply_to] = msg

        # Always delete to avoid stale messages
        try:
            ipc.delete_message(inbox, msg_id)
        except Exception:
            pass


def _wait_for_reply(
    ipc: IPCFileSystem,
    inbox: str,
    cache: Dict[str, Dict[str, Any]],
    task_id: str,
    timeout_s: float,
) -> Optional[Dict[str, Any]]:
    """
    Wait for response with matching reply_to.

    Uses cache to correlate responses correctly.
    """
    start = time.time()

    # Check cache first (response might already be there)
    if task_id in cache:
        return cache.pop(task_id)

    while time.time() - start < timeout_s:
        # Drain inbox to cache
        _drain_inbox_to_cache(ipc, inbox, cache, limit=50)

        # Check if our response arrived
        if task_id in cache:
            return cache.pop(task_id)

        time.sleep(0.1)

    return None  # Timeout


def send_and_wait(message: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> Optional[str]:
    """
    Send user task and wait for response.

    Args:
        message: User message
        timeout_s: Timeout in seconds

    Returns:
        Response text with source attribution, or None on timeout
    """
    global _response_cache
    ipc = get_ipc()

    # Create and send task
    task = _make_user_task(message)
    task_id = task["id"]

    try:
        ipc.write_message(INPUT_INBOX, task)
        print(f"[sent] {task_id[:20]}...")
    except IPCError as e:
        print(f"[error] Failed to send: {e}")
        return None

    # Wait for response with correct correlation
    resp_msg = _wait_for_reply(ipc, OUTPUT_INBOX, _response_cache, task_id, timeout_s)

    if resp_msg is None:
        return None  # Timeout

    # Extract source (who answered)
    msg_from = resp_msg.get("from", "unknown")
    source_label = {
        "gpt": "[GPT]",
        "claude": "[Claude]",
        "orchestrator": "[Orchestrator]",
    }.get(msg_from, f"[{msg_from}]")

    payload = resp_msg.get("payload", {})
    if not isinstance(payload, dict):
        return f"{source_label} [error] Invalid payload"

    text = _extract_text(payload)
    ok = bool(payload.get("ok", False))

    if ok:
        result = text if text else "[empty response]"
        return f"{source_label} {result}"
    else:
        return f"{source_label} [error] {text}"


def run_interactive() -> None:
    """Run interactive CLI loop."""
    print("=" * 50)
    print("HOPE Chat Gateway CLI v1.2")
    print("=" * 50)
    print("Type messages to send to GPT->Claude pipeline.")
    print("Commands: exit, quit, /status, /clear")
    print()

    ipc = get_ipc()

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
            print("Bye!")
            break

        if user_input == "/status":
            print(f"  gpt_inbox: {ipc.count_messages('gpt_inbox')} messages")
            print(f"  claude_agent_inbox: {ipc.count_messages('claude_agent_inbox')} messages")
            print(f"  claude_inbox: {ipc.count_messages('claude_inbox')} messages")
            print(f"  deadletter: {ipc.count_messages('deadletter')} messages")
            print(f"  response_cache: {len(_response_cache)} entries")
            continue

        if user_input == "/clear":
            # Clear inbox
            responses = ipc.read_inbox(OUTPUT_INBOX, limit=100)
            for r in responses:
                ipc.delete_message(OUTPUT_INBOX, r.get("id", ""))
            # Clear cache (modify global dict in-place)
            _response_cache.clear()
            print(f"Cleared {len(responses)} messages + cache")
            continue

        # Send message and wait
        print("[waiting...]")
        response = send_and_wait(user_input, timeout_s=DEFAULT_TIMEOUT_S)

        if response is None:
            print("[timeout] No response within 30s. Are runners started?")
            print("  Start: python -m core.gpt_orchestrator_runner")
            print("  Start: python -m core.claude_executor_runner")
        else:
            print(response)

        print()


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Chat Gateway CLI v1.2 - Interactive IPC testing"
    )
    parser.add_argument(
        "--message", "-m",
        type=str,
        help="Send single message and exit",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help=f"Response timeout in seconds (default: {DEFAULT_TIMEOUT_S})",
    )
    args = parser.parse_args()

    if args.message:
        # Single message mode
        response = send_and_wait(args.message, timeout_s=args.timeout)
        if response is None:
            print("[timeout]")
            return 1
        print(response)
        return 0

    # Interactive mode
    run_interactive()
    return 0


if __name__ == "__main__":
    exit(main())
