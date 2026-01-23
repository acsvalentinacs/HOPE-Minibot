# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 21:00:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 21:37:00 UTC
# === END SIGNATURE ===
"""
GPT Orchestrator Runner - AI Mode Task Orchestration Daemon.

Polls Friend Bridge /inbox/gpt for task_request messages from Claude,
generates structured tasks via OpenAI API, sends them back via /send.

Architecture:
    Windows Process → poll /inbox/gpt → OpenAI API → POST /send (to=claude)

Modes:
    AI_MODE=AI: Full autonomous task generation via OpenAI
    AI_MODE=ECHO: Echo back without OpenAI (for testing)

State Management:
    - Cursor persisted atomically in state/orchestrator_cursor.txt
    - Fail-closed: any error → skip cycle, log, retry next interval

Security:
    - OPENAI_API_KEY from env only (never logged)
    - FRIEND_BRIDGE_TOKEN from env only
    - SSH tunnel: localhost:18765 → VPS:8765

Usage:
    python -m core.gpt_orchestrator_runner --poll-ms 500
    python -m core.gpt_orchestrator_runner --poll-ms 500 --dry-run
    python -m core.gpt_orchestrator_runner --poll-ms 500 --once
"""
from __future__ import annotations

# UTF-8 setup MUST be first (before any output)
from core.util.utf8_console import setup_utf8_console
setup_utf8_console()

# HOPE-LAW-001: Policy bootstrap (output + network guards)
# NOTE: bootstrap() called in main() before any network activity

import argparse
import json
import logging
import os
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Set
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Proto layer imports
from core.audit import emit_startup_audit
from core.policy.bootstrap import bootstrap

# Portable paths
_THIS_FILE = Path(__file__).resolve()
_CORE_DIR = _THIS_FILE.parent
_MINIBOT_DIR = _CORE_DIR.parent
_STATE_DIR = _MINIBOT_DIR / "state"

# Logger initialized in main() after bootstrap
logger: logging.Logger | None = None

VERSION = "1.0.0"  # Initial version
DEFAULT_POLL_INTERVAL_MS = 500
DEFAULT_MODEL = "gpt-4o"
DEFAULT_BRIDGE_URL = "http://127.0.0.1:18765"  # Fallback if no env
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
CURSOR_FILE = _STATE_DIR / "orchestrator_cursor.txt"
PROCESSED_IDS_FILE = _STATE_DIR / "orchestrator_processed.txt"
SECRETS_PATH = Path(r"C:\secrets\hope\.env")
MAX_MESSAGE_LEN = 4000
MAX_PROCESSED_IDS = 1000  # Keep last N processed IDs


@dataclass
class Config:
    """Orchestrator configuration."""
    openai_api_key: str
    bridge_token: str
    bridge_url: str = DEFAULT_BRIDGE_URL
    model: str = DEFAULT_MODEL
    poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS
    max_retries: int = 3
    dry_run: bool = False
    ai_mode: str = "AI"  # AI or ECHO


def _load_secret(key: str) -> str:
    """
    Load secret from .env file (fail-closed).

    Returns empty string if not found.
    """
    if not SECRETS_PATH.exists():
        return ""

    try:
        text = SECRETS_PATH.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            k, _, v = stripped.partition("=")
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    except Exception as e:
        logger.error("Failed to load secret %s: %s", key, e)

    return ""


def _redact(s: str) -> str:
    """
    Redact secret for logging.

    SECURITY: Never log partial key values - even first/last chars leak info.
    Only show presence and length category.
    """
    if not s:
        return "[ABSENT]"
    if len(s) < 20:
        return "[PRESENT:short]"
    if len(s) < 50:
        return "[PRESENT:medium]"
    return "[PRESENT:long]"


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


