# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 15:50:00 UTC
# === END SIGNATURE ===
"""
tools/generate_allowlist.py - Generate AllowList files from sources_registry.json.

SSoT: sources_registry.json is the single source of truth.
Output: AllowList.core.txt, AllowList.spider.txt, AllowList.dev.txt

VALIDATION (fail-closed):
    - Host-only (no scheme, path, query)
    - No wildcards (* or ?)
    - Lowercase normalization
    - Duplicate detection
    - RFC-1123 hostname validation

USAGE:
    python -m tools.generate_allowlist           # Generate all
    python -m tools.generate_allowlist --dry-run # Preview without writing
    python -m tools.generate_allowlist --diff    # Show what would change
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Paths
_THIS_FILE = Path(__file__).resolve()
_TOOLS_DIR = _THIS_FILE.parent
_MINIBOT_DIR = _TOOLS_DIR.parent
_REGISTRY_PATH = _MINIBOT_DIR / "sources_registry.json"

# Output files
_OUTPUT_FILES = {
    "core": _MINIBOT_DIR / "AllowList.core.txt",
    "spider": _MINIBOT_DIR / "AllowList.spider.txt",
    "dev": _MINIBOT_DIR / "AllowList.dev.txt",
}

# Legacy combined file (for backwards compatibility)
_LEGACY_ALLOWLIST = _MINIBOT_DIR / "AllowList.txt"

# RFC-1123 hostname regex (simplified)
# Allows: a-z, 0-9, hyphen, dots; no leading/trailing hyphens per label
_HOSTNAME_REGEX = re.compile(
    r"^(?!-)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


class ValidationError(Exception):
    """Raised when validation fails."""
    pass


def validate_host(host: str, line_context: str = "") -> Tuple[bool, Optional[str]]:
    """
    Validate a single hostname.

    Returns:
        (is_valid, error_message)
    """
    # Normalize
    host = host.strip().lower()

    if not host:
        return False, f"Empty host {line_context}"

    # No schemes
    if host.startswith("http://") or host.startswith("https://"):
        return False, f"Scheme not allowed: {host} {line_context}"

    # No paths/queries
    if "/" in host or "?" in host or "#" in host:
        return False, f"Path/query not allowed: {host} {line_context}"

    # No wildcards
    if "*" in host or "?" in host:
        return False, f"Wildcards not allowed: {host} {line_context}"

    # No spaces
    if " " in host or "\t" in host:
        return False, f"Whitespace in host: {host} {line_context}"

    # RFC-1123 check
    if not _HOSTNAME_REGEX.match(host):
        return False, f"Invalid hostname format: {host} {line_context}"

    return True, None


def load_registry() -> Dict[str, Any]:
    """Load and validate sources_registry.json."""
    if not _REGISTRY_PATH.exists():
        raise FileNotFoundError(f"Registry not found: {_REGISTRY_PATH}")

    with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "sources" not in data:
        raise ValidationError("Registry missing 'sources' array")

    return data


def process_registry(data: Dict[str, Any]) -> Tuple[Dict[str, Set[str]], List[str]]:
    """
    Process registry and return hosts by use_case.

    Returns:
        (hosts_by_use_case, errors)
    """
    hosts: Dict[str, Set[str]] = {
        "core": set(),
        "spider": set(),
        "dev": set(),
    }
    errors: List[str] = []
    seen_hosts: Set[str] = set()

    for idx, source in enumerate(data.get("sources", [])):
        host = source.get("host", "").strip().lower()
        use_case = source.get("use_case", "spider")
        enabled = source.get("enabled", True)

        if not enabled:
            continue

        # Validate
        is_valid, error = validate_host(host, f"(source #{idx})")
        if not is_valid:
            errors.append(error)
            continue

        # Check duplicates in registry
        if host in seen_hosts:
            errors.append(f"Duplicate in registry: {host} (source #{idx})")
            continue

        seen_hosts.add(host)

        # Add to appropriate set
        if use_case in hosts:
            hosts[use_case].add(host)
        else:
            errors.append(f"Unknown use_case '{use_case}' for {host}")

    # Spider includes core; dev is separate
    # Actually, spider should be independent; core is minimal
    # User may want to combine: spider process can use spider list

    return hosts, errors


def generate_allowlist_content(hosts: Set[str], profile: str) -> str:
    """Generate allowlist file content."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = [
        "# HTTP allowlist (HOSTS ONLY). Wildcards forbidden. Fail-closed.",
        f"# Profile: {profile.upper()}",
        f"# Generated: {now}",
        f"# Source: sources_registry.json",
        f"# Count: {len(hosts)}",
        "#",
    ]

    # Sort and add hosts
    for host in sorted(hosts):
        lines.append(host)

    # Trailing newline
    lines.append("")

    return "\n".join(lines)


