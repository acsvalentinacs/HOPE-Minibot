# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 03:30:00 UTC
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
"""

__version__ = "1.0.0"
