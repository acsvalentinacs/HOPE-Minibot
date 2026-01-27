# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-26T11:00:00Z
# Modified by: Claude (opus-4)
# Modified at: 2026-01-26T15:15:00Z
# Purpose: Event Bus for HOPE OMNI-CHAT message routing + Task Status
# === END SIGNATURE ===
"""
Event Bus - Central message router for HOPE OMNI-CHAT.

Handles:
- Message routing to agents
- Parallel execution (all agents at once)
- Sequential execution (one agent at a time)
- History tracking
- Cost aggregation
- Task status tracking (NEW in v1.1)
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Any
from .connectors import BaseAgent, create_all_agents
from .security import redact_any


class MessageRole(Enum):
    USER = "user"
    GEMINI = "gemini"
    GPT = "gpt"
    CLAUDE = "claude"
    SYSTEM = "system"


class TaskStatus(Enum):
    """Task execution status."""
    IDLE = "idle"           # Ready for new task
    THINKING = "thinking"   # Processing request (< 60 sec)
    WORKING = "working"     # Long task (> 60 sec)
    DONE = "done"           # Task completed


@dataclass
class AgentTaskState:
    """Track individual agent's task state."""
    status: TaskStatus = TaskStatus.IDLE
    task_name: str = ""
    stage: str = ""
    stage_num: int = 0
    total_stages: int = 0
    started_at: Optional[datetime] = None
    last_update: Optional[datetime] = None

    def start_task(self, task_name: str = "Обработка запроса") -> None:
        """Start a new task."""
        self.status = TaskStatus.THINKING
        self.task_name = task_name
        self.stage = "Получение ответа"
        self.stage_num = 1
        self.total_stages = 1
        self.started_at = datetime.utcnow()
        self.last_update = datetime.utcnow()

    def update_stage(self, stage: str, stage_num: int = 0, total_stages: int = 0) -> None:
        """Update task stage."""
        self.stage = stage
        if stage_num > 0:
            self.stage_num = stage_num
        if total_stages > 0:
            self.total_stages = total_stages
        self.last_update = datetime.utcnow()

        # Upgrade to WORKING if > 5 seconds
        if self.started_at:
            elapsed = (datetime.utcnow() - self.started_at).total_seconds()
            if elapsed > 5:
                self.status = TaskStatus.WORKING

    def finish_task(self) -> None:
        """Mark task as done."""
        self.status = TaskStatus.DONE
        self.last_update = datetime.utcnow()

    def reset(self) -> None:
        """Reset to idle."""
        self.status = TaskStatus.IDLE
        self.task_name = ""
        self.stage = ""
        self.stage_num = 0
        self.total_stages = 0
        self.started_at = None
        self.last_update = None

    @property
    def elapsed_seconds(self) -> int:
        """Get elapsed time in seconds."""
        if self.started_at:
            return int((datetime.utcnow() - self.started_at).total_seconds())
        return 0

    @property
    def elapsed_str(self) -> str:
        """Get elapsed time as string MM:SS."""
        secs = self.elapsed_seconds
        return f"{secs // 60:02d}:{secs % 60:02d}"


@dataclass
class ChatMessage:
    """Single chat message."""
    role: MessageRole
    content: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    tokens_used: int = 0
    cost_cents: float = 0.0

    def to_dict(self) -> dict:
        return {
            "role": self.role.value,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "tokens_used": self.tokens_used,
            "cost_cents": self.cost_cents,
        }


@dataclass
class SessionStats:
    """Session statistics."""
    messages_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_cents: float = 0.0
    start_time: datetime = field(default_factory=datetime.utcnow)

    def update_from_agents(self, agents: dict[str, BaseAgent]) -> None:
        """Update stats from all agents."""
        self.total_cost_cents = 0.0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        for agent in agents.values():
            usage = agent.get_usage()
            self.total_input_tokens += usage.input_tokens
            self.total_output_tokens += usage.output_tokens
            self.total_cost_cents += usage.total_cost_cents


