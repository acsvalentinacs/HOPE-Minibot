# === AI SIGNATURE ===
# Created by: Kirill Dev
# Created at: 2026-01-19 18:24:32 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 17:10:00 UTC
# === END SIGNATURE ===
"""
GPT Bridge Runner - Autonomous GPT ↔ Claude communication daemon.

Polls Friend Bridge /inbox/gpt for messages from Claude,
processes them via OpenAI API, sends responses back via /send.

Architecture:
    VPS Process → poll /inbox/gpt → OpenAI API → POST /send (to=claude)

State Management:
    - Cursor (after) persisted atomically in state/gpt_runner_cursor.txt
    - Fail-closed: any error → skip cycle, log, retry next interval

Security:
    - OPENAI_API_KEY from env only (never logged)
    - FRIEND_BRIDGE_TOKEN from env only
    - All HTTP over localhost (Friend Bridge binds 127.0.0.1)

Usage:
    python -m core.gpt_bridge_runner [--dry-run] [--once]
"""
from __future__ import annotations

# HOPE-LAW-001: Policy bootstrap (output + network guards)
# NOTE: bootstrap() called in main() before any network activity

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from core.secrets import require_secret, redact
from core.audit import emit_startup_audit
from core.policy.bootstrap import bootstrap

_THIS_FILE = Path(__file__).resolve()
_CORE_DIR = _THIS_FILE.parent
_MINIBOT_DIR = _CORE_DIR.parent
_STATE_DIR = _MINIBOT_DIR / "state"

# Logger initialized in main() after bootstrap
logger: logging.Logger | None = None

VERSION = "1.3.0"  # Added ACK processing to clear pending_acks in IPC cursor
DEFAULT_POLL_INTERVAL = 30
DEFAULT_MODEL = "gpt-4o"
BRIDGE_BASE_URL = "http://127.0.0.1:8765"
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
CURSOR_FILE = _STATE_DIR / "gpt_runner_cursor.txt"
IPC_CURSOR_FILE = _STATE_DIR / "ipc_cursor_gpt-5.2.json"
MAX_MESSAGE_LEN = 4000


@dataclass
class Config:
    openai_api_key: str
    bridge_token: str
    model: str = DEFAULT_MODEL
    poll_interval: int = DEFAULT_POLL_INTERVAL
    max_retries: int = 3
    dry_run: bool = False


def _atomic_write(path: Path, content: str) -> None:
    """Atomic write: temp → fsync → replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _load_cursor() -> str:
    """Load cursor from state file, empty string if missing."""
    if CURSOR_FILE.exists():
        return CURSOR_FILE.read_text(encoding="utf-8").strip()
    return ""


def _save_cursor(cursor: str) -> None:
    """Atomically save cursor."""
    _atomic_write(CURSOR_FILE, cursor)


def _clear_pending_ack(acked_message_id: str) -> bool:
    """
    Remove message from pending_acks in IPC cursor file.

    This is needed because gpt_bridge_runner operates separately from IPCAgent,
    but both share the same cursor file for tracking ACKs.

    Args:
        acked_message_id: The message ID being acknowledged (reply_to value)

    Returns:
        True if successfully cleared, False otherwise
    """
    if not acked_message_id or not acked_message_id.startswith("sha256:"):
        logger.warning("Invalid acked_message_id: %s", acked_message_id[:30] if acked_message_id else "None")
        return False

    if not IPC_CURSOR_FILE.exists():
        logger.debug("IPC cursor file not found, nothing to clear")
        return True

    try:
        data = json.loads(IPC_CURSOR_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to read IPC cursor: %s", e)
        return False

    pending = data.get("pending_acks", {})
    resend = data.get("last_resend_attempt", {})

    if acked_message_id not in pending:
        logger.debug("Message %s not in pending_acks", acked_message_id[:24])
        return True

    del pending[acked_message_id]
    resend.pop(acked_message_id, None)
    data["pending_acks"] = pending
    data["last_resend_attempt"] = resend
    data["last_update"] = time.time()

    try:
        _atomic_write(IPC_CURSOR_FILE, json.dumps(data, indent=2, ensure_ascii=False))
        logger.info("Cleared pending_ack: %s", acked_message_id[:24])
        return True
    except OSError as e:
        logger.error("Failed to write IPC cursor: %s", e)
        return False


def _http_request(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    body: Optional[bytes] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    Execute HTTP request, return parsed JSON.

    Raises on HTTP errors or JSON decode failures.
    """
    req = Request(url, data=body, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)

    with urlopen(req, timeout=timeout) as resp:
        data = resp.read().decode("utf-8")
        return json.loads(data)


