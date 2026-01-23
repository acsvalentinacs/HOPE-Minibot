# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 12:20:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 17:05:00 UTC
# === END SIGNATURE ===
"""
NEXUS - Friend Chat Terminal UI (Command Center) with Black Box Persistence.

ARCHITECTURE DECISION:
    NEXUS listens to inbox/nexus (NOT inbox/claude) to avoid race conditions
    with the executor. The GPT Orchestrator must reply to=nexus for user responses.

    Work channel:     inbox/claude -> executor only
    Operator channel: inbox/nexus  -> NEXUS UI only

BLACK BOX PERSISTENCE:
    All messages (sent + received) are persisted to:
    state/nexus_history.jsonl

    Format: sha256-prefixed JSONL (Canon B)
    Features: inter-process locking, fsync, rotation by size

HOPE_MODE (LIVE PROTECTION):
    When HOPE_MODE=LIVE in environment:
    - /inbox claude command is BLOCKED (prevents executor race)
    - Warning banner shown at startup

USAGE:
    python -m tools.nexus
    python tools/nexus.py --poll-ms 1000
    python tools/nexus.py --no-history  # Disable persistence

COMMANDS:
    /help           - Show help
    /target gpt     - Send messages to GPT (default)
    /target claude  - Send messages directly to Claude
    /inbox nexus    - Listen to inbox/nexus (default, safe)
    /inbox claude   - Listen to inbox/claude (BLOCKED in LIVE mode)
    /status         - Show current settings
    /history        - Show recent message history
    /verify         - Verify history file integrity
    /clear          - Clear screen
    /exit           - Exit NEXUS

REQUIREMENTS:
    pip install rich prompt_toolkit
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Deque
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Add parent to path for imports
_THIS = Path(__file__).resolve()
_TOOLS_DIR = _THIS.parent
_MINIBOT_DIR = _TOOLS_DIR.parent
if str(_MINIBOT_DIR) not in sys.path:
    sys.path.insert(0, str(_MINIBOT_DIR))

try:
    from rich.console import Console
    from rich.text import Text
    from rich.panel import Panel
    from rich.table import Table
    from prompt_toolkit import PromptSession
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.history import InMemoryHistory
except ImportError as e:
    print(f"FAIL-CLOSED: Missing dependency: {e}")
    print("Install with: pip install rich prompt_toolkit")
    sys.exit(1)

# Import history module (optional - graceful degradation)
try:
    from core.nexus_history import append_history, load_history, verify_history, count_history
    HISTORY_AVAILABLE = True
except ImportError:
    HISTORY_AVAILABLE = False
    def append_history(*args, **kwargs): pass
    def load_history(*args, **kwargs): return []
    def verify_history(*args, **kwargs): return (0, 0)
    def count_history(*args, **kwargs): return 0


# === CONFIGURATION ===

BRIDGE_URL_DEFAULT = "https://bridge.acsvalentinacs.com"
SECRETS_PATH = Path(r"C:\secrets\hope\.env")
MAX_SEEN_IDS = 500  # Ring buffer for deduplication (by insertion order, not sorted!)
POLL_INTERVAL_DEFAULT_MS = 1000

# HOPE_MODE: LIVE mode blocks dangerous commands
HOPE_MODE = os.environ.get("HOPE_MODE", "DEV").upper()
IS_LIVE_MODE = HOPE_MODE == "LIVE"


# === SECRETS LOADING (fail-closed) ===

def _load_secret(key: str) -> str:
    """Load secret from .env file. Returns empty string if not found."""
    # Try environment variable first
    val = os.environ.get(key)
    if val:
        return val.strip()

    # Try secrets file
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
    except Exception:
        pass

    return ""


# === DATA STRUCTURES ===

@dataclass(frozen=True)
class BridgeMessage:
    """Immutable message from Friend Bridge."""
    id: str
    type: str
    reply_to: Optional[str]
    payload: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)


class SeenIdsBuffer:
    """
    Ring buffer for message deduplication.

    CRITICAL FIX: Uses deque with maxlen for true FIFO ordering,
    NOT sorted(set)[-N:] which preserves lexicographically largest.
    """

    def __init__(self, maxlen: int = MAX_SEEN_IDS) -> None:
        self._ids: Deque[str] = deque(maxlen=maxlen)
        self._set: set = set()

    def add(self, msg_id: str) -> bool:
        """Add ID, return True if it was new."""
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


# === BRIDGE CLIENT ===

class FriendBridgeClient:
    """HTTP client for Friend Bridge API."""

    def __init__(self, base_url: str, token: str, timeout_s: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_s = timeout_s

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        """Execute HTTP request, return parsed JSON."""
        url = f"{self.base_url}{path}"
        headers = {"X-HOPE-Token": self.token}
        data = None

        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")

        req = Request(url, data=data, headers=headers, method=method)

        with urlopen(req, timeout=self.timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}

    def send(
        self,
        to: str,
        type_: str,
        payload: dict,
        reply_to: Optional[str] = None,
    ) -> str:
        """Send message, return IPC ID."""
        body: Dict[str, Any] = {"to": to, "type": type_, "payload": payload}
        if reply_to:
            body["reply_to"] = reply_to

        res = self._request("POST", "/send", body=body)
        return str(res.get("ipc_id") or res.get("id") or "")

    def inbox(
        self,
        name: str,
        limit: int = 20,
        order: str = "desc",
        after: str = "",
    ) -> List[BridgeMessage]:
        """Poll inbox, return messages."""
        path = f"/inbox/{name}?limit={limit}&order={order}"
        if after:
            path += f"&after={after}"

        res = self._request("GET", path)
        out: List[BridgeMessage] = []

        for m in (res.get("messages") or []):
            out.append(
                BridgeMessage(
                    id=str(m.get("id") or ""),
                    type=str(m.get("type") or ""),
                    reply_to=(str(m.get("reply_to")) if m.get("reply_to") else None),
                    payload=dict(m.get("payload") or {}),
                )
            )

        return out

    def health(self) -> bool:
        """Check bridge health."""
        try:
            res = self._request("GET", "/healthz")
            return res.get("status") == "ok"
        except Exception:
            return False


# === NEXUS APPLICATION ===

class NexusApp:
    """
    NEXUS - Friend Chat Terminal UI with Black Box Persistence.

    ARCHITECTURE:
        - Listens to inbox/nexus (NOT inbox/claude) by default
        - Sends to target (gpt or claude)
        - Uses ring buffer for deduplication (FIFO, not sorted)
        - Thread-safe message queue for UI updates
        - Persists all messages to JSONL history (Black Box)

    LIVE MODE PROTECTION:
        When HOPE_MODE=LIVE, /inbox claude is blocked to prevent
        race conditions with the executor.
    """

    def __init__(
        self,
        bridge: FriendBridgeClient,
        inbox_name: str = "nexus",
        poll_interval_ms: int = POLL_INTERVAL_DEFAULT_MS,
        enable_history: bool = True,
    ) -> None:
        self.bridge = bridge
        self.console = Console()
        self.inbox_name = inbox_name
        self.poll_interval_s = poll_interval_ms / 1000.0

        # Current target for sending
        self.target = "gpt"
        self.identity = "nexus"

        # Black Box persistence
        self.history_enabled = enable_history and HISTORY_AVAILABLE

        # Deduplication (ring buffer, not sorted set!)
        self._seen = SeenIdsBuffer(MAX_SEEN_IDS)

        # Threading
        self._stop = threading.Event()
        self._events: "queue.Queue[tuple[str, Any]]" = queue.Queue()

        # In-memory message history for display
        self._history: List[Dict[str, Any]] = []

        # Load persistent history on startup
        if self.history_enabled:
            self._load_persistent_history()

    def _load_persistent_history(self) -> None:
        """Load history from disk on startup."""
        try:
            entries = load_history(limit=50)
            for e in entries:
                # Mark IDs as seen to prevent re-processing
                if "id" in e:
                    self._seen.add(e["id"])
                self._history.append(e)

            if entries:
                self.console.print(
                    f"[dim]Loaded {len(entries)} messages from history[/dim]"
                )
        except Exception as ex:
            self.console.print(f"[yellow]History load warning: {ex}[/yellow]")

    def _save_to_history(self, entry: Dict[str, Any]) -> None:
        """Persist entry to Black Box (async-safe)."""
        if not self.history_enabled:
            return

        try:
            # Add timestamp if missing
            if "timestamp" not in entry:
                entry["timestamp"] = datetime.now(timezone.utc).isoformat()

            append_history(entry)
        except Exception as ex:
            # Non-fatal: log but don't crash
            self.console.print(f"[yellow]History save warning: {ex}[/yellow]")

    def _fmt_time(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _fmt_incoming(self, sender: str, text: str, reply_to: Optional[str]) -> Text:
        """Format incoming message."""
        line = Text()
        line.append(f"[{self._fmt_time()}] ", style="bright_black")
        line.append(f"{sender}", style="bold green")
        line.append(" -> ", style="dim")
        line.append("YOU", style="bold cyan")
        if reply_to:
            line.append(f" (re:{reply_to[:12]}...)", style="dim")
        line.append("\n")
        line.append(text)
        return line

    def _fmt_outgoing(self, target: str, text: str) -> Text:
        """Format outgoing message."""
        line = Text()
        line.append(f"[{self._fmt_time()}] ", style="bright_black")
        line.append("YOU", style="bold cyan")
        line.append(" -> ", style="dim")
        line.append(target.upper(), style="bold yellow")
        line.append("\n")
        line.append(text)
        return line

    def _extract_message(self, m: BridgeMessage) -> tuple[str, str]:
        """Extract sender and text from message payload."""
        payload = m.payload or {}

        # Sender
        sender = payload.get("from") or payload.get("context") or "unknown"

        # Text (try multiple fields)
        text = (
            payload.get("message")
            or payload.get("response")
            or payload.get("text")
            or payload.get("description")
        )

        if text is None:
            # Fallback to JSON
            text = json.dumps(payload, ensure_ascii=False, indent=2)

        return str(sender), str(text)

    def poll_worker(self) -> None:
        """Background thread: poll inbox for new messages."""
        while not self._stop.is_set():
            try:
                msgs = self.bridge.inbox(self.inbox_name, limit=30, order="desc")

                # Process new messages (newest first, so reverse for display)
                new_msgs = [m for m in msgs if m.id and self._seen.add(m.id)]
                new_msgs.reverse()

                for m in new_msgs:
                    sender, text = self._extract_message(m)
                    self._events.put(("msg_in", (sender, text, m.reply_to, m.type)))

                    # Build history entry
                    history_entry = {
                        "id": m.id,
                        "from": sender,
                        "text": text,
                        "time": self._fmt_time(),
                        "direction": "in",
                        "type": m.type,
                        "inbox": self.inbox_name,
                    }

                    # Store in memory
                    self._history.append(history_entry)

                    # Persist to Black Box
                    self._save_to_history(history_entry)

            except (HTTPError, URLError) as e:
                self._events.put(("error", f"Network: {e}"))
            except Exception as e:
                self._events.put(("error", f"Poll: {e}"))

            time.sleep(self.poll_interval_s)

    def send_message(self, text: str) -> Optional[str]:
        """Send message to current target."""
        try:
            payload = {
                "task_type": "chat",
                "message": text,
                "from": self.identity,
                "context": "nexus_ui",
            }

            msg_id = self.bridge.send(
                to=self.target,
                type_="task",
                payload=payload,
            )

            # Build history entry
            history_entry = {
                "id": msg_id,
                "to": self.target,
                "text": text,
                "time": self._fmt_time(),
                "direction": "out",
                "from": self.identity,
            }

            # Store in memory
            self._history.append(history_entry)

            # Persist to Black Box
            self._save_to_history(history_entry)

            return msg_id

        except Exception as e:
            self._events.put(("error", f"Send failed: {e}"))
            return None

    def handle_command(self, cmd: str) -> bool:
        """
        Handle slash command.
        Returns True if should continue, False to exit.
        """
        parts = cmd.strip().split(maxsplit=1)
        command = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if command in ("/exit", "/quit", "/q"):
            return False

        if command == "/help":
            self._show_help()
            return True

        if command == "/target":
            if arg in ("gpt", "claude"):
                self.target = arg
                self.console.print(f"[green]Target set to: {arg.upper()}[/green]")
            else:
                self.console.print("[yellow]Usage: /target gpt|claude[/yellow]")
            return True

        if command == "/inbox":
            if arg in ("nexus", "claude", "user", "gpt"):
                # LIVE MODE PROTECTION: Block inbox/claude
                if arg == "claude" and IS_LIVE_MODE:
                    self.console.print(
                        "[bold red]BLOCKED: /inbox claude is disabled in LIVE mode![/bold red]"
                    )
                    self.console.print(
                        "[yellow]Reason: Prevents race condition with executor.[/yellow]"
                    )
                    self.console.print(
                        "[dim]Set HOPE_MODE=DEV to enable (not recommended).[/dim]"
                    )
                    return True

                old = self.inbox_name
                self.inbox_name = arg
                self._seen = SeenIdsBuffer(MAX_SEEN_IDS)  # Reset seen
                self.console.print(f"[green]Inbox changed: {old} -> {arg}[/green]")
                if arg == "claude":
                    self.console.print(
                        "[bold red]WARNING: inbox/claude conflicts with executor![/bold red]"
                    )
            else:
                self.console.print("[yellow]Usage: /inbox nexus|claude|user[/yellow]")
            return True

        if command == "/status":
            self._show_status()
            return True

        if command == "/clear":
            self.console.clear()
            return True

        if command == "/history":
            self._show_history()
            return True

        if command == "/verify":
            self._verify_history()
            return True

        self.console.print(f"[red]Unknown command: {command}[/red]")
        self.console.print("[dim]Type /help for commands[/dim]")
        return True

    def _show_help(self) -> None:
        """Display help panel."""
        live_note = "[red](BLOCKED in LIVE mode)[/red]" if IS_LIVE_MODE else "[yellow](dangerous)[/yellow]"

        help_text = f"""[bold cyan]NEXUS Commands:[/bold cyan]

