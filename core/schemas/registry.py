# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 13:50:00 UTC
# === END SIGNATURE ===
"""
core/schemas/registry.py - Event Schema Registry.

Lightweight schema validation without heavy dependencies.
Uses simple type checking and required field validation.

DESIGN PRINCIPLES:
1. Forward compatible: extra fields are allowed
2. Fail-soft: invalid events go to quarantine, not crash
3. No external dependencies (stdlib only)
4. Versioned schemas (name.vN format)

SCHEMA FORMAT:
    {
        "name": "schema_name.v1",
        "required": ["field1", "field2"],
        "types": {
            "field1": "str",
            "field2": "float",
            "field3": "int|None",  # Optional type
            "field4": "str|int",   # Union type
            "field5": "dict",
            "field6": "list",
        },
        "defaults": {
            "optional_field": "default_value"
        }
    }
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class SchemaError(Exception):
    """Raised when schema validation fails in strict mode."""
    pass


# === TYPE VALIDATORS ===

def _check_type(value: Any, type_spec: str) -> bool:
    """
    Check if value matches type specification.

    Supported types:
        str, int, float, bool, dict, list, None
        Union types: "str|int", "str|None"
    """
    if "|" in type_spec:
        # Union type
        return any(_check_type(value, t.strip()) for t in type_spec.split("|"))

    type_map = {
        "str": str,
        "int": int,
        "float": (int, float),  # int is valid as float
        "bool": bool,
        "dict": dict,
        "list": list,
        "None": type(None),
    }

    expected = type_map.get(type_spec)
    if expected is None:
        # Unknown type spec - allow anything
        return True

    return isinstance(value, expected)


# === SCHEMA DEFINITIONS ===

SCHEMA_NEXUS_HISTORY_V1 = {
    "name": "nexus_history.v1",
    "required": ["schema", "ts_unix", "ts_utc", "direction", "text", "peer"],
    "types": {
        "schema": "str",
        "ts_unix": "float",
        "ts_utc": "str",
        "direction": "str",  # in|out|err|status
        "text": "str",
        "peer": "str",
        "bridge_id": "str|None",
        "reply_to": "str|None",
        "inbox": "str|None",
        "msg_type": "str|None",
        "meta": "dict|None",
    },
    "defaults": {
        "bridge_id": None,
        "reply_to": None,
        "inbox": "nexus",
        "msg_type": None,
        "meta": None,
    },
}

SCHEMA_QUARANTINE_V1 = {
    "name": "quarantine.v1",
    "required": ["schema", "ts_unix", "ts_utc", "reason", "source", "blob_sha256"],
    "types": {
        "schema": "str",
        "ts_unix": "float",
        "ts_utc": "str",
        "reason": "str",  # schema_mismatch|sha256_mismatch|decode_error|...
        "source": "str",  # component.function
        "blob_path": "str|None",
        "blob_sha256": "str",
        "context": "dict|None",
    },
    "defaults": {
        "blob_path": None,
        "context": None,
    },
}

SCHEMA_AUDIT_V1 = {
    "name": "audit.v1",
    "required": ["schema", "ts_unix", "ts_utc", "component", "event"],
    "types": {
        "schema": "str",
        "ts_unix": "float",
        "ts_utc": "str",
        "component": "str",
        "event": "str",  # startup|rotate|archive|compress|maintenance|error|config_hash
        "details": "dict|None",
        "git_commit": "str|None",
        "git_dirty": "bool|None",
        "python_version": "str|None",
        "hope_mode": "str|None",
        "config_sha256": "str|None",
    },
    "defaults": {
        "details": None,
        "git_commit": None,
        "git_dirty": None,
        "python_version": None,
        "hope_mode": None,
        "config_sha256": None,
    },
}


# === SCHEMA REGISTRY ===

SCHEMAS: Dict[str, dict] = {
    "nexus_history.v1": SCHEMA_NEXUS_HISTORY_V1,
    "quarantine.v1": SCHEMA_QUARANTINE_V1,
    "audit.v1": SCHEMA_AUDIT_V1,
}


def get_schema(name: str) -> Optional[dict]:
    """Get schema by name, or None if not found."""
    return SCHEMAS.get(name)


def list_schemas() -> List[str]:
    """List all registered schema names."""
    return list(SCHEMAS.keys())


# === VALIDATION API ===

def validate(schema_name: str, obj: Dict[str, Any]) -> List[str]:
    """
    Validate object against schema.

    Args:
        schema_name: Schema name (e.g., "nexus_history.v1")
        obj: Object to validate

    Returns:
        List of error messages (empty if valid)
    """
    schema = SCHEMAS.get(schema_name)
    if schema is None:
        return [f"Unknown schema: {schema_name}"]

    errors: List[str] = []

    # Check required fields
    for field in schema.get("required", []):
        if field not in obj:
            errors.append(f"Missing required field: {field}")

    # Check types
    types = schema.get("types", {})
    for field, value in obj.items():
        if field in types:
            type_spec = types[field]
            if not _check_type(value, type_spec):
                errors.append(
                    f"Type mismatch for '{field}': expected {type_spec}, got {type(value).__name__}"
                )

    # Check schema field matches
    if "schema" in obj and obj["schema"] != schema_name:
        errors.append(
            f"Schema mismatch: object has '{obj['schema']}', validating against '{schema_name}'"
        )

    return errors


def require(schema_name: str, obj: Dict[str, Any]) -> None:
    """
    Validate object and raise SchemaError if invalid.

    Use this for fail-closed scenarios (rare).

    Args:
        schema_name: Schema name
        obj: Object to validate

    Raises:
        SchemaError: If validation fails
    """
    errors = validate(schema_name, obj)
    if errors:
        raise SchemaError(f"Schema validation failed for {schema_name}: {errors}")


def normalize(schema_name: str, obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize object by adding missing optional fields with defaults.

    Also adds schema, ts_unix, ts_utc if missing.

    Args:
        schema_name: Schema name
        obj: Object to normalize

    Returns:
        New dict with defaults applied (original is not modified)
    """
    schema = SCHEMAS.get(schema_name)
    if schema is None:
        # Unknown schema - return copy with basic fields
        result = dict(obj)
        if "schema" not in result:
            result["schema"] = schema_name
        return result

    result = dict(obj)

    # Add schema identifier
    if "schema" not in result:
        result["schema"] = schema_name

    # Add timestamps if missing
    now = datetime.now(timezone.utc)
    if "ts_unix" not in result:
        result["ts_unix"] = time.time()
    if "ts_utc" not in result:
        result["ts_utc"] = now.isoformat()

    # Apply defaults for missing optional fields
    defaults = schema.get("defaults", {})
    for field, default_value in defaults.items():
        if field not in result:
            result[field] = default_value

    return result


