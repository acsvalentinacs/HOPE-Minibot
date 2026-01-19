"""
Friend Bridge HTTP Server (Hardened).

Localhost-only HTTP API for Friend Chat.
Uses core.chat_dispatch for message sending (single source of truth).

Endpoints:
    GET  /healthz        - Health check (no auth required)
    POST /send           - Send message to Claude/GPT
    GET  /tail/gpt       - Tail GPT responses log
    GET  /ipc/status     - IPC system status
    GET  /last_sent      - Last sent message info (artifact-based proof)
    GET  /inbox/{agent}  - Poll inbox for agent (gpt or claude)
                           Query params: after, limit, type

Auth:
    Header: X-HOPE-Token must match FRIEND_BRIDGE_TOKEN from env

Security (FAIL-CLOSED):
    - Bind: 127.0.0.1 only (no external access)
    - Auth: REQUIRED by default. Server refuses to start without token
            unless --insecure flag is explicitly provided.
    - Never logs or echoes tokens/secrets.

Portable Paths:
    - All paths derived from Path(__file__).resolve()
    - No sys.path manipulation
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

# Portable path derivation (no sys.path hacks)
_THIS_FILE = Path(__file__).resolve()
_CORE_DIR = _THIS_FILE.parent
_MINIBOT_DIR = _CORE_DIR.parent
_STATE_DIR = _MINIBOT_DIR / "state"
_IPC_DIR = _MINIBOT_DIR / "ipc"

# Import from chat_dispatch (same package, no sys.path needed)
from core.chat_dispatch import (
    send_chat_tracked,
    get_last_sent,
    get_ipc_status,
    MAX_MESSAGE_LEN,
    VALID_RECIPIENTS,
)

logger = logging.getLogger("friend_bridge")

# Configuration
DEFAULT_PORT = 8765
BIND_HOST = "127.0.0.1"  # Localhost only, no external access
VERSION = "1.3.0"  # Bumped for timestamp-based cursor (fixes ordering bug)
MAX_INBOX_LIMIT = 200


def _load_token_from_env() -> str:
    """
    Load FRIEND_BRIDGE_TOKEN from environment only.

    Does NOT read from files to avoid accidental secret exposure.
    User must set env var manually or via secure mechanism.
    """
    return os.environ.get("FRIEND_BRIDGE_TOKEN", "")


def _safe_int(value: str, default: int) -> int:
    """Safely parse int with fallback."""
    try:
        return int(value)
    except Exception:
        return default


def _read_json_file(path: Path) -> Optional[Dict[str, Any]]:
    """Read JSON file, return None on error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _parse_cursor(cursor: str) -> Tuple[float, str]:
    """
    Parse cursor in format "{timestamp}_{filename}" or legacy "{filename}".

    Returns (timestamp, filename) tuple for comparison.
    Legacy cursors (filename only) are treated as timestamp=0.
    """
    if not cursor:
        return (0.0, "")

    if "_" in cursor and cursor.split("_", 1)[0].replace(".", "").isdigit():
        # New format: "1768836058.219_filename.json"
        parts = cursor.split("_", 1)
        try:
            return (float(parts[0]), parts[1] if len(parts) > 1 else "")
        except ValueError:
            pass

    # Legacy format or parse failure: treat as filename with timestamp=0
    return (0.0, cursor)


def _make_cursor(timestamp: float, filename: str) -> str:
    """Create cursor in format "{timestamp}_{filename}"."""
    return f"{timestamp:.6f}_{filename}"


