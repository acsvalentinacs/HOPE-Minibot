# -*- coding: utf-8 -*-
"""
HOPE Runtime Verify v1

Purpose:
- Verify lock contract continuously (not just at creation)
- Produce machine-readable verify state sidecar (ROLE.verify.json)
- Write JSONL audit trail for verification events (P9→P10 bridge)
- Fail-closed: any mismatch => raise RuntimeError

Invariants:
- Single source of truth for cmdline: ONLY via get_cmdline_raw (GetCommandLineW)
- Atomic writes: ONLY via atomic_write_json
- Explicit contracts: schema versioned strings
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from minibot.io_jsonl import append_jsonl
from minibot.lock_truth_v2 import (
    LOCK_SCHEMA,
    atomic_write_json,
    compute_mutex_name,
    get_cmdline_raw,
    get_current_birth_filetime,
    norm_root,
    pid_is_alive,
    root_hash_short,
    sha256_prefixed,
)

VERIFY_SCHEMA = "hope.lock.verify.v1"
AUDIT_SCHEMA = "hope.verify.audit.v1"
AUDIT_FILENAME = "verify_events.jsonl"


def append_audit_event(
    lock_dir: Path,
    *,
    event_type: str,
    role: str,
    ok: bool,
    errors: List[str],
    details: Optional[Dict[str, Any]] = None,
) -> Path:
    """
    Append verification event to JSONL audit trail.

    Uses P10 append_jsonl for atomic, locked writes.
    Append-only, never overwrites. Each line is a complete JSON object.
    """
    lock_dir = Path(lock_dir)
    audit_path = lock_dir / AUDIT_FILENAME

    event: Dict[str, Any] = {
        "event_type": event_type,
        "role": str(role).upper(),
        "pid": os.getpid(),
        "ok": ok,
        "error_count": len(errors),
        "errors": errors,
    }

    if details:
        event["details"] = details

    # P10: Use unified JSONL writer with lock + fsync
    append_jsonl(audit_path, event, schema=AUDIT_SCHEMA)

    return audit_path


@dataclass
class VerifyResult:
    """Result of lock verification."""
    ok: bool
    errors: List[str] = field(default_factory=list)
    report: Dict[str, Any] = field(default_factory=dict)


def verify_lock_object(
    lock_obj: Dict[str, Any],
    *,
    role: str,
    root: Path,
    check_current_process: bool = True,
    strict: bool = True,
) -> VerifyResult:
    """
    Verify a lock object against:
    1. Self-verification (cmdline_raw vs cmdline_hash)
    2. Root/mutex salting contract
    3. Current process identity (if check_current_process=True)

    Returns VerifyResult with ok=False if any check fails.
    """
    errors: List[str] = []
    now = time.time()

    role_u = str(role).strip().upper()
    root = Path(root).resolve()

    # Schema check
    if lock_obj.get("schema") != LOCK_SCHEMA:
        errors.append(f"schema mismatch: {lock_obj.get('schema')} != {LOCK_SCHEMA}")

    # Role check
    lock_role = str(lock_obj.get("role", "")).upper()
    if lock_role != role_u:
        errors.append(f"role mismatch: {lock_role} != {role_u}")

    # Root normalization check
    exp_root_norm = norm_root(root)
    if lock_obj.get("root_norm") != exp_root_norm:
        errors.append(f"root_norm mismatch: {lock_obj.get('root_norm')} != {exp_root_norm}")

    # Root hash check (support both field names)
    exp_rh = root_hash_short(root)
    lock_rh = lock_obj.get("root_hash12") or lock_obj.get("root_hash")
    if lock_rh != exp_rh:
        errors.append(f"root_hash mismatch: {lock_rh} != {exp_rh}")

    # Mutex name salting check
    exp_mutex = compute_mutex_name(role_u, root)
    if lock_obj.get("mutex_name") != exp_mutex:
        errors.append(f"mutex_name mismatch: {lock_obj.get('mutex_name')} != {exp_mutex}")

    # Self-verification: cmdline_raw -> cmdline_hash
    cmd_raw = lock_obj.get("cmdline_raw")
    cmd_hash = lock_obj.get("cmdline_hash")

    if not isinstance(cmd_raw, str) or not isinstance(cmd_hash, str):
        errors.append("cmdline fields missing or invalid type")
    else:
        computed_hash = sha256_prefixed(cmd_raw)
        if computed_hash != cmd_hash:
            errors.append(f"self-verification FAIL: sha256(cmdline_raw) != cmdline_hash")

    # Owner PID liveness check
    owner_pid = lock_obj.get("owner_pid")
    if not isinstance(owner_pid, int) or owner_pid <= 0:
        errors.append("owner_pid missing or invalid")
    elif not pid_is_alive(owner_pid):
        errors.append(f"owner_pid {owner_pid} is dead (stale lock)")

    # Current process identity checks
    cmd_now_hash = None
    birth_now = None

    if check_current_process:
        cmd_now = get_cmdline_raw()
        cmd_now_hash = sha256_prefixed(cmd_now)
        birth_now = get_current_birth_filetime()

        # Cmdline hash must match current process
        if isinstance(cmd_hash, str) and cmd_hash != cmd_now_hash:
            errors.append("current cmdline hash mismatch (process != lock owner)")

        # Birth filetime check (PID reuse detection)
        birth_lock = lock_obj.get("birth_filetime_100ns") or lock_obj.get("birth_filetime")

        if strict:
            if not isinstance(birth_lock, int):
                errors.append("birth_filetime missing/invalid (strict mode)")
            elif int(birth_lock) != int(birth_now):
                errors.append(f"birth_filetime mismatch: {birth_lock} != {birth_now} (PID reuse?)")

        # Owner PID must be current process
        if owner_pid != os.getpid():
            errors.append(f"owner_pid {owner_pid} != current pid {os.getpid()}")

    # Build report
    report: Dict[str, Any] = {
        "schema": VERIFY_SCHEMA,
        "verified_at_ts": now,
        "verified_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "ok": len(errors) == 0,
        "error_count": len(errors),
        "errors": errors,
        "role": role_u,
        "root_norm": exp_root_norm,
        "root_hash12": exp_rh,
        "mutex_name_expected": exp_mutex,
        "lock_owner_pid": owner_pid,
        "lock_created_ts": lock_obj.get("created_ts") or lock_obj.get("started_ts"),
    }

    if check_current_process:
        report["current_pid"] = os.getpid()
        report["current_cmdline_hash"] = cmd_now_hash
        report["current_birth_filetime"] = birth_now

    return VerifyResult(ok=len(errors) == 0, errors=errors, report=report)


def verify_lockfile(
    lock_path: Path,
    *,
    role: str,
    root: Path,
    check_current_process: bool = True,
    strict: bool = True,
) -> VerifyResult:
    """Load lock file and verify it."""
    lock_path = Path(lock_path)

    if not lock_path.exists():
        return VerifyResult(
            ok=False,
            errors=[f"lock file not found: {lock_path}"],
            report={"schema": VERIFY_SCHEMA, "ok": False, "errors": ["lock file not found"]}
        )

    try:
        obj = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception as e:
        return VerifyResult(
            ok=False,
            errors=[f"failed to parse lock: {e}"],
            report={"schema": VERIFY_SCHEMA, "ok": False, "errors": [str(e)]}
        )

    return verify_lock_object(
        obj,
        role=role,
        root=root,
        check_current_process=check_current_process,
        strict=strict,
    )


def write_verify_sidecar(
    lock_dir: Path,
    *,
    role: str,
    report: Dict[str, Any],
) -> Path:
    """Write verify sidecar atomically."""
    lock_dir = Path(lock_dir)
    lock_dir.mkdir(parents=True, exist_ok=True)
    out = lock_dir / f"{str(role).upper()}.verify.json"
    atomic_write_json(out, report)
    return out


def verify_and_write_sidecar(
    *,
    lock_path: Path,
    lock_dir: Path,
    role: str,
    root: Path,
    strict: bool = True,
    audit: bool = True,
    audit_event_type: str = "verify",
) -> VerifyResult:
    """
    Verify lock file and write sidecar.
    Returns VerifyResult (does NOT raise on failure).

    If audit=True, appends event to JSONL audit trail.
    """
    result = verify_lockfile(
        lock_path,
        role=role,
        root=root,
        check_current_process=True,
        strict=strict,
    )
    write_verify_sidecar(lock_dir, role=role, report=result.report)

    # Audit trail (always write on FAIL, optionally on PASS)
    if audit and (not result.ok or audit_event_type in ("verify_start", "verify_fail", "verify_loop")):
        append_audit_event(
            lock_dir,
            event_type=audit_event_type if not result.ok else f"{audit_event_type}_fail",
            role=role,
            ok=result.ok,
            errors=result.errors,
            details={
                "lock_path": str(lock_path),
                "root_norm": result.report.get("root_norm"),
                "lock_owner_pid": result.report.get("lock_owner_pid"),
                "strict": strict,
            },
        )

    return result


def verify_or_raise(
    *,
    lock_path: Path,
    lock_dir: Path,
    role: str,
    root: Path,
    strict: bool = True,
    audit: bool = True,
    audit_event_type: str = "verify_start",
) -> Path:
    """
    Verify lock file, write sidecar, raise on failure.
    Returns sidecar path on success.

    Always logs FAIL to audit trail. On success, only logs if event_type is verify_start.
    """
    result = verify_and_write_sidecar(
        lock_path=lock_path,
        lock_dir=lock_dir,
        role=role,
        root=root,
        strict=strict,
        audit=False,  # We handle audit ourselves for better event_type control
    )

    # Always audit FAIL events
    if not result.ok:
        append_audit_event(
            lock_dir,
            event_type="verify_fail",
            role=role,
            ok=False,
            errors=result.errors,
            details={
                "lock_path": str(lock_path),
                "root_norm": result.report.get("root_norm"),
                "lock_owner_pid": result.report.get("lock_owner_pid"),
                "strict": strict,
                "source": audit_event_type,
            },
        )
        raise RuntimeError(f"Runtime verify FAIL: {'; '.join(result.errors)}")

    # Audit success for initial verification
    if audit and audit_event_type == "verify_start":
        append_audit_event(
            lock_dir,
            event_type="verify_pass",
            role=role,
            ok=True,
            errors=[],
            details={
                "lock_path": str(lock_path),
                "root_norm": result.report.get("root_norm"),
                "lock_owner_pid": result.report.get("lock_owner_pid"),
            },
        )

    return lock_dir / f"{str(role).upper()}.verify.json"
