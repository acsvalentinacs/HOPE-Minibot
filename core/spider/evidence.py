# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T19:30:00Z
# Modified by: Claude (opus-4)
# Modified at (UTC): 2026-01-25T20:00:00Z
# Purpose: Spider evidence generation and validation (SSoT, fail-closed) v1.4
# === END SIGNATURE ===
"""
Spider Evidence Module v1.4

SSoT channel: state/health/spider_health.json

This module generates and validates evidence for spider runs.
All PASS claims require valid evidence in spider_health.json.

Evidence includes:
- schema_version: "spider_health_v1" (REQUIRED)
- cmdline_ssot: { source, raw, sha256 } (REQUIRED)
- run_id: unique identifier with __cmd= binding
- policy_egress: allowlist path and sha256
- evidence_line: machine-parseable KPI string
- sources_result: ok/total/failed counts with reason_codes

Fail-closed rules:
- Missing allowlist -> FAIL, no network I/O
- Health write failure -> FAIL, no network I/O
- Missing evidence -> "PASS 6/7" forbidden
- UNKNOWN_ERROR in enforced mode -> UNCLASSIFIED_ERROR_FORBIDDEN
"""

# Schema version constant
SCHEMA_VERSION = "spider_health_v1"

import hashlib
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional

from core.io.atomic import atomic_write_json
from core.spider.run_id import generate_run_id, validate_run_id, get_current_run_id
from core.spider.reason_codes import (
    ReasonCode,
    Stage,
    SourceFailure,
    is_valid_reason_code,
)


class EvidenceError(Exception):
    """Raised when evidence cannot be generated or validated."""
    pass


@dataclass
class CmdlineSsot:
    """Command line SSoT evidence (GetCommandLineW on Windows)."""
    source: str  # "GetCommandLineW" or "/proc/self/cmdline"
    raw: str  # Raw command line
    sha256: str  # SHA256 of raw command line

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "raw": self.raw,
            "sha256": self.sha256,
        }


@dataclass
class PolicyEgress:
    """Policy egress evidence."""
    allowlist_path: str
    allowlist_sha256: str
    policy_mode: str  # "enforced" or "lenient"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowlist_path": self.allowlist_path,
            "allowlist_sha256": self.allowlist_sha256,
            "policy_mode": self.policy_mode,
        }


@dataclass
class SourcesResult:
    """Sources collection result."""
    ok: int
    total: int
    failed: List[SourceFailure] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "total": self.total,
            "failed": [f.to_dict() for f in self.failed],
        }


@dataclass
class SpiderEvidence:
    """
    Complete evidence for a spider run.

    This is the contract for state/health/spider_health.json.
    schema_version and cmdline_ssot are REQUIRED fields.
    """
    ts_utc: str
    run_id: str
    policy_egress: PolicyEgress
    cmdline_ssot: CmdlineSsot
    schema_version: str = SCHEMA_VERSION
    sources_result: Optional[SourcesResult] = None
    result: str = "PENDING"  # "PASS", "FAIL", "PENDING"
    fail_closed: bool = True
    evidence_line: str = ""

    def __post_init__(self):
        """Generate evidence_line after initialization."""
        if not self.evidence_line:
            self.evidence_line = self._generate_evidence_line()

    def _generate_evidence_line(self) -> str:
        """Generate machine-parseable evidence line for KPI validation."""
        return (
            f"POLICY_EGRESS "
            f"allowlist_path={self.policy_egress.allowlist_path} "
            f"allowlist_sha256={self.policy_egress.allowlist_sha256} "
            f"cmdline_sha256={self.cmdline_ssot.sha256} "
            f"ts_utc={self.ts_utc} "
            f"run_id={self.run_id}"
        )

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "ts_utc": self.ts_utc,
            "run_id": self.run_id,
            "cmdline_ssot": self.cmdline_ssot.to_dict(),
            "policy_egress": self.policy_egress.to_dict(),
            "result": self.result,
            "fail_closed": self.fail_closed,
            "evidence_line": self.evidence_line,
        }
        if self.sources_result is not None:
            result["sources_result"] = self.sources_result.to_dict()
        return result


# Module state
_health_path: Optional[Path] = None
_current_evidence: Optional[SpiderEvidence] = None
_evidence_written: bool = False


def get_health_path(project_root: Optional[Path] = None) -> Path:
    """Get path to spider_health.json."""
    global _health_path
    if _health_path is not None:
        return _health_path

    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent.parent

    _health_path = project_root / "state" / "health" / "spider_health.json"
    return _health_path


def compute_file_sha256(path: Path) -> str:
    """
    Compute SHA256 of file contents.

    Args:
        path: File path

    Returns:
        Hex digest

    Raises:
        EvidenceError: If file cannot be read
    """
    try:
        content = path.read_bytes()
        return hashlib.sha256(content).hexdigest()
    except Exception as e:
        raise EvidenceError(f"Cannot compute SHA256 for {path}: {e}")


