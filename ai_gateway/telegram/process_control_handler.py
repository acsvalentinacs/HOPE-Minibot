# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 12:25:00 UTC
# Purpose: Telegram commands for process management
# Contract: Admin-only commands for controlling HOPE processes
# === END SIGNATURE ===
"""
Telegram Process Control Commands.

Commands:
    /processes              - List all processes and their status
    /start_process <name>   - Start a specific process
    /stop_process <name>    - Stop a specific process
    /restart_process <name> - Restart a specific process
    /allowlist              - Show AllowList status
    /logs <name> [n]        - Show last n lines of process logs

All commands are admin-only.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# Admin check decorator
def admin_only(func):
    """Decorator to restrict commands to admin users."""
    async def wrapper(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
        import os
        admin_id = os.getenv("HOPE_ADMIN_ID") or os.getenv("TG_ADMIN_ID")

        if admin_id:
            user_id = str(update.effective_user.id)
            if user_id != admin_id:
                await update.message.reply_text("Access denied. Admin only.")
                return

        return await func(update, context)
    return wrapper


# === Process Management Commands ===

@admin_only
async def cmd_processes(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    """
    /processes - List all processes and their status.

    Shows:
    - Process name
    - Status (running/stopped/failed)
    - Uptime
    - PID
    """
    try:
        from scripts.hope_process_manager import ProcessManager
        manager = ProcessManager()
        status = manager.get_status()

        lines = ["<b>HOPE PROCESSES</b>", ""]

        summary = status.get("summary", {})
        lines.append(
            f"Running: {summary.get('running', 0)}/{summary.get('total', 0)} | "
            f"Stopped: {summary.get('stopped', 0)} | "
            f"Failed: {summary.get('failed', 0)}"
        )
        lines.append("")

        for name, proc in sorted(status.get("processes", {}).items()):
            # Status indicator
            if proc.get("running"):
                indicator = "[OK]"
            elif proc.get("status") == "failed":
                indicator = "[!!]"
            else:
                indicator = "[--]"

            uptime = proc.get("uptime") or "--:--:--"
            pid = f"PID {proc.get('pid')}" if proc.get("pid") else "N/A"
            port = f":{proc.get('port')}" if proc.get("port") else ""

            lines.append(f"{indicator} <b>{proc.get('display_name', name)}</b>")
            lines.append(f"    {uptime} | {pid}{port}")

        lines.append("")
        lines.append("<i>Commands: /start_process, /stop_process, /restart_process</i>")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    except Exception as e:
        logger.error(f"Processes error: {e}")
        await update.message.reply_text(f"Error: {e}")


@admin_only
async def cmd_start_process(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    """
    /start_process <name> - Start a specific process.

    Args:
        name: Process name from registry (e.g., friend_bridge, dashboard)
    """
    if not context.args:
        await update.message.reply_text(
            "Usage: /start_process <name>\n\n"
            "Examples:\n"
            "  /start_process friend_bridge\n"
            "  /start_process dashboard\n"
            "  /start_process eye_of_god"
        )
        return

    name = context.args[0].lower()

    try:
        from scripts.hope_process_manager import ProcessManager
        manager = ProcessManager()

        await update.message.reply_text(f"Starting {name}...")

        success, msg = manager.start_process(name)

        if success:
            await update.message.reply_text(f"[OK] {name} started (PID {msg})")
        else:
            await update.message.reply_text(f"[FAIL] Failed to start {name}: {msg}")

    except Exception as e:
        logger.error(f"Start process error: {e}")
        await update.message.reply_text(f"Error: {e}")


@admin_only
async def cmd_stop_process(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    """
    /stop_process <name> - Stop a specific process.

    Args:
        name: Process name from registry
    """
    if not context.args:
        await update.message.reply_text(
            "Usage: /stop_process <name>\n\n"
            "Examples:\n"
            "  /stop_process friend_bridge\n"
            "  /stop_process dashboard"
        )
        return

    name = context.args[0].lower()

    try:
        from scripts.hope_process_manager import ProcessManager
        manager = ProcessManager()

        await update.message.reply_text(f"Stopping {name}...")

        success, msg = manager.stop_process(name)

        if success:
            await update.message.reply_text(f"[OK] {name} stopped")
        else:
            await update.message.reply_text(f"[FAIL] Failed to stop {name}: {msg}")

    except Exception as e:
        logger.error(f"Stop process error: {e}")
        await update.message.reply_text(f"Error: {e}")


@admin_only
async def cmd_restart_process(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    """
    /restart_process <name> - Restart a specific process.

    Args:
        name: Process name from registry
    """
    if not context.args:
        await update.message.reply_text(
            "Usage: /restart_process <name>\n\n"
            "Examples:\n"
            "  /restart_process friend_bridge\n"
            "  /restart_process dashboard"
        )
        return

    name = context.args[0].lower()

    try:
        from scripts.hope_process_manager import ProcessManager
        manager = ProcessManager()

        await update.message.reply_text(f"Restarting {name}...")

        success, msg = manager.restart_process(name)

        if success:
            await update.message.reply_text(f"[OK] {name} restarted (PID {msg})")
        else:
            await update.message.reply_text(f"[FAIL] Failed to restart {name}: {msg}")

    except Exception as e:
        logger.error(f"Restart process error: {e}")
        await update.message.reply_text(f"Error: {e}")


@admin_only
async def cmd_allowlist(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    """
    /allowlist - Show AllowList status (3-layer).

    Shows:
    - CORE_LIST: Permanent symbols
    - DYNAMIC_LIST: Volume-based
    - HOT_LIST: Pump detection
    """
    try:
        from core.unified_allowlist import get_unified_allowlist
        al = get_unified_allowlist()

        lines = ["<b>ALLOWLIST STATUS (3-Layer)</b>", ""]

        # Core
        core = list(al.core_list.keys())
        lines.append(f"<b>CORE ({len(core)}):</b> Permanent")
        lines.append(f"  {', '.join(core[:10])}")
        if len(core) > 10:
            lines.append(f"  ...and {len(core) - 10} more")
        lines.append("")

        # Dynamic
        dynamic = list(al.dynamic_list.keys())
        lines.append(f"<b>DYNAMIC ({len(dynamic)}):</b> By Volume")
        lines.append(f"  {', '.join(dynamic[:10])}")
        if len(dynamic) > 10:
            lines.append(f"  ...and {len(dynamic) - 10} more")
        lines.append("")

        # Hot
        hot = list(al.hot_list.keys())
        lines.append(f"<b>HOT ({len(hot)}):</b> Real-time Pumps")
        if hot:
            lines.append(f"  {', '.join(hot)}")
        else:
            lines.append("  <i>No hot symbols</i>")
        lines.append("")

        # Total
        total = al.get_symbols_set()
        lines.append(f"<b>Total Unique:</b> {len(total)} symbols")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    except Exception as e:
        logger.error(f"AllowList error: {e}")
        await update.message.reply_text(f"Error: {e}")


@admin_only
async def cmd_logs(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    """
    /logs <name> [n] - Show last n lines of process logs.

    Args:
        name: Process name
        n: Number of lines (default 20, max 100)
    """
    if not context.args:
        await update.message.reply_text(
            "Usage: /logs <name> [lines]\n\n"
            "Examples:\n"
            "  /logs friend_bridge\n"
            "  /logs dashboard 50"
        )
        return

    name = context.args[0].lower()
    lines_count = 20

    if len(context.args) > 1:
        try:
            lines_count = min(int(context.args[1]), 100)
        except ValueError:
            pass

    try:
        ROOT = Path(__file__).parent.parent.parent
        log_file = ROOT / "logs" / f"{name}_stdout.log"

        if not log_file.exists():
            await update.message.reply_text(f"No logs found for {name}")
            return

        # Read last N lines
        all_lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        last_lines = all_lines[-lines_count:]

        if not last_lines:
            await update.message.reply_text(f"Log file for {name} is empty")
            return

        # Format output (truncate long lines)
        output_lines = [f"<b>Logs: {name}</b> (last {len(last_lines)} lines)", ""]
        output_lines.append("<pre>")

        for line in last_lines:
            # Truncate long lines
            if len(line) > 80:
                line = line[:77] + "..."
            # Escape HTML
            line = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            output_lines.append(line)

        output_lines.append("</pre>")

        # Telegram has 4096 char limit
        message = "\n".join(output_lines)
        if len(message) > 4000:
            message = message[:3997] + "..."

        await update.message.reply_text(message, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Logs error: {e}")
        await update.message.reply_text(f"Error: {e}")


# === Setup ===

def setup_process_handlers(application) -> None:
    """
    Setup Telegram process control handlers.

    Args:
        application: python-telegram-bot Application instance
    """
    try:
        from telegram.ext import CommandHandler

        application.add_handler(CommandHandler("processes", cmd_processes))
        application.add_handler(CommandHandler("start_process", cmd_start_process))
        application.add_handler(CommandHandler("stop_process", cmd_stop_process))
        application.add_handler(CommandHandler("restart_process", cmd_restart_process))
        application.add_handler(CommandHandler("allowlist", cmd_allowlist))
        application.add_handler(CommandHandler("logs", cmd_logs))

        logger.info("Process control handlers registered")

    except ImportError:
        logger.warning("python-telegram-bot not installed, handlers not registered")
