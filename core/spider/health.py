# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T15:00:00Z
# Purpose: Source health tracking for News Spider (HOTFIX v1.1)
# === END SIGNATURE ===
"""
Source Health Tracking Module

Tracks health status of each news source:
- HEALTHY: Last fetch succeeded with items
- DEGRADED: 1-2 consecutive failures
- DEAD: 3+ consecutive failures

Persists state to JSON for monitoring and alerting.
"""

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, Optional, List


class HealthStatus(Enum):
    """Source health status."""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DEAD = "DEAD"
    UNKNOWN = "UNKNOWN"


class ErrorCategory(Enum):
    """Error categorization for proper handling."""
    OK = "ok"
    CLIENT_BUG = "client_bug"      # HTTP 4xx - our bug, MUST fix
    SERVER_ERROR = "server_error"  # HTTP 5xx - transient, retry later
    PARSE_FAIL = "parse_fail"      # 200 but 0 items or parse error
    POLICY_DENY = "policy_deny"    # AllowList block
    NETWORK = "network"            # Timeout, connection error
    UNKNOWN = "unknown"


@dataclass
class SourceHealth:
    """Health state for a single source."""
    source_id: str
    status: str = "UNKNOWN"  # HealthStatus value
    last_success_utc: Optional[str] = None
    last_failure_utc: Optional[str] = None
    consecutive_failures: int = 0
    last_error: Optional[str] = None
    last_error_category: Optional[str] = None
    last_items_count: int = 0
    total_successes: int = 0
    total_failures: int = 0


def categorize_error(error_str: Optional[str]) -> ErrorCategory:
    """
    Categorize error string into ErrorCategory.

    Args:
        error_str: Error message from SourceResult

    Returns:
        ErrorCategory for proper handling
    """
    if not error_str:
        return ErrorCategory.OK

    error_lower = error_str.lower()

    # HTTP 4xx = client bug (our fault)
    if "http 4" in error_lower or "http 400" in error_str or "http 403" in error_str or "http 404" in error_str:
        return ErrorCategory.CLIENT_BUG

    # HTTP 5xx = server error (transient)
    if "http 5" in error_lower:
        return ErrorCategory.SERVER_ERROR

    # Parse errors
    if "parse" in error_lower:
        return ErrorCategory.PARSE_FAIL

    # Policy denials
    if "egress denied" in error_lower or "not_in_allowlist" in error_lower:
        return ErrorCategory.POLICY_DENY

    # Network errors
    if "timeout" in error_lower or "network" in error_lower or "connection" in error_lower:
        return ErrorCategory.NETWORK

    return ErrorCategory.UNKNOWN


class HealthTracker:
    """
    Tracks and persists source health status.

    Usage:
        tracker = HealthTracker()
        tracker.record_success("coindesk_rss", items_count=25)
        tracker.record_failure("binance_new_listings", "HTTP 400")
        summary = tracker.get_summary()
    """

    def __init__(
        self,
        state_path: Optional[Path] = None,
        project_root: Optional[Path] = None,
    ):
        """
        Initialize health tracker.

        Args:
            state_path: Path to health state JSON
            project_root: Project root for path resolution
        """
        if project_root is None:
            project_root = Path(__file__).resolve().parent.parent.parent

        if state_path is None:
            state_path = project_root / "state" / "source_health.json"

        self._path = state_path
        self._health: Dict[str, SourceHealth] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Load state from disk if not already loaded."""
        if self._loaded:
            return

        self._path.parent.mkdir(parents=True, exist_ok=True)

        if self._path.exists():
            try:
                content = self._path.read_text(encoding="utf-8")
                data = json.loads(content)
                for source_id, state in data.get("sources", {}).items():
                    self._health[source_id] = SourceHealth(
                        source_id=source_id,
                        **{k: v for k, v in state.items() if k != "source_id"}
                    )
            except Exception:
                pass  # Start fresh on error

        self._loaded = True

    def _save(self) -> None:
        """Persist state to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "last_updated_utc": datetime.now(timezone.utc).isoformat(),
            "sources": {
                sid: asdict(h) for sid, h in self._health.items()
            }
        }

        # Atomic write
        tmp = self._path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
        except Exception:
            if tmp.exists():
                tmp.unlink()
            raise

    def _get_or_create(self, source_id: str) -> SourceHealth:
        """Get or create health record for source."""
        self._ensure_loaded()
        if source_id not in self._health:
            self._health[source_id] = SourceHealth(source_id=source_id)
        return self._health[source_id]

    def record_success(
        self,
        source_id: str,
        items_count: int,
    ) -> None:
        """
        Record successful fetch.

        Args:
            source_id: Source identifier
            items_count: Number of items fetched
        """
        h = self._get_or_create(source_id)
        h.status = HealthStatus.HEALTHY.value
        h.last_success_utc = datetime.now(timezone.utc).isoformat()
        h.consecutive_failures = 0
        h.last_items_count = items_count
        h.total_successes += 1
        h.last_error = None
        h.last_error_category = None
        self._save()

    def record_failure(
        self,
        source_id: str,
        error: str,
    ) -> ErrorCategory:
        """
        Record failed fetch.

        Args:
            source_id: Source identifier
            error: Error message

        Returns:
            ErrorCategory for the error
        """
        h = self._get_or_create(source_id)
        category = categorize_error(error)

        h.last_failure_utc = datetime.now(timezone.utc).isoformat()
        h.consecutive_failures += 1
        h.last_error = error
        h.last_error_category = category.value
        h.total_failures += 1

        # Update status based on consecutive failures
        if h.consecutive_failures >= 3:
            h.status = HealthStatus.DEAD.value
        elif h.consecutive_failures >= 1:
            h.status = HealthStatus.DEGRADED.value

        self._save()
        return category

    def get_health(self, source_id: str) -> Optional[SourceHealth]:
        """Get health record for source."""
        self._ensure_loaded()
        return self._health.get(source_id)

    def get_all_health(self) -> Dict[str, SourceHealth]:
        """Get all health records."""
        self._ensure_loaded()
        return dict(self._health)

    def get_summary(self) -> Dict[str, int]:
        """
        Get summary counts by status.

        Returns:
            Dict with counts: healthy, degraded, dead, unknown
        """
        self._ensure_loaded()
        summary = {
            "healthy": 0,
            "degraded": 0,
            "dead": 0,
            "unknown": 0,
            "total": len(self._health),
        }

        for h in self._health.values():
            status = h.status.lower()
            if status in summary:
                summary[status] += 1

        return summary

    def get_critical_issues(self) -> List[Dict]:
        """
        Get list of sources with critical issues (CLIENT_BUG or DEAD).

        Returns:
            List of dicts with source_id, status, error, category
        """
        self._ensure_loaded()
        issues = []

        for h in self._health.values():
            is_critical = (
                h.status == HealthStatus.DEAD.value or
                h.last_error_category == ErrorCategory.CLIENT_BUG.value
            )
            if is_critical:
                issues.append({
                    "source_id": h.source_id,
                    "status": h.status,
                    "error": h.last_error,
                    "category": h.last_error_category,
                    "consecutive_failures": h.consecutive_failures,
                })

        return issues
