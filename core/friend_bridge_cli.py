"""
Friend Bridge CLI Client (Hardened).

Commands:
    python -m core.friend_bridge_cli health
    python -m core.friend_bridge_cli send --to claude --message "your message"
    python -m core.friend_bridge_cli send --to gpt --message "your message"
    python -m core.friend_bridge_cli tail gpt --lines 50
    python -m core.friend_bridge_cli status
    python -m core.friend_bridge_cli last_sent

Env vars:
    FRIEND_BRIDGE_TOKEN - Auth token (required)
    FRIEND_BRIDGE_URL   - Base URL (default: http://127.0.0.1:8765)

Security:
    - Token loaded from env only (no file reads)
    - Never echoes tokens in output
    - Exit code 1 on all errors
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Optional

import requests

# Configuration
DEFAULT_URL = "http://127.0.0.1:8765"


def _load_token_from_env() -> str:
    """
    Load FRIEND_BRIDGE_TOKEN from environment only.

    Does NOT read from files to avoid accidental secret exposure.
    """
    return os.environ.get("FRIEND_BRIDGE_TOKEN", "")


def _get_base_url() -> str:
    """Get base URL from env or default."""
    return os.environ.get("FRIEND_BRIDGE_URL", DEFAULT_URL)


class BridgeClient:
    """HTTP client for Friend Bridge."""

    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None):
        self.base_url = (base_url or _get_base_url()).rstrip("/")
        self.token = token or _load_token_from_env()
        self._session = requests.Session()
        if self.token:
            self._session.headers["X-HOPE-Token"] = self.token

    def _request(
        self,
        method: str,
        path: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Make HTTP request."""
        url = f"{self.base_url}{path}"

        try:
            if method == "GET":
                resp = self._session.get(url, timeout=10)
            elif method == "POST":
                resp = self._session.post(url, json=data, timeout=10)
            else:
                return {"ok": False, "error": f"Unknown method: {method}"}

            return resp.json()

        except requests.exceptions.ConnectionError:
            return {"ok": False, "error": f"Connection refused: {url} (is bridge running?)"}
        except requests.exceptions.Timeout:
            return {"ok": False, "error": "Request timeout"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def health(self) -> Dict[str, Any]:
        """Check bridge health."""
        return self._request("GET", "/healthz")

    def send(self, to: str, message: str, context: str = "friend_chat") -> Dict[str, Any]:
        """Send message to Claude/GPT."""
        return self._request("POST", "/send", {
            "to": to,
            "message": message,
            "context": context,
        })

    def tail_gpt(self, lines: int = 50) -> Dict[str, Any]:
        """Get last N lines from GPT responses."""
        return self._request("GET", f"/tail/gpt?lines={lines}")

    def status(self) -> Dict[str, Any]:
        """Get IPC status."""
        return self._request("GET", "/ipc/status")

    def last_sent(self) -> Dict[str, Any]:
        """Get last sent message info."""
        return self._request("GET", "/last_sent")


def cmd_health(args: argparse.Namespace) -> int:
    """Health check command."""
    client = BridgeClient()
    result = client.health()

    if result.get("ok"):
        print("Bridge: OK")
        print(f"  service: {result.get('service')}")
        print(f"  version: {result.get('version')}")
        print(f"  time: {result.get('time_utc')}")
        print(f"  auth_enabled: {result.get('auth_enabled', 'unknown')}")
        return 0
    else:
        print("Bridge: FAIL")
        print(f"  error: {result.get('error')}")
        return 1


def cmd_send(args: argparse.Namespace) -> int:
    """Send message command."""
    if not args.message:
        print("ERROR: --message is required", file=sys.stderr)
        return 1

    client = BridgeClient()
    result = client.send(
        to=args.to,
        message=args.message,
        context=args.context or "friend_chat",
    )

    if result.get("ok"):
        print(f"SENT -> {args.to.upper()}")
        ipc_id = result.get("ipc_id", "?")
        print(f"  ipc_id: {ipc_id[:40]}..." if len(ipc_id) > 40 else f"  ipc_id: {ipc_id}")
        print(f"  file: {result.get('filename', '?')}")
        return 0
    else:
        print(f"FAILED: {result.get('error')}", file=sys.stderr)
        return 1


def cmd_tail(args: argparse.Namespace) -> int:
    """Tail GPT responses command."""
    client = BridgeClient()
    result = client.tail_gpt(lines=args.lines)

    if result.get("ok"):
        lines = result.get("lines", [])
        if not lines:
            print("(no lines)")
        else:
            print(f"=== GPT Responses (last {len(lines)} lines) ===")
            for line in lines:
                # Safe print with encoding fallback
                try:
                    print(line)
                except UnicodeEncodeError:
                    print(line.encode("utf-8", errors="replace").decode("utf-8"))
        return 0
    else:
        print(f"FAILED: {result.get('error')}", file=sys.stderr)
        return 1


def cmd_status(args: argparse.Namespace) -> int:
    """IPC status command."""
    client = BridgeClient()
    result = client.status()

    if result.get("ok"):
        healthy = result.get("healthy", False)
        claude = result.get("claude", {})
        gpt = result.get("gpt", {})

        print("=== IPC STATUS ===")
        print(f"Healthy: {'YES' if healthy else 'NO'}")
        print()
        print("Claude:")
        print(f"  inbox: {claude.get('inbox_pending', 0)}")
        print(f"  outbox: {claude.get('outbox_pending', 0)}")
        print()
        print("GPT:")
        print(f"  inbox: {gpt.get('inbox_pending', 0)}")
        print(f"  outbox: {gpt.get('outbox_pending', 0)}")
        print()
        print(f"Deadletter: {result.get('deadletter', 0)}")

        return 0 if healthy else 1
    else:
        print(f"FAILED: {result.get('error')}", file=sys.stderr)
        return 1


def cmd_last_sent(args: argparse.Namespace) -> int:
    """Show last sent message info."""
    client = BridgeClient()
    result = client.last_sent()

    if result.get("ok"):
        last = result.get("last_sent")
        if last is None:
            print("No message sent in current server session")
            return 0

        print("=== LAST SENT ===")
        print(f"  ok: {last.get('ok')}")
        print(f"  ipc_id: {last.get('ipc_id', '?')}")
        print(f"  to: {last.get('to', '?')}")
        print(f"  filename: {last.get('filename', '?')}")
        return 0
    else:
        print(f"FAILED: {result.get('error')}", file=sys.stderr)
        return 1


def main() -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Friend Bridge CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s health
  %(prog)s send --to claude --message "Hello!"
  %(prog)s send --to gpt --message "What's up?"
  %(prog)s tail gpt --lines 100
  %(prog)s status
  %(prog)s last_sent
        """,
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # health
    subparsers.add_parser("health", help="Check bridge health")

    # send
    send_parser = subparsers.add_parser("send", help="Send message to Claude/GPT")
    send_parser.add_argument("--to", required=True, choices=["claude", "gpt"], help="Recipient")
    send_parser.add_argument("--message", "-m", required=True, help="Message text")
    send_parser.add_argument("--context", default="friend_chat", help="Context (default: friend_chat)")

    # tail
    tail_parser = subparsers.add_parser("tail", help="Tail GPT responses")
    tail_parser.add_argument("target", choices=["gpt"], help="What to tail")
    tail_parser.add_argument("--lines", "-n", type=int, default=50, help="Lines (default: 50)")

    # status
    subparsers.add_parser("status", help="Show IPC status")

    # last_sent
    subparsers.add_parser("last_sent", help="Show last sent message info")

    args = parser.parse_args()

    commands = {
        "health": cmd_health,
        "send": cmd_send,
        "tail": cmd_tail,
        "status": cmd_status,
        "last_sent": cmd_last_sent,
    }

    handler = commands.get(args.command)
    if handler:
        return handler(args)

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
