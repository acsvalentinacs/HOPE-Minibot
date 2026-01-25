# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T12:30:00Z
# Purpose: Unit tests for Egress Policy (stdlib unittest, no network)
# === END SIGNATURE ===
"""
Unit Tests for Egress Policy Module (stdlib-only)

Tests cover:
- AllowList loading and validation
- Host matching (ALLOW/DENY)
- HTTP client egress enforcement
- Audit log recording

Run: python -m unittest tests.test_net_policy -v
"""

import os
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from io import BytesIO

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.net.net_policy import (
    AllowList,
    load_allowlist,
    validate_host,
    FatalPolicyError,
    PolicyValidationError,
    _normalize_host,
)
from core.net.audit_log import (
    append_audit_record,
    read_audit_log,
    AuditAction,
    AuditReason,
)


class TestHostValidation(unittest.TestCase):
    """Tests for hostname validation."""

    def test_valid_simple_host(self):
        """Simple hostname validates correctly."""
        self.assertEqual(validate_host("example.com"), "example.com")

    def test_valid_subdomain(self):
        """Subdomain validates correctly."""
        self.assertEqual(validate_host("api.binance.com"), "api.binance.com")

    def test_valid_with_numbers(self):
        """Hostname with numbers validates correctly."""
        self.assertEqual(validate_host("api1.binance.com"), "api1.binance.com")

    def test_normalize_uppercase(self):
        """Uppercase is normalized to lowercase."""
        self.assertEqual(validate_host("API.Binance.COM"), "api.binance.com")

    def test_normalize_trailing_dot(self):
        """Trailing dot is removed."""
        self.assertEqual(validate_host("example.com."), "example.com")

    def test_reject_scheme(self):
        """URL with scheme is rejected."""
        with self.assertRaises(PolicyValidationError) as ctx:
            validate_host("https://example.com")
        self.assertIn("scheme", str(ctx.exception).lower())

    def test_reject_port(self):
        """Hostname with port is rejected."""
        with self.assertRaises(PolicyValidationError) as ctx:
            validate_host("example.com:8080")
        self.assertIn("port", str(ctx.exception).lower())

    def test_reject_path(self):
        """Hostname with path is rejected."""
        with self.assertRaises(PolicyValidationError) as ctx:
            validate_host("example.com/path")
        self.assertIn("Invalid character '/'", str(ctx.exception))

    def test_reject_wildcard(self):
        """Wildcard is rejected."""
        with self.assertRaises(PolicyValidationError) as ctx:
            validate_host("*.example.com")
        self.assertIn("Invalid character '*'", str(ctx.exception))

    def test_reject_query(self):
        """Query string marker is rejected."""
        with self.assertRaises(PolicyValidationError) as ctx:
            validate_host("example.com?foo")
        self.assertIn("Invalid character '?'", str(ctx.exception))

    def test_reject_empty(self):
        """Empty string is rejected."""
        with self.assertRaises(PolicyValidationError):
            validate_host("")

    def test_reject_whitespace_only(self):
        """Whitespace-only is rejected."""
        with self.assertRaises(PolicyValidationError):
            validate_host("   ")


