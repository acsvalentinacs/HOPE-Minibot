# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 17:10:00 UTC
# Purpose: HOPE AI Live Learning System - learns from MoonBot signals + outcomes
# sha256: live_learning_v1.0
# === END SIGNATURE ===
"""
HOPE AI - Live Learning System v1.0

🧠 ОБУЧЕНИЕ ИИ НА РЕАЛЬНЫХ СИГНАЛАХ В РЕАЛЬНОМ ВРЕМЕНИ

Архитектура:
┌─────────────────────────────────────────────────────────────────────────────┐
│                          LIVE LEARNING LOOP                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   MoonBot Signal                     Outcome (5 min later)                  │
│   ──────────────                     ────────────────────                   │
│   delta: 2.5%                        MFE: +1.2%                             │
│   vol_raise: 242%          ───────>  MAE: -0.3%                             │
│   buys_sec: 32             COLLECT   WIN: true                              │
│   strategy: Delta                                                            │
│                                                                              │
│                      ↓                                                       │
│              ┌──────────────┐                                               │
│              │   DATASET    │  ← Накапливаем пары (features, outcome)       │
│              │   (JSONL)    │                                               │
│              └──────┬───────┘                                               │
│                     │                                                        │
│                     ↓ каждые 100 samples                                    │
│              ┌──────────────┐                                               │
│              │   RETRAIN    │  ← Переобучаем модель                         │
│              │   ML MODEL   │                                               │
│              └──────┬───────┘                                               │
│                     │                                                        │
│                     ↓                                                        │
│              ┌──────────────┐                                               │
│              │   IMPROVED   │  ← Новые пороги, веса                         │
│              │  THRESHOLDS  │                                               │
│              └──────────────┘                                               │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

Features Extracted from MoonBot:
- symbol (categorical → one-hot or embedding)
- strategy (PumpDetection, Delta, TopMarket, Drops, Volumes)
- direction (Long/Short)
- delta_pct (numeric)
- buys_per_sec (numeric)
- vol_per_sec (numeric)
- vol_raise_pct (numeric)
- buyers_count (numeric)
- daily_volume (numeric)
- dBTC (BTC correlation)
- dBTC5m (5min BTC change)
- dMarkets (market sentiment)
- hour_of_day (time feature)
- day_of_week (time feature)

Target:
- MFE (Maximum Favorable Excursion)
- MAE (Maximum Adverse Excursion)
- win (MFE > |MAE|)
- optimal_target (best exit point)
- optimal_stop (best stop level)

Models:
1. Win Probability Model (classification)
2. MFE Predictor (regression)
3. MAE Predictor (regression)
4. Optimal Target/Stop (regression)
"""

import json
import re
import hashlib
import logging
import pickle
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, asdict, field
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
from collections import defaultdict
import statistics

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    from sklearn.ensemble import RandomForestClassifier, GradientBoostingRegressor
    from sklearn.preprocessing import StandardScaler, LabelEncoder
    from sklearn.model_selection import cross_val_score
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

DATA_DIR = Path("state/ai/learning")
DATASET_FILE = DATA_DIR / "training_data.jsonl"
MODEL_FILE = DATA_DIR / "model.pkl"
THRESHOLDS_FILE = DATA_DIR / "learned_thresholds.json"
STATS_FILE = DATA_DIR / "learning_stats.json"

# Retraining triggers
MIN_SAMPLES_FOR_TRAINING = 30  # Lowered for early training
RETRAIN_EVERY_N_SAMPLES = 100

# Feature configuration
NUMERIC_FEATURES = [
    'delta_pct', 'buys_per_sec', 'vol_per_sec', 'vol_raise_pct',
    'buyers_count', 'daily_volume_m', 'dBTC', 'dBTC5m', 'dBTC1m',
    'dMarkets', 'hour', 'minute'
]

CATEGORICAL_FEATURES = ['strategy', 'direction', 'symbol_group']

