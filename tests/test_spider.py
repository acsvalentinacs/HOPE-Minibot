# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T14:00:00Z
# Purpose: News Spider unit tests (stdlib unittest, no network)
# === END SIGNATURE ===
"""
Unit Tests for News Spider Module (stdlib-only)

Tests cover:
- Source configuration loading and validation
- RSS/Atom XML parsing
- Binance announcement JSON parsing
- Deduplication store operations
- Collector orchestration (mocked network)

Run: python -m unittest tests.test_spider -v
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.spider.sources import (
    SourceConfig,
    SourceType,
    SourceLoadError,
    SourceValidationError,
    load_sources,
    get_enabled_sources,
    _extract_host,
)
from core.spider.parser import (
    RSSItem,
    ParseError,
    parse_rss_xml,
    parse_binance_announcements,
    _strip_html,
    _parse_rfc822_date,
)
from core.spider.dedup import DedupStore
from core.spider.collector import (
    NewsCollector,
    CollectorMode,
    CollectorResult,
)


class TestHostExtraction(unittest.TestCase):
    """Tests for URL host extraction."""

    def test_simple_url(self):
        """Extract host from simple URL."""
        self.assertEqual(_extract_host("https://example.com/path"), "example.com")

    def test_url_with_port(self):
        """Extract host ignoring port."""
        self.assertEqual(_extract_host("https://example.com:8080/path"), "example.com")

    def test_url_with_subdomain(self):
        """Extract host with subdomain."""
        self.assertEqual(_extract_host("https://www.example.com/"), "www.example.com")

    def test_url_uppercase(self):
        """Host is normalized to lowercase."""
        self.assertEqual(_extract_host("https://EXAMPLE.COM/"), "example.com")

    def test_empty_url_raises(self):
        """Empty URL raises error."""
        with self.assertRaises(SourceValidationError):
            _extract_host("")


class TestSourceConfigLoading(unittest.TestCase):
    """Tests for source configuration loading."""

    def setUp(self):
        """Create temp directory."""
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        """Clean up temp files."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_load_valid_registry(self):
        """Valid registry loads successfully."""
        registry = {
            "sources": [
                {
                    "id": "test_rss",
                    "name": "Test RSS",
                    "type": "rss",
                    "url": "https://example.com/feed.xml",
                    "enabled": True,
                }
            ]
        }
        path = Path(self.temp_dir) / "sources.json"
        path.write_text(json.dumps(registry), encoding="utf-8")

        sources = load_sources(registry_path=path)

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].id, "test_rss")
        self.assertEqual(sources[0].host, "example.com")
        self.assertEqual(sources[0].source_type, SourceType.RSS)

    def test_load_missing_file_raises(self):
        """Missing registry raises SourceLoadError."""
        path = Path(self.temp_dir) / "nonexistent.json"

        with self.assertRaises(SourceLoadError) as ctx:
            load_sources(registry_path=path)
        self.assertIn("not found", str(ctx.exception))

    def test_load_invalid_json_raises(self):
        """Invalid JSON raises SourceLoadError."""
        path = Path(self.temp_dir) / "invalid.json"
        path.write_text("{invalid json}", encoding="utf-8")

        with self.assertRaises(SourceLoadError):
            load_sources(registry_path=path)

    def test_load_missing_sources_key_raises(self):
        """Registry without 'sources' key raises error."""
        path = Path(self.temp_dir) / "bad.json"
        path.write_text('{"other": []}', encoding="utf-8")

        with self.assertRaises(SourceValidationError) as ctx:
            load_sources(registry_path=path)
        self.assertIn("sources", str(ctx.exception))

    def test_load_missing_required_field_raises(self):
        """Source missing required field raises error."""
        registry = {
            "sources": [
                {"id": "test", "name": "Test"}  # Missing type, url
            ]
        }
        path = Path(self.temp_dir) / "sources.json"
        path.write_text(json.dumps(registry), encoding="utf-8")

        with self.assertRaises(SourceValidationError) as ctx:
            load_sources(registry_path=path)
        self.assertIn("missing required", str(ctx.exception).lower())

    def test_load_invalid_type_raises(self):
        """Invalid source type raises error."""
        registry = {
            "sources": [
                {
                    "id": "test",
                    "name": "Test",
                    "type": "invalid_type",
                    "url": "https://example.com/"
                }
            ]
        }
        path = Path(self.temp_dir) / "sources.json"
        path.write_text(json.dumps(registry), encoding="utf-8")

        with self.assertRaises(SourceValidationError) as ctx:
            load_sources(registry_path=path)
        self.assertIn("invalid type", str(ctx.exception).lower())


