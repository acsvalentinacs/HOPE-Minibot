# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T21:00:00Z
# Purpose: DDO discussion export with sanitization and atomic writes
# Security: Fail-closed, atomic file operations
# === END SIGNATURE ===
"""
DDO Discussion Exporter.

Exports DDO discussions to Markdown and JSON formats with:
- Content sanitization (removes control chars, normalizes whitespace)
- Atomic file writes (temp -> fsync -> replace)
- Configurable output directory

Usage:
    from omnichat.src.ddo.exporter import DDOExporter, ExportResult

    exporter = DDOExporter()
    result = exporter.export_discussion(discussion, format="md")
    if result.success:
        print(f"Saved to: {result.path}")
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Union

logger = logging.getLogger(__name__)

# Default export directory
DEFAULT_EXPORT_DIR = Path(r"C:\Users\kirillDev\Desktop\TradingBot\minibot\omnichat\ddo_results")


@dataclass
class ExportResult:
    """Result of export operation."""
    success: bool
    path: Optional[Path] = None
    error: Optional[str] = None
    format: Optional[str] = None  # "md" | "json"
    size_bytes: int = 0


@dataclass
class DDOMessage:
    """Single message in DDO discussion."""
    role: str  # "user" | "claude" | "gpt" | "moderator"
    content: str
    timestamp: float
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class DDODiscussion:
    """Complete DDO discussion for export."""
    topic: str
    messages: List[DDOMessage]
    started_at: float
    ended_at: Optional[float] = None
    participants: Optional[List[str]] = None
    consensus: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


def sanitize_text(text: str) -> str:
    """
    Sanitize text for safe export.

    Removes:
    - Control characters (except newline, tab)
    - Null bytes
    - Excessive whitespace

    Normalizes:
    - Line endings to LF
    - Multiple spaces to single space
    """
    if not text:
        return ""

    # Remove null bytes
    text = text.replace("\x00", "")

    # Remove control chars except \n \t \r
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

    # Normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Collapse multiple spaces (but preserve indentation)
    lines = []
    for line in text.split("\n"):
        # Preserve leading whitespace, collapse internal spaces
        stripped = line.lstrip()
        indent = line[:len(line) - len(stripped)]
        collapsed = re.sub(r"  +", " ", stripped)
        lines.append(indent + collapsed)

    text = "\n".join(lines)

    # Remove excessive blank lines (max 2 consecutive)
    text = re.sub(r"\n{4,}", "\n\n\n", text)

    return text.strip()


def sanitize_filename(name: str, max_length: int = 100) -> str:
    """
    Sanitize string for use as filename.

    Removes/replaces:
    - Invalid filename characters
    - Leading/trailing spaces and dots
    - Reserved Windows names
    """
    if not name:
        return "untitled"

    # Remove invalid chars
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)

    # Replace spaces with underscores
    name = re.sub(r"\s+", "_", name)

    # Remove leading/trailing dots and spaces
    name = name.strip(". ")

    # Truncate
    if len(name) > max_length:
        name = name[:max_length]

    # Check for reserved Windows names
    reserved = {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4",
                "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2",
                "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"}
    base = name.upper().split(".")[0]
    if base in reserved:
        name = f"_{name}"

    return name or "untitled"


def _atomic_write(path: Path, content: str) -> None:
    """
    Atomic file write: temp -> fsync -> replace.

    Ensures file is either fully written or not changed.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        # Cleanup temp file on error
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass
        raise