class TestAllowListLoading(unittest.TestCase):
    """Tests for AllowList file loading."""

    def setUp(self):
        """Create temp directory for test files."""
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        """Clean up temp files."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_load_valid_allowlist(self):
        """Valid AllowList loads successfully."""
        path = Path(self.temp_dir) / "AllowList.txt"
        path.write_text(
            "# Comment\n"
            "api.binance.com\n"
            "api.coingecko.com\n"
            "\n"
            "example.com\n",
            encoding='utf-8'
        )

        allowlist = load_allowlist(path)

        self.assertEqual(allowlist.count, 3)
        self.assertTrue(allowlist.is_allowed("api.binance.com"))
        self.assertTrue(allowlist.is_allowed("api.coingecko.com"))
        self.assertTrue(allowlist.is_allowed("example.com"))

    def test_load_missing_file_fails(self):
        """Missing AllowList raises FatalPolicyError."""
        path = Path(self.temp_dir) / "nonexistent.txt"

        with self.assertRaises(FatalPolicyError) as ctx:
            load_allowlist(path)
        self.assertIn("not found", str(ctx.exception).lower())

    def test_load_empty_file_fails(self):
        """Empty AllowList raises FatalPolicyError."""
        path = Path(self.temp_dir) / "AllowList.txt"
        path.write_text("# Only comments\n\n", encoding='utf-8')

        with self.assertRaises(FatalPolicyError) as ctx:
            load_allowlist(path)
        self.assertIn("empty", str(ctx.exception).lower())

    def test_load_invalid_entry_fails(self):
        """AllowList with invalid entry raises PolicyValidationError."""
        path = Path(self.temp_dir) / "AllowList.txt"
        path.write_text(
            "api.binance.com\n"
            "https://invalid.com\n"  # Invalid: has scheme
            "example.com\n",
            encoding='utf-8'
        )

        with self.assertRaises(PolicyValidationError) as ctx:
            load_allowlist(path)
        self.assertIn("Line 2", str(ctx.exception))


class TestAllowListMatching(unittest.TestCase):
    """Tests for AllowList host matching."""

    def setUp(self):
        """Create AllowList with test hosts."""
        self.allowlist = AllowList(
            hosts={"api.binance.com", "example.com", "sub.domain.org"},
            source_path="/test/AllowList.txt",
            load_time_utc="2026-01-25T12:00:00Z"
        )

    def test_allowed_host_returns_true(self):
        """Host in list returns True."""
        self.assertTrue(self.allowlist.is_allowed("api.binance.com"))

    def test_allowed_host_case_insensitive(self):
        """Matching is case-insensitive."""
        self.assertTrue(self.allowlist.is_allowed("API.BINANCE.COM"))

    def test_denied_host_returns_false(self):
        """Host not in list returns False."""
        self.assertFalse(self.allowlist.is_allowed("evil.com"))

    def test_subdomain_not_matched(self):
        """Subdomain of allowed host is NOT allowed (exact match only)."""
        # api.binance.com is allowed, but sub.api.binance.com is NOT
        self.assertFalse(self.allowlist.is_allowed("sub.api.binance.com"))

    def test_parent_domain_not_matched(self):
        """Parent of allowed host is NOT allowed."""
        # sub.domain.org is allowed, but domain.org is NOT
        self.assertFalse(self.allowlist.is_allowed("domain.org"))

    def test_empty_host_denied(self):
        """Empty string is denied."""
        self.assertFalse(self.allowlist.is_allowed(""))

    def test_none_host_denied(self):
        """None is denied (gracefully)."""
        # Should not crash, just return False
        self.assertFalse(self.allowlist.is_allowed(None))


class TestAuditLog(unittest.TestCase):
    """Tests for audit log recording."""

    def setUp(self):
        """Create temp directory for audit log."""
        self.temp_dir = tempfile.mkdtemp()
        self.audit_path = Path(self.temp_dir) / "test_audit.jsonl"

    def tearDown(self):
        """Clean up temp files."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_append_creates_file(self):
        """First append creates audit file."""
        request_id = append_audit_record(
            action=AuditAction.ALLOW,
            host="api.binance.com",
            reason=AuditReason.HOST_IN_ALLOWLIST,
            url="https://api.binance.com/api/v3/time",
            latency_ms=100,
            process="test",
            audit_path=self.audit_path,
        )

        self.assertTrue(self.audit_path.exists())
        self.assertTrue(len(request_id) > 0)

    def test_append_writes_valid_json(self):
        """Appended record is valid JSON."""
        append_audit_record(
            action=AuditAction.DENY,
            host="evil.com",
            reason=AuditReason.HOST_NOT_IN_ALLOWLIST,
            url="https://evil.com/bad",
            latency_ms=50,
            process="test",
            audit_path=self.audit_path,
        )

        content = self.audit_path.read_text(encoding='utf-8').strip()
        record = json.loads(content)

        self.assertEqual(record["action"], "DENY")
        self.assertEqual(record["host"], "evil.com")
        self.assertEqual(record["reason"], "host_not_in_allowlist")
        self.assertIn("ts_utc", record)
        self.assertIn("request_id", record)

    def test_url_is_hashed_not_stored(self):
        """URL is hashed, not stored in plain text."""
        url = "https://api.binance.com/secret?key=VERYSECRET"

        append_audit_record(
            action=AuditAction.ALLOW,
            host="api.binance.com",
            reason=AuditReason.HOST_IN_ALLOWLIST,
            url=url,
            latency_ms=100,
            process="test",
            audit_path=self.audit_path,
        )

        content = self.audit_path.read_text(encoding='utf-8')
        record = json.loads(content.strip())

        # URL should NOT appear in plain text
        self.assertNotIn("VERYSECRET", content)
        self.assertNotIn("key=", content)

        # url_sha256 should be present
        self.assertIn("url_sha256", record)
        self.assertEqual(len(record["url_sha256"]), 16)  # First 16 chars of sha256

    def test_read_audit_log(self):
        """Read function returns records."""
        # Write two records
        append_audit_record(
            action=AuditAction.ALLOW,
            host="host1.com",
            reason=AuditReason.HOST_IN_ALLOWLIST,
            latency_ms=10,
            process="test",
            audit_path=self.audit_path,
        )
        append_audit_record(
            action=AuditAction.DENY,
            host="host2.com",
            reason=AuditReason.HOST_NOT_IN_ALLOWLIST,
            latency_ms=20,
            process="test",
            audit_path=self.audit_path,
        )

        records = read_audit_log(audit_path=self.audit_path)

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["host"], "host1.com")
        self.assertEqual(records[1]["host"], "host2.com")