class ProcessedIdsBuffer:
    """
    Ring buffer for message deduplication with FIFO eviction.

    CRITICAL FIX: Uses deque for true insertion-order,
    NOT sorted(set)[-N:] which keeps lexicographically largest (WRONG!).
    """

    def __init__(self, maxlen: int = MAX_PROCESSED_IDS) -> None:
        self._ids: Deque[str] = deque(maxlen=maxlen)
        self._set: Set[str] = set()

    def add(self, msg_id: str) -> bool:
        """Add ID. Returns True if new, False if duplicate."""
        if msg_id in self._set:
            return False

        # If at capacity, remove oldest
        if len(self._ids) >= self._ids.maxlen:
            oldest = self._ids[0]
            self._set.discard(oldest)

        self._ids.append(msg_id)
        self._set.add(msg_id)
        return True

    def __contains__(self, msg_id: str) -> bool:
        return msg_id in self._set

    def __len__(self) -> int:
        return len(self._ids)

    def to_list(self) -> List[str]:
        """Return IDs in insertion order (oldest first)."""
        return list(self._ids)

    @classmethod
    def from_list(cls, ids: List[str], maxlen: int = MAX_PROCESSED_IDS) -> "ProcessedIdsBuffer":
        """Create buffer from list, keeping last maxlen entries."""
        buf = cls(maxlen)
        # Take only last maxlen to not exceed
        for msg_id in ids[-maxlen:]:
            buf.add(msg_id)
        return buf


def _load_processed_ids() -> ProcessedIdsBuffer:
    """Load processed IDs as ring buffer (preserves insertion order)."""
    if PROCESSED_IDS_FILE.exists():
        text = PROCESSED_IDS_FILE.read_text(encoding="utf-8")
        ids = [line.strip() for line in text.splitlines() if line.strip()]
        return ProcessedIdsBuffer.from_list(ids, MAX_PROCESSED_IDS)
    return ProcessedIdsBuffer(MAX_PROCESSED_IDS)


def _save_processed_ids(buf: ProcessedIdsBuffer) -> None:
    """Atomically save processed IDs (in insertion order)."""
    ids_list = buf.to_list()
    content = "\n".join(ids_list) + "\n" if ids_list else ""
    _atomic_write(PROCESSED_IDS_FILE, content)


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
    url = f"{config.bridge_url}/inbox/gpt?limit=10"
    if after:
        url += f"&after={after}"

    headers = {"X-HOPE-Token": config.bridge_token}

    result = _http_request(url, headers=headers)

    if not result.get("ok"):
        raise RuntimeError(f"Inbox poll failed: {result.get('error')}")

    return result.get("messages", []), result.get("next_after", after)


def call_openai_for_task(config: Config, user_message: str, context: str = "") -> Dict[str, Any]:
    """
    Call OpenAI API to generate structured task from task_request.

    Args:
        config: Configuration
        user_message: Task request message
        context: Additional context

    Returns:
        Structured task dict with description, acceptance_criteria, etc.
    """
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

