#!/usr/bin/env python3
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-20 12:00:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 14:00:00 UTC
# === END SIGNATURE ===
"""
E2E Orchestrated Test

Tests the full Split-Inbox flow:
    User -> GPT Orchestrator -> Claude Executor -> GPT Orchestrator -> User

Usage:
    python -m tools.e2e_orchestrated

    # With natural language
    python -m tools.e2e_orchestrated --msg "сколько будет 1+1?"

    # With expression only
    python -m tools.e2e_orchestrated --expr "2 * 3 + 4"

    # With timeout
    python -m tools.e2e_orchestrated --timeout 30

Flow:
    1. Send user task to gpt_inbox
    2. Run orchestrator --once (extracts expression, routes to claude_agent_inbox)
    3. Run executor --once (computes, writes to gpt_inbox)
    4. Run orchestrator --once (creates final response in claude_inbox)
    5. Read response from claude_inbox

Exit codes:
    0: PASS - full flow completed
    1: FAIL - flow failed
    2: ERROR - exception
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from core.ssot_cmdline import cmdline_sha256
from core.sha256_id import sha256_id
from core.ipc_fs import get_ipc
from core.safe_math import safe_eval
from core.gpt_orchestrator_runner import extract_expression


def run_e2e(message: str, expected_expr: str = None, timeout_s: float = 10.0) -> dict:
    """
    Run full E2E test.

    Args:
        message: User message (can be natural language)
        expected_expr: Expected extracted expression (optional)
        timeout_s: Timeout for entire flow

    Returns:
        Result dict with status and details
    """
    result = {
        "message": message,
        "extracted_expression": None,
        "expected": None,
        "actual": None,
        "status": "pending",
        "steps": [],
        "cmdline_sha256": cmdline_sha256(),
    }

    ipc = get_ipc()
    start = time.time()

    # Extract expression from message
    extracted = extract_expression(message)
    result["extracted_expression"] = extracted

    if not extracted:
        result["status"] = "fail"
        result["error"] = "No arithmetic expression found in message"
        return result

    # Calculate expected result
    try:
        result["expected"] = safe_eval(extracted)
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"Invalid expression: {e}"
        return result

    # Step 1: Send user task (IPC v2.1 strict - timestamp, no reply_to at top level)
    user_task = {
        "from": "user",
        "to": "gpt",
        "type": "task",
        "timestamp": time.time(),
        "payload": {
            "task_type": "chat",
            "message": message,
        },
    }
    user_task["id"] = sha256_id(user_task)

    ipc.write_message("gpt_inbox", user_task)
    result["steps"].append({
        "step": 1,
        "action": "send_user_task",
        "message_id": user_task["id"],
        "inbox": "gpt_inbox",
    })
    print(f"[1] User task sent: {user_task['id'][:50]}...")
    print(f"    Message: {message}")
    print(f"    Extracted: {extracted}")

    # Step 2: Run orchestrator (routes to Claude)
    from core.gpt_orchestrator_runner import run_once as orchestrator_once
    processed = orchestrator_once()
    result["steps"].append({
        "step": 2,
        "action": "orchestrator_route",
        "processed": processed,
    })
    print(f"[2] Orchestrator routed: {processed} messages")

    if processed == 0:
        result["status"] = "fail"
        result["error"] = "Orchestrator did not process user task"
        return result

    # Step 3: Run executor (computes)
    from core.claude_executor_runner import run_once as executor_once
    processed = executor_once()
    result["steps"].append({
        "step": 3,
        "action": "executor_compute",
        "processed": processed,
    })
    print(f"[3] Executor computed: {processed} messages")

    if processed == 0:
        result["status"] = "fail"
        result["error"] = "Executor did not process task"
        return result

    # Step 4: Run orchestrator (creates final response)
    processed = orchestrator_once()
    result["steps"].append({
        "step": 4,
        "action": "orchestrator_respond",
        "processed": processed,
    })
    print(f"[4] Orchestrator responded: {processed} messages")

    if processed == 0:
        result["status"] = "fail"
        result["error"] = "Orchestrator did not create final response"
        return result

    # Step 5: Read response from claude_inbox
    # reply_to is now inside payload, so we need to filter differently
    all_messages = ipc.read_inbox("claude_inbox", limit=100)
    matching = [m for m in all_messages if m.get("payload", {}).get("reply_to") == user_task["id"]]

    if not matching:
        result["status"] = "fail"
        result["error"] = "No response found in claude_inbox with matching payload.reply_to"
        return result

    response = matching[0]
    payload = response.get("payload", {})
    answer = payload.get("message")

    result["steps"].append({
        "step": 5,
        "action": "read_response",
        "response_id": response.get("id"),
        "answer": answer,
        "ok": payload.get("ok"),
    })
    print(f"[5] Response: {response.get('id', 'unknown')[:50]}...")

    # Verify
    if not payload.get("ok"):
        result["status"] = "fail"
        result["error"] = payload.get("error", "Response not ok")
        result["actual"] = None
        print(f"\n[FAIL] Error: {payload.get('error')}")
    else:
        try:
            result["actual"] = float(answer) if answer else None
        except (ValueError, TypeError):
            result["actual"] = answer

        if result["actual"] == result["expected"]:
            result["status"] = "pass"
            print(f"\n[PASS] {message} -> {extracted} = {result['actual']}")
        else:
            result["status"] = "fail"
            result["error"] = f"Expected {result['expected']}, got {result['actual']}"
            print(f"\n[FAIL] Expected {result['expected']}, got {result['actual']}")

    # Cleanup: delete response
    ipc.delete_message("claude_inbox", response.get("id"))

    result["duration_s"] = time.time() - start
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="E2E test for Split-Inbox orchestration"
    )
    parser.add_argument(
        "--msg",
        default=None,
        help="User message with natural language (e.g., 'сколько 1+1?')",
    )
    parser.add_argument(
        "--expr",
        default=None,
        help="Expression to test (e.g., '1 + 1')",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON",
    )
    args = parser.parse_args()

    # Determine message to test
    if args.msg:
        message = args.msg
    elif args.expr:
        message = args.expr
    else:
        message = "1 + 1"  # Default

    print("=" * 60)
    print("E2E Orchestrated Test v1.2")
    print("=" * 60)
    print(f"Message: {message}")
    print(f"cmdline_sha256: {cmdline_sha256()}")
    print()

    try:
        result = run_e2e(message, timeout_s=args.timeout)

        if args.json:
            print(json.dumps(result, indent=2))

        print()
        print("=" * 60)
        print(f"Result: {result['status'].upper()}")
        print(f"Duration: {result.get('duration_s', 0):.3f}s")
        print("=" * 60)

        if result["status"] == "pass":
            return 0
        elif result["status"] == "fail":
            return 1
        else:
            return 2

    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
