# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 13:35:00 UTC
# Purpose: Alerts module for HOPE AI
# === END SIGNATURE ===
"""
HOPE AI Alerts Module.

Provides Telegram alerts for trading signals.
"""

from .telegram_alerts import (
    AlertManager,
    AlertConfig,
    Alert,
    TelegramClient,
    get_alert_manager,
)

__all__ = [
    "AlertManager",
    "AlertConfig",
    "Alert",
    "TelegramClient",
    "get_alert_manager",
]
