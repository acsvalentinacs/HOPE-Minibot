# === AI SIGNATURE ===
# Created by: Claude
# Created at: 2026-01-20 09:50:00 UTC
# === END SIGNATURE ===
"""
Tests for AI File Guard module.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
import tempfile
import json

from core.ai_file_guard import (
    set_current_ai,
    get_current_ai,
    make_python_signature,
    make_json_meta,
    make_markdown_signature,
    extract_python_creator,
    extract_json_creator,
    extract_markdown_creator,
    check_can_delete,
    is_protected_env,
    check_env_write_allowed,
    safe_env_append,
    guard_file_operation,
    SECRETS_ENV_PATH,
)


def test_ai_identity():
    """Test AI identity management."""
    set_current_ai("TestAI")
    assert get_current_ai() == "TestAI"
    set_current_ai("Claude")
    assert get_current_ai() == "Claude"


def test_python_signature():
    """Test Python signature generation and extraction."""
    set_current_ai("Claude")
    sig = make_python_signature()
    assert "Created by: Claude" in sig
    assert "Created at:" in sig

    # Extract
    result = extract_python_creator(sig)
    assert result is not None
    assert result[0] == "Claude"


def test_json_meta():
    """Test JSON meta generation and extraction."""
    set_current_ai("GPT")
    meta = make_json_meta()
    assert meta["_ai_meta"]["created_by"] == "GPT"

    # Extract
    result = extract_json_creator(meta)
    assert result is not None
    assert result[0] == "GPT"


def test_markdown_signature():
    """Test Markdown signature generation and extraction."""
    set_current_ai("Claude")
    sig = make_markdown_signature()
    assert "Created by Claude" in sig

    # Extract
    result = extract_markdown_creator(sig)
    assert result is not None
    assert result[0] == "Claude"


def test_delete_same_ai():
    """Test that same AI can delete its files."""
    set_current_ai("Claude")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(make_python_signature(created_by="Claude"))
        f.write("# test file\n")
        temp_path = Path(f.name)

    try:
        can_delete, reason = check_can_delete(temp_path, "Claude")
        assert can_delete is True
        assert "same AI" in reason
    finally:
        temp_path.unlink(missing_ok=True)


def test_delete_other_ai_refused():
    """Test that other AI cannot delete files."""
    set_current_ai("GPT")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(make_python_signature(created_by="Claude"))
        f.write("# test file\n")
        temp_path = Path(f.name)

    try:
        can_delete, reason = check_can_delete(temp_path, "GPT")
        assert can_delete is False
        assert "REFUSED" in reason
        assert "Claude" in reason
    finally:
        temp_path.unlink(missing_ok=True)


def test_delete_legacy_file():
    """Test that files without signature can be deleted."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("# legacy file without signature\n")
        temp_path = Path(f.name)

    try:
        can_delete, reason = check_can_delete(temp_path, "Claude")
        assert can_delete is True
        assert "legacy" in reason.lower()
    finally:
        temp_path.unlink(missing_ok=True)


def test_protected_env_detection():
    """Test protected .env path detection."""
    assert is_protected_env(SECRETS_ENV_PATH) is True
    assert is_protected_env(Path(r"C:\secrets\hope\.env")) is True
    assert is_protected_env(Path(r"C:\other\path\.env")) is False
    assert is_protected_env(Path(r"C:\secrets\hope\other.txt")) is False


def test_env_write_forbidden():
    """Test that write operations on .env are forbidden."""
    allowed, reason = check_env_write_allowed(SECRETS_ENV_PATH, "write")
    assert allowed is False
    assert "FORBIDDEN" in reason

    allowed, reason = check_env_write_allowed(SECRETS_ENV_PATH, "overwrite")
    assert allowed is False

    allowed, reason = check_env_write_allowed(SECRETS_ENV_PATH, "delete")
    assert allowed is False


def test_env_append_allowed():
    """Test that append operations on .env are allowed."""
    allowed, reason = check_env_write_allowed(SECRETS_ENV_PATH, "append")
    assert allowed is True


def test_guard_file_operation():
    """Test main guard function."""
    # Test delete on protected env
    allowed, reason = guard_file_operation("delete", SECRETS_ENV_PATH)
    assert allowed is False

    # Test write on protected env
    allowed, reason = guard_file_operation("write", SECRETS_ENV_PATH)
    assert allowed is False

    # Test append on protected env
    allowed, reason = guard_file_operation("append", SECRETS_ENV_PATH)
    assert allowed is True

    # Test regular file operations
    allowed, reason = guard_file_operation("write", Path("some/other/file.py"))
    assert allowed is True


if __name__ == "__main__":
    print("Running AI File Guard tests...")
    test_ai_identity()
    print("[OK] test_ai_identity")
    test_python_signature()
    print("[OK] test_python_signature")
    test_json_meta()
    print("[OK] test_json_meta")
    test_markdown_signature()
    print("[OK] test_markdown_signature")
    test_delete_same_ai()
    print("[OK] test_delete_same_ai")
    test_delete_other_ai_refused()
    print("[OK] test_delete_other_ai_refused")
    test_delete_legacy_file()
    print("[OK] test_delete_legacy_file")
    test_protected_env_detection()
    print("[OK] test_protected_env_detection")
    test_env_write_forbidden()
    print("[OK] test_env_write_forbidden")
    test_env_append_allowed()
    print("[OK] test_env_append_allowed")
    test_guard_file_operation()
    print("[OK] test_guard_file_operation")
    print("\n=== ALL TESTS PASSED ===")