# Strategy groups for feature engineering
SYMBOL_GROUPS = {
    'meme': ['DOGEUSDT', 'SHIBUSDT', 'PEPEUSDT', 'FLOKIUSDT'],
    'defi': ['AAVEUSDT', 'UNIUSDT', 'COMPUSDT', 'MKRUSDT'],
    'gaming': ['AXSUSDT', 'SANDUSDT', 'MANAUSDT', 'ENJUSDT'],
    'layer1': ['SOLUSDT', 'AVAXUSDT', 'NEARUSDT', 'ATOMUSDT'],
    'ai': ['FETUSDT', 'AGIXUSDT', 'OCEANUSDT', 'RENDERUSDT'],
    'storage': ['FILUSDT', 'ARUSDT', 'STORJUSDT'],
}


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SignalFeatures:
    """Features extracted from MoonBot signal"""
    # Identifiers
    signal_id: str
    timestamp: str
    symbol: str
    
    # Strategy
    strategy: str  # PumpDetection, Delta, TopMarket, DropsDetection, Volumes
    direction: str  # Long, Short
    
    # Core metrics
    price: float
    delta_pct: float
    buys_per_sec: float
    vol_per_sec: float
    vol_raise_pct: float
    buyers_count: int
    daily_volume_m: float  # in millions
    
    # BTC correlation
    dBTC: float = 0.0
    dBTC5m: float = 0.0
    dBTC1m: float = 0.0
    
    # Market sentiment
    dMarkets: float = 0.0
    dMarkets24: float = 0.0
    
    # Time features
    hour: int = 0
    minute: int = 0
    day_of_week: int = 0
    
    # Derived
    symbol_group: str = "other"
    
    def to_feature_dict(self) -> Dict[str, Any]:
        """Convert to feature dictionary for ML"""
        return {
            'delta_pct': self.delta_pct,
            'buys_per_sec': self.buys_per_sec,
            'vol_per_sec': self.vol_per_sec,
            'vol_raise_pct': self.vol_raise_pct,
            'buyers_count': self.buyers_count,
            'daily_volume_m': self.daily_volume_m,
            'dBTC': self.dBTC,
            'dBTC5m': self.dBTC5m,
            'dBTC1m': self.dBTC1m,
            'dMarkets': self.dMarkets,
            'hour': self.hour,
            'minute': self.minute,
            'strategy': self.strategy,
            'direction': self.direction,
            'symbol_group': self.symbol_group,
        }


@dataclass
class Outcome:
    """Trade outcome"""
    signal_id: str
    mfe: float  # Maximum Favorable Excursion (%)
    mae: float  # Maximum Adverse Excursion (%)
    final_pnl: float  # Final P&L (%)
    duration_sec: int
    exit_reason: str  # target, stop, timeout, manual
    
    @property
    def win(self) -> bool:
        return self.mfe > abs(self.mae) * 0.5


@dataclass
class TrainingSample:
    """Complete training sample (features + outcome)"""
    features: SignalFeatures
    outcome: Outcome
    collected_at: str = ""
    
    def to_dict(self) -> Dict:
        return {
            'features': asdict(self.features),
            'outcome': asdict(self.outcome),
            'collected_at': self.collected_at,
        }
    
    @classmethod
    def from_dict(cls, d: Dict) -> 'TrainingSample':
        features = SignalFeatures(**d['features'])
        outcome = Outcome(**d['outcome'])
        return cls(features=features, outcome=outcome, collected_at=d.get('collected_at', ''))


# ═══════════════════════════════════════════════════════════════════════════════
# MOONBOT PARSER (Enhanced for Learning)
# ═══════════════════════════════════════════════════════════════════════════════

