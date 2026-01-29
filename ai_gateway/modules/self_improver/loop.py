# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 10:00:00 UTC
# Purpose: Self-Improving Loop - AI that learns from its own trades
# === END SIGNATURE ===
"""
Self-Improving Loop Module.

The core of autonomous AI improvement:

    Signal → AI Predict → Trade → Result
         ↑                          ↓
         └──── Auto-Retrain ←───────┘

Key features:
- Auto-retrain every N trades (configurable, default: 100)
- A/B testing: new model vs old model
- Automatic rollback on 5 consecutive losses
- Fail-closed: won't trade if model not trained
- Version control for all models

This module integrates:
- OutcomeTracker: Tracks signal outcomes (MFE/MAE)
- ModelRegistry: Version control for models
- ABTester: A/B testing between versions
- SignalClassifier: XGBoost prediction model
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...base_module import BaseAIModule, ModuleConfig, ModuleState
from ...contracts import BaseArtifact, SelfImproverArtifact, create_artifact_id
from ..predictor.signal_classifier import SignalClassifier, XGB_AVAILABLE, extract_features

from .outcome_tracker import OutcomeTracker
from .model_registry import ModelRegistry
from .ab_tester import ABTester

logger = logging.getLogger(__name__)


# === Configuration ===

DEFAULT_CONFIG = {
    "retrain_threshold": 100,     # Retrain after N new outcomes
    "min_train_samples": 30,      # Minimum samples to train
    "max_consecutive_losses": 5,  # Rollback after N losses
    "ab_test_samples": 50,        # Samples per arm for A/B test
    "auto_ab_test": True,         # Automatically A/B test new models
    "horizon": "5m",              # Primary horizon for training
}


class SelfImprovingLoop(BaseAIModule):
    """
    Self-Improving AI Loop.

    Automatically:
    1. Tracks signal outcomes
    2. Retrains model when enough data collected
    3. A/B tests new vs old model
    4. Rolls back if performance degrades

    Usage:
        config = ModuleConfig(
            module_id="self_improver",
            interval_seconds=60,  # Check every minute
            enabled=True,
        )

        loop = SelfImprovingLoop(config)
        await loop.start()

        # Process a signal
        prediction = loop.predict(signal)

        # Update with price data
        loop.update_prices({"BTCUSDT": 42000.0})
    """

    def __init__(
        self,
        config: ModuleConfig,
        state_dir: Path = Path("state/ai"),
    ):
        super().__init__(config)

        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)

        # Configuration
        self.retrain_threshold = config.extra.get("retrain_threshold", DEFAULT_CONFIG["retrain_threshold"])
        self.min_train_samples = config.extra.get("min_train_samples", DEFAULT_CONFIG["min_train_samples"])
        self.max_consecutive_losses = config.extra.get("max_consecutive_losses", DEFAULT_CONFIG["max_consecutive_losses"])
        self.ab_test_samples = config.extra.get("ab_test_samples", DEFAULT_CONFIG["ab_test_samples"])
        self.auto_ab_test = config.extra.get("auto_ab_test", DEFAULT_CONFIG["auto_ab_test"])
        self.horizon = config.extra.get("horizon", DEFAULT_CONFIG["horizon"])

        # Components
        self.outcome_tracker = OutcomeTracker(state_dir=state_dir / "outcomes")
        self.model_registry = ModelRegistry(models_dir=state_dir / "models")
        self.ab_tester = ABTester(state_dir=state_dir / "ab_tests")
        self.classifier = SignalClassifier(model_path=state_dir / "models" / "active_model.joblib")

        # State
        self._consecutive_losses = 0
        self._last_train_count = 0
        self._pending_signals: Dict[str, Dict[str, Any]] = {}  # signal_id -> signal_data

        # Load active model from registry
        self._load_active_model()

        logger.info(f"SelfImprovingLoop initialized (threshold={self.retrain_threshold}, min_samples={self.min_train_samples})")

    def _load_active_model(self) -> None:
        """Load active model from registry."""
        model = self.model_registry.get_active()
        if model is not None:
            self.classifier.model = model
            self.classifier.is_trained = True
            logger.info(f"Loaded model v{self.model_registry.get_active_version()}")

    async def on_start(self) -> None:
        """Initialize on module start."""
        if not XGB_AVAILABLE:
            logger.warning("XGBoost not installed, predictions will be disabled")

        logger.info(f"SelfImprovingLoop started, tracking {len(self.outcome_tracker._active)} signals")

    async def on_stop(self) -> None:
        """Cleanup on module stop."""
        logger.info("SelfImprovingLoop stopped")

    async def run_once(self) -> Optional[BaseArtifact]:
        """
        Execute one loop iteration.

        1. Check if retraining needed
        2. Check A/B test results
        3. Generate status artifact
        """
        # Check for retraining
        await self._check_retrain()

        # Check A/B test
        await self._check_ab_test()

        # Generate status artifact
        return self._create_status_artifact()

    # === Public API ===

    def predict(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """
        Make prediction for a signal.

        Args:
            signal: Signal data dict

        Returns:
            Prediction with:
            - win_probability: float
            - prediction: "WIN" | "LOSS" | "UNKNOWN"
            - confidence: "HIGH" | "MEDIUM" | "LOW"
            - recommendation: "BUY" | "SKIP" | "WATCH"
        """
        # Register for outcome tracking
        signal_id = self.outcome_tracker.register_signal(signal)
        self._pending_signals[signal_id] = signal

        # Make prediction
        if not self.classifier.is_trained:
            return {
                "signal_id": signal_id,
                "win_probability": 0.5,
                "prediction": "UNKNOWN",
                "confidence": "LOW",
                "recommendation": "SKIP",
                "reason": "Model not trained yet",
                "model_version": None,
            }

        # A/B test routing
        if self.ab_tester.is_testing:
            arm = self.ab_tester.route_signal(signal_id)
            models = self.ab_tester.get_arm_models()
            if models:
                model_version = models[0] if arm == "A" else models[1]
                # Use specific model version
                model = self.model_registry.get_version(model_version)
                if model is not None:
                    prediction = self._predict_with_model(signal, model)
                    prediction["signal_id"] = signal_id
                    prediction["model_version"] = model_version
                    prediction["ab_arm"] = arm

                    # Record for A/B test
                    self.ab_tester.record_prediction(arm, signal_id, prediction)

                    return prediction

        # Normal prediction with active model
        prediction = self.classifier.predict(signal)
        prediction["signal_id"] = signal_id
        prediction["model_version"] = self.model_registry.get_active_version()

        return prediction

    def _predict_with_model(self, signal: Dict[str, Any], model: Any) -> Dict[str, Any]:
        """Make prediction with specific model."""
        features = extract_features(signal).reshape(1, -1)
        proba = model.predict_proba(features)[0, 1]

        if proba >= 0.7:
            confidence = "HIGH"
            recommendation = "BUY"
        elif proba >= 0.55:
            confidence = "MEDIUM"
            recommendation = "WATCH"
        else:
            confidence = "LOW"
            recommendation = "SKIP"

        return {
            "win_probability": float(proba),
            "prediction": "WIN" if proba >= 0.5 else "LOSS",
            "confidence": confidence,
            "recommendation": recommendation,
        }

    def update_prices(self, prices: Dict[str, float]) -> int:
        """
        Update prices for tracked signals.

        Args:
            prices: Dict mapping symbol -> current_price

        Returns:
            Number of newly completed signals
        """
        completed = self.outcome_tracker.update_prices(prices)

        if completed > 0:
            logger.info(f"Completed {completed} signal outcomes")
            self._process_completed_outcomes()

        return completed

    def record_outcome(self, signal_id: str, is_win: bool) -> None:
        """
        Manually record outcome for a signal.

        Args:
            signal_id: Signal identifier
            is_win: Whether the trade was a win
        """
        # Update consecutive losses counter
        if is_win:
            self._consecutive_losses = 0
        else:
            self._consecutive_losses += 1
            logger.warning(f"Loss recorded, consecutive: {self._consecutive_losses}")

            # Check for rollback
            if self._consecutive_losses >= self.max_consecutive_losses:
                self._trigger_rollback()

        # Record for A/B test if active
        if self.ab_tester.is_testing:
            self.ab_tester.record_outcome(signal_id, is_win)

    def _process_completed_outcomes(self) -> None:
        """Process newly completed outcomes."""
        outcomes = self.outcome_tracker.get_completed_outcomes()

        for outcome in outcomes[-10:]:  # Check last 10
            signal_id = outcome.get("signal_id")
            is_win = outcome.get("outcomes", {}).get(f"{self.horizon}", {}).get("win", False)

            if signal_id:
                self.record_outcome(signal_id, is_win)

    # === Training ===

    async def _check_retrain(self) -> None:
        """Check if retraining is needed."""
        outcomes = self.outcome_tracker.get_completed_outcomes()
        new_outcomes = len(outcomes) - self._last_train_count

        if new_outcomes >= self.retrain_threshold:
            logger.info(f"Retrain threshold reached: {new_outcomes} new outcomes")
            await self._retrain()

    async def _retrain(self) -> bool:
        """
        Retrain the model on collected outcomes.

        Returns:
            True if training successful
        """
        if not XGB_AVAILABLE:
            logger.error("Cannot retrain: XGBoost not installed")
            return False

        outcomes_file = self.outcome_tracker.outcomes_file

        if not outcomes_file.exists():
            logger.warning("No outcomes file for training")
            return False

        outcomes = self.outcome_tracker.get_completed_outcomes()
        if len(outcomes) < self.min_train_samples:
            logger.warning(f"Insufficient samples: {len(outcomes)} < {self.min_train_samples}")
            return False

        try:
            # Create new classifier and train
            new_classifier = SignalClassifier()
            metrics = new_classifier.train(outcomes_file, horizon=self.horizon)

            # Register new model
            new_version = self.model_registry.register(
                model=new_classifier.model,
                metrics=metrics,
                trained_samples=len(outcomes),
                notes=f"Auto-retrain at {datetime.utcnow().isoformat()}Z",
                activate=not self.auto_ab_test,  # Don't activate if A/B testing
            )

            self._last_train_count = len(outcomes)

            # Start A/B test if enabled
            if self.auto_ab_test and self.model_registry.get_active_version() is not None:
                old_version = self.model_registry.get_active_version()
                self.ab_tester.start_test(old_version, new_version)
                logger.info(f"Started A/B test: v{old_version} vs v{new_version}")
            else:
                # Update active classifier
                self.classifier.model = new_classifier.model
                self.classifier.is_trained = True
                logger.info(f"Trained and activated model v{new_version}")

            return True

        except Exception as e:
            logger.error(f"Training failed: {e}")
            return False

    # === A/B Testing ===

    async def _check_ab_test(self) -> None:
        """Check A/B test status."""
        if not self.ab_tester.is_testing:
            return

        if self.ab_tester.has_winner():
            winner_version = self.ab_tester.get_winner()
            results = self.ab_tester.end_test()

            logger.info(f"A/B test complete, winner: v{winner_version}")

            # Activate winner
            if winner_version is not None:
                model = self.model_registry.get_version(winner_version)
                if model is not None:
                    self.classifier.model = model
                    self.classifier.is_trained = True
                    self.model_registry._activate_version(winner_version)
                    self.model_registry._save_registry()

    # === Rollback ===

    def _trigger_rollback(self) -> None:
        """Rollback to previous model version."""
        logger.warning(f"Triggering rollback due to {self._consecutive_losses} consecutive losses")

        if self.model_registry.rollback():
            # Reload model
            self._load_active_model()
            self._consecutive_losses = 0

            # Cancel any active A/B test
            if self.ab_tester.is_testing:
                self.ab_tester.cancel_test()

    # === Status ===

    def _create_status_artifact(self) -> SelfImproverArtifact:
        """Create status artifact."""
        stats = self.outcome_tracker.get_stats()

        artifact = SelfImproverArtifact(
            artifact_id=create_artifact_id("self_improver"),
            model_version=self.model_registry.get_active_version(),
            is_trained=self.classifier.is_trained,
            consecutive_losses=self._consecutive_losses,
            active_signals=stats["active_signals"],
            completed_signals=stats["completed_signals"],
            win_rate=stats["win_rate_5m"],
            last_train_samples=self._last_train_count,
            last_train_metrics=self.classifier.model.get_booster().attributes() if self.classifier.model else {},
            ab_test_active=self.ab_tester.is_testing,
            ab_test_results=self.ab_tester.get_results() if self.ab_tester.is_testing else {},
            status_message=self._get_status_message(),
        )

        return artifact.with_checksum()

    def _get_status_message(self) -> str:
        """Generate human-readable status message."""
        if not self.classifier.is_trained:
            outcomes = self.outcome_tracker.get_completed_outcomes()
            needed = self.min_train_samples - len(outcomes)
            return f"⏳ Collecting data: {len(outcomes)}/{self.min_train_samples} (need {needed} more)"

        if self.ab_tester.is_testing:
            results = self.ab_tester.get_results()
            arm_a = results.get("arm_a", {})
            arm_b = results.get("arm_b", {})
            return f"🔬 A/B Testing: v{arm_a.get('model_version')} ({arm_a.get('win_rate', 0):.1%}) vs v{arm_b.get('model_version')} ({arm_b.get('win_rate', 0):.1%})"

        if self._consecutive_losses > 0:
            return f"⚠️ Active (v{self.model_registry.get_active_version()}), {self._consecutive_losses} consecutive losses"

        return f"✅ Active model v{self.model_registry.get_active_version()}"

    def get_info(self) -> Dict[str, Any]:
        """Get extended module info."""
        base_info = super().get_info()

        base_info.update({
            "model_version": self.model_registry.get_active_version(),
            "is_trained": self.classifier.is_trained,
            "consecutive_losses": self._consecutive_losses,
            "active_signals": len(self.outcome_tracker._active),
            "completed_signals": len(self.outcome_tracker.get_completed_outcomes()),
            "ab_test_active": self.ab_tester.is_testing,
            "retrain_threshold": self.retrain_threshold,
            "outcomes_until_retrain": self.retrain_threshold - (len(self.outcome_tracker.get_completed_outcomes()) - self._last_train_count),
        })

        return base_info


# === Quick test ===

async def test_self_improving_loop():
    """Quick test of the self-improving loop."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        state_dir = Path(tmpdir)

        config = ModuleConfig(
            module_id="self_improver",
            interval_seconds=60,
            enabled=True,
            extra={
                "retrain_threshold": 10,
                "min_train_samples": 5,
            }
        )

        loop = SelfImprovingLoop(config, state_dir=state_dir)

        # Test prediction (untrained)
        signal = {
            "symbol": "BTCUSDT",
            "price": 42000.0,
            "direction": "Long",
            "delta_pct": 2.5,
            "signal_type": "pump",
        }

        prediction = loop.predict(signal)
        print(f"Prediction (untrained): {prediction}")

        # Simulate some outcomes
        for i in range(10):
            signal = {
                "symbol": "BTCUSDT",
                "price": 42000.0 + i * 100,
                "direction": "Long",
                "delta_pct": 2.0 + i * 0.1,
                "signal_type": "pump",
            }
            loop.predict(signal)

        # Update prices to trigger outcome calculation
        for _ in range(5):
            loop.update_prices({"BTCUSDT": 42500.0})

        print(f"\nInfo: {loop.get_info()}")


if __name__ == "__main__":
    asyncio.run(test_self_improving_loop())
