#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-26T11:00:00Z
# Modified by: Claude (opus-4)
# Modified at: 2026-01-27T15:00:00Z
# Purpose: HOPE OMNI-CHAT v1.4.1 - Trinity AI Chat TUI with Search + DDO
# FIX: DDO uses run_worker instead of asyncio.create_task
# === END SIGNATURE ===
"""
HOPE OMNI-CHAT v1.4 - Trinity AI Chat System

A professional TUI (Text User Interface) for real-time chat with
multiple AI agents: Gemini (Strategist), GPT (Analyst), Claude (Developer).

NEW in v1.4: DDO - Dynamic Discussion Orchestrator
- Ctrl+D - Launch DDO (multi-agent discussion)
- Automated phases: ARCHITECT → ANALYZE → IMPLEMENT → REVIEW
- Fail-closed guards at every step
- Real-time progress and cost tracking

NEW in v1.3: Full-text Search
- Ctrl+F - Open search panel
- Filters: by agent, date range, keywords
- Keyboard navigation (↑↓ arrows)
- Match highlighting
- Jump to message

Hotkeys:
    F1 - Send to Gemini only
    F2 - Send to GPT only
    F3 - Send to Claude only
    F5 - Send to ALL agents
    F6 - Copy last Gemini response
    F7 - Copy last GPT response
    F8 - Copy last Claude response
    Ctrl+D - DDO Discussion (NEW!)
    Ctrl+F - Search history
    Ctrl+H - Load history on startup
    Ctrl+L - Load message from file
    Ctrl+E - Export chat to Markdown
    Ctrl+Q - Quit
"""

from __future__ import annotations

import asyncio
from datetime import datetime, date
from pathlib import Path
from typing import Optional

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
    TextArea,
    Checkbox,
    ListView,
    ListItem,
)
from textual.screen import ModalScreen
from textual.message import Message

from src.bus import EventBus, ChatMessage, MessageRole, SessionStats, TaskStatus, AgentTaskState
from src.search import SearchEngine, SearchQuery, SearchResult, SearchResults, parse_date_input
from src.connectors import create_all_agents
from src.ddo import (
    DDOOrchestrator,
    DiscussionMode,
    DiscussionPhase,
)
from src.ddo.orchestrator import (
    DDOEvent,
    PhaseStartEvent,
    ResponseEvent,
    GuardFailEvent,
    ProgressEvent,
    CompletedEvent,
    ErrorEvent,
)


# === CLIPBOARD HELPER ===
def copy_to_clipboard(text: str) -> bool:
    """Copy text to system clipboard. Returns True on success."""
    try:
        import subprocess
        process = subprocess.Popen(
            ['clip.exe'],
            stdin=subprocess.PIPE,
            shell=True
        )
        process.communicate(text.encode('utf-16-le'))
        return True
    except Exception:
        try:
            import pyperclip
            pyperclip.copy(text)
            return True
        except Exception:
            return False


# === SEARCH RESULT WIDGET ===
class SearchResultWidget(Static):
    """Widget displaying a single search result."""

    class Selected(Message):
        """Message sent when result is selected."""
        def __init__(self, result: SearchResult) -> None:
            self.result = result
            super().__init__()

    def __init__(self, result: SearchResult, index: int) -> None:
        self.result = result
        self.index = index
        super().__init__()

    def compose(self) -> ComposeResult:
        self.add_class("search-result")

        # Header: role + time
        header = f"{self.result.role_display} [{self.result.time_str}]"
        yield Static(header, classes="search-result-header")

        # Content preview (truncated)
        preview = self.result.get_highlighted_content(max_length=150)
        yield Static(preview, classes="search-result-content")

    def on_click(self) -> None:
        """Handle click on result."""
        self.post_message(self.Selected(self.result))


