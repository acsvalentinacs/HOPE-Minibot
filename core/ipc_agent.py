# === AI SIGNATURE ===
# Created by: Kirill Dev
# Created at: 2026-01-19 18:24:32 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 11:30:00 UTC
# === END SIGNATURE ===
"""
Inter-AI Communication Protocol v2.1

Production-grade folder-based IPC for GPT-5.2 <-> Claude communication.

Architecture:
- Internal paths (source of truth): minibot/ipc/{claude_inbox, claude_outbox, gpt_inbox, gpt_outbox}
- External aliases (UI/interop): Russian-named folders, auto-synced
- Transport: outbox -> peer_inbox via atomic write (not manual copy)
- ACK protocol: response delivered only after ack, with resend on timeout
- Backpressure: max 64KB message, max 100 files/cycle
- Fail-closed: any schema violation -> deadletter

Message format (canonical JSON, sorted keys):
{
    "id": "sha256:<64-char-hex>",  # FULL sha256 of message without id field
    "from": "gpt-5.2" | "claude",
    "to": "claude" | "gpt-5.2",
    "timestamp": 1737042000.123,
    "type": "task" | "response" | "ack" | "error",
    "payload": {...},
    "reply_to": "sha256:..." (optional)
}

Usage:
    python core/ipc_agent.py --role=claude --poll_sec=2
    python core/ipc_agent.py --role=gpt --poll_sec=5
    python core/ipc_agent.py --role=claude --selftest=2+2
"""
from __future__ import annotations

import ast
import hashlib
import json
import logging
import logging.handlers
import os
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

# Ensure minibot is in path when running as script
_script_dir = Path(__file__).resolve().parent.parent
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))

from core.win_cmdline import get_argv

# =============================================================================
# PATHS - Internal structure (source of truth) with external aliases
# =============================================================================

BASE_DIR = Path(r"C:\Users\kirillDev\Desktop\TradingBot\minibot")
IPC_DIR = BASE_DIR / "ipc"

# Internal paths (used by code - SOURCE OF TRUTH)
CLAUDE_INBOX = IPC_DIR / "claude_inbox"
CLAUDE_OUTBOX = IPC_DIR / "claude_outbox"
GPT_INBOX = IPC_DIR / "gpt_inbox"
GPT_OUTBOX = IPC_DIR / "gpt_outbox"
DEADLETTER = IPC_DIR / "deadletter"

# External aliases (Russian names - UI/interop surface, auto-synced)
EXTERNAL_ALIASES: Dict[Path, Path] = {
    BASE_DIR / "ИИ Claude слушает 2026" / "inbox": CLAUDE_INBOX,
    BASE_DIR / "ИИ Claude говорит 2026" / "outbox": CLAUDE_OUTBOX,
    BASE_DIR / "Чат GPT-5.2 слушает 2026" / "inbox": GPT_INBOX,
    BASE_DIR / "Чат GPT-5.2 говорит 2026" / "outbox": GPT_OUTBOX,
}

# Logs
LOGS_DIR = BASE_DIR / "logs"
IPC_LOG_FILE = LOGS_DIR / "ipc.log"

# =============================================================================
# CONSTANTS - Backpressure and resend limits
# =============================================================================

MAX_MESSAGE_SIZE = 64 * 1024  # 64KB
MAX_FILES_PER_CYCLE = 100
MAX_EXPRESSION_LENGTH = 64

ACK_TIMEOUT_SEC = 15.0
RESEND_MIN_INTERVAL_SEC = 5.0
MAX_RESENDS_PER_CYCLE = 10

# Friend Chat ACK tracking file (separate from IPC cursor to avoid race conditions)
FRIEND_CHAT_ACKS_FILE = BASE_DIR / "state" / "friend_chat" / "acked_messages.json"

# =============================================================================
# LOGGING - UTF-8 file logger with duplicate handler guard
# =============================================================================

logger = logging.getLogger("ipc")


