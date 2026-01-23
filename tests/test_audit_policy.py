# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 20:30:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 20:30:00 UTC
# === END SIGNATURE ===
"""
Pytest tests for audit modules (fail-closed policy enforcement).

Tests ensure that the "no PASS by empty" policy is enforced and
various edge cases are handled correctly.
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add parent directory to path for imports
_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "tools"))

# Import modules using importlib to handle the module structure
import importlib.util

def _import_from_path(module_name: str, file_path: Path):
    """Import a module from file path."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

# Import audit modules
_audit_ai_sig = _import_from_path("audit_ai_signature", _root / "tools" / "audit_ai_signature.py")
_audit_allowlist = _import_from_path("audit_allowlist", _root / "tools" / "audit_allowlist.py")
_audit_no_del = _import_from_path("audit_no_deletions", _root / "tools" / "audit_no_deletions.py")

# Extract needed functions/classes
signature_ok = _audit_ai_sig.signature_ok
filter_auditable_files = _audit_ai_sig.filter_auditable_files
_resolve_inside_root = _audit_ai_sig._resolve_inside_root
PathSecurityError = _audit_ai_sig.PathSecurityError
audit_allowlist = _audit_allowlist.audit_allowlist
HOST_RE = _audit_allowlist.HOST_RE
audit_no_deletions = _audit_no_del.audit_no_deletions


class TestSignatureOk:
    """Tests for signature_ok() function."""

    def test_valid_signature_utc(self):
        """Valid AI signature with UTC format."""
        text = """# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 20:00:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 20:00:00 UTC
# === END SIGNATURE ===
def foo():
    pass
"""
        assert signature_ok(text) is True

    def test_valid_signature_iso(self):
        """Valid AI signature with ISO Z format."""
        text = """# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23T20:00:00Z
# === END SIGNATURE ===
"""
        assert signature_ok(text) is True

    def test_missing_start_marker(self):
        """Missing START marker."""
        text = """# Created by: Claude
# Created at: 2026-01-23 20:00:00 UTC
# === END SIGNATURE ===
"""
        assert signature_ok(text) is False

    def test_missing_end_marker(self):
        """Missing END marker."""
        text = """# === AI SIGNATURE ===
# Created by: Claude
# Created at: 2026-01-23 20:00:00 UTC
"""
        assert signature_ok(text) is False

    def test_created_by_outside_block(self):
        """Created by outside block should fail."""
        text = """# Created by: Claude
# === AI SIGNATURE ===
# Created at: 2026-01-23 20:00:00 UTC
# === END SIGNATURE ===
"""
        assert signature_ok(text) is False

    def test_end_before_start(self):
        """END marker before START should fail."""
        text = """# === END SIGNATURE ===
# === AI SIGNATURE ===
# Created by: Claude
# Created at: 2026-01-23 20:00:00 UTC
"""
        assert signature_ok(text) is False

    def test_empty_file(self):
        """Empty file."""
        assert signature_ok("") is False

    def test_missing_created_at(self):
        """Missing Created at should fail."""
        text = """# === AI SIGNATURE ===
# Created by: Claude
# === END SIGNATURE ===
"""
        assert signature_ok(text) is False


class TestFilterAuditableFiles:
    """Tests for filter_auditable_files() function."""

    def test_py_in_core(self):
        """Python files in core/ are auditable."""
        assert filter_auditable_files(["core/module.py"]) == ["core/module.py"]

    def test_py_in_scripts(self):
        """Python files in scripts/ are auditable."""
        assert filter_auditable_files(["scripts/run.py"]) == ["scripts/run.py"]

    def test_py_in_tools(self):
        """Python files in tools/ are auditable."""
        assert filter_auditable_files(["tools/audit.py"]) == ["tools/audit.py"]

    def test_ps1_in_tools(self):
        """PowerShell files in tools/ are auditable."""
        assert filter_auditable_files(["tools/gate.ps1"]) == ["tools/gate.ps1"]

    def test_ps1_in_core_not_auditable(self):
        """PowerShell files in core/ are NOT auditable."""
        assert filter_auditable_files(["core/script.ps1"]) == []

    def test_py_in_root_not_auditable(self):
        """Python files in root are NOT auditable."""
        assert filter_auditable_files(["config.py"]) == []

    def test_non_py_not_auditable(self):
        """Non-Python/PowerShell files are NOT auditable."""
        assert filter_auditable_files(["core/data.json", "README.md"]) == []

    def test_mixed_files(self):
        """Mixed files - only auditable ones returned."""
        result = filter_auditable_files([
            "core/module.py",
            "README.md",
            "tools/gate.ps1",
            "config.py",
        ])
        assert result == ["core/module.py", "tools/gate.ps1"]