# === SEARCH SCREEN ===
class SearchScreen(ModalScreen):
    """
    Modal screen for searching chat history.

    Features:
    - Full-text search with debounce
    - Filters: agent, date range
    - Paginated results
    - Keyboard navigation
    - Jump to message in main chat
    """

    BINDINGS = [
        Binding("escape", "close_search", "Закрыть"),
        Binding("enter", "select_result", "Выбрать"),
        Binding("up", "prev_result", "Вверх", show=False),
        Binding("down", "next_result", "Вниз", show=False),
        Binding("pageup", "prev_page", "Пред.стр"),
        Binding("pagedown", "next_page", "След.стр"),
    ]

    def __init__(self, search_engine: SearchEngine) -> None:
        super().__init__()
        self.search_engine = search_engine
        self.current_query = SearchQuery()
        self.current_results: Optional[SearchResults] = None
        self.selected_index = 0
        self._search_task: Optional[asyncio.Task] = None
        self._debounce_task: Optional[asyncio.Task] = None

    def compose(self) -> ComposeResult:
        with Container(id="search-modal"):
            # Header
            yield Static("🔍 ПОИСК ПО ИСТОРИИ", id="search-title")

            # Search input
            with Horizontal(id="search-input-row"):
                yield Input(
                    placeholder="Введите ключевые слова...",
                    id="search-input"
                )
                yield Button("Искать", id="search-btn", variant="primary")

            # Filters row
            with Horizontal(id="search-filters"):
                yield Static("Агенты:", classes="filter-label")
                yield Checkbox("User", id="filter-user", value=True)
                yield Checkbox("Gemini", id="filter-gemini", value=True)
                yield Checkbox("GPT", id="filter-gpt", value=True)
                yield Checkbox("Claude", id="filter-claude", value=True)

            # Date filters
            with Horizontal(id="search-date-filters"):
                yield Static("Дата от:", classes="filter-label")
                yield Input(placeholder="YYYY-MM-DD", id="date-from", classes="date-input")
                yield Static("до:", classes="filter-label")
                yield Input(placeholder="YYYY-MM-DD", id="date-to", classes="date-input")
                yield Button("Сброс", id="clear-filters-btn")

            # Status line
            yield Static("Введите запрос для поиска", id="search-status")

            # Results
            with VerticalScroll(id="search-results"):
                pass  # Results will be added dynamically

            # Pagination
            with Horizontal(id="search-pagination"):
                yield Button("◀ Назад", id="prev-page-btn", disabled=True)
                yield Static("", id="page-info")
                yield Button("Вперёд ▶", id="next-page-btn", disabled=True)

            # Footer
            with Horizontal(id="search-footer"):
                yield Static("↑↓ навигация | Enter выбрать | Esc закрыть", classes="search-hint")

    def on_mount(self) -> None:
        """Focus search input on mount."""
        self.query_one("#search-input", Input).focus()

    async def _debounced_search(self, delay: float = 0.3) -> None:
        """Execute search after debounce delay."""
        await asyncio.sleep(delay)
        await self._execute_search()

    async def _execute_search(self, page: int = 1) -> None:
        """Execute search with current filters."""
        # Build query from UI state
        search_input = self.query_one("#search-input", Input)
        self.current_query.text = search_input.value

        # Agent filters
        agents = []
        if self.query_one("#filter-user", Checkbox).value:
            agents.append("user")
        if self.query_one("#filter-gemini", Checkbox).value:
            agents.append("gemini")
        if self.query_one("#filter-gpt", Checkbox).value:
            agents.append("gpt")
        if self.query_one("#filter-claude", Checkbox).value:
            agents.append("claude")
        self.current_query.agents = agents

        # Date filters
        date_from_input = self.query_one("#date-from", Input)
        date_to_input = self.query_one("#date-to", Input)
        self.current_query.date_from = parse_date_input(date_from_input.value)
        self.current_query.date_to = parse_date_input(date_to_input.value)

        # Update status
        status = self.query_one("#search-status", Static)
        status.update("⏳ Поиск...")

        # Execute search in background
        try:
            results = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.search_engine.search(self.current_query, page=page)
            )
            self.current_results = results
            self.selected_index = 0
            self._update_results_ui()
        except Exception as e:
            status.update(f"❌ Ошибка: {e}")

    def _update_results_ui(self) -> None:
        """Update results display."""
        if not self.current_results:
            return

        results = self.current_results

        # Update status
        status = self.query_one("#search-status", Static)
        status.update(results.status_text)

        # Clear and repopulate results
        results_container = self.query_one("#search-results", VerticalScroll)

        # Remove old results
        for child in list(results_container.children):
            child.remove()

        # Add new results
        for idx, result in enumerate(results.items):
            widget = SearchResultWidget(result, idx)
            if idx == self.selected_index:
                widget.add_class("selected")
            results_container.mount(widget)

        # Update pagination
        prev_btn = self.query_one("#prev-page-btn", Button)
        next_btn = self.query_one("#next-page-btn", Button)
        page_info = self.query_one("#page-info", Static)

        prev_btn.disabled = not results.has_prev_page
        next_btn.disabled = not results.has_next_page
        page_info.update(f"Стр. {results.page} из {results.total_pages}")

    def _update_selection(self) -> None:
        """Update visual selection in results."""
        results_container = self.query_one("#search-results", VerticalScroll)
        for idx, child in enumerate(results_container.children):
            if isinstance(child, SearchResultWidget):
                if idx == self.selected_index:
                    child.add_class("selected")
                else:
                    child.remove_class("selected")

    # === EVENT HANDLERS ===

    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle input change with debounce."""
        if event.input.id == "search-input":
            # Cancel previous debounce
            if self._debounce_task:
                self._debounce_task.cancel()
            # Start new debounce
            self._debounce_task = asyncio.create_task(self._debounced_search())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button clicks."""
        if event.button.id == "search-btn":
            asyncio.create_task(self._execute_search())
        elif event.button.id == "prev-page-btn":
            if self.current_results and self.current_results.has_prev_page:
                asyncio.create_task(self._execute_search(self.current_results.page - 1))
        elif event.button.id == "next-page-btn":
            if self.current_results and self.current_results.has_next_page:
                asyncio.create_task(self._execute_search(self.current_results.page + 1))
        elif event.button.id == "clear-filters-btn":
            self._clear_filters()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        """Handle checkbox changes - trigger search."""
        if self._debounce_task:
            self._debounce_task.cancel()
        self._debounce_task = asyncio.create_task(self._debounced_search(0.5))

    def on_search_result_widget_selected(self, event: SearchResultWidget.Selected) -> None:
        """Handle result selection."""
        self.dismiss(event.result)

    def _clear_filters(self) -> None:
        """Clear all filters."""
        self.query_one("#search-input", Input).value = ""
        self.query_one("#filter-user", Checkbox).value = True
        self.query_one("#filter-gemini", Checkbox).value = True
        self.query_one("#filter-gpt", Checkbox).value = True
        self.query_one("#filter-claude", Checkbox).value = True
        self.query_one("#date-from", Input).value = ""
        self.query_one("#date-to", Input).value = ""

        status = self.query_one("#search-status", Static)
        status.update("Фильтры сброшены")

    # === ACTIONS ===

    def action_close_search(self) -> None:
        """Close search screen."""
        self.dismiss(None)

    def action_select_result(self) -> None:
        """Select current result."""
        if self.current_results and self.current_results.items:
            if 0 <= self.selected_index < len(self.current_results.items):
                result = self.current_results.items[self.selected_index]
                self.dismiss(result)

    def action_prev_result(self) -> None:
        """Move to previous result."""
        if self.current_results and self.current_results.items:
            self.selected_index = max(0, self.selected_index - 1)
            self._update_selection()

    def action_next_result(self) -> None:
        """Move to next result."""
        if self.current_results and self.current_results.items:
            max_idx = len(self.current_results.items) - 1
            self.selected_index = min(max_idx, self.selected_index + 1)
            self._update_selection()

    def action_prev_page(self) -> None:
        """Go to previous page."""
        if self.current_results and self.current_results.has_prev_page:
            asyncio.create_task(self._execute_search(self.current_results.page - 1))

    def action_next_page(self) -> None:
        """Go to next page."""
        if self.current_results and self.current_results.has_next_page:
            asyncio.create_task(self._execute_search(self.current_results.page + 1))


