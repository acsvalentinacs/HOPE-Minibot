"""
Chat Dispatch Module - Single source of truth for IPC chat messaging.

This module provides the public API for sending chat messages through IPC.
Used by both friend_bridge_server and ipc_tools.

Does NOT expose IPC internals. Uses only the public contract.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

# Derive paths from this file location (portable)
_THIS_FILE = Path(__file__).resolve()
_CORE_DIR = _THIS_FILE.parent
_MINIBOT_DIR = _CORE_DIR.parent
_IPC_DIR = _MINIBOT_DIR / "ipc"

# IPC directories
CLAUDE_INBOX = _IPC_DIR / "claude_inbox"
GPT_INBOX = _IPC_DIR / "gpt_inbox"


class Recipient(str, Enum):
    """Valid message recipients."""
    CLAUDE = "claude"
    GPT = "gpt"


class MessageType(str, Enum):
    """IPC message types."""
    TASK = "task"
    RESPONSE = "response"
    ACK = "ack"
    ERROR = "error"


@dataclass
class SendResult:
    """Result of send_chat operation."""
    ok: bool
    ipc_id: str = ""
    to: str = ""
    stored_file: str = ""
    filename: str = ""
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "ok": self.ok,
            "ipc_id": self.ipc_id,
            "to": self.to,
            "stored_file": self.stored_file,
            "filename": self.filename,
            "error": self.error,
        }


# Message limits
MAX_MESSAGE_LEN = 4000
MIN_MESSAGE_LEN = 1
VALID_RECIPIENTS = {Recipient.CLAUDE.value, Recipient.GPT.value}

# Sender identities (per IPC v2.1)
SENDER_CLAUDE = "claude"
SENDER_GPT = "gpt-5.2"


def _atomic_write(path: Path, content: str) -> None:
    """
    Atomic write: temp -> fsync -> replace.

    This is the ONLY place we do atomic writes for chat dispatch.
    Compliant with CLAUDE.md CRITICAL RULE: FILE WRITING.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _json_canonical(obj: object) -> str:
    """
    Canonical JSON: sorted keys, no spaces, ensure_ascii=False.

    CRITICAL: Must match ipc_agent._json_canonical exactly for ID generation.
    Uses separators=(",", ":") to produce compact JSON without spaces.
    """
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _generate_message_id(msg_data: Dict[str, Any]) -> str:
    """
    Generate deterministic message ID from message fields.

    Format: sha256:<hex>

    CRITICAL: Uses _json_canonical for exact match with ipc_agent ID validation.
    """
    # Canonical fields for hashing (excluding 'id' itself)
    fields_for_hash = {
        "from": msg_data.get("from", ""),
        "to": msg_data.get("to", ""),
        "timestamp": msg_data.get("timestamp", 0),
        "type": msg_data.get("type", ""),
        "payload": msg_data.get("payload", {}),
    }
    canonical = _json_canonical(fields_for_hash)
    hash_hex = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{hash_hex}"


def _ensure_ipc_folders() -> None:
    """Ensure IPC folders exist."""
    CLAUDE_INBOX.mkdir(parents=True, exist_ok=True)
    GPT_INBOX.mkdir(parents=True, exist_ok=True)


def send_chat(
    to: str,
    message: str,
    context: str = "friend_chat",
) -> SendResult:
    """
    Send chat message to Claude or GPT via IPC.

    This is the single source of truth for chat message dispatch.
    Both friend_bridge_server and ipc_tools.cmd_chat should use this.

    Args:
        to: Recipient ("claude" or "gpt")
        message: Message text (1-4000 chars)
        context: Context identifier (default: "friend_chat")

    Returns:
        SendResult with success status, ipc_id, stored_file path
    """
    # Validate recipient
    to_lower = to.lower()
    if to_lower not in VALID_RECIPIENTS:
        return SendResult(
            ok=False,
            error=f"Invalid recipient: {to}. Must be one of: {VALID_RECIPIENTS}",
        )

    # Validate message
    if not message or len(message) < MIN_MESSAGE_LEN:
        return SendResult(ok=False, error="Message is empty")

    if len(message) > MAX_MESSAGE_LEN:
        return SendResult(
            ok=False,
            error=f"Message too long ({len(message)} chars, max {MAX_MESSAGE_LEN})",
        )

    # Ensure folders exist
    _ensure_ipc_folders()

    # Build payload
    payload = {
        "task_type": "chat",
        "message": message,
        "context": context,
    }

    # Determine sender and inbox
    if to_lower == Recipient.CLAUDE.value:
        sender = SENDER_GPT
        recipient = SENDER_CLAUDE
        inbox = CLAUDE_INBOX
    else:
        sender = SENDER_CLAUDE
        recipient = SENDER_GPT
        inbox = GPT_INBOX

    # Build message
    ts = time.time()
    msg_data = {
        "from": sender,
        "to": recipient,
        "timestamp": ts,
        "type": MessageType.TASK.value,
        "payload": payload,
    }

    # Generate ID
    msg_id = _generate_message_id(msg_data)
    msg_data["id"] = msg_id

    # Generate filename
    filename = f"{msg_id[7:23]}_{int(ts * 1000) % 100000000:08x}.json"
    filepath = inbox / filename

    # Write atomically
    content = json.dumps(msg_data, ensure_ascii=False, sort_keys=True, indent=2)
    _atomic_write(filepath, content)

    return SendResult(
        ok=True,
        ipc_id=msg_id,
        to=to_lower,
        stored_file=str(filepath),
        filename=filename,
    )


def get_ipc_status() -> Dict[str, Any]:
    """
    Get IPC system status (read-only, no agent instantiation).

    Returns basic file counts without importing heavy agent modules.
    """
    _ensure_ipc_folders()

    claude_inbox_count = len(list(CLAUDE_INBOX.glob("*.json")))
    gpt_inbox_count = len(list(GPT_INBOX.glob("*.json")))

    claude_outbox = _IPC_DIR / "claude_outbox"
    gpt_outbox = _IPC_DIR / "gpt_outbox"
    deadletter = _IPC_DIR / "deadletter"

    claude_outbox_count = len(list(claude_outbox.glob("*.json"))) if claude_outbox.exists() else 0
    gpt_outbox_count = len(list(gpt_outbox.glob("*.json"))) if gpt_outbox.exists() else 0
    deadletter_count = len(list(deadletter.glob("*"))) if deadletter.exists() else 0

    return {
        "ok": True,
        "healthy": deadletter_count == 0,
        "claude": {
            "inbox_pending": claude_inbox_count,
            "outbox_pending": claude_outbox_count,
        },
        "gpt": {
            "inbox_pending": gpt_inbox_count,
            "outbox_pending": gpt_outbox_count,
        },
        "deadletter": deadletter_count,
    }


# In-memory cache for last sent message (for /last_sent endpoint)
_last_sent: Optional[SendResult] = None


def send_chat_tracked(
    to: str,
    message: str,
    context: str = "friend_chat",
) -> SendResult:
    """
    Send chat message and track it for /last_sent endpoint.

    Same as send_chat() but also stores result in module-level cache.
    """
    global _last_sent
    result = send_chat(to, message, context)
    if result.ok:
        _last_sent = result
    return result


def get_last_sent() -> Optional[Dict[str, Any]]:
    """
    Get last successfully sent message info.

    Returns None if no message has been sent in this process lifetime.
    """
    if _last_sent is None:
        return None
    return _last_sent.to_dict()