class TestGetEnabledSources(unittest.TestCase):
    """Tests for filtering enabled sources against AllowList."""

    @patch('core.spider.sources.get_allowlist')
    def test_filter_allowed_sources(self, mock_allowlist):
        """Only sources with allowed hosts are returned."""
        mock_al = MagicMock()
        mock_al.is_allowed.side_effect = lambda h: h == "allowed.com"
        mock_allowlist.return_value = mock_al

        sources = [
            SourceConfig(
                id="allowed", name="Allowed", source_type=SourceType.RSS,
                url="https://allowed.com/feed", host="allowed.com", enabled=True
            ),
            SourceConfig(
                id="denied", name="Denied", source_type=SourceType.RSS,
                url="https://denied.com/feed", host="denied.com", enabled=True
            ),
        ]

        # LENIENT mode: skip denied
        enabled = get_enabled_sources(sources, strict_mode=False)

        self.assertEqual(len(enabled), 1)
        self.assertEqual(enabled[0].id, "allowed")

    @patch('core.spider.sources.get_allowlist')
    def test_strict_mode_raises_on_denied(self, mock_allowlist):
        """STRICT mode raises error for denied hosts."""
        from core.net.net_policy import FatalPolicyError

        mock_al = MagicMock()
        mock_al.is_allowed.return_value = False
        mock_allowlist.return_value = mock_al

        sources = [
            SourceConfig(
                id="denied", name="Denied", source_type=SourceType.RSS,
                url="https://denied.com/feed", host="denied.com", enabled=True
            ),
        ]

        with self.assertRaises(FatalPolicyError) as ctx:
            get_enabled_sources(sources, strict_mode=True)
        self.assertIn("STRICT MODE", str(ctx.exception))

    @patch('core.spider.sources.get_allowlist')
    def test_disabled_sources_excluded(self, mock_allowlist):
        """Disabled sources are not included."""
        mock_al = MagicMock()
        mock_al.is_allowed.return_value = True
        mock_allowlist.return_value = mock_al

        sources = [
            SourceConfig(
                id="enabled", name="Enabled", source_type=SourceType.RSS,
                url="https://example.com/feed", host="example.com", enabled=True
            ),
            SourceConfig(
                id="disabled", name="Disabled", source_type=SourceType.RSS,
                url="https://example.com/other", host="example.com", enabled=False
            ),
        ]

        enabled = get_enabled_sources(sources, strict_mode=False)

        self.assertEqual(len(enabled), 1)
        self.assertEqual(enabled[0].id, "enabled")


class TestRSSParsing(unittest.TestCase):
    """Tests for RSS XML parsing."""

    def test_parse_rss2_feed(self):
        """Parse standard RSS 2.0 feed."""
        xml = b'''<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
            <channel>
                <title>Test Feed</title>
                <item>
                    <title>Test Article</title>
                    <link>https://example.com/article1</link>
                    <pubDate>Mon, 25 Jan 2026 12:00:00 +0000</pubDate>
                    <description>Article description</description>
                </item>
            </channel>
        </rss>'''

        items = parse_rss_xml(xml, "test_source")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Test Article")
        self.assertEqual(items[0].link, "https://example.com/article1")
        self.assertEqual(items[0].source_id, "test_source")
        self.assertIsNotNone(items[0].published_utc)

    def test_parse_atom_feed(self):
        """Parse Atom feed format."""
        xml = b'''<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
            <title>Atom Feed</title>
            <entry>
                <title>Atom Entry</title>
                <link href="https://example.com/entry1" rel="alternate"/>
                <published>2026-01-25T12:00:00Z</published>
                <summary>Entry summary</summary>
            </entry>
        </feed>'''

        items = parse_rss_xml(xml, "atom_source")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Atom Entry")
        self.assertEqual(items[0].link, "https://example.com/entry1")

    def test_parse_invalid_xml_raises(self):
        """Invalid XML raises ParseError."""
        xml = b'<invalid xml'

        with self.assertRaises(ParseError):
            parse_rss_xml(xml, "test")

    def test_parse_unknown_format_raises(self):
        """Unknown root element raises ParseError."""
        xml = b'<?xml version="1.0"?><unknown><data/></unknown>'

        with self.assertRaises(ParseError) as ctx:
            parse_rss_xml(xml, "test")
        self.assertIn("Unrecognized feed format", str(ctx.exception))

    def test_html_stripped_from_content(self):
        """HTML tags are stripped from title and description."""
        xml = b'''<?xml version="1.0"?>
        <rss version="2.0">
            <channel>
                <item>
                    <title>&lt;b&gt;Bold Title&lt;/b&gt;</title>
                    <link>https://example.com/1</link>
                    <description>&lt;p&gt;Paragraph&lt;/p&gt;</description>
                </item>
            </channel>
        </rss>'''

        items = parse_rss_xml(xml, "test")

        self.assertEqual(items[0].title, "Bold Title")
        self.assertEqual(items[0].description, "Paragraph")


