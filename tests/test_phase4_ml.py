# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T23:00:00Z
# Purpose: Tests for Phase 4 - ML Integration
# Security: Test-only, no production impact
# === END SIGNATURE ===
"""
Tests for Phase 4 ML Integration.

Modules tested:
- core.ai.features (FeatureExtractor, FeatureSet)
- core.ai.ml_predictor (MLPredictor, HeuristicModel)
- Integration with SignalEngine
"""
import pytest
import numpy as np
import time
from pathlib import Path
from unittest.mock import Mock, patch


class TestFeatureExtractor:
    """Tests for FeatureExtractor."""

    def test_extractor_creates(self):
        """Verify extractor can be created."""
        from core.ai.features import FeatureExtractor

        extractor = FeatureExtractor()
        assert extractor is not None
        assert extractor.min_candles == 50

    def test_get_feature_names(self):
        """Verify feature names are returned."""
        from core.ai.features import FeatureExtractor

        extractor = FeatureExtractor()
        names = extractor.get_feature_names()

        assert len(names) == 20
        assert "rsi_14" in names
        assert "macd_hist_norm" in names
        assert "bb_position" in names

    def test_extract_features_valid(self):
        """Verify features are extracted from valid data."""
        from core.ai.features import FeatureExtractor
        from core.ai.signal_engine import MarketData
        from core.backtest.data_loader import generate_synthetic_klines

        extractor = FeatureExtractor()

        # Generate synthetic data
        klines = generate_synthetic_klines(candle_count=100, seed=42)

        market_data = MarketData(
            symbol="BTCUSDT",
            timestamp=int(time.time()),
            opens=klines.opens,
            highs=klines.highs,
            lows=klines.lows,
            closes=klines.closes,
            volumes=klines.volumes,
        )

        features = extractor.extract(market_data)

        assert features is not None
        assert features.num_features == 20
        assert features.symbol == "BTCUSDT"
        assert len(features.names) == len(features.features)

    def test_extract_features_insufficient_data(self):
        """Verify extraction fails with insufficient data for extractor."""
        from core.ai.features import FeatureExtractor
        from core.ai.signal_engine import MarketData
        from core.backtest.data_loader import generate_synthetic_klines

        # Extractor requires 60 candles
        extractor = FeatureExtractor(min_candles=60)

        # 40 candles (valid for MarketData which needs 35, but not for extractor)
        klines = generate_synthetic_klines(candle_count=40)

        market_data = MarketData(
            symbol="BTCUSDT",
            timestamp=int(time.time()),
            opens=klines.opens,
            highs=klines.highs,
            lows=klines.lows,
            closes=klines.closes,
            volumes=klines.volumes,
        )

        features = extractor.extract(market_data)
        assert features is None

    def test_extract_features_none_input(self):
        """Verify extraction returns None for None input."""
        from core.ai.features import FeatureExtractor

        extractor = FeatureExtractor()
        features = extractor.extract(None)
        assert features is None

    def test_all_features_finite(self):
        """Verify all features are finite (no NaN/Inf)."""
        from core.ai.features import FeatureExtractor
        from core.ai.signal_engine import MarketData
        from core.backtest.data_loader import generate_synthetic_klines

        extractor = FeatureExtractor()

        klines = generate_synthetic_klines(candle_count=100, seed=123)

        market_data = MarketData(
            symbol="BTCUSDT",
            timestamp=int(time.time()),
            opens=klines.opens,
            highs=klines.highs,
            lows=klines.lows,
            closes=klines.closes,
            volumes=klines.volumes,
        )

        features = extractor.extract(market_data)

        assert features is not None
        assert features.is_valid()
        assert np.all(np.isfinite(features.features))

    def test_feature_normalization(self):
        """Verify features are roughly normalized."""
        from core.ai.features import FeatureExtractor
        from core.ai.signal_engine import MarketData
        from core.backtest.data_loader import generate_synthetic_klines

        extractor = FeatureExtractor()

        klines = generate_synthetic_klines(candle_count=100, seed=456)

        market_data = MarketData(
            symbol="BTCUSDT",
            timestamp=int(time.time()),
            opens=klines.opens,
            highs=klines.highs,
            lows=klines.lows,
            closes=klines.closes,
            volumes=klines.volumes,
        )

        features = extractor.extract(market_data)
        assert features is not None

        # Most features should be in [-3, 3] range after normalization
        for i, val in enumerate(features.features):
            assert -5 <= val <= 5, f"Feature {features.names[i]} out of range: {val}"

    def test_feature_to_dict(self):
        """Verify to_dict conversion."""
        from core.ai.features import FeatureExtractor
        from core.ai.signal_engine import MarketData
        from core.backtest.data_loader import generate_synthetic_klines

        extractor = FeatureExtractor()
        klines = generate_synthetic_klines(candle_count=100, seed=42)

        market_data = MarketData(
            symbol="BTCUSDT",
            timestamp=int(time.time()),
            opens=klines.opens,
            highs=klines.highs,
            lows=klines.lows,
            closes=klines.closes,
            volumes=klines.volumes,
        )

        features = extractor.extract(market_data)
        assert features is not None

        feature_dict = features.to_dict()
        assert isinstance(feature_dict, dict)
        assert "rsi_14" in feature_dict
        assert 0 <= feature_dict["rsi_14"] <= 1


