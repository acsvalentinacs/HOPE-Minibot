# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 03:30:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-29 16:25:00 UTC
# Change: Added scheduler, diagnostics, telegram_panel exports
# Purpose: AI-Gateway package - Separate from Trading Core
# === END SIGNATURE ===
"""
AI-Gateway: Intelligence Layer for HOPE Trading Bot.

CRITICAL ARCHITECTURE RULE:
- AI-Gateway runs as SEPARATE process from Trading Core
- Core NEVER imports AI libraries (anthropic, torch, sklearn)
- Core ONLY reads artifacts from state/ai/*.jsonl
- AI-Gateway writes artifacts with checksums and TTL
- If AI-Gateway fails, Core continues with base strategy

Module Status Indicators:
- HEALTHY (green): Working normally
- WARNING (yellow): Needs attention
- ERROR (red): Failed, needs repair
- DISABLED (gray): Turned off by user

Components:
- contracts: Pydantic models for artifacts
- jsonl_writer: Atomic JSONL persistence
- status_manager: Module health tracking
- scheduler: Lifecycle management
- diagnostics: Health checks
- telegram_panel: Telegram UI
- server: FastAPI HTTP API
"""

__version__ = "2.0.0"

# Core exports
from .contracts import (
    ModuleStatus,
    SentimentLevel,
    MarketRegime,
    HealthStatus,
    AnomalySeverity,
    BaseArtifact,
    SentimentArtifact,
    RegimeArtifact,
    StrategyDoctorArtifact,
    AnomalyScannerArtifact,
    create_artifact_id,
)

from .status_manager import (
    StatusManager,
    get_status_manager,
)

from .jsonl_writer import (
    JSONLWriter,
    get_writer,
    write_artifact,
    read_latest,
    read_valid,
)

from .scheduler import (
    ModuleScheduler,
    get_scheduler,
)

from .diagnostics import (
    GatewayDiagnostics,
    run_health_check,
)

from .base_module import (
    BaseAIModule,
    ModuleConfig,
    ModuleState,
)

__all__ = [
    # Version
    "__version__",
    # Contracts
    "ModuleStatus",
    "SentimentLevel",
    "MarketRegime",
    "HealthStatus",
    "AnomalySeverity",
    "BaseArtifact",
    "SentimentArtifact",
    "RegimeArtifact",
    "StrategyDoctorArtifact",
    "AnomalyScannerArtifact",
    "create_artifact_id",
    # Status
    "StatusManager",
    "get_status_manager",
    # JSONL
    "JSONLWriter",
    "get_writer",
    "write_artifact",
    "read_latest",
    "read_valid",
    # Scheduler
    "ModuleScheduler",
    "get_scheduler",
    # Diagnostics
    "GatewayDiagnostics",
    "run_health_check",
    # Base module
    "BaseAIModule",
    "ModuleConfig",
    "ModuleState",
]
