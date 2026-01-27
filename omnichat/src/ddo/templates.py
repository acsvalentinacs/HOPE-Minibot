# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T12:40:00Z
# Purpose: DDO Discussion Templates - Predefined discussion flows
# === END SIGNATURE ===
"""
DDO Discussion Templates.

Predefined templates for common discussion types.
Each template defines the sequence of phases and their configurations.

Available Templates:
- ARCHITECTURE: Full design cycle (Architect → Analyze → Implement → Reviews)
- CODE_REVIEW: Review existing code (Security → Code Review → Refine)
- BRAINSTORM: Generate ideas (Architect → Analyze → Implement)
- QUICK: Fast 3-phase (Architect → Implement → Synthesize)
- TROUBLESHOOT: Debug problems (Analyze → Implement → Test)
"""

from __future__ import annotations

from .types import (
    DiscussionMode,
    DiscussionPhase,
    DiscussionTemplate,
    PhaseConfig,
)


# ==================== ARCHITECTURE TEMPLATE ====================
# Full design cycle for new features/systems
# Total phases: 6 + synthesize
# Expected time: 5-10 minutes
# Expected cost: $0.20-0.50

ARCHITECTURE_TEMPLATE = DiscussionTemplate(
    mode=DiscussionMode.ARCHITECTURE,
    name="Full Architecture Design",
    description=(
        "Complete design and implementation cycle. "
        "Gemini designs architecture, GPT analyzes and creates TZ, "
        "Claude implements, then both review the code."
    ),
    phases=[
        PhaseConfig(
            phase=DiscussionPhase.ARCHITECT,
            agent="gemini",
            prompt_key="architect",
            required=True,
            timeout_seconds=120,
            retry_count=2,
            min_response_length=500,
            required_markers=["Вариант", "Рекоменд"],
        ),
        PhaseConfig(
            phase=DiscussionPhase.ANALYZE,
            agent="gpt",
            prompt_key="analyze",
            required=True,
            timeout_seconds=120,
            retry_count=2,
            min_response_length=500,
            required_markers=["Анализ", "ТЗ", "Выбор"],
        ),
        PhaseConfig(
            phase=DiscussionPhase.IMPLEMENT,
            agent="claude",
            prompt_key="implement",
            required=True,
            timeout_seconds=180,
            retry_count=2,
            min_response_length=300,
            required_markers=["```python", "def "],
        ),
        PhaseConfig(
            phase=DiscussionPhase.SECURITY_REVIEW,
            agent="gemini",
            prompt_key="security_review",
            required=True,
            timeout_seconds=120,
            retry_count=2,
            min_response_length=300,
            required_markers=["Security", "Finding", "Verdict"],
        ),
        PhaseConfig(
            phase=DiscussionPhase.CODE_REVIEW,
            agent="gpt",
            prompt_key="code_review",
            required=True,
            timeout_seconds=120,
            retry_count=2,
            min_response_length=300,
            required_markers=["Review", "Issue", "Verdict"],
        ),
        PhaseConfig(
            phase=DiscussionPhase.REFINE,
            agent="claude",
            prompt_key="refine",
            required=True,
            timeout_seconds=180,
            retry_count=2,
            min_response_length=300,
            required_markers=["```python", "исправлен"],
        ),
    ],
    synthesizer_agent="gpt",
    allow_phase_skip=False,
    require_consensus=True,
)


# ==================== CODE REVIEW TEMPLATE ====================
# Review existing code
# Total phases: 3 + synthesize
# Expected time: 3-5 minutes
# Expected cost: $0.10-0.20

