# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T14:00:00Z
# Purpose: News collector with egress policy enforcement (stdlib-only)
# === END SIGNATURE ===
"""
News Collector Module

Orchestrates fetching from all enabled sources using egress-policy-enforced HTTP.
Supports STRICT (fail-fast) and LENIENT (skip-and-continue) modes.
"""

import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple

from core.net.http_client import http_get, EgressDeniedError, EgressError
from core.spider.sources import (
    SourceConfig,
    SourceType,
    load_sources,
    get_enabled_sources,
)
from core.spider.parser import (
    RSSItem,
    parse_rss_xml,
    parse_binance_announcements,
    ParseError,
)
from core.spider.dedup import DedupStore


class CollectorMode(Enum):
    """
    Collector operating mode.

    STRICT: Any error (fetch, parse, policy) causes immediate FATAL STOP.
            Use for TESTNET/LIVE where consistency is critical.

    LENIENT: Errors are logged but collection continues with other sources.
             Use for DRY/dev where partial results are acceptable.
    """
    STRICT = "strict"
    LENIENT = "lenient"


@dataclass
class SourceResult:
    """Result of fetching single source."""
    source_id: str
    success: bool
    items_count: int
    new_items_count: int
    error: Optional[str] = None
    latency_ms: int = 0


@dataclass
class CollectorResult:
    """
    Complete collection run result.

    Attributes:
        mode: Operating mode used
        started_utc: Run start timestamp
        finished_utc: Run finish timestamp
        sources_attempted: Number of sources attempted
        sources_success: Number of sources successfully fetched
        sources_failed: Number of sources that failed
        total_items: Total items parsed across all sources
        new_items: New items (not seen before)
        duplicate_items: Items skipped as duplicates
        source_results: Per-source result details
        fatal_error: If STRICT mode, the error that caused stop
    """
    mode: CollectorMode
    started_utc: datetime
    finished_utc: Optional[datetime] = None
    sources_attempted: int = 0
    sources_success: int = 0
    sources_failed: int = 0
    total_items: int = 0
    new_items: int = 0
    duplicate_items: int = 0
    source_results: List[SourceResult] = field(default_factory=list)
    fatal_error: Optional[str] = None

    def is_success(self) -> bool:
        """Check if run completed successfully."""
        return self.fatal_error is None and self.sources_failed == 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "mode": self.mode.value,
            "started_utc": self.started_utc.isoformat(),
            "finished_utc": self.finished_utc.isoformat() if self.finished_utc else None,
            "sources_attempted": self.sources_attempted,
            "sources_success": self.sources_success,
            "sources_failed": self.sources_failed,
            "total_items": self.total_items,
            "new_items": self.new_items,
            "duplicate_items": self.duplicate_items,
            "fatal_error": self.fatal_error,
            "source_results": [
                {
                    "source_id": r.source_id,
                    "success": r.success,
                    "items_count": r.items_count,
                    "new_items_count": r.new_items_count,
                    "error": r.error,
                    "latency_ms": r.latency_ms,
                }
                for r in self.source_results
            ],
        }


