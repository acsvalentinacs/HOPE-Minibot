# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 10:00:00 UTC
# Purpose: Self-Improving Loop module - AI that learns from its own trades
# === END SIGNATURE ===
"""
Self-Improving Loop Module.

Architecture:
    Signal → AI Predict → Trade → Result
         ↑                          ↓
         └──── Auto-Retrain ←───────┘

Features:
- Auto-retrain after N trades (default: 100)
- A/B testing: old model vs new model
- Version control for models
- Rollback on 5 consecutive losses
- Fail-closed on insufficient data
"""

from .loop import SelfImprovingLoop
from .outcome_tracker import OutcomeTracker
from .model_registry import ModelRegistry
from .ab_tester import ABTester

__all__ = [
    "SelfImprovingLoop",
    "OutcomeTracker",
    "ModelRegistry",
    "ABTester",
]
