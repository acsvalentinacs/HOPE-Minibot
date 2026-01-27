# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T12:20:00Z
# Purpose: DDO Type definitions - Dataclasses and Enums
# === END SIGNATURE ===
"""
DDO Type Definitions.

Contains all dataclasses and enums used throughout the DDO module.
Designed for type safety and clear contracts between components.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Callable, Any


class DiscussionPhase(Enum):
    """
    Phases of a discussion lifecycle.

    Flow: INIT → ARCHITECT → ANALYZE → IMPLEMENT → SECURITY_REVIEW
                → CODE_REVIEW → REFINE → SYNTHESIZE → CONSENSUS → DONE

    Terminal states: DONE, FAILED, ESCALATED
    """
    INIT = "init"                    # Инициализация дискуссии
    ARCHITECT = "architect"          # Gemini: архитектурные решения
    ANALYZE = "analyze"              # GPT: анализ и ТЗ
    IMPLEMENT = "implement"          # Claude: реализация кода
    SECURITY_REVIEW = "security"     # Gemini: security audit
    CODE_REVIEW = "code_review"      # GPT: code review
    REFINE = "refine"                # Claude: доработка по ревью
    SYNTHESIZE = "synthesize"        # Формирование итогового результата
    CONSENSUS = "consensus"          # Проверка консенсуса между агентами
    DONE = "done"                    # Успешное завершение
    FAILED = "failed"                # Провал (fail-closed)
    ESCALATED = "escalated"          # Эскалация на человека

    @property
    def is_terminal(self) -> bool:
        """Check if this is a terminal (final) state."""
        return self in (
            DiscussionPhase.DONE,
            DiscussionPhase.FAILED,
            DiscussionPhase.ESCALATED,
        )

    @property
    def display_name(self) -> str:
        """Human-readable name for UI."""
        names = {
            DiscussionPhase.INIT: "🚀 Инициализация",
            DiscussionPhase.ARCHITECT: "🏗️ Архитектура (Gemini)",
            DiscussionPhase.ANALYZE: "📊 Анализ (GPT)",
            DiscussionPhase.IMPLEMENT: "💻 Реализация (Claude)",
            DiscussionPhase.SECURITY_REVIEW: "🔒 Security Review (Gemini)",
            DiscussionPhase.CODE_REVIEW: "🔍 Code Review (GPT)",
            DiscussionPhase.REFINE: "✨ Доработка (Claude)",
            DiscussionPhase.SYNTHESIZE: "📝 Синтез",
            DiscussionPhase.CONSENSUS: "🤝 Консенсус",
            DiscussionPhase.DONE: "✅ Завершено",
            DiscussionPhase.FAILED: "❌ Провал",
            DiscussionPhase.ESCALATED: "⚠️ Эскалация",
        }
        return names.get(self, self.value)


class DiscussionMode(Enum):
    """
    Discussion modes - determines the template/flow used.

    Each mode has a predefined sequence of phases optimized
    for specific types of tasks.
    """
    ARCHITECTURE = "architecture"    # Full design cycle with implementation
    CODE_REVIEW = "code_review"      # Review existing code
    BRAINSTORM = "brainstorm"        # Generate and evaluate ideas
    DEBATE = "debate"                # Argue for/against a position
    TROUBLESHOOT = "troubleshoot"    # Diagnose and fix problems
    QUICK = "quick"                  # Fast 3-phase discussion
    CUSTOM = "custom"                # User-defined flow

    @property
    def display_name(self) -> str:
        """Human-readable name."""
        names = {
            DiscussionMode.ARCHITECTURE: "🏗️ Архитектура",
            DiscussionMode.CODE_REVIEW: "🔍 Code Review",
            DiscussionMode.BRAINSTORM: "💡 Брейншторм",
            DiscussionMode.DEBATE: "⚔️ Дебаты",
            DiscussionMode.TROUBLESHOOT: "🔧 Troubleshoot",
            DiscussionMode.QUICK: "⚡ Быстрый",
            DiscussionMode.CUSTOM: "🎨 Кастомный",
        }
        return names.get(self, self.value)


class ResponseFlag(Enum):
    """Flags that can be attached to agent responses."""
    NEEDS_CLARIFICATION = "needs_clarification"
    RISK_DETECTED = "risk_detected"
    LOW_CONFIDENCE = "low_confidence"
    CONFLICT = "conflict"
    APPROVED = "approved"
    REJECTED = "rejected"
    PARTIAL = "partial"


@dataclass
class AgentResponse:
    """
    Single response from an AI agent during discussion.

    Attributes:
        agent: Agent key (gemini, gpt, claude)
        phase: Discussion phase when response was generated
        content: Full text response
        timestamp: When response was received
        tokens_used: Total tokens (input + output)
        cost_cents: Cost in cents
        confidence: Agent's self-reported confidence (0.0-1.0)
        flags: List of response flags
        metadata: Additional metadata dict
    """
    agent: str
    phase: DiscussionPhase
    content: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    tokens_used: int = 0
    cost_cents: float = 0.0
    confidence: float = 1.0
    flags: list[ResponseFlag] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Validate fields after initialization."""
        if not 0.0 <= self.confidence <= 1.0:
            self.confidence = max(0.0, min(1.0, self.confidence))
        if self.tokens_used < 0:
            self.tokens_used = 0
        if self.cost_cents < 0:
            self.cost_cents = 0.0

    @property
    def is_error(self) -> bool:
        """Check if response is an error."""
        return self.content.startswith("❌")

    @property
    def is_approved(self) -> bool:
        """Check if response contains approval."""
        return ResponseFlag.APPROVED in self.flags or "✅ APPROVED" in self.content

    @property
    def is_rejected(self) -> bool:
        """Check if response contains rejection."""
        return ResponseFlag.REJECTED in self.flags or "❌ REJECTED" in self.content

    @property
    def word_count(self) -> int:
        """Get word count of response."""
        return len(self.content.split())

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "agent": self.agent,
            "phase": self.phase.value,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "tokens_used": self.tokens_used,
            "cost_cents": self.cost_cents,
            "confidence": self.confidence,
            "flags": [f.value for f in self.flags],
            "metadata": self.metadata,
        }


