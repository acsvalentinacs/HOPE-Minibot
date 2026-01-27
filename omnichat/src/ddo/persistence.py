# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T13:30:00Z
# Purpose: DDO Results Persistence - Save discussions to JSONL and Markdown
# === END SIGNATURE ===
"""
DDO Results Persistence.

Saves DDO discussion results to disk in two formats:
- JSONL: Machine-readable, for analysis and replay
- Markdown: Human-readable reports

Includes DataSanitizer for masking sensitive information.
"""

from __future__ import annotations

import json
import os
import re
import uuid
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from .types import DiscussionContext, AgentResponse

_log = logging.getLogger("ddo.persistence")


# === DATA SANITIZER ===

class DataSanitizer:
    """
    Sanitize sensitive data before persisting.

    Masks:
    - API keys (OpenAI, Google, Anthropic, etc.)
    - Tokens (GitHub, Telegram, etc.)
    - Passwords and secrets
    - Email addresses (optional)
    - IP addresses (optional)
    """

    # Patterns for sensitive data
    PATTERNS = {
        "openai_key": (r'sk-[A-Za-z0-9]{20,}', "[OPENAI_KEY_REDACTED]"),
        "anthropic_key": (r'sk-ant-[A-Za-z0-9_-]{20,}', "[ANTHROPIC_KEY_REDACTED]"),
        "google_key": (r'AIza[A-Za-z0-9_-]{30,}', "[GOOGLE_KEY_REDACTED]"),
        "github_token": (r'ghp_[A-Za-z0-9]{30,}', "[GITHUB_TOKEN_REDACTED]"),
        "telegram_token": (r'bot[0-9]{8,}:[A-Za-z0-9_-]{30,}', "[TELEGRAM_TOKEN_REDACTED]"),
        "generic_api_key": (r'api[_-]?key["\s:=]+["\']?[A-Za-z0-9_-]{16,}["\']?', "[API_KEY_REDACTED]"),
        "password": (r'password["\s:=]+["\']?[^\s"\']{8,}["\']?', "[PASSWORD_REDACTED]"),
        "bearer_token": (r'Bearer\s+[A-Za-z0-9._-]{20,}', "[BEARER_REDACTED]"),
    }

    def __init__(self, mask_emails: bool = False, mask_ips: bool = False):
        """
        Initialize sanitizer.

        Args:
            mask_emails: Also mask email addresses
            mask_ips: Also mask IP addresses
        """
        self.mask_emails = mask_emails
        self.mask_ips = mask_ips

    def sanitize(self, text: str) -> str:
        """
        Sanitize text by replacing sensitive patterns.

        Args:
            text: Input text

        Returns:
            Sanitized text
        """
        result = text

        # Apply all patterns
        for name, (pattern, replacement) in self.PATTERNS.items():
            result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

        # Optional email masking
        if self.mask_emails:
            result = re.sub(
                r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
                "[EMAIL_REDACTED]",
                result
            )

        # Optional IP masking
        if self.mask_ips:
            result = re.sub(
                r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b',
                "[IP_REDACTED]",
                result
            )

        return result

    def sanitize_dict(self, data: dict) -> dict:
        """Recursively sanitize all string values in a dict."""
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                result[key] = self.sanitize(value)
            elif isinstance(value, dict):
                result[key] = self.sanitize_dict(value)
            elif isinstance(value, list):
                result[key] = [
                    self.sanitize(v) if isinstance(v, str)
                    else self.sanitize_dict(v) if isinstance(v, dict)
                    else v
                    for v in value
                ]
            else:
                result[key] = value
        return result


# === RESULT FORMATTER ===