Context about the request:
""" + (context or "No additional context")

    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": 1500,
        "temperature": 0.3,
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

    response_text = choices[0].get("message", {}).get("content", "")

    # Try to parse as JSON
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        # Wrap non-JSON response
        logger.warning("GPT returned non-JSON, wrapping as description")
        return {
            "description": response_text[:500],
            "acceptance_criteria": ["Task completed successfully"],
            "expected_artifacts": [],
            "verification_commands": [],
        }


def call_openai_for_chat(config: Config, user_message: str, context: str = "") -> str:
    """
    Call OpenAI API for chat response.

    Args:
        config: Configuration
        user_message: User message
        context: Additional context

    Returns:
        Response text
    """
    system_prompt = (
        "You are GPT, an AI assistant collaborating with Claude in the HOPE trading system. "
        "Respond concisely and professionally. Focus on actionable insights."
    )
    if context:
        system_prompt += f"\n\nContext: {context}"

    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": 1000,
        "temperature": 0.7,
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


def send_structured_response(
    config: Config,
    msg_type: str,
    payload_data: Dict[str, Any],
    reply_to: Optional[str] = None,
    to: str = "claude",
) -> bool:
    """
    Send structured response via /send endpoint.

    Args:
        config: Configuration
        msg_type: Message type (task, response, ack)
        payload_data: Structured payload dict
        reply_to: Parent message ID for correlation
        to: Recipient (claude, nexus, etc.)

    Returns:
        True if successful
    """
    request_payload = {
        "to": to,
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
        f"{config.bridge_url}/send",
        method="POST",
        headers=headers,
        body=json.dumps(request_payload).encode("utf-8"),
    )

    return result.get("ok", False)


def send_chat_response(
    config: Config,
    message: str,
    reply_to: Optional[str] = None,
    context: str = "gpt_orchestrator",
    to: str = "claude",
) -> bool:
    """
    Send chat response via /send endpoint.

    Routes responses to appropriate inbox:
    - to="claude": executor inbox (for task work)
    - to="nexus": NEXUS UI inbox (for operator chat)

    Args:
        config: Configuration
        message: Response message
        reply_to: Parent message ID for correlation
        context: Context identifier
        to: Recipient inbox (claude, nexus)

    Returns:
        True if successful
    """
    if len(message) > MAX_MESSAGE_LEN:
        message = message[:MAX_MESSAGE_LEN - 100] + "\n\n[truncated]"

    # Use structured mode for proper correlation via reply_to
    payload = {
        "task_type": "chat",
        "message": message,
        "context": context,
        "from": "gpt",  # Identify sender for NEXUS UI
    }

    request_data = {
        "to": to,
        "type": "task",
        "payload": payload,
    }
    if reply_to:
        request_data["reply_to"] = reply_to

    headers = {
        "X-HOPE-Token": config.bridge_token,
        "Content-Type": "application/json",
    }

    result = _http_request(
        f"{config.bridge_url}/send",
        method="POST",
        headers=headers,
        body=json.dumps(request_data).encode("utf-8"),
    )

    return result.get("ok", False)


def process_message(config: Config, msg: Dict[str, Any]) -> bool:
    """
    Process single message from inbox.

    Handles different message types:
    - task_request: Generate structured task with acceptance_criteria
    - chat/task with task_type=chat: Generate chat response
    - ack: Log acknowledgment
    - result: Log outcome

    Returns:
        True if processed successfully
    """
    msg_id = msg.get("id", "unknown")
    msg_type = (msg.get("type") or "").lower()
    payload = msg.get("payload", {})

    # Extract message content
    if isinstance(payload, str):
        user_text = payload
        context = ""
        correlation_id = ""
        task_type = ""
        msg_from = ""
    elif isinstance(payload, dict):
        user_text = payload.get("message", payload.get("text", str(payload)))
        context = payload.get("context", "")
        correlation_id = payload.get("correlation_id", "")
        task_type = payload.get("task_type", "")
        msg_from = payload.get("from", "")
    else:
        user_text = str(payload)
        context = ""
        correlation_id = ""
        task_type = ""
        msg_from = ""

    # CRITICAL FIX: Skip echo messages from Claude executor to prevent feedback loop.
    # These are legacy "[Claude Executor] Received:" messages that should NOT trigger
    # GPT responses. The executor now sends ACKs instead, but old messages may still exist.
    if user_text.startswith("[Claude Executor] Received:"):
        logger.debug("Skipping executor echo message: %s", msg_id[:24] if msg_id else "?")
        return True  # Mark as processed but don't respond

    # Also skip messages that are clearly internal/echo patterns
    if context == "claude_executor" or msg_from == "claude_executor":
        logger.debug("Skipping message from executor context: %s", msg_id[:24] if msg_id else "?")
        return True

    # Determine reply channel: ONLY trust context, NOT payload.from (spoofable!)
    # SECURITY: payload["from"] can be forged by any sender.
    # We ONLY trust context which is set by the originating UI.
    # This prevents conflicts with executor which owns inbox/claude
    if context == "nexus_ui":
        reply_to_channel = "nexus"
    else:
        reply_to_channel = "claude"

    # Log warning if from/context mismatch (potential spoofing attempt)
    if msg_from == "nexus" and context != "nexus_ui":
        logger.warning("SECURITY: from=nexus but context!='nexus_ui' - possible spoof attempt")

    logger.info(
        "Processing [%s/%s] message %s: %.50s...",
        msg_type or "legacy", task_type or "-", msg_id[:24] if msg_id else "?", user_text
    )

    if config.dry_run:
        logger.info("[DRY-RUN] Would process and send response")
        return True

    # === Handle ACK: Just log it ===
    if msg_type == "ack":
        reply_to = msg.get("reply_to", "")
        logger.info("ACK received for %s", reply_to[:24] if reply_to else "unknown")
        return True

    # === Handle task_request: Generate structured task via OpenAI ===
    if msg_type == "task_request":
        if config.ai_mode == "ECHO":
            # Echo mode for testing
            task_data = {
                "description": f"[ECHO] {user_text[:200]}",
                "acceptance_criteria": ["Echo test passed"],
                "expected_artifacts": [],
                "verification_commands": [],
            }
        else:
            # AI mode: call OpenAI
            task_data = call_openai_for_task(config, user_text, context)

        # Build task payload
        task_payload = {
            "correlation_id": correlation_id or str(uuid.uuid4()),
            "description": task_data.get("description", "Task from GPT"),
            "acceptance_criteria": task_data.get("acceptance_criteria", []),
            "expected_artifacts": task_data.get("expected_artifacts", []),
            "verification_commands": task_data.get("verification_commands", []),
        }

        success = send_structured_response(
            config, "task", task_payload, reply_to=msg_id, to=reply_to_channel
        )

        if success:
            logger.info(
                "Sent task to %s for %s: %s",
                reply_to_channel, msg_id[:24], task_payload.get("description", "")[:50]
            )
        else:
            logger.error("Failed to send task for %s", msg_id[:24])

        return success

    # === Handle chat messages ===
    if msg_type == "task" and task_type == "chat":
        if config.ai_mode == "ECHO":
            response = f"[ECHO from GPT] {user_text}"
        else:
            response = call_openai_for_chat(config, user_text, context)

        if not response:
            logger.error("Empty response from OpenAI for chat %s", msg_id[:24])
            return False

        success = send_chat_response(config, response, reply_to=msg_id, to=reply_to_channel)

        if success:
            logger.info(
                "Sent chat response to %s for %s: %.50s...",
                reply_to_channel, msg_id[:24], response
            )
        else:
            logger.error("Failed to send chat response for %s", msg_id[:24])

        return success

    # === Handle result: Log outcome ===
    if msg_type == "result":
        outcome = payload.get("outcome", "unknown") if isinstance(payload, dict) else "unknown"
        corr_id = payload.get("correlation_id", "") if isinstance(payload, dict) else ""
        logger.info("Result received: correlation_id=%s, outcome=%s", corr_id, outcome)

        # Send ACK (always to the original sender)
        ack_payload = {
            "correlation_id": corr_id,
            "status": "received",
            "outcome_logged": outcome,
        }
        return send_structured_response(
            config, "ack", ack_payload, reply_to=msg_id, to=reply_to_channel
        )

    # === Default: Legacy chat response ===
    if not user_text:
        logger.warning("Empty message payload, skipping: %s", msg_id[:24])
        return False

    if config.ai_mode == "ECHO":
        response = f"[ECHO from GPT] {user_text}"
    else:
        response = call_openai_for_chat(config, user_text, context)

    if not response:
        logger.error("Empty response from OpenAI for message %s", msg_id[:24])
        return False

    success = send_chat_response(config, response, reply_to=msg_id, to=reply_to_channel)

    if success:
        logger.info("Sent response to %s for %s: %.50s...", reply_to_channel, msg_id[:24], response)
    else:
        logger.error("Failed to send response for %s", msg_id[:24])

    return success


def run_cycle(config: Config) -> None:
    """
    Single poll-process-respond cycle.

    Fail-closed: any error logs and returns without updating cursor.
    Uses deduplication by message ID to avoid reprocessing.
    """
    cursor = _load_cursor()
    processed_ids = _load_processed_ids()
    logger.debug("Polling inbox, cursor=%s, processed=%d", cursor or "(empty)", len(processed_ids))

    messages, next_cursor = poll_inbox(config, cursor)

    if not messages:
        logger.debug("No new messages")
        return

    # Filter out already-processed messages
    new_messages = []
    for msg in messages:
        msg_id = msg.get("id", "")
        if msg_id and msg_id in processed_ids:
            logger.debug("Skipping already-processed: %s", msg_id[:24])
            continue
        new_messages.append(msg)

    if not new_messages:
        logger.debug("All %d messages already processed", len(messages))
        # Still update cursor to move forward
        if next_cursor and next_cursor != cursor:
            _save_cursor(next_cursor)
        return

    logger.info("Found %d new message(s) (%d skipped)", len(new_messages), len(messages) - len(new_messages))

    # Process new messages only
    all_success = True
    newly_processed = []
    for msg in new_messages:
        msg_id = msg.get("id", "")
        if process_message(config, msg):
            if msg_id:
                newly_processed.append(msg_id)
        else:
            all_success = False

    # Update processed IDs (using ring buffer add, not set.update)
    if newly_processed:
        for msg_id in newly_processed:
            processed_ids.add(msg_id)
        _save_processed_ids(processed_ids)
        logger.debug("Marked %d messages as processed", len(newly_processed))

    # Update cursor only if all messages processed successfully
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
        "GPT Orchestrator Runner v%s starting (model=%s, interval=%dms, mode=%s, dry_run=%s)",
        VERSION, config.model, config.poll_interval_ms, config.ai_mode, config.dry_run
    )

    poll_sec = config.poll_interval_ms / 1000.0

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

        time.sleep(poll_sec)


def load_config_from_env() -> Config:
    """
    Load configuration from environment variables and secrets file.

    Required:
        OPENAI_API_KEY (from secrets file)
        FRIEND_BRIDGE_TOKEN (from secrets file)

    Optional:
        FRIEND_BRIDGE_URL (from secrets file, default: localhost tunnel)
        AI_MODE (default: AI)
        GPT_MODEL (default: gpt-4o)
    """
    api_key = _load_secret("OPENAI_API_KEY")
    bridge_token = _load_secret("FRIEND_BRIDGE_TOKEN")
    bridge_url = _load_secret("FRIEND_BRIDGE_URL") or DEFAULT_BRIDGE_URL

    if not api_key:
        logger.error("FAIL-CLOSED: OPENAI_API_KEY not found in %s", SECRETS_PATH)
        sys.exit(1)

    if not bridge_token:
        logger.error("FAIL-CLOSED: FRIEND_BRIDGE_TOKEN not found in %s", SECRETS_PATH)
        sys.exit(1)

    logger.info("OpenAI API key: %s", _redact(api_key))
    logger.info("Bridge token: %s", _redact(bridge_token))
    logger.info("Bridge URL: %s", bridge_url)

    ai_mode = os.environ.get("AI_MODE", "AI")
    if ai_mode not in ("AI", "ECHO"):
        logger.warning("Unknown AI_MODE '%s', using AI", ai_mode)
        ai_mode = "AI"

    return Config(
        openai_api_key=api_key,
        bridge_token=bridge_token,
        bridge_url=bridge_url,
        model=os.environ.get("GPT_MODEL", DEFAULT_MODEL),
        ai_mode=ai_mode,
    )


def main() -> int:
    """CLI entrypoint."""
    # HOPE-LAW-001: Policy bootstrap MUST be first (before logging/network)
    bootstrap("gpt_orchestrator", network_profile="core")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    global logger
    logger = logging.getLogger("gpt_orchestrator_runner")

    parser = argparse.ArgumentParser(description="GPT Orchestrator Runner")
    parser.add_argument(
        "--poll-ms",
        type=int,
        default=DEFAULT_POLL_INTERVAL_MS,
        help=f"Poll interval in milliseconds (default: {DEFAULT_POLL_INTERVAL_MS})",
    )
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
    config.poll_interval_ms = args.poll_ms
    config.dry_run = args.dry_run

    # Emit startup audit (records git commit, python version, config hash)
    emit_startup_audit(
        "gpt_orchestrator",
        config_public={
            "poll_interval_ms": config.poll_interval_ms,
            "model": config.model,
            "dry_run": config.dry_run,
        },
    )

    run_daemon(config, once=args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