def poll_inbox(config: Config, after: str = "") -> tuple[List[Dict[str, Any]], str]:
    """
    Poll /inbox/gpt for new messages.

    Returns:
        Tuple of (messages list, next_after cursor)
    """
    url = f"{BRIDGE_BASE_URL}/inbox/gpt?limit=10"
    if after:
        url += f"&after={after}"

    headers = {"X-HOPE-Token": config.bridge_token}

    result = _http_request(url, headers=headers)

    if not result.get("ok"):
        raise RuntimeError(f"Inbox poll failed: {result.get('error')}")

    return result.get("messages", []), result.get("next_after", after)


def call_openai(config: Config, user_message: str, context: str = "", is_task_request: bool = False) -> str:
    """
    Call OpenAI API with user message.

    Args:
        config: Configuration
        user_message: User message text
        context: Additional context
        is_task_request: If True, generate structured task response

    Returns:
        Assistant response text (JSON for task_request)
    """
    if is_task_request:
        system_prompt = """You are GPT, the task coordinator in the HOPE trading system.
When Claude sends a task_request, respond with a structured task in JSON format:

{
  "description": "Clear, actionable task description",
  "acceptance_criteria": [
    "Criterion 1: what must be true",
    "Criterion 2: what must be true"
  ],
  "expected_artifacts": ["file1.py", "logs/output.log"],
  "verification_commands": ["python -m py_compile file1.py", "pytest tests/"]
}

Guidelines:
- Tasks should be specific and verifiable
- acceptance_criteria must be objectively measurable
- verification_commands must have exit_code=0 on success
- Keep tasks focused (1 clear objective)

Respond ONLY with valid JSON, no markdown or extra text."""
    else:
        system_prompt = (
            "You are GPT, an AI assistant collaborating with Claude in the HOPE trading system. "
            "Respond concisely and professionally. Focus on actionable insights."
        )
    if context and not is_task_request:
        system_prompt += f"\n\nContext: {context}"

    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": 1500 if is_task_request else 1000,
        "temperature": 0.3 if is_task_request else 0.7,
    }

    headers = {
        "Authorization": f"Bearer {config.openai_api_key}",
        "Content-Type": "application/json",
    }

    result = _http_request(
        OPENAI_API_URL,
        method="POST",
        headers=headers,
        body=json.dumps(payload).encode("utf-8"),
        timeout=60,
    )

    choices = result.get("choices", [])
    if not choices:
        raise RuntimeError("OpenAI returned no choices")

    return choices[0].get("message", {}).get("content", "")


def send_response(config: Config, message: str, reply_to: Optional[str] = None) -> bool:
    """
    Send legacy response to Claude via /send endpoint.

    Returns:
        True if successful
    """
    if len(message) > MAX_MESSAGE_LEN:
        message = message[:MAX_MESSAGE_LEN - 100] + "\n\n[truncated]"

    payload = {
        "to": "claude",
        "message": message,
        "context": "gpt_bridge_runner",
    }
    if reply_to:
        payload["reply_to"] = reply_to

    headers = {
        "X-HOPE-Token": config.bridge_token,
        "Content-Type": "application/json",
    }

    result = _http_request(
        f"{BRIDGE_BASE_URL}/send",
        method="POST",
        headers=headers,
        body=json.dumps(payload).encode("utf-8"),
    )

    return result.get("ok", False)