def setup_ipc_logging() -> None:
    """
    Setup IPC logging with rotation; idempotent (no duplicate handlers).

    Rotation policy: 10MB x 5 backups.
    Uses named handlers for reliable duplicate detection.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.DEBUG)

    # Guard by handler name (most reliable across handler subclasses)
    existing_names = {getattr(h, "name", "") for h in logger.handlers}

    if "ipc_file" not in existing_names:
        file_handler = logging.handlers.RotatingFileHandler(
            IPC_LOG_FILE,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,               # keep 5 rotated files
            encoding="utf-8",
            delay=True,
        )
        file_handler.name = "ipc_file"
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        logger.addHandler(file_handler)

    if "ipc_console" not in existing_names:
        console_handler = logging.StreamHandler(stream=sys.stdout)
        console_handler.name = "ipc_console"
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s"
        ))
        logger.addHandler(console_handler)


# =============================================================================
# ENUMS & DATACLASSES
# =============================================================================

class MessageType(str, Enum):
    """IPC message types."""
    TASK = "task"
    RESPONSE = "response"
    ACK = "ack"
    ERROR = "error"


class Sender(str, Enum):
    """Message senders."""
    GPT = "gpt-5.2"
    CLAUDE = "claude"


@dataclass
class IPCMessage:
    """Parsed IPC message."""
    id: str
    sender: str
    recipient: str
    timestamp: float
    msg_type: MessageType
    payload: Dict[str, Any]
    reply_to: Optional[str] = None


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def _json_canonical(obj: object) -> str:
    """Canonical JSON: sorted keys, no spaces, ensure_ascii=False."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _generate_id_from_message_fields(fields_without_id: dict) -> str:
    """Generate FULL sha256 ID from canonical JSON of message fields."""
    canonical = _json_canonical(fields_without_id)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()  # FULL 64 chars
    return f"sha256:{digest}"


