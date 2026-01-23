# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 13:50:00 UTC
# === END SIGNATURE ===
"""
core/schemas - Event Schema Registry.

Provides validation for all HOPE event types:
- nexus_history.v1 - NEXUS message history
- quarantine.v1    - Quarantined invalid data
- audit.v1         - Audit trail events

USAGE:
    from core.schemas import validate, normalize, SchemaError

    # Validate an event
    errors = validate("nexus_history.v1", event_dict)
    if errors:
        # Handle validation failure
        quarantine(event_dict, reason="schema_mismatch")

    # Normalize an event (add missing fields)
    normalized = normalize("nexus_history.v1", event_dict)

FAIL-SOFT POLICY:
    Invalid events are NOT rejected outright.
    They are quarantined for later inspection, preserving data.
"""
from core.schemas.registry import (
    validate,
    require,
    normalize,
    get_schema,
    list_schemas,
    SchemaError,
    SCHEMAS,
)

__all__ = [
    "validate",
    "require",
    "normalize",
    "get_schema",
    "list_schemas",
    "SchemaError",
    "SCHEMAS",
]
