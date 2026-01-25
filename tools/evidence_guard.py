# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T18:00:00Z
# Purpose: Evidence Guard - fail-closed schema validation for health/evidence files
# === END SIGNATURE ===
"""
Evidence Guard - Fail-Closed Schema Validation.

Validates health/evidence JSON files against required schema.
Missing fields, wrong types, or invalid values = FAIL.

Supported schemas:
- live_trade_v1: Trading evidence (run_live_trading.py output)
- spider_health_v1: Spider evidence
- testnet_gate_v1: TESTNET gate evidence

Usage:
    python tools/evidence_guard.py --path state/health/live_trade.json
    python tools/evidence_guard.py --all  # Validate all known health files

Exit codes:
    0 = VALID (schema matches)
    1 = INVALID (schema mismatch or missing fields)
    2 = ERROR (file not found, parse error)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# SSoT paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = PROJECT_ROOT / "state"
HEALTH_DIR = STATE_DIR / "health"


# === SCHEMA DEFINITIONS ===

SCHEMAS = {
    "live_trade_v1": {
        "required_fields": {
            "schema_version": {"type": "str", "value": "live_trade_v1"},
            "ts_utc": {"type": "str", "pattern": r"^\d{4}-\d{2}-\d{2}T"},
            "mode": {"type": "str", "values": ["DRY", "TESTNET", "MAINNET"]},
            "run_id": {"type": "str", "min_length": 20},
            "cmdline_ssot": {"type": "dict"},
            "gates": {"type": "dict"},
        },
        "nested_required": {
            "cmdline_ssot.sha256": {"type": "str", "pattern": r"^[a-f0-9]{64}$"},
            "gates.live_gate": {"type": "dict"},
            "gates.live_gate.passed": {"type": "bool"},
            "gates.live_gate.decision": {"type": "str"},
        },
    },
    "spider_health_v1": {
        "required_fields": {
            "schema_version": {"type": "str", "value": "spider_health_v1"},
            "ts_utc": {"type": "str", "pattern": r"^\d{4}-\d{2}-\d{2}T"},
            "run_id": {"type": "str", "min_length": 20},
            "cmdline_ssot": {"type": "dict"},
            "policy_egress": {"type": "dict"},
        },
        "nested_required": {
            "cmdline_ssot.sha256": {"type": "str", "pattern": r"^[a-f0-9]{64}$"},
            "policy_egress.allowlist_sha256": {"type": "str", "pattern": r"^[a-f0-9]{64}$"},
        },
    },
    "testnet_gate_v1": {
        "required_fields": {
            "schema_version": {"type": "str", "value": "testnet_gate_v1"},
            "ts_utc": {"type": "str", "pattern": r"^\d{4}-\d{2}-\d{2}T"},
            "passed": {"type": "bool"},
            "endpoint": {"type": "str"},
            "response_status": {"type": "int"},
        },
        "nested_required": {},
    },
}


def get_nested_value(data: Dict, path: str) -> Any:
    """
    Get nested value from dict using dot notation.

    Args:
        data: Source dictionary
        path: Dot-separated path (e.g., "cmdline_ssot.sha256")

    Returns:
        Value at path or None if not found
    """
    keys = path.split(".")
    current = data

    for key in keys:
        if not isinstance(current, dict):
            return None
        if key not in current:
            return None
        current = current[key]

    return current


def validate_field(value: Any, spec: Dict) -> tuple[bool, str]:
    """
    Validate a field value against specification.

    Args:
        value: Field value to validate
        spec: Field specification dict

    Returns:
        (is_valid, error_message)
    """
    expected_type = spec.get("type")

    # Type check
    if expected_type == "str":
        if not isinstance(value, str):
            return False, f"expected str, got {type(value).__name__}"

        # Check exact value
        if "value" in spec and value != spec["value"]:
            return False, f"expected '{spec['value']}', got '{value}'"

        # Check pattern
        if "pattern" in spec and not re.match(spec["pattern"], value):
            return False, f"does not match pattern {spec['pattern']}"

        # Check allowed values
        if "values" in spec and value not in spec["values"]:
            return False, f"must be one of {spec['values']}, got '{value}'"

        # Check min length
        if "min_length" in spec and len(value) < spec["min_length"]:
            return False, f"min length {spec['min_length']}, got {len(value)}"

    elif expected_type == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            return False, f"expected int, got {type(value).__name__}"

    elif expected_type == "bool":
        if not isinstance(value, bool):
            return False, f"expected bool, got {type(value).__name__}"

    elif expected_type == "dict":
        if not isinstance(value, dict):
            return False, f"expected dict, got {type(value).__name__}"

    elif expected_type == "list":
        if not isinstance(value, list):
            return False, f"expected list, got {type(value).__name__}"

    return True, ""


def validate_evidence(path: Path) -> tuple[bool, str, List[str]]:
    """
    Validate evidence file against schema.

    Args:
        path: Path to evidence JSON file

    Returns:
        (is_valid, schema_version, list of errors)
    """
    errors = []

    # Check file exists
    if not path.exists():
        return False, "", [f"File not found: {path}"]

    # Parse JSON
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return False, "", [f"Invalid JSON: {e}"]

    if not isinstance(data, dict):
        return False, "", ["Root must be object/dict"]

    # Get schema version
    schema_version = data.get("schema_version", "")
    if not schema_version:
        return False, "", ["Missing required field: schema_version"]

    # Find matching schema
    if schema_version not in SCHEMAS:
        return False, schema_version, [f"Unknown schema_version: {schema_version}"]

    schema = SCHEMAS[schema_version]

    # Validate required fields
    for field_name, spec in schema["required_fields"].items():
        if field_name not in data:
            errors.append(f"Missing required field: {field_name}")
            continue

        valid, error = validate_field(data[field_name], spec)
        if not valid:
            errors.append(f"Field '{field_name}': {error}")

    # Validate nested required fields
    for field_path, spec in schema.get("nested_required", {}).items():
        value = get_nested_value(data, field_path)
        if value is None:
            errors.append(f"Missing nested field: {field_path}")
            continue

        valid, error = validate_field(value, spec)
        if not valid:
            errors.append(f"Field '{field_path}': {error}")

    return len(errors) == 0, schema_version, errors


def validate_gates_passed(path: Path) -> tuple[bool, List[str]]:
    """
    Additional check: verify all gates passed.

    Args:
        path: Path to evidence file

    Returns:
        (all_passed, list of failed gates)
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False, ["Cannot read file"]

    gates = data.get("gates", {})
    if not gates:
        return True, []  # No gates to check

    failed = []
    for gate_name, gate_data in gates.items():
        if isinstance(gate_data, dict):
            if gate_data.get("passed") is False:
                failed.append(f"{gate_name}: {gate_data.get('reason', 'FAIL')}")

    return len(failed) == 0, failed