def send_structured_response(
    config: Config,
    msg_type: str,
    payload_data: Dict[str, Any],
    reply_to: Optional[str] = None,
) -> bool:
    """
    Send structured response to Claude via /send endpoint.

    Args:
        config: Configuration
        msg_type: Message type (task, response, ack)
        payload_data: Structured payload dict
        reply_to: Parent message ID

    Returns:
        True if successful
    """
    request_payload = {
        "to": "claude",
        "type": msg_type,
        "payload": payload_data,
    }
    if reply_to:
        request_payload["reply_to"] = reply_to

    headers = {
        "X-HOPE-Token": config.bridge_token,
        "Content-Type": "application/json",
    }

    result = _http_request(
        f"{BRIDGE_BASE_URL}/send",
        method="POST",
        headers=headers,
        body=json.dumps(request_payload).encode("utf-8"),
    )

    return result.get("ok", False)


def process_message(config: Config, msg: Dict[str, Any]) -> bool:
    """
    Process single message from inbox.

    Handles different message types:
    - task_request: Generate structured task with acceptance_criteria
    - result: Log outcome, optionally validate
    - (default): Legacy chat response

    Returns:
        True if processed successfully
    """
    import uuid

    msg_id = msg.get("id", "unknown")
    msg_type = msg.get("type", "").lower()
    payload = msg.get("payload", {})

    if isinstance(payload, str):
        user_text = payload
        context = ""
        correlation_id = ""
    elif isinstance(payload, dict):
        user_text = payload.get("message", payload.get("text", str(payload)))
        context = payload.get("context", "")
        correlation_id = payload.get("correlation_id", "")
    else:
        user_text = str(payload)
        context = ""
        correlation_id = ""

    logger.info("Processing [%s] message %s: %.50s...", msg_type or "legacy", msg_id, user_text)

    if config.dry_run:
        logger.info("[DRY-RUN] Would call OpenAI and send response")
        return True

    # === Handle ACK: Clear from pending_acks ===
    if msg_type == "ack":
        reply_to = msg.get("reply_to", "")
        if reply_to:
            cleared = _clear_pending_ack(reply_to)
            logger.info("ACK received for %s, cleared=%s", reply_to[:24], cleared)
            return cleared
        else:
            logger.warning("ACK message %s missing reply_to", msg_id[:24])
            return False

    # === Handle task_request: Generate structured task ===
    if msg_type == "task_request":
        response = call_openai(config, user_text, context, is_task_request=True)

        if not response:
            logger.error("Empty response from OpenAI for task_request %s", msg_id)
            return False

        # Parse JSON response from GPT
        try:
            task_data = json.loads(response)
        except json.JSONDecodeError:
            # If GPT didn't return valid JSON, wrap it
            logger.warning("GPT returned non-JSON for task_request, wrapping")
            task_data = {
                "description": response[:500],
                "acceptance_criteria": ["Task completed successfully"],
                "expected_artifacts": [],
                "verification_commands": [],
            }

        # Build task payload with correlation_id
        task_payload = {
            "correlation_id": correlation_id or str(uuid.uuid4()),
            "description": task_data.get("description", "Task from GPT"),
            "acceptance_criteria": task_data.get("acceptance_criteria", []),
            "expected_artifacts": task_data.get("expected_artifacts", []),
            "verification_commands": task_data.get("verification_commands", []),
        }

        success = send_structured_response(config, "task", task_payload, reply_to=msg_id)

        if success:
            logger.info("Sent task for %s: %s", msg_id, task_payload.get("description", "")[:50])
        else:
            logger.error("Failed to send task for %s", msg_id)

        return success

    # === Handle result: Log outcome ===
    elif msg_type == "result":
        outcome = payload.get("outcome", "unknown") if isinstance(payload, dict) else "unknown"
        corr_id = payload.get("correlation_id", "") if isinstance(payload, dict) else ""
        logger.info("Received result: correlation_id=%s, outcome=%s", corr_id, outcome)

        # Optionally send acknowledgment
        ack_payload = {
            "correlation_id": corr_id,
            "status": "received",
            "outcome_logged": outcome,
        }
        success = send_structured_response(config, "ack", ack_payload, reply_to=msg_id)
        return success

    # === Default: Legacy chat response ===
    else:
        if not user_text:
            logger.warning("Empty message payload, skipping: %s", msg_id)
            return False

        response = call_openai(config, user_text, context)

        if not response:
            logger.error("Empty response from OpenAI for message %s", msg_id)
            return False

        success = send_response(config, response, reply_to=msg_id)

        if success:
            logger.info("Sent response for %s: %.50s...", msg_id, response)
        else:
            logger.error("Failed to send response for %s", msg_id)

        return success


