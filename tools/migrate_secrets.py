# === AI SIGNATURE ===
# Created by: Claude
# Created at: 2026-01-20 11:40:00 UTC
# === END SIGNATURE ===
"""
Secrets Migration Tool v1.0

Migrates secrets from .env files to Windows Credential Manager.
This is a ONE-TIME operation.

Usage:
    python -m tools.migrate_secrets scan       - Find all secrets in .env files
    python -m tools.migrate_secrets migrate    - Migrate to keyring (interactive)
    python -m tools.migrate_secrets verify     - Verify migration
    python -m tools.migrate_secrets cleanup    - Delete old .env files (after verification)

IMPORTANT: After migration, run `verify` to ensure all secrets are accessible,
then run `cleanup` to remove old .env files (security risk if left behind).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.secrets import (
    get_secret,
    set_secret_keyring,
    list_sources,
    redact,
    SECRETS_ENV_PATH,
)

# Known .env file locations to scan
ENV_LOCATIONS = [
    Path(r"C:\secrets\hope\.env"),
    Path(__file__).parent.parent / "config" / "telegram.env",
    Path(__file__).parent.parent / ".env",
    Path(__file__).parent.parent.parent / ".env",  # TradingBot root
]

# Critical secrets that MUST exist
REQUIRED_SECRETS = [
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_TOKEN",
    "TELEGRAM_TOKEN_MINI",
]

# Optional secrets
OPTIONAL_SECRETS = [
    "OPENAI_API_KEY",
    "FRIEND_BRIDGE_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_ALLOWED",
    "TELEGRAM_ALERT_CHAT_IDS",
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
]


def parse_env_file(path: Path) -> dict:
    """Parse .env file and return key-value dict."""
    if not path.exists():
        return {}

    result = {}
    try:
        content = path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # Remove quotes
                if value and value[0] in ('"', "'") and value[-1] == value[0]:
                    value = value[1:-1]
                if key and value:
                    result[key] = value
    except Exception as e:
        print(f"ERROR reading {path}: {e}")
    return result


def cmd_scan():
    """Scan all .env files and report found secrets."""
    print("=" * 60)
    print("SECRETS SCAN")
    print("=" * 60)

    all_secrets = {}

    for env_path in ENV_LOCATIONS:
        if env_path.exists():
            print(f"\n[FOUND] {env_path}")
            secrets = parse_env_file(env_path)
            for key, value in secrets.items():
                print(f"  {key} = {redact(value)}")
                if key not in all_secrets:
                    all_secrets[key] = (value, env_path)
        else:
            print(f"[SKIP] {env_path} (not found)")

    print("\n" + "=" * 60)
    print(f"Total unique secrets found: {len(all_secrets)}")

    # Check required
    print("\nRequired secrets status:")
    for key in REQUIRED_SECRETS:
        if key in all_secrets:
            print(f"  [OK] {key}")
        else:
            print(f"  [MISSING] {key}")

    return all_secrets


def cmd_migrate():
    """Migrate secrets to Windows Credential Manager."""
    print("=" * 60)
    print("SECRETS MIGRATION")
    print("=" * 60)

    all_secrets = {}
    for env_path in ENV_LOCATIONS:
        if env_path.exists():
            secrets = parse_env_file(env_path)
            for key, value in secrets.items():
                if key not in all_secrets:
                    all_secrets[key] = value

    if not all_secrets:
        print("No secrets found to migrate.")
        return

    print(f"\nFound {len(all_secrets)} secrets to migrate:")
    for key in all_secrets:
        print(f"  - {key}")

    print("\nMigrating to Windows Credential Manager...")

    success_count = 0
    for key, value in all_secrets.items():
        # Check if already in keyring
        sources = list_sources(key)
        if sources["keyring"]:
            print(f"  [SKIP] {key} - already in keyring")
            continue

        if set_secret_keyring(key, value):
            print(f"  [OK] {key} - migrated")
            success_count += 1
        else:
            print(f"  [FAIL] {key} - migration failed")

    print(f"\nMigrated {success_count} secrets.")
    print("\nRun 'python -m tools.migrate_secrets verify' to confirm.")


def cmd_verify():
    """Verify all secrets are accessible."""
    print("=" * 60)
    print("SECRETS VERIFICATION")
    print("=" * 60)

    all_keys = set(REQUIRED_SECRETS) | set(OPTIONAL_SECRETS)

    print("\nChecking secret access:")
    ok_count = 0
    fail_count = 0

    for key in sorted(all_keys):
        value = get_secret(key)
        sources = list_sources(key)

        source_list = [s for s, found in sources.items() if found]
        source_str = ", ".join(source_list) if source_list else "NOT FOUND"

        if value:
            print(f"  [OK] {key} = {redact(value)} (from: {source_str})")
            ok_count += 1
        else:
            # Check if it's required
            if key in REQUIRED_SECRETS:
                print(f"  [FAIL] {key} - NOT FOUND (REQUIRED!)")
                fail_count += 1
            else:
                print(f"  [SKIP] {key} - not set (optional)")

    print(f"\nResult: {ok_count} OK, {fail_count} FAIL")

    if fail_count > 0:
        print("\nWARNING: Some required secrets are missing!")
        print("Run 'python -m tools.migrate_secrets migrate' first.")
        return False

    print("\nAll required secrets verified. Safe to run cleanup.")
    return True


def cmd_cleanup():
    """Delete old .env files (after verification)."""
    print("=" * 60)
    print("CLEANUP OLD .env FILES")
    print("=" * 60)

    print("\nWARNING: This will DELETE old .env files!")
    print("Make sure you have run 'verify' first.\n")

    # Don't delete the protected secrets file
    to_delete = [p for p in ENV_LOCATIONS if p.exists() and p != SECRETS_ENV_PATH]

    if not to_delete:
        print("No old .env files found to delete.")
        return

    print("Files to delete:")
    for path in to_delete:
        print(f"  - {path}")

    confirm = input("\nType 'DELETE' to confirm: ")
    if confirm != "DELETE":
        print("Aborted.")
        return

    for path in to_delete:
        try:
            path.unlink()
            print(f"  [DELETED] {path}")
        except Exception as e:
            print(f"  [ERROR] {path}: {e}")

    print("\nCleanup complete.")
    print("Old .env files have been removed.")
    print(f"Protected file remains: {SECRETS_ENV_PATH}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1].lower()

    if cmd == "scan":
        cmd_scan()
    elif cmd == "migrate":
        cmd_migrate()
    elif cmd == "verify":
        cmd_verify()
    elif cmd == "cleanup":
        cmd_cleanup()
    else:
        print(f"Unknown command: {cmd}")
        print("Available: scan, migrate, verify, cleanup")


if __name__ == "__main__":
    main()
