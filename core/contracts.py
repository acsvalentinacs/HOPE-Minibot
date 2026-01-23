# === AI SIGNATURE ===
# Created by: Kirill Dev
# Created at: 2026-01-19 18:24:32 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-22 19:20:00 UTC
# === END SIGNATURE ===
"""
Contract validation primitives with fail-closed semantics.

- canonical_json: deterministic JSON for hashing
- json_hash_hex: sha256 of canonical JSON
- wrap/parse_sha256_prefix_line: integrity-verified JSONL format
- require_fresh: TTL validation
- make_envelope: standard event envelope builder
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Sequence, Set, Tuple


class ContractViolation(RuntimeError):
    """Fail-closed: contract broken."""


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj: Dict[str, Any], exclude_keys: Optional[Set[str]] = None) -> str:
    """
    Deterministic JSON representation.
    - sort_keys=True
    - separators without spaces
    - allow_nan=False (fail-closed)
    - optional top-level key exclusion for hash computation (e.g., {"id"})
    """
    data = obj
    if exclude_keys:
        data = {k: v for k, v in obj.items() if k not in exclude_keys}
    try:
        return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except ValueError as e:
        raise ContractViolation(f"Non-JSON value (NaN/Inf?) in payload: {e}") from e


def json_hash_hex(obj: Dict[str, Any], exclude_keys: Optional[Set[str]] = None) -> str:
    return _sha256_hex(canonical_json(obj, exclude_keys=exclude_keys).encode("utf-8"))


def wrap_sha256_prefix_line(obj: Dict[str, Any], hash_exclude_keys: Optional[Set[str]] = None) -> str:
    """
    Returns: sha256:<hash>:<json>
    Hash computed over canonical JSON (optionally excluding keys like 'id').
    """
    js = canonical_json(obj, exclude_keys=None)
    h = _sha256_hex(canonical_json(obj, exclude_keys=hash_exclude_keys).encode("utf-8"))
    return f"sha256:{h}:{js}"


def parse_sha256_prefix_line(line: str, hash_exclude_keys: Optional[Set[str]] = None) -> Dict[str, Any]:
    """
    Strict parser+verifier for 'sha256:<hex>:<json>'.
    Fail-closed on any mismatch.
    """
    raw = line.strip()
    if not raw:
        raise ContractViolation("Empty JSONL line")
    if not raw.startswith("sha256:"):
        raise ContractViolation("Missing sha256 prefix")
    try:
        _, hex_hash, json_part = raw.split(":", 2)
    except ValueError as e:
        raise ContractViolation("Bad sha256 prefix format") from e
    if len(hex_hash) != 64 or any(c not in "0123456789abcdef" for c in hex_hash):
        raise ContractViolation("Bad sha256 hex")
    try:
        obj = json.loads(json_part)
    except json.JSONDecodeError as e:
        raise ContractViolation(f"Invalid JSON: {e}") from e
    if not isinstance(obj, dict):
        raise ContractViolation("Event must be a JSON object")
    expected = _sha256_hex(canonical_json(obj, exclude_keys=hash_exclude_keys).encode("utf-8"))
    if expected != hex_hash:
        raise ContractViolation("sha256 mismatch (corruption or non-canonical writer)")
    return obj


def now_ms() -> int:
    return int(time.time() * 1000)


def require_fresh(ts_ms: int, ttl_ms: int, *, now_ms_value: Optional[int] = None) -> None:
    if ttl_ms <= 0:
        raise ContractViolation("ttl_ms must be positive")
    n = now_ms_value if now_ms_value is not None else now_ms()
    age = n - int(ts_ms)
    if age < 0:
        raise ContractViolation("Event timestamp is from the future")
    if age > ttl_ms:
        raise ContractViolation(f"Event stale: age_ms={age} ttl_ms={ttl_ms}")


def compute_cmdline_hash_windows_strict() -> str:
    """
    Windows SSoT: sha256(GetCommandLineW()).
    Fail-closed if not available on Windows.
    """
    if sys.platform.startswith("win"):
        try:
            from core.win_cmdline import get_command_line_w  # type: ignore
        except Exception as e:
            raise ContractViolation(f"win_cmdline not available: {e}") from e
        cmd = get_command_line_w()
        if not cmd or not isinstance(cmd, str):
            raise ContractViolation("GetCommandLineW returned empty/invalid")
        return _sha256_hex(cmd.encode("utf-8"))
    raise ContractViolation("Windows strict cmdline hash requested on non-Windows platform")


def make_envelope(
    *,
    schema: str,
    version: int,
    source: str,
    type_name: str,
    run_id: str,
    payload: Dict[str, Any],
    cmdline_hash: str,
    ts_ms: Optional[int] = None,
    event_id: Optional[str] = None,
) -> Dict[str, Any]:
    if version <= 0:
        raise ContractViolation("version must be positive")
    if not schema or not source or not type_name or not run_id:
        raise ContractViolation("schema/source/type/run_id required")
    if not isinstance(payload, dict):
        raise ContractViolation("payload must be object")

    eid = event_id or str(uuid.uuid4())
    tms = int(ts_ms) if ts_ms is not None else now_ms()

    env: Dict[str, Any] = {
        "schema": schema,
        "v": int(version),
        "ts": tms,
        "id": eid,
        "source": source,
        "type": type_name,
        "run_id": run_id,
        "cmdline_hash": cmdline_hash,
        "payload": payload,
    }
    return env

