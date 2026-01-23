# === AI SIGNATURE ===
# Created by: Claude
# Created at: 2026-01-21 15:00:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 17:05:00 UTC
# Change: Added HOPE bootstrap (LAW-001), moved logging to main()
# === END SIGNATURE ===
"""
Telegram Gateway → IPC v1.1 (python-telegram-bot v20.x)

Bridges Telegram messages to GPT→Claude IPC pipeline.
Does NOT modify tg_bot_simple.py - runs as separate gateway.

v1.1 changes:
- Fixed reply_to correlation (cache-based matching like CLI v1.1)
- Drain inbox to cache to avoid stale responses
- Match by reply_to -> pending task lookup

Flow:
    Telegram → gpt_inbox → orchestrator → claude_agent_inbox → executor
                                       ↓
    Telegram ← claude_inbox ← orchestrator ← gpt_inbox ← executor

Usage:
    # Set environment variables:
    #   TELEGRAM_BOT_TOKEN - bot token from @BotFather
    #   ALLOWED_USERS - comma-separated user IDs (e.g., "123456,789012")

    python -m tools.telegram_gateway_ipc_ptb

Commands:
    /ask <text> - send task to pipeline
    /status - show IPC queue status
    /help - show help

Any plain text is treated as /ask.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Set

# Add parent to path for imports
_MINIBOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_MINIBOT_DIR))

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.ipc_fs import get_ipc, IPCFileSystem, VALID_INBOXES
from core.ipc_compat import make_msg

# Logger initialized in main() after bootstrap
log: logging.Logger | None = None


def _setup_logging() -> logging.Logger:
    """Setup logging after bootstrap."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [TG-GW] %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger(__name__)


class FailClosed(RuntimeError):
    """Fail-closed error - gateway cannot start or continue."""
    pass


def _must_env(name: str) -> str:
    """Get required environment variable (fail-closed)."""
    v = os.getenv(name, "").strip()
    if not v:
        raise FailClosed(f"Missing env {name} (fail-closed)")
    return v


def _get_allowed_users() -> Set[int]:
    """Parse ALLOWED_USERS env variable (fail-closed if empty)."""
    raw = _must_env("ALLOWED_USERS")
    out: Set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            log.warning(f"Invalid user ID in ALLOWED_USERS: {part}")
    if not out:
        raise FailClosed("ALLOWED_USERS resolved to empty set (fail-closed)")
    return out


@dataclass(frozen=True)
class Queues:
    """IPC queue names (must match gpt_orchestrator_runner.py SSoT)."""
    # Gateway writes user tasks here (orchestrator reads)
    to_orchestrator: str = "gpt_inbox"
    # Gateway reads final responses from here (orchestrator writes)
    from_orchestrator: str = "claude_inbox"