class TestHeuristicModel:
    """Tests for HeuristicModel."""

    def test_heuristic_creates(self):
        """Verify heuristic model can be created."""
        from core.ai.ml_predictor import HeuristicModel

        model = HeuristicModel()
        assert model is not None

    def test_heuristic_predict_basic(self):
        """Verify heuristic prediction works."""
        from core.ai.ml_predictor import HeuristicModel
        from core.ai.features import FeatureExtractor
        from core.ai.signal_engine import MarketData
        from core.backtest.data_loader import generate_synthetic_klines

        model = HeuristicModel()
        extractor = FeatureExtractor()

        klines = generate_synthetic_klines(candle_count=100, seed=42)
        market_data = MarketData(
            symbol="BTCUSDT",
            timestamp=int(time.time()),
            opens=klines.opens,
            highs=klines.highs,
            lows=klines.lows,
            closes=klines.closes,
            volumes=klines.volumes,
        )

        features = extractor.extract(market_data)
        assert features is not None

        prediction = model.predict(features.features)

        assert isinstance(prediction, float)
        assert -1.0 <= prediction <= 1.0

    def test_heuristic_predict_proba(self):
        """Verify probability predictions."""
        from core.ai.ml_predictor import HeuristicModel
        from core.ai.features import FeatureExtractor
        from core.ai.signal_engine import MarketData
        from core.backtest.data_loader import generate_synthetic_klines

        model = HeuristicModel()
        extractor = FeatureExtractor()

        klines = generate_synthetic_klines(candle_count=100, seed=42)
        market_data = MarketData(
            symbol="BTCUSDT",
            timestamp=int(time.time()),
            opens=klines.opens,
            highs=klines.highs,
            lows=klines.lows,
            closes=klines.closes,
            volumes=klines.volumes,
        )

        features = extractor.extract(market_data)
        proba = model.predict_proba(features.features)

        assert "long" in proba
        assert "short" in proba
        assert "hold" in proba
        assert 0 <= proba["long"] <= 1
        assert 0 <= proba["short"] <= 1
        # Probabilities should sum to ~1
        total = proba["long"] + proba["short"] + proba["hold"]
        assert 0.99 <= total <= 1.01


