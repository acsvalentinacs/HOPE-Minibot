# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T18:00:00Z
# Purpose: Spider Policy Engine - strict/lenient evaluation with error classification
# === END SIGNATURE ===
"""
Spider Policy Engine

Evaluates collection results against policy rules.

Error Categories:
- internal_bug: Our code error (parser crash, assertion fail)
- security_violation: Egress policy violation (allowlist deny, redirect to forbidden host)
- storage_failure: Cannot write state (disk full, permission denied)
- external_transient: Remote server error (5xx, timeout, DNS fail) - retryable
- external_permanent: Remote client error (4xx) - our bug or config error

Policy Modes:
- STRICT: Any error = FAIL. For production where consistency is critical.
- LENIENT: Only internal_bug/security_violation/storage_failure = FAIL.
           external_transient OK if min_success met and mandatory sources OK.

Fail-closed: Unknown errors = internal_bug (worst case assumption).
"""

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, Any


class ErrorCategory(str, Enum):
    """Error categorization for policy decisions."""
    OK = "ok"
    INTERNAL_BUG = "internal_bug"
    SECURITY_VIOLATION = "security_violation"
    STORAGE_FAILURE = "storage_failure"
    EXTERNAL_TRANSIENT = "external_transient"
    EXTERNAL_PERMANENT = "external_permanent"


class PolicyMode(str, Enum):
    """Policy evaluation mode."""
    STRICT = "strict"
    LENIENT = "lenient"


@dataclass
class SourceResult:
    """Result from single source fetch."""
    source_id: str
    success: bool
    items_count: int
    error: Optional[str] = None
    error_category: ErrorCategory = ErrorCategory.OK
    latency_ms: int = 0
    bytes_received: int = 0


@dataclass
class PolicyConfig:
    """Policy configuration."""
    mode: PolicyMode = PolicyMode.STRICT
    min_success_count: int = 1
    mandatory_sources: Set[str] = field(default_factory=set)
    max_external_transient: int = 2  # Max allowed transient failures in lenient


@dataclass
class PolicyVerdict:
    """Policy evaluation result."""
    passed: bool
    mode: PolicyMode
    reason: str
    exit_code: int

    # Detailed counts
    total_sources: int = 0
    success_count: int = 0
    fail_count: int = 0

    # By category
    internal_bug_count: int = 0
    security_violation_count: int = 0
    storage_failure_count: int = 0
    external_transient_count: int = 0
    external_permanent_count: int = 0

    # Mandatory check
    mandatory_failed: List[str] = field(default_factory=list)

    # Details
    source_verdicts: Dict[str, str] = field(default_factory=dict)


def categorize_error(error_str: Optional[str]) -> ErrorCategory:
    """
    Categorize error string into ErrorCategory.

    Fail-closed: Unknown errors = INTERNAL_BUG.
    """
    if not error_str:
        return ErrorCategory.OK

    error_lower = error_str.lower()

    # Security violations
    if any(kw in error_lower for kw in [
        "egress denied", "not_in_allowlist", "allowlist",
        "redirect", "forbidden host", "security"
    ]):
        return ErrorCategory.SECURITY_VIOLATION

    # Storage failures
    if any(kw in error_lower for kw in [
        "disk full", "permission denied", "storage",
        "write failed", "no space", "readonly"
    ]):
        return ErrorCategory.STORAGE_FAILURE

    # HTTP 4xx = external permanent (often our bug: wrong URL, auth)
    if any(kw in error_lower for kw in [
        "http 400", "http 401", "http 403", "http 404",
        "http 4", "client error"
    ]):
        return ErrorCategory.EXTERNAL_PERMANENT

    # HTTP 5xx = external transient (server problem)
    if any(kw in error_lower for kw in [
        "http 500", "http 502", "http 503", "http 504",
        "http 5", "server error"
    ]):
        return ErrorCategory.EXTERNAL_TRANSIENT

    # Network errors = external transient
    if any(kw in error_lower for kw in [
        "timeout", "connection", "network", "dns",
        "unreachable", "refused", "reset"
    ]):
        return ErrorCategory.EXTERNAL_TRANSIENT

    # Parse errors = internal bug (our parser)
    if any(kw in error_lower for kw in [
        "parse", "xml", "json", "decode", "invalid"
    ]):
        return ErrorCategory.INTERNAL_BUG

    # Unknown = assume internal bug (fail-closed)
    return ErrorCategory.INTERNAL_BUG