class EventBus:
    """
    Central event bus for routing messages between User and AI Agents.

    Supports:
    - Broadcast to all agents (parallel)
    - Send to specific agent
    - Message history with JSONL persistence
    - Task status tracking (v1.1)
    """

    def __init__(self, history_path: Optional[Path] = None):
        self.agents = create_all_agents()
        self.history: list[ChatMessage] = []
        self.stats = SessionStats()
        self.history_path = history_path

        # Task states for each agent
        self.task_states: dict[str, AgentTaskState] = {
            "gemini": AgentTaskState(),
            "gpt": AgentTaskState(),
            "claude": AgentTaskState(),
        }

        # Callbacks for UI updates
        self._on_message: Optional[Callable[[ChatMessage], None]] = None
        self._on_typing: Optional[Callable[[str, bool], None]] = None
        self._on_stats_update: Optional[Callable[[SessionStats], None]] = None
        self._on_task_update: Optional[Callable[[str, AgentTaskState], None]] = None

    def set_callbacks(
        self,
        on_message: Optional[Callable[[ChatMessage], None]] = None,
        on_typing: Optional[Callable[[str, bool], None]] = None,
        on_stats_update: Optional[Callable[[SessionStats], None]] = None,
        on_task_update: Optional[Callable[[str, AgentTaskState], None]] = None,
    ) -> None:
        """Set UI callback functions."""
        self._on_message = on_message
        self._on_typing = on_typing
        self._on_stats_update = on_stats_update
        self._on_task_update = on_task_update

    def _emit_message(self, msg: ChatMessage) -> None:
        """Emit message to UI."""
        self.history.append(msg)
        self.stats.messages_count = len(self.history)
        if self._on_message:
            self._on_message(msg)
        self._save_message(msg)

    def _emit_typing(self, agent_name: str, is_typing: bool) -> None:
        """Emit typing indicator to UI."""
        if self._on_typing:
            self._on_typing(agent_name, is_typing)

    def _emit_task_update(self, agent_key: str) -> None:
        """Emit task status update to UI."""
        if self._on_task_update:
            state = self.task_states.get(agent_key)
            if state:
                self._on_task_update(agent_key, state)

    def _update_stats(self) -> None:
        """Update and emit session stats."""
        self.stats.update_from_agents(self.agents)
        if self._on_stats_update:
            self._on_stats_update(self.stats)

    def _save_message(self, msg: ChatMessage) -> None:
        """Append message to JSONL history file. SECURITY: Redacts secrets."""
        if self.history_path:
            try:
                # SECURITY: Redact any secrets before persisting to disk
                safe_dict = redact_any(msg.to_dict())
                with open(self.history_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(safe_dict, ensure_ascii=False) + "\n")
            except Exception:
                pass  # Fail silently for history

    def get_task_state(self, agent_key: str) -> Optional[AgentTaskState]:
        """Get task state for an agent."""
        return self.task_states.get(agent_key)

    def get_all_task_states(self) -> dict[str, AgentTaskState]:
        """Get all task states."""
        return self.task_states.copy()

    async def send_to_agent(self, agent_key: str, text: str) -> None:
        """Send message to a specific agent."""
        agent = self.agents.get(agent_key)
        if not agent:
            self._emit_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content=f"❌ Агент '{agent_key}' не найден",
            ))
            return

        if not agent.is_connected:
            self._emit_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content=f"❌ {agent.name} недоступен: {agent.error_message}",
            ))
            return

        # Start task tracking
        task_state = self.task_states[agent_key]
        task_state.start_task("Обработка запроса")
        self._emit_task_update(agent_key)

        # Show typing indicator
        self._emit_typing(agent.name, True)

        try:
            response = await asyncio.wait_for(
                agent.ask_async(text),
                timeout=60.0
            )

            role = MessageRole[agent_key.upper()]
            usage = agent.get_usage()

            # Mark task as done
            task_state.finish_task()
            self._emit_task_update(agent_key)

            self._emit_message(ChatMessage(
                role=role,
                content=response,
                tokens_used=usage.input_tokens + usage.output_tokens,
                cost_cents=usage.total_cost_cents,
            ))
        except asyncio.TimeoutError:
            task_state.reset()
            self._emit_task_update(agent_key)
            self._emit_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content=f"⏱️ Таймаут: {agent.name} не ответил за 60 секунд",
            ))
        except Exception as e:
            task_state.reset()
            self._emit_task_update(agent_key)
            self._emit_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content=f"❌ Ошибка {agent.name}: {e}",
            ))
        finally:
            self._emit_typing(agent.name, False)
            self._update_stats()

            # Reset to IDLE after 3 seconds
            await asyncio.sleep(3)
            task_state.reset()
            self._emit_task_update(agent_key)

    async def broadcast(self, text: str, agents_keys: Optional[list[str]] = None) -> None:
        """
        Send message to multiple agents in parallel.

        Args:
            text: Message to send
            agents_keys: List of agent keys, or None for all connected agents
        """
        if agents_keys is None:
            agents_keys = [k for k, a in self.agents.items() if a.is_connected]

        if not agents_keys:
            self._emit_message(ChatMessage(
                role=MessageRole.SYSTEM,
                content="❌ Нет доступных агентов",
            ))
            return

        # Run all agents in parallel
        tasks = [self.send_to_agent(key, text) for key in agents_keys]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def send_user_message(
        self,
        text: str,
        target: Optional[str] = None
    ) -> None:
        """
        Process user message.

        Args:
            text: User's message
            target: Specific agent key, or None to broadcast to all
        """
        # Record user message
        self._emit_message(ChatMessage(
            role=MessageRole.USER,
            content=text,
        ))

        # Route message
        if target:
            await self.send_to_agent(target, text)
        else:
            await self.broadcast(text)

    def get_agent_status(self) -> dict[str, dict[str, Any]]:
        """Get status of all agents."""
        return {
            key: {
                "name": agent.name,
                "connected": agent.is_connected,
                "error": agent.error_message,
                "color": agent.color,
                "cost_cents": agent.get_usage().total_cost_cents,
            }
            for key, agent in self.agents.items()
        }

    def export_to_markdown(self, path: Path) -> None:
        """Export chat history to Markdown file."""
        lines = [
            "# HOPE OMNI-CHAT Session Export",
            f"**Date:** {self.stats.start_time.strftime('%Y-%m-%d %H:%M:%S')} UTC",
            f"**Messages:** {self.stats.messages_count}",
            f"**Total Cost:** ${self.stats.total_cost_cents / 100:.4f}",
            "",
            "---",
            "",
        ]

        role_emoji = {
            MessageRole.USER: "👤 **User**",
            MessageRole.GEMINI: "💜 **Gemini**",
            MessageRole.GPT: "💛 **GPT**",
            MessageRole.CLAUDE: "💙 **Claude**",
            MessageRole.SYSTEM: "⚙️ **System**",
        }

        for msg in self.history:
            emoji = role_emoji.get(msg.role, "❓")
            lines.append(f"### {emoji}")
            lines.append(f"_{msg.timestamp.strftime('%H:%M:%S')}_")
            lines.append("")
            lines.append(msg.content)
            lines.append("")
            lines.append("---")
            lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")