def generate_policy_evidence(
    allowlist_path: Path,
    policy_mode: str = "enforced",
) -> PolicyEgress:
    """
    Generate policy egress evidence.

    Args:
        allowlist_path: Path to allowlist file
        policy_mode: "enforced" or "lenient"

    Returns:
        PolicyEgress with path and sha256

    Raises:
        EvidenceError: If allowlist missing/unreadable
    """
    if not allowlist_path.exists():
        raise EvidenceError(
            f"POLICY_ALLOWLIST_MISSING: {allowlist_path}"
        )

    try:
        sha256 = compute_file_sha256(allowlist_path)
    except Exception as e:
        raise EvidenceError(
            f"POLICY_ALLOWLIST_HASH_FAIL: {allowlist_path}: {e}"
        )

    # Use relative path if possible
    try:
        project_root = Path(__file__).resolve().parent.parent.parent
        rel_path = allowlist_path.resolve().relative_to(project_root)
        path_str = str(rel_path).replace("\\", "/")
    except ValueError:
        path_str = str(allowlist_path)

    return PolicyEgress(
        allowlist_path=path_str,
        allowlist_sha256=sha256,
        policy_mode=policy_mode,
    )


def _get_cmdline_ssot() -> CmdlineSsot:
    """
    Get command line SSoT evidence.

    Uses GetCommandLineW on Windows (the only reliable source).
    Uses /proc/self/cmdline on Linux.

    Returns:
        CmdlineSsot with source, raw, and sha256

    Raises:
        EvidenceError: If cmdline cannot be retrieved
    """
    try:
        from core.truth.cmdline_ssot import get_raw_cmdline, get_cmdline_sha256

        raw = get_raw_cmdline()
        sha256 = get_cmdline_sha256()

        if sys.platform == "win32":
            source = "GetCommandLineW"
        else:
            source = "/proc/self/cmdline"

        return CmdlineSsot(source=source, raw=raw, sha256=sha256)

    except Exception as e:
        raise EvidenceError(f"CMDLINE_SSOT_FAIL: {e}")


def create_initial_evidence(
    allowlist_path: Path,
    policy_mode: str = "enforced",
) -> SpiderEvidence:
    """
    Create initial evidence before network I/O.

    MUST be called before any network requests.
    MUST be written to disk before network I/O.

    Args:
        allowlist_path: Path to allowlist file
        policy_mode: "enforced" or "lenient"

    Returns:
        SpiderEvidence ready to write

    Raises:
        EvidenceError: If evidence cannot be created
    """
    global _current_evidence

    # Generate cmdline SSoT FIRST (fail-closed if unavailable)
    cmdline_ssot = _get_cmdline_ssot()

    # Generate run_id (once per process, now includes __cmd= binding)
    run_id = generate_run_id()

    # Generate timestamp
    ts_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Generate policy evidence
    policy_egress = generate_policy_evidence(allowlist_path, policy_mode)

    evidence = SpiderEvidence(
        schema_version=SCHEMA_VERSION,
        ts_utc=ts_utc,
        run_id=run_id,
        cmdline_ssot=cmdline_ssot,
        policy_egress=policy_egress,
        result="PENDING",
        fail_closed=True,
    )

    _current_evidence = evidence
    return evidence


def write_evidence(
    evidence: SpiderEvidence,
    project_root: Optional[Path] = None,
) -> Path:
    """
    Write evidence to spider_health.json atomically.

    MUST succeed before any network I/O.
    Fail-closed: raises on any error.

    Args:
        evidence: SpiderEvidence to write
        project_root: Project root for path resolution

    Returns:
        Path to written file

    Raises:
        EvidenceError: If write fails
    """
    global _evidence_written

    health_path = get_health_path(project_root)

    try:
        atomic_write_json(health_path, evidence.to_dict())
        _evidence_written = True
        return health_path
    except Exception as e:
        raise EvidenceError(
            f"POLICY_EVIDENCE_WRITE_FAIL: {health_path}: {e}"
        )


def update_evidence_result(
    sources_result: SourcesResult,
    result: str,
    project_root: Optional[Path] = None,
) -> Path:
    """
    Update evidence with sources result and final status.

    Called after collection completes.

    Args:
        sources_result: Collection results
        result: "PASS" or "FAIL"
        project_root: Project root for path resolution

    Returns:
        Path to written file

    Raises:
        EvidenceError: If update fails
    """
    global _current_evidence

    if _current_evidence is None:
        raise EvidenceError("No initial evidence created - call create_initial_evidence first")

    _current_evidence.sources_result = sources_result
    _current_evidence.result = result

    return write_evidence(_current_evidence, project_root)


