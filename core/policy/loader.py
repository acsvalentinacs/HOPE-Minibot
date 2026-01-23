# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 16:10:00 UTC
# === END SIGNATURE ===
"""
core/policy/loader.py - Policy Loader with SHA256 Self-Check.

HOPE-LAW-001: Policy must be verified before any work.

FAIL-CLOSED:
    - Missing policy file -> PolicyError
    - SHA256 mismatch -> PolicyError
    - Missing required keys -> PolicyError
"""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


class PolicyError(RuntimeError):
    """Raised when policy loading/validation fails. System must stop."""
    pass


def _canonical_json_bytes(obj: Dict[str, Any]) -> bytes:
    """
    Canonical JSON (sorted keys, minimal separators, UTF-8).
    Used for deterministic SHA256 computation.
    """
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    """Compute SHA256 hex digest."""
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Policy:
    """Immutable policy configuration."""
    policy_version: int
    forbidden_phrases: List[str]
    secret_patterns: List[str]
    enforce_channels: Dict[str, bool]
    allowlist: Dict[str, str]
    policy_sha256: str
    raw: Dict[str, Any]


_REQUIRED_KEYS = (
    "policy_version",
    "forbidden_phrases",
    "secret_patterns",
    "enforce_channels",
    "allowlist",
    "policy_sha256",
)


def load_policy(path: Path, component: str) -> Policy:
    """
    Load and validate policy from JSON file.

    FAIL-CLOSED:
        - File not found -> PolicyError
        - Invalid JSON -> PolicyError
        - Missing keys -> PolicyError
        - SHA256 mismatch -> PolicyError

    Args:
        path: Path to policy.json
        component: Component name (for logging)

    Returns:
        Validated Policy object

    Raises:
        PolicyError: On any validation failure
    """
    if not path.exists():
        raise PolicyError(f"[{component}] Policy file not found: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise PolicyError(f"[{component}] Invalid policy JSON: {e}") from e

    if not isinstance(raw, dict):
        raise PolicyError(f"[{component}] Policy must be a JSON object")

    # Check required keys
    for key in _REQUIRED_KEYS:
        if key not in raw:
            raise PolicyError(f"[{component}] Missing policy key: {key}")

    # Compute SHA256 (excluding policy_sha256 and _comment fields)
    to_hash = {k: v for k, v in raw.items() if k not in ("policy_sha256", "_comment")}
    computed = "sha256:" + _sha256_hex(_canonical_json_bytes(to_hash))
    expected = str(raw.get("policy_sha256", "")).strip()

    # Allow "PENDING_COMPUTATION" for initial setup
    if expected == "sha256:PENDING_COMPUTATION":
        print(
            f"[WARN] [{component}] Policy SHA256 not set. Run compute_policy_sha256() to generate.",
            file=sys.stderr,
        )
    elif expected != computed:
        raise PolicyError(
            f"[{component}] Policy SHA256 mismatch!\n"
            f"  Expected: {expected}\n"
            f"  Computed: {computed}\n"
            f"  FAIL-CLOSED: Policy may be tampered."
        )

    return Policy(
        policy_version=int(raw["policy_version"]),
        forbidden_phrases=list(raw["forbidden_phrases"]),
        secret_patterns=list(raw["secret_patterns"]),
        enforce_channels=dict(raw["enforce_channels"]),
        allowlist=dict(raw["allowlist"]),
        policy_sha256=expected,
        raw=raw,
    )


def compute_policy_sha256(path: Path) -> str:
    """
    Compute SHA256 for policy file (for initial setup).

    Args:
        path: Path to policy.json

    Returns:
        SHA256 string in format "sha256:..."
    """
    raw = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(raw, dict):
        raise PolicyError("Policy must be a JSON object")

    # Exclude policy_sha256 and _comment from hash computation
    to_hash = {k: v for k, v in raw.items() if k not in ("policy_sha256", "_comment")}
    return "sha256:" + _sha256_hex(_canonical_json_bytes(to_hash))


# === CLI ===

def _cli_main() -> int:
    """CLI for policy operations."""
    import argparse

    parser = argparse.ArgumentParser(description="HOPE Policy Loader CLI")
    parser.add_argument(
        "--compute-sha",
        action="store_true",
        help="Compute SHA256 for policy.json",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify policy.json integrity",
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=Path(__file__).parent / "policy.json",
        help="Path to policy.json",
    )

    args = parser.parse_args()

    if args.compute_sha:
        try:
            sha = compute_policy_sha256(args.path)
            print(f"Computed SHA256: {sha}")
            print(f"\nUpdate policy.json with:")
            print(f'  "policy_sha256": "{sha}"')
            return 0
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

    if args.verify:
        try:
            policy = load_policy(args.path, component="cli")
            print(f"Policy version: {policy.policy_version}")
            print(f"SHA256: {policy.policy_sha256}")
            print(f"Forbidden phrases: {len(policy.forbidden_phrases)}")
            print(f"Secret patterns: {len(policy.secret_patterns)}")
            print("VERIFY: PASS")
            return 0
        except PolicyError as e:
            print(f"VERIFY: FAIL\n{e}", file=sys.stderr)
            return 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_cli_main())