class NewsCollector:
    """
    News collector with egress policy enforcement.

    All HTTP requests go through core.net.http_client.http_get,
    which enforces AllowList.txt and logs to audit.
    """

    def __init__(
        self,
        mode: CollectorMode = CollectorMode.STRICT,
        dedup_store: Optional[DedupStore] = None,
        output_path: Optional[Path] = None,
        project_root: Optional[Path] = None,
    ):
        """
        Initialize collector.

        Args:
            mode: Operating mode (STRICT or LENIENT)
            dedup_store: Deduplication store (creates default if None)
            output_path: Path to write collected items JSONL
            project_root: Project root for resolving paths
        """
        self._mode = mode
        self._project_root = project_root or Path(__file__).resolve().parent.parent.parent

        if dedup_store is None:
            dedup_store = DedupStore(project_root=self._project_root)
        self._dedup = dedup_store

        if output_path is None:
            output_path = self._project_root / "state" / "news_items.jsonl"
        self._output_path = output_path

    def collect(
        self,
        sources: Optional[List[SourceConfig]] = None,
        dry_run: bool = False,
    ) -> CollectorResult:
        """
        Run collection from all enabled sources.

        Args:
            sources: Source list (loads enabled from registry if None)
            dry_run: If True, fetch but don't persist items

        Returns:
            CollectorResult with run statistics

        Raises:
            In STRICT mode, any error propagates immediately.
        """
        result = CollectorResult(
            mode=self._mode,
            started_utc=datetime.now(timezone.utc),
        )

        # Load sources if not provided
        if sources is None:
            try:
                # Use strict_mode matching collector mode
                sources = get_enabled_sources(
                    strict_mode=(self._mode == CollectorMode.STRICT)
                )
            except Exception as e:
                result.fatal_error = f"Source loading failed: {e}"
                result.finished_utc = datetime.now(timezone.utc)
                if self._mode == CollectorMode.STRICT:
                    raise
                return result

        # Sort by priority (lower = higher priority)
        sources = sorted(sources, key=lambda s: s.priority)

        for source in sources:
            result.sources_attempted += 1

            try:
                src_result = self._fetch_source(source, dry_run)
                result.source_results.append(src_result)

                if src_result.success:
                    result.sources_success += 1
                    result.total_items += src_result.items_count
                    result.new_items += src_result.new_items_count
                    result.duplicate_items += (
                        src_result.items_count - src_result.new_items_count
                    )
                else:
                    result.sources_failed += 1
                    if self._mode == CollectorMode.STRICT:
                        result.fatal_error = (
                            f"Source {source.id} failed: {src_result.error}"
                        )
                        break

            except Exception as e:
                error_msg = f"{type(e).__name__}: {e}"
                result.source_results.append(SourceResult(
                    source_id=source.id,
                    success=False,
                    items_count=0,
                    new_items_count=0,
                    error=error_msg,
                ))
                result.sources_failed += 1

                if self._mode == CollectorMode.STRICT:
                    result.fatal_error = f"Source {source.id} failed: {error_msg}"
                    raise

        result.finished_utc = datetime.now(timezone.utc)
        return result

    def _fetch_source(
        self,
        source: SourceConfig,
        dry_run: bool,
    ) -> SourceResult:
        """
        Fetch and parse single source.

        Args:
            source: Source configuration
            dry_run: Skip persistence if True

        Returns:
            SourceResult with fetch statistics
        """
        start_ms = time.monotonic_ns() // 1_000_000

        try:
            # Fetch via egress-controlled http_get
            status, body, final_url = http_get(
                source.url,
                timeout_sec=30,
                process=f"spider:{source.id}",
            )

            latency_ms = (time.monotonic_ns() // 1_000_000) - start_ms

            if status != 200:
                return SourceResult(
                    source_id=source.id,
                    success=False,
                    items_count=0,
                    new_items_count=0,
                    error=f"HTTP {status}",
                    latency_ms=latency_ms,
                )

            # Parse based on source type
            if source.source_type == SourceType.RSS:
                items = parse_rss_xml(body, source.id)
            elif source.source_type == SourceType.BINANCE_ANN:
                items = parse_binance_announcements(body, source.id)
            elif source.source_type == SourceType.JSON_API:
                # For generic JSON API, use binance parser as fallback
                items = parse_binance_announcements(body, source.id)
            else:
                return SourceResult(
                    source_id=source.id,
                    success=False,
                    items_count=0,
                    new_items_count=0,
                    error=f"Unknown source type: {source.source_type}",
                    latency_ms=latency_ms,
                )

            # Deduplicate and optionally persist
            new_count = 0
            for item in items:
                is_new = self._dedup.add(item.item_id, source.id, item.link)
                if is_new:
                    new_count += 1
                    if not dry_run:
                        self._persist_item(item)

            return SourceResult(
                source_id=source.id,
                success=True,
                items_count=len(items),
                new_items_count=new_count,
                latency_ms=latency_ms,
            )

        except EgressDeniedError as e:
            latency_ms = (time.monotonic_ns() // 1_000_000) - start_ms
            return SourceResult(
                source_id=source.id,
                success=False,
                items_count=0,
                new_items_count=0,
                error=f"Egress DENIED: {e.reason.value}",
                latency_ms=latency_ms,
            )

        except EgressError as e:
            latency_ms = (time.monotonic_ns() // 1_000_000) - start_ms
            return SourceResult(
                source_id=source.id,
                success=False,
                items_count=0,
                new_items_count=0,
                error=f"Egress error: {e.reason.value}",
                latency_ms=latency_ms,
            )

        except ParseError as e:
            latency_ms = (time.monotonic_ns() // 1_000_000) - start_ms
            return SourceResult(
                source_id=source.id,
                success=False,
                items_count=0,
                new_items_count=0,
                error=f"Parse error: {e}",
                latency_ms=latency_ms,
            )

    def _persist_item(self, item: RSSItem) -> None:
        """
        Append item to output JSONL file.

        Uses atomic append with file locking.
        """
        self._output_path.parent.mkdir(parents=True, exist_ok=True)

        record = {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            **item.to_dict(),
        }

        # Atomic append
        with open(self._output_path, "a", encoding="utf-8") as f:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                finally:
                    try:
                        f.seek(0)
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                    except Exception:
                        pass
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def run_collection(
    mode: str = "strict",
    dry_run: bool = False,
) -> CollectorResult:
    """
    Convenience function to run collection.

    Args:
        mode: "strict" or "lenient"
        dry_run: Skip persistence if True

    Returns:
        CollectorResult
    """
    collector_mode = CollectorMode(mode.lower())
    collector = NewsCollector(mode=collector_mode)
    return collector.collect(dry_run=dry_run)
