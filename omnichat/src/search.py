# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-26T16:45:00Z
# Modified by: Claude (opus-4)
# Modified at: 2026-01-26T17:05:00Z
# Purpose: HOPE OMNI-CHAT Search Engine v1.1 - Full-text search with filters
# Security: Secrets redaction in search results
# Changes: Added performance metrics (Gemini recommendation)
# === END SIGNATURE ===
"""
HOPE OMNI-CHAT Search Engine v1.0

Production-grade search functionality for chat history:
- Full-text search with keyword matching
- Filters: agent, date range, case sensitivity
- Pagination for large datasets
- Match highlighting
- Caching for performance
- Fail-closed error handling

Architecture:
- SearchEngine: Core search logic, file I/O, caching
- SearchQuery: Query parameters dataclass
- SearchResult: Single result with match positions
- SearchResults: Paginated results container
"""

from __future__ import annotations

import json
import re
import time
import sys
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Optional, Generator, Callable
from functools import lru_cache
import hashlib

from .security import redact, contains_secret


# === PERFORMANCE METRICS ===

@dataclass
class SearchMetrics:
    """Performance metrics for search operations."""
    query_time_ms: float = 0.0
    lines_scanned: int = 0
    matches_found: int = 0
    cache_hit: bool = False
    file_size_kb: float = 0.0
    memory_usage_mb: float = 0.0

    def to_dict(self) -> dict:
        return {
            "query_time_ms": round(self.query_time_ms, 2),
            "lines_scanned": self.lines_scanned,
            "matches_found": self.matches_found,
            "cache_hit": self.cache_hit,
            "file_size_kb": round(self.file_size_kb, 2),
            "memory_usage_mb": round(self.memory_usage_mb, 2),
        }

    def __str__(self) -> str:
        hit = "HIT" if self.cache_hit else "MISS"
        return (
            f"[{hit}] {self.query_time_ms:.1f}ms | "
            f"{self.lines_scanned} lines | {self.matches_found} matches | "
            f"{self.file_size_kb:.1f}KB | {self.memory_usage_mb:.1f}MB mem"
        )


# === DATA STRUCTURES ===

@dataclass
class SearchQuery:
    """
    Search query parameters.

    Attributes:
        text: Keywords to search for (space-separated = AND logic)
        agents: List of agent keys to filter by (empty = all)
        date_from: Start date (inclusive)
        date_to: End date (inclusive)
        case_sensitive: Whether search is case-sensitive
        regex_mode: Treat text as regex pattern
    """
    text: str = ""
    agents: list[str] = field(default_factory=list)
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    case_sensitive: bool = False
    regex_mode: bool = False

    def is_empty(self) -> bool:
        """Check if query has any criteria."""
        return (
            not self.text.strip() and
            not self.agents and
            self.date_from is None and
            self.date_to is None
        )

    def cache_key(self) -> str:
        """Generate unique cache key for this query."""
        key_data = (
            self.text,
            tuple(sorted(self.agents)),
            str(self.date_from),
            str(self.date_to),
            self.case_sensitive,
            self.regex_mode,
        )
        return hashlib.md5(str(key_data).encode()).hexdigest()