class TestBinanceAnnouncementParsing(unittest.TestCase):
    """Tests for Binance announcement JSON parsing."""

    def test_parse_valid_announcements(self):
        """Parse standard Binance announcement format."""
        json_data = json.dumps({
            "data": {
                "articles": [
                    {
                        "id": 123,
                        "code": "abc123",
                        "title": "New Listing: XYZ",
                        "releaseDate": 1737799200000  # 2026-01-25 12:00 UTC
                    }
                ]
            }
        }).encode("utf-8")

        items = parse_binance_announcements(json_data, "binance_ann")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "New Listing: XYZ")
        self.assertIn("abc123", items[0].link)
        self.assertEqual(items[0].source_id, "binance_ann")
        self.assertIsNotNone(items[0].published_utc)

    def test_parse_empty_articles(self):
        """Empty articles array returns empty list."""
        json_data = json.dumps({"data": {"articles": []}}).encode("utf-8")

        items = parse_binance_announcements(json_data, "test")

        self.assertEqual(len(items), 0)

    def test_parse_invalid_json_raises(self):
        """Invalid JSON raises ParseError."""
        with self.assertRaises(ParseError):
            parse_binance_announcements(b'{invalid}', "test")


class TestStripHTML(unittest.TestCase):
    """Tests for HTML stripping utility."""

    def test_strip_tags(self):
        """HTML tags are removed."""
        self.assertEqual(_strip_html("<p>Hello</p>"), "Hello")

    def test_unescape_entities(self):
        """HTML entities are unescaped."""
        self.assertEqual(_strip_html("&amp; &lt; &gt;"), "& < >")

    def test_normalize_whitespace(self):
        """Multiple whitespace normalized to single space."""
        self.assertEqual(_strip_html("Hello   World\n\tTest"), "Hello World Test")

    def test_empty_string(self):
        """Empty string returns empty."""
        self.assertEqual(_strip_html(""), "")