class TestHttpClientEnforcement(unittest.TestCase):
    """Tests for HTTP client egress enforcement."""

    def setUp(self):
        """Set up temp AllowList and audit log."""
        self.temp_dir = tempfile.mkdtemp()

        # Create temp AllowList
        self.allowlist_path = Path(self.temp_dir) / "AllowList.txt"
        self.allowlist_path.write_text(
            "api.binance.com\n"
            "allowed.example.com\n",
            encoding='utf-8'
        )

        # Create temp audit log path
        self.audit_path = Path(self.temp_dir) / "audit.jsonl"

        # Clear cached allowlist
        import core.net.net_policy as policy_module
        policy_module._cached_allowlist = None

    def tearDown(self):
        """Clean up."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

        # Clear cached allowlist
        import core.net.net_policy as policy_module
        policy_module._cached_allowlist = None

    @patch('core.net.net_policy.load_allowlist')
    @patch('core.net.http_client.urlopen')
    @patch('core.net.http_client.append_audit_record')
    def test_deny_host_not_in_allowlist(self, mock_audit, mock_urlopen, mock_load):
        """Host not in allowlist is DENIED and urlopen NOT called."""
        from core.net.http_client import http_get, EgressDeniedError

        # Mock AllowList
        mock_allowlist = MagicMock()
        mock_allowlist.is_allowed.return_value = False
        mock_load.return_value = mock_allowlist

        mock_audit.return_value = "test-request-id"

        # Attempt request to denied host
        with self.assertRaises(EgressDeniedError) as ctx:
            http_get("https://evil.com/bad")

        # urlopen should NOT have been called
        mock_urlopen.assert_not_called()

        # Audit should record DENY
        mock_audit.assert_called()
        call_args = mock_audit.call_args
        self.assertEqual(call_args.kwargs['action'], AuditAction.DENY)
        self.assertEqual(call_args.kwargs['reason'], AuditReason.HOST_NOT_IN_ALLOWLIST)

    @patch('core.net.net_policy.load_allowlist')
    @patch('core.net.http_client.urlopen')
    @patch('core.net.http_client.append_audit_record')
    def test_allow_host_in_allowlist(self, mock_audit, mock_urlopen, mock_load):
        """Host in allowlist is ALLOWED and urlopen IS called."""
        from core.net.http_client import http_get

        # Mock AllowList
        mock_allowlist = MagicMock()
        mock_allowlist.is_allowed.return_value = True
        mock_load.return_value = mock_allowlist

        # Mock response with proper read() that returns empty on second call
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.url = "https://api.binance.com/api/v3/time"
        # read() returns content first, then empty to signal EOF
        mock_response.read.side_effect = [b'{"serverTime":123}', b'']
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        mock_audit.return_value = "test-request-id"

        # Attempt request to allowed host
        status, body, url = http_get("https://api.binance.com/api/v3/time")

        # urlopen SHOULD have been called
        mock_urlopen.assert_called_once()

        # Should succeed
        self.assertEqual(status, 200)

    @patch('core.net.net_policy.load_allowlist')
    @patch('core.net.http_client.append_audit_record')
    def test_deny_when_policy_fails_to_load(self, mock_audit, mock_load):
        """Request is DENIED if AllowList cannot be loaded."""
        from core.net.http_client import http_get, EgressDeniedError

        # Mock load failure
        mock_load.side_effect = FatalPolicyError("File not found")
        mock_audit.return_value = "test-request-id"

        with self.assertRaises(EgressDeniedError) as ctx:
            http_get("https://any.host.com/")

        self.assertEqual(ctx.exception.reason, AuditReason.POLICY_LOAD_FAILED)

    @patch('core.net.net_policy.load_allowlist')
    @patch('core.net.http_client.urlopen')
    @patch('core.net.http_client.append_audit_record')
    def test_deny_redirect_to_different_host(self, mock_audit, mock_urlopen, mock_load):
        """Redirect to different host (not in allowlist) is DENIED."""
        from core.net.http_client import http_get, EgressDeniedError

        # Mock AllowList: only original host allowed
        mock_allowlist = MagicMock()

        def is_allowed_side_effect(host):
            return host == "allowed.example.com"

        mock_allowlist.is_allowed.side_effect = is_allowed_side_effect
        mock_load.return_value = mock_allowlist

        # Mock response that redirects to different host
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.url = "https://evil.redirect.com/page"  # Different host!
        mock_response.read.return_value = b'redirected'
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        mock_audit.return_value = "test-request-id"

        with self.assertRaises(EgressDeniedError) as ctx:
            http_get("https://allowed.example.com/")

        self.assertEqual(ctx.exception.reason, AuditReason.REDIRECT_TO_DIFFERENT_HOST)


class TestNormalizeHost(unittest.TestCase):
    """Tests for host normalization."""

    def test_lowercase(self):
        self.assertEqual(_normalize_host("EXAMPLE.COM"), "example.com")

    def test_strip_whitespace(self):
        self.assertEqual(_normalize_host("  example.com  "), "example.com")

    def test_strip_trailing_dot(self):
        self.assertEqual(_normalize_host("example.com."), "example.com")

    def test_combined(self):
        self.assertEqual(_normalize_host("  EXAMPLE.COM.  "), "example.com")


if __name__ == '__main__':
    unittest.main(verbosity=2)