CODE_REVIEW_TEMPLATE = DiscussionTemplate(
    mode=DiscussionMode.CODE_REVIEW,
    name="Code Review",
    description=(
        "Review existing code for security and quality issues. "
        "Gemini does security audit, GPT does code review, "
        "Claude fixes issues if needed."
    ),
    phases=[
        PhaseConfig(
            phase=DiscussionPhase.SECURITY_REVIEW,
            agent="gemini",
            prompt_key="security_review",
            required=True,
            timeout_seconds=120,
            retry_count=2,
            min_response_length=200,
            required_markers=["Security", "Verdict"],
        ),
        PhaseConfig(
            phase=DiscussionPhase.CODE_REVIEW,
            agent="gpt",
            prompt_key="code_review",
            required=True,
            timeout_seconds=120,
            retry_count=2,
            min_response_length=200,
            required_markers=["Review", "Verdict"],
        ),
        PhaseConfig(
            phase=DiscussionPhase.REFINE,
            agent="claude",
            prompt_key="refine",
            required=False,  # Only if issues found
            timeout_seconds=180,
            retry_count=2,
            min_response_length=100,
            required_markers=[],
        ),
    ],
    synthesizer_agent="gpt",
    allow_phase_skip=True,
    require_consensus=True,
)


# ==================== BRAINSTORM TEMPLATE ====================
# Generate and evaluate ideas
# Total phases: 3 + synthesize
# Expected time: 3-5 minutes
# Expected cost: $0.10-0.15

BRAINSTORM_TEMPLATE = DiscussionTemplate(
    mode=DiscussionMode.BRAINSTORM,
    name="Brainstorm Ideas",
    description=(
        "Generate ideas collaboratively. "
        "Gemini proposes approaches, GPT analyzes feasibility, "
        "Claude provides implementation perspective."
    ),
    phases=[
        PhaseConfig(
            phase=DiscussionPhase.ARCHITECT,
            agent="gemini",
            prompt_key="architect",
            required=True,
            timeout_seconds=90,
            retry_count=2,
            min_response_length=300,
            required_markers=["Вариант", "идея"],
        ),
        PhaseConfig(
            phase=DiscussionPhase.ANALYZE,
            agent="gpt",
            prompt_key="analyze",
            required=True,
            timeout_seconds=90,
            retry_count=2,
            min_response_length=300,
            required_markers=["Анализ"],
        ),
        PhaseConfig(
            phase=DiscussionPhase.IMPLEMENT,
            agent="claude",
            prompt_key="implement",
            required=True,
            timeout_seconds=120,
            retry_count=2,
            min_response_length=200,
            required_markers=[],
        ),
    ],
    synthesizer_agent="gpt",
    allow_phase_skip=False,
    require_consensus=False,
)


# ==================== QUICK TEMPLATE ====================
# Fast 3-phase for simple tasks
# Total phases: 3
# Expected time: 2-3 minutes
# Expected cost: $0.05-0.10

QUICK_TEMPLATE = DiscussionTemplate(
    mode=DiscussionMode.QUICK,
    name="Quick Discussion",
    description=(
        "Fast discussion for simple tasks. "
        "Gemini designs, Claude implements, GPT synthesizes."
    ),
    phases=[
        PhaseConfig(
            phase=DiscussionPhase.ARCHITECT,
            agent="gemini",
            prompt_key="architect",
            required=True,
            timeout_seconds=60,
            retry_count=1,
            min_response_length=200,
            required_markers=[],
        ),
        PhaseConfig(
            phase=DiscussionPhase.IMPLEMENT,
            agent="claude",
            prompt_key="implement",
            required=True,
            timeout_seconds=120,
            retry_count=1,
            min_response_length=100,
            required_markers=["```"],
        ),
    ],
    synthesizer_agent="gpt",
    allow_phase_skip=False,
    require_consensus=False,
)


# ==================== TROUBLESHOOT TEMPLATE ====================
# Debug and fix problems
# Total phases: 4
# Expected time: 4-6 minutes
# Expected cost: $0.15-0.25

