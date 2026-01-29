# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 04:18:00 UTC
# Purpose: Tests for AI-Gateway modules
# === END SIGNATURE ===
"""
AI-Gateway Tests: Verify contracts, status manager, and JSONL writer.
"""

import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest


class TestContracts:
    """Test Pydantic contract models."""

    def test_sentiment_artifact_creation(self):
        """Test SentimentArtifact creation and checksum."""
        from ai_gateway.contracts import (
            SentimentArtifact,
            SentimentLevel,
            SentimentSignal,
            create_artifact_id,
        )

        artifact = SentimentArtifact(
            artifact_id=create_artifact_id("sentiment", "BTCUSDT"),
            symbol="BTCUSDT",
            overall_sentiment=SentimentLevel.GREED,
            overall_score=0.65,
            confidence=0.8,
            signals=[
                SentimentSignal(source="news", score=0.5, confidence=0.7, sample_size=5),
                SentimentSignal(source="market", score=0.8, confidence=0.9, sample_size=1),
            ],
            bias="long",
            strength=0.65,
        )

        # Checksum should be computed
        artifact = artifact.with_checksum()
        assert artifact.checksum is not None
        assert artifact.checksum.startswith("sha256:")

        # Should be valid
        assert artifact.is_valid()

        # Not expired yet
        assert not artifact.is_expired()

    def test_regime_artifact_creation(self):
        """Test RegimeArtifact creation."""
        from ai_gateway.contracts import (
            RegimeArtifact,
            MarketRegime,
            RegimeIndicator,
            create_artifact_id,
        )

        artifact = RegimeArtifact(
            artifact_id=create_artifact_id("regime", "BTCUSDT"),
            symbol="BTCUSDT",
            timeframe="4h",
            current_regime=MarketRegime.TRENDING_UP,
            regime_confidence=0.75,
            trend_direction="up",
            trend_strength=0.6,
            volatility_percentile=45.0,
            indicators=[
                RegimeIndicator(
                    name="trend_strength",
                    value=0.6,
                    threshold_low=0.2,
                    threshold_high=0.4,
                    signal="bullish",
                ),
            ],
            recommended_strategy="trend_follow_long",
            position_size_modifier=1.2,
        )

        artifact = artifact.with_checksum()
        assert artifact.is_valid()

    def test_anomaly_artifact_creation(self):
        """Test AnomalyScannerArtifact creation."""
        from ai_gateway.contracts import (
            AnomalyScannerArtifact,
            AnomalyEvent,
            AnomalySeverity,
            create_artifact_id,
        )

        artifact = AnomalyScannerArtifact(
            artifact_id=create_artifact_id("anomaly"),
            anomalies_found=2,
            critical_count=0,
            high_count=1,
            events=[
                AnomalyEvent(
                    anomaly_type="volume_surge",
                    severity=AnomalySeverity.HIGH,
                    description="BTC volume 3.5x average",
                    detected_at=datetime.utcnow(),
                    affected_symbols=["BTCUSDT"],
                    metrics={"volume_ratio": 3.5},
                ),
            ],
            market_stress_level=0.4,
            alert_level="elevated",
            recommended_actions=["Monitor closely"],
        )

        artifact = artifact.with_checksum()
        assert artifact.is_valid()

    def test_artifact_ttl_expiry(self):
        """Test that expired artifacts are detected."""
        from ai_gateway.contracts import SentimentArtifact, SentimentLevel, create_artifact_id
        from datetime import timedelta

        artifact = SentimentArtifact(
            artifact_id=create_artifact_id("sentiment", "TEST"),
            symbol="TEST",
            overall_sentiment=SentimentLevel.NEUTRAL,
            overall_score=0.0,
            confidence=0.5,
            ttl_seconds=1,  # 1 second TTL
        )

        # Manually set created_at to past
        artifact.created_at = datetime.utcnow() - timedelta(seconds=10)

        assert artifact.is_expired()


class TestStatusManager:
    """Test StatusManager functionality."""

    def test_status_manager_singleton(self):
        """Test StatusManager is singleton."""
        from ai_gateway.status_manager import StatusManager

        # Reset singleton for test
        StatusManager._instance = None

        with tempfile.TemporaryDirectory() as tmpdir:
            sm1 = StatusManager(Path(tmpdir))
            sm2 = StatusManager(Path(tmpdir))
            assert sm1 is sm2

    def test_module_enable_disable(self):
        """Test enabling and disabling modules."""
        from ai_gateway.status_manager import StatusManager
        from ai_gateway.contracts import ModuleStatus

        # Reset singleton
        StatusManager._instance = None

        with tempfile.TemporaryDirectory() as tmpdir:
            sm = StatusManager(Path(tmpdir))

            # Initially disabled
            assert not sm.is_enabled("sentiment")
            assert sm.get_status("sentiment") == ModuleStatus.DISABLED

            # Enable
            assert sm.enable_module("sentiment")
            assert sm.is_enabled("sentiment")
            assert sm.get_status("sentiment") == ModuleStatus.HEALTHY

            # Disable
            assert sm.disable_module("sentiment")
            assert not sm.is_enabled("sentiment")
            assert sm.get_status("sentiment") == ModuleStatus.DISABLED

    def test_status_emojis(self):
        """Test status emoji mapping."""
        from ai_gateway.status_manager import StatusManager
        from ai_gateway.contracts import ModuleStatus

        StatusManager._instance = None

        with tempfile.TemporaryDirectory() as tmpdir:
            sm = StatusManager(Path(tmpdir))

            # Disabled = gray
            assert sm.get_emoji("sentiment") == "⚪"

            # Enable -> healthy = green
            sm.enable_module("sentiment")
            assert sm.get_emoji("sentiment") == "🟢"

            # Error = red
            sm.mark_error("sentiment", "Test error")
            assert sm.get_emoji("sentiment") == "🔴"

            # Warning = yellow
            sm.mark_warning("sentiment", "Test warning")
            assert sm.get_emoji("sentiment") == "🟡"


