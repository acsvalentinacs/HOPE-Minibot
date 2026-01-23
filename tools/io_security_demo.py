# === AI SIGNATURE ===
# Created by: Claude
# Created at: 2026-01-20 10:50:00 UTC
# === END SIGNATURE ===
"""
IO Security Layer - Demo / Examples

Run: python -m tools.io_security_demo
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.io_security import (
    SecureIO,
    AIActor,
    SecurityViolation,
    OwnershipViolation,
    EnvProtectionViolation,
)


def demo_basic_write():
    """Demo: Basic file write with metadata."""
    print("\n=== Demo: Basic Write ===")

    # Create actor identity
    actor = AIActor(
        actor_id="claude@minibot",
        name="Claude",
        model="opus-4"
    )
    io = SecureIO(actor)

    # Write a file
    test_file = Path("state/demo_file.txt")
    sha, meta = io.write_text(
        test_file,
        "This file was created by Claude AI",
        reason="demo creation"
    )

    print(f"File created: {test_file}")
    print(f"SHA256: {sha}")
    print(f"Metadata: {meta}")


def demo_ownership_protection():
    """Demo: Protection against deleting other AI's files."""
    print("\n=== Demo: Ownership Protection ===")

    # GPT creates a file
    gpt = SecureIO(AIActor("gpt@remote", "GPT", "4o"))
    gpt_file = Path("state/gpt_created.txt")
    gpt.write_text(gpt_file, "GPT wrote this", reason="gpt demo")
    print(f"GPT created: {gpt_file}")

    # Claude tries to delete it
    claude = SecureIO(AIActor("claude@local", "Claude", "opus"))
    try:
        claude.delete(gpt_file, reason="cleanup attempt")
        print("ERROR: Should have blocked!")
    except OwnershipViolation as e:
        print(f"BLOCKED: {e}")

    # GPT can delete own file
    success, path = gpt.delete(gpt_file, reason="self-cleanup")
    print(f"GPT deleted own file: {success} -> quarantine: {path}")


def demo_env_protection():
    """Demo: .env append-only protection."""
    print("\n=== Demo: .env Protection ===")

    io = SecureIO(AIActor("demo@test", "Demo", "v1"))

    # Try to write (BLOCKED)
    try:
        io.write_text(
            Path(r"C:\secrets\hope\.env"),
            "HACKED=true",
            reason="test"
        )
        print("ERROR: Should have blocked!")
    except EnvProtectionViolation as e:
        print(f"BLOCKED write: {e}")

    # Append is OK (but we won't actually run it)
    print("Append would work: io.append_env('NEW_KEY', 'value', reason='...')")


def demo_edit_history():
    """Demo: Edit history tracking."""
    print("\n=== Demo: Edit History ===")

    actor = AIActor("demo@test", "Demo", "v1")
    io = SecureIO(actor)

    test_file = Path("state/history_demo.txt")

    # Create
    io.write_text(test_file, "Version 1", reason="initial")
    print("Created v1")

    # Edit
    io.write_text(test_file, "Version 2", reason="update")
    print("Edited to v2")

    # Edit again
    io.write_text(test_file, "Version 3", reason="another update")
    print("Edited to v3")

    # Read metadata
    import json
    meta_file = test_file.with_name(test_file.name + ".ai.meta.json")
    if meta_file.exists():
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        print(f"\nEdit history ({len(meta['edit_history'])} events):")
        for event in meta["edit_history"]:
            print(f"  - {event['ts_utc']}: {event['action']} by {event['by']}")


def main():
    print("=" * 60)
    print("IO SECURITY LAYER v2.0 - DEMO")
    print("=" * 60)

    # Set env vars for actor (alternative to explicit AIActor)
    os.environ["HOPE_AI_ACTOR_ID"] = "demo@cli"
    os.environ["HOPE_AI_NAME"] = "DemoCLI"
    os.environ["HOPE_AI_MODEL"] = "demo"

    demo_basic_write()
    demo_ownership_protection()
    demo_env_protection()
    demo_edit_history()

    print("\n" + "=" * 60)
    print("DEMO COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
