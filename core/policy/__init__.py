# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 16:10:00 UTC
# === END SIGNATURE ===
"""
core/policy - HOPE Policy Layer (HOPE-LAW-001 + HOPE-RULE-001).

CRITICAL: This module must be imported FIRST in all entrypoints.

Usage:
    from core.policy.bootstrap import bootstrap
    bootstrap("component_name")  # MUST be first executable line
"""
from core.policy.bootstrap import bootstrap
from core.policy.loader import Policy, PolicyError, load_policy

__all__ = ["bootstrap", "Policy", "PolicyError", "load_policy"]