def main() -> int:
    """Main entrypoint."""
    parser = argparse.ArgumentParser(
        description="Evidence Guard - fail-closed schema validation",
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=HEALTH_DIR / "live_trade.json",
        help="Path to evidence file (default: state/health/live_trade.json)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Validate all known health files",
    )
    parser.add_argument(
        "--check-gates",
        action="store_true",
        default=True,
        help="Also verify all gates passed (default: True)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    args = parser.parse_args()

    print("=== EVIDENCE GUARD ===")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print()

    # Collect files to validate
    files_to_check = []
    if args.all:
        # Check all known health files
        known_files = [
            HEALTH_DIR / "live_trade.json",
            HEALTH_DIR / "spider_health.json",
            HEALTH_DIR / "testnet_gate.json",
        ]
        files_to_check = [f for f in known_files if f.exists()]
        if not files_to_check:
            print("No health files found to validate")
            return 2
    else:
        files_to_check = [args.path]

    all_valid = True
    results = []

    for filepath in files_to_check:
        print(f"Validating: {filepath.relative_to(PROJECT_ROOT)}")

        is_valid, schema_version, errors = validate_evidence(filepath)

        # Additional gates check
        gates_ok = True
        failed_gates = []
        if is_valid and args.check_gates:
            gates_ok, failed_gates = validate_gates_passed(filepath)
            if not gates_ok:
                errors.extend([f"Gate failed: {g}" for g in failed_gates])
                is_valid = False

        result = {
            "path": str(filepath.relative_to(PROJECT_ROOT)),
            "valid": is_valid,
            "schema_version": schema_version,
            "errors": errors,
        }
        results.append(result)

        if is_valid:
            print(f"  Schema: {schema_version}")
            print(f"  Result: PASS")
        else:
            print(f"  Schema: {schema_version or 'UNKNOWN'}")
            print(f"  Errors ({len(errors)}):")
            for err in errors:
                print(f"    - {err}")
            print(f"  Result: FAIL")
            all_valid = False

        print()

    # Summary
    if args.json:
        output = {
            "passed": all_valid,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "files_checked": len(results),
            "results": results,
        }
        print(json.dumps(output, indent=2))
    else:
        print("=== SUMMARY ===")
        print(f"Files checked: {len(results)}")
        print(f"Result: {'PASS' if all_valid else 'FAIL'}")

    return 0 if all_valid else 1


if __name__ == "__main__":
    sys.exit(main())