def read_existing(path: Path) -> Set[str]:
    """Read existing allowlist (for diff)."""
    if not path.exists():
        return set()

    hosts = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                hosts.add(line.lower())

    return hosts


def atomic_write(path: Path, content: str) -> None:
    """Atomic write: temp -> fsync -> replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def main() -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Generate AllowList files from sources_registry.json"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview without writing files",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Show what would change",
    )
    parser.add_argument(
        "--profile",
        choices=["core", "spider", "dev", "all"],
        default="all",
        help="Which profile to generate (default: all)",
    )

    args = parser.parse_args()

    print("=== ALLOWLIST GENERATOR ===\n")

    # Load registry
    try:
        data = load_registry()
        print(f"Loaded registry: {_REGISTRY_PATH}")
        print(f"Total sources: {len(data.get('sources', []))}")
    except Exception as e:
        print(f"FAIL: Cannot load registry: {e}", file=sys.stderr)
        return 1

    # Process
    hosts_by_profile, errors = process_registry(data)

    if errors:
        print(f"\nVALIDATION ERRORS ({len(errors)}):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        print("\nFAIL: Fix validation errors before generating.", file=sys.stderr)
        return 1

    # Summary
    print(f"\nHosts by profile:")
    print(f"  core:   {len(hosts_by_profile['core'])}")
    print(f"  spider: {len(hosts_by_profile['spider'])}")
    print(f"  dev:    {len(hosts_by_profile['dev'])}")

    # Generate profiles
    profiles_to_generate = (
        ["core", "spider", "dev"] if args.profile == "all"
        else [args.profile]
    )

    for profile in profiles_to_generate:
        hosts = hosts_by_profile[profile]
        output_path = _OUTPUT_FILES[profile]

        print(f"\n--- {profile.upper()} ---")

        if args.diff:
            existing = read_existing(output_path)
            added = hosts - existing
            removed = existing - hosts

            if added:
                print(f"  + Added ({len(added)}):")
                for h in sorted(added)[:10]:
                    print(f"    + {h}")
                if len(added) > 10:
                    print(f"    ... and {len(added) - 10} more")

            if removed:
                print(f"  - Removed ({len(removed)}):")
                for h in sorted(removed)[:10]:
                    print(f"    - {h}")
                if len(removed) > 10:
                    print(f"    ... and {len(removed) - 10} more")

            if not added and not removed:
                print("  No changes")

        if args.dry_run:
            print(f"  Would write: {output_path}")
            print(f"  Hosts: {len(hosts)}")
        else:
            content = generate_allowlist_content(hosts, profile)
            atomic_write(output_path, content)
            print(f"  Written: {output_path}")
            print(f"  Hosts: {len(hosts)}")

    # Generate legacy combined file (core + spider + dev)
    if not args.dry_run and args.profile == "all":
        all_hosts = (
            hosts_by_profile["core"] |
            hosts_by_profile["spider"] |
            hosts_by_profile["dev"]
        )
        content = generate_allowlist_content(all_hosts, "combined")
        atomic_write(_LEGACY_ALLOWLIST, content)
        print(f"\n--- COMBINED (legacy) ---")
        print(f"  Written: {_LEGACY_ALLOWLIST}")
        print(f"  Hosts: {len(all_hosts)}")

    print("\n=== DONE ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
