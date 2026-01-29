# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 16:20:00 UTC
# Purpose: Telegram UI handlers for AI-Gateway control panel
# === END SIGNATURE ===
"""
AI-Gateway Telegram Panel: Interactive control panel with inline buttons.

Commands:
    /ai - Show main AI panel with module status
    /ai_health - Run diagnostics
    /ai_enable <module> - Enable module
    /ai_disable <module> - Disable module

Inline buttons:
    ▶️ Start - Start module scheduler
    ⏹ Stop - Stop module scheduler
    🔄 Restart - Restart module
    ⚡ Run Now - Execute once immediately
    ✅ Enable - Enable module
    ❌ Disable - Disable module
    🔙 Back - Return to main panel

Integration:
    Add these handlers to your telegram bot application.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, Set

logger = logging.getLogger(__name__)

# Check if python-telegram-bot is available
try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import (
        ContextTypes,
        CommandHandler,
        CallbackQueryHandler,
    )
    PTB_AVAILABLE = True
except ImportError:
    PTB_AVAILABLE = False
    logger.warning("python-telegram-bot not installed - Telegram panel disabled")


# Module display names (Russian)
MODULE_NAMES = {
    "sentiment": "📰 Sentiment",
    "regime": "📊 Regime",
    "doctor": "🩺 Doctor",
    "anomaly": "🚨 Anomaly",
}


class AIGatewayPanel:
    """
    Telegram panel for AI-Gateway control.

    Usage:
        panel = AIGatewayPanel(allowed_users={123456, 789012})

        # Add to your bot application:
        app.add_handler(CommandHandler("ai", panel.cmd_ai))
        app.add_handler(CommandHandler("ai_health", panel.cmd_health))
        app.add_handler(CallbackQueryHandler(panel.handle_callback, pattern="^ai:"))
    """

    def __init__(
        self,
        allowed_users: Optional[Set[int]] = None,
        gateway_url: str = "http://127.0.0.1:8100",
    ):
        """
        Initialize panel.

        Args:
            allowed_users: Set of Telegram user IDs allowed to use panel.
                           If None, all users allowed (unsafe!).
            gateway_url: URL of AI-Gateway HTTP server.
        """
        self.allowed_users = allowed_users
        self.gateway_url = gateway_url.rstrip("/")

    def _is_allowed(self, update: Update) -> bool:
        """Check if user is allowed."""
        if self.allowed_users is None:
            return True
        user = update.effective_user
        return user is not None and user.id in self.allowed_users

    async def _call_gateway(self, method: str, endpoint: str) -> Optional[dict]:
        """Call AI-Gateway HTTP API."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=30.0) as client:
                if method == "GET":
                    resp = await client.get(f"{self.gateway_url}{endpoint}")
                else:
                    resp = await client.post(f"{self.gateway_url}{endpoint}")
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error(f"Gateway call failed: {method} {endpoint}: {e}")
            return None

    def _build_main_keyboard(self) -> InlineKeyboardMarkup:
        """Build main panel keyboard."""
        buttons = []

        # Module rows
        for module_id in ["sentiment", "regime", "doctor", "anomaly"]:
            name = MODULE_NAMES.get(module_id, module_id)
            buttons.append([
                InlineKeyboardButton(name, callback_data=f"ai:detail:{module_id}"),
            ])

        # Control row
        buttons.append([
            InlineKeyboardButton("🏥 Диагностика", callback_data="ai:diagnostics"),
            InlineKeyboardButton("🔄 Обновить", callback_data="ai:refresh"),
        ])

        return InlineKeyboardMarkup(buttons)

    def _build_module_keyboard(self, module_id: str, is_running: bool, is_enabled: bool) -> InlineKeyboardMarkup:
        """Build module detail keyboard."""
        buttons = []

        # Start/Stop row
        if is_running:
            buttons.append([
                InlineKeyboardButton("⏹ Остановить", callback_data=f"ai:stop:{module_id}"),
                InlineKeyboardButton("🔄 Перезапустить", callback_data=f"ai:restart:{module_id}"),
            ])
        else:
            buttons.append([
                InlineKeyboardButton("▶️ Запустить", callback_data=f"ai:start:{module_id}"),
            ])

        # Run now
        buttons.append([
            InlineKeyboardButton("⚡ Выполнить сейчас", callback_data=f"ai:run_now:{module_id}"),
        ])

        # Enable/Disable
        if is_enabled:
            buttons.append([
                InlineKeyboardButton("❌ Выключить модуль", callback_data=f"ai:disable:{module_id}"),
            ])
        else:
            buttons.append([
                InlineKeyboardButton("✅ Включить модуль", callback_data=f"ai:enable:{module_id}"),
            ])

        # Back
        buttons.append([
            InlineKeyboardButton("🔙 Назад", callback_data="ai:back"),
        ])

        return InlineKeyboardMarkup(buttons)

    async def _get_status_text(self) -> str:
        """Get formatted status text."""
        data = await self._call_gateway("GET", "/status")

        if data is None:
            return (
                "╔══════════════════════════════╗\n"
                "║    🤖 AI-GATEWAY СТАТУС      ║\n"
                "╠══════════════════════════════╣\n"
                "║ ⚠️ Gateway недоступен        ║\n"
                "╚══════════════════════════════╝"
            )

        lines = [
            "╔══════════════════════════════╗",
            "║    🤖 AI-GATEWAY СТАТУС      ║",
            "╠══════════════════════════════╣",
            f"║ Шлюз: {data.get('gateway_emoji', '⚪')} ({data.get('active_modules', 0)}/{data.get('total_modules', 4)} активно)",
            "╟──────────────────────────────╢",
        ]

        for mod in data.get("modules", []):
            emoji = mod.get("emoji", "⚪")
            name = MODULE_NAMES.get(mod["module"], mod["module"])
            enabled = "✓" if mod.get("enabled") else "✗"
            lines.append(f"║ {emoji} {name} [{enabled}]")

        lines.append("╚══════════════════════════════╝")

        return "\n".join(lines)

    async def _get_module_detail_text(self, module_id: str) -> tuple[str, bool, bool]:
        """Get module detail text and state."""
        # Get status
        status_data = await self._call_gateway("GET", f"/status/{module_id}")
        # Get scheduler info
        scheduler_data = await self._call_gateway("GET", "/scheduler/info")

        is_running = False
        is_enabled = False

        if scheduler_data:
            is_running = module_id in scheduler_data.get("running_modules", [])
            mod_info = scheduler_data.get("modules", {}).get(module_id, {})
            is_enabled = mod_info.get("enabled", False)

        if status_data is None:
            return f"❌ Модуль '{module_id}' недоступен", is_running, is_enabled

        emoji = status_data.get("emoji", "⚪")
        name = MODULE_NAMES.get(module_id, module_id)
        status = status_data.get("status", "unknown")

        lines = [
            f"╔══ {emoji} {name} ══╗",
            f"║ Статус: {status}",
            f"║ Включен: {'Да' if is_enabled else 'Нет'}",
            f"║ Запущен: {'Да' if is_running else 'Нет'}",
        ]

        last_run = status_data.get("last_run")
        if last_run:
            lines.append(f"║ Последний запуск: {last_run[:19]}")

        error_count = status_data.get("error_count", 0)
        if error_count > 0:
            lines.append(f"║ Ошибок подряд: {error_count}")

        lines.append("╚" + "═" * 28 + "╝")

        return "\n".join(lines), is_running, is_enabled

    # === Command Handlers ===

    async def cmd_ai(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /ai command - show main panel."""
        if not PTB_AVAILABLE:
            return
        if not self._is_allowed(update):
            return
        if update.effective_message is None:
            return

        text = await self._get_status_text()
        keyboard = self._build_main_keyboard()

        await update.effective_message.reply_text(
            text,
            reply_markup=keyboard,
            parse_mode=None,  # Plain text for box drawing
        )

    async def cmd_health(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /ai_health command - run diagnostics."""
        if not PTB_AVAILABLE:
            return
        if not self._is_allowed(update):
            return
        if update.effective_message is None:
            return

        await update.effective_message.reply_text("🏥 Запускаю диагностику...")

        data = await self._call_gateway("GET", "/diagnostics/telegram")
        if data and "block" in data:
            text = data["block"]
        else:
            text = "❌ Не удалось получить диагностику"

        await update.effective_message.reply_text(text)

    # === Callback Handler ===

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline button callbacks."""
        if not PTB_AVAILABLE:
            return
        if not self._is_allowed(update):
            return

        query = update.callback_query
        if query is None:
            return

        await query.answer()

        data = query.data or ""
        if not data.startswith("ai:"):
            return

        parts = data.split(":")
        action = parts[1] if len(parts) > 1 else ""
        module_id = parts[2] if len(parts) > 2 else ""

        # Handle actions
        if action == "refresh" or action == "back":
            text = await self._get_status_text()
            keyboard = self._build_main_keyboard()
            await query.edit_message_text(text, reply_markup=keyboard)

        elif action == "detail":
            text, is_running, is_enabled = await self._get_module_detail_text(module_id)
            keyboard = self._build_module_keyboard(module_id, is_running, is_enabled)
            await query.edit_message_text(text, reply_markup=keyboard)

        elif action == "diagnostics":
            data = await self._call_gateway("GET", "/diagnostics/telegram")
            if data and "block" in data:
                text = data["block"]
            else:
                text = "❌ Не удалось получить диагностику"
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Назад", callback_data="ai:back"),
                ]]),
            )

        elif action == "start":
            result = await self._call_gateway("POST", f"/modules/{module_id}/start")
            status = "✅ Запущен" if result else "❌ Ошибка запуска"
            await query.answer(status, show_alert=True)
            # Refresh detail view
            text, is_running, is_enabled = await self._get_module_detail_text(module_id)
            keyboard = self._build_module_keyboard(module_id, is_running, is_enabled)
            await query.edit_message_text(text, reply_markup=keyboard)

        elif action == "stop":
            result = await self._call_gateway("POST", f"/modules/{module_id}/stop")
            status = "⏹ Остановлен" if result else "❌ Ошибка остановки"
            await query.answer(status, show_alert=True)
            # Refresh detail view
            text, is_running, is_enabled = await self._get_module_detail_text(module_id)
            keyboard = self._build_module_keyboard(module_id, is_running, is_enabled)
            await query.edit_message_text(text, reply_markup=keyboard)

        elif action == "restart":
            await query.answer("🔄 Перезапуск...", show_alert=False)
            result = await self._call_gateway("POST", f"/modules/{module_id}/restart")
            status = "✅ Перезапущен" if result else "❌ Ошибка"
            await query.answer(status, show_alert=True)
            # Refresh detail view
            text, is_running, is_enabled = await self._get_module_detail_text(module_id)
            keyboard = self._build_module_keyboard(module_id, is_running, is_enabled)
            await query.edit_message_text(text, reply_markup=keyboard)

        elif action == "run_now":
            await query.answer("⚡ Выполняю...", show_alert=False)
            result = await self._call_gateway("POST", f"/modules/{module_id}/run-now")
            if result and result.get("artifact_produced"):
                status = "✅ Выполнено, артефакт создан"
            elif result:
                status = "✅ Выполнено"
            else:
                status = "❌ Ошибка выполнения"
            await query.answer(status, show_alert=True)

        elif action == "enable":
            result = await self._call_gateway("POST", f"/modules/{module_id}/enable")
            status = "✅ Модуль включен" if result else "❌ Ошибка"
            await query.answer(status, show_alert=True)
            # Refresh detail view
            text, is_running, is_enabled = await self._get_module_detail_text(module_id)
            keyboard = self._build_module_keyboard(module_id, is_running, is_enabled)
            await query.edit_message_text(text, reply_markup=keyboard)

        elif action == "disable":
            result = await self._call_gateway("POST", f"/modules/{module_id}/disable")
            status = "❌ Модуль выключен" if result else "❌ Ошибка"
            await query.answer(status, show_alert=True)
            # Refresh detail view
            text, is_running, is_enabled = await self._get_module_detail_text(module_id)
            keyboard = self._build_module_keyboard(module_id, is_running, is_enabled)
            await query.edit_message_text(text, reply_markup=keyboard)


def register_ai_panel(
    app,
    allowed_users: Optional[Set[int]] = None,
    gateway_url: str = "http://127.0.0.1:8100",
) -> AIGatewayPanel:
    """
    Register AI panel handlers with a telegram bot application.

    Args:
        app: telegram.ext.Application instance
        allowed_users: Set of allowed user IDs
        gateway_url: AI-Gateway HTTP server URL

    Returns:
        AIGatewayPanel instance

    Usage:
        from ai_gateway.telegram_panel import register_ai_panel

        app = ApplicationBuilder().token(token).build()
        panel = register_ai_panel(app, allowed_users={123456})
    """
    if not PTB_AVAILABLE:
        raise RuntimeError("python-telegram-bot not installed")

    panel = AIGatewayPanel(allowed_users=allowed_users, gateway_url=gateway_url)

    app.add_handler(CommandHandler("ai", panel.cmd_ai))
    app.add_handler(CommandHandler("ai_health", panel.cmd_health))
    app.add_handler(CallbackQueryHandler(panel.handle_callback, pattern="^ai:"))

    logger.info("AI-Gateway Telegram panel registered")
    return panel