# === EVENT BUILDERS (convenience) ===

def build_nexus_event(
    direction: str,
    text: str,
    peer: str,
    *,
    bridge_id: Optional[str] = None,
    reply_to: Optional[str] = None,
    inbox: str = "nexus",
    msg_type: Optional[str] = None,
    meta: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    Build a nexus_history.v1 event.

    Args:
        direction: "in" | "out" | "err" | "status"
        text: Message text
        peer: Sender (for in) or target (for out)
        bridge_id: Optional message ID from bridge
        reply_to: Optional parent message ID
        inbox: Inbox name
        msg_type: Message type
        meta: Additional metadata

    Returns:
        Normalized event dict
    """
    now = datetime.now(timezone.utc)
    return {
        "schema": "nexus_history.v1",
        "ts_unix": time.time(),
        "ts_utc": now.isoformat(),
        "direction": direction,
        "text": text,
        "peer": peer,
        "bridge_id": bridge_id,
        "reply_to": reply_to,
        "inbox": inbox,
        "msg_type": msg_type,
        "meta": meta,
    }


def build_quarantine_event(
    reason: str,
    source: str,
    blob_sha256: str,
    *,
    blob_path: Optional[str] = None,
    context: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    Build a quarantine.v1 event.

    Args:
        reason: Why this was quarantined
        source: Where it came from (component.function)
        blob_sha256: SHA256 of the raw blob
        blob_path: Path where blob is stored
        context: Additional context

    Returns:
        Normalized event dict
    """
    now = datetime.now(timezone.utc)
    return {
        "schema": "quarantine.v1",
        "ts_unix": time.time(),
        "ts_utc": now.isoformat(),
        "reason": reason,
        "source": source,
        "blob_sha256": blob_sha256,
        "blob_path": blob_path,
        "context": context,
    }


def build_audit_event(
    component: str,
    event: str,
    *,
    details: Optional[dict] = None,
    git_commit: Optional[str] = None,
    git_dirty: Optional[bool] = None,
    python_version: Optional[str] = None,
    hope_mode: Optional[str] = None,
    config_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build an audit.v1 event.

    Args:
        component: Component name
        event: Event type (startup, rotate, archive, etc.)
        details: Additional details
        git_commit: Git commit hash
        git_dirty: Whether working tree is dirty
        python_version: Python version string
        hope_mode: HOPE_MODE value
        config_sha256: Config hash

    Returns:
        Normalized event dict
    """
    now = datetime.now(timezone.utc)
    return {
        "schema": "audit.v1",
        "ts_unix": time.time(),
        "ts_utc": now.isoformat(),
        "component": component,
        "event": event,
        "details": details,
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "python_version": python_version,
        "hope_mode": hope_mode,
        "config_sha256": config_sha256,
    }