def _atomic_write(path: Path, content: str) -> None:
    """Atomic write: temp -> fsync -> replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as e:
        logger.error("Atomic write failed: %s", e)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _move_to_deadletter(file_path: Path, reason: str) -> None:
    """Move failed message to deadletter queue."""
    DEADLETTER.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    dest = DEADLETTER / f"{ts}_{file_path.name}"

    try:
        shutil.move(str(file_path), str(dest))
        _atomic_write(dest.with_suffix(dest.suffix + ".reason"), reason)
        logger.warning("Deadletter: %s (%s)", file_path.name, reason)
    except OSError as e:
        logger.error("Deadletter move failed: %s", e)


def _safe_eval_expr(expr: str) -> float:
    """
    Safe math expression evaluator using AST.

    Only allows: numbers, +, -, *, /, **, ()
    """
    if not isinstance(expr, str):
        raise ValueError("expression must be string")

    expr = expr.strip()
    if not expr:
        raise ValueError("empty expression")

    if len(expr) > MAX_EXPRESSION_LENGTH:
        raise ValueError(f"expression too long (max {MAX_EXPRESSION_LENGTH})")

    tree = ast.parse(expr, mode="eval")

    allowed_nodes = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow,
        ast.USub, ast.UAdd,
        ast.Constant,
        ast.Load,
    )

    for node in ast.walk(tree):
        if not isinstance(node, allowed_nodes):
            raise ValueError(f"disallowed syntax: {type(node).__name__}")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ValueError("only numeric constants allowed")

    def eval_node(n: ast.AST) -> float:
        if isinstance(n, ast.Expression):
            return eval_node(n.body)
        if isinstance(n, ast.Constant):
            return float(n.value)
        if isinstance(n, ast.UnaryOp):
            v = eval_node(n.operand)
            if isinstance(n.op, ast.UAdd):
                return +v
            if isinstance(n.op, ast.USub):
                return -v
            raise ValueError("unsupported unary op")
        if isinstance(n, ast.BinOp):
            a = eval_node(n.left)
            b = eval_node(n.right)
            if isinstance(n.op, ast.Add):
                return a + b
            if isinstance(n.op, ast.Sub):
                return a - b
            if isinstance(n.op, ast.Mult):
                return a * b
            if isinstance(n.op, ast.Div):
                if b == 0:
                    raise ValueError("division by zero")
                return a / b
            if isinstance(n.op, ast.Pow):
                return a ** b
            raise ValueError("unsupported binary op")
        raise ValueError("unsupported node")

    return eval_node(tree)


def init_ipc_folders() -> None:
    """Initialize IPC folder structure."""
    # Create internal folders (source of truth)
    for d in (CLAUDE_INBOX, CLAUDE_OUTBOX, GPT_INBOX, GPT_OUTBOX, DEADLETTER, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)

    # Create external alias folders
    for ext_path in EXTERNAL_ALIASES.keys():
        ext_path.mkdir(parents=True, exist_ok=True)

    logger.info("IPC initialized: %s", IPC_DIR)


# =============================================================================
# SYNC FUNCTIONS - External <-> Internal
# =============================================================================

def _sync_external_to_internal(
    ext_dir: Path,
    internal_dir: Path,
    limit: int = MAX_FILES_PER_CYCLE
) -> int:
    """
    Sync external (Russian-named) inbox to internal inbox.

    External folders are UI/interop surface.
    Internal ipc/* is the source of truth for processing.
    Fail-closed: unreadable/oversized -> deadletter.
    """
    if not ext_dir.exists():
        return 0

    moved = 0
    files = sorted(ext_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)

    for p in files[:limit]:
        try:
            if p.stat().st_size > MAX_MESSAGE_SIZE:
                _move_to_deadletter(p, f"too_large:{p.stat().st_size}")
                continue

            raw = p.read_text(encoding="utf-8")
            data = json.loads(raw)
            msg_id = data.get("id", "")

            if not isinstance(msg_id, str) or not msg_id.startswith("sha256:"):
                _move_to_deadletter(p, "bad_id_in_external")
                continue

            # Atomic ingest into internal inbox
            dst = internal_dir / f"{msg_id[7:23]}_{p.name}"
            if not dst.exists():
                _atomic_write(dst, raw)

            p.unlink(missing_ok=True)
            moved += 1

        except Exception as e:
            _move_to_deadletter(p, f"external_sync_error:{e}")

    return moved


def _sync_internal_to_external(
    internal_dir: Path,
    ext_dir: Path,
    limit: int = MAX_FILES_PER_CYCLE
) -> int:
    """
    Mirror internal outbox to external (Russian-named) outbox.

    This allows external tools/GPT to read responses.
    """
    if not internal_dir.exists():
        return 0

    ext_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    files = sorted(internal_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)

    for p in files[:limit]:
        try:
            raw = p.read_text(encoding="utf-8")
            data = json.loads(raw)
            msg_id = data.get("id", "")

            if not isinstance(msg_id, str) or not msg_id.startswith("sha256:"):
                continue

            dst = ext_dir / p.name
            if dst.exists():
                continue

            _atomic_write(dst, raw)
            copied += 1

        except Exception:
            continue

    return copied


# =============================================================================
# BASE AGENT CLASS
# =============================================================================

class IPCAgent:
    """
    Base IPC agent with ACK protocol, transport, and resend.

    Subclassed by ClaudeAgent and GPTAgent.
    """

    def __init__(
        self,
        role: Sender,
        inbox: Path,
        outbox: Path,
        peer_inbox: Path,
    ) -> None:
        self._role = role
        self._inbox = inbox
        self._outbox = outbox
        self._peer_inbox = peer_inbox

        self._handlers: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}
        self._processed: Set[str] = set()
        self._pending_acks: Dict[str, float] = {}  # msg_id -> sent_timestamp
        self._last_resend_attempt: Dict[str, float] = {}  # msg_id -> last_resend_ts

        self._cursor_file = BASE_DIR / "state" / f"ipc_cursor_{role.value}.json"
        self._load_cursor()
        self._register_default_handlers()

    def _load_cursor(self) -> None:
        """Load processed message IDs and pending acks."""
        if not self._cursor_file.exists():
            return

        try:
            data = json.loads(self._cursor_file.read_text(encoding="utf-8"))
            self._processed = set(data.get("processed", []))
            self._pending_acks = {k: float(v) for k, v in data.get("pending_acks", {}).items()}
            self._last_resend_attempt = {k: float(v) for k, v in data.get("last_resend_attempt", {}).items()}
        except Exception as e:
            logger.warning("Cursor load failed: %s", e)

    def _sync_friend_chat_acks(self) -> int:
        """
        Sync ACKs from Friend Chat tracking file.

        Friend Chat sends ACKs via HTTP API which are recorded in acked_messages.json.
        This function clears corresponding entries from pending_acks.

        Returns number of entries cleared.
        """
        if not FRIEND_CHAT_ACKS_FILE.exists():
            return 0

        try:
            data = json.loads(FRIEND_CHAT_ACKS_FILE.read_text(encoding="utf-8"))
            acked_ids = set(data.get("acked_messages", {}).keys())
        except (json.JSONDecodeError, OSError):
            return 0

        cleared = 0
        for msg_id in list(self._pending_acks.keys()):
            if msg_id in acked_ids:
                del self._pending_acks[msg_id]
                self._last_resend_attempt.pop(msg_id, None)
                cleared += 1
                logger.info("Friend Chat ACK cleared: %s", msg_id[:24])

        if cleared:
            self._save_cursor()

        return cleared

    def _save_cursor(self) -> None:
        """Save processed message IDs and pending acks."""
        self._cursor_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "processed": list(self._processed)[-2000:],
            "pending_acks": self._pending_acks,
            "last_resend_attempt": self._last_resend_attempt,
            "last_update": time.time(),
        }
        _atomic_write(self._cursor_file, json.dumps(data, indent=2, ensure_ascii=False))

    def _register_default_handlers(self) -> None:
        """Register default task handlers (safe subset only)."""
        self._handlers["echo"] = lambda payload: {"echo": payload}
        self._handlers["math"] = self._handle_math
        self._handlers["ping"] = lambda payload: {
            "pong": True,
            "timestamp": time.time(),
            "agent": self._role.value,
        }
        self._handlers["status"] = lambda payload: self.get_stats()
        self._handlers["chat"] = self._handle_chat

    def _handle_chat(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle chat message from friend chat."""
        message = payload.get("message", "")
        context = payload.get("context", "unknown")

        logger.info("CHAT received: %s (context=%s)", message[:100], context)

        # For now, echo back with acknowledgment
        # Future: integrate with actual AI processing
        return {
            "received": True,
            "message": message,
            "context": context,
            "agent": self._role.value,
            "timestamp": time.time(),
            "note": "Chat message acknowledged. Agent is listening.",
        }

    def enable_debug_handlers(self) -> None:
        """
        Enable debug handlers (file_read/glob/verify).

        SECURITY: This can ONLY be called via CLI flag --enable_debug_tasks=1
        IPC activation is NOT allowed (fail-closed).
        """
        self._handlers["file_read"] = self._handle_file_read
        self._handlers["glob"] = self._handle_glob
        self._handlers["verify"] = self._handle_verify
        self._debug_enabled = True
        logger.warning("Debug handlers ENABLED (file_read/glob/verify)")

    def is_debug_enabled(self) -> bool:
        """Check if debug handlers are enabled (flag-only, no IPC activation)."""
        return getattr(self, "_debug_enabled", False)

    def _handle_file_read(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Read file contents for GPT verification."""
        rel_path = payload.get("path", "")
        max_lines = payload.get("max_lines", 100)

        # Security: only allow reading within minibot directory
        try:
            target = (BASE_DIR / rel_path).resolve()
            if not str(target).startswith(str(BASE_DIR)):
                return {"error": "Path outside minibot directory"}

            if not target.exists():
                return {"error": f"File not found: {rel_path}", "exists": False}

            if target.is_dir():
                return {"error": "Path is a directory", "is_dir": True}

            content = target.read_text(encoding="utf-8")
            lines = content.split("\n")

            return {
                "path": rel_path,
                "exists": True,
                "lines_total": len(lines),
                "content": "\n".join(lines[:max_lines]),
                "truncated": len(lines) > max_lines,
            }
        except Exception as e:
            return {"error": str(e)}

    def _handle_glob(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """List files by glob pattern for GPT verification."""
        pattern = payload.get("pattern", "*")
        max_files = payload.get("max_files", 50)

        try:
            # Security: only within minibot
            files = list(BASE_DIR.glob(pattern))[:max_files]
            return {
                "pattern": pattern,
                "count": len(files),
                "files": [str(f.relative_to(BASE_DIR)) for f in files],
                "truncated": len(list(BASE_DIR.glob(pattern))) > max_files,
            }
        except Exception as e:
            return {"error": str(e)}

    def _handle_verify(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Verify artifacts exist for GPT DoD checks."""
        checks = payload.get("checks", [])
        results = {}

        for check in checks:
            if "ipc.log" in check.lower():
                results["ipc_log"] = IPC_LOG_FILE.exists()
            elif "gpt_inbox" in check.lower():
                results["gpt_inbox_files"] = len(list(GPT_INBOX.glob("*.json")))
            elif "claude_outbox" in check.lower():
                results["claude_outbox_files"] = len(list(CLAUDE_OUTBOX.glob("*.json")))
            elif "deadletter" in check.lower():
                results["deadletter_files"] = len(list(DEADLETTER.glob("*.json")))

        results["timestamp"] = time.time()
        results["agent"] = self._role.value
        return results

    def _handle_math(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Math handler - safe AST evaluation."""
        expr = payload.get("expression", "")
        try:
            return {"expression": expr, "result": _safe_eval_expr(expr)}
        except Exception as e:
            return {"error": str(e)}

    def register_handler(self, task_type: str, handler: Callable) -> None:
        """Register custom task handler."""
        self._handlers[task_type] = handler

    def _parse_message(self, file_path: Path) -> Optional[IPCMessage]:
        """Parse message file with fail-closed validation."""
        try:
            if file_path.stat().st_size > MAX_MESSAGE_SIZE:
                _move_to_deadletter(file_path, "too_large")
                return None

            data = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception as e:
            _move_to_deadletter(file_path, f"parse_error:{e}")
            return None

        # Validate required fields
        required = ["id", "from", "to", "timestamp", "type", "payload"]
        missing = [k for k in required if k not in data]
        if missing:
            _move_to_deadletter(file_path, f"missing_fields:{missing}")
            return None

        # Validate message type
        try:
            msg_type = MessageType(data["type"])
        except ValueError:
            _move_to_deadletter(file_path, f"unknown_type:{data.get('type')}")
            return None

        # Validate recipient
        if data["to"] != self._role.value:
            _move_to_deadletter(file_path, f"wrong_recipient:{data.get('to')}")
            return None

        # Validate ID integrity
        fields_for_id = {
            "from": data["from"],
            "to": data["to"],
            "timestamp": data["timestamp"],
            "type": data["type"],
            "payload": data["payload"],
        }
        if data.get("reply_to"):
            fields_for_id["reply_to"] = data["reply_to"]

        expected_id = _generate_id_from_message_fields(fields_for_id)
        if data["id"] != expected_id:
            _move_to_deadletter(file_path, "id_mismatch")
            return None

        return IPCMessage(
            id=data["id"],
            sender=data["from"],
            recipient=data["to"],
            timestamp=float(data["timestamp"]),
            msg_type=msg_type,
            payload=dict(data["payload"]),
            reply_to=data.get("reply_to"),
        )

    def _deliver_to_peer_inbox(self, msg: dict) -> None:
        """
        Transport: deliver message to peer inbox via atomic write.

        This is the critical transport layer that makes outbox a real spool.
        Messages are delivered directly to peer's internal inbox.
        """
        msg_id = msg["id"]
        dst = self._peer_inbox / f"{msg_id[7:23]}_{msg_id[-8:]}.json"

        if dst.exists():
            return

        _atomic_write(dst, json.dumps(msg, indent=2, ensure_ascii=False))
        logger.debug("Delivered to peer inbox: %s", msg_id[:24])

    def _send_message(
        self,
        recipient: str,
        msg_type: MessageType,
        payload: Dict[str, Any],
        reply_to: Optional[str] = None,
    ) -> str:
        """Send message: write to outbox + deliver to peer inbox."""
        ts = time.time()

        # Build message fields (without id)
        fields = {
            "from": self._role.value,
            "to": recipient,
            "timestamp": ts,
            "type": msg_type.value,
            "payload": payload,
        }
        if reply_to:
            fields["reply_to"] = reply_to

        # Generate FULL sha256 ID from canonical fields
        msg_id = _generate_id_from_message_fields(fields)

        # Build final message
        msg = dict(fields)
        msg["id"] = msg_id

        # Save to outbox (archive/audit trail)
        fname = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{msg_id[7:23]}.json"
        out_path = self._outbox / fname
        _atomic_write(out_path, json.dumps(msg, indent=2, ensure_ascii=False))

        # Track pending ack for non-ack messages
        if msg_type != MessageType.ACK:
            self._pending_acks[msg_id] = ts
            self._save_cursor()

        # TRANSPORT: deliver to peer inbox
        self._deliver_to_peer_inbox(msg)

        logger.info("Sent %s: %s -> %s", msg_type.value, msg_id[:24], recipient)
        return msg_id

    def send_ack(self, msg_id: str) -> str:
        """Send acknowledgment for received message."""
        peer = Sender.GPT.value if self._role == Sender.CLAUDE else Sender.CLAUDE.value
        return self._send_message(peer, MessageType.ACK, {"acked": msg_id}, reply_to=msg_id)

    def send_response(self, task_id: str, result: Dict[str, Any]) -> str:
        """Send task response."""
        peer = Sender.GPT.value if self._role == Sender.CLAUDE else Sender.CLAUDE.value
        return self._send_message(peer, MessageType.RESPONSE, result, reply_to=task_id)

    def send_error(self, task_id: str, error: str) -> str:
        """Send error response."""
        peer = Sender.GPT.value if self._role == Sender.CLAUDE else Sender.CLAUDE.value
        return self._send_message(peer, MessageType.ERROR, {"error": error}, reply_to=task_id)

    def send_task(self, task_type: str, payload: Optional[Dict[str, Any]] = None) -> str:
        """
        Public API: send TASK to peer agent.

        payload dict will have task_type injected.
        """
        peer = Sender.GPT.value if self._role == Sender.CLAUDE else Sender.CLAUDE.value
        task_payload = dict(payload) if payload else {}
        task_payload["task_type"] = task_type
        return self._send_message(peer, MessageType.TASK, task_payload)

    def _resend_unacked(self, now: float) -> int:
        """
        Resend messages that haven't received ACK within timeout.

        Deterministic resend with minimum interval between attempts.
        """
        resent = 0

        for msg_id, sent_ts in sorted(self._pending_acks.items(), key=lambda kv: kv[1]):
            if resent >= MAX_RESENDS_PER_CYCLE:
                break

            # Check if timeout expired
            if now - sent_ts < ACK_TIMEOUT_SEC:
                continue

            # Check minimum interval between resends
            last_try = self._last_resend_attempt.get(msg_id, 0.0)
            if now - last_try < RESEND_MIN_INTERVAL_SEC:
                continue

            # Find message JSON in outbox by id
            for p in self._outbox.glob("*.json"):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if data.get("id") == msg_id:
                        self._deliver_to_peer_inbox(data)
                        self._last_resend_attempt[msg_id] = now
                        resent += 1
                        logger.info("Resent unacked: %s", msg_id[:24])
                        break
                except Exception:
                    continue

        if resent:
            self._save_cursor()

        return resent

    def process_cycle(self) -> Dict[str, Any]:
        """
        One deterministic cycle:
        0) Sync Friend Chat ACKs from acked_messages.json
        1) Sync external -> internal (ingest from Russian folders)
        2) Process internal inbox
        3) Sync internal -> external (mirror outbox to Russian folders)
        4) Resend unacked messages
        """
        ext_to_int = 0
        int_to_ext = 0

        # 0) Sync Friend Chat ACKs (clears pending_acks for messages ACKed via HTTP API)
        friend_chat_cleared = self._sync_friend_chat_acks()

        # 1) External -> internal ingest
        if self._role == Sender.CLAUDE:
            ext_to_int += _sync_external_to_internal(
                BASE_DIR / "ИИ Claude слушает 2026" / "inbox",
                CLAUDE_INBOX
            )
        else:
            ext_to_int += _sync_external_to_internal(
                BASE_DIR / "Чат GPT-5.2 слушает 2026" / "inbox",
                GPT_INBOX
            )

        # 2) Process internal inbox
        results: List[Dict[str, Any]] = []
        inbox_files = sorted(
            self._inbox.glob("*.json"),
            key=lambda p: p.stat().st_mtime
        )[:MAX_FILES_PER_CYCLE]

        for fp in inbox_files:
            msg = self._parse_message(fp)
            if msg is None:
                continue

            if msg.id in self._processed:
                fp.unlink(missing_ok=True)
                continue

            # Process by message type
            if msg.msg_type == MessageType.TASK:
                task_type = msg.payload.get("task_type", "unknown")

                # Debug tasks require --enable_debug_tasks=1 flag (no IPC activation)
                debug_tasks = {"file_read", "glob", "verify"}
                if task_type in debug_tasks and not self.is_debug_enabled():
                    self.send_error(msg.id, f"Debug task '{task_type}' disabled. Start agent with --enable_debug_tasks=1")
                    results.append({"id": msg.id, "status": "error", "error": "debug_disabled"})
                    self._processed.add(msg.id)
                    fp.unlink(missing_ok=True)
                    continue

                handler = self._handlers.get(task_type)

                if handler is None:
                    self.send_error(msg.id, f"Unknown task type: {task_type}")
                    results.append({"id": msg.id, "status": "error", "error": "unknown_task_type"})
                else:
                    out = handler(msg.payload)
                    self.send_response(msg.id, out)
                    results.append({"id": msg.id, "status": "success", "result": out})

            elif msg.msg_type in (MessageType.RESPONSE, MessageType.ERROR):
                self.send_ack(msg.id)
                results.append({"id": msg.id, "status": f"{msg.msg_type.value}_received"})

            elif msg.msg_type == MessageType.ACK:
                if msg.reply_to and msg.reply_to in self._pending_acks:
                    del self._pending_acks[msg.reply_to]
                    # Also clean up resend tracking
                    self._last_resend_attempt.pop(msg.reply_to, None)
                    self._save_cursor()
                results.append({"id": msg.id, "status": "ack_received", "acked": msg.reply_to})

            self._processed.add(msg.id)
            fp.unlink(missing_ok=True)

        if results:
            self._save_cursor()

        # 3) Internal -> external mirror (outbox)
        if self._role == Sender.CLAUDE:
            int_to_ext += _sync_internal_to_external(
                CLAUDE_OUTBOX,
                BASE_DIR / "ИИ Claude говорит 2026" / "outbox"
            )
        else:
            int_to_ext += _sync_internal_to_external(
                GPT_OUTBOX,
                BASE_DIR / "Чат GPT-5.2 говорит 2026" / "outbox"
            )

        # 4) Resend unacked
        resent = self._resend_unacked(time.time())

        return {
            "friend_chat_cleared": friend_chat_cleared,
            "ext_to_int": ext_to_int,
            "processed": len(results),
            "int_to_ext": int_to_ext,
            "resent": resent,
            "results": results,
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get agent statistics."""
        return {
            "role": self._role.value,
            "inbox_pending": len(list(self._inbox.glob("*.json"))),
            "outbox_pending": len(list(self._outbox.glob("*.json"))),
            "deadletter_count": len(list(DEADLETTER.glob("*.json"))),
            "processed_count": len(self._processed),
            "pending_acks_count": len(self._pending_acks),
            "handlers": list(self._handlers.keys()),
        }


# =============================================================================
# CLAUDE AGENT
# =============================================================================

class ClaudeAgent(IPCAgent):
    """Claude's IPC agent."""

    def __init__(self) -> None:
        super().__init__(
            role=Sender.CLAUDE,
            inbox=CLAUDE_INBOX,
            outbox=CLAUDE_OUTBOX,
            peer_inbox=GPT_INBOX,
        )


# =============================================================================
# GPT AGENT - with OpenAI integration
# =============================================================================

# GPT responses log file (human-readable)
GPT_RESPONSES_LOG = BASE_DIR / "state" / "gpt_responses.log"


def _load_openai_key() -> Optional[str]:
    """Load OpenAI API key from env file."""
    env_file = Path(r"C:\secrets\hope\.env")
    if not env_file.exists():
        return None
    try:
        text = env_file.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return None


def _log_gpt_response(question: str, answer: str, model: str = "gpt-4o") -> None:
    """Log GPT response to human-readable file."""
    GPT_RESPONSES_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = (
        f"\n{'='*60}\n"
        f"[{ts}] MODEL: {model}\n"
        f"{'─'*60}\n"
        f"QUESTION:\n{question}\n"
        f"{'─'*60}\n"
        f"ANSWER:\n{answer}\n"
        f"{'='*60}\n"
    )
    with open(GPT_RESPONSES_LOG, "a", encoding="utf-8") as f:
        f.write(entry)


class GPTAgent(IPCAgent):
    """GPT's IPC agent with OpenAI API integration."""

    def __init__(self) -> None:
        super().__init__(
            role=Sender.GPT,
            inbox=GPT_INBOX,
            outbox=GPT_OUTBOX,
            peer_inbox=CLAUDE_INBOX,
        )
        self._openai_key = _load_openai_key()
        self._openai_client = None
        if self._openai_key:
            try:
                import openai
                self._openai_client = openai.OpenAI(api_key=self._openai_key)
                logger.info("OpenAI client initialized")
            except ImportError:
                logger.warning("openai package not installed")
            except Exception as e:
                logger.error("OpenAI init error: %s", e)

    def _register_default_handlers(self) -> None:
        """Register GPT-specific handlers."""
        super()._register_default_handlers()
        self._handlers["analyze"] = self._handle_analyze
        self._handlers["ask"] = self._handle_ask

    def _handle_analyze(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze data using GPT."""
        data = payload.get("data", "")
        prompt = payload.get("prompt", "Analyze this data and provide insights:")

        if not self._openai_client:
            return {"error": "OpenAI client not available"}

        full_prompt = f"{prompt}\n\n{data}"

        try:
            response = self._openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a helpful trading assistant for HOPE system."},
                    {"role": "user", "content": full_prompt},
                ],
                max_tokens=2000,
            )
            answer = response.choices[0].message.content or ""

            # Log to human-readable file
            _log_gpt_response(full_prompt, answer, "gpt-4o")
            logger.info("GPT analyze complete, logged to gpt_responses.log")

            return {
                "success": True,
                "answer": answer,
                "model": "gpt-4o",
                "tokens": response.usage.total_tokens if response.usage else 0,
            }
        except Exception as e:
            logger.error("GPT analyze error: %s", e)
            return {"error": str(e)}

    def _handle_ask(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Ask GPT a question."""
        question = payload.get("question", payload.get("message", ""))

        if not question:
            return {"error": "No question provided"}

        if not self._openai_client:
            return {"error": "OpenAI client not available"}

        try:
            response = self._openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are GPT-5.2, a helpful AI assistant working in HOPE trading system alongside Claude. Be concise and practical."},
                    {"role": "user", "content": question},
                ],
                max_tokens=1500,
            )
            answer = response.choices[0].message.content or ""

            # Log to human-readable file
            _log_gpt_response(question, answer, "gpt-4o")
            logger.info("GPT ask complete, logged to gpt_responses.log")

            return {
                "success": True,
                "answer": answer,
                "model": "gpt-4o",
                "tokens": response.usage.total_tokens if response.usage else 0,
            }
        except Exception as e:
            logger.error("GPT ask error: %s", e)
            return {"error": str(e)}


# =============================================================================
# SINGLETONS
# =============================================================================

_claude_agent: Optional[ClaudeAgent] = None
_gpt_agent: Optional[GPTAgent] = None


def get_claude_agent() -> ClaudeAgent:
    """Get singleton Claude agent."""
    global _claude_agent
    if _claude_agent is None:
        _claude_agent = ClaudeAgent()
    return _claude_agent


def get_gpt_agent() -> GPTAgent:
    """Get singleton GPT agent."""
    global _gpt_agent
    if _gpt_agent is None:
        _gpt_agent = GPTAgent()
    return _gpt_agent


# =============================================================================
# TEST UTILITIES
# =============================================================================

def create_test_task(expression: str = "2+2") -> str:
    """
    Create a TASK as if sent by GPT -> Claude.

    Writes directly to internal claude_inbox.
    External sync will mirror it on next cycle.
    """
    ts = time.time()

    msg_fields = {
        "from": Sender.GPT.value,
        "to": Sender.CLAUDE.value,
        "timestamp": ts,
        "type": MessageType.TASK.value,
        "payload": {"task_type": "math", "expression": expression},
    }

    msg_id = _generate_id_from_message_fields(msg_fields)
    msg = dict(msg_fields)
    msg["id"] = msg_id

    CLAUDE_INBOX.mkdir(parents=True, exist_ok=True)
    dst = CLAUDE_INBOX / f"{msg_id[7:23]}_{msg_id[-8:]}.json"
    _atomic_write(dst, json.dumps(msg, indent=2, ensure_ascii=False))

    logger.info("Created test task: %s", msg_id[:24])
    return msg_id


# =============================================================================
# CLI ENTRYPOINT
# =============================================================================

def _parse_kv_args(argv: List[str]) -> Dict[str, str]:
    """Parse --key=value arguments."""
    out: Dict[str, str] = {}
    for a in argv[1:]:
        if a.startswith("--") and "=" in a:
            k, v = a[2:].split("=", 1)
            out[k.strip()] = v.strip()
    return out


def main() -> None:
    """CLI entrypoint with GetCommandLineW()."""
    setup_ipc_logging()
    init_ipc_folders()

    argv = get_argv()
    args = _parse_kv_args(argv)

    role = args.get("role", "claude")
    poll_sec = float(args.get("poll_sec", "2.0"))
    selftest = args.get("selftest", "")
    enable_debug = args.get("enable_debug_tasks", "0") == "1"

    # Select agent by role
    agent: IPCAgent
    if role == "claude":
        agent = ClaudeAgent()
    elif role == "gpt":
        agent = GPTAgent()
    else:
        logger.error("FAIL: Unknown role '%s'. Use --role=claude or --role=gpt", role)
        raise SystemExit(1)

    # Enable debug tasks if flag is set (ONLY via CLI, no IPC activation)
    if enable_debug:
        agent.enable_debug_handlers()

    # Selftest mode: create task and process one cycle
    if selftest:
        test_id = create_test_task(selftest)
        logger.info("Selftest task created: %s (expression: %s)", test_id[:24], selftest)

    logger.info("Agent start: role=%s poll=%.2fs debug=%s", role, poll_sec, enable_debug)
    logger.info("Inbox: %s", agent._inbox)
    logger.info("Outbox: %s", agent._outbox)
    logger.info("Peer inbox: %s", agent._peer_inbox)

    try:
        while True:
            cycle = agent.process_cycle()
            if cycle["processed"] > 0 or cycle["resent"] > 0 or cycle.get("friend_chat_cleared", 0) > 0:
                logger.info("Cycle: %s", {k: v for k, v in cycle.items() if k != "results"})
            time.sleep(poll_sec)
    except KeyboardInterrupt:
        logger.info("Agent stopped by user")


if __name__ == "__main__":
    main()

