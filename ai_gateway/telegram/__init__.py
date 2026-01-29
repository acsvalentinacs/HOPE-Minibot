# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 19:10:00 UTC
# Purpose: Telegram commands package for AI Gateway
# === END SIGNATURE ===
"""
AI Gateway Telegram Commands.

Commands:
- /predict SYMBOL: Manual prediction check
- /stats: Performance statistics
- /history: Recent trades
- /mode: Mode distribution
"""

from .commands import (
    setup_handlers,
    cmd_predict,
    cmd_stats,
    cmd_history,
    format_prediction_message,
)

__all__ = [
    "setup_handlers",
    "cmd_predict",
    "cmd_stats",
    "cmd_history",
    "format_prediction_message",
]
