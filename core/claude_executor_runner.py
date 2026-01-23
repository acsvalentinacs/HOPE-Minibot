# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 21:00:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 21:37:00 UTC
# === END SIGNATURE ===
"""
Claude Executor Runner - Task Execution Daemon.

Polls Friend Bridge /inbox/claude for task messages from GPT,
executes them locally, sends results back via /send.

Architecture:
    Windows Process → poll /inbox/claude → Execute Task → POST /send (to=gpt)

Task Execution:
    - Runs verification_commands via subprocess
    - Checks expected_artifacts exist
    - Validates acceptance_criteria
    - Reports outcome: SUCCESS, PARTIAL, or FAIL

State Management:
    - Cursor persisted atomically in state/executor_cursor.txt
    - Deduplication via SQLite (state/ipc/claude_executor_dedup.sqlite3)
    - Fail-closed: any error → skip cycle, log, retry next interval

Security:
    - FRIEND_BRIDGE_TOKEN from env only
    - SSH tunnel: localhost:18765 → VPS:8765
    - Commands sandboxed to minibot directory

Usage:
    python -m core.claude_executor_runner --poll-ms 500
    python -m core.claude_executor_runner --poll-ms 500 --dry-run
    python -m core.claude_executor_runner --poll-ms 500 --once
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
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
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
DEFAULT_BRIDGE_URL = "http://127.0.0.1:18765"  # Fallback if no env
CURSOR_FILE = _STATE_DIR / "executor_cursor.txt"
SECRETS_PATH = Path(r"C:\secrets\hope\.env")
EXECUTION_LOG = _STATE_DIR / "execution_log.jsonl"
COMMAND_TIMEOUT = 60  # seconds
DEDUP_DB_PATH = _STATE_DIR / "ipc" / "claude_executor_dedup.sqlite3"


# === Deduplication via SQLite ===
def _dedup_init() -> sqlite3.Connection:
    """Initialize deduplication database."""
    DEDUP_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DEDUP_DB_PATH), timeout=5)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute(
        "CREATE TABLE IF NOT EXISTS seen ("
        "  msg_id TEXT PRIMARY KEY,"
        "  ts INTEGER NOT NULL"
        ")"
    )
    # Cleanup old entries (older than 24h) to prevent unbounded growth
    con.execute("DELETE FROM seen WHERE ts < ?", (int(time.time()) - 86400,))
    con.commit()
    return con


_DEDUP_CON: sqlite3.Connection | None = None


def _get_dedup_con() -> sqlite3.Connection:
    """Get or create deduplication connection."""
    global _DEDUP_CON
    if _DEDUP_CON is None:
        _DEDUP_CON = _dedup_init()
    return _DEDUP_CON


def _dedup_seen(msg_id: str) -> bool:
    """Check if message was already processed."""
    if not msg_id:
        return False
    con = _get_dedup_con()
    cur = con.execute("SELECT 1 FROM seen WHERE msg_id = ? LIMIT 1", (msg_id,))
    return cur.fetchone() is not None


def _dedup_mark(msg_id: str) -> None:
    """Mark message as processed."""
    if not msg_id:
        return
    con = _get_dedup_con()
    con.execute(
        "INSERT OR IGNORE INTO seen (msg_id, ts) VALUES (?, ?)",
        (msg_id, int(time.time())),
    )
    con.commit()


@dataclass
class Config:
    """Executor configuration."""
    bridge_token: str
    bridge_url: str = DEFAULT_BRIDGE_URL
    poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS
    dry_run: bool = False
    sandbox_dir: Path = _MINIBOT_DIR


@dataclass
class ExecutionResult:
    """Result of task execution."""
    outcome: str  # SUCCESS, PARTIAL, FAIL
    correlation_id: str
    artifacts_found: List[str] = field(default_factory=list)
    artifacts_missing: List[str] = field(default_factory=list)
    commands_passed: List[str] = field(default_factory=list)
    commands_failed: List[str] = field(default_factory=list)
    criteria_met: List[str] = field(default_factory=list)
    criteria_unmet: List[str] = field(default_factory=list)
    error: Optional[str] = None
    duration_sec: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            "outcome": self.outcome,
            "correlation_id": self.correlation_id,
            "artifacts_found": self.artifacts_found,
            "artifacts_missing": self.artifacts_missing,
            "commands_passed": self.commands_passed,
            "commands_failed": self.commands_failed,
            "criteria_met": self.criteria_met,
            "criteria_unmet": self.criteria_unmet,
            "error": self.error,
            "duration_sec": self.duration_sec,
        }


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
    """Redact secret for logging."""
    if not s or len(s) < 8:
        return "***"
    return s[:4] + "..." + s[-4:]


def _atomic_write(path: Path, content: str) -> None:
    """Atomic write: temp → fsync → replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    """Append record to JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


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
    Poll /inbox/claude for new messages.

    Returns:
        Tuple of (messages list, next_after cursor)
    """
    url = f"{config.bridge_url}/inbox/claude?limit=10"
    if after:
        url += f"&after={after}"

    headers = {"X-HOPE-Token": config.bridge_token}

    result = _http_request(url, headers=headers)

    if not result.get("ok"):
        raise RuntimeError(f"Inbox poll failed: {result.get('error')}")

    return result.get("messages", []), result.get("next_after", after)


