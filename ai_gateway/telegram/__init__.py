# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 19:10:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-30 12:30:00 UTC
# Purpose: Telegram commands package for AI Gateway
# === END SIGNATURE ===
"""
AI Gateway Telegram Commands.

Commands:
- /predict SYMBOL: Manual prediction check
- /stats: Performance statistics
- /history: Recent trades
- /mode: Mode distribution

Process Control (admin only):
- /processes: List all processes
- /start_process: Start a process
- /stop_process: Stop a process
- /restart_process: Restart a process
- /allowlist: Show AllowList status
- /logs: View process logs
"""

from .commands import (
    setup_handlers,
    cmd_predict,
    cmd_stats,
    cmd_history,
    format_prediction_message,
)

from .process_control_handler import (
    setup_process_handlers,
    cmd_processes,
    cmd_start_process,
    cmd_stop_process,
    cmd_restart_process,
    cmd_allowlist,
    cmd_logs,
)

__all__ = [
    # AI commands
    "setup_handlers",
    "cmd_predict",
    "cmd_stats",
    "cmd_history",
    "format_prediction_message",
    # Process control commands
    "setup_process_handlers",
    "cmd_processes",
    "cmd_start_process",
    "cmd_stop_process",
    "cmd_restart_process",
    "cmd_allowlist",
    "cmd_logs",
]