class DDOExporter:
    """
    Exports DDO discussions to various formats.

    Features:
    - Atomic file writes
    - Content sanitization
    - Markdown and JSON formats
    """

    def __init__(self, export_dir: Optional[Path] = None):
        """
        Initialize exporter.

        Args:
            export_dir: Output directory (default: omnichat/ddo_results)
        """
        self.export_dir = export_dir or DEFAULT_EXPORT_DIR
        self.export_dir.mkdir(parents=True, exist_ok=True)

    def _generate_filename(self, discussion: DDODiscussion, ext: str) -> str:
        """Generate unique filename for discussion."""
        # Use topic + timestamp
        topic_part = sanitize_filename(discussion.topic, max_length=50)
        ts = datetime.fromtimestamp(discussion.started_at).strftime("%Y%m%d_%H%M%S")
        return f"ddo_{ts}_{topic_part}.{ext}"

    def _to_markdown(self, discussion: DDODiscussion) -> str:
        """Convert discussion to Markdown format."""
        lines = []

        # Header
        lines.append(f"# DDO Discussion: {sanitize_text(discussion.topic)}")
        lines.append("")

        # Metadata
        started = datetime.fromtimestamp(discussion.started_at).isoformat()
        lines.append(f"**Started:** {started}")

        if discussion.ended_at:
            ended = datetime.fromtimestamp(discussion.ended_at).isoformat()
            duration = discussion.ended_at - discussion.started_at
            lines.append(f"**Ended:** {ended}")
            lines.append(f"**Duration:** {duration:.0f} seconds")

        if discussion.participants:
            lines.append(f"**Participants:** {', '.join(discussion.participants)}")

        lines.append("")
        lines.append("---")
        lines.append("")

        # Messages
        lines.append("## Discussion")
        lines.append("")

        for msg in discussion.messages:
            role = msg.role.upper()
            ts = datetime.fromtimestamp(msg.timestamp).strftime("%H:%M:%S")
            content = sanitize_text(msg.content)

            lines.append(f"### [{ts}] {role}")
            lines.append("")
            lines.append(content)
            lines.append("")

        # Consensus
        if discussion.consensus:
            lines.append("---")
            lines.append("")
            lines.append("## Consensus")
            lines.append("")
            lines.append(sanitize_text(discussion.consensus))
            lines.append("")

        # Footer
        lines.append("---")
        lines.append("")
        lines.append(f"*Exported at {datetime.now().isoformat()} by HOPE DDO Exporter*")

        return "\n".join(lines)

    def _to_json(self, discussion: DDODiscussion) -> str:
        """Convert discussion to JSON format."""
        data = {
            "topic": sanitize_text(discussion.topic),
            "started_at": discussion.started_at,
            "started_at_iso": datetime.fromtimestamp(discussion.started_at).isoformat(),
            "ended_at": discussion.ended_at,
            "ended_at_iso": datetime.fromtimestamp(discussion.ended_at).isoformat() if discussion.ended_at else None,
            "participants": discussion.participants or [],
            "consensus": sanitize_text(discussion.consensus) if discussion.consensus else None,
            "message_count": len(discussion.messages),
            "messages": [
                {
                    "role": msg.role,
                    "content": sanitize_text(msg.content),
                    "timestamp": msg.timestamp,
                    "timestamp_iso": datetime.fromtimestamp(msg.timestamp).isoformat(),
                    "metadata": msg.metadata,
                }
                for msg in discussion.messages
            ],
            "metadata": discussion.metadata,
            "_export_meta": {
                "exported_at": time.time(),
                "exported_at_iso": datetime.now().isoformat(),
                "exporter": "HOPE DDO Exporter v1.0",
            },
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    def export_discussion(
        self,
        discussion: DDODiscussion,
        format: str = "md",
        filename: Optional[str] = None,
    ) -> ExportResult:
        """
        Export discussion to file.

        Args:
            discussion: DDO discussion to export
            format: Output format ("md" or "json")
            filename: Optional custom filename (without extension)

        Returns:
            ExportResult with path and status
        """
        format = format.lower()
        if format not in ("md", "json"):
            return ExportResult(success=False, error=f"Unsupported format: {format}")

        try:
            # Generate content
            if format == "md":
                content = self._to_markdown(discussion)
            else:
                content = self._to_json(discussion)

            # Generate filename
            if filename:
                safe_name = sanitize_filename(filename, max_length=100)
                file_name = f"{safe_name}.{format}"
            else:
                file_name = self._generate_filename(discussion, format)

            # Write atomically
            path = self.export_dir / file_name
            _atomic_write(path, content)

            size = path.stat().st_size
            logger.info("Exported DDO discussion to %s (%d bytes)", path, size)

            return ExportResult(
                success=True,
                path=path,
                format=format,
                size_bytes=size,
            )

        except Exception as e:
            logger.error("Export failed: %s", e)
            return ExportResult(success=False, error=str(e))

    def export_both(
        self,
        discussion: DDODiscussion,
        filename: Optional[str] = None,
    ) -> Dict[str, ExportResult]:
        """
        Export discussion to both MD and JSON formats.

        Returns:
            Dict with "md" and "json" keys containing ExportResults
        """
        return {
            "md": self.export_discussion(discussion, format="md", filename=filename),
            "json": self.export_discussion(discussion, format="json", filename=filename),
        }


# Convenience function for quick export
def export_ddo(
    topic: str,
    messages: List[Dict[str, Any]],
    format: str = "md",
    export_dir: Optional[Path] = None,
) -> ExportResult:
    """
    Quick export function for DDO discussions.

    Args:
        topic: Discussion topic
        messages: List of message dicts with role, content, timestamp
        format: Output format ("md" or "json")
        export_dir: Optional export directory

    Returns:
        ExportResult
    """
    # Convert dicts to DDOMessage
    msg_objects = []
    for msg in messages:
        msg_objects.append(DDOMessage(
            role=msg.get("role", "unknown"),
            content=msg.get("content", ""),
            timestamp=msg.get("timestamp", time.time()),
            metadata=msg.get("metadata"),
        ))

    # Create discussion
    discussion = DDODiscussion(
        topic=topic,
        messages=msg_objects,
        started_at=msg_objects[0].timestamp if msg_objects else time.time(),
        ended_at=msg_objects[-1].timestamp if msg_objects else None,
    )

    # Export
    exporter = DDOExporter(export_dir)
    return exporter.export_discussion(discussion, format=format)


if __name__ == "__main__":
    # Quick test
    test_messages = [
        {"role": "moderator", "content": "Starting discussion on market analysis", "timestamp": time.time() - 120},
        {"role": "claude", "content": "Based on technical indicators, BTC shows bullish divergence.", "timestamp": time.time() - 60},
        {"role": "gpt", "content": "I agree. RSI is oversold at 28.", "timestamp": time.time()},
    ]

    result = export_ddo(
        topic="BTC Market Analysis Test",
        messages=test_messages,
        format="md",
    )
    print(f"Export result: {result}")
