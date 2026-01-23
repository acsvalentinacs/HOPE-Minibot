# === AI SIGNATURE ===
# Created by: Claude
# Created at: 2026-01-21 15:30:00 UTC
# === END SIGNATURE ===
"""
Tests for nore.market_state module.

Run: pytest tests/test_market_state.py -v
"""
from __future__ import annotations

import pytest

from nore.market_state import (
    MarketState,
    MarketStateError,
    MarketStateMeta,
    NewsItem,
    Ticker,
    Announcement,
)


# Test cmdline_sha256 (valid format)
TEST_CMDLINE = "sha256:" + "0" * 64


class TestTicker:
    """Tests for Ticker dataclass."""

    def test_valid_ticker(self) -> None:
        t = Ticker(symbol="BTCUSDT", price=42000.0, volume=1e9)
        assert t.symbol == "BTCUSDT"
        assert t.price == 42000.0
        assert t.volume == 1e9

    def test_ticker_zero_price(self) -> None:
        """Zero price is valid (e.g., for delisted tokens)."""
        t = Ticker(symbol="DEAD", price=0.0, volume=0.0)
        assert t.price == 0.0

    def test_ticker_empty_symbol_fails(self) -> None:
        with pytest.raises(MarketStateError, match="symbol"):
            Ticker(symbol="", price=100.0, volume=1.0)

    def test_ticker_negative_price_fails(self) -> None:
        with pytest.raises(MarketStateError, match="price"):
            Ticker(symbol="BTC", price=-1.0, volume=1.0)

    def test_ticker_negative_volume_fails(self) -> None:
        with pytest.raises(MarketStateError, match="volume"):
            Ticker(symbol="BTC", price=100.0, volume=-1.0)


class TestNewsItem:
    """Tests for NewsItem dataclass."""

    def test_valid_news_item(self) -> None:
        n = NewsItem(
            title="BTC hits 100k",
            url="https://example.com/news/1",
            published_utc="2026-01-21T10:00:00Z",
            source="coindesk",
        )
        assert n.title == "BTC hits 100k"
        assert n.source == "coindesk"

    def test_news_item_empty_title_fails(self) -> None:
        with pytest.raises(MarketStateError, match="title"):
            NewsItem(title="", url="https://x.com", published_utc="2026-01-21T10:00:00Z", source="x")


class TestMarketState:
    """Tests for MarketState contract and serialization."""

    def test_contract_roundtrip_ok(self) -> None:
        """Create -> serialize -> deserialize -> verify."""
        state = MarketState(
            tickers=(Ticker(symbol="BTCUSDT", price=100.0, volume=1.0),),
            news=(NewsItem(title="t", url="u", published_utc="2026-01-21T00:00:00Z", source="rss"),),
            announcements=(),
        ).with_contract(sources=["binance", "rss"], cmdline_sha256=TEST_CMDLINE)

        # Verify before serialize
        state.verify_contract()

        # Serialize
        d = state.to_dict()
        assert "meta" in d
        assert d["meta"]["sha256"].startswith("sha256:")

        # Deserialize (includes verification)
        state2 = MarketState.from_dict(d)
        state2.verify_contract()

        # Data preserved
        assert len(state2.tickers) == 1
        assert state2.tickers[0].symbol == "BTCUSDT"

    def test_contract_tamper_price_fails(self) -> None:
        """Tampering with price should fail verification."""
        state = MarketState(
            tickers=(Ticker(symbol="BTCUSDT", price=100.0, volume=1.0),),
        ).with_contract(sources=["binance"], cmdline_sha256=TEST_CMDLINE)

        d = state.to_dict()

        # Tamper
        d["tickers"][0]["price"] = 101.0

        with pytest.raises(MarketStateError, match="sha256 contract mismatch"):
            MarketState.from_dict(d)

    def test_contract_tamper_meta_sha256_fails(self) -> None:
        """Tampering with meta.sha256 should fail verification."""
        state = MarketState(
            tickers=(Ticker(symbol="BTCUSDT", price=100.0, volume=1.0),),
        ).with_contract(sources=["binance"], cmdline_sha256=TEST_CMDLINE)

        d = state.to_dict()

        # Tamper sha256
        d["meta"]["sha256"] = "sha256:" + "f" * 64

        with pytest.raises(MarketStateError, match="sha256 contract mismatch"):
            MarketState.from_dict(d)

    def test_empty_state_ok(self) -> None:
        """Empty state (no tickers/news) should still work."""
        state = MarketState().with_contract(sources=["none"], cmdline_sha256=TEST_CMDLINE)
        state.verify_contract()

        d = state.to_dict()
        state2 = MarketState.from_dict(d)
        assert len(state2.tickers) == 0
        assert len(state2.news) == 0

    def test_serialize_without_contract_fails(self) -> None:
        """to_dict() without with_contract() should fail."""
        state = MarketState(tickers=(Ticker(symbol="BTC", price=1.0, volume=1.0),))
        with pytest.raises(MarketStateError, match="without meta"):
            state.to_dict()

    def test_invalid_cmdline_sha256_fails(self) -> None:
        """Invalid cmdline_sha256 format should fail."""
        state = MarketState()

        with pytest.raises(MarketStateError, match="sha256"):
            state.with_contract(sources=["x"], cmdline_sha256="invalid")

        with pytest.raises(MarketStateError, match="sha256"):
            state.with_contract(sources=["x"], cmdline_sha256="sha256:short")

    def test_sources_sorted_and_deduplicated(self) -> None:
        """Sources should be sorted and deduplicated."""
        state = MarketState().with_contract(
            sources=["rss", "binance", "rss", "coingecko"],
            cmdline_sha256=TEST_CMDLINE,
        )
        assert state.meta.sources == ("binance", "coingecko", "rss")

    def test_from_dict_missing_meta_fails(self) -> None:
        """Missing meta should fail."""
        with pytest.raises(MarketStateError, match="Missing meta"):
            MarketState.from_dict({"tickers": [], "news": []})

    def test_from_dict_invalid_type_fails(self) -> None:
        """Non-dict input should fail."""
        with pytest.raises(MarketStateError, match="must be object"):
            MarketState.from_dict([])  # type: ignore


class TestMarketStateIntegration:
    """Integration tests with multiple components."""

    def test_full_pipeline(self) -> None:
        """Full pipeline: create -> contract -> serialize -> verify -> modify -> fail."""
        # Create rich state
        state = MarketState(
            tickers=(
                Ticker("BTCUSDT", 42000.0, 1e9),
                Ticker("ETHUSDT", 2200.0, 5e8),
            ),
            news=(
                NewsItem("News 1", "https://a.com", "2026-01-21T10:00:00Z", "coindesk"),
                NewsItem("News 2", "https://b.com", "2026-01-21T11:00:00Z", "cointelegraph"),
            ),
            announcements=(
                Announcement("Listing", "https://binance.com/ann/1", "2026-01-21T12:00:00Z", "binance"),
            ),
        ).with_contract(
            sources=["binance", "coindesk", "cointelegraph"],
            cmdline_sha256=TEST_CMDLINE,
        )

        # Verify
        state.verify_contract()

        # Serialize
        d = state.to_dict()

        # Load and verify
        loaded = MarketState.from_dict(d)
        assert len(loaded.tickers) == 2
        assert len(loaded.news) == 2
        assert len(loaded.announcements) == 1

        # Tamper and verify fails
        d["announcements"][0]["title"] = "HACKED"
        with pytest.raises(MarketStateError):
            MarketState.from_dict(d)