class EnhancedMoonBotParser:
    """Parses MoonBot signals and extracts ALL features for ML"""
    
    # Comprehensive regex patterns
    PUMP_PATTERN = re.compile(
        r'(\d{2}:\d{2}:\d{2}).*USDT-(\w+).*(?:Pump|pump).*'
        r'DailyVol:\s*([\d.]+)m.*Buys/sec:\s*([\d.]+).*'
        r'Vol/sec:\s*([\d.]+)\s*k.*PriceDelta:\s*([\d.]+)%',
        re.IGNORECASE
    )
    
    DELTA_PATTERN = re.compile(
        r'(\d{2}:\d{2}:\d{2}).*USDT-(\w+).*Ask:([\d.]+).*'
        r'dBTC:\s*([-\d.]+).*dBTC5m:\s*([-\d.]+).*dBTC1m:\s*([-\d.]+).*'
        r'dMarkets:\s*([-\d.]+).*dMarkets24:\s*([-\d.]+).*'
        r'Delta:.*Delta:\s*([\d.]+)%.*VolRaise:\s*([\d.]+)%.*Buyers:\s*(\d+).*Vol/Sec:\s*([\d.]+)\s*k',
        re.IGNORECASE
    )
    
    TOPMARKET_PATTERN = re.compile(
        r'(\d{2}:\d{2}:\d{2}).*USDT-(\w+).*Ask:([\d.]+).*'
        r'dBTC:\s*([-\d.]+).*dBTC5m:\s*([-\d.]+).*dBTC1m:\s*([-\d.]+).*'
        r'dMarkets:\s*([-\d.]+).*dMarkets24:\s*([-\d.]+).*'
        r'TopMarket.*Delta:\s*([\d.]+)%\s+(\w+)',
        re.IGNORECASE
    )
    
    DROP_PATTERN = re.compile(
        r'(\d{2}:\d{2}:\d{2}).*USDT-(\w+).*Ask:([\d.]+).*'
        r'dBTC:\s*([-\d.]+).*dBTC5m:\s*([-\d.]+).*dBTC1m:\s*([-\d.]+).*'
        r'dMarkets:\s*([-\d.]+).*dMarkets24:\s*([-\d.]+).*'
        r'(?:Drop|drop).*xPriceDelta:\s*([\d.]+)',
        re.IGNORECASE
    )
    
    def __init__(self):
        self.today = datetime.now(timezone.utc).date()
    
    def _get_symbol_group(self, symbol: str) -> str:
        """Determine symbol group"""
        for group, symbols in SYMBOL_GROUPS.items():
            if symbol in symbols:
                return group
        return "other"
    
    def parse(self, line: str) -> Optional[SignalFeatures]:
        """Parse MoonBot line into features"""
        line = line.strip()
        if not line or 'Signal' not in line:
            return None
        
        # Try Delta pattern (most comprehensive)
        match = self.DELTA_PATTERN.search(line)
        if match:
            groups = match.groups()
            time_str = groups[0]
            symbol = f"{groups[1]}USDT"
            price = float(groups[2])
            dBTC = float(groups[3])
            dBTC5m = float(groups[4])
            dBTC1m = float(groups[5])
            dMarkets = float(groups[6])
            dMarkets24 = float(groups[7])
            delta_pct = float(groups[8])
            vol_raise = float(groups[9])
            buyers = int(groups[10])
            vol_sec = float(groups[11]) * 1000
            
            # Extract daily volume
            daily_vol_match = re.search(r'DailyVol:\s*([\d.]+)m', line)
            daily_vol = float(daily_vol_match.group(1)) if daily_vol_match else 0
            
            # Parse time
            dt = datetime.combine(self.today, datetime.strptime(time_str, "%H:%M:%S").time())
            
            return SignalFeatures(
                signal_id=f"sig_{int(dt.timestamp()*1000)}_{symbol}",
                timestamp=dt.isoformat(),
                symbol=symbol,
                strategy="Delta",
                direction="Long",
                price=price,
                delta_pct=delta_pct,
                buys_per_sec=0,  # Not in Delta
                vol_per_sec=vol_sec,
                vol_raise_pct=vol_raise,
                buyers_count=buyers,
                daily_volume_m=daily_vol,
                dBTC=dBTC,
                dBTC5m=dBTC5m,
                dBTC1m=dBTC1m,
                dMarkets=dMarkets,
                dMarkets24=dMarkets24,
                hour=dt.hour,
                minute=dt.minute,
                day_of_week=dt.weekday(),
                symbol_group=self._get_symbol_group(symbol),
            )
        
        # Try PumpDetection
        match = self.PUMP_PATTERN.search(line)
        if match:
            groups = match.groups()
            time_str = groups[0]
            symbol = f"{groups[1]}USDT"
            daily_vol = float(groups[2])
            buys_sec = float(groups[3])
            vol_sec = float(groups[4]) * 1000
            delta_pct = float(groups[5])
            
            # Extract BTC correlation from full line
            dBTC = self._extract_float(line, r'dBTC:\s*([-\d.]+)')
            dBTC5m = self._extract_float(line, r'dBTC5m:\s*([-\d.]+)')
            dBTC1m = self._extract_float(line, r'dBTC1m:\s*([-\d.]+)')
            dMarkets = self._extract_float(line, r'dMarkets:\s*([-\d.]+)')
            price = self._extract_float(line, r'Ask:([\d.]+)')
            
            dt = datetime.combine(self.today, datetime.strptime(time_str, "%H:%M:%S").time())
            
            return SignalFeatures(
                signal_id=f"sig_{int(dt.timestamp()*1000)}_{symbol}",
                timestamp=dt.isoformat(),
                symbol=symbol,
                strategy="PumpDetection",
                direction="Long",
                price=price,
                delta_pct=delta_pct,
                buys_per_sec=buys_sec,
                vol_per_sec=vol_sec,
                vol_raise_pct=0,
                buyers_count=0,
                daily_volume_m=daily_vol,
                dBTC=dBTC,
                dBTC5m=dBTC5m,
                dBTC1m=dBTC1m,
                dMarkets=dMarkets,
                hour=dt.hour,
                minute=dt.minute,
                day_of_week=dt.weekday(),
                symbol_group=self._get_symbol_group(symbol),
            )
        
        # Try TopMarket
        match = self.TOPMARKET_PATTERN.search(line)
        if match:
            groups = match.groups()
            time_str = groups[0]
            symbol = f"{groups[1]}USDT"
            price = float(groups[2])
            dBTC = float(groups[3])
            dBTC5m = float(groups[4])
            dBTC1m = float(groups[5])
            dMarkets = float(groups[6])
            dMarkets24 = float(groups[7])
            delta_pct = float(groups[8])
            direction = groups[9]
            
            dt = datetime.combine(self.today, datetime.strptime(time_str, "%H:%M:%S").time())
            
            return SignalFeatures(
                signal_id=f"sig_{int(dt.timestamp()*1000)}_{symbol}",
                timestamp=dt.isoformat(),
                symbol=symbol,
                strategy="TopMarket",
                direction=direction,
                price=price,
                delta_pct=delta_pct,
                buys_per_sec=0,
                vol_per_sec=0,
                vol_raise_pct=0,
                buyers_count=0,
                daily_volume_m=0,
                dBTC=dBTC,
                dBTC5m=dBTC5m,
                dBTC1m=dBTC1m,
                dMarkets=dMarkets,
                dMarkets24=dMarkets24,
                hour=dt.hour,
                minute=dt.minute,
                day_of_week=dt.weekday(),
                symbol_group=self._get_symbol_group(symbol),
            )
        
        # Try DropsDetection
        match = self.DROP_PATTERN.search(line)
        if match:
            groups = match.groups()
            time_str = groups[0]
            symbol = f"{groups[1]}USDT"
            price = float(groups[2])
            dBTC = float(groups[3])
            dBTC5m = float(groups[4])
            dBTC1m = float(groups[5])
            dMarkets = float(groups[6])
            dMarkets24 = float(groups[7])
            delta_pct = float(groups[8])
            
            dt = datetime.combine(self.today, datetime.strptime(time_str, "%H:%M:%S").time())
            
            return SignalFeatures(
                signal_id=f"sig_{int(dt.timestamp()*1000)}_{symbol}",
                timestamp=dt.isoformat(),
                symbol=symbol,
                strategy="DropsDetection",
                direction="Long",
                price=price,
                delta_pct=delta_pct,
                buys_per_sec=0,
                vol_per_sec=0,
                vol_raise_pct=0,
                buyers_count=0,
                daily_volume_m=0,
                dBTC=dBTC,
                dBTC5m=dBTC5m,
                dBTC1m=dBTC1m,
                dMarkets=dMarkets,
                dMarkets24=dMarkets24,
                hour=dt.hour,
                minute=dt.minute,
                day_of_week=dt.weekday(),
                symbol_group=self._get_symbol_group(symbol),
            )
        
        return None
    
    def _extract_float(self, text: str, pattern: str) -> float:
        """Extract float from text using regex"""
        match = re.search(pattern, text)
        return float(match.group(1)) if match else 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# DATA COLLECTOR
