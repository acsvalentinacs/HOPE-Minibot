#!/usr/bin/env python3
"""
HOPE AI Training Pipeline v2.0
==============================
Unified training for all AI models.

Run: python hope_ai_trainer.py --all
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
import argparse

try:
    import numpy as np
    from sklearn.ensemble import RandomForestClassifier, IsolationForest
    from sklearn.model_selection import train_test_split, cross_val_score
    from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
    import joblib
except ImportError:
    print("Installing required packages...")
    os.system("pip install numpy scikit-learn joblib --break-system-packages")
    import numpy as np
    from sklearn.ensemble import RandomForestClassifier, IsolationForest
    from sklearn.model_selection import train_test_split, cross_val_score
    from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
    import joblib

# Paths
SCRIPT_DIR = Path(__file__).parent
STATE_DIR = SCRIPT_DIR / "state" / "ai"
MODELS_DIR = STATE_DIR / "models"

# Ensure directories exist
STATE_DIR.mkdir(parents=True, exist_ok=True)
MODELS_DIR.mkdir(parents=True, exist_ok=True)

class HopeAITrainer:
    """Unified trainer for HOPE AI models"""
    
    def __init__(self):
        self.training_data_file = STATE_DIR / "training_data.jsonl"
        self.anomaly_data_file = STATE_DIR / "anomaly.jsonl"
        self.decisions_file = STATE_DIR / "decisions.jsonl"
        
        # Model files
        self.signal_classifier_file = STATE_DIR / "signal_classifier.joblib"
        self.anomaly_model_file = STATE_DIR / "anomaly_model.joblib"
        
        # Feature names
        self.feature_names = [
            'delta_1m', 'delta_5m', 'volume_ratio', 'buy_dominance',
            'rsi_14', 'atr_pct', 'btc_correlation', 'hour_of_day',
            'day_of_week', 'volatility_24h'
        ]
    
    def load_training_data(self):
        """Load and parse training data"""
        data = []
        
        # Load from training_data.jsonl
        if self.training_data_file.exists():
            with open(self.training_data_file, 'r') as f:
                for line in f:
                    try:
                        record = json.loads(line.strip())
                        data.append(record)
                    except:
                        continue
        
        # Load from decisions.jsonl
        if self.decisions_file.exists():
            with open(self.decisions_file, 'r') as f:
                for line in f:
                    try:
                        record = json.loads(line.strip())
                        if 'features' in record and 'outcome' in record:
                            data.append(record)
                    except:
                        continue
        
        print(f"Loaded {len(data)} training records")
        return data
    
    def prepare_features(self, data):
        """Convert data to feature matrix"""
        X = []
        y = []
        
        for record in data:
            features = record.get('features', {})
            outcome = record.get('outcome', '')
            
            # Skip incomplete records
            if not features or not outcome:
                continue
            
            # Extract features (with defaults)
            feature_vector = [
                features.get('delta_1m', 0),
                features.get('delta_5m', 0),
                features.get('volume_ratio', 1),
                features.get('buy_dominance', 0.5),
                features.get('rsi_14', 50),
                features.get('atr_pct', 1),
                features.get('btc_correlation', 0),
                features.get('hour_of_day', 12),
                features.get('day_of_week', 3),
                features.get('volatility_24h', 1)
            ]
            
            # Label: 1 = win, 0 = loss
            label = 1 if outcome in ['TP_HIT', 'WIN', 'PROFIT'] else 0
            
            X.append(feature_vector)
            y.append(label)
        
        return np.array(X), np.array(y)
    
    def train_signal_classifier(self, X, y):
        """Train the signal classifier model"""
        print("\n" + "="*60)
        print("TRAINING SIGNAL CLASSIFIER")
        print("="*60)
        
        if len(X) < 20:
            print("⚠️  Not enough data for training (need at least 20 samples)")
            return None
        
        # Split data
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )
        
        print(f"Training samples: {len(X_train)}")
        print(f"Test samples: {len(X_test)}")
        print(f"Positive ratio: {y.mean():.2%}")
        
        # Train model
        model = RandomForestClassifier(
            n_estimators=100,
            max_depth=10,
            min_samples_split=5,
            random_state=42,
            class_weight='balanced'
        )
        
        model.fit(X_train, y_train)
        
        # Evaluate
        y_pred = model.predict(X_test)
        
        accuracy = accuracy_score(y_test, y_pred)
        precision = precision_score(y_test, y_pred, zero_division=0)
        recall = recall_score(y_test, y_pred, zero_division=0)
        f1 = f1_score(y_test, y_pred, zero_division=0)
        
        print(f"\n📊 Model Performance:")
        print(f"   Accuracy:  {accuracy:.2%}")
        print(f"   Precision: {precision:.2%}")
        print(f"   Recall:    {recall:.2%}")
        print(f"   F1-Score:  {f1:.2%}")
        
        # Cross-validation
        cv_scores = cross_val_score(model, X, y, cv=5)
        print(f"\n📈 Cross-Validation: {cv_scores.mean():.2%} (+/- {cv_scores.std()*2:.2%})")
        
        # Feature importance
        print(f"\n🔑 Feature Importance:")
        importance = list(zip(self.feature_names, model.feature_importances_))
        importance.sort(key=lambda x: x[1], reverse=True)
        for name, imp in importance[:5]:
            print(f"   {name}: {imp:.3f}")
        
        # Save model
        joblib.dump(model, self.signal_classifier_file)
        print(f"\n✅ Model saved to: {self.signal_classifier_file}")
        
        return {
            'accuracy': accuracy,
            'precision': precision,
            'recall': recall,
            'f1': f1,
            'cv_mean': cv_scores.mean(),
            'cv_std': cv_scores.std()
        }
    
    def train_anomaly_detector(self):
        """Train anomaly detection model"""
        print("\n" + "="*60)
        print("TRAINING ANOMALY DETECTOR")
        print("="*60)
        
        data = []
        
        # Load anomaly data
        if self.anomaly_data_file.exists():
            with open(self.anomaly_data_file, 'r') as f:
                for line in f:
                    try:
                        record = json.loads(line.strip())
                        data.append(record)
                    except:
                        continue
        
        if len(data) < 50:
            print("⚠️  Not enough anomaly data (need at least 50 samples)")
            return None
        
        print(f"Loaded {len(data)} anomaly records")
        
        # Prepare features
        X = []
        for record in data:
            features = [
                record.get('delta', 0),
                record.get('volume', 0),
                record.get('buy_ratio', 0.5),
                record.get('spread', 0),
                record.get('trades_per_sec', 0)
            ]
            X.append(features)
        
        X = np.array(X)
        
        # Train Isolation Forest
        model = IsolationForest(
            n_estimators=100,
            contamination=0.1,
            random_state=42
        )
        
        model.fit(X)
        
        # Evaluate
        predictions = model.predict(X)
        anomalies = (predictions == -1).sum()
        
        print(f"\n📊 Anomaly Detection Results:")
        print(f"   Total samples: {len(X)}")
        print(f"   Anomalies detected: {anomalies} ({anomalies/len(X):.1%})")
        
        # Save model
        joblib.dump(model, self.anomaly_model_file)
        print(f"\n✅ Model saved to: {self.anomaly_model_file}")
        
        return {
            'total_samples': len(X),
            'anomalies': anomalies,
            'anomaly_rate': anomalies / len(X)
        }
    
    def generate_synthetic_data(self, n_samples=500):
        """Generate synthetic training data for testing"""
        print("\n" + "="*60)
        print("GENERATING SYNTHETIC TRAINING DATA")
        print("="*60)
        
        np.random.seed(42)
        
        records = []
        for i in range(n_samples):
            # Generate features
            delta_1m = np.random.uniform(-2, 5)
            delta_5m = np.random.uniform(-3, 8)
            volume_ratio = np.random.uniform(0.5, 5)
            buy_dominance = np.random.uniform(0.3, 0.9)
            rsi_14 = np.random.uniform(20, 80)
            atr_pct = np.random.uniform(0.5, 3)
            btc_correlation = np.random.uniform(-0.5, 0.9)
            hour = np.random.randint(0, 24)
            day = np.random.randint(0, 7)
            volatility = np.random.uniform(1, 5)
            
            # Determine outcome based on features (with some logic)
            score = (
                (delta_1m > 1) * 0.2 +
                (delta_5m > 2) * 0.2 +
                (volume_ratio > 2) * 0.15 +
                (buy_dominance > 0.6) * 0.15 +
                (30 < rsi_14 < 70) * 0.1 +
                (atr_pct < 2) * 0.1 +
                (btc_correlation > 0.5) * 0.1
            )
            
            # Add some noise
            score += np.random.uniform(-0.2, 0.2)
            outcome = "TP_HIT" if score > 0.5 else "SL_HIT"
            
            record = {
                "timestamp": datetime.now().isoformat(),
                "symbol": f"COIN{i}USDT",
                "features": {
                    "delta_1m": round(delta_1m, 4),
                    "delta_5m": round(delta_5m, 4),
                    "volume_ratio": round(volume_ratio, 4),
                    "buy_dominance": round(buy_dominance, 4),
                    "rsi_14": round(rsi_14, 2),
                    "atr_pct": round(atr_pct, 4),
                    "btc_correlation": round(btc_correlation, 4),
                    "hour_of_day": hour,
                    "day_of_week": day,
                    "volatility_24h": round(volatility, 4)
                },
                "outcome": outcome,
                "ai_confidence": round(score, 4)
            }
            records.append(record)
        
        # Save to file
        with open(self.training_data_file, 'w') as f:
            for record in records:
                f.write(json.dumps(record) + '\n')
        
        wins = sum(1 for r in records if r['outcome'] == 'TP_HIT')
        print(f"Generated {n_samples} samples")
        print(f"Win rate: {wins/n_samples:.1%}")
        print(f"Saved to: {self.training_data_file}")
        
        return records
    
    def train_all(self):
        """Train all models"""
        print("\n" + "="*60)
        print("🚀 HOPE AI TRAINING PIPELINE v2.0")
        print("="*60)
        print(f"Started at: {datetime.now()}")
        
        results = {}
        
        # Load data
        data = self.load_training_data()
        
        if len(data) < 20:
            print("\n⚠️  Not enough real data. Generating synthetic data...")
            data = self.generate_synthetic_data(500)
        
        # Prepare features
        X, y = self.prepare_features(data)
        
        if len(X) > 0:
            # Train signal classifier
            results['signal_classifier'] = self.train_signal_classifier(X, y)
        
        # Train anomaly detector
        results['anomaly_detector'] = self.train_anomaly_detector()
        
        # Summary
        print("\n" + "="*60)
        print("📋 TRAINING SUMMARY")
        print("="*60)
        
        if results.get('signal_classifier'):
            sc = results['signal_classifier']
            print(f"\nSignal Classifier:")
            print(f"  ✅ Accuracy: {sc['accuracy']:.2%}")
            print(f"  ✅ F1-Score: {sc['f1']:.2%}")
        
        if results.get('anomaly_detector'):
            ad = results['anomaly_detector']
            print(f"\nAnomaly Detector:")
            print(f"  ✅ Samples: {ad['total_samples']}")
            print(f"  ✅ Anomaly rate: {ad['anomaly_rate']:.1%}")
        
        print(f"\n✅ Training completed at: {datetime.now()}")
        
        return results
    
    def validate_models(self):
        """Validate loaded models"""
        print("\n" + "="*60)
        print("🔍 VALIDATING MODELS")
        print("="*60)
        
        # Check signal classifier
        if self.signal_classifier_file.exists():
            model = joblib.load(self.signal_classifier_file)
            print(f"✅ Signal Classifier: Loaded ({type(model).__name__})")
            
            # Test prediction
            test_features = np.array([[1.5, 2.0, 2.5, 0.7, 55, 1.2, 0.6, 14, 3, 2.0]])
            prob = model.predict_proba(test_features)[0]
            print(f"   Test prediction: {prob[1]:.2%} confidence")
        else:
            print("❌ Signal Classifier: NOT FOUND")
        
        # Check anomaly model
        if self.anomaly_model_file.exists():
            model = joblib.load(self.anomaly_model_file)
            print(f"✅ Anomaly Detector: Loaded ({type(model).__name__})")
            
            # Test prediction
            test_features = np.array([[2.0, 1000000, 0.7, 0.001, 50]])
            pred = model.predict(test_features)[0]
            print(f"   Test prediction: {'Normal' if pred == 1 else 'Anomaly'}")
        else:
            print("❌ Anomaly Detector: NOT FOUND")

def main():
    parser = argparse.ArgumentParser(description="HOPE AI Trainer")
    parser.add_argument("--all", action="store_true", help="Train all models")
    parser.add_argument("--signal", action="store_true", help="Train signal classifier only")
    parser.add_argument("--anomaly", action="store_true", help="Train anomaly detector only")
    parser.add_argument("--generate", action="store_true", help="Generate synthetic data")
    parser.add_argument("--validate", action="store_true", help="Validate models")
    parser.add_argument("--samples", type=int, default=500, help="Number of synthetic samples")
    
    args = parser.parse_args()
    
    trainer = HopeAITrainer()
    
    if args.generate:
        trainer.generate_synthetic_data(args.samples)
    elif args.validate:
        trainer.validate_models()
    elif args.signal:
        data = trainer.load_training_data()
        X, y = trainer.prepare_features(data)
        trainer.train_signal_classifier(X, y)
    elif args.anomaly:
        trainer.train_anomaly_detector()
    else:
        # Default: train all
        trainer.train_all()

if __name__ == "__main__":
    main()