def validate_evidence(evidence_dict: Dict[str, Any], enforced: bool = True) -> bool:
    """
    Validate evidence structure and content.

    Args:
        evidence_dict: Parsed spider_health.json
        enforced: If True, UNKNOWN_ERROR in failed[] is forbidden

    Returns:
        True if valid

    Raises:
        EvidenceError: If validation fails
    """
    # Required fields (schema_version and cmdline_ssot are now REQUIRED)
    required = ["schema_version", "ts_utc", "run_id", "cmdline_ssot", "policy_egress", "result", "evidence_line"]
    for fld in required:
        if fld not in evidence_dict:
            raise EvidenceError(f"Missing required field: {fld}")

    # Validate schema_version
    schema_version = evidence_dict.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise EvidenceError(f"Invalid schema_version: {schema_version}, expected {SCHEMA_VERSION}")

    # Validate cmdline_ssot
    cmdline = evidence_dict.get("cmdline_ssot", {})
    if not cmdline.get("source"):
        raise EvidenceError("Missing cmdline_ssot.source")
    if not cmdline.get("sha256"):
        raise EvidenceError("Missing cmdline_ssot.sha256")
    # cmdline.raw can be empty string (though unusual), but sha256 is required

    # Validate run_id
    run_id = evidence_dict["run_id"]
    if not validate_run_id(run_id):
        raise EvidenceError(f"Invalid run_id format: {run_id}")

    # Validate policy_egress
    policy = evidence_dict.get("policy_egress", {})
    if not policy.get("allowlist_path"):
        raise EvidenceError("Missing policy_egress.allowlist_path")
    if not policy.get("allowlist_sha256"):
        raise EvidenceError("Missing policy_egress.allowlist_sha256")

    # Validate evidence_line format
    evidence_line = evidence_dict.get("evidence_line", "")
    if not evidence_line.startswith("POLICY_EGRESS "):
        raise EvidenceError(f"Invalid evidence_line format: {evidence_line[:50]}")

    # Validate sources_result if present
    sources = evidence_dict.get("sources_result")
    if sources:
        if "ok" not in sources or "total" not in sources:
            raise EvidenceError("Missing sources_result.ok or sources_result.total")

        # Validate failed[] reason_codes
        for failure in sources.get("failed", []):
            reason_code = failure.get("reason_code", "")
            if not is_valid_reason_code(reason_code):
                raise EvidenceError(f"Invalid reason_code: {reason_code}")

            # In enforced mode, UNKNOWN_ERROR is forbidden (UNCLASSIFIED_ERROR_FORBIDDEN)
            if enforced and reason_code == "UNKNOWN_ERROR":
                raise EvidenceError("UNCLASSIFIED_ERROR_FORBIDDEN: UNKNOWN_ERROR not allowed in enforced mode")

    return True


def load_and_validate_evidence(
    project_root: Optional[Path] = None,
    enforced: bool = True,
) -> Dict[str, Any]:
    """
    Load and validate spider_health.json.

    Args:
        project_root: Project root for path resolution
        enforced: If True, use strict validation

    Returns:
        Validated evidence dict

    Raises:
        EvidenceError: If load or validation fails
    """
    import json

    health_path = get_health_path(project_root)

    if not health_path.exists():
        raise EvidenceError(f"Evidence file not found: {health_path}")

    try:
        content = health_path.read_text(encoding="utf-8")
        evidence = json.loads(content)
    except Exception as e:
        raise EvidenceError(f"Cannot read evidence: {health_path}: {e}")

    validate_evidence(evidence, enforced=enforced)

    return evidence


def is_evidence_valid_for_pass(
    ok: int,
    total: int,
    project_root: Optional[Path] = None,
) -> bool:
    """
    Check if current evidence allows claiming PASS with given counts.

    Args:
        ok: Number of successful sources
        total: Total sources attempted
        project_root: Project root for path resolution

    Returns:
        True if PASS claim is valid

    Raises:
        EvidenceError: If evidence is invalid or insufficient
    """
    try:
        evidence = load_and_validate_evidence(project_root, enforced=True)
    except EvidenceError:
        return False

    # Check schema_version
    if evidence.get("schema_version") != SCHEMA_VERSION:
        return False

    # Check cmdline_ssot.sha256 present
    cmdline = evidence.get("cmdline_ssot", {})
    if not cmdline.get("sha256"):
        return False

    # Check sources_result matches
    sources = evidence.get("sources_result", {})
    if sources.get("ok") != ok or sources.get("total") != total:
        return False

    # Check all required fields present
    if not evidence.get("policy_egress", {}).get("allowlist_sha256"):
        return False

    if not evidence.get("evidence_line"):
        return False

    return True


def format_sources_result_line(sources: SourcesResult) -> str:
    """
    Format sources result as machine-parseable line.

    Format: SOURCES_RESULT ok=6 total=7 failed=<source_id> reason=<reason_code>
    """
    parts = [f"SOURCES_RESULT ok={sources.ok} total={sources.total}"]

    for f in sources.failed:
        parts.append(f"failed={f.source_id} reason={f.reason_code.value}")

    return " ".join(parts)
