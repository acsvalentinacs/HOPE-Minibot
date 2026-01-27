# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T21:10:00Z
# Purpose: Integration tests for Phase 2.5 - OHLCV provider, clipboard, exporter
# Security: Test-only, no production impact
# === END SIGNATURE ===
"""
Tests for Phase 2.5 integration components.

Modules tested:
- core.market.klines_provider (KlinesProvider)
- core.strategy_integration (StrategyIntegration with real OHLCV)
- omnichat.src.clipboard (copy_to_clipboard)
- omnichat.src.ddo.exporter (DDOExporter)
"""
import pytest
import time
import numpy as np
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock


class TestKlinesProvider:
    """Tests for KlinesProvider."""

    def test_provider_creates(self):
        """Verify provider can be instantiated."""
        from core.market.klines_provider import KlinesProvider, KlinesConfig

        config = KlinesConfig(cache_ttl_sec=30)
        provider = KlinesProvider(config)
        assert provider is not None
        assert provider.config.cache_ttl_sec == 30

    def test_singleton_works(self):
        """Verify singleton pattern works."""
        from core.market import klines_provider

        # Reset singleton
        klines_provider._provider_instance = None

        p1 = klines_provider.get_klines_provider()
        p2 = klines_provider.get_klines_provider()
        assert p1 is p2

        # Cleanup
        klines_provider._provider_instance = None

    def test_invalid_timeframe_returns_none(self):
        """Verify invalid timeframe returns None (fail-closed)."""
        from core.market.klines_provider import KlinesProvider

        provider = KlinesProvider()
        result = provider.get_klines("BTCUSDT", "invalid")
        assert result is None

    def test_cache_key_generation(self):
        """Verify cache key format."""
        from core.market.klines_provider import KlinesProvider

        provider = KlinesProvider()
        key = provider._cache_key("BTCUSDT", "15m")
        assert key == "BTCUSDT:15m"

    def test_parse_klines_empty_returns_none(self):
        """Verify empty klines returns None."""
        from core.market.klines_provider import KlinesProvider

        provider = KlinesProvider()
        result = provider._parse_klines([])
        assert result is None

    def test_parse_klines_insufficient_returns_none(self):
        """Verify insufficient candles returns None (min 35)."""
        from core.market.klines_provider import KlinesProvider

        provider = KlinesProvider()
        # Only 10 candles
        klines = [[i * 1000, 100, 101, 99, 100, 1000, (i + 1) * 1000] for i in range(10)]
        result = provider._parse_klines(klines)
        assert result is None

    def test_parse_klines_valid(self):
        """Verify valid klines are parsed correctly."""
        from core.market.klines_provider import KlinesProvider

        provider = KlinesProvider()
        # 50 candles
        klines = [
            [i * 60000, str(100 + i * 0.1), str(101 + i * 0.1), str(99 + i * 0.1), str(100.5 + i * 0.1), str(1000 + i), (i + 1) * 60000]
            for i in range(50)
        ]
        result = provider._parse_klines(klines)
        assert result is not None
        assert len(result["closes"]) == 50
        assert result["closes"][0] == pytest.approx(100.5, rel=0.01)


class TestStrategyIntegrationOHLCV:
    """Tests for StrategyIntegration with OHLCV."""

    def test_integration_config_defaults(self):
        """Verify IntegrationConfig defaults."""
        from core.strategy_integration import IntegrationConfig

        config = IntegrationConfig()
        assert config.spot_only is True
        assert config.use_real_ohlcv is True
        assert config.allow_synthetic_fallback is False

    def test_integration_with_synthetic_fallback(self):
        """Verify synthetic fallback works when enabled."""
        from core.strategy_integration import StrategyIntegration, IntegrationConfig

        config = IntegrationConfig(
            use_real_ohlcv=False,  # Don't require real OHLCV
            allow_synthetic_fallback=True,
        )
        integration = StrategyIntegration(config)

        # Mock ticker
        mock_ticker = Mock()
        mock_ticker.price = 50000.0
        mock_ticker.high_24h = 51000.0
        mock_ticker.low_24h = 49000.0
        mock_ticker.volume_24h = 1000000.0

        tickers = {"BTCUSDT": mock_ticker}

        market_data = integration._build_market_data("BTCUSDT", tickers)
        assert market_data is not None
        assert market_data.symbol == "BTCUSDT"
        assert len(market_data.closes) >= 35

    def test_integration_fail_closed_without_real_ohlcv(self):
        """Verify fail-closed when real OHLCV required but unavailable."""
        from core.strategy_integration import StrategyIntegration, IntegrationConfig
        from core.market import klines_provider

        # Mock klines provider to return None
        mock_provider = Mock()
        mock_provider.get_klines.return_value = None

        config = IntegrationConfig(
            use_real_ohlcv=True,
            allow_synthetic_fallback=False,  # Fail-closed
        )
        integration = StrategyIntegration(config)
        integration._klines_provider = mock_provider

        mock_ticker = Mock()
        mock_ticker.price = 50000.0

        tickers = {"BTCUSDT": mock_ticker}

        # Should return None (fail-closed)
        market_data = integration._build_market_data("BTCUSDT", tickers)
        assert market_data is None