@dataclass
class SearchResult:
    """
    Single search result.

    Attributes:
        role: Message role (user, gemini, gpt, claude, system)
        content: Message content (may be redacted)
        timestamp: Message timestamp
        match_positions: List of (start, end) tuples for highlighting
        line_number: Line number in JSONL file for navigation
        tokens_used: Token count (if available)
        cost_cents: Cost in cents (if available)
    """
    role: str
    content: str
    timestamp: datetime
    match_positions: list[tuple[int, int]] = field(default_factory=list)
    line_number: int = 0
    tokens_used: int = 0
    cost_cents: float = 0.0

    @property
    def role_display(self) -> str:
        """Get display name for role."""
        role_names = {
            "user": "👤 You",
            "gemini": "💜 Gemini",
            "gpt": "💛 GPT",
            "claude": "💙 Claude",
            "system": "⚙️ System",
        }
        return role_names.get(self.role, f"❓ {self.role}")

    @property
    def time_str(self) -> str:
        """Get formatted time string."""
        return self.timestamp.strftime("%Y-%m-%d %H:%M:%S")

    @property
    def short_time_str(self) -> str:
        """Get short time string (time only)."""
        return self.timestamp.strftime("%H:%M:%S")

    @property
    def date_str(self) -> str:
        """Get date string."""
        return self.timestamp.strftime("%Y-%m-%d")

    def get_highlighted_content(self, max_length: int = 200) -> str:
        """
        Get content with match positions marked for highlighting.
        Truncates to max_length with ellipsis.
        """
        content = self.content

        # Truncate if needed
        if len(content) > max_length:
            # Try to show first match
            if self.match_positions:
                first_match_start = self.match_positions[0][0]
                if first_match_start > max_length // 2:
                    # Start from before the match
                    start = max(0, first_match_start - max_length // 4)
                    content = "..." + content[start:start + max_length] + "..."
                else:
                    content = content[:max_length] + "..."
            else:
                content = content[:max_length] + "..."

        return content


@dataclass
class SearchResults:
    """
    Paginated search results container.

    Attributes:
        items: List of SearchResult objects for current page
        total_count: Total number of matches across all pages
        page: Current page number (1-indexed)
        page_size: Number of results per page
        query: The query that produced these results
    """
    items: list[SearchResult] = field(default_factory=list)
    total_count: int = 0
    page: int = 1
    page_size: int = 50
    query: Optional[SearchQuery] = None

    @property
    def total_pages(self) -> int:
        """Calculate total number of pages."""
        if self.total_count == 0:
            return 1
        return (self.total_count + self.page_size - 1) // self.page_size

    @property
    def has_next_page(self) -> bool:
        """Check if there's a next page."""
        return self.page < self.total_pages

    @property
    def has_prev_page(self) -> bool:
        """Check if there's a previous page."""
        return self.page > 1

    @property
    def start_index(self) -> int:
        """Get 1-indexed start position for current page."""
        return (self.page - 1) * self.page_size + 1

    @property
    def end_index(self) -> int:
        """Get 1-indexed end position for current page."""
        return min(self.page * self.page_size, self.total_count)

    @property
    def status_text(self) -> str:
        """Get status text like 'Showing 1-50 of 123'."""
        if self.total_count == 0:
            return "Ничего не найдено"
        return f"Показано {self.start_index}-{self.end_index} из {self.total_count}"


# === SEARCH ENGINE ===

class SearchEngine:
    """
    Production-grade search engine for OMNI-CHAT history.

    Features:
    - Full-text search with keyword matching (AND logic)
    - Multiple filters: agent, date range
    - Case-insensitive by default
    - Optional regex mode
    - Pagination for large datasets
    - Result caching for performance
    - Fail-closed error handling
    - Secrets redaction in results

    Usage:
        engine = SearchEngine(history_path)
        query = SearchQuery(text="error", agents=["claude"])
        results = engine.search(query, page=1)
    """

    # Default page size
    DEFAULT_PAGE_SIZE = 50

    # Maximum results to return (safety limit)
    MAX_RESULTS = 10000

    # Cache size for query results
    CACHE_SIZE = 32

    def __init__(
        self,
        history_path: Path,
        page_size: int = DEFAULT_PAGE_SIZE,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ):
        """
        Initialize search engine.

        Args:
            history_path: Path to chat_history.jsonl file
            page_size: Number of results per page
            on_progress: Optional callback for progress updates (current, total)
        """
        self.history_path = history_path
        self.page_size = page_size
        self.on_progress = on_progress

        # Result cache: query_hash -> (results, timestamp)
        self._cache: dict[str, tuple[list[SearchResult], datetime]] = {}
        self._cache_file_mtime: Optional[float] = None

        # Performance metrics (last query)
        self.last_metrics: SearchMetrics = SearchMetrics()

    def _invalidate_cache_if_needed(self) -> None:
        """Invalidate cache if history file has been modified."""
        if not self.history_path.exists():
            self._cache.clear()
            return

        current_mtime = self.history_path.stat().st_mtime
        if self._cache_file_mtime != current_mtime:
            self._cache.clear()
            self._cache_file_mtime = current_mtime

    def _load_messages(self) -> Generator[tuple[int, dict], None, None]:
        """
        Load messages from JSONL file as generator.

        Yields:
            Tuple of (line_number, message_dict)

        Raises:
            FileNotFoundError: If history file doesn't exist
        """
        if not self.history_path.exists():
            return

        with open(self.history_path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                    yield line_num, msg
                except json.JSONDecodeError:
                    # Skip malformed lines (fail-closed: don't crash)
                    continue

    def _parse_message(self, line_num: int, msg: dict) -> Optional[SearchResult]:
        """
        Parse message dict into SearchResult.

        Args:
            line_num: Line number in JSONL
            msg: Message dictionary

        Returns:
            SearchResult or None if parsing fails
        """
        try:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            timestamp_str = msg.get("timestamp", "")
            tokens_used = msg.get("tokens_used", 0)
            cost_cents = msg.get("cost_cents", 0.0)

            # Parse timestamp
            try:
                # Handle ISO format with optional microseconds
                if "." in timestamp_str:
                    timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                else:
                    timestamp = datetime.fromisoformat(timestamp_str.replace("Z", ""))
            except (ValueError, AttributeError):
                timestamp = datetime.utcnow()

            # SECURITY: Redact any secrets that might be in history
            # (This is defense-in-depth; secrets should already be redacted on save)
            if contains_secret(content):
                content = redact(content)

            return SearchResult(
                role=role,
                content=content,
                timestamp=timestamp,
                line_number=line_num,
                tokens_used=tokens_used,
                cost_cents=cost_cents,
            )
        except Exception:
            # Fail-closed: skip malformed messages
            return None

    def _matches_query(self, result: SearchResult, query: SearchQuery) -> bool:
        """
        Check if a message matches the search query.

        Args:
            result: SearchResult to check
            query: SearchQuery with criteria

        Returns:
            True if message matches all criteria
        """
        # Filter by agent
        if query.agents:
            if result.role not in query.agents:
                return False

        # Filter by date range
        msg_date = result.timestamp.date()

        if query.date_from and msg_date < query.date_from:
            return False

        if query.date_to and msg_date > query.date_to:
            return False

        # Filter by text (if specified)
        if query.text.strip():
            content = result.content
            search_text = query.text

            if not query.case_sensitive:
                content = content.lower()
                search_text = search_text.lower()

            if query.regex_mode:
                # Regex search
                try:
                    flags = 0 if query.case_sensitive else re.IGNORECASE
                    if not re.search(search_text, result.content, flags):
                        return False
                except re.error:
                    # Invalid regex - fail-closed, don't match
                    return False
            else:
                # Keyword search (AND logic for multiple words)
                keywords = search_text.split()
                for keyword in keywords:
                    if keyword not in content:
                        return False

        return True

    def _find_match_positions(
        self,
        content: str,
        query: SearchQuery
    ) -> list[tuple[int, int]]:
        """
        Find positions of matches in content for highlighting.

        Args:
            content: Message content
            query: SearchQuery with search text

        Returns:
            List of (start, end) tuples
        """
        positions = []

        if not query.text.strip():
            return positions

        search_content = content
        search_text = query.text

        if not query.case_sensitive:
            search_content = content.lower()
            search_text = search_text.lower()

        if query.regex_mode:
            try:
                flags = 0 if query.case_sensitive else re.IGNORECASE
                for match in re.finditer(search_text, content, flags):
                    positions.append((match.start(), match.end()))
            except re.error:
                pass
        else:
            # Find all keyword positions
            keywords = search_text.split()
            for keyword in keywords:
                start = 0
                while True:
                    pos = search_content.find(keyword, start)
                    if pos == -1:
                        break
                    positions.append((pos, pos + len(keyword)))
                    start = pos + 1

        # Sort by position
        positions.sort(key=lambda x: x[0])

        return positions

    def search(
        self,
        query: SearchQuery,
        page: int = 1,
    ) -> SearchResults:
        """
        Execute search query and return paginated results.

        Args:
            query: SearchQuery with search criteria
            page: Page number (1-indexed)

        Returns:
            SearchResults with matching messages

        Note:
            Results are cached by query hash. Cache is invalidated
            when the history file is modified.
        """
        # Start timing
        start_time = time.perf_counter()

        # Initialize metrics
        metrics = SearchMetrics()
        if self.history_path.exists():
            metrics.file_size_kb = self.history_path.stat().st_size / 1024

        # Validate page number
        page = max(1, page)

        # Check cache
        self._invalidate_cache_if_needed()
        cache_key = query.cache_key()

        if cache_key in self._cache:
            all_results, _ = self._cache[cache_key]
            metrics.cache_hit = True
        else:
            # Execute search
            all_results, lines_scanned = self._execute_search(query)
            metrics.lines_scanned = lines_scanned
            metrics.cache_hit = False

            # Cache results (limit cache size)
            if len(self._cache) >= self.CACHE_SIZE:
                # Remove oldest entry
                oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][1])
                del self._cache[oldest_key]

            self._cache[cache_key] = (all_results, datetime.utcnow())

        # Record metrics
        metrics.matches_found = len(all_results)
        metrics.query_time_ms = (time.perf_counter() - start_time) * 1000
        metrics.memory_usage_mb = sys.getsizeof(all_results) / (1024 * 1024)
        self.last_metrics = metrics

        # Paginate
        total_count = len(all_results)
        start_idx = (page - 1) * self.page_size
        end_idx = start_idx + self.page_size
        page_results = all_results[start_idx:end_idx]

        return SearchResults(
            items=page_results,
            total_count=total_count,
            page=page,
            page_size=self.page_size,
            query=query,
        )

    def _execute_search(self, query: SearchQuery) -> tuple[list[SearchResult], int]:
        """
        Execute search and return all matching results.

        Args:
            query: SearchQuery with criteria

        Returns:
            Tuple of (results list, lines scanned count)
        """
        results = []
        total_lines = 0
        lines_scanned = 0

        # Count total lines for progress
        if self.on_progress and self.history_path.exists():
            with open(self.history_path, "r", encoding="utf-8") as f:
                total_lines = sum(1 for _ in f)

        # Search
        for line_num, msg in self._load_messages():
            lines_scanned = line_num

            # Progress callback
            if self.on_progress and total_lines > 0:
                self.on_progress(line_num, total_lines)

            result = self._parse_message(line_num, msg)
            if result is None:
                continue

            if self._matches_query(result, query):
                # Find match positions for highlighting
                result.match_positions = self._find_match_positions(
                    result.content, query
                )
                results.append(result)

                # Safety limit
                if len(results) >= self.MAX_RESULTS:
                    break

        # Sort by timestamp (newest first)
        results.sort(key=lambda r: r.timestamp, reverse=True)

        return results, lines_scanned

    def get_stats(self) -> dict:
        """
        Get statistics about the history file.

        Returns:
            Dict with total_messages, date_range, agents, file_size
        """
        stats = {
            "total_messages": 0,
            "date_from": None,
            "date_to": None,
            "agents": set(),
            "file_size_kb": 0,
        }

        if not self.history_path.exists():
            return stats

        stats["file_size_kb"] = self.history_path.stat().st_size / 1024

        for line_num, msg in self._load_messages():
            stats["total_messages"] = line_num

            role = msg.get("role", "unknown")
            stats["agents"].add(role)

            timestamp_str = msg.get("timestamp", "")
            try:
                timestamp = datetime.fromisoformat(timestamp_str.replace("Z", ""))
                msg_date = timestamp.date()

                if stats["date_from"] is None or msg_date < stats["date_from"]:
                    stats["date_from"] = msg_date
                if stats["date_to"] is None or msg_date > stats["date_to"]:
                    stats["date_to"] = msg_date
            except (ValueError, AttributeError):
                pass

        stats["agents"] = list(stats["agents"])

        return stats

    def clear_cache(self) -> None:
        """Clear the search result cache."""
        self._cache.clear()

    def get_metrics(self) -> SearchMetrics:
        """Get metrics from last search operation."""
        return self.last_metrics

    def get_metrics_summary(self) -> str:
        """Get human-readable metrics summary."""
        return str(self.last_metrics)


