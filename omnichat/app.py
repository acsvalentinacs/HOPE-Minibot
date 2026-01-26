#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-26T11:00:00Z
# Purpose: HOPE OMNI-CHAT v1.0 - Trinity AI Chat TUI Application
# === END SIGNATURE ===
"""
HOPE OMNI-CHAT v1.0 - Trinity AI Chat System

A professional TUI (Text User Interface) for real-time chat with
multiple AI agents: Gemini (Strategist), GPT (Analyst), Claude (Developer).

Hotkeys:
    F1 - Send to Gemini only
    F2 - Send to GPT only
    F3 - Send to Claude only
    F5 - Send to ALL agents
    Ctrl+E - Export chat to Markdown
    Ctrl+Q - Quit
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

# SECURITY: Configure safe logging BEFORE any other imports
from src.security import configure_safe_logging, contains_secret, redact
configure_safe_logging(log_file=str(Path(__file__).parent / "omnichat.log"))

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    Static,
)

from src.bus import EventBus, ChatMessage, MessageRole, SessionStats


class MessageWidget(Static):
    """Widget for displaying a single chat message."""

    def __init__(self, message: ChatMessage) -> None:
        self.message = message
        super().__init__()

    def compose(self) -> ComposeResult:
        role_config = {
            MessageRole.USER: ("👤 You", "message-user"),
            MessageRole.GEMINI: ("💜 Gemini", "message-gemini"),
            MessageRole.GPT: ("💛 GPT", "message-gpt"),
            MessageRole.CLAUDE: ("💙 Claude", "message-claude"),
            MessageRole.SYSTEM: ("⚙️ System", "message-system"),
        }

        name, css_class = role_config.get(
            self.message.role,
            ("❓ Unknown", "message-system")
        )

        time_str = self.message.timestamp.strftime("%H:%M:%S")

        self.add_class("message")
        self.add_class(css_class)

        yield Static(f"{name} [{time_str}]", classes="message-header")
        yield Static(self.message.content)


class CostDisplay(Static):
    """Widget showing session cost in real-time."""

    def __init__(self) -> None:
        super().__init__("💰 $0.0000")
        self.id = "cost-display"

    def update_cost(self, stats: SessionStats) -> None:
        cost_usd = stats.total_cost_cents / 100
        self.update(f"💰 ${cost_usd:.4f}")


class AgentStatusWidget(Static):
    """Widget showing agent connection status."""

    def __init__(self, agent_key: str, agent_info: dict) -> None:
        self.agent_key = agent_key
        self.agent_info = agent_info
        super().__init__()

    def compose(self) -> ComposeResult:
        name = self.agent_info["name"]
        connected = self.agent_info["connected"]

        icon = "🟢" if connected else "🔴"
        status = "OK" if connected else "OFF"

        self.add_class("agent-status")
        self.add_class("agent-connected" if connected else "agent-disconnected")

        yield Static(f"{icon} {name}: {status}")


class HopeOmniChat(App):
    """HOPE OMNI-CHAT - Trinity AI Chat Application."""

    CSS_PATH = "src/styles.tcss"
    TITLE = "HOPE OMNI-CHAT v1.0"

    BINDINGS = [
        Binding("f1", "send_gemini", "Gemini", show=True),
        Binding("f2", "send_gpt", "GPT", show=True),
        Binding("f3", "send_claude", "Claude", show=True),
        Binding("f5", "send_all", "Send All", show=True),
        Binding("ctrl+e", "export", "Export"),
        Binding("ctrl+q", "quit", "Quit"),
        Binding("escape", "clear_input", "Clear", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        history_path = Path(__file__).parent / "chat_history.jsonl"
        self.bus = EventBus(history_path=history_path)
        self.bus.set_callbacks(
            on_message=self._handle_chat_message,
            on_typing=self._handle_typing,
            on_stats_update=self._handle_stats_update,
        )
        self._typing_agents: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Header()

        with Container(id="main-container"):
            # Cost display in top-right
            yield CostDisplay()

            # Chat log
            with VerticalScroll(id="chat-log"):
                yield Static(
                    "🚀 Добро пожаловать в HOPE OMNI-CHAT!\n\n"
                    "Используйте F1/F2/F3 для отправки конкретному агенту,\n"
                    "или F5 / кнопку 'Send All' для отправки всем.\n\n"
                    "Агенты: 💜 Gemini (стратег) | 💛 GPT (аналитик) | 💙 Claude (разработчик)",
                    classes="message message-system",
                    id="welcome-message"
                )

            # Typing indicator
            yield Static("", id="typing-indicator", classes="typing-indicator")

            # Status bar with agent statuses
            with Horizontal(id="status-bar"):
                for key, info in self.bus.get_agent_status().items():
                    yield AgentStatusWidget(key, info)

            # Input area
            with Horizontal(id="input-area"):
                yield Input(
                    placeholder="Введите сообщение...",
                    id="message-input",
                    max_length=5000,
                )
                yield Button("Send All", id="send-all", classes="send-button")

        yield Footer()

    def on_mount(self) -> None:
        """Focus input on mount."""
        self.query_one("#message-input", Input).focus()

    def _handle_chat_message(self, message: ChatMessage) -> None:
        """Handle new message from bus. Safe to call anytime."""
        try:
            chat_log = self.query_one("#chat-log", VerticalScroll)
            chat_log.mount(MessageWidget(message))
            chat_log.scroll_end(animate=False)
        except Exception:
            pass  # Ignore if UI not ready

    def _handle_typing(self, agent_name: str, is_typing: bool) -> None:
        """Handle typing indicator."""
        try:
            if is_typing:
                self._typing_agents.add(agent_name)
            else:
                self._typing_agents.discard(agent_name)

            indicator = self.query_one("#typing-indicator", Static)
            if self._typing_agents:
                names = ", ".join(sorted(self._typing_agents))
                indicator.update(f"⌛ {names} печатает...")
            else:
                indicator.update("")
        except Exception:
            pass  # Ignore if UI not ready

    def _handle_stats_update(self, stats: SessionStats) -> None:
        """Handle stats update."""
        try:
            cost_display = self.query_one(CostDisplay)
            cost_display.update_cost(stats)
        except Exception:
            pass  # Ignore if UI not ready

    async def _send_message(self, target: str | None = None) -> None:
        """Send message from input. SECURITY: Fail-closed on secrets."""
        input_widget = self.query_one("#message-input", Input)
        text = input_widget.value.strip()

        if not text:
            return

        # SECURITY: FAIL-CLOSED - Block messages containing secrets
        if contains_secret(text):
            # Show warning but DO NOT send to API
            self._handle_chat_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content=(
                    "🚫 BLOCKED: Сообщение содержит секрет (API key/token)!\n"
                    f"Обнаружено: {redact(text)}\n"
                    "Сообщение НЕ отправлено агентам. Удалите секрет и попробуйте снова."
                ),
            ))
            # Clear input but keep focus
            input_widget.value = ""
            input_widget.focus()
            return

        input_widget.value = ""
        input_widget.focus()

        # Run async without blocking UI
        asyncio.create_task(self.bus.send_user_message(text, target))

    # === ACTIONS ===

    async def action_send_gemini(self) -> None:
        """Send to Gemini only."""
        await self._send_message("gemini")

    async def action_send_gpt(self) -> None:
        """Send to GPT only."""
        await self._send_message("gpt")

    async def action_send_claude(self) -> None:
        """Send to Claude only."""
        await self._send_message("claude")

    async def action_send_all(self) -> None:
        """Send to all agents."""
        await self._send_message(None)

    def action_clear_input(self) -> None:
        """Clear input field."""
        self.query_one("#message-input", Input).value = ""

    def action_export(self) -> None:
        """Export chat to Markdown."""
        export_path = Path(__file__).parent / f"chat_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        self.bus.export_to_markdown(export_path)
        self._handle_chat_message(ChatMessage(
            role=MessageRole.SYSTEM,
            content=f"📁 Чат экспортирован: {export_path.name}",
        ))

    # === EVENT HANDLERS ===

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button clicks."""
        if event.button.id == "send-all":
            asyncio.create_task(self._send_message(None))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter in input field."""
        if event.input.id == "message-input":
            asyncio.create_task(self._send_message(None))


def main() -> None:
    """Entry point with crash protection."""
    try:
        app = HopeOmniChat()
        app.run()
    except KeyboardInterrupt:
        print("\n👋 OMNI-CHAT закрыт пользователем (Ctrl+C)")
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        print("Нажмите Enter для выхода...")
        input()


if __name__ == "__main__":
    main()
