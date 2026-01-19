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

_THIS_FILE = Path(__file__).resolve()
_CORE_DIR = _THIS_FILE.parent
_MINIBOT_DIR = _CORE_DIR.parent
_STATE_DIR = _MINIBOT_DIR / "state"

logger = logging.getLogger("gpt_bridge_runner")

VERSION = "1.1.0"  # Fixed cursor handling to use timestamp-based format
DEFAULT_POLL_INTERVAL = 30
DEFAULT_MODEL = "gpt-4o"
BRIDGE_BASE_URL = "http://127.0.0.1:8765"
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
CURSOR_FILE = _STATE_DIR / "gpt_runner_cursor.txt"
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


def call_openai(config: Config, user_message: str, context: str = "") -> str:
    """
    Call OpenAI API with user message.

    Returns:
        Assistant response text
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


def send_response(config: Config, message: str, reply_to: Optional[str] = None) -> bool:
    """
    Send response to Claude via /send endpoint.

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


def process_message(config: Config, msg: Dict[str, Any]) -> bool:
    """
    Process single message from inbox.

    Returns:
        True if processed successfully
    """
    msg_id = msg.get("id", "unknown")
    payload = msg.get("payload", {})

    if isinstance(payload, str):
        user_text = payload
        context = ""
    elif isinstance(payload, dict):
        user_text = payload.get("message", payload.get("text", str(payload)))
        context = payload.get("context", "")
    else:
        user_text = str(payload)
        context = ""

    if not user_text:
        logger.warning("Empty message payload, skipping: %s", msg_id)
        return False

    logger.info("Processing message %s: %.50s...", msg_id, user_text)

    if config.dry_run:
        logger.info("[DRY-RUN] Would call OpenAI and send response")
        return True

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

    Required:
        OPENAI_API_KEY
        FRIEND_BRIDGE_TOKEN

    Optional:
        GPT_MODEL (default: gpt-4o)
        POLL_INTERVAL_SEC (default: 30)
        MAX_RETRIES (default: 3)
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    bridge_token = os.environ.get("FRIEND_BRIDGE_TOKEN", "")

    if not api_key:
        logger.error("FAIL-CLOSED: OPENAI_API_KEY not set")
        sys.exit(1)

    if not bridge_token:
        logger.error("FAIL-CLOSED: FRIEND_BRIDGE_TOKEN not set")
        sys.exit(1)

    return Config(
        openai_api_key=api_key,
        bridge_token=bridge_token,
        model=os.environ.get("GPT_MODEL", DEFAULT_MODEL),
        poll_interval=int(os.environ.get("POLL_INTERVAL_SEC", DEFAULT_POLL_INTERVAL)),
        max_retries=int(os.environ.get("MAX_RETRIES", "3")),
    )


def main() -> int:
    """CLI entrypoint."""
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

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

    run_daemon(config, once=args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