# === UTILITY FUNCTIONS ===

def highlight_text(text: str, positions: list[tuple[int, int]], marker: str = "**") -> str:
    """
    Add highlight markers around match positions.

    Args:
        text: Original text
        positions: List of (start, end) tuples
        marker: Marker to wrap matches with (e.g., "**" for bold)

    Returns:
        Text with markers around matches
    """
    if not positions:
        return text

    # Build result with markers
    result = []
    last_end = 0

    for start, end in positions:
        # Add text before match
        result.append(text[last_end:start])
        # Add marked match
        result.append(marker)
        result.append(text[start:end])
        result.append(marker)
        last_end = end

    # Add remaining text
    result.append(text[last_end:])

    return "".join(result)


def parse_date_input(date_str: str) -> Optional[date]:
    """
    Parse date from user input (various formats).

    Supported formats:
    - YYYY-MM-DD
    - DD.MM.YYYY
    - DD/MM/YYYY
    - today, yesterday

    Returns:
        date object or None if parsing fails
    """
    date_str = date_str.strip().lower()

    if not date_str:
        return None

    # Special keywords
    today = date.today()
    if date_str == "today" or date_str == "сегодня":
        return today
    if date_str == "yesterday" or date_str == "вчера":
        from datetime import timedelta
        return today - timedelta(days=1)

    # Try various formats
    formats = [
        "%Y-%m-%d",    # ISO: 2026-01-26
        "%d.%m.%Y",    # EU: 26.01.2026
        "%d/%m/%Y",    # EU alt: 26/01/2026
        "%m/%d/%Y",    # US: 01/26/2026
        "%d-%m-%Y",    # EU dash: 26-01-2026
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue

    return None
