# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 13:55:00 UTC
# === END SIGNATURE ===
"""
core/audit.py - Unified Audit Logging.

Emits structured audit events for:
- Component startup (version, git commit, config hash)
- Maintenance operations (rotate, archive, compress)
- Errors and warnings
- Configuration changes

USAGE:
    from core.audit import emit_startup_audit, emit_audit

    # At component startup
    emit_startup_audit("nexus", config_public={"poll_ms": 1000})

    # For other events
    emit_audit("nexus", "rotate", details={"file": "history.jsonl", "size": 10485760})

AUDIT FILE:
    state/audit/<component>_audit.jsonl
    state/audit/startup_audit.jsonl (all startups)

FORMAT:
    sha256-prefixed JSONL (Canon B)
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from core.state_layout import get_layout
from core.schemas.registry import build_audit_event, validate
from core.jsonl_sha import append_sha256_line


def _get_git_info() -> tuple[Optional[str], Optional[bool]]:
    """
    Get git commit hash and dirty status.

    Returns:
        (commit_hash, is_dirty) - both None if git unavailable
    """
    try:
        # Get commit hash
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).parent.parent,
        )
        if result.returncode != 0:
            return None, None

        commit = result.stdout.strip()

        # Check dirty status
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).parent.parent,
        )
        dirty = bool(result.stdout.strip()) if result.returncode == 0 else None

        return commit, dirty

    except Exception:
        return None, None


def _hash_config(config: Dict[str, Any]) -> str:
    """
    Compute SHA256 hash of config (canonical JSON).

    Args:
        config: Config dict (should NOT contain secrets)

    Returns:
        SHA256 hex digest
    """
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def emit_startup_audit(
    component: str,
    config_public: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Emit startup audit event.

    Records:
    - Git commit and dirty status
    - Python version
    - HOPE_MODE
    - Config hash (of public config only)

    Args:
        component: Component name (e.g., "nexus", "orchestrator")
        config_public: Public config (NO SECRETS!)

    Returns:
        SHA256 of the written event
    """
    git_commit, git_dirty = _get_git_info()

    config_sha = None
    if config_public:
        config_sha = _hash_config(config_public)

    event = build_audit_event(
        component=component,
        event="startup",
        git_commit=git_commit,
        git_dirty=git_dirty,
        python_version=sys.version,
        hope_mode=os.environ.get("HOPE_MODE", "DEV"),
        config_sha256=config_sha,
        details={"config_public": config_public} if config_public else None,
    )

    # Validate before writing
    errors = validate("audit.v1", event)
    if errors:
        # Log to stderr but don't fail
        print(f"[WARN] audit: startup event validation errors: {errors}", file=sys.stderr)

    # Write to startup audit log
    layout = get_layout()
    startup_log = layout.audit_log("startup")

    return append_sha256_line(startup_log, event)


def emit_audit(
    component: str,
    event_type: str,
    *,
    details: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Emit general audit event.

    Args:
        component: Component name
        event_type: Event type (rotate, archive, compress, error, etc.)
        details: Additional details

    Returns:
        SHA256 of the written event
    """
    event = build_audit_event(
        component=component,
        event=event_type,
        details=details,
    )

    # Validate before writing
    errors = validate("audit.v1", event)
    if errors:
        print(f"[WARN] audit: event validation errors: {errors}", file=sys.stderr)

    # Write to component audit log
    layout = get_layout()
    audit_log = layout.audit_log(component)

    return append_sha256_line(audit_log, event)


def emit_maintenance_audit(
    action: str,
    files_affected: list[str],
    *,
    details: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Emit maintenance operation audit event.

    CRITICAL: Used before ANY destructive operation.
    Creates immutable record of what was done.

    Args:
        action: Maintenance action (archive, compress, purge)
        files_affected: List of affected file paths
        details: Additional details

    Returns:
        SHA256 of the written event
    """
    full_details = {
        "action": action,
        "files_count": len(files_affected),
        "files": files_affected,
    }
    if details:
        full_details.update(details)

    return emit_audit("maintenance", action, details=full_details)


def emit_error_audit(
    component: str,
    error_type: str,
    error_message: str,
    *,
    context: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Emit error audit event.

    Args:
        component: Component where error occurred
        error_type: Error classification
        error_message: Error description
        context: Additional context

    Returns:
        SHA256 of the written event
    """
    details = {
        "error_type": error_type,
        "error_message": error_message,
    }
    if context:
        details["context"] = context

    return emit_audit(component, "error", details=details)


# === CLI SELF-TEST ===

def _self_test() -> int:
    """Run self-test."""
    import tempfile

    print("=== AUDIT SELF-TEST ===")

    # Use temp directory
    with tempfile.TemporaryDirectory() as tmpdir:
        from core.state_layout import reset_layout
        reset_layout(Path(tmpdir))

        # Test 1: Startup audit
        print("Test 1: Startup audit...")
        sha1 = emit_startup_audit("test_component", config_public={"poll_ms": 500})
        assert len(sha1) == 64, f"Expected 64-char SHA, got {len(sha1)}"
        print(f"  PASS: sha256:{sha1[:16]}...")

        # Test 2: General audit
        print("Test 2: General audit...")
        sha2 = emit_audit("test_component", "test_event", details={"key": "value"})
        assert len(sha2) == 64
        print(f"  PASS: sha256:{sha2[:16]}...")

        # Test 3: Maintenance audit
        print("Test 3: Maintenance audit...")
        sha3 = emit_maintenance_audit("archive", ["file1.jsonl", "file2.jsonl"])
        assert len(sha3) == 64
        print(f"  PASS: sha256:{sha3[:16]}...")

        # Test 4: Error audit
        print("Test 4: Error audit...")
        sha4 = emit_error_audit("test_component", "TestError", "Test error message")
        assert len(sha4) == 64
        print(f"  PASS: sha256:{sha4[:16]}...")

        # Verify files exist
        print("Test 5: Verify files...")
        layout = get_layout()
        startup_log = layout.audit_log("startup")
        component_log = layout.audit_log("test_component")
        maintenance_log = layout.audit_log("maintenance")

        assert startup_log.exists(), "Startup log missing"
        assert component_log.exists(), "Component log missing"
        assert maintenance_log.exists(), "Maintenance log missing"
        print("  PASS: All audit logs created")

        print("\n=== ALL AUDIT TESTS PASSED ===")
        return 0


if __name__ == "__main__":
    sys.exit(_self_test())