class TestClipboard:
    """Tests for clipboard module."""

    def test_clipboard_result_dataclass(self):
        """Verify ClipboardResult structure."""
        from omnichat.src.clipboard import ClipboardResult

        result = ClipboardResult(success=True, method="clip.exe")
        assert result.success is True
        assert result.method == "clip.exe"
        assert result.error is None

    def test_copy_empty_fails(self):
        """Verify empty text fails."""
        from omnichat.src.clipboard import copy_to_clipboard

        result = copy_to_clipboard("")
        assert result.success is False
        assert "Empty" in result.error

    def test_copy_non_string_fails(self):
        """Verify non-string input fails."""
        from omnichat.src.clipboard import copy_to_clipboard

        result = copy_to_clipboard(123)  # type: ignore
        assert result.success is False


class TestDDOExporter:
    """Tests for DDO exporter module."""

    def test_sanitize_text_removes_nulls(self):
        """Verify null bytes are removed."""
        from omnichat.src.ddo.exporter import sanitize_text

        text = "Hello\x00World"
        result = sanitize_text(text)
        assert "\x00" not in result
        assert "HelloWorld" == result

    def test_sanitize_text_normalizes_newlines(self):
        """Verify newlines are normalized."""
        from omnichat.src.ddo.exporter import sanitize_text

        text = "Line1\r\nLine2\rLine3"
        result = sanitize_text(text)
        assert "\r" not in result
        assert result == "Line1\nLine2\nLine3"

    def test_sanitize_filename_removes_invalid_chars(self):
        """Verify invalid filename chars are removed."""
        from omnichat.src.ddo.exporter import sanitize_filename

        name = 'file<>:"/\\|?*name.txt'
        result = sanitize_filename(name)
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result
        assert "file" in result

    def test_sanitize_filename_reserved_names(self):
        """Verify reserved Windows names are handled."""
        from omnichat.src.ddo.exporter import sanitize_filename

        result = sanitize_filename("CON")
        assert result.upper() != "CON"
        assert result.startswith("_")

    def test_exporter_creates(self):
        """Verify exporter can be instantiated."""
        from omnichat.src.ddo.exporter import DDOExporter
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = DDOExporter(export_dir=Path(tmpdir))
            assert exporter is not None
            assert exporter.export_dir.exists()

    def test_export_to_markdown(self):
        """Verify Markdown export works."""
        from omnichat.src.ddo.exporter import DDOExporter, DDODiscussion, DDOMessage
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = DDOExporter(export_dir=Path(tmpdir))

            discussion = DDODiscussion(
                topic="Test Topic",
                messages=[
                    DDOMessage(role="user", content="Hello", timestamp=time.time()),
                    DDOMessage(role="claude", content="Hi there!", timestamp=time.time()),
                ],
                started_at=time.time() - 60,
            )

            result = exporter.export_discussion(discussion, format="md")
            assert result.success is True
            assert result.path is not None
            assert result.path.exists()
            assert result.path.suffix == ".md"

            content = result.path.read_text(encoding="utf-8")
            assert "Test Topic" in content
            assert "Hello" in content
            assert "Hi there!" in content

    def test_export_to_json(self):
        """Verify JSON export works."""
        from omnichat.src.ddo.exporter import DDOExporter, DDODiscussion, DDOMessage
        import tempfile
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = DDOExporter(export_dir=Path(tmpdir))

            discussion = DDODiscussion(
                topic="Test Topic JSON",
                messages=[
                    DDOMessage(role="user", content="Test message", timestamp=time.time()),
                ],
                started_at=time.time() - 60,
            )

            result = exporter.export_discussion(discussion, format="json")
            assert result.success is True
            assert result.path.suffix == ".json"

            data = json.loads(result.path.read_text(encoding="utf-8"))
            assert data["topic"] == "Test Topic JSON"
            assert len(data["messages"]) == 1

    def test_export_invalid_format_fails(self):
        """Verify invalid format returns error."""
        from omnichat.src.ddo.exporter import DDOExporter, DDODiscussion
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = DDOExporter(export_dir=Path(tmpdir))

            discussion = DDODiscussion(
                topic="Test",
                messages=[],
                started_at=time.time(),
            )

            result = exporter.export_discussion(discussion, format="xml")
            assert result.success is False
            assert "Unsupported format" in result.error


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
