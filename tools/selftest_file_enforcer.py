# === AI SIGNATURE ===
# Created by: Claude
# Created at: 2026-01-22 12:00:00 UTC
# === END SIGNATURE ===
"""
Self-test for File Enforcer ("Sheriff") v2.0.

Comprehensive validation of filesystem security:
- Scope confinement
- Ownership validation
- Atomic writes
- Append-only .env
- sha256 JSONL integrity

Usage:
    python -m tools.selftest_file_enforcer
    python -m tools.selftest_file_enforcer --verbose
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════════════════════
# Test Framework
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TestResult:
    """Single test result."""
    name: str
    passed: bool
    duration_ms: float
    message: str = ""
    error: Optional[str] = None


@dataclass
class TestSuite:
    """Test suite runner."""
    name: str
    results: List[TestResult] = field(default_factory=list)
    verbose: bool = False

    def run_test(self, name: str, test_fn: Callable[[], bool]) -> TestResult:
        """Run single test with timing."""
        start = time.perf_counter()
        try:
            passed = test_fn()
            duration = (time.perf_counter() - start) * 1000
            result = TestResult(
                name=name,
                passed=passed,
                duration_ms=duration,
                message="OK" if passed else "FAILED",
            )
        except Exception as e:
            duration = (time.perf_counter() - start) * 1000
            result = TestResult(
                name=name,
                passed=False,
                duration_ms=duration,
                message="EXCEPTION",
                error=str(e),
            )

        self.results.append(result)

        # Print result
        status = "[PASS]" if result.passed else "[FAIL]"
        print(f"  {status} {name} ({result.duration_ms:.1f}ms)")
        if result.error and self.verbose:
            print(f"        Error: {result.error}")

        return result

    def summary(self) -> Dict[str, Any]:
        """Get test summary."""
        passed = sum(1 for r in self.results if r.passed)
        failed = sum(1 for r in self.results if not r.passed)
        total_time = sum(r.duration_ms for r in self.results)

        return {
            "suite": self.name,
            "total": len(self.results),
            "passed": passed,
            "failed": failed,
            "pass_rate": passed / max(len(self.results), 1),
            "total_time_ms": total_time,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# File Enforcer Tests
# ═══════════════════════════════════════════════════════════════════════════════

def run_enforcer_tests(verbose: bool = False) -> Tuple[bool, Dict[str, Any]]:
    """
    Run comprehensive File Enforcer tests.

    Returns: (all_passed, summary_dict)
    """
    # Import here to test import works
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from core.file_enforcer import FileEnforcer, FileLock, get_cmdline_sha256, sha256_str

    suite = TestSuite(name="FileEnforcerTests", verbose=verbose)

    # Create isolated test environment
    with tempfile.TemporaryDirectory() as tmpdir:
        test_root = Path(tmpdir)
        enforcer = FileEnforcer(root=test_root, owner="TestOwner")

        # ═══════════════════════════════════════════════════════════════════
        # Test 1: GetCommandLineW SSoT
        # ═══════════════════════════════════════════════════════════════════

        def test_cmdline_sha256() -> bool:
            hash1 = get_cmdline_sha256()
            hash2 = get_cmdline_sha256()
            # Must be deterministic
            assert hash1 == hash2, f"Non-deterministic: {hash1} != {hash2}"
            # Must have sha256: prefix
            assert hash1.startswith("sha256:"), f"Missing prefix: {hash1}"
            # Must be 64 hex chars after prefix
            hex_part = hash1.replace("sha256:", "")
            assert len(hex_part) == 64, f"Wrong length: {len(hex_part)}"
            assert all(c in "0123456789abcdef" for c in hex_part), "Invalid hex"
            return True

        suite.run_test("cmdline_sha256_deterministic", test_cmdline_sha256)

        # ═══════════════════════════════════════════════════════════════════
        # Test 2: Atomic Write
        # ═══════════════════════════════════════════════════════════════════

        def test_atomic_write() -> bool:
            content = "print('hello world')"
            result = enforcer.write_text_atomic(Path("test_atomic.py"), content)
            assert result.success, f"Write failed: {result.error}"
            assert result.path.exists(), "File not created"
            assert result.sha256.startswith("sha256:"), "No hash"
            return True

        suite.run_test("atomic_write_basic", test_atomic_write)

        # ═══════════════════════════════════════════════════════════════════
        # Test 3: Signature Injection
        # ═══════════════════════════════════════════════════════════════════

        def test_signature_injection() -> bool:
            content, error = enforcer.read_text(Path("test_atomic.py"))
            assert error is None, f"Read failed: {error}"
            assert "=== AI SIGNATURE ===" in content, "No signature header"
            assert "Created by: TestOwner" in content, "Wrong owner in signature"
            assert "=== END SIGNATURE ===" in content, "No signature footer"
            return True

        suite.run_test("signature_injection", test_signature_injection)

        # ═══════════════════════════════════════════════════════════════════
        # Test 4: Path Traversal Blocked
        # ═══════════════════════════════════════════════════════════════════

        def test_path_traversal_blocked() -> bool:
            result = enforcer.write_text_atomic(Path("../escape.txt"), "bad")
            assert not result.success, "Path traversal should be blocked"
            assert "escapes" in result.error.lower(), f"Wrong error: {result.error}"
            return True

        suite.run_test("path_traversal_blocked", test_path_traversal_blocked)

        def test_path_traversal_dotdot() -> bool:
            result = enforcer.write_text_atomic(Path("foo/../../../etc/passwd"), "bad")
            assert not result.success, "Path traversal should be blocked"
            return True

        suite.run_test("path_traversal_dotdot", test_path_traversal_dotdot)

        # ═══════════════════════════════════════════════════════════════════
        # Test 5: Ownership Validation
        # ═══════════════════════════════════════════════════════════════════

        def test_ownership_validation() -> bool:
            # Create file as TestOwner
            enforcer.write_text_atomic(Path("owned.py"), "x = 1", owner="TestOwner")

            # Try to modify as different owner
            enforcer2 = FileEnforcer(root=test_root, owner="Attacker")
            result = enforcer2.write_text_atomic(Path("owned.py"), "x = 'hacked'")
            assert not result.success, "Should deny non-owner write"
            assert "ownership" in result.error.lower(), f"Wrong error: {result.error}"
            return True

        suite.run_test("ownership_validation", test_ownership_validation)

        # ═══════════════════════════════════════════════════════════════════
        # Test 6: sha256 JSONL
        # ═══════════════════════════════════════════════════════════════════

        def test_jsonl_sha256_write() -> bool:
            result = enforcer.append_jsonl_sha256(
                Path("test.jsonl"),
                {"event": "test", "value": 123},
            )
            assert result.success, f"Append failed: {result.error}"
            assert result.sha256.startswith("sha256:"), "No hash"
            return True

        suite.run_test("jsonl_sha256_write", test_jsonl_sha256_write)

        def test_jsonl_sha256_read() -> bool:
            # Append more records
            enforcer.append_jsonl_sha256(Path("test.jsonl"), {"event": "test2"})
            enforcer.append_jsonl_sha256(Path("test.jsonl"), {"event": "test3"})

            records, errors = enforcer.read_jsonl_sha256(Path("test.jsonl"))
            assert len(errors) == 0, f"Read errors: {errors}"
            assert len(records) == 3, f"Expected 3 records, got {len(records)}"
            assert records[0]["event"] == "test", "First record wrong"
            assert records[2]["event"] == "test3", "Last record wrong"
            return True

        suite.run_test("jsonl_sha256_read", test_jsonl_sha256_read)

        def test_jsonl_integrity() -> bool:
            # Tamper with file
            jsonl_path = test_root / "tampered.jsonl"
            jsonl_path.write_text('sha256:0000000000000000000000000000000000000000000000000000000000000000:{"bad":"data"}\n')

            records, errors = enforcer.read_jsonl_sha256(Path("tampered.jsonl"))
            # Should detect tamper
            assert len(records) == 0, "Should reject tampered data"
            assert len(errors) > 0, "Should report error"
            assert "mismatch" in errors[0].lower(), f"Wrong error: {errors[0]}"
            return True

        suite.run_test("jsonl_integrity_tamper_detect", test_jsonl_integrity)

        # ═══════════════════════════════════════════════════════════════════
        # Test 7: .env Append-Only
        # ═══════════════════════════════════════════════════════════════════

        def test_env_append() -> bool:
            result = enforcer.env_append_line(Path(".env"), "MY_KEY", "my_value")
            assert result.success, f"Append failed: {result.error}"

            content = (test_root / ".env").read_text()
            assert "MY_KEY=my_value" in content, "Key not found"
            return True

        suite.run_test("env_append_basic", test_env_append)

        def test_env_append_only() -> bool:
            # Try to overwrite existing key
            result = enforcer.env_append_line(Path(".env"), "MY_KEY", "new_value")
            assert not result.success, "Should deny duplicate key"
            assert "append-only" in result.error.lower(), f"Wrong error: {result.error}"

            # Verify original value preserved
            content = (test_root / ".env").read_text()
            assert "MY_KEY=my_value" in content, "Original value lost"
            assert "MY_KEY=new_value" not in content, "Value was overwritten!"
            return True

        suite.run_test("env_append_only_enforced", test_env_append_only)

        def test_env_invalid_key() -> bool:
            result = enforcer.env_append_line(Path(".env"), "invalid-key", "value")
            assert not result.success, "Should reject invalid key format"
            return True

        suite.run_test("env_invalid_key_rejected", test_env_invalid_key)

        # ═══════════════════════════════════════════════════════════════════
        # Test 8: Protected Directories
        # ═══════════════════════════════════════════════════════════════════

        def test_protected_dir_git() -> bool:
            result = enforcer.write_text_atomic(Path(".git/config"), "bad")
            assert not result.success, "Should block .git"
            return True

        suite.run_test("protected_dir_git_blocked", test_protected_dir_git)

        def test_protected_dir_pycache() -> bool:
            result = enforcer.write_text_atomic(Path("__pycache__/test.pyc"), "bad")
            assert not result.success, "Should block __pycache__"
            return True

        suite.run_test("protected_dir_pycache_blocked", test_protected_dir_pycache)

        # ═══════════════════════════════════════════════════════════════════
        # Test 9: Extension Whitelist
        # ═══════════════════════════════════════════════════════════════════

        def test_extension_whitelist() -> bool:
            # Allowed extension
            result = enforcer.write_text_atomic(Path("test.json"), '{"ok": true}', add_signature=False)
            assert result.success, f"JSON should be allowed: {result.error}"

            # Disallowed extension
            result = enforcer.write_text_atomic(Path("test.exe"), "bad", add_signature=False)
            assert not result.success, "EXE should be blocked"
            assert "extension" in result.error.lower(), f"Wrong error: {result.error}"
            return True

        suite.run_test("extension_whitelist", test_extension_whitelist)

        # ═══════════════════════════════════════════════════════════════════
        # Test 10: Delete with Ownership
        # ═══════════════════════════════════════════════════════════════════

        def test_delete_with_ownership() -> bool:
            # Create file
            enforcer.write_text_atomic(Path("to_delete.py"), "x = 1")

            # Delete as owner
            result = enforcer.delete(Path("to_delete.py"), owner="TestOwner")
            assert result.success, f"Delete failed: {result.error}"
            assert not (test_root / "to_delete.py").exists(), "File still exists"
            return True

        suite.run_test("delete_with_ownership", test_delete_with_ownership)

        def test_delete_wrong_owner() -> bool:
            # Create file as TestOwner
            enforcer.write_text_atomic(Path("protected.py"), "secret = 1")

            # Try to delete as different owner
            enforcer2 = FileEnforcer(root=test_root, owner="Attacker")
            result = enforcer2.delete(Path("protected.py"), owner="Attacker")
            assert not result.success, "Should deny non-owner delete"
            assert (test_root / "protected.py").exists(), "File was deleted!"
            return True

        suite.run_test("delete_wrong_owner_denied", test_delete_wrong_owner)

        # ═══════════════════════════════════════════════════════════════════
        # Test 11: FileLock
        # ═══════════════════════════════════════════════════════════════════

        def test_file_lock() -> bool:
            lock_path = test_root / "locktest.txt"

            with FileLock(lock_path) as lock:
                assert lock._fd is not None, "Lock not acquired"
                assert lock.lock_path.exists(), "Lock file not created"

            # Lock should be released
            assert not lock.lock_path.exists(), "Lock file not cleaned up"
            return True

        suite.run_test("file_lock_basic", test_file_lock)

        # ═══════════════════════════════════════════════════════════════════
        # Test 12: Audit Log
        # ═══════════════════════════════════════════════════════════════════

        def test_audit_log() -> bool:
            summary = enforcer.get_audit_summary()
            assert summary["total"] > 0, "No audit entries"
            assert "write_success" in summary["actions"], "No write success entries"
            return True

        suite.run_test("audit_log_populated", test_audit_log)

        def test_audit_integrity() -> bool:
            # Read audit log and verify sha256 prefixes
            audit_path = test_root / "state" / "enforcer_audit.jsonl"
            if not audit_path.exists():
                return False

            content = audit_path.read_text()
            for line in content.strip().split("\n"):
                if not line:
                    continue
                assert line.startswith("sha256:"), f"Audit line missing prefix: {line[:50]}"
                parts = line.split(":", 2)
                assert len(parts) == 3, f"Invalid audit line format"
                # Verify hash
                expected_hash = parts[1]
                actual_hash = sha256_str(parts[2])
                assert expected_hash == actual_hash, f"Audit hash mismatch"
            return True

        suite.run_test("audit_log_integrity", test_audit_integrity)

        # ═══════════════════════════════════════════════════════════════════
        # Test 13: Hash Validation on Read
        # ═══════════════════════════════════════════════════════════════════

        def test_read_hash_validation() -> bool:
            # Write file
            result = enforcer.write_text_atomic(Path("hashtest.txt"), "test content", add_signature=False)
            correct_hash = result.sha256

            # Read with correct hash
            content, error = enforcer.read_text(Path("hashtest.txt"), validate_sha256=correct_hash)
            assert error is None, f"Read with correct hash failed: {error}"

            # Read with wrong hash
            content, error = enforcer.read_text(Path("hashtest.txt"), validate_sha256="sha256:wrong")
            assert error is not None, "Should fail with wrong hash"
            assert "mismatch" in error.lower(), f"Wrong error: {error}"
            return True

        suite.run_test("read_hash_validation", test_read_hash_validation)

        # ═══════════════════════════════════════════════════════════════════
        # Test 14: List Owned Files
        # ═══════════════════════════════════════════════════════════════════

        def test_list_owned() -> bool:
            # Create several files
            enforcer.write_text_atomic(Path("owned1.py"), "x = 1")
            enforcer.write_text_atomic(Path("owned2.py"), "x = 2")

            files = enforcer.list_owned("TestOwner", "**/*.py")
            assert len(files) >= 2, f"Expected at least 2 files, got {len(files)}"
            return True

        suite.run_test("list_owned_files", test_list_owned)

        # ═══════════════════════════════════════════════════════════════════
        # Test 15: Binary Write
        # ═══════════════════════════════════════════════════════════════════

        def test_binary_write() -> bool:
            data = b"\x00\x01\x02\x03\xff\xfe"
            result = enforcer.write_bytes_atomic(Path("binary.dat"), data)
            # Should fail - .dat not in allowed extensions
            assert not result.success, "Should block .dat extension"
            return True

        suite.run_test("binary_extension_blocked", test_binary_write)

    # Summary
    summary = suite.summary()
    all_passed = summary["failed"] == 0

    return all_passed, summary


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    """CLI entry point."""
    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    print("=" * 60)
    print("FILE ENFORCER SELF-TEST")
    print("=" * 60)
    print()

    all_passed, summary = run_enforcer_tests(verbose=verbose)

    print()
    print("=" * 60)
    print(f"RESULTS: {summary['passed']}/{summary['total']} passed ({summary['pass_rate']:.1%})")
    print(f"Time: {summary['total_time_ms']:.1f}ms")
    print("=" * 60)

    if all_passed:
        print("\n[SUCCESS] All File Enforcer tests passed!")
        print("Sheriff is operational. Filesystem security is ACTIVE.")
    else:
        print(f"\n[FAILURE] {summary['failed']} test(s) failed!")
        print("File Enforcer has issues. DO NOT deploy to production.")
        sys.exit(1)


if __name__ == "__main__":
    main()