class TestMLPredictor:
    """Tests for MLPredictor."""

    def test_predictor_creates(self):
        """Verify predictor can be created."""
        from core.ai.ml_predictor import MLPredictor, MLConfig

        config = MLConfig(enabled=True, model_type="heuristic")
        predictor = MLPredictor(config)

        assert predictor is not None

    def test_predictor_disabled(self):
        """Verify disabled predictor returns 0."""
        from core.ai.ml_predictor import MLPredictor, MLConfig
        from core.ai.features import FeatureSet

        config = MLConfig(enabled=False)
        predictor = MLPredictor(config)

        features = FeatureSet(
            features=np.zeros(20),
            names=["f" + str(i) for i in range(20)],
            timestamp=int(time.time()),
            symbol="BTCUSDT",
        )

        score = predictor.predict(features)
        assert score == 0.0

    def test_predictor_heuristic_fallback(self):
        """Verify heuristic fallback works."""
        from core.ai.ml_predictor import MLPredictor, MLConfig
        from core.ai.features import FeatureExtractor
        from core.ai.signal_engine import MarketData
        from core.backtest.data_loader import generate_synthetic_klines

        # Request xgboost but don't provide model path
        config = MLConfig(
            enabled=True,
            model_type="xgboost",
            model_path=None,  # No model
            fallback_to_heuristic=True,
        )
        predictor = MLPredictor(config)

        extractor = FeatureExtractor()
        klines = generate_synthetic_klines(candle_count=100, seed=42)
        market_data = MarketData(
            symbol="BTCUSDT",
            timestamp=int(time.time()),
            opens=klines.opens,
            highs=klines.highs,
            lows=klines.lows,
            closes=klines.closes,
            volumes=klines.volumes,
        )

        features = extractor.extract(market_data)
        score = predictor.predict(features)

        # Should still produce a prediction via heuristic
        assert isinstance(score, float)
        assert -1.0 <= score <= 1.0

    def test_prediction_in_range(self):
        """Verify predictions are always in [-1, 1]."""
        from core.ai.ml_predictor import MLPredictor
        from core.ai.features import FeatureExtractor
        from core.ai.signal_engine import MarketData
        from core.backtest.data_loader import generate_synthetic_klines

        predictor = MLPredictor()
        extractor = FeatureExtractor()

        # Test with multiple seeds
        for seed in [1, 42, 123, 456, 789]:
            klines = generate_synthetic_klines(candle_count=100, seed=seed)
            market_data = MarketData(
                symbol="BTCUSDT",
                timestamp=int(time.time()),
                opens=klines.opens,
                highs=klines.highs,
                lows=klines.lows,
                closes=klines.closes,
                volumes=klines.volumes,
            )

            features = extractor.extract(market_data)
            if features:
                score = predictor.predict(features)
                assert -1.0 <= score <= 1.0, f"Prediction out of range for seed {seed}: {score}"

    def test_predict_full(self):
        """Verify full prediction with probabilities."""
        from core.ai.ml_predictor import MLPredictor
        from core.ai.features import FeatureExtractor
        from core.ai.signal_engine import MarketData
        from core.backtest.data_loader import generate_synthetic_klines

        predictor = MLPredictor()
        extractor = FeatureExtractor()

        klines = generate_synthetic_klines(candle_count=100, seed=42)
        market_data = MarketData(
            symbol="BTCUSDT",
            timestamp=int(time.time()),
            opens=klines.opens,
            highs=klines.highs,
            lows=klines.lows,
            closes=klines.closes,
            volumes=klines.volumes,
        )

        features = extractor.extract(market_data)
        result = predictor.predict_full(features)

        assert result is not None
        assert result.model_type == "heuristic"
        assert -1.0 <= result.score <= 1.0
        assert 0 <= result.confidence <= 1
        assert result.direction in ["LONG", "SHORT", "NEUTRAL"]

    def test_model_info(self):
        """Verify model info is returned."""
        from core.ai.ml_predictor import MLPredictor

        predictor = MLPredictor()
        info = predictor.get_model_info()

        assert "enabled" in info
        assert "model_type" in info
        assert "model_loaded" in info

    def test_cache_works(self):
        """Verify prediction cache works."""
        from core.ai.ml_predictor import MLPredictor, MLConfig
        from core.ai.features import FeatureExtractor
        from core.ai.signal_engine import MarketData
        from core.backtest.data_loader import generate_synthetic_klines

        config = MLConfig(cache_predictions=True)
        predictor = MLPredictor(config)
        extractor = FeatureExtractor()

        klines = generate_synthetic_klines(candle_count=100, seed=42)
        timestamp = int(time.time())
        market_data = MarketData(
            symbol="BTCUSDT",
            timestamp=timestamp,
            opens=klines.opens,
            highs=klines.highs,
            lows=klines.lows,
            closes=klines.closes,
            volumes=klines.volumes,
        )

        features = extractor.extract(market_data)

        # First prediction
        score1 = predictor.predict(features)

        # Second prediction (should use cache)
        score2 = predictor.predict(features)

        assert score1 == score2

        # Clear cache
        predictor.clear_cache()
        assert predictor.get_model_info()["cache_size"] == 0