TROUBLESHOOT_TEMPLATE = DiscussionTemplate(
    mode=DiscussionMode.TROUBLESHOOT,
    name="Troubleshooting",
    description=(
        "Diagnose and fix problems. "
        "GPT analyzes the issue, Claude proposes fix, "
        "Gemini validates security, GPT verifies solution."
    ),
    phases=[
        PhaseConfig(
            phase=DiscussionPhase.ANALYZE,
            agent="gpt",
            prompt_key="analyze",
            required=True,
            timeout_seconds=90,
            retry_count=2,
            min_response_length=200,
            required_markers=["Анализ", "проблем"],
        ),
        PhaseConfig(
            phase=DiscussionPhase.IMPLEMENT,
            agent="claude",
            prompt_key="implement",
            required=True,
            timeout_seconds=120,
            retry_count=2,
            min_response_length=200,
            required_markers=["```python"],
        ),
        PhaseConfig(
            phase=DiscussionPhase.SECURITY_REVIEW,
            agent="gemini",
            prompt_key="security_review",
            required=True,
            timeout_seconds=90,
            retry_count=1,
            min_response_length=100,
            required_markers=["Verdict"],
        ),
        PhaseConfig(
            phase=DiscussionPhase.CODE_REVIEW,
            agent="gpt",
            prompt_key="code_review",
            required=True,
            timeout_seconds=90,
            retry_count=1,
            min_response_length=100,
            required_markers=["Verdict"],
        ),
    ],
    synthesizer_agent="gpt",
    allow_phase_skip=False,
    require_consensus=True,
)


# ==================== DEBATE TEMPLATE ====================
# Argue for/against a position
# Total phases: 4
# Expected time: 4-5 minutes
# Expected cost: $0.15-0.20

DEBATE_TEMPLATE = DiscussionTemplate(
    mode=DiscussionMode.DEBATE,
    name="Debate",
    description=(
        "Structured debate on a topic. "
        "Agents take positions and argue with evidence."
    ),
    phases=[
        PhaseConfig(
            phase=DiscussionPhase.ARCHITECT,
            agent="gemini",
            prompt_key="architect",
            required=True,
            timeout_seconds=90,
            retry_count=1,
            min_response_length=300,
            required_markers=["позиция", "аргумент"],
        ),
        PhaseConfig(
            phase=DiscussionPhase.ANALYZE,
            agent="gpt",
            prompt_key="analyze",
            required=True,
            timeout_seconds=90,
            retry_count=1,
            min_response_length=300,
            required_markers=["контр", "аргумент"],
        ),
        PhaseConfig(
            phase=DiscussionPhase.IMPLEMENT,
            agent="claude",
            prompt_key="implement",
            required=True,
            timeout_seconds=90,
            retry_count=1,
            min_response_length=300,
            required_markers=["вывод", "позиция"],
        ),
    ],
    synthesizer_agent="gpt",
    allow_phase_skip=False,
    require_consensus=False,
)


# ==================== TEMPLATE REGISTRY ====================

TEMPLATES: dict[DiscussionMode, DiscussionTemplate] = {
    DiscussionMode.ARCHITECTURE: ARCHITECTURE_TEMPLATE,
    DiscussionMode.CODE_REVIEW: CODE_REVIEW_TEMPLATE,
    DiscussionMode.BRAINSTORM: BRAINSTORM_TEMPLATE,
    DiscussionMode.QUICK: QUICK_TEMPLATE,
    DiscussionMode.TROUBLESHOOT: TROUBLESHOOT_TEMPLATE,
    DiscussionMode.DEBATE: DEBATE_TEMPLATE,
}


def get_template(mode: DiscussionMode) -> DiscussionTemplate:
    """
    Get template for a discussion mode.

    Args:
        mode: Discussion mode

    Returns:
        DiscussionTemplate for the mode

    Raises:
        ValueError: If no template exists for mode
    """
    if mode not in TEMPLATES:
        raise ValueError(f"No template for mode: {mode}")
    return TEMPLATES[mode]


def list_templates() -> list[dict]:
    """
    List all available templates with their info.

    Returns:
        List of template info dicts
    """
    return [
        {
            "mode": t.mode.value,
            "name": t.name,
            "description": t.description,
            "phases": t.phase_count,
            "requires_consensus": t.require_consensus,
        }
        for t in TEMPLATES.values()
    ]


def get_template_summary(mode: DiscussionMode) -> str:
    """
    Get human-readable summary of a template.

    Args:
        mode: Discussion mode

    Returns:
        Summary string
    """
    t = get_template(mode)
    phases_str = " → ".join(
        f"{p.agent.upper()}({p.phase.value})"
        for p in t.phases
    )
    return (
        f"📋 {t.name}\n"
        f"   {t.description}\n"
        f"   Phases: {phases_str}\n"
        f"   Consensus: {'Required' if t.require_consensus else 'Not required'}"
    )
