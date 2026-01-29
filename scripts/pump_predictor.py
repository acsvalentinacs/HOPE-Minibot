# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 08:20:00 UTC
# Purpose: Pump Predictor - ML model for predicting pumps from MoonBot signals
# === END SIGNATURE ===
"""
Pump Predictor — ML модель для предсказания пампов.

ЗАПУСК:
    python scripts/pump_predictor.py train     # Обучить модель
    python scripts/pump_predictor.py predict   # Real-time предсказания
    python scripts/pump_predictor.py evaluate  # Оценить качество

FEATURES:
    - GradientBoosting + RandomForest ensemble
    - Feature importance analysis
    - Cross-validation
    - Real-time prediction API
"""
import json
import logging
import sys
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("pump_predictor")

# Paths
LABELED_FILE = Path("state/ai/signals/labeled_signals.jsonl")
OUTCOMES_FILE = Path("data/moonbot_signals/outcomes.jsonl")
MODEL_DIR = Path("state/ai/models")
MODEL_FILE = MODEL_DIR / "pump_predictor.pkl"

MODEL_DIR.mkdir(parents=True, exist_ok=True)

# Check sklearn availability
try:
    from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier, VotingClassifier
    from sklearn.model_selection import train_test_split, cross_val_score, StratifiedKFold
    from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score, precision_recall_curve
    from sklearn.preprocessing import StandardScaler
    import joblib
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    logger.warning("sklearn not installed: pip install scikit-learn")


@dataclass
class PredictionResult:
    """Результат предсказания."""
    prediction: str  # "PUMP", "NO_PUMP"
    probability: float  # 0-1
    confidence: str  # "HIGH", "MEDIUM", "LOW"
    recommendation: str  # "BUY", "WATCH", "SKIP"
    features_used: int
    top_factors: List[Tuple[str, float]]


