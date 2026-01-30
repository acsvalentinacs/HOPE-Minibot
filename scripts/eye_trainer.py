# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-01-30 12:45:00 UTC
# Purpose: Eye of God Training Module - Learn from market patterns
# Version: 1.0
# === END SIGNATURE ===
"""
Eye of God Training Module v1.0

═══════════════════════════════════════════════════════════════════════════════
ИСТОЧНИКИ ОБУЧЕНИЯ:
═══════════════════════════════════════════════════════════════════════════════
1. TradingView Ideas API (через RSS/веб)
2. Собственные сделки (outcomes)
3. Binance historical data
4. Pattern recognition

═══════════════════════════════════════════════════════════════════════════════
ЧТО УЧИМ:
═══════════════════════════════════════════════════════════════════════════════
- Какие сигналы приводят к прибыли
- Какие паттерны (RSI, MACD, Volume) предсказывают памп
- Оптимальные пороги для разных монет
- Time-of-day patterns
- BTC correlation patterns

═══════════════════════════════════════════════════════════════════════════════
ИСПОЛЬЗОВАНИЕ:
═══════════════════════════════════════════════════════════════════════════════
# Собрать данные для обучения
python scripts/eye_trainer.py --collect

# Обучить модель
python scripts/eye_trainer.py --train

# Показать статистику
python scripts/eye_trainer.py --stats
"""

import asyncio
import json
import logging
import hashlib
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import random

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger("EYE_TRAINER")

# Directories
STATE_DIR = Path("state/ai/training")
STATE_DIR.mkdir(parents=True, exist_ok=True)

MODEL_DIR = Path("state/ai/models")
MODEL_DIR.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class TradeOutcome:
    """Результат сделки для обучения."""
    symbol: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    is_win: bool
    mode: str  # super_scalp, scalp, swing
    
    # Features at entry
    delta_pct: float
    buys_per_sec: float
    vol_raise_pct: float
    volume_24h: float
    btc_change: float
    hour_utc: int
    day_of_week: int
    
    # Exit reason
    exit_reason: str  # target, stop_loss, timeout, manual


@dataclass  
class PatternSignal:
    """Паттерн/сигнал для обучения."""
    symbol: str
    timestamp: str
    pattern_type: str  # pump_start, dump_start, consolidation_break, etc
    
    # Metrics before pattern
    delta_pct: float
    volume_spike_pct: float
    buys_per_sec: float
    
    # Outcome (if known)
    outcome_pct: Optional[float] = None  # Change in next 5 min
    outcome_time: Optional[str] = None


@dataclass
class TradingViewIdea:
    """Идея с TradingView."""
    symbol: str
    direction: str  # long, short
    timestamp: str
    author: str
    likes: int
    
    # Extracted signals
    patterns: List[str]  # "RSI oversold", "MACD cross", etc
    targets: List[float]  # Price targets
    stop_loss: Optional[float] = None


# ══════════════════════════════════════════════════════════════════════════════
# PATTERN DEFINITIONS - Что ищем в данных
# ══════════════════════════════════════════════════════════════════════════════

PATTERN_RULES = {
    "volume_explosion": {
        "description": "Резкий рост объёма > 200%",
        "condition": lambda d: d.get("vol_raise_pct", 0) > 200,
        "weight": 0.8,
        "expected_outcome": "pump",
    },
    "buy_pressure": {
        "description": "Высокое давление покупок > 50 buys/sec",
        "condition": lambda d: d.get("buys_per_sec", 0) > 50,
        "weight": 0.7,
        "expected_outcome": "pump",
    },
    "momentum_surge": {
        "description": "Быстрый рост цены > 5% за минуту",
        "condition": lambda d: d.get("delta_pct", 0) > 5,
        "weight": 0.9,
        "expected_outcome": "pump",
    },
    "btc_correlation_break": {
        "description": "Альт растёт при падении BTC",
        "condition": lambda d: d.get("delta_pct", 0) > 2 and d.get("btc_change", 0) < -1,
        "weight": 0.85,
        "expected_outcome": "strong_pump",
    },
    "dead_cat_bounce": {
        "description": "Отскок после сильного падения",
        "condition": lambda d: d.get("delta_pct", 0) > 3 and d.get("prev_change_1h", 0) < -10,
        "weight": 0.4,
        "expected_outcome": "uncertain",
    },
    "asia_session_pump": {
        "description": "Памп в азиатскую сессию (00-08 UTC)",
        "condition": lambda d: d.get("hour_utc", 12) < 8 and d.get("delta_pct", 0) > 3,
        "weight": 0.6,
        "expected_outcome": "pump",
    },
    "us_session_momentum": {
        "description": "Momentum во время US сессии (14-22 UTC)",
        "condition": lambda d: 14 <= d.get("hour_utc", 0) <= 22 and d.get("delta_pct", 0) > 2,
        "weight": 0.75,
        "expected_outcome": "pump",
    },
}


