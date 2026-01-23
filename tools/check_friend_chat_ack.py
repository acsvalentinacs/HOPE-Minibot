# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-20 12:00:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 14:00:00 UTC
# === END SIGNATURE ===
"""
Friend Chat ACK Verification Tool.

Read-only diagnostic: verifies that ACK was recorded.
Uses TWO sources of truth:
1. friend_chat/acked_messages.json - Friend Chat's own ACK tracking (primary)
2. ipc_cursor_*.json pending_acks - IPC agent's tracking (secondary, may have race conditions)

Usage:
    python -m tools.check_friend_chat_ack
    python -m tools.check_friend_chat_ack --response-id sha256:abc123...

Exit codes:
    0 - ACK verified (response_id in acked_messages.json)
    1 - ACK NOT verified
    2 - Error (missing files, parse error)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = BASE_DIR / "state"
FRIEND_CHAT_DIR = STATE_DIR / "friend_chat"

IPC_CURSOR_PATTERNS = [
    "ipc_cursor_gpt*.json",
    "ipc_cursor_claude*.json",
]

FRIEND_CHAT_ACKS_FILE = FRIEND_CHAT_DIR / "acked_messages.json"


def load_json_safe(path: Path) -> Optional[Dict[str, Any]]:
    """Load JSON file, return None on error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def find_cursor_files() -> List[Path]:
    """Find all IPC cursor files."""
    files = []
    for pattern in IPC_CURSOR_PATTERNS:
        files.extend(STATE_DIR.glob(pattern))
    return sorted(files)


def get_all_pending_acks() -> Dict[str, Dict[str, Any]]:
    """
    Collect pending_acks from all cursor files.

    Returns:
        Dict mapping message_id to {cursor_file, timestamp}
    """
    result: Dict[str, Dict[str, Any]] = {}

    for cursor_file in find_cursor_files():
        data = load_json_safe(cursor_file)
        if not data:
            continue

        pending = data.get("pending_acks", {})
        for msg_id, ts in pending.items():
            result[msg_id] = {
                "cursor_file": cursor_file.name,
                "timestamp": ts,
            }

    return result


def get_response_id_from_artifacts() -> Optional[str]:
    """Extract response_id from matched_message.json artifact."""
    matched_path = FRIEND_CHAT_DIR / "matched_message.json"
    data = load_json_safe(matched_path)
    if not data:
        return None

    matched_msg = data.get("matched_message", {})
    return matched_msg.get("id")


def get_ack_info_from_artifacts() -> Optional[Dict[str, Any]]:
    """Get ACK info from ack_result.json artifact."""
    ack_path = FRIEND_CHAT_DIR / "ack_result.json"
    return load_json_safe(ack_path)


def get_friend_chat_acks() -> Dict[str, float]:
    """Load ACKed messages from Friend Chat tracking file."""
    data = load_json_safe(FRIEND_CHAT_ACKS_FILE)
    if not data:
        return {}
    return data.get("acked_messages", {})


def verify_ack(response_id: str) -> Tuple[bool, str]:
    """
    Verify that response_id was ACKed.

    Primary check: response_id in friend_chat/acked_messages.json
    Secondary info: pending_acks status in IPC cursor files

    Args:
        response_id: The message ID that should have been ACKed

    Returns:
        Tuple of (verified: bool, reason: str)
    """
    if not response_id:
        return False, "Empty response_id"

    if not response_id.startswith("sha256:"):
        return False, f"Invalid response_id format: {response_id[:20]}..."

    # Primary check: Friend Chat ACK tracking
    friend_chat_acks = get_friend_chat_acks()
    if response_id in friend_chat_acks:
        ack_time = friend_chat_acks[response_id]
        return True, f"OK: response_id found in acked_messages.json (acked at {ack_time})"

    # Secondary info: check IPC pending_acks
    all_pending = get_all_pending_acks()
    if response_id in all_pending:
        info = all_pending[response_id]
        return False, (
            f"FAIL: response_id NOT in acked_messages.json\n"
            f"  Also still in IPC pending_acks ({info['cursor_file']})"
        )

    return False, "FAIL: response_id not found in acked_messages.json (ACK not recorded)"


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Verify Friend Chat ACK removed message from pending_acks"
    )
    parser.add_argument(
        "--response-id",
        help="Specific response_id to check (default: from matched_message.json)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed output",
    )

    args = parser.parse_args()

    print("=" * 50)
    print("Friend Chat ACK Verification")
    print("=" * 50)

    # Get response_id
    response_id = args.response_id
    if not response_id:
        response_id = get_response_id_from_artifacts()
        if not response_id:
            print("[ERROR] No response_id provided and matched_message.json not found")
            return 2
        print(f"Source: matched_message.json")
    else:
        print(f"Source: command line")

    print(f"Response ID: {response_id}")
    print()

    # Get ACK info
    ack_info = get_ack_info_from_artifacts()
    if ack_info:
        print("ACK Info (from ack_result.json):")
        print(f"  ack_ok: {ack_info.get('ack_ok')}")
        print(f"  ack_ipc_id: {ack_info.get('ack_ipc_id', 'N/A')[:40]}...")
        print(f"  to_agent: {ack_info.get('to_agent')}")
        print()

    # Show all pending_acks if verbose
    if args.verbose:
        all_pending = get_all_pending_acks()
        print(f"All pending_acks ({len(all_pending)} total):")
        for msg_id, info in all_pending.items():
            marker = " <-- TARGET" if msg_id == response_id else ""
            print(f"  {msg_id[:40]}... ({info['cursor_file']}){marker}")
        print()

    # Verify
    verified, reason = verify_ack(response_id)

    print("Verification Result:")
    print(f"  {reason}")
    print()

    if verified:
        print("[PASS] ACK verified - resend loop should be closed")
        return 0
    else:
        print("[FAIL] ACK NOT verified - resend loop may continue")
        print()
        print("Possible causes:")
        print("  1. ACK sent to wrong agent (check to_agent)")
        print("  2. IPC agent not running to process ACK")
        print("  3. ACK reply_to does not match response_id")
        return 1


if __name__ == "__main__":
    sys.exit(main())