class TelegramIPCGateway:
    """
    Telegram <-> IPC Gateway v1.1.

    - Receives Telegram messages
    - Creates IPC tasks in gpt_inbox
    - Polls claude_inbox for responses (cache-based correlation)
    - Sends responses back to Telegram
    """

    def __init__(self, ipc: IPCFileSystem, queues: Queues, allowed_users: Set[int]) -> None:
        self.ipc = ipc
        self.queues = queues
        self.allowed_users = allowed_users
        # Track pending requests: task_id -> (chat_id, message_id)
        self._pending: Dict[str, tuple[int, int]] = {}
        # Response cache: reply_to -> message (for correlation)
        self._response_cache: Dict[str, Dict[str, Any]] = {}

    def _is_allowed(self, update: Update) -> bool:
        """Check if user is allowed to use the bot."""
        u = update.effective_user
        if u is None:
            return False
        return int(u.id) in self.allowed_users

    def _get_telegram_context(self, update: Update) -> Dict[str, Any]:
        """Extract Telegram context from update (fail-closed)."""
        if update.effective_chat is None or update.effective_message is None:
            raise FailClosed("Missing chat/message in update (fail-closed)")
        u = update.effective_user
        return {
            "chat_id": int(update.effective_chat.id),
            "message_id": int(update.effective_message.message_id),
            "user_id": int(u.id) if u else None,
            "username": str(u.username) if u and u.username else None,
            "context": "telegram",
        }

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command."""
        if not self._is_allowed(update):
            return
        if update.effective_message is None:
            return

        help_text = (
            "HOPE Telegram Gateway\n\n"
            "/ask <text> - отправить задачу в пайплайн\n"
            "/status - статус IPC очередей\n"
            "/help - эта справка\n\n"
            "Любой текст без команды = /ask"
        )
        await update.effective_message.reply_text(help_text)

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status command."""
        if not self._is_allowed(update):
            return
        if update.effective_message is None:
            return

        lines = ["IPC Queue Status:"]
        for q in ["gpt_inbox", "claude_agent_inbox", "claude_inbox", "deadletter"]:
            count = self.ipc.count_messages(q)
            lines.append(f"  {q}: {count}")
        lines.append(f"\nPending requests: {len(self._pending)}")
        lines.append(f"Response cache: {len(self._response_cache)}")

        await update.effective_message.reply_text("\n".join(lines))

    async def cmd_ask(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /ask command - send task to pipeline."""
        if not self._is_allowed(update):
            return
        if update.effective_message is None:
            return

        text = (update.effective_message.text or "").strip()

        # Extract message after /ask
        if text.startswith("/ask"):
            payload_text = text[len("/ask"):].strip()
        else:
            payload_text = text

        if not payload_text:
            await update.effective_message.reply_text("Usage: /ask <text>")
            return

        try:
            tc = self._get_telegram_context(update)
        except FailClosed as e:
            log.error(f"Failed to get telegram context: {e}")
            return

        # Create IPC message matching orchestrator's expected format
        # orchestrator expects: from="user", to="gpt", type="task"
        # payload: task_type, message, context, telegram_chat_id, telegram_message_id
        msg = make_msg(
            from_="user",
            to="gpt",
            type_="task",
            payload={
                "task_type": "chat",
                "message": payload_text,
                "context": "telegram",
                "telegram_chat_id": tc["chat_id"],
                "telegram_message_id": tc["message_id"],
                "telegram_user_id": tc["user_id"],
            },
        )

        task_id = msg["id"]

        try:
            self.ipc.write_message(self.queues.to_orchestrator, msg)
            log.info(f"Task sent: {task_id[:30]}... -> {self.queues.to_orchestrator}")
        except Exception as e:
            log.error(f"Failed to write IPC message: {e}")
            await update.effective_message.reply_text(f"[error] IPC write failed: {e}")
            return

        # Track pending request
        self._pending[task_id] = (tc["chat_id"], tc["message_id"])

        await update.effective_message.reply_text("Принято. Обрабатываю...")

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle plain text messages (treat as /ask)."""
        if not self._is_allowed(update):
            return
        if update.effective_message is None or not update.effective_message.text:
            return
        await self.cmd_ask(update, context)

    def _drain_inbox_to_cache(self, limit: int = 50) -> None:
        """
        Read all messages from claude_inbox and store in cache by reply_to.

        ACK (delete) immediately to avoid stale message buildup.
        """
        batch = self.ipc.read_inbox(self.queues.from_orchestrator, limit=limit)

        for msg in batch:
            msg_id = msg.get("id", "")
            payload = msg.get("payload", {})

            if isinstance(payload, dict):
                reply_to = payload.get("reply_to", "")
                if reply_to:
                    self._response_cache[reply_to] = msg

            # Always delete to avoid stale messages
            try:
                self.ipc.delete_message(self.queues.from_orchestrator, msg_id)
            except Exception:
                pass

    def _extract_response_text(self, payload: Dict[str, Any]) -> str:
        """Extract text from response payload (multiple formats supported)."""
        ok = bool(payload.get("ok", False))

        if ok:
            # Try multiple fields for success response
            for key in ("message", "text", "answer", "result", "result_text"):
                val = payload.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
                if isinstance(val, (int, float)):
                    return str(val)
            return ""
        else:
            # Error response
            for key in ("error", "message", "text"):
                val = payload.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
            return "Unknown error"

    async def poll_responses_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Poll claude_inbox for responses and send to Telegram.

        Uses cache-based correlation by reply_to field.
        This is called by job_queue periodically.
        """
        try:
            # Drain inbox to cache
            self._drain_inbox_to_cache(limit=50)

            # Process matched responses for pending Telegram requests
            matched_task_ids = []

            for task_id, (chat_id, message_id) in self._pending.items():
                if task_id not in self._response_cache:
                    continue

                # Found matching response
                resp_msg = self._response_cache.pop(task_id)
                matched_task_ids.append(task_id)

                payload = resp_msg.get("payload", {})
                if not isinstance(payload, dict):
                    log.warning(f"Invalid payload for task {task_id[:20]}...")
                    continue

                # Extract response text
                ok = bool(payload.get("ok", False))
                text = self._extract_response_text(payload)

                if ok:
                    final_text = text if text else "[empty response]"
                else:
                    final_text = f"[error] {text}"

                # Send to Telegram
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=final_text[:4096],  # Telegram limit
                        reply_to_message_id=message_id,
                    )
                    log.info(f"Response sent to chat={chat_id} for task={task_id[:20]}...")
                except Exception as e:
                    log.error(f"Failed to send to Telegram: {e}")

            # Remove matched tasks from pending
            for task_id in matched_task_ids:
                del self._pending[task_id]

        except Exception as e:
            log.error(f"Poll error: {e}")


def main() -> int:
    """CLI entry point."""
    # HOPE-LAW-001: Policy bootstrap MUST be first
    from core.policy.bootstrap import bootstrap
    bootstrap("telegram_gateway", network_profile="core")

    global log
    log = _setup_logging()
    log.info("Telegram Gateway starting...")

    # Load config (fail-closed)
    try:
        token = _must_env("TELEGRAM_BOT_TOKEN")
        allowed_users = _get_allowed_users()
    except FailClosed as e:
        log.error(f"Startup failed: {e}")
        return 1

    log.info(f"Allowed users: {allowed_users}")

    # Initialize IPC
    ipc = get_ipc()
    queues = Queues()

    # Verify queues exist
    for q in [queues.to_orchestrator, queues.from_orchestrator]:
        if q not in VALID_INBOXES:
            log.error(f"Queue {q} not in VALID_INBOXES (fail-closed)")
            return 1

    log.info(f"Queues: write={queues.to_orchestrator}, read={queues.from_orchestrator}")

    # Create gateway
    gw = TelegramIPCGateway(ipc=ipc, queues=queues, allowed_users=allowed_users)

    # Build application
    app: Application = ApplicationBuilder().token(token).build()

    # Add handlers
    app.add_handler(CommandHandler("help", gw.cmd_help))
    app.add_handler(CommandHandler("start", gw.cmd_help))
    app.add_handler(CommandHandler("ask", gw.cmd_ask))
    app.add_handler(CommandHandler("status", gw.cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, gw.handle_text))

    # Poll IPC responses every 0.8s
    if app.job_queue is not None:
        app.job_queue.run_repeating(gw.poll_responses_job, interval=0.8, first=1.0)
    else:
        log.warning("job_queue is None - response polling disabled")

    log.info("Starting Telegram polling...")
    app.run_polling(close_loop=False)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