# ══════════════════════════════════════════════════════════════════════════════
# DATA COLLECTOR
# ══════════════════════════════════════════════════════════════════════════════

class DataCollector:
    """Сборщик данных для обучения."""
    
    def __init__(self):
        self.outcomes_file = STATE_DIR / "trade_outcomes.jsonl"
        self.patterns_file = STATE_DIR / "detected_patterns.jsonl"
        
    def record_outcome(self, outcome: TradeOutcome):
        """Записать результат сделки."""
        with open(self.outcomes_file, 'a') as f:
            f.write(json.dumps(asdict(outcome)) + "\n")
        log.info(f"Recorded outcome: {outcome.symbol} {'WIN' if outcome.is_win else 'LOSS'} {outcome.pnl_pct:+.2f}%")
        
    def record_pattern(self, pattern: PatternSignal):
        """Записать обнаруженный паттерн."""
        with open(self.patterns_file, 'a') as f:
            f.write(json.dumps(asdict(pattern)) + "\n")
            
    def get_outcomes(self, limit: int = 1000) -> List[TradeOutcome]:
        """Загрузить outcomes."""
        outcomes = []
        if self.outcomes_file.exists():
            with open(self.outcomes_file, 'r') as f:
                for line in f:
                    try:
                        data = json.loads(line.strip())
                        outcomes.append(TradeOutcome(**data))
                    except:
                        continue
        return outcomes[-limit:]
        
    def get_patterns(self, limit: int = 1000) -> List[PatternSignal]:
        """Загрузить patterns."""
        patterns = []
        if self.patterns_file.exists():
            with open(self.patterns_file, 'r') as f:
                for line in f:
                    try:
                        data = json.loads(line.strip())
                        patterns.append(PatternSignal(**data))
                    except:
                        continue
        return patterns[-limit:]
        
    def get_stats(self) -> Dict[str, Any]:
        """Получить статистику."""
        outcomes = self.get_outcomes()
        
        if not outcomes:
            return {"total": 0, "win_rate": 0, "message": "No data yet"}
            
        wins = sum(1 for o in outcomes if o.is_win)
        total_pnl = sum(o.pnl_pct for o in outcomes)
        
        # By mode
        by_mode = {}
        for o in outcomes:
            if o.mode not in by_mode:
                by_mode[o.mode] = {"wins": 0, "total": 0, "pnl": 0}
            by_mode[o.mode]["total"] += 1
            by_mode[o.mode]["pnl"] += o.pnl_pct
            if o.is_win:
                by_mode[o.mode]["wins"] += 1
                
        # By hour
        by_hour = {}
        for o in outcomes:
            h = o.hour_utc
            if h not in by_hour:
                by_hour[h] = {"wins": 0, "total": 0}
            by_hour[h]["total"] += 1
            if o.is_win:
                by_hour[h]["wins"] += 1
                
        return {
            "total": len(outcomes),
            "wins": wins,
            "win_rate": wins / len(outcomes) if outcomes else 0,
            "total_pnl": total_pnl,
            "avg_pnl": total_pnl / len(outcomes) if outcomes else 0,
            "by_mode": by_mode,
            "best_hours": sorted(
                by_hour.items(),
                key=lambda x: x[1]["wins"] / x[1]["total"] if x[1]["total"] > 0 else 0,
                reverse=True
            )[:5],
        }


# ══════════════════════════════════════════════════════════════════════════════
# PATTERN DETECTOR
# ══════════════════════════════════════════════════════════════════════════════