# ═══════════════════════════════════════════════════════════════════════════════

class DataCollector:
    """Collects and stores training data"""
    
    def __init__(self, data_file: Path = DATASET_FILE):
        self.data_file = data_file
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        self.pending_signals: Dict[str, SignalFeatures] = {}
        self.sample_count = self._count_samples()
    
    def _count_samples(self) -> int:
        """Count existing samples"""
        if not self.data_file.exists():
            return 0
        
        count = 0
        with open(self.data_file, 'r') as f:
            for _ in f:
                count += 1
        return count
    
    def record_signal(self, features: SignalFeatures):
        """Record signal for later matching with outcome"""
        self.pending_signals[features.signal_id] = features
        logger.info(f"Recorded signal: {features.symbol} {features.strategy}")
    
    def record_outcome(self, signal_id: str, outcome: Outcome) -> Optional[TrainingSample]:
        """Record outcome and create training sample"""
        if signal_id not in self.pending_signals:
            # Try to match by symbol
            for sid, features in list(self.pending_signals.items()):
                if features.symbol in signal_id or signal_id in features.symbol:
                    signal_id = sid
                    break
            else:
                logger.warning(f"No matching signal for outcome: {signal_id}")
                return None
        
        features = self.pending_signals.pop(signal_id)
        
        sample = TrainingSample(
            features=features,
            outcome=outcome,
            collected_at=datetime.now(timezone.utc).isoformat(),
        )
        
        # Append to file
        with open(self.data_file, 'a') as f:
            f.write(json.dumps(sample.to_dict()) + '\n')
        
        self.sample_count += 1
        logger.info(f"Training sample #{self.sample_count}: {features.symbol} | MFE={outcome.mfe:.2f}% MAE={outcome.mae:.2f}%")
        
        return sample
    
    def load_all_samples(self) -> List[TrainingSample]:
        """Load all training samples"""
        if not self.data_file.exists():
            return []
        
        samples = []
        with open(self.data_file, 'r') as f:
            for line in f:
                try:
                    d = json.loads(line.strip())
                    samples.append(TrainingSample.from_dict(d))
                except Exception as e:
                    logger.warning(f"Failed to parse sample: {e}")
        
        return samples
    
    def get_stats(self) -> Dict:
        """Get collection statistics"""
        samples = self.load_all_samples()
        
        if not samples:
            return {"total_samples": 0}
        
        wins = sum(1 for s in samples if s.outcome.win)
        mfes = [s.outcome.mfe for s in samples]
        maes = [s.outcome.mae for s in samples]
        
        # By strategy
        by_strategy = defaultdict(list)
        for s in samples:
            by_strategy[s.features.strategy].append(s)
        
        strategy_stats = {}
        for strat, strat_samples in by_strategy.items():
            strat_wins = sum(1 for s in strat_samples if s.outcome.win)
            strategy_stats[strat] = {
                "count": len(strat_samples),
                "win_rate": strat_wins / len(strat_samples) if strat_samples else 0,
                "avg_mfe": statistics.mean([s.outcome.mfe for s in strat_samples]),
                "avg_mae": statistics.mean([s.outcome.mae for s in strat_samples]),
            }
        
        return {
            "total_samples": len(samples),
            "win_rate": wins / len(samples) if samples else 0,
            "avg_mfe": statistics.mean(mfes),
            "avg_mae": statistics.mean(maes),
            "by_strategy": strategy_stats,
            "pending_signals": len(self.pending_signals),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# ML MODEL
# ═══════════════════════════════════════════════════════════════════════════════

class SignalPredictor:
    """ML model for predicting signal outcomes"""
    
    def __init__(self, model_file: Path = MODEL_FILE):
        self.model_file = model_file
        self.model_file.parent.mkdir(parents=True, exist_ok=True)
        
        self.win_model = None
        self.mfe_model = None
        self.mae_model = None
        self.scaler = None
        self.label_encoders = {}
        
        self._load_model()
    
    def _load_model(self):
        """Load trained model"""
        if self.model_file.exists():
            try:
                with open(self.model_file, 'rb') as f:
                    data = pickle.load(f)
                self.win_model = data.get('win_model')
                self.mfe_model = data.get('mfe_model')
                self.mae_model = data.get('mae_model')
                self.scaler = data.get('scaler')
                self.label_encoders = data.get('label_encoders', {})
                logger.info("Loaded trained model")
            except Exception as e:
                logger.warning(f"Failed to load model: {e}")
    
    def _save_model(self):
        """Save trained model"""
        data = {
            'win_model': self.win_model,
            'mfe_model': self.mfe_model,
            'mae_model': self.mae_model,
            'scaler': self.scaler,
            'label_encoders': self.label_encoders,
            'trained_at': datetime.now(timezone.utc).isoformat(),
        }
        
        with open(self.model_file, 'wb') as f:
            pickle.dump(data, f)
        
        logger.info(f"Saved model to {self.model_file}")
    
    def _prepare_features(self, samples: List[TrainingSample], fit: bool = False) -> Tuple:
        """Prepare features for ML"""
        if not HAS_NUMPY or not HAS_SKLEARN:
            raise RuntimeError("numpy and sklearn required for ML")
        
        # Extract feature dicts
        feature_dicts = [s.features.to_feature_dict() for s in samples]
        
        # Numeric features
        X_numeric = np.array([
            [fd[f] for f in NUMERIC_FEATURES]
            for fd in feature_dicts
        ])
        
        # Categorical features (one-hot encode)
        X_categorical = []
        for feat in CATEGORICAL_FEATURES:
            values = [fd[feat] for fd in feature_dicts]
            
            if fit:
                le = LabelEncoder()
                encoded = le.fit_transform(values)
                self.label_encoders[feat] = le
            else:
                le = self.label_encoders.get(feat)
                if le is None:
                    continue
                # Handle unseen values
                encoded = []
                for v in values:
                    if v in le.classes_:
                        encoded.append(le.transform([v])[0])
                    else:
                        encoded.append(0)  # Unknown
                encoded = np.array(encoded)
            
            X_categorical.append(encoded.reshape(-1, 1))
        
        if X_categorical:
            X_cat = np.hstack(X_categorical)
            X = np.hstack([X_numeric, X_cat])
        else:
            X = X_numeric
        
        # Scale
        if fit:
            self.scaler = StandardScaler()
            X = self.scaler.fit_transform(X)
        elif self.scaler:
            X = self.scaler.transform(X)
        
        # Targets
        y_win = np.array([1 if s.outcome.win else 0 for s in samples])
        y_mfe = np.array([s.outcome.mfe for s in samples])
        y_mae = np.array([s.outcome.mae for s in samples])
        
        return X, y_win, y_mfe, y_mae
    
    def train(self, samples: List[TrainingSample]) -> Dict:
        """Train models on samples"""
        if not HAS_SKLEARN:
            logger.warning("sklearn not installed, using rule-based model")
            return self._train_rule_based(samples)
        
        if len(samples) < MIN_SAMPLES_FOR_TRAINING:
            logger.warning(f"Not enough samples ({len(samples)} < {MIN_SAMPLES_FOR_TRAINING})")
            return {"error": "insufficient_samples"}
        
        logger.info(f"Training on {len(samples)} samples...")
        
        # Prepare data
        X, y_win, y_mfe, y_mae = self._prepare_features(samples, fit=True)
        
        # Train win classifier
        self.win_model = RandomForestClassifier(
            n_estimators=100,
            max_depth=10,
            min_samples_leaf=5,
            random_state=42
        )
        self.win_model.fit(X, y_win)
        
        # Cross-validation score
        cv_scores = cross_val_score(self.win_model, X, y_win, cv=5)
        win_cv_score = cv_scores.mean()
        
        # Train MFE regressor
        self.mfe_model = GradientBoostingRegressor(
            n_estimators=100,
            max_depth=5,
            random_state=42
        )
        self.mfe_model.fit(X, y_mfe)
        
        # Train MAE regressor
        self.mae_model = GradientBoostingRegressor(
            n_estimators=100,
            max_depth=5,
            random_state=42
        )
        self.mae_model.fit(X, y_mae)
        
        # Save model
        self._save_model()
        
        # Feature importance
        feature_names = NUMERIC_FEATURES + CATEGORICAL_FEATURES
        importances = dict(zip(feature_names, self.win_model.feature_importances_[:len(feature_names)]))
        
        return {
            "status": "trained",
            "samples": len(samples),
            "win_cv_score": win_cv_score,
            "feature_importance": importances,
        }
    
    def _train_rule_based(self, samples: List[TrainingSample]) -> Dict:
        """Train simple rule-based model"""
        # Calculate stats by strategy
        by_strategy = defaultdict(list)
        for s in samples:
            by_strategy[s.features.strategy].append(s)
        
        self.strategy_stats = {}
        for strat, strat_samples in by_strategy.items():
            wins = sum(1 for s in strat_samples if s.outcome.win)
            self.strategy_stats[strat] = {
                "win_rate": wins / len(strat_samples) if strat_samples else 0,
                "avg_mfe": statistics.mean([s.outcome.mfe for s in strat_samples]),
                "avg_mae": statistics.mean([s.outcome.mae for s in strat_samples]),
                "count": len(strat_samples),
            }
        
        return {
            "status": "rule_based",
            "samples": len(samples),
            "strategy_stats": self.strategy_stats,
        }
    
    def predict(self, features: SignalFeatures) -> Dict:
        """Predict outcome for signal"""
        if self.win_model is None:
            # Use rule-based
            if hasattr(self, 'strategy_stats'):
                stats = self.strategy_stats.get(features.strategy, {})
                return {
                    "win_probability": stats.get("win_rate", 0.5),
                    "predicted_mfe": stats.get("avg_mfe", 1.0),
                    "predicted_mae": stats.get("avg_mae", -1.0),
                    "confidence": min(stats.get("count", 0) / 20, 1.0),
                    "model_type": "rule_based",
                }
            return {"win_probability": 0.5, "model_type": "no_model"}
        
        # Use ML model
        try:
            sample = TrainingSample(features=features, outcome=Outcome(
                signal_id="", mfe=0, mae=0, final_pnl=0, duration_sec=0, exit_reason=""
            ))
            X, _, _, _ = self._prepare_features([sample], fit=False)
            
            win_prob = self.win_model.predict_proba(X)[0][1]
            pred_mfe = self.mfe_model.predict(X)[0]
            pred_mae = self.mae_model.predict(X)[0]
            
            return {
                "win_probability": float(win_prob),
                "predicted_mfe": float(pred_mfe),
                "predicted_mae": float(pred_mae),
                "confidence": float(win_prob),
                "model_type": "ml",
            }
        except Exception as e:
            logger.warning(f"Prediction failed: {e}")
            return {"win_probability": 0.5, "model_type": "error", "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# THRESHOLD LEARNER
# ═══════════════════════════════════════════════════════════════════════════════

class ThresholdLearner:
    """Learns optimal thresholds from data"""
    
    def __init__(self, thresholds_file: Path = THRESHOLDS_FILE):
        self.thresholds_file = thresholds_file
        self.thresholds_file.parent.mkdir(parents=True, exist_ok=True)
        self.thresholds = self._load_thresholds()
    
    def _load_thresholds(self) -> Dict:
        """Load learned thresholds"""
        if self.thresholds_file.exists():
            with open(self.thresholds_file, 'r') as f:
                return json.load(f)
        
        # Default thresholds
        return {
            "min_buys_sec": 10,
            "scalp_buys_sec": 30,
            "pump_override_buys_sec": 100,
            "min_delta": 1.0,
            "scalp_delta": 2.0,
            "min_vol_raise": 50,
            "strong_vol_raise": 100,
            "min_confidence": 0.70,
        }
    
    def _save_thresholds(self):
        """Save thresholds"""
        with open(self.thresholds_file, 'w') as f:
            json.dump(self.thresholds, f, indent=2)
    
    def learn(self, samples: List[TrainingSample]) -> Dict:
        """Learn optimal thresholds from samples"""
        if len(samples) < 30:
            return {"status": "insufficient_samples", "samples": len(samples)}
        
        # Analyze winning vs losing trades
        winners = [s for s in samples if s.outcome.win]
        losers = [s for s in samples if not s.outcome.win]
        
        if not winners or not losers:
            return {"status": "no_variance", "winners": len(winners), "losers": len(losers)}
        
        # Find optimal thresholds that maximize win rate
        new_thresholds = {}
        
        # buys_per_sec threshold
        winner_buys = [s.features.buys_per_sec for s in winners if s.features.buys_per_sec > 0]
        loser_buys = [s.features.buys_per_sec for s in losers if s.features.buys_per_sec > 0]
        
        if winner_buys and loser_buys:
            # Find threshold that separates winners from losers
            optimal_buys = statistics.median(winner_buys)
            new_thresholds["scalp_buys_sec"] = max(15, min(50, optimal_buys * 0.8))
        
        # delta threshold
        winner_delta = [s.features.delta_pct for s in winners if s.features.delta_pct > 0]
        loser_delta = [s.features.delta_pct for s in losers if s.features.delta_pct > 0]
        
        if winner_delta and loser_delta:
            optimal_delta = statistics.median(winner_delta)
            new_thresholds["min_delta"] = max(1.0, min(3.0, optimal_delta * 0.8))
        
        # vol_raise threshold
        winner_vol = [s.features.vol_raise_pct for s in winners if s.features.vol_raise_pct > 0]
        loser_vol = [s.features.vol_raise_pct for s in losers if s.features.vol_raise_pct > 0]
        
        if winner_vol and loser_vol:
            optimal_vol = statistics.median(winner_vol)
            new_thresholds["min_vol_raise"] = max(30, min(100, optimal_vol * 0.7))
        
        # Update thresholds
        for k, v in new_thresholds.items():
            old_val = self.thresholds.get(k, v)
            # Smooth update (80% old, 20% new)
            self.thresholds[k] = old_val * 0.8 + v * 0.2
        
        self.thresholds["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.thresholds["samples_used"] = len(samples)
        
        self._save_thresholds()
        
        return {
            "status": "updated",
            "new_thresholds": new_thresholds,
            "thresholds": self.thresholds,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# LEARNING ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

class LearningOrchestrator:
    """Orchestrates the entire learning pipeline"""
    
    def __init__(self):
        self.parser = EnhancedMoonBotParser()
        self.collector = DataCollector()
        self.predictor = SignalPredictor()
        self.threshold_learner = ThresholdLearner()
        self.last_retrain_count = self.collector.sample_count
    
    def process_moonbot_line(self, line: str) -> Optional[SignalFeatures]:
        """Process MoonBot line and record signal"""
        features = self.parser.parse(line)
        if features:
            self.collector.record_signal(features)
            
            # Get prediction
            prediction = self.predictor.predict(features)
            logger.info(f"Prediction for {features.symbol}: win_prob={prediction['win_probability']:.0%}")
            
            return features
        return None
    
    def record_outcome(self, signal_id: str, mfe: float, mae: float, 
                       duration_sec: int = 300, exit_reason: str = "timeout"):
        """Record outcome and possibly retrain"""
        outcome = Outcome(
            signal_id=signal_id,
            mfe=mfe,
            mae=mae,
            final_pnl=mfe if mfe > abs(mae) else mae,
            duration_sec=duration_sec,
            exit_reason=exit_reason,
        )
        
        sample = self.collector.record_outcome(signal_id, outcome)
        
        if sample:
            # Check if we should retrain
            current_count = self.collector.sample_count
            if current_count >= MIN_SAMPLES_FOR_TRAINING:
                if current_count - self.last_retrain_count >= RETRAIN_EVERY_N_SAMPLES:
                    self.retrain()
    
    def retrain(self):
        """Retrain models on all data"""
        samples = self.collector.load_all_samples()
        
        # Train ML model
        train_result = self.predictor.train(samples)
        logger.info(f"Model training result: {train_result}")
        
        # Learn thresholds
        threshold_result = self.threshold_learner.learn(samples)
        logger.info(f"Threshold learning result: {threshold_result}")
        
        self.last_retrain_count = len(samples)
        
        return {
            "training": train_result,
            "thresholds": threshold_result,
        }
    
    def get_enhanced_prediction(self, features: SignalFeatures) -> Dict:
        """Get prediction with learned thresholds"""
        prediction = self.predictor.predict(features)
        thresholds = self.threshold_learner.thresholds
        
        # Apply learned thresholds
        should_trade = True
        reasons = []
        
        if features.buys_per_sec > 0 and features.buys_per_sec < thresholds.get("min_buys_sec", 10):
            should_trade = False
            reasons.append(f"buys_sec {features.buys_per_sec:.1f} < learned threshold {thresholds['min_buys_sec']:.1f}")
        
        if features.delta_pct > 0 and features.delta_pct < thresholds.get("min_delta", 1.0):
            should_trade = False
            reasons.append(f"delta {features.delta_pct:.1f}% < learned threshold {thresholds['min_delta']:.1f}%")
        
        if prediction["win_probability"] < thresholds.get("min_confidence", 0.7):
            should_trade = False
            reasons.append(f"win_prob {prediction['win_probability']:.0%} < threshold {thresholds['min_confidence']:.0%}")
        
        return {
            **prediction,
            "should_trade": should_trade,
            "skip_reasons": reasons,
            "learned_thresholds": thresholds,
        }
    
    def get_stats(self) -> Dict:
        """Get learning statistics"""
        return {
            "collection": self.collector.get_stats(),
            "thresholds": self.threshold_learner.thresholds,
            "model_file": str(self.predictor.model_file),
            "has_ml_model": self.predictor.win_model is not None,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="HOPE AI Live Learning System")
    parser.add_argument("--process", type=str, help="Process MoonBot line")
    parser.add_argument("--outcome", type=str, help="Record outcome (signal_id:mfe:mae)")
    parser.add_argument("--retrain", action="store_true", help="Force retrain")
    parser.add_argument("--stats", action="store_true", help="Show statistics")
    parser.add_argument("--predict", type=str, help="Predict for MoonBot line")
    
    args = parser.parse_args()
    
    orchestrator = LearningOrchestrator()
    
    if args.process:
        features = orchestrator.process_moonbot_line(args.process)
        if features:
            pred = orchestrator.get_enhanced_prediction(features)
            print(json.dumps({"features": asdict(features), "prediction": pred}, indent=2))
        else:
            print("Failed to parse signal")
    
    elif args.outcome:
        parts = args.outcome.split(":")
        if len(parts) >= 3:
            signal_id, mfe, mae = parts[0], float(parts[1]), float(parts[2])
            orchestrator.record_outcome(signal_id, mfe, mae)
            print(f"Recorded outcome for {signal_id}")
        else:
            print("Format: signal_id:mfe:mae")
    
    elif args.retrain:
        result = orchestrator.retrain()
        print(json.dumps(result, indent=2))
    
    elif args.stats:
        stats = orchestrator.get_stats()
        print(json.dumps(stats, indent=2))
    
    elif args.predict:
        features = orchestrator.parser.parse(args.predict)
        if features:
            pred = orchestrator.get_enhanced_prediction(features)
            print(json.dumps(pred, indent=2))
        else:
            print("Failed to parse signal")
    
    else:
        parser.print_help()
        
        print("\n" + "=" * 60)
        print("  LIVE LEARNING DEMO")
        print("=" * 60)
        
        demo_signal = """17:08:22   Signal USDT-ENJ Ask:0.030300  dBTC: -1.33 dBTC5m: 0.67 dBTC1m: 0.25 24hBTC: -2.53 72hBTC: -2.81 dMarkets: -2.07 dMarkets24: -4.58  AutoStart: FALSE (manual mode)  AutoBuy is off by strategy settings <Delta_1_SIGNAL> Autodetect ON;  Autodetected  [ Delta: USDT-ENJ  DailyVol: 3.6m  HourlyVol: 994 k  Delta: 2.5%  LastDelta: 0.5%  Vol: 120.6 k BTC  VolRaise: 242.5%  Buyers: 100   Vol/Sec: 3.54 k USDT] (strategy <Delta_1_SIGNAL>)"""
        
        features = orchestrator.parser.parse(demo_signal)
        if features:
            print(f"\nParsed Features:")
            print(f"  Symbol: {features.symbol}")
            print(f"  Strategy: {features.strategy}")
            print(f"  Delta: {features.delta_pct}%")
            print(f"  Vol Raise: {features.vol_raise_pct}%")
            print(f"  dBTC: {features.dBTC}")
            print(f"  dMarkets: {features.dMarkets}")
            print(f"  Hour: {features.hour}")
            
            pred = orchestrator.get_enhanced_prediction(features)
            print(f"\nPrediction:")
            print(f"  Win Probability: {pred.get('win_probability', 0):.0%}")
            print(f"  Should Trade: {pred.get('should_trade', False)}")
            if pred.get('skip_reasons'):
                print(f"  Skip Reasons: {pred['skip_reasons']}")


if __name__ == "__main__":
    main()