# === DDO SCREEN ===

class DDOScreen(ModalScreen):
    """
    Modal screen for DDO (Dynamic Discussion Orchestrator).

    Allows user to input topic and select discussion mode,
    then runs the multi-agent discussion with real-time updates.
    """

    BINDINGS = [
        Binding("escape", "close_ddo", "Закрыть"),
        Binding("enter", "start_discussion", "Начать", show=False),
    ]

    def __init__(self, agents: dict) -> None:
        super().__init__()
        self.agents = agents
        self._running = False
        self._ddo_task: Optional[asyncio.Task] = None

    def compose(self) -> ComposeResult:
        with Container(id="ddo-modal"):
            # Header
            yield Static("🎯 DDO - Dynamic Discussion Orchestrator", id="ddo-title")
            yield Static(
                "Автоматическая дискуссия между Gemini, GPT и Claude",
                classes="ddo-subtitle"
            )

            # Topic input
            yield Static("📝 Тема дискуссии:", classes="ddo-label")
            yield TextArea(
                id="ddo-topic",
                language=None,
                soft_wrap=True,
                show_line_numbers=False,
            )

            # Mode selection
            yield Static("📋 Режим:", classes="ddo-label")
            with Horizontal(id="ddo-modes"):
                yield Button("🏗️ ARCHITECTURE", id="mode-architecture", variant="primary")
                yield Button("🔍 CODE_REVIEW", id="mode-code_review")
                yield Button("💡 BRAINSTORM", id="mode-brainstorm")
                yield Button("⚡ QUICK", id="mode-quick")
                yield Button("🔧 TROUBLESHOOT", id="mode-troubleshoot")

            # Progress section
            yield Static("", id="ddo-progress")
            yield Static("", id="ddo-phase")
            yield Static("", id="ddo-cost")

            # Output log
            with VerticalScroll(id="ddo-log"):
                yield Static("💬 Лог дискуссии появится здесь...", id="ddo-log-content")

            # Actions
            with Horizontal(id="ddo-actions"):
                yield Button("▶️ Запустить", id="ddo-start", variant="success")
                yield Button("⏹️ Стоп", id="ddo-stop", variant="error", disabled=True)
                yield Button("❌ Закрыть", id="ddo-close")

    def on_mount(self) -> None:
        """Focus topic input on mount."""
        self.query_one("#ddo-topic", TextArea).focus()

    def _get_selected_mode(self) -> DiscussionMode:
        """Get currently selected mode from button states."""
        # Check which button has 'primary' variant
        for mode_name in ["architecture", "code_review", "brainstorm", "quick", "troubleshoot"]:
            btn = self.query_one(f"#mode-{mode_name}", Button)
            if btn.variant == "primary":
                return DiscussionMode(mode_name)
        return DiscussionMode.ARCHITECTURE

    def _select_mode(self, mode_name: str) -> None:
        """Select a mode button."""
        for name in ["architecture", "code_review", "brainstorm", "quick", "troubleshoot"]:
            btn = self.query_one(f"#mode-{name}", Button)
            if name == mode_name:
                btn.variant = "primary"
            else:
                btn.variant = "default"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button clicks."""
        btn_id = event.button.id

        # Mode selection
        if btn_id and btn_id.startswith("mode-"):
            mode_name = btn_id.replace("mode-", "")
            self._select_mode(mode_name)
            return

        # Actions
        if btn_id == "ddo-start":
            # Start discussion using Textual's async pattern
            self._do_start_discussion()
        elif btn_id == "ddo-stop":
            self._stop_discussion()
        elif btn_id == "ddo-close":
            self.action_close_ddo()

    def _do_start_discussion(self) -> None:
        """Start DDO discussion (wrapper for async)."""
        if self._running:
            return
        # Use Textual's run_worker with thread=False for async
        self.run_worker(self._start_discussion(), exclusive=True, thread=False)

    async def _start_discussion(self) -> None:
        """Start the DDO discussion."""
        import traceback

        topic_widget = self.query_one("#ddo-topic", TextArea)
        topic = topic_widget.text.strip()

        if not topic:
            self.query_one("#ddo-progress", Static).update("❌ Введите тему дискуссии!")
            return

        if self._running:
            return

        self._running = True

        # Update UI state
        self.query_one("#ddo-start", Button).disabled = True
        self.query_one("#ddo-stop", Button).disabled = False

        # Clear log
        log = self.query_one("#ddo-log-content", Static)
        log.update("")

        mode = self._get_selected_mode()
        self.query_one("#ddo-progress", Static).update(f"🚀 Запуск DDO: {mode.display_name}")
        log.update(f"🚀 Начинаем дискуссию...\nТема: {topic}\nРежим: {mode.display_name}\n")

        # Create orchestrator and run
        orchestrator = DDOOrchestrator(self.agents)

        try:
            log.update(log.renderable + "\n📡 Подключение к агентам...")
            async for event in orchestrator.run_discussion(
                topic=topic,
                mode=mode,
                cost_limit=100.0,  # $1.00 limit
                time_limit=600,    # 10 minutes
            ):
                if not self._running:
                    break
                self._handle_ddo_event(event)
        except Exception as e:
            error_trace = traceback.format_exc()
            log.update(log.renderable + f"\n❌ ОШИБКА: {e}\n\nTraceback:\n{error_trace}")
            self.query_one("#ddo-progress", Static).update(f"❌ Ошибка: {type(e).__name__}")

        self._running = False
        self.query_one("#ddo-start", Button).disabled = False
        self.query_one("#ddo-stop", Button).disabled = True

    def _stop_discussion(self) -> None:
        """Stop the running discussion."""
        self._running = False
        self._update_progress("⏹️ Дискуссия остановлена")

    def _handle_ddo_event(self, event: DDOEvent) -> None:
        """Handle a DDO event."""
        if isinstance(event, PhaseStartEvent):
            self._update_phase(f"📍 {event.phase.display_name} ({event.agent.upper()})")
            self._append_log(f"\n{'='*40}\n📍 ФАЗА: {event.phase.display_name}\n{'='*40}")

        elif isinstance(event, ResponseEvent):
            if event.response:
                agent = event.response.agent.upper()
                content = event.response.content
                # Truncate for log (first 500 chars)
                preview = content[:500] + "..." if len(content) > 500 else content
                self._append_log(f"\n💬 {agent}:\n{preview}")

        elif isinstance(event, GuardFailEvent):
            self._append_log(f"\n⚠️ GUARD FAIL: {event.guard_name}\n   {event.reason}")

        elif isinstance(event, ProgressEvent):
            self._update_progress(
                f"📊 Фаза {event.current_phase}/{event.total_phases}: {event.message}"
            )
            self._update_cost(event.cost_cents, event.elapsed_seconds)

        elif isinstance(event, CompletedEvent):
            if event.success:
                self._update_progress("✅ Дискуссия завершена успешно!")
                self._append_log(f"\n{'='*40}\n✅ УСПЕХ! Консенсус достигнут.\n{'='*40}")
            else:
                self._update_progress("❌ Дискуссия завершена с ошибкой")
                self._append_log(f"\n{'='*40}\n❌ FAIL: Консенсус не достигнут.\n{'='*40}")

            if event.context:
                self._append_log(
                    f"\n📊 Статистика:\n"
                    f"   Стоимость: ${event.context.cost_usd:.4f}\n"
                    f"   Время: {event.context.elapsed_str}\n"
                    f"   Сообщений: {event.context.response_count}"
                )

        elif isinstance(event, ErrorEvent):
            self._append_log(f"\n❌ ОШИБКА: {event.error}")

    def _update_progress(self, text: str) -> None:
        """Update progress display."""
        try:
            self.query_one("#ddo-progress", Static).update(text)
        except Exception:
            pass

    def _update_phase(self, text: str) -> None:
        """Update phase display."""
        try:
            self.query_one("#ddo-phase", Static).update(text)
        except Exception:
            pass

    def _update_cost(self, cost_cents: float, elapsed_seconds: float) -> None:
        """Update cost display."""
        try:
            mins = int(elapsed_seconds) // 60
            secs = int(elapsed_seconds) % 60
            self.query_one("#ddo-cost", Static).update(
                f"💰 ${cost_cents/100:.4f} | ⏱️ {mins:02d}:{secs:02d}"
            )
        except Exception:
            pass

    def _append_log(self, text: str) -> None:
        """Append text to log."""
        try:
            log = self.query_one("#ddo-log-content", Static)
            current = str(log.renderable) if log.renderable else ""
            log.update(current + text)

            # Scroll to bottom
            scroll = self.query_one("#ddo-log", VerticalScroll)
            scroll.scroll_end(animate=False)
        except Exception:
            pass

    def action_close_ddo(self) -> None:
        """Close DDO screen."""
        self._running = False
        self.dismiss(None)

    def action_start_discussion(self) -> None:
        """Start discussion on Enter."""
        if not self._running:
            asyncio.create_task(self._start_discussion())


# === MAIN WIDGETS ===

class MessageWidget(Static):
    """Widget for displaying a single chat message."""

    def __init__(self, message: ChatMessage, msg_id: Optional[int] = None) -> None:
        self.message = message
        self.msg_id = msg_id
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

        if self.msg_id is not None:
            self.id = f"msg-{self.msg_id}"

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


class TaskPanelWidget(Static):
    """Widget showing current task status for all agents."""

    def __init__(self) -> None:
        super().__init__()
        self.id = "task-panel"
        self._agent_states: dict[str, AgentTaskState] = {}

    def compose(self) -> ComposeResult:
        yield Static("📋 СТАТУС ЗАДАЧ", classes="task-panel-header")
        yield Static("", id="task-gemini", classes="task-line")
        yield Static("", id="task-gpt", classes="task-line")
        yield Static("", id="task-claude", classes="task-line")

    def update_agent(self, agent_key: str, state: AgentTaskState) -> None:
        """Update status for a specific agent."""
        self._agent_states[agent_key] = state

        status_display = {
            TaskStatus.IDLE: ("🟢", "ГОТОВ"),
            TaskStatus.THINKING: ("🟡", "ДУМАЕТ"),
            TaskStatus.WORKING: ("🔴", "РАБОТАЕТ"),
            TaskStatus.DONE: ("✅", "ГОТОВО"),
        }

        icon, status_text = status_display.get(state.status, ("⚪", "?"))

        agent_names = {"gemini": "Gemini", "gpt": "GPT", "claude": "Claude"}
        agent_name = agent_names.get(agent_key, agent_key)

        if state.status in (TaskStatus.THINKING, TaskStatus.WORKING):
            line = f"{icon} {agent_name}: {status_text} ⏱️ {state.elapsed_str}"
            if state.stage:
                line += f" | {state.stage}"
        else:
            line = f"{icon} {agent_name}: {status_text}"

        try:
            widget = self.query_one(f"#task-{agent_key}", Static)
            widget.update(line)
        except Exception:
            pass


class AgentStatusWidget(Static):
    """Widget showing agent connection status with task state."""

    def __init__(self, agent_key: str, agent_info: dict) -> None:
        self.agent_key = agent_key
        self.agent_info = agent_info
        self._task_state: Optional[AgentTaskState] = None
        super().__init__()

    def compose(self) -> ComposeResult:
        name = self.agent_info["name"]
        connected = self.agent_info["connected"]

        icon = "🟢" if connected else "🔴"
        status = "OK" if connected else "OFF"

        self.add_class("agent-status")
        self.add_class("agent-connected" if connected else "agent-disconnected")

        yield Static(f"{icon} {name}: {status}", id=f"status-{self.agent_key}")

    def update_task_state(self, state: AgentTaskState) -> None:
        """Update the task state display."""
        self._task_state = state

        name = self.agent_info["name"]
        connected = self.agent_info["connected"]

        if not connected:
            display = f"🔴 {name}: OFF"
        elif state.status == TaskStatus.IDLE:
            display = f"🟢 {name}: OK"
        elif state.status == TaskStatus.THINKING:
            display = f"🟡 {name}: ⏳ {state.elapsed_str}"
        elif state.status == TaskStatus.WORKING:
            display = f"🔴 {name}: ⚙️ {state.elapsed_str}"
        elif state.status == TaskStatus.DONE:
            display = f"✅ {name}: ✓"
        else:
            display = f"⚪ {name}: ?"

        try:
            widget = self.query_one(f"#status-{self.agent_key}", Static)
            widget.update(display)
        except Exception:
            pass


# === MAIN APPLICATION ===

class HopeOmniChat(App):
    """HOPE OMNI-CHAT - Trinity AI Chat Application."""

    CSS_PATH = "src/styles.tcss"
    TITLE = "HOPE OMNI-CHAT v1.4"

    BINDINGS = [
        Binding("f1", "send_gemini", "Gemini", show=True),
        Binding("f2", "send_gpt", "GPT", show=True),
        Binding("f3", "send_claude", "Claude", show=True),
        Binding("f5", "send_all", "Send All", show=True),
        Binding("f6", "copy_gemini", "📋Gem"),
        Binding("f7", "copy_gpt", "📋GPT"),
        Binding("f8", "copy_claude", "📋Cld"),
        Binding("ctrl+d", "open_ddo", "🎯DDO"),
        Binding("ctrl+f", "open_search", "🔍Search"),
        Binding("ctrl+h", "load_history", "History"),
        Binding("ctrl+l", "load_file", "Load"),
        Binding("ctrl+e", "export", "Export"),
        Binding("ctrl+q", "quit", "Quit"),
        Binding("escape", "clear_input", "Clear", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._history_path = Path(__file__).parent / "chat_history.jsonl"
        self.bus = EventBus(history_path=self._history_path)
        self.bus.set_callbacks(
            on_message=self._handle_chat_message,
            on_typing=self._handle_typing,
            on_stats_update=self._handle_stats_update,
            on_task_update=self._handle_task_update,
        )
        self._typing_agents: set[str] = set()

        # Search engine
        self.search_engine = SearchEngine(self._history_path)

        # DDO agents (separate instances for DDO)
        self._ddo_agents = create_all_agents()

        # Message counter for IDs
        self._message_counter = 0

        # Track last responses for copy feature
        self._last_responses: dict[str, str] = {
            "gemini": "",
            "gpt": "",
            "claude": "",
        }

        # Agent status widgets for task updates
        self._agent_widgets: dict[str, AgentStatusWidget] = {}

        # Inbox folder for auto-send
        self._inbox_path = Path(__file__).parent / "inbox"
        self._inbox_path.mkdir(exist_ok=True)
        self._inbox_task: Optional[asyncio.Task] = None
        self._timer_task: Optional[asyncio.Task] = None

    def compose(self) -> ComposeResult:
        yield Header()

        with Container(id="main-container"):
            # Top bar with cost and task panel
            with Horizontal(id="top-bar"):
                yield CostDisplay()
                yield TaskPanelWidget()

            # Chat log
            with VerticalScroll(id="chat-log"):
                yield Static(
                    "🚀 HOPE OMNI-CHAT v1.4 - DDO + Search\n\n"
                    "F1/F2/F3 — отправить агенту | F5 — всем\n"
                    "Ctrl+D — 🎯 DDO (автоматическая дискуссия агентов)\n"
                    "Ctrl+F — 🔍 ПОИСК по истории\n"
                    "Ctrl+H — загрузить историю при старте\n"
                    "F6/F7/F8 — копировать ответ | Ctrl+E — экспорт\n\n"
                    "📋 Статус задач отображается в панели сверху\n"
                    "🟢 ГОТОВ | 🟡 ДУМАЕТ | 🔴 РАБОТАЕТ | ✅ ГОТОВО\n\n"
                    "💜 Gemini (стратег) | 💛 GPT (аналитик) | 💙 Claude (разработчик)",
                    classes="message message-system",
                    id="welcome-message"
                )

            # Typing indicator
            yield Static("", id="typing-indicator", classes="typing-indicator")

            # Status bar with agent statuses
            with Horizontal(id="status-bar"):
                for key, info in self.bus.get_agent_status().items():
                    widget = AgentStatusWidget(key, info)
                    self._agent_widgets[key] = widget
                    yield widget

            # Input area
            with Horizontal(id="input-area"):
                yield TextArea(
                    id="message-input",
                    language=None,
                    soft_wrap=True,
                    show_line_numbers=False,
                )
                yield Button("Send All", id="send-all", classes="send-button")

        yield Footer()

    def on_mount(self) -> None:
        """Focus input on mount and start background tasks."""
        self.query_one("#message-input", TextArea).focus()
        self._inbox_task = asyncio.create_task(self._watch_inbox())
        self._timer_task = asyncio.create_task(self._update_timers())

    async def _update_timers(self) -> None:
        """Update task panel timers every second."""
        while True:
            try:
                for agent_key, state in self.bus.get_all_task_states().items():
                    if state.status in (TaskStatus.THINKING, TaskStatus.WORKING):
                        try:
                            task_panel = self.query_one(TaskPanelWidget)
                            task_panel.update_agent(agent_key, state)
                        except Exception:
                            pass
                        if agent_key in self._agent_widgets:
                            self._agent_widgets[agent_key].update_task_state(state)
            except Exception:
                pass
            await asyncio.sleep(1)

    async def _watch_inbox(self) -> None:
        """Watch inbox folder for new .txt files and auto-send them."""
        while True:
            try:
                for txt_file in self._inbox_path.glob("*.txt"):
                    if txt_file.name.startswith("."):
                        continue
                    try:
                        content = txt_file.read_text(encoding="utf-8").strip()
                        if content:
                            self._handle_chat_message(ChatMessage(
                                role=MessageRole.SYSTEM,
                                content=f"📂 Inbox: загружен файл {txt_file.name} ({len(content)} символов)",
                            ))
                            await self.bus.send_user_message(content, target=None)
                        txt_file.unlink()
                    except Exception as e:
                        self._handle_chat_message(ChatMessage(
                            role=MessageRole.SYSTEM,
                            content=f"❌ Ошибка чтения {txt_file.name}: {e}",
                        ))
            except Exception:
                pass
            await asyncio.sleep(2)

    def _handle_chat_message(self, message: ChatMessage) -> None:
        """Handle new message from bus."""
        try:
            if message.role == MessageRole.GEMINI:
                self._last_responses["gemini"] = message.content
            elif message.role == MessageRole.GPT:
                self._last_responses["gpt"] = message.content
            elif message.role == MessageRole.CLAUDE:
                self._last_responses["claude"] = message.content

            self._message_counter += 1
            chat_log = self.query_one("#chat-log", VerticalScroll)
            chat_log.mount(MessageWidget(message, self._message_counter))
            chat_log.scroll_end(animate=False)
        except Exception:
            pass

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
            pass

    def _handle_stats_update(self, stats: SessionStats) -> None:
        """Handle stats update."""
        try:
            cost_display = self.query_one(CostDisplay)
            cost_display.update_cost(stats)
        except Exception:
            pass

    def _handle_task_update(self, agent_key: str, state: AgentTaskState) -> None:
        """Handle task status update."""
        try:
            task_panel = self.query_one(TaskPanelWidget)
            task_panel.update_agent(agent_key, state)
            if agent_key in self._agent_widgets:
                self._agent_widgets[agent_key].update_task_state(state)
        except Exception:
            pass

    async def _send_message(self, target: str | None = None) -> None:
        """Send message from input."""
        input_widget = self.query_one("#message-input", TextArea)
        text = input_widget.text.strip()

        if not text:
            return

        if contains_secret(text):
            self._handle_chat_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content=(
                    "🚫 BLOCKED: Сообщение содержит секрет (API key/token)!\n"
                    f"Обнаружено: {redact(text)}\n"
                    "Сообщение НЕ отправлено агентам."
                ),
            ))
            input_widget.text = ""
            input_widget.focus()
            return

        input_widget.text = ""
        input_widget.focus()
        asyncio.create_task(self.bus.send_user_message(text, target))

    # === DDO ===

    def action_open_ddo(self) -> None:
        """Open DDO (Dynamic Discussion Orchestrator) modal."""
        def handle_ddo_result(result) -> None:
            if result:
                self._handle_chat_message(ChatMessage(
                    role=MessageRole.SYSTEM,
                    content=f"🎯 DDO дискуссия завершена",
                ))

        self.push_screen(DDOScreen(self._ddo_agents), handle_ddo_result)

    # === SEARCH ===

    def action_open_search(self) -> None:
        """Open search modal."""
        def handle_search_result(result: Optional[SearchResult]) -> None:
            if result:
                self._handle_chat_message(ChatMessage(
                    role=MessageRole.SYSTEM,
                    content=(
                        f"🔍 Найдено сообщение от {result.time_str}:\n"
                        f"{result.role_display}\n"
                        f"───────────────\n"
                        f"{result.content[:500]}{'...' if len(result.content) > 500 else ''}"
                    ),
                ))

        self.push_screen(SearchScreen(self.search_engine), handle_search_result)

    def action_load_history(self) -> None:
        """Load recent history on startup."""
        try:
            query = SearchQuery()  # Empty query = all messages
            results = self.search_engine.search(query, page=1)

            if results.total_count == 0:
                self._handle_chat_message(ChatMessage(
                    role=MessageRole.SYSTEM,
                    content="📜 История пуста",
                ))
                return

            # Load last 50 messages (oldest first for correct order)
            items = list(reversed(results.items[:50]))

            self._handle_chat_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content=f"📜 Загружена история: {len(items)} сообщений из {results.total_count}",
            ))

            for result in items:
                role = MessageRole(result.role) if result.role in [r.value for r in MessageRole] else MessageRole.SYSTEM
                msg = ChatMessage(
                    role=role,
                    content=result.content,
                    timestamp=result.timestamp,
                )
                self._message_counter += 1
                try:
                    chat_log = self.query_one("#chat-log", VerticalScroll)
                    chat_log.mount(MessageWidget(msg, self._message_counter))
                except Exception:
                    pass

        except Exception as e:
            self._handle_chat_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content=f"❌ Ошибка загрузки истории: {e}",
            ))

    # === OTHER ACTIONS ===

    async def action_send_gemini(self) -> None:
        await self._send_message("gemini")

    async def action_send_gpt(self) -> None:
        await self._send_message("gpt")

    async def action_send_claude(self) -> None:
        await self._send_message("claude")

    async def action_send_all(self) -> None:
        await self._send_message(None)

    def action_clear_input(self) -> None:
        self.query_one("#message-input", TextArea).text = ""

    def action_copy_gemini(self) -> None:
        self._copy_agent_response("gemini", "Gemini")

    def action_copy_gpt(self) -> None:
        self._copy_agent_response("gpt", "GPT")

    def action_copy_claude(self) -> None:
        self._copy_agent_response("claude", "Claude")

    def _copy_agent_response(self, agent_key: str, agent_name: str) -> None:
        response = self._last_responses.get(agent_key, "")
        if not response:
            self._handle_chat_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content=f"⚠️ Нет ответа от {agent_name} для копирования",
            ))
            return

        if copy_to_clipboard(response):
            self._handle_chat_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content=f"📋 Ответ {agent_name} скопирован ({len(response)} символов)",
            ))
        else:
            self._handle_chat_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content=f"❌ Не удалось скопировать. pip install pyperclip",
            ))

    def action_load_file(self) -> None:
        message_file = self._inbox_path / "message.txt"

        if not message_file.exists():
            self._handle_chat_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content=f"📂 Создайте файл: {message_file}",
            ))
            message_file.write_text("# Напишите сообщение здесь\n", encoding="utf-8")
            return

        try:
            content = message_file.read_text(encoding="utf-8").strip()
            lines = [l for l in content.split("\n") if not l.strip().startswith("#")]
            content = "\n".join(lines).strip()

            if content:
                input_widget = self.query_one("#message-input", TextArea)
                input_widget.text = content
                input_widget.focus()
                self._handle_chat_message(ChatMessage(
                    role=MessageRole.SYSTEM,
                    content=f"📂 Загружено ({len(content)} символов). F5 для отправки.",
                ))
            else:
                self._handle_chat_message(ChatMessage(
                    role=MessageRole.SYSTEM,
                    content="⚠️ Файл пуст",
                ))
        except Exception as e:
            self._handle_chat_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content=f"❌ Ошибка: {e}",
            ))

    def action_export(self) -> None:
        export_path = Path(__file__).parent / f"chat_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        self.bus.export_to_markdown(export_path)
        self._handle_chat_message(ChatMessage(
            role=MessageRole.SYSTEM,
            content=f"📁 Экспорт: {export_path.name}",
        ))

    # === EVENT HANDLERS ===

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-all":
            asyncio.create_task(self._send_message(None))

    def on_key(self, event) -> None:
        if event.key == "ctrl+enter":
            asyncio.create_task(self._send_message(None))


def main() -> None:
    """Entry point with crash protection."""
    try:
        app = HopeOmniChat()
        app.run()
    except KeyboardInterrupt:
        print("\n👋 OMNI-CHAT закрыт")
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        print("Enter для выхода...")
        input()


if __name__ == "__main__":
    main()