class PatternDetector:
    """Детектор паттернов в real-time данных."""
    
    def __init__(self):
        self.collector = DataCollector()
        
    def detect(self, signal_data: Dict[str, Any]) -> List[str]:
        """
        Обнаружить паттерны в сигнале.
        
        Returns:
            List of detected pattern names
        """
        detected = []
        
        for pattern_name, rule in PATTERN_RULES.items():
            try:
                if rule["condition"](signal_data):
                    detected.append(pattern_name)
                    
                    # Record pattern
                    self.collector.record_pattern(PatternSignal(
                        symbol=signal_data.get("symbol", "UNKNOWN"),
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        pattern_type=pattern_name,
                        delta_pct=signal_data.get("delta_pct", 0),
                        volume_spike_pct=signal_data.get("vol_raise_pct", 0),
                        buys_per_sec=signal_data.get("buys_per_sec", 0),
                    ))
            except Exception as e:
                log.debug(f"Pattern check failed for {pattern_name}: {e}")
                
        return detected
        
    def get_pattern_score(self, patterns: List[str]) -> float:
        """Получить общий скор от паттернов."""
        if not patterns:
            return 0.0
            
        total_weight = sum(PATTERN_RULES[p]["weight"] for p in patterns if p in PATTERN_RULES)
        return min(1.0, total_weight / len(patterns))


# ══════════════════════════════════════════════════════════════════════════════
# ADAPTIVE THRESHOLDS LEARNER
# ══════════════════════════════════════════════════════════════════════════════

class ThresholdLearner:
    """
    Обучение оптимальных порогов на основе outcomes.
    
    Анализирует исторические сделки и находит оптимальные пороги для:
    - min_delta_pct
    - min_buys_per_sec
    - min_volume
    - confidence thresholds
    """
    
    def __init__(self):
        self.collector = DataCollector()
        self.thresholds_file = MODEL_DIR / "learned_thresholds.json"
        
        self.current_thresholds = {
            "min_delta_pct": 3.0,
            "min_buys_per_sec": 15.0,
            "min_volume_usd": 5_000_000,
            "min_confidence": 0.65,
        }
        
        self._load()
        
    def _load(self):
        """Загрузить текущие пороги."""
        if self.thresholds_file.exists():
            try:
                with open(self.thresholds_file, 'r') as f:
                    data = json.load(f)
                self.current_thresholds.update(data.get("thresholds", {}))
            except:
                pass
                
    def _save(self):
        """Сохранить пороги."""
        with open(self.thresholds_file, 'w') as f:
            json.dump({
                "thresholds": self.current_thresholds,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }, f, indent=2)
            
    def learn(self) -> Dict[str, Any]:
        """
        Обучить пороги на основе исторических данных.
        
        Алгоритм:
        1. Группировать сделки по диапазонам delta/buys/volume
        2. Найти диапазоны с лучшим win rate
        3. Установить пороги на границах лучших диапазонов
        """
        outcomes = self.collector.get_outcomes()
        
        if len(outcomes) < 50:
            log.warning(f"Not enough data for learning: {len(outcomes)} < 50")
            return {"status": "insufficient_data", "count": len(outcomes)}
            
        log.info(f"Learning from {len(outcomes)} trades...")
        
        # Analyze delta thresholds
        delta_analysis = self._analyze_threshold(
            outcomes,
            key="delta_pct",
            ranges=[(0, 2), (2, 4), (4, 6), (6, 10), (10, 20)]
        )
        
        # Analyze buys_per_sec thresholds
        buys_analysis = self._analyze_threshold(
            outcomes,
            key="buys_per_sec",
            ranges=[(0, 10), (10, 25), (25, 50), (50, 100), (100, 500)]
        )
        
        # Find best thresholds
        best_delta = self._find_best_threshold(delta_analysis)
        best_buys = self._find_best_threshold(buys_analysis)
        
        # Update thresholds
        if best_delta:
            self.current_thresholds["min_delta_pct"] = best_delta
        if best_buys:
            self.current_thresholds["min_buys_per_sec"] = best_buys
            
        # Adjust confidence based on overall win rate
        stats = self.collector.get_stats()
        if stats["win_rate"] < 0.4:
            self.current_thresholds["min_confidence"] = min(0.85, self.current_thresholds["min_confidence"] + 0.05)
        elif stats["win_rate"] > 0.6:
            self.current_thresholds["min_confidence"] = max(0.55, self.current_thresholds["min_confidence"] - 0.03)
            
        self._save()
        
        return {
            "status": "learned",
            "trades_analyzed": len(outcomes),
            "win_rate": stats["win_rate"],
            "new_thresholds": self.current_thresholds,
            "delta_analysis": delta_analysis,
            "buys_analysis": buys_analysis,
        }
        
    def _analyze_threshold(self, outcomes: List[TradeOutcome], key: str, ranges: List[Tuple]) -> Dict:
        """Анализ win rate по диапазонам значений."""
        analysis = {}
        
        for low, high in ranges:
            bucket = [o for o in outcomes if low <= getattr(o, key, 0) < high]
            if bucket:
                wins = sum(1 for o in bucket if o.is_win)
                analysis[f"{low}-{high}"] = {
                    "count": len(bucket),
                    "wins": wins,
                    "win_rate": wins / len(bucket),
                    "avg_pnl": sum(o.pnl_pct for o in bucket) / len(bucket),
                }
                
        return analysis
        
    def _find_best_threshold(self, analysis: Dict) -> Optional[float]:
        """Найти лучший порог (начало диапазона с лучшим win rate)."""
        if not analysis:
            return None
            
        # Find range with best win rate (min 10 trades)
        best_range = None
        best_wr = 0
        
        for range_str, data in analysis.items():
            if data["count"] >= 10 and data["win_rate"] > best_wr:
                best_wr = data["win_rate"]
                best_range = range_str
                
        if best_range:
            # Return lower bound of best range
            return float(best_range.split("-")[0])
            
        return None
        
    def get_thresholds(self) -> Dict[str, float]:
        """Получить текущие пороги."""
        return self.current_thresholds.copy()