def _list_inbox_messages(
    source_dir: Path,
    *,
    to_prefix: str,
    after: str = "",
    msg_type: str = "",
    limit: int = 50,
) -> Tuple[List[Dict[str, Any]], str]:
    """
    List IPC messages from source_dir without modifying or deleting them.

    Cursoring is timestamp-based (monotonic):
      - Cursor format: "{message.timestamp}_{filename}"
      - Messages sorted by (timestamp, filename) - MONOTONIC ordering
      - Fixes bug where sha256-prefixed filenames could be lexicographically
        smaller than cursor, causing message loss.

    Args:
        source_dir: Directory to read messages from
        to_prefix: Filter messages by recipient prefix (e.g. "gpt", "claude")
        after: Cursor in format "{timestamp}_{filename}" or legacy "{filename}"
        msg_type: Filter by message type (response, ack, chat)
        limit: Max messages to return

    Returns:
        Tuple of (messages list, next_after cursor)
    """
    limit = max(1, min(limit, MAX_INBOX_LIMIT))

    if not source_dir.exists():
        return [], after

    # Parse cursor
    cursor_ts, cursor_fname = _parse_cursor(after)

    # Read all files with their message timestamps
    file_data: List[Tuple[float, str, Path, Dict[str, Any]]] = []

    for f in source_dir.glob("*.json"):
        if not f.is_file():
            continue

        d = _read_json_file(f)
        if not d:
            continue

        # Get message timestamp (required field per IPC v2.1)
        msg_ts = d.get("timestamp", 0.0)
        if not isinstance(msg_ts, (int, float)):
            msg_ts = 0.0

        file_data.append((float(msg_ts), f.name, f, d))

    # Sort by (timestamp, filename) - MONOTONIC ordering
    file_data.sort(key=lambda x: (x[0], x[1]))

    out: List[Dict[str, Any]] = []
    next_after = after

    for msg_ts, fname, f, d in file_data:
        # Skip if <= cursor (timestamp first, then filename as tie-break)
        if (msg_ts, fname) <= (cursor_ts, cursor_fname):
            continue

        # Filter by message type if requested
        if msg_type and d.get("type") != msg_type:
            continue

        # Filter by recipient prefix (gpt / claude)
        to_val = (d.get("to") or "").lower()
        if to_prefix and not to_val.startswith(to_prefix):
            continue

        st = f.stat()
        out.append({
            "filename": fname,
            "mtime_utc": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            "timestamp": msg_ts,
            "id": d.get("id"),
            "reply_to": d.get("reply_to"),
            "type": d.get("type"),
            "from": d.get("from"),
            "to": d.get("to"),
            "payload": d.get("payload"),
        })
        next_after = _make_cursor(msg_ts, fname)

        if len(out) >= limit:
            break

    return out, next_after


def tail_gpt_responses(lines: int = 50) -> Dict[str, Any]:
    """
    Read last N lines from GPT responses log.

    Args:
        lines: Number of lines to return (default 50, max 500)

    Returns:
        Dict with lines array
    """
    lines = min(max(1, lines), 500)  # Clamp to 1-500

    log_file = _STATE_DIR / "gpt_responses.log"

    if not log_file.exists():
        return {"ok": True, "lines": [], "note": "Log file not found"}

    try:
        all_lines = log_file.read_text(encoding="utf-8").splitlines()
        return {"ok": True, "lines": all_lines[-lines:]}
    except Exception as e:
        logger.error("Error reading GPT log: %s", e)
        return {"ok": False, "error": str(e)}


class FriendBridgeHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Friend Bridge."""

    # Class-level token (set at server start)
    auth_token: str = ""
    # Insecure mode flag (must be explicitly enabled)
    insecure_mode: bool = False

    def log_message(self, format: str, *args: Any) -> None:
        """Override to use logger instead of stderr."""
        # Never log request details that might contain tokens
        logger.debug("%s - %s", self.address_string(), format % args)

    def _send_json(self, data: Dict[str, Any], status: int = 200) -> None:
        """Send JSON response."""
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        """
        Check X-HOPE-Token header.

        FAIL-CLOSED: If auth_token is set, request MUST provide matching token.
        """
        if self.insecure_mode:
            # Explicit insecure mode - allow without token
            return True

        if not self.auth_token:
            # This should never happen if server startup is correct
            self._send_json(
                {"ok": False, "error": "Server misconfigured: no auth token"},
                status=500,
            )
            return False

        token = self.headers.get("X-HOPE-Token", "")
        if token != self.auth_token:
            self._send_json({"ok": False, "error": "Unauthorized"}, status=401)
            return False
        return True

    def do_GET(self) -> None:
        """Handle GET requests."""
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # Health check - no auth required
        if path == "/healthz":
            self._send_json({
                "ok": True,
                "service": "friend_bridge",
                "version": VERSION,
                "time_utc": datetime.now(timezone.utc).isoformat(),
                "auth_enabled": bool(self.auth_token) and not self.insecure_mode,
            })
            return

        # All other endpoints require auth
        if not self._check_auth():
            return

        if path == "/tail/gpt":
            lines = int(query.get("lines", ["50"])[0])
            result = tail_gpt_responses(lines)
            self._send_json(result)

        elif path == "/ipc/status":
            result = get_ipc_status()
            self._send_json(result)

        elif path == "/last_sent":
            # Artifact-based proof endpoint
            last = get_last_sent()
            if last is None:
                self._send_json({
                    "ok": True,
                    "last_sent": None,
                    "note": "No message sent in this server session",
                })
            else:
                self._send_json({"ok": True, "last_sent": last})

        elif path.startswith("/inbox/"):
            # Polling inbox endpoint: GET /inbox/gpt?after=<filename>&limit=50&type=response
            parts = [p for p in path.split("/") if p]
            if len(parts) != 2:
                self._send_json({"ok": False, "error": "Bad inbox path"}, status=400)
                return

            agent = parts[1].lower()
            after = (query.get("after", [""])[0] or "").strip()
            limit = _safe_int(query.get("limit", ["50"])[0], 50)
            msg_type = (query.get("type", [""])[0] or "").strip().lower()

            # Validate type filter
            if msg_type and msg_type not in {"response", "ack", "chat"}:
                self._send_json({"ok": False, "error": "Invalid type filter"}, status=400)
                return

            # Map agent to source directory and filter prefix
            # GPT reads from gpt_inbox (messages TO GPT)
            # Claude reads from claude_inbox (messages TO Claude)
            if agent == "gpt":
                source_dir = _IPC_DIR / "gpt_inbox"
                to_prefix = "gpt"
            elif agent == "claude":
                source_dir = _IPC_DIR / "claude_inbox"
                to_prefix = "claude"
            else:
                self._send_json({"ok": False, "error": "Unknown agent"}, status=400)
                return

            messages, next_after = _list_inbox_messages(
                source_dir,
                to_prefix=to_prefix,
                after=after,
                msg_type=msg_type,
                limit=limit,
            )
            self._send_json({
                "ok": True,
                "agent": agent,
                "source_dir": str(source_dir),
                "count": len(messages),
                "after": after,
                "next_after": next_after,
                "messages": messages,
            })

        else:
            self._send_json({"ok": False, "error": "Not found"}, status=404)

    def do_POST(self) -> None:
        """Handle POST requests."""
        if not self._check_auth():
            return

        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/send":
            # Read body
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 10000:
                self._send_json({"ok": False, "error": "Request too large"}, status=400)
                return

            body = self.rfile.read(content_length).decode("utf-8")

            try:
                data = json.loads(body)
            except json.JSONDecodeError as e:
                self._send_json({"ok": False, "error": f"Invalid JSON: {e}"}, status=400)
                return

            # Validate fields
            to = data.get("to", "").lower()
            message = data.get("message", "")
            context = data.get("context", "friend_chat")

            if to not in VALID_RECIPIENTS:
                self._send_json(
                    {"ok": False, "error": f"'to' must be one of: {VALID_RECIPIENTS}"},
                    status=400,
                )
                return

            if not message:
                self._send_json({"ok": False, "error": "'message' is required"}, status=400)
                return

            if len(message) > MAX_MESSAGE_LEN:
                self._send_json(
                    {"ok": False, "error": f"Message too long (max {MAX_MESSAGE_LEN} chars)"},
                    status=400,
                )
                return

            # Send message (tracked for /last_sent)
            result = send_chat_tracked(to, message, context)
            status = 200 if result.ok else 400
            self._send_json(result.to_dict(), status=status)

        else:
            self._send_json({"ok": False, "error": "Not found"}, status=404)


def run_server(
    port: int = DEFAULT_PORT,
    token: Optional[str] = None,
    insecure: bool = False,
) -> None:
    """
    Run Friend Bridge HTTP server.

    FAIL-CLOSED: Server refuses to start without token unless --insecure is set.

    Args:
        port: Port to listen on (default 8765)
        token: Auth token (default from env FRIEND_BRIDGE_TOKEN)
        insecure: If True, allow running without token (DANGEROUS)
    """
    # Load token
    auth_token = token or _load_token_from_env()

    # FAIL-CLOSED: Refuse to start without token unless explicitly insecure
    if not auth_token and not insecure:
        logger.error(
            "FAIL-CLOSED: FRIEND_BRIDGE_TOKEN not set. "
            "Set the environment variable or use --insecure flag (not recommended)."
        )
        sys.exit(1)

    if insecure:
        logger.warning(
            "INSECURE MODE: Running without authentication. "
            "This is NOT recommended for any use beyond local testing."
        )

    # Set handler config
    FriendBridgeHandler.auth_token = auth_token
    FriendBridgeHandler.insecure_mode = insecure

    # Create server (localhost only)
    server_address = (BIND_HOST, port)
    httpd = HTTPServer(server_address, FriendBridgeHandler)

    logger.info("Friend Bridge v%s starting on http://%s:%d", VERSION, BIND_HOST, port)
    logger.info("Auth: %s", "ENABLED" if auth_token and not insecure else "DISABLED (insecure)")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        httpd.shutdown()


def main() -> int:
    """CLI entrypoint."""
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Friend Bridge HTTP Server")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Allow running without FRIEND_BRIDGE_TOKEN (NOT RECOMMENDED)",
    )

    args = parser.parse_args()

    run_server(port=args.port, insecure=args.insecure)
    return 0


if __name__ == "__main__":
    sys.exit(main())
