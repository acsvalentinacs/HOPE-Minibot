# === AI SIGNATURE ===
# Created by: Claude
# Created at: 2026-01-20 10:45:00 UTC
# === END SIGNATURE ===
"""
Tests for IO Security Layer v2.0
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import tempfile
from pathlib import Path

from core.io_security import (
    SecureIO,
    AIActor,
    SecurityViolation,
    OwnershipViolation,
    EnvProtectionViolation,
    is_protected_env,
    meta_path_for,
    sha256_text,
)


def test_ai_actor():
    """Test AIActor creation."""
    actor = AIActor("claude@test", "Claude", "opus-4")
    assert actor.actor_id == "claude@test"
    assert actor.name == "Claude"
    assert actor.model == "opus-4"
    print("[OK] test_ai_actor")


def test_is_protected_env():
    """Test protected env detection."""
    # Protected
    assert is_protected_env(Path(r"C:\secrets\hope\.env")) == True
    assert is_protected_env(Path(r"C:\secrets\hope\production.env")) == True

    # Not protected
    assert is_protected_env(Path(r"C:\other\.env")) == False
    assert is_protected_env(Path(r"test.env")) == False
    print("[OK] test_is_protected_env")


def test_write_text():
    """Test secure write with metadata."""
    with tempfile.TemporaryDirectory() as tmpdir:
        actor = AIActor("test@local", "TestAI", "v1")
        io = SecureIO(actor)

        # Monkey-patch QUARANTINE_DIR for testing
        original_quarantine = io.__class__.__module__

        test_file = Path(tmpdir) / "test.txt"
        content = "Hello, World!"

        sha, meta_path = io.write_text(test_file, content, reason="test write")

        # Check file created
        assert test_file.exists()
        assert test_file.read_text(encoding="utf-8").strip() == content

        # Check metadata created
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["created_by"] == "TestAI"
        assert meta["created_by_actor_id"] == "test@local"
        assert len(meta["edit_history"]) == 1
        assert meta["edit_history"][0]["action"] == "create"

        print("[OK] test_write_text")


def test_write_edit():
    """Test edit updates metadata."""
    with tempfile.TemporaryDirectory() as tmpdir:
        actor = AIActor("test@local", "TestAI", "v1")
        io = SecureIO(actor)

        test_file = Path(tmpdir) / "edit_test.txt"

        # Create
        io.write_text(test_file, "Version 1", reason="create")

        # Edit
        io.write_text(test_file, "Version 2", reason="update")

        # Check metadata has 2 events
        meta_path = meta_path_for(test_file)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert len(meta["edit_history"]) == 2
        assert meta["edit_history"][1]["action"] == "edit"

        print("[OK] test_write_edit")


def test_delete_own_file():
    """Test deletion of own file (quarantine)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        actor = AIActor("test@local", "TestAI", "v1")
        io = SecureIO(actor)

        # Temporarily redirect quarantine
        import core.io_security as sec
        orig_quarantine = sec.QUARANTINE_DIR
        sec.QUARANTINE_DIR = Path(tmpdir) / "quarantine"

        try:
            test_file = Path(tmpdir) / "to_delete.txt"
            io.write_text(test_file, "Delete me", reason="create")

            assert test_file.exists()

            success, q_path = io.delete(test_file, reason="cleanup")

            assert success
            assert not test_file.exists()  # Original gone
            assert q_path.exists()  # Moved to quarantine

            print("[OK] test_delete_own_file")
        finally:
            sec.QUARANTINE_DIR = orig_quarantine


def test_delete_other_ai_file():
    """Test that deleting another AI's file raises error."""
    with tempfile.TemporaryDirectory() as tmpdir:
        actor1 = AIActor("gpt@remote", "GPT", "4o")
        actor2 = AIActor("claude@local", "Claude", "opus")

        io1 = SecureIO(actor1)
        io2 = SecureIO(actor2)

        # GPT creates file
        test_file = Path(tmpdir) / "gpt_file.txt"
        io1.write_text(test_file, "GPT created this", reason="test")

        # Claude tries to delete - should fail
        try:
            io2.delete(test_file, reason="cleanup")
            assert False, "Should have raised OwnershipViolation"
        except OwnershipViolation as e:
            assert "gpt@remote" in str(e)
            print("[OK] test_delete_other_ai_file")


def test_env_protection():
    """Test that .env write raises error."""
    actor = AIActor("test@local", "TestAI", "v1")
    io = SecureIO(actor)

    # Try to write to protected .env
    try:
        io.write_text(Path(r"C:\secrets\hope\.env"), "HACKED=true", reason="test")
        assert False, "Should have raised EnvProtectionViolation"
    except EnvProtectionViolation as e:
        assert "append_env" in str(e).lower() or "protected" in str(e).lower()
        print("[OK] test_env_protection")


def test_can_delete_check():
    """Test can_delete check."""
    with tempfile.TemporaryDirectory() as tmpdir:
        actor1 = AIActor("gpt@remote", "GPT", "4o")
        actor2 = AIActor("claude@local", "Claude", "opus")

        io1 = SecureIO(actor1)
        io2 = SecureIO(actor2)

        # GPT creates file
        test_file = Path(tmpdir) / "check_file.txt"
        io1.write_text(test_file, "GPT owns this", reason="test")

        # GPT can delete
        can, reason = io1.can_delete(test_file)
        assert can == True

        # Claude cannot
        can, reason = io2.can_delete(test_file)
        assert can == False
        assert "gpt@remote" in reason

        print("[OK] test_can_delete_check")


def test_sha256():
    """Test SHA256 calculation."""
    sha = sha256_text("Hello")
    assert sha.startswith("sha256:")
    assert len(sha) == 7 + 64  # "sha256:" + 64 hex chars
    print("[OK] test_sha256")


if __name__ == "__main__":
    print("Running IO Security tests...\n")

    test_ai_actor()
    test_is_protected_env()
    test_write_text()
    test_write_edit()
    test_delete_own_file()
    test_delete_other_ai_file()
    test_env_protection()
    test_can_delete_check()
    test_sha256()

    print("\n=== ALL TESTS PASSED ===")