class TestJSONLWriter:
    """Test JSONL writer functionality."""

    def test_write_and_read_artifact(self):
        """Test writing and reading artifacts."""
        from ai_gateway.jsonl_writer import JSONLWriter
        from ai_gateway.contracts import SentimentArtifact, SentimentLevel, create_artifact_id

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = JSONLWriter(Path(tmpdir))

            artifact = SentimentArtifact(
                artifact_id=create_artifact_id("sentiment", "BTCUSDT"),
                symbol="BTCUSDT",
                overall_sentiment=SentimentLevel.GREED,
                overall_score=0.7,
                confidence=0.85,
                bias="long",
                strength=0.7,
            )

            # Write
            assert writer.write_artifact(artifact)

            # Read back
            results = writer.read_latest("sentiment", count=1)
            assert len(results) == 1
            assert results[0]["symbol"] == "BTCUSDT"
            assert results[0]["overall_score"] == 0.7

    def test_read_valid_artifact(self):
        """Test reading only valid (non-expired) artifacts."""
        from ai_gateway.jsonl_writer import JSONLWriter
        from ai_gateway.contracts import SentimentArtifact, SentimentLevel, create_artifact_id

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = JSONLWriter(Path(tmpdir))

            # Write artifact with long TTL
            artifact = SentimentArtifact(
                artifact_id=create_artifact_id("sentiment", "BTCUSDT"),
                symbol="BTCUSDT",
                overall_sentiment=SentimentLevel.NEUTRAL,
                overall_score=0.0,
                confidence=0.5,
                ttl_seconds=3600,  # 1 hour
            )
            writer.write_artifact(artifact)

            # Should be readable
            valid = writer.read_valid_artifact("sentiment")
            assert valid is not None

    def test_multiple_artifacts(self):
        """Test reading multiple artifacts."""
        from ai_gateway.jsonl_writer import JSONLWriter
        from ai_gateway.contracts import SentimentArtifact, SentimentLevel, create_artifact_id

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = JSONLWriter(Path(tmpdir))

            # Write 5 artifacts
            for i in range(5):
                artifact = SentimentArtifact(
                    artifact_id=create_artifact_id("sentiment", f"TEST{i}"),
                    symbol=f"TEST{i}",
                    overall_sentiment=SentimentLevel.NEUTRAL,
                    overall_score=float(i) / 10,
                    confidence=0.5,
                )
                writer.write_artifact(artifact)

            # Read last 3
            results = writer.read_latest("sentiment", count=3)
            assert len(results) == 3
            # Newest first
            assert results[0]["symbol"] == "TEST4"
            assert results[1]["symbol"] == "TEST3"
            assert results[2]["symbol"] == "TEST2"


class TestAIReader:
    """Test Core's AI artifact reader."""

    def test_reader_without_artifacts(self):
        """Test reader behavior when no artifacts exist."""
        from core.ai_reader import AIArtifactReader

        with tempfile.TemporaryDirectory() as tmpdir:
            reader = AIArtifactReader(Path(tmpdir))

            # Should return None
            assert reader.get_sentiment("BTCUSDT") is None
            assert reader.get_regime("BTCUSDT") is None
            assert reader.get_doctor() is None
            assert reader.get_anomaly() is None

    def test_reader_with_artifacts(self):
        """Test reader with valid artifacts."""
        from core.ai_reader import AIArtifactReader
        from ai_gateway.jsonl_writer import JSONLWriter
        from ai_gateway.contracts import SentimentArtifact, SentimentLevel, create_artifact_id

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Write artifact
            writer = JSONLWriter(tmppath)
            artifact = SentimentArtifact(
                artifact_id=create_artifact_id("sentiment", "BTCUSDT"),
                symbol="BTCUSDT",
                overall_sentiment=SentimentLevel.GREED,
                overall_score=0.6,
                confidence=0.8,
                bias="long",
                strength=0.6,
                ttl_seconds=3600,
            )
            writer.write_artifact(artifact)

            # Read via reader
            reader = AIArtifactReader(tmppath)
            sentiment = reader.get_sentiment("BTCUSDT")

            assert sentiment is not None
            assert sentiment["symbol"] == "BTCUSDT"
            assert sentiment["overall_score"] == 0.6
            assert sentiment["bias"] == "long"

    def test_insights_combined(self):
        """Test getting combined insights."""
        from core.ai_reader import AIArtifactReader

        with tempfile.TemporaryDirectory() as tmpdir:
            reader = AIArtifactReader(Path(tmpdir))
            insights = reader.get_all_insights("BTCUSDT")

            # No data = neutral defaults
            assert not insights.has_any
            assert not insights.should_reduce_exposure
            assert insights.suggested_bias == "neutral"
            assert insights.position_size_multiplier == 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