def run_cycle(config: Config) -> None:
    """
    Single poll-process-respond cycle.

    Fail-closed: any error logs and returns without updating cursor.

    Cursor format (v1.1.0+):
        "{timestamp}_{filename}" - timestamp-based for monotonic ordering
        This fixes the bug where sha256-prefixed filenames could be
        lexicographically smaller than cursor, causing message loss.
    """
    cursor = _load_cursor()
    logger.debug("Polling inbox, cursor=%s", cursor or "(empty)")

    messages, next_cursor = poll_inbox(config, cursor)

    if not messages:
        logger.debug("No new messages")
        return

    logger.info("Found %d new message(s)", len(messages))

    # Process all messages - fail-closed means we only update cursor
    # after ALL messages in batch are processed successfully
    all_success = True
    for msg in messages:
        if not process_message(config, msg):
            all_success = False
            # Continue processing other messages, but don't advance cursor past failure

    # Update cursor to next_cursor from API (timestamp-based format)
    # Only if we successfully processed all messages
    if all_success and next_cursor and next_cursor != cursor:
        _save_cursor(next_cursor)
        logger.info("Cursor updated: %s", next_cursor)


def run_daemon(config: Config, once: bool = False) -> None:
    """
    Main daemon loop.

    Args:
        config: Runner configuration
        once: If True, run single cycle and exit
    """
    logger.info(
        "GPT Bridge Runner v%s starting (model=%s, interval=%ds, dry_run=%s)",
        VERSION, config.model, config.poll_interval, config.dry_run
    )

    while True:
        try:
            run_cycle(config)
        except (HTTPError, URLError) as e:
            logger.error("Network error: %s", e)
        except json.JSONDecodeError as e:
            logger.error("JSON decode error: %s", e)
        except Exception as e:
            logger.exception("Unexpected error in cycle: %s", e)

        if once:
            logger.info("Single cycle complete, exiting")
            break

        time.sleep(config.poll_interval)


def load_config_from_env() -> Config:
    """
    Load configuration from environment variables.

    Required (loaded from secure storage):
        OPENAI_API_KEY
        FRIEND_BRIDGE_TOKEN

    Optional:
        GPT_MODEL (default: gpt-4o)
        POLL_INTERVAL_SEC (default: 30)
        MAX_RETRIES (default: 3)
    """
    # FAIL-CLOSED: require_secret raises if not found
    api_key = require_secret("OPENAI_API_KEY")
    bridge_token = require_secret("FRIEND_BRIDGE_TOKEN")

    logger.info(f"OpenAI API key: {redact(api_key)}")
    logger.info(f"Bridge token: {redact(bridge_token)}")

    return Config(
        openai_api_key=api_key,
        bridge_token=bridge_token,
        model=os.environ.get("GPT_MODEL", DEFAULT_MODEL),
        poll_interval=int(os.environ.get("POLL_INTERVAL_SEC", DEFAULT_POLL_INTERVAL)),
        max_retries=int(os.environ.get("MAX_RETRIES", "3")),
    )


def main() -> int:
    """CLI entrypoint."""
    # HOPE-LAW-001: Policy bootstrap MUST be first (before logging/network)
    bootstrap("gpt_bridge", network_profile="core")

    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    global logger
    logger = logging.getLogger("gpt_bridge_runner")

    parser = argparse.ArgumentParser(description="GPT Bridge Runner")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log actions without calling OpenAI or sending messages",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run single cycle and exit",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    config = load_config_from_env()
    config.dry_run = args.dry_run

    # Emit startup audit (records git commit, python version, config hash)
    emit_startup_audit(
        "gpt_bridge",
        config_public={
            "poll_interval": config.poll_interval,
            "model": config.model,
            "dry_run": config.dry_run,
        },
    )

    run_daemon(config, once=args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())