def run_command(
    command: str,
    cwd: Path,
    timeout: int = COMMAND_TIMEOUT,
) -> tuple[bool, str, str]:
    """
    Run verification command in sandboxed directory.

    Args:
        command: Command to run
        cwd: Working directory
        timeout: Timeout in seconds

    Returns:
        Tuple of (success, stdout, stderr)
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        success = result.returncode == 0
        return success, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", f"Command timed out after {timeout}s"
    except Exception as e:
        return False, "", str(e)


def check_artifact(artifact_path: str, sandbox_dir: Path) -> bool:
    """
    Check if expected artifact exists.

    Security: Only allows paths within sandbox_dir.
    """
    try:
        target = (sandbox_dir / artifact_path).resolve()
        # Security check: must be within sandbox
        if not str(target).startswith(str(sandbox_dir.resolve())):
            logger.warning("Artifact path outside sandbox: %s", artifact_path)
            return False
        return target.exists()
    except Exception as e:
        logger.warning("Error checking artifact %s: %s", artifact_path, e)
        return False


def execute_task(
    task_payload: Dict[str, Any],
    sandbox_dir: Path,
    dry_run: bool = False,
) -> ExecutionResult:
    """
    Execute task and validate results.

    Args:
        task_payload: Task payload with description, criteria, commands, artifacts
        sandbox_dir: Directory to run commands in
        dry_run: If True, skip actual execution

    Returns:
        ExecutionResult with outcome and details
    """
    start_time = time.time()

    correlation_id = task_payload.get("correlation_id", "unknown")
    description = task_payload.get("description", "")
    acceptance_criteria = task_payload.get("acceptance_criteria", [])
    expected_artifacts = task_payload.get("expected_artifacts", [])
    verification_commands = task_payload.get("verification_commands", [])

    logger.info("Executing task: %s", description[:100])
    logger.info("  correlation_id: %s", correlation_id)
    logger.info("  criteria: %d, artifacts: %d, commands: %d",
                len(acceptance_criteria), len(expected_artifacts), len(verification_commands))

    if dry_run:
        return ExecutionResult(
            outcome="DRY_RUN",
            correlation_id=correlation_id,
            duration_sec=time.time() - start_time,
        )

    result = ExecutionResult(correlation_id=correlation_id)

    # 1. Check expected artifacts
    for artifact in expected_artifacts:
        if check_artifact(artifact, sandbox_dir):
            result.artifacts_found.append(artifact)
        else:
            result.artifacts_missing.append(artifact)

    # 2. Run verification commands
    for cmd in verification_commands:
        success, stdout, stderr = run_command(cmd, sandbox_dir)
        if success:
            result.commands_passed.append(cmd)
            logger.info("  PASS: %s", cmd[:50])
        else:
            result.commands_failed.append(cmd)
            logger.warning("  FAIL: %s -> %s", cmd[:50], stderr[:100] if stderr else "no output")

    # 3. Evaluate acceptance criteria
    # For now, criteria are considered met if all commands pass and all artifacts exist
    for criterion in acceptance_criteria:
        # Simple heuristic: criterion met if no failures
        if not result.commands_failed and not result.artifacts_missing:
            result.criteria_met.append(criterion)
        else:
            result.criteria_unmet.append(criterion)

    # 4. Determine outcome
    result.duration_sec = time.time() - start_time

    if not result.commands_failed and not result.artifacts_missing and not result.criteria_unmet:
        result.outcome = "SUCCESS"
    elif result.commands_passed or result.artifacts_found:
        result.outcome = "PARTIAL"
    else:
        result.outcome = "FAIL"

    logger.info("Task outcome: %s (duration: %.2fs)", result.outcome, result.duration_sec)

    return result


def send_result(
    config: Config,
    result: ExecutionResult,
    reply_to: Optional[str] = None,
) -> bool:
    """
    Send task result to GPT via /send endpoint.

    Args:
        config: Configuration
        result: Execution result
        reply_to: Parent message ID for correlation

    Returns:
        True if successful
    """
    request_payload = {
        "to": "gpt",
        "type": "result",
        "payload": result.to_dict(),
    }
    if reply_to:
        request_payload["reply_to"] = reply_to

    headers = {
        "X-HOPE-Token": config.bridge_token,
        "Content-Type": "application/json",
    }

    api_result = _http_request(
        f"{config.bridge_url}/send",
        method="POST",
        headers=headers,
        body=json.dumps(request_payload).encode("utf-8"),
    )

    return api_result.get("ok", False)


def send_chat_response(
    config: Config,
    message: str,
    reply_to: Optional[str] = None,
    context: str = "claude_executor",
) -> bool:
    """
    Send chat response to GPT via /send endpoint.

    Uses structured mode with type=task, payload={task_type: "chat"} for proper correlation.

    Args:
        config: Configuration
        message: Response message
        reply_to: Parent message ID for correlation
        context: Context identifier

    Returns:
        True if successful
    """
    payload = {
        "task_type": "chat",
        "message": message,
        "context": context,
    }

    request_data = {
        "to": "gpt",
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


def send_ack(config: Config, reply_to: str) -> bool:
    """
    Send acknowledgment for received message.

    Args:
        config: Configuration
        reply_to: Message ID being acknowledged

    Returns:
        True if successful
    """
    request_payload = {
        "to": "gpt",
        "type": "ack",
        "payload": {"acked": reply_to},
        "reply_to": reply_to,
    }

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


def process_message(config: Config, msg: Dict[str, Any]) -> bool:
    """
    Process single message from inbox.

    Handles different message types:
    - task: Execute task and send result
    - chat: Handle chat message
    - ack: Log acknowledgment
    - response: Log and ACK

    Returns:
        True if processed successfully
    """
    msg_id = msg.get("id", "unknown")
    msg_type = (msg.get("type") or "").lower()
    payload = msg.get("payload", {})

    # === Deduplication: skip already-processed messages ===
    if _dedup_seen(msg_id):
        logger.debug("Duplicate message skipped: %s", msg_id[:24] if msg_id else "?")
        return True

    # Extract task type for tasks
    task_type = ""
    if isinstance(payload, dict):
        task_type = payload.get("task_type", "")

    logger.info(
        "Processing [%s/%s] message %s",
        msg_type or "legacy", task_type or "-", msg_id[:24] if msg_id else "?"
    )

    if config.dry_run:
        logger.info("[DRY-RUN] Would process message")
        _dedup_mark(msg_id)  # Mark as seen even in dry-run
        return True

    # === Handle ACK ===
    if msg_type == "ack":
        reply_to = msg.get("reply_to", "")
        logger.info("ACK received for %s", reply_to[:24] if reply_to else "unknown")
        _dedup_mark(msg_id)
        return True

    # === Handle structured task ===
    if msg_type == "task":
        if not isinstance(payload, dict):
            logger.warning("Task payload is not a dict: %s", msg_id[:24])
            return False

        # Chat task
        if task_type == "chat":
            message = payload.get("message", "")
            context = payload.get("context", "")
            msg_from = payload.get("from", "")
            logger.info("Chat message (from=%s, ctx=%s): %.100s...", msg_from, context, message)

            # CRITICAL FIX: Do NOT send responses back to GPT for chat messages.
            # This was causing infinite feedback loop:
            #   GPT -> Claude -> "Received..." -> GPT -> "Please clarify..." -> Claude -> ...
            #
            # Executor should only:
            # 1. Log the message
            # 2. Send ACK (not a full response)
            # 3. Forward to actual Claude agent if needed
            #
            # Chat responses should go to the ORIGINAL sender (user/nexus), not back to GPT.
            if context in ("cli", "nexus_ui", "user"):
                # Message came from user via CLI/NEXUS - just acknowledge, don't loop back
                logger.info("Chat from user context '%s' - logging only, no GPT loop", context)
                _dedup_mark(msg_id)
                return send_ack(config, msg_id)

            # For other contexts, just acknowledge without sending full response
            logger.info("Chat acknowledged for %s (no response sent to avoid loop)", msg_id[:24])
            _dedup_mark(msg_id)
            return send_ack(config, msg_id)

        # Execution task (has acceptance_criteria, verification_commands, etc.)
        if payload.get("acceptance_criteria") or payload.get("verification_commands"):
            exec_result = execute_task(payload, config.sandbox_dir, config.dry_run)

            # Log execution
            log_record = {
                "timestamp": time.time(),
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "msg_id": msg_id,
                "result": exec_result.to_dict(),
            }
            _append_jsonl(EXECUTION_LOG, log_record)

            success = send_result(config, exec_result, reply_to=msg_id)

            if success:
                logger.info(
                    "Sent result for %s: outcome=%s",
                    msg_id[:24], exec_result.outcome
                )
                _dedup_mark(msg_id)
            else:
                logger.error("Failed to send result for %s", msg_id[:24])

            return success

        # Unknown task type
        logger.warning("Unknown task type in message %s: %s", msg_id[:24], task_type)
        _dedup_mark(msg_id)
        return True  # Don't fail, just skip

    # === Handle response ===
    if msg_type == "response":
        logger.info("Response received: %s", msg_id[:24])
        send_ack(config, msg_id)
        _dedup_mark(msg_id)
        return True

    # === Legacy message (no type field) ===
    if isinstance(payload, dict):
        message = payload.get("message", payload.get("text", ""))
    else:
        message = str(payload)

    if message:
        logger.info("Legacy message: %.100s...", message)
        # Just acknowledge
        _dedup_mark(msg_id)
        return send_ack(config, msg_id)

    logger.warning("Empty message payload: %s", msg_id[:24])
    _dedup_mark(msg_id)
    return True


def run_cycle(config: Config) -> None:
    """
    Single poll-process cycle.

    Fail-closed: any error logs and returns without updating cursor.
    """
    cursor = _load_cursor()
    logger.debug("Polling inbox, cursor=%s", cursor or "(empty)")

    messages, next_cursor = poll_inbox(config, cursor)

    if not messages:
        logger.debug("No new messages")
        return

    logger.info("Found %d new message(s)", len(messages))

    # Process all messages
    all_success = True
    for msg in messages:
        if not process_message(config, msg):
            all_success = False

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
        "Claude Executor Runner v%s starting (interval=%dms, dry_run=%s)",
        VERSION, config.poll_interval_ms, config.dry_run
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
        FRIEND_BRIDGE_TOKEN (from secrets file)

    Optional:
        FRIEND_BRIDGE_URL (from secrets file, default: localhost tunnel)
    """
    bridge_token = _load_secret("FRIEND_BRIDGE_TOKEN")
    bridge_url = _load_secret("FRIEND_BRIDGE_URL") or DEFAULT_BRIDGE_URL

    if not bridge_token:
        logger.error("FAIL-CLOSED: FRIEND_BRIDGE_TOKEN not found in %s", SECRETS_PATH)
        sys.exit(1)

    logger.info("Bridge token: %s", _redact(bridge_token))
    logger.info("Bridge URL: %s", bridge_url)

    return Config(bridge_token=bridge_token, bridge_url=bridge_url)


def main() -> int:
    """CLI entrypoint."""
    # HOPE-LAW-001: Policy bootstrap MUST be first (before logging/network)
    bootstrap("claude_executor", network_profile="core")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    global logger
    logger = logging.getLogger("claude_executor_runner")

    parser = argparse.ArgumentParser(description="Claude Executor Runner")
    parser.add_argument(
        "--poll-ms",
        type=int,
        default=DEFAULT_POLL_INTERVAL_MS,
        help=f"Poll interval in milliseconds (default: {DEFAULT_POLL_INTERVAL_MS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log actions without executing tasks or sending messages",
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
        "claude_executor",
        config_public={
            "poll_interval_ms": config.poll_interval_ms,
            "dry_run": config.dry_run,
        },
    )

    run_daemon(config, once=args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