# ══════════════════════════════════════════════════════════════════════════════
# EYE OF GOD TRAINER
# ══════════════════════════════════════════════════════════════════════════════

class EyeOfGodTrainer:
    """
    Главный класс для обучения Eye of God.
    
    Объединяет:
    - Сбор данных (outcomes, patterns)
    - Обучение порогов
    - Pattern recognition
    - ML model training (future)
    """
    
    def __init__(self):
        self.collector = DataCollector()
        self.pattern_detector = PatternDetector()
        self.threshold_learner = ThresholdLearner()
        
    def record_trade(self, trade_data: Dict[str, Any]):
        """
        Записать результат сделки.
        
        Args:
            trade_data: {
                "symbol": "BTCUSDT",
                "entry_time": "2026-01-30T12:00:00Z",
                "exit_time": "2026-01-30T12:05:00Z",
                "entry_price": 82000,
                "exit_price": 82500,
                "pnl_pct": 0.6,
                "is_win": True,
                "mode": "scalp",
                "delta_pct": 3.5,
                "buys_per_sec": 25,
                "vol_raise_pct": 80,
                "volume_24h": 50000000,
                "btc_change": -0.5,
                "hour_utc": 14,
                "day_of_week": 4,
                "exit_reason": "target"
            }
        """
        outcome = TradeOutcome(**trade_data)
        self.collector.record_outcome(outcome)
        
    def analyze_signal(self, signal_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Анализировать сигнал через призму обученных данных.
        
        Returns:
            {
                "patterns": ["volume_explosion", "buy_pressure"],
                "pattern_score": 0.75,
                "recommendation": "BUY",
                "confidence_boost": 0.1,
                "similar_trades": {
                    "count": 15,
                    "win_rate": 0.73
                }
            }
        """
        # Detect patterns
        patterns = self.pattern_detector.detect(signal_data)
        pattern_score = self.pattern_detector.get_pattern_score(patterns)
        
        # Find similar historical trades
        outcomes = self.collector.get_outcomes()
        similar = self._find_similar(signal_data, outcomes)
        
        # Get thresholds
        thresholds = self.threshold_learner.get_thresholds()
        
        # Check if signal passes learned thresholds
        passes_thresholds = (
            signal_data.get("delta_pct", 0) >= thresholds["min_delta_pct"] and
            signal_data.get("buys_per_sec", 0) >= thresholds["min_buys_per_sec"]
        )
        
        # Calculate confidence boost
        confidence_boost = 0.0
        if patterns:
            confidence_boost += pattern_score * 0.1
        if similar["win_rate"] > 0.6:
            confidence_boost += 0.05
            
        return {
            "patterns": patterns,
            "pattern_score": pattern_score,
            "passes_thresholds": passes_thresholds,
            "thresholds": thresholds,
            "confidence_boost": confidence_boost,
            "similar_trades": similar,
            "recommendation": "BUY" if passes_thresholds and pattern_score > 0.5 else "SKIP",
        }
        
    def _find_similar(self, signal: Dict, outcomes: List[TradeOutcome], tolerance: float = 0.3) -> Dict:
        """Найти похожие исторические сделки."""
        delta = signal.get("delta_pct", 0)
        buys = signal.get("buys_per_sec", 0)
        
        similar = []
        for o in outcomes:
            delta_diff = abs(o.delta_pct - delta) / max(delta, 1)
            buys_diff = abs(o.buys_per_sec - buys) / max(buys, 1)
            
            if delta_diff < tolerance and buys_diff < tolerance:
                similar.append(o)
                
        if not similar:
            return {"count": 0, "win_rate": 0.5}
            
        wins = sum(1 for o in similar if o.is_win)
        return {
            "count": len(similar),
            "win_rate": wins / len(similar),
            "avg_pnl": sum(o.pnl_pct for o in similar) / len(similar),
        }
        
    def train(self) -> Dict[str, Any]:
        """
        Запустить полное обучение.
        
        Returns:
            Training results and new parameters
        """
        log.info("=" * 60)
        log.info("TRAINING EYE OF GOD")
        log.info("=" * 60)
        
        # Get stats
        stats = self.collector.get_stats()
        log.info(f"Total trades: {stats['total']}")
        log.info(f"Win rate: {stats['win_rate']*100:.1f}%")
        
        # Learn thresholds
        learn_result = self.threshold_learner.learn()
        
        # Generate report
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stats": stats,
            "learning_result": learn_result,
            "new_thresholds": self.threshold_learner.get_thresholds(),
        }
        
        # Save report
        report_file = MODEL_DIR / f"training_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2, default=str)
            
        log.info(f"Training report saved: {report_file}")
        
        return report
        
    def show_stats(self):
        """Показать статистику."""
        stats = self.collector.get_stats()

        print("\n" + "=" * 60)
        print("EYE OF GOD TRAINING STATS")
        print("=" * 60)
        print(f"Total trades: {stats.get('total', 0)}")

        if stats.get('total', 0) == 0:
            print("No training data yet. Start trading to collect data.")
            print("=" * 60)
            return

        print(f"Wins: {stats.get('wins', 0)} ({stats.get('win_rate', 0)*100:.1f}%)")
        print(f"Total PnL: {stats.get('total_pnl', 0):+.2f}%")
        print(f"Avg PnL per trade: {stats.get('avg_pnl', 0):+.2f}%")
        
        print("\nBy Mode:")
        for mode, data in stats.get("by_mode", {}).items():
            wr = data["wins"] / data["total"] if data["total"] > 0 else 0
            print(f"  {mode}: {data['total']} trades, {wr*100:.1f}% WR, {data['pnl']:+.2f}% PnL")
            
        print("\nBest Hours (UTC):")
        for hour, data in stats.get("best_hours", []):
            wr = data["wins"] / data["total"] if data["total"] > 0 else 0
            print(f"  {hour:02d}:00 - {wr*100:.1f}% WR ({data['total']} trades)")
            
        print("\nCurrent Thresholds:")
        thresholds = self.threshold_learner.get_thresholds()
        for k, v in thresholds.items():
            print(f"  {k}: {v}")
            
        print("=" * 60)


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Eye of God Training Module")
    parser.add_argument("--train", action="store_true", help="Run training")
    parser.add_argument("--stats", action="store_true", help="Show statistics")
    parser.add_argument("--analyze", type=str, help="Analyze signal (JSON)")
    
    args = parser.parse_args()
    
    trainer = EyeOfGodTrainer()
    
    if args.train:
        result = trainer.train()
        print(f"\nTraining complete: {result['learning_result']['status']}")
        
    elif args.stats:
        trainer.show_stats()
        
    elif args.analyze:
        try:
            signal = json.loads(args.analyze)
            result = trainer.analyze_signal(signal)
            print(json.dumps(result, indent=2))
        except json.JSONDecodeError:
            print("Invalid JSON")
            
    else:
        parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())