class TestResolveInsideRoot:
    """Tests for _resolve_inside_root() function."""

    def test_valid_relative_path(self, tmp_path):
        """Valid relative path inside root."""
        result = _resolve_inside_root(tmp_path, "core/module.py")
        assert result == tmp_path / "core" / "module.py"

    def test_empty_path_fails(self, tmp_path):
        """Empty path should fail."""
        with pytest.raises(PathSecurityError, match="empty_path"):
            _resolve_inside_root(tmp_path, "")

    def test_absolute_path_fails(self, tmp_path):
        """Absolute path should fail (Windows: C:\\..., Unix-style treated as path_outside_root)."""
        # On Windows, /etc/passwd is treated as relative (no drive letter), but ends up outside root
        # On Unix, it's absolute. Either way, it should fail.
        with pytest.raises(PathSecurityError, match="(absolute_path_not_allowed|path_outside_root)"):
            _resolve_inside_root(tmp_path, "/etc/passwd")

    def test_traversal_outside_root_fails(self, tmp_path):
        """Path traversal outside root should fail."""
        with pytest.raises(PathSecurityError, match="path_outside_root"):
            _resolve_inside_root(tmp_path, "../../../etc/passwd")


class TestAuditAllowlist:
    """Tests for audit_allowlist() function."""

    def test_valid_hosts(self, tmp_path):
        """Valid lowercase hosts should pass."""
        f = tmp_path / "AllowList.txt"
        f.write_text("api.binance.com\napi.coingecko.com\n")
        is_valid, errors, count = audit_allowlist(f)
        assert is_valid is True
        assert errors == []
        assert count == 2

    def test_wildcard_fails(self, tmp_path):
        """Wildcard should fail."""
        f = tmp_path / "AllowList.txt"
        f.write_text("*\n")
        is_valid, errors, _ = audit_allowlist(f)
        assert is_valid is False
        assert any("wildcard_forbidden" in e for e in errors)

    def test_scheme_fails(self, tmp_path):
        """URL with scheme should fail."""
        f = tmp_path / "AllowList.txt"
        f.write_text("https://example.com\n")
        is_valid, errors, _ = audit_allowlist(f)
        assert is_valid is False
        assert any("scheme_forbidden" in e for e in errors)

    def test_path_fails(self, tmp_path):
        """URL with path should fail."""
        f = tmp_path / "AllowList.txt"
        f.write_text("example.com/api/v1\n")
        is_valid, errors, _ = audit_allowlist(f)
        assert is_valid is False
        assert any("path_forbidden" in e for e in errors)

    def test_uppercase_fails(self, tmp_path):
        """Uppercase hostname should fail."""
        f = tmp_path / "AllowList.txt"
        f.write_text("Api.Binance.Com\n")
        is_valid, errors, _ = audit_allowlist(f)
        assert is_valid is False
        assert any("must_be_lowercase" in e for e in errors)

    def test_empty_allowlist_fails(self, tmp_path):
        """Empty allowlist should fail."""
        f = tmp_path / "AllowList.txt"
        f.write_text("# only comments\n")
        is_valid, errors, _ = audit_allowlist(f)
        assert is_valid is False
        assert any("no_entries" in e for e in errors)

    def test_missing_file_fails(self, tmp_path):
        """Missing file should fail."""
        f = tmp_path / "NonExistent.txt"
        is_valid, errors, _ = audit_allowlist(f)
        assert is_valid is False
        assert any("allowlist_not_found" in e for e in errors)

    def test_comments_ignored(self, tmp_path):
        """Comments and empty lines should be ignored."""
        f = tmp_path / "AllowList.txt"
        f.write_text("# comment\n\napi.example.com\n# another comment\n")
        is_valid, errors, count = audit_allowlist(f)
        assert is_valid is True
        assert count == 1


class TestEnvPolicy:
    """Tests for ENV_RE pattern in audit_env_policy.py."""

    ENV_RE = re.compile(r"(^|/)\.env$")

    @pytest.mark.parametrize("line,expected", [
        ("M\t.env", True),
        ("D\t.env", True),
        ("A\t.env", True),
        ("D\tfoo/.env", True),
        ("R100\t.env\t.env.bak", True),
        ("M\tcore/.env", True),
        ("M\t.envrc", False),
        ("M\tconfig.env", False),
        ("M\t.env.local", False),
        ("M\ttest.env", False),
    ])
    def test_env_pattern(self, line, expected):
        """Test ENV_RE pattern matches only .env files."""
        parts = line.split("\t")
        hit = any(self.ENV_RE.search(p.replace("\\", "/")) for p in parts[1:])
        assert hit == expected, f"Pattern test failed for: {line}"


class TestHostRegex:
    """Tests for HOST_RE regex in audit_allowlist.py."""

    @pytest.mark.parametrize("hostname,expected", [
        ("api.binance.com", True),
        ("example.com", True),
        ("sub.domain.example.com", True),
        ("a.b", True),
        ("x", True),
        # Invalid cases
        ("-invalid.com", False),  # starts with hyphen
        ("invalid-.com", False),  # ends with hyphen in segment
        ("", False),
        ("a" * 254, False),  # too long
    ])
    def test_host_regex(self, hostname, expected):
        """Test HOST_RE matches valid hostnames."""
        result = HOST_RE.match(hostname) is not None
        assert result == expected, f"HOST_RE test failed for: {hostname}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
