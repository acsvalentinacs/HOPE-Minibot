# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T12:20:00Z
# Purpose: DDO - Dynamic Discussion Orchestrator package
# === END SIGNATURE ===
"""
DDO - Dynamic Discussion Orchestrator

Автоматическая оркестрация дискуссий между AI-агентами.

Usage:
    from src.ddo import DDOOrchestrator, DiscussionMode

    ddo = DDOOrchestrator(event_bus)
    result = await ddo.run_discussion(
        topic="Design caching system",
        mode=DiscussionMode.ARCHITECTURE,
    )
"""

from .types import (
    DiscussionPhase,
    DiscussionMode,
    AgentResponse,
    DiscussionContext,
    PhaseConfig,
    DiscussionTemplate,
)
from .orchestrator import DDOOrchestrator, run_discussion_stream
from .fsm import DiscussionFSM
from .templates import get_template, TEMPLATES
from .persistence import (
    PersistenceAdapter,
    DataSanitizer,
    DDOResultFormatter,
    save_ddo_result,
)

__all__ = [
    # Types
    "DiscussionPhase",
    "DiscussionMode",
    "AgentResponse",
    "DiscussionContext",
    "PhaseConfig",
    "DiscussionTemplate",
    # Core
    "DDOOrchestrator",
    "run_discussion_stream",
    "DiscussionFSM",
    # Templates
    "get_template",
    "TEMPLATES",
    # Persistence
    "PersistenceAdapter",
    "DataSanitizer",
    "DDOResultFormatter",
    "save_ddo_result",
]

__version__ = "1.0.0"