class TestDateParsing(unittest.TestCase):
    """Tests for date parsing."""

    def test_rfc822_date(self):
        """RFC 822 date format parsed correctly."""
        dt = _parse_rfc822_date("Mon, 25 Jan 2026 12:00:00 +0000")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 1)
        self.assertEqual(dt.day, 25)

    def test_iso8601_date(self):
        """ISO 8601 date format parsed correctly."""
        dt = _parse_rfc822_date("2026-01-25T12:00:00Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)

    def test_invalid_date_returns_none(self):
        """Invalid date returns None."""
        dt = _parse_rfc822_date("not a date")
        self.assertIsNone(dt)


class TestDedupStore(unittest.TestCase):
    """Tests for deduplication store."""

    def setUp(self):
        """Create temp directory."""
        self.temp_dir = tempfile.mkdtemp()
        self.store_path = Path(self.temp_dir) / "dedup.jsonl"

    def tearDown(self):
        """Clean up temp files."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_add_new_item_returns_true(self):
        """Adding new item returns True."""
        store = DedupStore(store_path=self.store_path)
        result = store.add("item1", "source1", "https://example.com/1")
        self.assertTrue(result)

    def test_add_duplicate_returns_false(self):
        """Adding duplicate item returns False."""
        store = DedupStore(store_path=self.store_path)
        store.add("item1", "source1")
        result = store.add("item1", "source1")
        self.assertFalse(result)

    def test_contains_added_item(self):
        """Contains returns True for added item."""
        store = DedupStore(store_path=self.store_path)
        store.add("item1", "source1")
        self.assertTrue(store.contains("item1"))

    def test_not_contains_unknown_item(self):
        """Contains returns False for unknown item."""
        store = DedupStore(store_path=self.store_path)
        self.assertFalse(store.contains("unknown"))

    def test_persistence_across_instances(self):
        """Items persist across store instances."""
        store1 = DedupStore(store_path=self.store_path)
        store1.add("item1", "source1")

        store2 = DedupStore(store_path=self.store_path)
        self.assertTrue(store2.contains("item1"))

    def test_count_returns_item_count(self):
        """Count returns number of items."""
        store = DedupStore(store_path=self.store_path)
        store.add("item1", "source1")
        store.add("item2", "source1")
        self.assertEqual(store.count(), 2)


class TestCollectorResult(unittest.TestCase):
    """Tests for collector result."""

    def test_is_success_when_no_errors(self):
        """is_success returns True when no errors."""
        result = CollectorResult(
            mode=CollectorMode.STRICT,
            started_utc=datetime.now(timezone.utc),
            sources_success=3,
            sources_failed=0,
        )
        self.assertTrue(result.is_success())

    def test_is_not_success_on_fatal_error(self):
        """is_success returns False on fatal error."""
        result = CollectorResult(
            mode=CollectorMode.STRICT,
            started_utc=datetime.now(timezone.utc),
            fatal_error="Something failed",
        )
        self.assertFalse(result.is_success())

    def test_is_not_success_on_failed_sources(self):
        """is_success returns False when sources failed."""
        result = CollectorResult(
            mode=CollectorMode.LENIENT,
            started_utc=datetime.now(timezone.utc),
            sources_success=2,
            sources_failed=1,
        )
        self.assertFalse(result.is_success())

    def test_to_dict_serializable(self):
        """to_dict produces JSON-serializable output."""
        result = CollectorResult(
            mode=CollectorMode.STRICT,
            started_utc=datetime.now(timezone.utc),
            finished_utc=datetime.now(timezone.utc),
            total_items=10,
            new_items=5,
        )
        d = result.to_dict()
        # Should not raise
        json.dumps(d)
        self.assertEqual(d["total_items"], 10)


class TestNewsCollector(unittest.TestCase):
    """Tests for news collector orchestration."""

    def setUp(self):
        """Create temp directory."""
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        """Clean up temp files."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch('core.spider.collector.http_get')
    @patch('core.spider.sources.get_allowlist')
    def test_collect_success(self, mock_allowlist, mock_http):
        """Successful collection returns result."""
        # Mock allowlist
        mock_al = MagicMock()
        mock_al.is_allowed.return_value = True
        mock_allowlist.return_value = mock_al

        # Mock HTTP response
        rss_content = b'''<?xml version="1.0"?>
        <rss version="2.0">
            <channel>
                <item>
                    <title>Test</title>
                    <link>https://example.com/1</link>
                </item>
            </channel>
        </rss>'''
        mock_http.return_value = (200, rss_content, "https://example.com/feed")

        # Create source
        sources = [
            SourceConfig(
                id="test", name="Test", source_type=SourceType.RSS,
                url="https://example.com/feed", host="example.com", enabled=True
            )
        ]

        # Run collection
        dedup = DedupStore(store_path=Path(self.temp_dir) / "dedup.jsonl")
        output = Path(self.temp_dir) / "items.jsonl"
        collector = NewsCollector(
            mode=CollectorMode.LENIENT,
            dedup_store=dedup,
            output_path=output,
        )
        result = collector.collect(sources=sources, dry_run=True)

        self.assertEqual(result.sources_attempted, 1)
        self.assertEqual(result.sources_success, 1)
        self.assertEqual(result.total_items, 1)

    @patch('core.spider.collector.http_get')
    @patch('core.spider.sources.get_allowlist')
    def test_collect_strict_mode_stops_on_error(self, mock_allowlist, mock_http):
        """STRICT mode stops on first error (only attempts first source)."""
        from core.net.http_client import EgressDeniedError
        from core.net.audit_log import AuditReason

        mock_al = MagicMock()
        mock_al.is_allowed.return_value = True
        mock_allowlist.return_value = mock_al

        # Mock HTTP to raise error
        mock_http.side_effect = EgressDeniedError(
            host="example.com",
            reason=AuditReason.HOST_NOT_IN_ALLOWLIST,
            request_id="test",
        )

        sources = [
            SourceConfig(
                id="test1", name="Test1", source_type=SourceType.RSS,
                url="https://example.com/feed1", host="example.com", enabled=True
            ),
            SourceConfig(
                id="test2", name="Test2", source_type=SourceType.RSS,
                url="https://example.com/feed2", host="example.com", enabled=True
            ),
        ]

        dedup = DedupStore(store_path=Path(self.temp_dir) / "dedup.jsonl")
        collector = NewsCollector(
            mode=CollectorMode.STRICT,
            dedup_store=dedup,
        )

        # STRICT mode should stop after first failed source
        result = collector.collect(sources=sources, dry_run=True)

        # Only first source should be attempted before stopping
        self.assertEqual(result.sources_attempted, 1)
        self.assertEqual(result.sources_failed, 1)
        self.assertIsNotNone(result.fatal_error)
        self.assertIn("test1", result.fatal_error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
