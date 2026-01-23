# === AI SIGNATURE ===
# Created by: Claude
# Created at: 2026-01-21 15:30:00 UTC
# === END SIGNATURE ===
"""
Tests for nore.store module (atomic save/load).

Run: pytest tests/test_nore_store.py -v
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nore.market_state import MarketState, MarketStateError, Ticker, NewsItem
from nore.store import save_state, load_state, load_state_or_none, StoreError


# Test cmdline_sha256 (valid format)
TEST_CMDLINE = "sha256:" + "0" * 64


class TestAtomicSaveLoad:
    """Tests for atomic save/load operations."""

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        """Save -> Load roundtrip should preserve data."""
        p = tmp_path / "state.json"

        state = MarketState(
            tickers=(Ticker(symbol="BTCUSDT", price=42000.0, volume=1e9),),
            news=(NewsItem(title="Test", url="https://x.com", published_utc="2026-01-21T10:00:00Z", source="test"),),
        ).with_contract(sources=["binance", "test"], cmdline_sha256=TEST_CMDLINE)

        # Save
        result = save_state(p, state)
        assert p.exists()
        assert result.bytes_written > 0
        assert result.sha256_hex  # non-empty

        # Load
        loaded = load_state(p)
        loaded.verify_contract()

        # Data preserved
        assert len(loaded.tickers) == 1
        assert loaded.tickers[0].symbol == "BTCUSDT"
        assert loaded.tickers[0].price == 42000.0

    def test_load_verifies_contract(self, tmp_path: Path) -> None:
        """load_state should verify sha256 contract."""
        p = tmp_path / "state.json"

        state = MarketState(
            tickers=(Ticker(symbol="BTC", price=100.0, volume=1.0),),
        ).with_contract(sources=["x"], cmdline_sha256=TEST_CMDLINE)

        save_state(p, state)

        # Tamper with file
        data = json.loads(p.read_text(encoding="utf-8"))
        data["tickers"][0]["price"] = 999.0
        p.write_text(json.dumps(data), encoding="utf-8")

        # Load should fail
        with pytest.raises(MarketStateError, match="sha256 contract mismatch"):
            load_state(p)

    def test_load_nonexistent_fails(self, tmp_path: Path) -> None:
        """Loading non-existent file should fail."""
        p = tmp_path / "nonexistent.json"
        with pytest.raises(StoreError, match="not found"):
            load_state(p)

    def test_load_or_none_returns_none(self, tmp_path: Path) -> None:
        """load_state_or_none should return None for missing file."""
        p = tmp_path / "nonexistent.json"
        result = load_state_or_none(p)
        assert result is None

    def test_load_or_none_returns_state(self, tmp_path: Path) -> None:
        """load_state_or_none should return state for existing file."""
        p = tmp_path / "state.json"

        state = MarketState().with_contract(sources=["x"], cmdline_sha256=TEST_CMDLINE)
        save_state(p, state)

        result = load_state_or_none(p)
        assert result is not None
        assert isinstance(result, MarketState)

    def test_load_or_none_raises_on_tamper(self, tmp_path: Path) -> None:
        """load_state_or_none should raise on tampered file (not return None)."""
        p = tmp_path / "state.json"

        state = MarketState(
            tickers=(Ticker(symbol="BTC", price=100.0, volume=1.0),),
        ).with_contract(sources=["x"], cmdline_sha256=TEST_CMDLINE)

        save_state(p, state)

        # Tamper
        data = json.loads(p.read_text(encoding="utf-8"))
        data["tickers"][0]["price"] = 999.0
        p.write_text(json.dumps(data), encoding="utf-8")

        # Should raise, not return None
        with pytest.raises(MarketStateError):
            load_state_or_none(p)

    def test_save_without_meta_fails(self, tmp_path: Path) -> None:
        """Saving state without meta should fail."""
        p = tmp_path / "state.json"
        state = MarketState(tickers=(Ticker(symbol="BTC", price=1.0, volume=1.0),))

        with pytest.raises(StoreError):
            save_state(p, state)

    def test_load_invalid_json_fails(self, tmp_path: Path) -> None:
        """Loading invalid JSON should fail."""
        p = tmp_path / "bad.json"
        p.write_text("not json {{{", encoding="utf-8")

        with pytest.raises(StoreError, match="JSON parse failed"):
            load_state(p)

    def test_load_non_object_fails(self, tmp_path: Path) -> None:
        """Loading non-object JSON should fail."""
        p = tmp_path / "array.json"
        p.write_text("[]", encoding="utf-8")

        with pytest.raises(StoreError, match="must be JSON object"):
            load_state(p)

    def test_atomic_no_partial_write(self, tmp_path: Path) -> None:
        """Atomic write should not leave partial files on error."""
        p = tmp_path / "state.json"

        # Save valid state first
        state1 = MarketState().with_contract(sources=["a"], cmdline_sha256=TEST_CMDLINE)
        save_state(p, state1)

        original_content = p.read_text(encoding="utf-8")

        # Try to save invalid state (no meta)
        state2 = MarketState(tickers=(Ticker("X", 1.0, 1.0),))
        with pytest.raises(StoreError):
            save_state(p, state2)

        # Original file should be unchanged
        assert p.read_text(encoding="utf-8") == original_content


class TestStoreEdgeCases:
    """Edge case tests."""

    def test_empty_state_save_load(self, tmp_path: Path) -> None:
        """Empty state should save/load correctly."""
        p = tmp_path / "empty.json"

        state = MarketState().with_contract(sources=["none"], cmdline_sha256=TEST_CMDLINE)
        save_state(p, state)

        loaded = load_state(p)
        assert len(loaded.tickers) == 0
        assert len(loaded.news) == 0
        assert len(loaded.announcements) == 0

    def test_unicode_in_news(self, tmp_path: Path) -> None:
        """Unicode content should be preserved."""
        p = tmp_path / "unicode.json"

        state = MarketState(
            news=(NewsItem(
                title="Биткоин достиг 100к! 🚀",
                url="https://test.com",
                published_utc="2026-01-21T10:00:00Z",
                source="test",
            ),),
        ).with_contract(sources=["test"], cmdline_sha256=TEST_CMDLINE)

        save_state(p, state)
        loaded = load_state(p)

        assert loaded.news[0].title == "Биткоин достиг 100к! 🚀"

    def test_large_state(self, tmp_path: Path) -> None:
        """Large state should save/load correctly."""
        p = tmp_path / "large.json"

        # Create state with many items
        tickers = tuple(Ticker(f"COIN{i}USDT", float(i), float(i * 1000)) for i in range(100))
        news = tuple(NewsItem(f"News {i}", f"https://x.com/{i}", "2026-01-21T10:00:00Z", "test") for i in range(50))

        state = MarketState(tickers=tickers, news=news).with_contract(
            sources=["test"],
            cmdline_sha256=TEST_CMDLINE,
        )

        save_state(p, state)
        loaded = load_state(p)

        assert len(loaded.tickers) == 100
        assert len(loaded.news) == 50
