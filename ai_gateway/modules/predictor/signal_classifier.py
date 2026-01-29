# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 08:00:00 UTC
# Purpose: XGBoost signal classifier for pump/dump prediction
# === END SIGNATURE ===
"""
Signal Classifier — XGBoost model for predicting signal outcomes.

Features:
- Train on MoonBot signals with labeled outcomes
- Predict WIN/LOSS probability for new signals
- Feature importance analysis

Usage:
    from ai_gateway.modules.predictor import SignalClassifier

    classifier = SignalClassifier()
    classifier.train(outcomes_file="data/moonbot_signals/outcomes.jsonl")
    prediction = classifier.predict(signal)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Check XGBoost availability
try:
    import xgboost as xgb
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False
    logger.warning("XGBoost not installed: pip install xgboost")

try:
    import joblib
    JOBLIB_AVAILABLE = True
except ImportError:
    JOBLIB_AVAILABLE = False


# === Feature Engineering ===

FEATURE_NAMES = [
    "delta_pct",       # Signal delta percentage
    "daily_vol_m",     # Daily volume in millions
    "dBTC",            # Change vs BTC now
    "dBTC5m",          # Change vs BTC 5 min
    "dBTC1m",          # Change vs BTC 1 min
    "dMarkets",        # Change vs market now
    "dMarkets24",      # Change vs market 24h
    "buys_per_sec",    # Buys per second (if pump)
    "vol_per_sec",     # Volume per second
    "vol_raise_pct",   # Volume raise percentage
    "hour_of_day",     # Hour (0-23)
    "is_pump",         # Signal type: pump
    "is_drop",         # Signal type: drop
    "is_topmarket",    # Signal type: topmarket
    "strategies_count", # Number of strategies triggered
]


def extract_features(signal: Dict[str, Any]) -> np.ndarray:
    """
    Extract features from a signal dict.

    Returns:
        1D numpy array of features
    """
    # Parse timestamp for hour
    ts = signal.get("timestamp", "")
    hour = 12  # Default to noon
    if ts:
        try:
            if "T" in ts:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                hour = dt.hour
        except:
            pass

    # Signal type flags
    sig_type = signal.get("signal_type", "").lower()

    features = [
        signal.get("delta_pct", 0),
        signal.get("daily_vol_m", 0),
        signal.get("dBTC", 0),
        signal.get("dBTC5m", 0),
        signal.get("dBTC1m", 0),
        signal.get("dMarkets", 0),
        signal.get("dMarkets24", 0),
        signal.get("buys_per_sec", 0),
        signal.get("vol_per_sec_k", 0),
        signal.get("vol_raise_pct", 0),
        hour,
        1 if sig_type == "pump" else 0,
        1 if sig_type == "drop" else 0,
        1 if sig_type == "topmarket" else 0,
        signal.get("strategies_count", 1),
    ]

    return np.array(features, dtype=np.float32)


def extract_label(outcome: Dict[str, Any], horizon: str = "5m") -> int:
    """
    Extract label from outcome dict.

    Returns:
        1 = WIN, 0 = LOSS
    """
    outcomes = outcome.get("outcomes", {})
    horizon_outcome = outcomes.get(horizon, {})
    return 1 if horizon_outcome.get("win", False) else 0


class SignalClassifier:
    """
    XGBoost classifier for trading signals.

    Predicts probability of WIN based on signal features.
    """

    def __init__(self, model_path: Optional[Path] = None):
        """
        Initialize classifier.

        Args:
            model_path: Path to saved model (load if exists)
        """
        self.model: Optional[xgb.XGBClassifier] = None
        self.model_path = model_path or Path("state/ai/signal_classifier.joblib")
        self.feature_names = FEATURE_NAMES
        self.is_trained = False

        # Load existing model
        if self.model_path.exists() and JOBLIB_AVAILABLE:
            try:
                self.model = joblib.load(self.model_path)
                self.is_trained = True
                logger.info(f"Loaded model from {self.model_path}")
            except Exception as e:
                logger.warning(f"Failed to load model: {e}")

    def train(
        self,
        outcomes_file: Path,
        horizon: str = "5m",
        test_size: float = 0.2,
    ) -> Dict[str, float]:
        """
        Train classifier on labeled outcomes.

        Args:
            outcomes_file: Path to outcomes JSONL file
            horizon: Which horizon to use for labels ("1m", "5m", "15m", "60m")
            test_size: Fraction for test set

        Returns:
            Dictionary with training metrics
        """
        if not XGB_AVAILABLE:
            raise RuntimeError("XGBoost not installed")

        # Load data
        outcomes = []
        with open(outcomes_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    outcomes.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        if len(outcomes) < 20:
            raise ValueError(f"Need at least 20 samples, got {len(outcomes)}")

        logger.info(f"Training on {len(outcomes)} samples")

        # Extract features and labels
        X = []
        y = []
        for outcome in outcomes:
            features = extract_features(outcome)
            label = extract_label(outcome, horizon)
            X.append(features)
            y.append(label)

        X = np.array(X)
        y = np.array(y)

        # Split
        from sklearn.model_selection import train_test_split
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=test_size, random_state=42, stratify=y
        )

        # Train
        self.model = xgb.XGBClassifier(
            n_estimators=100,
            max_depth=5,
            learning_rate=0.1,
            objective="binary:logistic",
            eval_metric="auc",
            use_label_encoder=False,
            random_state=42,
        )

        self.model.fit(
            X_train, y_train,
            eval_set=[(X_test, y_test)],
            verbose=False,
        )

        # Evaluate
        from sklearn.metrics import (
            accuracy_score,
            precision_score,
            recall_score,
            f1_score,
            roc_auc_score,
        )

        y_pred = self.model.predict(X_test)
        y_proba = self.model.predict_proba(X_test)[:, 1]

        metrics = {
            "accuracy": accuracy_score(y_test, y_pred),
            "precision": precision_score(y_test, y_pred, zero_division=0),
            "recall": recall_score(y_test, y_pred, zero_division=0),
            "f1": f1_score(y_test, y_pred, zero_division=0),
            "auc": roc_auc_score(y_test, y_proba) if len(np.unique(y_test)) > 1 else 0.5,
            "train_size": len(X_train),
            "test_size": len(X_test),
            "win_rate_train": y_train.mean(),
            "win_rate_test": y_test.mean(),
        }

        logger.info(f"Training complete: AUC={metrics['auc']:.3f}, Precision={metrics['precision']:.3f}")

        # Save model
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        if JOBLIB_AVAILABLE:
            joblib.dump(self.model, self.model_path)
            logger.info(f"Model saved to {self.model_path}")

        self.is_trained = True
        return metrics

    def predict(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """
        Predict outcome for a signal.

        Args:
            signal: Signal dictionary with features

        Returns:
            {
                "win_probability": float (0-1),
                "prediction": "WIN" | "LOSS",
                "confidence": "HIGH" | "MEDIUM" | "LOW",
                "recommendation": "BUY" | "SKIP" | "WATCH"
            }
        """
        if not self.is_trained or self.model is None:
            return {
                "win_probability": 0.5,
                "prediction": "UNKNOWN",
                "confidence": "LOW",
                "recommendation": "SKIP",
                "reason": "Model not trained",
            }

        # Extract features
        features = extract_features(signal).reshape(1, -1)

        # Predict
        proba = self.model.predict_proba(features)[0, 1]

        # Determine confidence and recommendation
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
            "features_used": len(self.feature_names),
        }

    def get_feature_importance(self) -> Dict[str, float]:
        """
        Get feature importance from trained model.

        Returns:
            Dictionary mapping feature name to importance score
        """
        if not self.is_trained or self.model is None:
            return {}

        importances = self.model.feature_importances_
        return {
            name: float(imp)
            for name, imp in zip(self.feature_names, importances)
        }

    def explain_prediction(self, signal: Dict[str, Any]) -> str:
        """
        Generate human-readable explanation for prediction.

        Args:
            signal: Signal dictionary

        Returns:
            Explanation string
        """
        result = self.predict(signal)
        importance = self.get_feature_importance()

        # Sort features by importance
        sorted_features = sorted(importance.items(), key=lambda x: x[1], reverse=True)
        top_features = sorted_features[:5]

        explanation = f"""