class DDOResultFormatter:
    """Format DDO results for output."""

    @staticmethod
    def to_jsonl_record(context: DiscussionContext) -> dict:
        """
        Convert context to JSONL record.

        Returns:
            Dict ready for JSON serialization
        """
        return {
            "id": context.id,
            "mode": context.mode.value,
            "topic": context.topic,
            "goal": context.goal,
            "constraints": context.constraints,
            "started_at": context.started_at.isoformat(),
            "ended_at": context.ended_at.isoformat() if context.ended_at else None,
            "elapsed_seconds": context.elapsed_seconds,
            "total_cost_cents": context.total_cost_cents,
            "response_count": context.response_count,
            "consensus_reached": context.consensus_reached,
            "final_phase": context.current_phase.value,
            "escalation_reason": context.escalation_reason,
            "responses": [
                {
                    "agent": r.agent,
                    "phase": r.phase.value,
                    "content": r.content,
                    "timestamp": r.timestamp.isoformat(),
                    "tokens_used": r.tokens_used,
                    "cost_cents": r.cost_cents,
                    "confidence": r.confidence,
                }
                for r in context.responses
            ],
        }

    @staticmethod
    def to_markdown(context: DiscussionContext) -> str:
        """
        Convert context to Markdown report.

        Returns:
            Markdown string
        """
        lines = [
            f"# DDO Discussion Report",
            f"",
            f"**ID:** `{context.id}`",
            f"**Mode:** {context.mode.display_name}",
            f"**Topic:** {context.topic}",
            f"**Goal:** {context.goal}",
            f"",
            f"## Summary",
            f"",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Started | {context.started_at.strftime('%Y-%m-%d %H:%M:%S')} |",
            f"| Duration | {context.elapsed_str} |",
            f"| Cost | ${context.cost_usd:.4f} |",
            f"| Responses | {context.response_count} |",
            f"| Consensus | {'✅ Yes' if context.consensus_reached else '❌ No'} |",
            f"| Final Phase | {context.current_phase.display_name} |",
            f"",
        ]

        if context.constraints:
            lines.extend([
                f"## Constraints",
                f"",
            ])
            for c in context.constraints:
                lines.append(f"- {c}")
            lines.append("")

        lines.extend([
            f"## Discussion Log",
            f"",
        ])

        for resp in context.responses:
            phase_name = resp.phase.display_name
            time_str = resp.timestamp.strftime("%H:%M:%S")

            lines.extend([
                f"### {phase_name} ({resp.agent.upper()}) [{time_str}]",
                f"",
                f"{resp.content}",
                f"",
                f"---",
                f"",
            ])

        if context.final_result:
            lines.extend([
                f"## Final Result",
                f"",
                context.final_result,
                f"",
            ])

        lines.extend([
            f"---",
            f"*Generated by DDO v1.0 at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC*",
        ])

        return "\n".join(lines)


# === PERSISTENCE ADAPTER ===

class PersistenceAdapter:
    """
    Save DDO results to disk.

    Creates:
    - omnichat/ddo_results/discussions.jsonl (append-only log)
    - omnichat/ddo_results/reports/{id}.md (individual reports)
    """

    def __init__(
        self,
        base_path: Optional[Path] = None,
        sanitize: bool = True,
    ):
        """
        Initialize adapter.

        Args:
            base_path: Base directory for results (default: omnichat/ddo_results)
            sanitize: Whether to sanitize sensitive data
        """
        if base_path is None:
            base_path = Path(__file__).parent.parent.parent / "ddo_results"

        self.base_path = base_path
        self.jsonl_path = base_path / "discussions.jsonl"
        self.reports_path = base_path / "reports"

        self.sanitizer = DataSanitizer() if sanitize else None
        self.formatter = DDOResultFormatter()

        # Ensure directories exist
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.reports_path.mkdir(parents=True, exist_ok=True)

    def save(self, context: DiscussionContext) -> tuple[Path, Path]:
        """
        Save discussion results.

        Args:
            context: Completed discussion context

        Returns:
            Tuple of (jsonl_path, markdown_path)
        """
        # Convert to records
        jsonl_record = self.formatter.to_jsonl_record(context)
        markdown_content = self.formatter.to_markdown(context)

        # Sanitize if enabled
        if self.sanitizer:
            jsonl_record = self.sanitizer.sanitize_dict(jsonl_record)
            markdown_content = self.sanitizer.sanitize(markdown_content)

        # Save JSONL (append)
        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(jsonl_record, ensure_ascii=False) + "\n")

        # Save Markdown report
        report_filename = f"{context.id}.md"
        report_path = self.reports_path / report_filename
        report_path.write_text(markdown_content, encoding="utf-8")

        _log.info(f"Saved DDO results: {self.jsonl_path}, {report_path}")

        return self.jsonl_path, report_path

    def load_all(self) -> list[dict]:
        """
        Load all discussions from JSONL.

        Returns:
            List of discussion records
        """
        if not self.jsonl_path.exists():
            return []

        records = []
        with open(self.jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))

        return records

    def get_report(self, discussion_id: str) -> Optional[str]:
        """
        Get Markdown report by ID.

        Args:
            discussion_id: Discussion ID

        Returns:
            Markdown content or None
        """
        report_path = self.reports_path / f"{discussion_id}.md"
        if report_path.exists():
            return report_path.read_text(encoding="utf-8")
        return None


# === CONVENIENCE FUNCTION ===

_default_adapter: Optional[PersistenceAdapter] = None


def save_ddo_result(context: DiscussionContext) -> tuple[Path, Path]:
    """
    Save DDO result using default adapter.

    Args:
        context: Completed discussion context

    Returns:
        Tuple of (jsonl_path, markdown_path)
    """
    global _default_adapter
    if _default_adapter is None:
        _default_adapter = PersistenceAdapter()
    return _default_adapter.save(context)