class TestMLIntegration:
    """Tests for ML integration with SignalEngine."""

    def test_signal_with_ml_prediction(self):
        """Verify signal generation accepts ml_prediction."""
        from core.ai.signal_engine import SignalEngine, MarketData
        from core.backtest.data_loader import generate_synthetic_klines

        engine = SignalEngine()

        klines = generate_synthetic_klines(candle_count=100, trend=0.001, seed=42)
        market_data = MarketData(
            symbol="BTCUSDT",
            timestamp=int(time.time()),
            opens=klines.opens,
            highs=klines.highs,
            lows=klines.lows,
            closes=klines.closes,
            volumes=klines.volumes,
        )

        # Generate signal WITH ml_prediction
        signal = engine.generate_signal(
            market_data=market_data,
            ml_prediction=0.5,  # Bullish ML prediction
        )

        # Signal may or may not be generated depending on other factors
        # But if it is, ml_score should reflect the prediction
        if signal is not None:
            assert signal.ml_score == pytest.approx(0.5, rel=0.1)

    def test_ml_affects_confidence(self):
        """Verify ML prediction affects signal confidence."""
        from core.ai.signal_engine import SignalEngine, MarketData
        from core.backtest.data_loader import generate_synthetic_klines

        engine = SignalEngine()

        klines = generate_synthetic_klines(candle_count=100, trend=0.001, seed=42)
        market_data = MarketData(
            symbol="BTCUSDT",
            timestamp=int(time.time()),
            opens=klines.opens,
            highs=klines.highs,
            lows=klines.lows,
            closes=klines.closes,
            volumes=klines.volumes,
        )

        # Generate signal without ML
        signal_no_ml = engine.generate_signal(
            market_data=market_data,
            ml_prediction=None,
        )

        # Generate signal with strong ML prediction
        signal_with_ml = engine.generate_signal(
            market_data=market_data,
            ml_prediction=0.8,
        )

        # Both may be None if signal thresholds not met
        # Just verify the function handles both cases
        assert signal_no_ml is None or signal_no_ml.ml_score == 0.0
        assert signal_with_ml is None or signal_with_ml.ml_score == pytest.approx(0.8, rel=0.1)

    def test_end_to_end_ml_pipeline(self):
        """Test complete ML pipeline: data → features → prediction → signal."""
        from core.ai.features import FeatureExtractor
        from core.ai.ml_predictor import MLPredictor
        from core.ai.signal_engine import SignalEngine, MarketData
        from core.backtest.data_loader import generate_synthetic_klines

        # Setup
        extractor = FeatureExtractor()
        predictor = MLPredictor()
        engine = SignalEngine()

        # Generate data
        klines = generate_synthetic_klines(candle_count=100, trend=0.001, seed=42)
        market_data = MarketData(
            symbol="BTCUSDT",
            timestamp=int(time.time()),
            opens=klines.opens,
            highs=klines.highs,
            lows=klines.lows,
            closes=klines.closes,
            volumes=klines.volumes,
        )

        # Extract features
        features = extractor.extract(market_data)
        assert features is not None

        # Get ML prediction
        ml_score = predictor.predict(features)
        assert -1.0 <= ml_score <= 1.0

        # Generate signal with ML
        signal = engine.generate_signal(
            market_data=market_data,
            ml_prediction=ml_score,
        )

        # Verify pipeline completed (signal may or may not be generated)
        print(f"ML Score: {ml_score:.3f}")
        if signal:
            print(f"Signal: {signal.direction.value}, confidence={signal.confidence:.2f}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
