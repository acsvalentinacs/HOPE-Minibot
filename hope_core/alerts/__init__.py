# === AI SIGNATURE ===
# Module: hope_core/alerts/__init__.py
# Created by: Claude (opus-4.5)
# === END SIGNATURE ===
"""HOPE Core Alerts Module."""

from .telegram import (
    TelegramAlertManager,
    TelegramConfig,
    AlertLevel,
    get_alert_manager,
    send_alert,
    send_trade_alert,
)

__all__ = [
    "TelegramAlertManager",
    "TelegramConfig", 
    "AlertLevel",
    "get_alert_manager",
    "send_alert",
    "send_trade_alert",
]
