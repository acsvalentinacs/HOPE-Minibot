# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T20:00:00Z
# Purpose: News run metrics - mandatory artifact for every spider run (fail-closed)
# === END SIGNATURE ===
"""
News Run Metrics - Mandatory Run Artifact.

Every spider run MUST produce state/news_run.json with run metrics.
This is the SSoT for run outcome (PASS/FAIL) and statistics.

Schema: news_run_v1
- schema_version: str
- run_id: str (immutable per process)
- started_utc: ISO8601
- ended_utc: ISO8601
- mode: str (strict/lenient)
- dry_run: bool
- result: PASS | FAIL
- reason: str (if FAIL)
- sources: {ok: int, fail: int, total: int}
- items: {total: int, new: int, deduped: int}
- publish: {attempted: bool, sent: int, skipped: int}
- evidence_sha256: str (reference to spider_health.json)

Fail-closed rules:
- Missing news_run.json after run = policy violation
- result=PASS requires all mandatory checks passed
- result=FAIL requires reason field
"""
from __future__ import annotations

import json
import os
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

# SSoT paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATE_DIR = PROJECT_ROOT / "state"
NEWS_RUN_PATH = STATE_DIR / "news_run.json"


@dataclass
class SourcesMetrics:
    """Source collection metrics."""
    ok: int = 0
    fail: int = 0
    total: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {"ok": self.ok, "fail": self.fail, "total": self.total}


@dataclass
class ItemsMetrics:
    """Item processing metrics."""
    total: int = 0
    new: int = 0
    deduped: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {"total": self.total, "new": self.new, "deduped": self.deduped}


@dataclass
class PublishMetrics:
    """Publish operation metrics."""
    attempted: bool = False
    sent: int = 0
    skipped: int = 0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {"attempted": self.attempted, "sent": self.sent, "skipped": self.skipped}
        if self.error:
            d["error"] = self.error
        return d


@dataclass
class NewsRun:
    """
    News run metrics container.

    Represents a complete spider run with all metrics.
    """
    schema_version: str = "news_run_v1"
    run_id: str = ""
    started_utc: str = ""
    ended_utc: str = ""
    mode: str = "lenient"
    dry_run: bool = True
    result: str = "PENDING"  # PASS, FAIL, PENDING
    reason: str = ""
    sources: SourcesMetrics = field(default_factory=SourcesMetrics)
    items: ItemsMetrics = field(default_factory=ItemsMetrics)
    publish: PublishMetrics = field(default_factory=PublishMetrics)
    evidence_sha256: str = ""
    cmdline_sha256: str = ""

    def start(self, run_id: str, mode: str, dry_run: bool) -> None:
        """Mark run as started."""
        self.run_id = run_id
        self.mode = mode
        self.dry_run = dry_run
        self.started_utc = datetime.now(timezone.utc).isoformat()
        self.result = "PENDING"

    def finish_pass(self) -> None:
        """Mark run as PASS."""
        self.ended_utc = datetime.now(timezone.utc).isoformat()
        self.result = "PASS"
        self.reason = ""

    def finish_fail(self, reason: str) -> None:
        """Mark run as FAIL with reason."""
        self.ended_utc = datetime.now(timezone.utc).isoformat()
        self.result = "FAIL"
        self.reason = reason

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "started_utc": self.started_utc,
            "ended_utc": self.ended_utc,
            "mode": self.mode,
            "dry_run": self.dry_run,
            "result": self.result,
            "reason": self.reason,
            "sources": self.sources.to_dict(),
            "items": self.items.to_dict(),
            "publish": self.publish.to_dict(),
            "evidence_sha256": self.evidence_sha256,
            "cmdline_sha256": self.cmdline_sha256,
        }


def atomic_write_news_run(run: NewsRun, path: Optional[Path] = None) -> str:
    """
    Write news_run.json atomically.

    Args:
        run: NewsRun metrics object
        path: Optional custom path (default: state/news_run.json)

    Returns:
        SHA256 of written content

    Raises:
        OSError: On write failure (fail-closed)
    """
    if path is None:
        path = NEWS_RUN_PATH

    path.parent.mkdir(parents=True, exist_ok=True)

    data = run.to_dict()
    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    content_bytes = content.encode("utf-8")
    content_sha256 = hashlib.sha256(content_bytes).hexdigest()

    # Atomic write: temp -> fsync -> replace
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    try:
        with open(tmp_path, "wb") as f:
            f.write(content_bytes)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, path)
        return content_sha256

    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def load_news_run(path: Optional[Path] = None) -> Optional[NewsRun]:
    """
    Load news_run.json if exists.

    Args:
        path: Optional custom path

    Returns:
        NewsRun object or None if not found
    """
    if path is None:
        path = NEWS_RUN_PATH

    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))

        run = NewsRun(
            schema_version=data.get("schema_version", "news_run_v1"),
            run_id=data.get("run_id", ""),
            started_utc=data.get("started_utc", ""),
            ended_utc=data.get("ended_utc", ""),
            mode=data.get("mode", "lenient"),
            dry_run=data.get("dry_run", True),
            result=data.get("result", "PENDING"),
            reason=data.get("reason", ""),
            evidence_sha256=data.get("evidence_sha256", ""),
            cmdline_sha256=data.get("cmdline_sha256", ""),
        )

        # Load nested metrics
        sources = data.get("sources", {})
        run.sources = SourcesMetrics(
            ok=sources.get("ok", 0),
            fail=sources.get("fail", 0),
            total=sources.get("total", 0),
        )

        items = data.get("items", {})
        run.items = ItemsMetrics(
            total=items.get("total", 0),
            new=items.get("new", 0),
            deduped=items.get("deduped", 0),
        )

        publish = data.get("publish", {})
        run.publish = PublishMetrics(
            attempted=publish.get("attempted", False),
            sent=publish.get("sent", 0),
            skipped=publish.get("skipped", 0),
            error=publish.get("error"),
        )

        return run

    except Exception:
        return None


def validate_news_run(run: NewsRun) -> tuple[bool, str]:
    """
    Validate news_run meets requirements.

    Args:
        run: NewsRun to validate

    Returns:
        (is_valid, error_message)
    """
    if not run.schema_version:
        return False, "Missing schema_version"

    if not run.run_id:
        return False, "Missing run_id"

    if not run.started_utc:
        return False, "Missing started_utc"

    if run.result not in ("PASS", "FAIL", "PENDING"):
        return False, f"Invalid result: {run.result}"

    if run.result == "FAIL" and not run.reason:
        return False, "result=FAIL requires reason"

    if run.result == "PASS" and not run.ended_utc:
        return False, "result=PASS requires ended_utc"

    return True, ""