def evaluate_policy(
    results: List[SourceResult],
    config: PolicyConfig,
) -> PolicyVerdict:
    """
    Evaluate collection results against policy.

    Args:
        results: List of per-source results
        config: Policy configuration

    Returns:
        PolicyVerdict with pass/fail and details
    """
    verdict = PolicyVerdict(
        passed=False,
        mode=config.mode,
        reason="",
        exit_code=1,
        total_sources=len(results),
    )

    # Count by category
    for r in results:
        verdict.source_verdicts[r.source_id] = f"{r.error_category.value}:{r.error or 'OK'}"

        if r.success:
            verdict.success_count += 1
        else:
            verdict.fail_count += 1

            cat = r.error_category
            if cat == ErrorCategory.INTERNAL_BUG:
                verdict.internal_bug_count += 1
            elif cat == ErrorCategory.SECURITY_VIOLATION:
                verdict.security_violation_count += 1
            elif cat == ErrorCategory.STORAGE_FAILURE:
                verdict.storage_failure_count += 1
            elif cat == ErrorCategory.EXTERNAL_TRANSIENT:
                verdict.external_transient_count += 1
            elif cat == ErrorCategory.EXTERNAL_PERMANENT:
                verdict.external_permanent_count += 1

    # Check mandatory sources
    success_ids = {r.source_id for r in results if r.success}
    for mandatory in config.mandatory_sources:
        if mandatory not in success_ids:
            verdict.mandatory_failed.append(mandatory)

    # STRICT mode: any failure = FAIL
    if config.mode == PolicyMode.STRICT:
        if verdict.fail_count > 0:
            verdict.passed = False
            verdict.reason = f"STRICT: {verdict.fail_count} sources failed"
            verdict.exit_code = 1
        elif verdict.success_count == 0:
            verdict.passed = False
            verdict.reason = "STRICT: No sources succeeded"
            verdict.exit_code = 1
        else:
            verdict.passed = True
            verdict.reason = f"STRICT: All {verdict.success_count} sources OK"
            verdict.exit_code = 0
        return verdict

    # LENIENT mode: check blocker categories
    blocker_count = (
        verdict.internal_bug_count +
        verdict.security_violation_count +
        verdict.storage_failure_count
    )

    if blocker_count > 0:
        categories = []
        if verdict.internal_bug_count:
            categories.append(f"internal_bug={verdict.internal_bug_count}")
        if verdict.security_violation_count:
            categories.append(f"security_violation={verdict.security_violation_count}")
        if verdict.storage_failure_count:
            categories.append(f"storage_failure={verdict.storage_failure_count}")

        verdict.passed = False
        verdict.reason = f"LENIENT BLOCKED: {', '.join(categories)}"
        verdict.exit_code = 1
        return verdict

    # Check min_success
    if verdict.success_count < config.min_success_count:
        verdict.passed = False
        verdict.reason = f"LENIENT: success={verdict.success_count} < min={config.min_success_count}"
        verdict.exit_code = 1
        return verdict

    # Check mandatory
    if verdict.mandatory_failed:
        verdict.passed = False
        verdict.reason = f"LENIENT: mandatory failed: {verdict.mandatory_failed}"
        verdict.exit_code = 1
        return verdict

    # Check external transient limit
    if verdict.external_transient_count > config.max_external_transient:
        verdict.passed = False
        verdict.reason = f"LENIENT: external_transient={verdict.external_transient_count} > max={config.max_external_transient}"
        verdict.exit_code = 1
        return verdict

    # All checks passed
    verdict.passed = True
    verdict.reason = f"LENIENT: {verdict.success_count}/{verdict.total_sources} sources OK"
    verdict.exit_code = 0
    return verdict


def load_policy_config(config_path: Optional[Path] = None) -> PolicyConfig:
    """
    Load policy config from JSON file.

    Default path: config/spider_policy.json
    """
    if config_path is None:
        config_path = Path("config/spider_policy.json")

    if not config_path.exists():
        return PolicyConfig()  # Defaults

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        return PolicyConfig(
            mode=PolicyMode(data.get("mode", "strict")),
            min_success_count=int(data.get("min_success_count", 1)),
            mandatory_sources=set(data.get("mandatory_sources", [])),
            max_external_transient=int(data.get("max_external_transient", 2)),
        )
    except Exception:
        return PolicyConfig()  # Defaults on error


def format_verdict_report(verdict: PolicyVerdict) -> str:
    """Format verdict as human-readable report."""
    lines = [
        f"=== POLICY VERDICT: {'PASS' if verdict.passed else 'FAIL'} ===",
        f"Mode: {verdict.mode.value}",
        f"Reason: {verdict.reason}",
        f"Exit code: {verdict.exit_code}",
        "",
        f"Sources: {verdict.total_sources} total, {verdict.success_count} success, {verdict.fail_count} failed",
        "",
        "Error categories:",
        f"  internal_bug: {verdict.internal_bug_count}",
        f"  security_violation: {verdict.security_violation_count}",
        f"  storage_failure: {verdict.storage_failure_count}",
        f"  external_transient: {verdict.external_transient_count}",
        f"  external_permanent: {verdict.external_permanent_count}",
    ]

    if verdict.mandatory_failed:
        lines.append("")
        lines.append(f"Mandatory failed: {', '.join(verdict.mandatory_failed)}")

    lines.append("")
    lines.append("Per-source verdicts:")
    for source_id, detail in sorted(verdict.source_verdicts.items()):
        lines.append(f"  {source_id}: {detail}")

    lines.append("=== END VERDICT ===")
    return "\n".join(lines)