@dataclass
class DiscussionContext:
    """
    Full context of a discussion session.

    Contains all state needed to track and manage the discussion.
    This is the central data structure passed between components.

    Attributes:
        id: Unique discussion identifier
        mode: Discussion mode (determines template)
        topic: Main topic/question being discussed
        goal: What we want to achieve
        constraints: List of constraints/limitations
        responses: All agent responses collected so far
        current_phase: Current phase in FSM
        started_at: When discussion started
        ended_at: When discussion ended (None if ongoing)
        total_cost_cents: Accumulated cost
        cost_limit_cents: Maximum allowed cost (fail-closed)
        time_limit_seconds: Maximum allowed time (fail-closed)
        max_responses: Maximum number of responses allowed
        consensus_reached: Whether agents reached consensus
        final_result: Final synthesized result
        escalation_reason: Why discussion was escalated (if applicable)
        user_id: User who initiated (for audit)
    """
    id: str
    mode: DiscussionMode
    topic: str
    goal: str = ""
    constraints: list[str] = field(default_factory=list)
    responses: list[AgentResponse] = field(default_factory=list)
    current_phase: DiscussionPhase = DiscussionPhase.INIT
    started_at: datetime = field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None
    total_cost_cents: float = 0.0
    cost_limit_cents: float = 100.0  # $1.00 default limit
    time_limit_seconds: int = 600    # 10 minutes default
    max_responses: int = 30          # Safety limit
    consensus_reached: bool = False
    final_result: Optional[str] = None
    escalation_reason: Optional[str] = None
    user_id: str = "default"

    def __post_init__(self):
        """Set default goal if not provided."""
        if not self.goal:
            self.goal = f"Решить задачу: {self.topic}"

    @property
    def elapsed_seconds(self) -> float:
        """Get elapsed time in seconds."""
        end = self.ended_at or datetime.utcnow()
        return (end - self.started_at).total_seconds()

    @property
    def elapsed_str(self) -> str:
        """Get elapsed time as MM:SS string."""
        secs = int(self.elapsed_seconds)
        return f"{secs // 60:02d}:{secs % 60:02d}"

    @property
    def cost_usd(self) -> float:
        """Get total cost in USD."""
        return self.total_cost_cents / 100.0

    @property
    def is_over_budget(self) -> bool:
        """Check if cost limit exceeded."""
        return self.total_cost_cents >= self.cost_limit_cents

    @property
    def is_over_time(self) -> bool:
        """Check if time limit exceeded."""
        return self.elapsed_seconds >= self.time_limit_seconds

    @property
    def is_terminal(self) -> bool:
        """Check if discussion is in terminal state."""
        return self.current_phase.is_terminal

    @property
    def response_count(self) -> int:
        """Get number of responses."""
        return len(self.responses)

    def get_responses_by_agent(self, agent: str) -> list[AgentResponse]:
        """Get all responses from a specific agent."""
        return [r for r in self.responses if r.agent == agent]

    def get_responses_by_phase(self, phase: DiscussionPhase) -> list[AgentResponse]:
        """Get all responses from a specific phase."""
        return [r for r in self.responses if r.phase == phase]

    def get_last_response(self, agent: str = None) -> Optional[AgentResponse]:
        """Get last response, optionally filtered by agent."""
        responses = self.responses
        if agent:
            responses = [r for r in responses if r.agent == agent]
        return responses[-1] if responses else None

    def add_response(self, response: AgentResponse) -> None:
        """Add response and update totals."""
        self.responses.append(response)
        self.total_cost_cents += response.cost_cents

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "mode": self.mode.value,
            "topic": self.topic,
            "goal": self.goal,
            "constraints": self.constraints,
            "responses": [r.to_dict() for r in self.responses],
            "current_phase": self.current_phase.value,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "total_cost_cents": self.total_cost_cents,
            "cost_limit_cents": self.cost_limit_cents,
            "time_limit_seconds": self.time_limit_seconds,
            "consensus_reached": self.consensus_reached,
            "final_result": self.final_result,
            "escalation_reason": self.escalation_reason,
        }