[yellow]/help[/yellow]           Show this help
[yellow]/target gpt|claude[/yellow]  Set message recipient
[yellow]/inbox nexus|claude[/yellow] Set inbox to listen (nexus=safe)
                    inbox/claude {live_note}
[yellow]/status[/yellow]         Show current settings
[yellow]/history[/yellow]        Show message history (in-memory)
[yellow]/verify[/yellow]         Verify Black Box integrity
[yellow]/clear[/yellow]          Clear screen
[yellow]/exit[/yellow]           Exit NEXUS

[dim]Just type text to send a message to current target.[/dim]
[dim]History is {"enabled" if self.history_enabled else "disabled"} (Black Box persistence).[/dim]"""

        self.console.print(Panel(help_text, title="NEXUS Help", border_style="cyan"))

    def _show_status(self) -> None:
        """Display current status."""
        table = Table(title="NEXUS Status", show_header=False)
        table.add_column("Key", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Target", self.target.upper())
        table.add_row("Inbox", self.inbox_name)
        table.add_row("Poll interval", f"{int(self.poll_interval_s * 1000)}ms")
        table.add_row("Seen messages", str(len(self._seen)))
        table.add_row("Session history", str(len(self._history)))
        table.add_row("Bridge", self.bridge.base_url)

        # HOPE_MODE
        mode_color = "[red]LIVE[/red]" if IS_LIVE_MODE else "[green]DEV[/green]"
        table.add_row("HOPE_MODE", mode_color)

        # Black Box status
        if self.history_enabled:
            try:
                disk_count = count_history()
                table.add_row("Black Box", f"[green]ENABLED[/green] ({disk_count} entries)")
            except Exception:
                table.add_row("Black Box", "[yellow]ENABLED (read error)[/yellow]")
        else:
            table.add_row("Black Box", "[dim]DISABLED[/dim]")

        # Health check
        try:
            healthy = self.bridge.health()
            table.add_row("Bridge status", "[green]OK[/green]" if healthy else "[red]DOWN[/red]")
        except Exception:
            table.add_row("Bridge status", "[red]ERROR[/red]")

        self.console.print(table)

    def _show_history(self) -> None:
        """Display message history (in-memory, this session)."""
        if not self._history:
            self.console.print("[dim]No messages in session yet[/dim]")
            return

        self.console.print(f"[dim]Showing last 10 of {len(self._history)} session messages:[/dim]")

        for h in self._history[-10:]:  # Last 10
            direction = h.get("direction", "?")
            time_str = h.get("time", "?")
            text = h.get("text", "")[:50]

            if direction == "in":
                sender = h.get("from", "?")
                self.console.print(
                    f"[bright_black]{time_str}[/bright_black] "
                    f"[green]{sender}[/green] -> YOU: {text}..."
                )
            else:
                target = h.get("to", "?")
                self.console.print(
                    f"[bright_black]{time_str}[/bright_black] "
                    f"YOU -> [yellow]{target}[/yellow]: {text}..."
                )

    def _verify_history(self) -> None:
        """Verify Black Box history file integrity."""
        if not self.history_enabled:
            self.console.print("[yellow]Black Box history is disabled.[/yellow]")
            return

        try:
            valid, corrupted = verify_history()

            table = Table(title="Black Box Integrity Check", show_header=False)
            table.add_column("Metric", style="cyan")
            table.add_column("Value", style="green")

            table.add_row("Valid entries", str(valid))
            table.add_row(
                "Corrupted entries",
                f"[green]{corrupted}[/green]" if corrupted == 0 else f"[red]{corrupted}[/red]"
            )
            table.add_row(
                "Integrity",
                "[green]PASS[/green]" if corrupted == 0 else "[red]FAIL[/red]"
            )

            self.console.print(table)

            if corrupted > 0:
                self.console.print(
                    f"[yellow]Warning: {corrupted} line(s) have SHA256 mismatch.[/yellow]"
                )

        except Exception as ex:
            self.console.print(f"[red]Verify failed: {ex}[/red]")

    def _process_events(self) -> None:
        """Process queued events (non-blocking)."""
        while True:
            try:
                event_type, data = self._events.get_nowait()
            except queue.Empty:
                break

            if event_type == "msg_in":
                sender, text, reply_to, msg_type = data
                self.console.print()
                self.console.print(
                    Panel(
                        self._fmt_incoming(sender, text, reply_to),
                        title=f"[green]{sender}[/green] [{msg_type}]",
                        border_style="green",
                    )
                )

            elif event_type == "error":
                self.console.print(f"[red]Error: {data}[/red]")

    def run(self) -> None:
        """Main loop with TUI."""
        # Show banner
        mode_indicator = "[red][LIVE][/red]" if IS_LIVE_MODE else "[green][DEV][/green]"
        history_indicator = "[green]ON[/green]" if self.history_enabled else "[dim]OFF[/dim]"

        self.console.print(
            Panel(
                f"[bold cyan]NEXUS[/bold cyan] - Friend Chat Command Center {mode_indicator}\n"
                f"[dim]Listening: inbox/{self.inbox_name} | Target: {self.target}[/dim]\n"
                f"[dim]Black Box: {history_indicator} | Type /help for commands[/dim]",
                border_style="cyan",
            )
        )

        # LIVE mode warning
        if IS_LIVE_MODE:
            self.console.print(
                "[bold yellow]⚠ LIVE MODE: /inbox claude command is blocked.[/bold yellow]"
            )

        # Start poll thread
        poll_thread = threading.Thread(target=self.poll_worker, daemon=True)
        poll_thread.start()

        # Create prompt session
        session: PromptSession = PromptSession(
            history=InMemoryHistory(),
        )

        try:
            with patch_stdout():
                while not self._stop.is_set():
                    # Process any pending events
                    self._process_events()

                    try:
                        # Get user input
                        prompt = f"[{self.target}]> "
                        user_input = session.prompt(prompt)

                        if not user_input.strip():
                            continue

                        # Handle command or send message
                        if user_input.startswith("/"):
                            if not self.handle_command(user_input):
                                break
                        else:
                            # Send message
                            msg_id = self.send_message(user_input)
                            if msg_id:
                                self.console.print(
                                    Panel(
                                        self._fmt_outgoing(self.target, user_input),
                                        title=f"[cyan]YOU[/cyan] -> [yellow]{self.target.upper()}[/yellow]",
                                        border_style="cyan",
                                    )
                                )
                                self.console.print(f"[dim]Sent: {msg_id[:24]}...[/dim]")

                    except KeyboardInterrupt:
                        self.console.print("\n[yellow]Use /exit to quit[/yellow]")
                    except EOFError:
                        break

        finally:
            self._stop.set()
            self.console.print("[dim]NEXUS terminated[/dim]")


# === MAIN ===

def main() -> int:
    """CLI entry point."""
    # HOPE-LAW-001: Policy bootstrap MUST be first
    from core.policy.bootstrap import bootstrap
    bootstrap("nexus", network_profile="core")

    parser = argparse.ArgumentParser(
        description="NEXUS - Friend Chat Terminal UI with Black Box Persistence",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--poll-ms",
        type=int,
        default=POLL_INTERVAL_DEFAULT_MS,
        help=f"Poll interval in ms (default: {POLL_INTERVAL_DEFAULT_MS})",
    )
    parser.add_argument(
        "--inbox",
        default="nexus",
        help="Inbox to listen (default: nexus)",
    )
    parser.add_argument(
        "--target",
        default="gpt",
        choices=["gpt", "claude"],
        help="Initial target for messages (default: gpt)",
    )
    parser.add_argument(
        "--bridge-url",
        default="",
        help=f"Bridge URL (default: from env or {BRIDGE_URL_DEFAULT})",
    )
    parser.add_argument(
        "--no-history",
        action="store_true",
        help="Disable Black Box persistence (history not saved to disk)",
    )

    args = parser.parse_args()

    # Load credentials
    token = _load_secret("FRIEND_BRIDGE_TOKEN")
    if not token:
        print("FAIL-CLOSED: FRIEND_BRIDGE_TOKEN not found")
        print(f"Set env var or add to {SECRETS_PATH}")
        return 1

    bridge_url = args.bridge_url or _load_secret("FRIEND_BRIDGE_URL") or BRIDGE_URL_DEFAULT

    # Create client
    bridge = FriendBridgeClient(bridge_url, token)

    # Check health
    console = Console()
    console.print(f"[dim]Connecting to {bridge_url}...[/dim]")

    try:
        if not bridge.health():
            console.print("[red]Bridge health check failed[/red]")
            return 1
    except Exception as e:
        console.print(f"[red]Cannot reach bridge: {e}[/red]")
        return 1

    console.print("[green]Bridge connected[/green]")

    # Create and run app
    app = NexusApp(
        bridge=bridge,
        inbox_name=args.inbox,
        poll_interval_ms=args.poll_ms,
        enable_history=not args.no_history,
    )
    app.target = args.target

    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
