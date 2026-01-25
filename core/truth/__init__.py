# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T18:00:00Z
# Purpose: Core truth module exports
# === END SIGNATURE ===
"""
Core Truth Module

Single Source of Truth (SSoT) utilities for execution evidence.
"""

from core.truth.cmdline_ssot import (
    get_raw_cmdline,
    get_cmdline_sha256,
    get_python_executable_info,
    get_execution_evidence,
    print_evidence,
    verify_venv,
)

__all__ = [
    "get_raw_cmdline",
    "get_cmdline_sha256",
    "get_python_executable_info",
    "get_execution_evidence",
    "print_evidence",
    "verify_venv",
]