🧠 AI PREDICTION: {result['prediction']} ({result['win_probability']*100:.1f}%)

Confidence: {result['confidence']}
Recommendation: {result['recommendation']}

Top factors:
"""
        for name, imp in top_features:
            value = signal.get(name, 0)
            explanation += f"  • {name}: {value} (importance: {imp:.2f})\n"

        return explanation.strip()


# === Quick test ===

def test_classifier():
    """Quick test with synthetic data."""
    if not XGB_AVAILABLE:
        print("XGBoost not installed")
        return

    # Create synthetic training data
    np.random.seed(42)
    n_samples = 200

    # Generate features
    X = np.random.randn(n_samples, len(FEATURE_NAMES))

    # Generate labels (correlated with delta_pct and vol_raise_pct)
    y = ((X[:, 0] > 0.5) & (X[:, 9] > 0)).astype(int)

    # Save as fake outcomes
    outcomes_file = Path("state/ai/test_outcomes.jsonl")
    outcomes_file.parent.mkdir(parents=True, exist_ok=True)

    with open(outcomes_file, "w", encoding="utf-8") as f:
        for i in range(n_samples):
            outcome = {
                "delta_pct": float(X[i, 0]),
                "daily_vol_m": float(X[i, 1]),
                "dBTC": float(X[i, 2]),
                "dBTC5m": float(X[i, 3]),
                "dBTC1m": float(X[i, 4]),
                "dMarkets": float(X[i, 5]),
                "dMarkets24": float(X[i, 6]),
                "buys_per_sec": float(X[i, 7]),
                "vol_per_sec_k": float(X[i, 8]),
                "vol_raise_pct": float(X[i, 9]),
                "signal_type": "pump",
                "outcomes": {
                    "5m": {"win": bool(y[i])}
                }
            }
            f.write(json.dumps(outcome) + "\n")

    # Train
    classifier = SignalClassifier()
    metrics = classifier.train(outcomes_file)

    print("Training metrics:")
    for k, v in metrics.items():
        print(f"  {k}: {v:.3f}" if isinstance(v, float) else f"  {k}: {v}")

    # Predict
    test_signal = {
        "delta_pct": 5.0,
        "daily_vol_m": 10,
        "vol_raise_pct": 50,
        "signal_type": "pump",
    }

    result = classifier.predict(test_signal)
    print(f"\nPrediction: {result}")

    print(f"\nExplanation:\n{classifier.explain_prediction(test_signal)}")


if __name__ == "__main__":
    test_classifier()
