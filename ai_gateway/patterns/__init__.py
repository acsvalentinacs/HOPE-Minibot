# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 17:10:00 UTC
# Purpose: AI Gateway patterns package
# === END SIGNATURE ===
"""
AI Gateway Patterns - Signal pattern detectors.

Modules:
- pump_precursor_detector: Detect pump precursor patterns
"""

from .pump_precursor_detector import (
    PumpPrecursorDetector,
    PrecursorResult,
    PrecursorSignal,
)

__all__ = [
    "PumpPrecursorDetector",
    "PrecursorResult",
    "PrecursorSignal",
]
