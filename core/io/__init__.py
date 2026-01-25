# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T18:00:00Z
# Purpose: Core I/O module exports
# === END SIGNATURE ===
"""
Core I/O Module

Exports atomic file operations with fail-closed semantics.
"""

from core.io.atomic import (
    compute_sha256,
    compute_sha256_str,
    atomic_write_text,
    atomic_write_json,
    format_sha256_jsonl_line,
    parse_sha256_jsonl_line,
    atomic_append_sha256_jsonl,
    read_sha256_jsonl,
    AtomicFileLock,
)

__all__ = [
    "compute_sha256",
    "compute_sha256_str",
    "atomic_write_text",
    "atomic_write_json",
    "format_sha256_jsonl_line",
    "parse_sha256_jsonl_line",
    "atomic_append_sha256_jsonl",
    "read_sha256_jsonl",
    "AtomicFileLock",
]