@dataclass
class PhaseConfig:
    """
    Configuration for a single discussion phase.

    Defines which agent handles the phase, timeouts, and success criteria.

    Attributes:
        phase: Which phase this config is for
        agent: Which agent handles this phase (gemini, gpt, claude)
        prompt_key: Key for looking up prompt template
        required: Is this phase required? (skip if False and conditions met)
        timeout_seconds: Max time to wait for response
        retry_count: Number of retries on failure
        min_response_length: Minimum acceptable response length
        required_markers: Strings that must appear in response
    """
    phase: DiscussionPhase
    agent: str
    prompt_key: str = ""
    required: bool = True
    timeout_seconds: int = 90
    retry_count: int = 2
    min_response_length: int = 100
    required_markers: list[str] = field(default_factory=list)

    def __post_init__(self):
        """Set default prompt key if not provided."""
        if not self.prompt_key:
            self.prompt_key = self.phase.value


@dataclass
class DiscussionTemplate:
    """
    Template defining a discussion flow.

    Each mode has an associated template that defines:
    - Which phases to execute
    - In what order
    - With what configurations

    Attributes:
        mode: Discussion mode this template is for
        name: Human-readable template name
        description: What this template is for
        phases: Ordered list of phase configurations
        synthesizer_agent: Which agent synthesizes final result
        allow_phase_skip: Allow skipping non-required phases
        require_consensus: Require all agents to agree
    """
    mode: DiscussionMode
    name: str
    description: str
    phases: list[PhaseConfig]
    synthesizer_agent: str = "gpt"
    allow_phase_skip: bool = False
    require_consensus: bool = True

    @property
    def phase_count(self) -> int:
        """Get number of phases."""
        return len(self.phases)

    @property
    def required_phases(self) -> list[PhaseConfig]:
        """Get only required phases."""
        return [p for p in self.phases if p.required]

    def get_phase_config(self, phase: DiscussionPhase) -> Optional[PhaseConfig]:
        """Get config for specific phase."""
        for cfg in self.phases:
            if cfg.phase == phase:
                return cfg
        return None

    def get_phase_index(self, phase: DiscussionPhase) -> int:
        """Get index of phase in sequence (-1 if not found)."""
        for i, cfg in enumerate(self.phases):
            if cfg.phase == phase:
                return i
        return -1

    def get_next_phase(self, current: DiscussionPhase) -> Optional[DiscussionPhase]:
        """Get next phase after current."""
        idx = self.get_phase_index(current)
        if idx < 0 or idx >= len(self.phases) - 1:
            return None
        return self.phases[idx + 1].phase


# Type aliases for callbacks
PhaseChangeCallback = Callable[[DiscussionPhase, DiscussionPhase], None]
ResponseCallback = Callable[[AgentResponse], None]
DiscussionEndCallback = Callable[[DiscussionContext], None]
ProgressCallback = Callable[[int, int, str], None]  # current, total, message