class PumpPredictor:
    """
    Предсказатель пампов на основе сигналов MoonBot.

    Использует ансамбль GradientBoosting + RandomForest.
    """

    FEATURES = [
        "delta_pct",
        "dbtc",
        "dbtc_5m",
        "dbtc_1m",
        "dbtc_24h",
        "dbtc_72h",
        "dmarkets",
        "dmarkets_24h",
        "daily_volume",
        "hourly_volume",
        "vol_raise_pct",
        "buys_per_sec",
        "buyers_count",
        "vol_per_sec",
        "ppl_per_sec",
        # Derived features
        "delta_momentum",  # dbtc_5m - dbtc_1m
        "market_strength",  # dmarkets - dmarkets_24h
        "volume_intensity",  # vol_per_sec * buys_per_sec
    ]

    # Signal type one-hot
    SIGNAL_TYPES = ["PumpDetect", "DropDetect", "TopMarket", "Delta"]

    def __init__(self):
        self.model = None
        self.scaler = StandardScaler()
        self.is_trained = False
        self.feature_names = self.FEATURES + [f"is_{t}" for t in self.SIGNAL_TYPES]

    def _extract_features(self, signal: dict) -> np.ndarray:
        """Извлечь признаки из сигнала."""
        features = []

        # Basic features
        for f in self.FEATURES[:15]:  # Original features
            val = signal.get(f, 0)
            if val is None:
                val = 0
            features.append(float(val))

        # Derived features
        dbtc_5m = signal.get("dbtc_5m", 0) or 0
        dbtc_1m = signal.get("dbtc_1m", 0) or 0
        features.append(dbtc_5m - dbtc_1m)  # delta_momentum

        dmarkets = signal.get("dmarkets", 0) or 0
        dmarkets_24h = signal.get("dmarkets_24h", 0) or 0
        features.append(dmarkets - dmarkets_24h)  # market_strength

        vol_per_sec = signal.get("vol_per_sec", 0) or 0
        buys_per_sec = signal.get("buys_per_sec", 0) or 0
        features.append(vol_per_sec * buys_per_sec / 1000)  # volume_intensity (scaled)

        # Signal type one-hot encoding
        sig_type = signal.get("signal_type", "")
        for t in self.SIGNAL_TYPES:
            features.append(1.0 if sig_type == t else 0.0)

        return np.array(features, dtype=np.float32)

    def _load_data(self) -> Tuple[np.ndarray, np.ndarray, List[dict]]:
        """Загрузить размеченные данные."""
        X, y = [], []
        raw_signals = []

        # Try multiple data sources
        data_files = [LABELED_FILE, OUTCOMES_FILE]

        for data_file in data_files:
            if not data_file.exists():
                continue

            with open(data_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        sig = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Need label or outcomes
                    label = sig.get("label")
                    if label is None:
                        # Try to infer from outcomes
                        outcomes = sig.get("outcomes", {})
                        outcome_5m = outcomes.get("5m", {})
                        if outcome_5m.get("win"):
                            label = "pump"
                        elif outcome_5m.get("change_pct", 0) < -0.5:
                            label = "dump"
                        else:
                            label = "flat"

                    if label is None:
                        continue

                    features = self._extract_features(sig)
                    X.append(features)

                    # Binary classification: pump vs no_pump
                    is_pump = 1 if label in ["pump", "strong_pump"] else 0
                    y.append(is_pump)
                    raw_signals.append(sig)

        return np.array(X), np.array(y), raw_signals

    def train(self, min_samples: int = 50) -> Dict[str, float]:
        """
        Обучить модель.

        Args:
            min_samples: Минимальное количество примеров для обучения

        Returns:
            Метрики качества
        """
        if not SKLEARN_AVAILABLE:
            raise RuntimeError("sklearn not installed")

        logger.info("Loading labeled data...")
        X, y, raw = self._load_data()

        if len(X) < min_samples:
            logger.warning(f"Not enough data: {len(X)} < {min_samples}")
            return {"error": "not_enough_data", "samples": len(X)}

        pump_ratio = y.mean()
        logger.info(f"Dataset: {len(X)} samples, {y.sum()} pumps ({pump_ratio*100:.1f}%)")

        # Handle class imbalance
        if pump_ratio < 0.1 or pump_ratio > 0.9:
            logger.warning("Class imbalance detected, using class_weight='balanced'")

        # Scale features
        X_scaled = self.scaler.fit_transform(X)

        # Split
        X_train, X_test, y_train, y_test = train_test_split(
            X_scaled, y, test_size=0.2, random_state=42, stratify=y
        )

        # Create ensemble
        logger.info("Training ensemble model...")

        gb = GradientBoostingClassifier(
            n_estimators=100,
            max_depth=5,
            learning_rate=0.1,
            random_state=42
        )

        rf = RandomForestClassifier(
            n_estimators=100,
            max_depth=7,
            class_weight="balanced",
            random_state=42
        )

        self.model = VotingClassifier(
            estimators=[("gb", gb), ("rf", rf)],
            voting="soft"
        )

        self.model.fit(X_train, y_train)

        # Evaluate
        y_pred = self.model.predict(X_test)
        y_proba = self.model.predict_proba(X_test)[:, 1]

        logger.info("\n" + "=" * 50)
        logger.info("CLASSIFICATION REPORT")
        logger.info("=" * 50)
        logger.info("\n" + classification_report(y_test, y_pred, target_names=["no_pump", "pump"]))

        # Confusion matrix
        cm = confusion_matrix(y_test, y_pred)
        logger.info(f"Confusion Matrix:\n{cm}")

        # AUC
        auc = roc_auc_score(y_test, y_proba) if len(np.unique(y_test)) > 1 else 0.5
        logger.info(f"ROC AUC: {auc:.3f}")

        # Cross-validation
        cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
        cv_scores = cross_val_score(self.model, X_scaled, y, cv=cv, scoring="roc_auc")
        logger.info(f"CV AUC: {cv_scores.mean():.3f} (+/- {cv_scores.std()*2:.3f})")

        # Feature importance (from GB)
        logger.info("\n" + "=" * 50)
        logger.info("FEATURE IMPORTANCE")
        logger.info("=" * 50)
        gb_model = self.model.named_estimators_["gb"]
        importances = list(zip(self.feature_names, gb_model.feature_importances_))
        importances.sort(key=lambda x: -x[1])
        for name, imp in importances[:10]:
            logger.info(f"  {name}: {imp:.3f}")

        # Save model
        joblib.dump({
            "model": self.model,
            "scaler": self.scaler,
            "feature_names": self.feature_names,
        }, MODEL_FILE)
        logger.info(f"\nModel saved to {MODEL_FILE}")

        self.is_trained = True

        return {
            "samples": len(X),
            "pump_ratio": float(pump_ratio),
            "auc": float(auc),
            "cv_auc_mean": float(cv_scores.mean()),
            "cv_auc_std": float(cv_scores.std()),
        }

    def load(self) -> bool:
        """Загрузить модель."""
        if not MODEL_FILE.exists():
            logger.warning(f"Model file not found: {MODEL_FILE}")
            return False

        try:
            data = joblib.load(MODEL_FILE)
            self.model = data["model"]
            self.scaler = data["scaler"]
            self.feature_names = data.get("feature_names", self.feature_names)
            self.is_trained = True
            logger.info(f"Model loaded from {MODEL_FILE}")
            return True
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            return False

    def predict(self, signal: dict) -> PredictionResult:
        """
        Предсказать вероятность пампа.

        Args:
            signal: Словарь с данными сигнала

        Returns:
            PredictionResult с предсказанием
        """
        if not self.is_trained or self.model is None:
            return PredictionResult(
                prediction="UNKNOWN",
                probability=0.5,
                confidence="LOW",
                recommendation="SKIP",
                features_used=0,
                top_factors=[],
            )

        # Extract and scale features
        features = self._extract_features(signal)
        X = self.scaler.transform([features])

        # Predict
        pred = self.model.predict(X)[0]
        prob = self.model.predict_proba(X)[0][1]

        # Determine confidence and recommendation
        if prob >= 0.75:
            confidence = "HIGH"
            recommendation = "BUY"
        elif prob >= 0.6:
            confidence = "MEDIUM"
            recommendation = "WATCH"
        elif prob >= 0.4:
            confidence = "LOW"
            recommendation = "WATCH"
        else:
            confidence = "LOW"
            recommendation = "SKIP"

        # Get top factors
        gb_model = self.model.named_estimators_["gb"]
        importances = list(zip(self.feature_names, gb_model.feature_importances_))
        importances.sort(key=lambda x: -x[1])
        top_factors = [(name, float(features[self.feature_names.index(name)]))
                       for name, _ in importances[:5]]

        return PredictionResult(
            prediction="PUMP" if pred == 1 else "NO_PUMP",
            probability=float(prob),
            confidence=confidence,
            recommendation=recommendation,
            features_used=len(self.feature_names),
            top_factors=top_factors,
        )

    def explain(self, signal: dict) -> str:
        """Объяснить предсказание."""
        result = self.predict(signal)

        explanation = f"""
{'='*50}
🧠 AI PUMP PREDICTION
{'='*50}

Symbol: {signal.get('symbol', 'N/A')}
Signal Type: {signal.get('signal_type', 'N/A')}
Delta: {signal.get('delta_pct', 0):+.2f}%

PREDICTION: {result.prediction}
Probability: {result.probability*100:.1f}%
Confidence: {result.confidence}
Recommendation: {result.recommendation}

Top Factors:
"""
        for name, value in result.top_factors:
            explanation += f"  • {name}: {value:.4f}\n"

        explanation += "=" * 50
        return explanation


def interactive_mode():
    """Интерактивный режим предсказания."""
    predictor = PumpPredictor()
    if not predictor.load():
        logger.error("No trained model found. Run: python pump_predictor.py train")
        return

    print("\n" + "=" * 50)
    print("🤖 PUMP PREDICTOR - Interactive Mode")
    print("Enter signal data as JSON or 'quit' to exit")
    print("=" * 50 + "\n")

    while True:
        try:
            user_input = input("Signal JSON> ").strip()
            if user_input.lower() in ["quit", "exit", "q"]:
                break

            signal = json.loads(user_input)
            print(predictor.explain(signal))

        except json.JSONDecodeError:
            print("Invalid JSON. Try again.")
        except KeyboardInterrupt:
            break

    print("Goodbye!")


def main():
    if len(sys.argv) < 2:
        print("Usage: python pump_predictor.py [train|predict|evaluate|interactive]")
        return

    predictor = PumpPredictor()
    command = sys.argv[1].lower()

    if command == "train":
        metrics = predictor.train()
        print(f"\nTraining complete: {json.dumps(metrics, indent=2)}")

    elif command == "predict":
        if not predictor.load():
            print("Train model first: python pump_predictor.py train")
            return

        # Example prediction
        test_signal = {
            "symbol": "SYNUSDT",
            "signal_type": "TopMarket",
            "delta_pct": 9.51,
            "dbtc": -0.04,
            "dbtc_5m": 0.10,
            "dbtc_1m": 0.07,
            "dbtc_24h": -1.03,
            "dbtc_72h": -0.83,
            "dmarkets": 0.04,
            "dmarkets_24h": -1.91,
            "daily_volume": 6_000_000,
            "vol_raise_pct": 71.5,
            "buys_per_sec": 0.53,
            "buyers_count": 59,
        }

        print(predictor.explain(test_signal))

    elif command == "evaluate":
        if not predictor.load():
            print("Train model first")
            return

        X, y, _ = predictor._load_data()
        if len(X) == 0:
            print("No data to evaluate")
            return

        X_scaled = predictor.scaler.transform(X)
        y_pred = predictor.model.predict(X_scaled)
        y_proba = predictor.model.predict_proba(X_scaled)[:, 1]

        print(classification_report(y, y_pred, target_names=["no_pump", "pump"]))
        print(f"AUC: {roc_auc_score(y, y_proba):.3f}")

    elif command == "interactive":
        interactive_mode()

    else:
        print(f"Unknown command: {command}")


if __name__ == "__main__":
    main()
