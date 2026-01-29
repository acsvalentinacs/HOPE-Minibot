# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 17:30:00 UTC
# Purpose: HOPE AI MoonBot Data Collector - extracts signals + outcomes for ML
# sha256: data_collector_v1.0
# === END SIGNATURE ===
"""
HOPE AI - MoonBot Data Collector v1.0

СОБИРАЕТ ДАННЫЕ ДЛЯ ОБУЧЕНИЯ ИИ ИЗ ЛОГОВ MOONBOT

Что извлекаем:
1. SIGNALS (features) - все параметры сигнала
2. OUTCOMES - результат из AutoClose или цены

ФОРМАТ ДАННЫХ:
┌─────────────────────────────────────────────────────────────────────────────┐
│  SIGNAL (Input Features)                 │  OUTCOME (Target)                │
├─────────────────────────────────────────────────────────────────────────────┤
│  timestamp: 15:43:39                     │  exit_type: TIMEOUT              │
│  symbol: ENJUSDT                         │  duration_sec: 62                │
│  strategy: PumpDetection                 │  mfe: +1.2%  (calc from price)   │
│  price: 0.027820                         │  mae: -0.3%  (calc from price)   │
│  buys_sec: 68.79                         │  win: true/false                 │
│  delta: 1.9%                             │                                  │
│  dBTC: 0.01                              │                                  │
│  dBTC5m: 0.07                            │                                  │
│  dMarkets: 0.21                          │                                  │
│  vol_raise: 32.4%                        │                                  │
│  buyers: 100                             │                                  │
│  hour: 15                                │                                  │
└─────────────────────────────────────────────────────────────────────────────┘

Usage:
    # Parse MoonBot log file and extract training data
    python moonbot_data_collector.py --log moonbot.log --output training_data.jsonl
    
    # Real-time collection from live log
    python moonbot_data_collector.py --watch moonbot.log --output training_data.jsonl
    
    # Analyze collected data
    python moonbot_data_collector.py --analyze training_data.jsonl
"""

import json
import re
import sys
import time
import argparse
import logging
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from collections import defaultdict
import statistics

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SignalFeatures:
    """All features extracted from MoonBot signal"""
    # Identifiers
    timestamp: str
    symbol: str
    
    # Strategy info
    strategy: str  # PumpDetection, Delta, TopMarket, DropsDetection, Volumes
    direction: str = "Long"
    
    # Price
    price: float = 0.0
    
    # Core metrics (varies by strategy)
    buys_per_sec: float = 0.0
    vol_per_sec: float = 0.0
    delta_pct: float = 0.0
    last_delta_pct: float = 0.0
    vol_raise_pct: float = 0.0
    buyers_count: int = 0
    daily_volume_m: float = 0.0
    hourly_volume_k: float = 0.0
    ppl_per_sec: int = 0
    
    # BTC correlation (VERY IMPORTANT!)
    dBTC: float = 0.0
    dBTC5m: float = 0.0
    dBTC1m: float = 0.0
    dBTC24h: float = 0.0
    dBTC72h: float = 0.0
    
    # Market sentiment
    dMarkets: float = 0.0
    dMarkets24: float = 0.0
    
    # DropsDetection specific
    price_is_low: bool = False
    x_price_delta: float = 0.0
    
    # Time features
    hour: int = 0
    minute: int = 0
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class Outcome:
    """Trade outcome from AutoClose or price tracking"""
    symbol: str
    signal_timestamp: str
    exit_type: str  # TIMEOUT, TARGET, STOP, UNKNOWN
    duration_sec: int = 0
    entry_price: float = 0.0
    exit_price: float = 0.0
    mfe_pct: float = 0.0  # Maximum Favorable Excursion
    mae_pct: float = 0.0  # Maximum Adverse Excursion
    pnl_pct: float = 0.0
    win: bool = False
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class TrainingSample:
    """Complete training sample: features + outcome"""
    features: SignalFeatures
    outcome: Outcome
    collected_at: str = ""
    
    def to_dict(self) -> Dict:
        return {
            "features": self.features.to_dict(),
            "outcome": self.outcome.to_dict(),
            "collected_at": self.collected_at,
        }
    
    @classmethod
    def from_dict(cls, d: Dict) -> 'TrainingSample':
        features = SignalFeatures(**d['features'])
        outcome = Outcome(**d['outcome'])
        return cls(features=features, outcome=outcome, collected_at=d.get('collected_at', ''))


# ═══════════════════════════════════════════════════════════════════════════════
# MOONBOT PARSER (Enhanced)
# ═══════════════════════════════════════════════════════════════════════════════

class MoonBotParser:
    """Parses ALL MoonBot log formats"""
    
    # === SIGNAL PATTERNS ===
    
    # Common header: timestamp + symbol + price + BTC correlation
    HEADER_PATTERN = re.compile(
        r'(\d{2}:\d{2}:\d{2})\s+Signal USDT-(\w+)\s+Ask:([\d.]+)\s+'
        r'dBTC:\s*([-\d.]+)\s+dBTC5m:\s*([-\d.]+)\s+dBTC1m:\s*([-\d.]+)\s+'
        r'24hBTC:\s*([-\d.]+)\s+72hBTC:\s*([-\d.]+)\s+'
        r'dMarkets:\s*([-\d.]+)\s+dMarkets24:\s*([-\d.]+)'
    )
    
    # PumpDetection: DailyVol: 1.6m PPL/sec: 11 Buys/sec: 32.91 Vol/sec: 2.56 k PriceDelta: 2.0%
    PUMP_DETAILS = re.compile(
        r'PumpDetection.*DailyVol:\s*([\d.]+)m\s+PPL/sec:\s*(\d+)\s+'
        r'Buys/sec:\s*([\d.]+)\s+Vol/sec:\s*([\d.]+)\s*k\s+PriceDelta:\s*([\d.]+)%',
        re.IGNORECASE
    )
    
    # Delta: DailyVol: 3.6m HourlyVol: 994 k Delta: 2.5% LastDelta: 0.5% Vol: 120.6 k BTC VolRaise: 242.5% Buyers: 100 Vol/Sec: 3.54 k
    DELTA_DETAILS = re.compile(
        r'Delta:\s*USDT-\w+\s+DailyVol:\s*([\d.]+)m\s+HourlyVol:\s*([\d.]+)\s*k\s+'
        r'Delta:\s*([\d.]+)%\s+LastDelta:\s*([\d.]+)%.*'
        r'VolRaise:\s*([\d.]+)%\s+Buyers:\s*(\d+)\s+Vol/Sec:\s*([\d.]+)\s*k',
        re.IGNORECASE
    )
    
    # TopMarket: Delta: 6.97% Long/Short
    TOPMARKET_DETAILS = re.compile(
        r'TopMarket.*Delta:\s*([\d.]+)%\s+(Long|Short)',
        re.IGNORECASE
    )
    
    # DropsDetection: DailyVol: 1.6m PriceIsLow: false xPriceDelta: 2.6
    DROPS_DETAILS = re.compile(
        r'DropsDetection.*DailyVol:\s*([\d.]+)m\s+PriceIsLow:\s*(true|false)\s+xPriceDelta:\s*([\d.]+)',
        re.IGNORECASE
    )
    
    # Volumes: step2: DeltaAtMaxP: ... etc
    VOLUMES_DETAILS = re.compile(
        r'VolDetection.*USDT-(\w+).*step2.*BidToAsk:\s*([\d.]+)',
        re.IGNORECASE
    )
    
    # === OUTCOME PATTERNS ===
    
    # AutoClose: "16:09:51 Chart ENJ AutoCLose after 64 sec (> 60)"
    AUTOCLOSE_PATTERN = re.compile(
        r'(\d{2}:\d{2}:\d{2})\s+Chart\s+(\w+)\s+AutoCLose\s+after\s+(\d+)\s+sec',
        re.IGNORECASE
    )
    
    # Strategy tag patterns
    STRATEGY_PUMP = re.compile(r'Pumpdetect\d*_USDT|PumpDetection', re.IGNORECASE)
    STRATEGY_DELTA = re.compile(r'Delta_\d*_SIGNAL|<Delta', re.IGNORECASE)
    STRATEGY_TOPMARKET = re.compile(r'Top\s*Market', re.IGNORECASE)
    STRATEGY_DROPS = re.compile(r'Dropdetect\d*_USDT|DropsDetection', re.IGNORECASE)
    STRATEGY_VOLUMES = re.compile(r'VOLUMES|VolDetection', re.IGNORECASE)
    
    def __init__(self):
        self.today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    def parse_signal(self, line: str) -> Optional[SignalFeatures]:
        """Parse signal line into features"""
        line = line.strip()
        if 'Signal USDT-' not in line:
            return None
        
        # Parse header (common to all signals)
        header_match = self.HEADER_PATTERN.search(line)
        if not header_match:
            return None
        
        time_str, symbol, price, dBTC, dBTC5m, dBTC1m, dBTC24h, dBTC72h, dMarkets, dMarkets24 = header_match.groups()
        
        # Determine strategy
        strategy = self._detect_strategy(line)
        if not strategy:
            return None
        
        # Create base features
        features = SignalFeatures(
            timestamp=f"{self.today}T{time_str}Z",
            symbol=f"{symbol}USDT",
            strategy=strategy,
            price=float(price),
            dBTC=float(dBTC),
            dBTC5m=float(dBTC5m),
            dBTC1m=float(dBTC1m),
            dBTC24h=float(dBTC24h),
            dBTC72h=float(dBTC72h),
            dMarkets=float(dMarkets),
            dMarkets24=float(dMarkets24),
            hour=int(time_str.split(':')[0]),
            minute=int(time_str.split(':')[1]),
        )
        
        # Parse strategy-specific details
        self._parse_strategy_details(line, features)
        
        return features
    
    def _detect_strategy(self, line: str) -> Optional[str]:
        """Detect strategy from line"""
        if self.STRATEGY_PUMP.search(line):
            return "PumpDetection"
        elif self.STRATEGY_DELTA.search(line):
            return "Delta"
        elif self.STRATEGY_TOPMARKET.search(line):
            return "TopMarket"
        elif self.STRATEGY_DROPS.search(line):
            return "DropsDetection"
        elif self.STRATEGY_VOLUMES.search(line):
            return "Volumes"
        return None
    
    def _parse_strategy_details(self, line: str, features: SignalFeatures):
        """Parse strategy-specific details"""
        
        if features.strategy == "PumpDetection":
            match = self.PUMP_DETAILS.search(line)
            if match:
                daily_vol, ppl_sec, buys_sec, vol_sec, delta = match.groups()
                features.daily_volume_m = float(daily_vol)
                features.ppl_per_sec = int(ppl_sec)
                features.buys_per_sec = float(buys_sec)
                features.vol_per_sec = float(vol_sec) * 1000
                features.delta_pct = float(delta)
        
        elif features.strategy == "Delta":
            match = self.DELTA_DETAILS.search(line)
            if match:
                daily_vol, hourly_vol, delta, last_delta, vol_raise, buyers, vol_sec = match.groups()
                features.daily_volume_m = float(daily_vol)
                features.hourly_volume_k = float(hourly_vol)
                features.delta_pct = float(delta)
                features.last_delta_pct = float(last_delta)
                features.vol_raise_pct = float(vol_raise)
                features.buyers_count = int(buyers)
                features.vol_per_sec = float(vol_sec) * 1000
        
        elif features.strategy == "TopMarket":
            match = self.TOPMARKET_DETAILS.search(line)
            if match:
                delta, direction = match.groups()
                features.delta_pct = float(delta)
                features.direction = direction
        
        elif features.strategy == "DropsDetection":
            match = self.DROPS_DETAILS.search(line)
            if match:
                daily_vol, price_is_low, x_delta = match.groups()
                features.daily_volume_m = float(daily_vol)
                features.price_is_low = price_is_low.lower() == 'true'
                features.x_price_delta = float(x_delta)
        
        elif features.strategy == "Volumes":
            match = self.VOLUMES_DETAILS.search(line)
            if match:
                symbol, bid_to_ask = match.groups()
                features.delta_pct = float(bid_to_ask)  # Using BidToAsk as delta proxy
    
    def parse_autoclose(self, line: str) -> Optional[Tuple[str, str, int]]:
        """
        Parse AutoClose line
        
        Returns: (timestamp, symbol, duration_sec) or None
        """
        match = self.AUTOCLOSE_PATTERN.search(line)
        if match:
            time_str, symbol, duration = match.groups()
            return f"{self.today}T{time_str}Z", f"{symbol}USDT", int(duration)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# DATA COLLECTOR
# ═══════════════════════════════════════════════════════════════════════════════

class DataCollector:
    """Collects and matches signals with outcomes"""
    
    def __init__(self, output_file: str = "training_data.jsonl"):
        self.output_file = Path(output_file)
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        
        self.parser = MoonBotParser()
        
        # Pending signals waiting for outcome
        self.pending_signals: Dict[str, List[SignalFeatures]] = defaultdict(list)
        
        # Statistics
        self.stats = {
            "signals_parsed": 0,
            "outcomes_found": 0,
            "samples_created": 0,
            "by_strategy": defaultdict(int),
            "by_outcome": defaultdict(int),
        }
    
    def process_line(self, line: str) -> Optional[TrainingSample]:
        """Process a single log line"""
        line = line.strip()
        if not line:
            return None
        
        # Try to parse as signal
        features = self.parser.parse_signal(line)
        if features:
            self.pending_signals[features.symbol].append(features)
            self.stats["signals_parsed"] += 1
            self.stats["by_strategy"][features.strategy] += 1
            logger.debug(f"Signal: {features.symbol} {features.strategy}")
            return None
        
        # Try to parse as AutoClose (outcome)
        autoclose = self.parser.parse_autoclose(line)
        if autoclose:
            timestamp, symbol, duration = autoclose
            sample = self._match_outcome(symbol, timestamp, duration)
            if sample:
                self._save_sample(sample)
                return sample
        
        return None
    
    def _match_outcome(self, symbol: str, close_timestamp: str, duration: int) -> Optional[TrainingSample]:
        """Match AutoClose with pending signal"""
        if symbol not in self.pending_signals or not self.pending_signals[symbol]:
            logger.debug(f"No pending signal for {symbol}")
            return None
        
        # Get the most recent signal for this symbol
        # (AutoClose should match the signal that was opened ~duration seconds ago)
        signals = self.pending_signals[symbol]
        
        # Find best matching signal by timestamp
        best_signal = None
        best_diff = float('inf')
        
        for sig in signals:
            try:
                sig_time = datetime.fromisoformat(sig.timestamp.replace('Z', '+00:00'))
                close_time = datetime.fromisoformat(close_timestamp.replace('Z', '+00:00'))
                
                # Expected signal time = close_time - duration
                expected_sig_time = close_time - timedelta(seconds=duration)
                diff = abs((sig_time - expected_sig_time).total_seconds())
                
                if diff < best_diff and diff < 30:  # Allow 30 sec tolerance
                    best_diff = diff
                    best_signal = sig
            except:
                continue
        
        if not best_signal:
            # Fall back to most recent signal
            best_signal = signals[-1]
        
        # Remove matched signal
        self.pending_signals[symbol] = [s for s in signals if s != best_signal]
        
        # Create outcome
        # TIMEOUT = closed by timer (didn't hit target or stop)
        outcome = Outcome(
            symbol=symbol,
            signal_timestamp=best_signal.timestamp,
            exit_type="TIMEOUT",
            duration_sec=duration,
            entry_price=best_signal.price,
            # We don't have exact MFE/MAE from logs, will calculate from price feed later
            mfe_pct=0.0,
            mae_pct=0.0,
            pnl_pct=0.0,
            win=False,  # TIMEOUT usually means loss
        )
        
        self.stats["outcomes_found"] += 1
        self.stats["by_outcome"]["TIMEOUT"] += 1
        
        return TrainingSample(
            features=best_signal,
            outcome=outcome,
            collected_at=datetime.now(timezone.utc).isoformat(),
        )
    
    def _save_sample(self, sample: TrainingSample):
        """Save sample to JSONL file"""
        with open(self.output_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(sample.to_dict(), ensure_ascii=False) + '\n')
        
        self.stats["samples_created"] += 1
        logger.info(f"Sample #{self.stats['samples_created']}: {sample.features.symbol} {sample.features.strategy} → {sample.outcome.exit_type}")
    
    def process_file(self, log_file: str):
        """Process entire log file"""
        log_path = Path(log_file)
        if not log_path.exists():
            logger.error(f"File not found: {log_file}")
            return
        
        logger.info(f"Processing: {log_file}")
        
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                self.process_line(line)
        
        logger.info(f"Completed. Samples created: {self.stats['samples_created']}")
    
    def watch_file(self, log_file: str):
        """Watch log file for new lines (real-time collection)"""
        log_path = Path(log_file)
        
        if not log_path.exists():
            logger.error(f"File not found: {log_file}")
            return
        
        logger.info(f"Watching: {log_file}")
        
        # Start from end of file
        last_position = log_path.stat().st_size
        
        while True:
            try:
                current_size = log_path.stat().st_size
                
                if current_size > last_position:
                    with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                        f.seek(last_position)
                        for line in f:
                            self.process_line(line)
                        last_position = f.tell()
                
                elif current_size < last_position:
                    # File was truncated/rotated
                    last_position = 0
                
                time.sleep(0.5)
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Watch error: {e}")
                time.sleep(1)
        
        logger.info("Stopped watching")
    
    def get_stats(self) -> Dict:
        """Get collection statistics"""
        return {
            "signals_parsed": self.stats["signals_parsed"],
            "outcomes_found": self.stats["outcomes_found"],
            "samples_created": self.stats["samples_created"],
            "by_strategy": dict(self.stats["by_strategy"]),
            "by_outcome": dict(self.stats["by_outcome"]),
            "pending_signals": sum(len(v) for v in self.pending_signals.values()),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# DATA ANALYZER
# ═══════════════════════════════════════════════════════════════════════════════

class DataAnalyzer:
    """Analyzes collected training data"""
    
    def __init__(self, data_file: str):
        self.data_file = Path(data_file)
        self.samples: List[TrainingSample] = []
        self._load()
    
    def _load(self):
        """Load samples from file"""
        if not self.data_file.exists():
            logger.warning(f"Data file not found: {self.data_file}")
            return
        
        with open(self.data_file, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    d = json.loads(line.strip())
                    self.samples.append(TrainingSample.from_dict(d))
                except:
                    continue
        
        logger.info(f"Loaded {len(self.samples)} samples")
    
    def analyze(self) -> Dict:
        """Comprehensive analysis of training data"""
        if not self.samples:
            return {"error": "No samples loaded"}
        
        # Overall stats
        total = len(self.samples)
        wins = sum(1 for s in self.samples if s.outcome.win)
        
        # By strategy
        by_strategy = defaultdict(list)
        for s in self.samples:
            by_strategy[s.features.strategy].append(s)
        
        strategy_stats = {}
        for strat, samples in by_strategy.items():
            strat_wins = sum(1 for s in samples if s.outcome.win)
            durations = [s.outcome.duration_sec for s in samples]
            
            # Feature averages for winners vs losers
            winner_buys = [s.features.buys_per_sec for s in samples if s.outcome.win and s.features.buys_per_sec > 0]
            loser_buys = [s.features.buys_per_sec for s in samples if not s.outcome.win and s.features.buys_per_sec > 0]
            
            winner_delta = [s.features.delta_pct for s in samples if s.outcome.win]
            loser_delta = [s.features.delta_pct for s in samples if not s.outcome.win]
            
            strategy_stats[strat] = {
                "count": len(samples),
                "win_rate": strat_wins / len(samples) if samples else 0,
                "avg_duration": statistics.mean(durations) if durations else 0,
                "avg_buys_sec_winners": statistics.mean(winner_buys) if winner_buys else 0,
                "avg_buys_sec_losers": statistics.mean(loser_buys) if loser_buys else 0,
                "avg_delta_winners": statistics.mean(winner_delta) if winner_delta else 0,
                "avg_delta_losers": statistics.mean(loser_delta) if loser_delta else 0,
            }
        
        # By hour
        by_hour = defaultdict(list)
        for s in self.samples:
            by_hour[s.features.hour].append(s)
        
        hour_stats = {}
        for hour, samples in sorted(by_hour.items()):
            hour_wins = sum(1 for s in samples if s.outcome.win)
            hour_stats[hour] = {
                "count": len(samples),
                "win_rate": hour_wins / len(samples) if samples else 0,
            }
        
        # BTC correlation analysis
        positive_btc = [s for s in self.samples if s.features.dBTC > 0]
        negative_btc = [s for s in self.samples if s.features.dBTC < 0]
        
        btc_analysis = {
            "positive_dBTC_count": len(positive_btc),
            "positive_dBTC_win_rate": sum(1 for s in positive_btc if s.outcome.win) / len(positive_btc) if positive_btc else 0,
            "negative_dBTC_count": len(negative_btc),
            "negative_dBTC_win_rate": sum(1 for s in negative_btc if s.outcome.win) / len(negative_btc) if negative_btc else 0,
        }
        
        # Feature importance hints
        feature_hints = self._calculate_feature_hints()
        
        return {
            "total_samples": total,
            "overall_win_rate": wins / total if total else 0,
            "by_strategy": strategy_stats,
            "by_hour": hour_stats,
            "btc_correlation": btc_analysis,
            "feature_hints": feature_hints,
        }
    
    def _calculate_feature_hints(self) -> Dict:
        """Calculate which features seem most predictive"""
        if len(self.samples) < 20:
            return {"status": "insufficient_data"}
        
        winners = [s for s in self.samples if s.outcome.win]
        losers = [s for s in self.samples if not s.outcome.win]
        
        if not winners or not losers:
            return {"status": "no_variance"}
        
        hints = {}
        
        # buys_per_sec
        win_buys = [s.features.buys_per_sec for s in winners if s.features.buys_per_sec > 0]
        lose_buys = [s.features.buys_per_sec for s in losers if s.features.buys_per_sec > 0]
        if win_buys and lose_buys:
            hints["buys_per_sec"] = {
                "winner_avg": statistics.mean(win_buys),
                "loser_avg": statistics.mean(lose_buys),
                "suggested_threshold": statistics.median(win_buys) * 0.8,
            }
        
        # delta_pct
        win_delta = [s.features.delta_pct for s in winners if s.features.delta_pct > 0]
        lose_delta = [s.features.delta_pct for s in losers if s.features.delta_pct > 0]
        if win_delta and lose_delta:
            hints["delta_pct"] = {
                "winner_avg": statistics.mean(win_delta),
                "loser_avg": statistics.mean(lose_delta),
                "suggested_threshold": statistics.median(win_delta) * 0.8,
            }
        
        # vol_raise_pct
        win_vol = [s.features.vol_raise_pct for s in winners if s.features.vol_raise_pct > 0]
        lose_vol = [s.features.vol_raise_pct for s in losers if s.features.vol_raise_pct > 0]
        if win_vol and lose_vol:
            hints["vol_raise_pct"] = {
                "winner_avg": statistics.mean(win_vol),
                "loser_avg": statistics.mean(lose_vol),
                "suggested_threshold": statistics.median(win_vol) * 0.7,
            }
        
        # dBTC (market correlation)
        win_dbtc = [s.features.dBTC for s in winners]
        lose_dbtc = [s.features.dBTC for s in losers]
        if win_dbtc and lose_dbtc:
            hints["dBTC"] = {
                "winner_avg": statistics.mean(win_dbtc),
                "loser_avg": statistics.mean(lose_dbtc),
                "insight": "Positive dBTC = BTC going up = generally better for alts",
            }
        
        return hints
    
    def suggest_thresholds(self) -> Dict:
        """Suggest optimal thresholds based on data"""
        analysis = self.analyze()
        hints = analysis.get("feature_hints", {})
        
        suggestions = {
            "min_buys_sec": hints.get("buys_per_sec", {}).get("suggested_threshold", 20),
            "min_delta_pct": hints.get("delta_pct", {}).get("suggested_threshold", 1.5),
            "min_vol_raise_pct": hints.get("vol_raise_pct", {}).get("suggested_threshold", 50),
            "prefer_positive_dBTC": hints.get("dBTC", {}).get("winner_avg", 0) > hints.get("dBTC", {}).get("loser_avg", 0),
        }
        
        # Best strategy
        best_strat = None
        best_wr = 0
        for strat, stats in analysis.get("by_strategy", {}).items():
            if stats["count"] >= 5 and stats["win_rate"] > best_wr:
                best_wr = stats["win_rate"]
                best_strat = strat
        
        suggestions["best_strategy"] = best_strat
        suggestions["best_strategy_win_rate"] = best_wr
        
        # Best hours
        best_hours = []
        for hour, stats in analysis.get("by_hour", {}).items():
            if stats["count"] >= 3 and stats["win_rate"] > 0.5:
                best_hours.append(hour)
        
        suggestions["best_hours"] = best_hours
        
        return suggestions


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="HOPE AI MoonBot Data Collector")
    parser.add_argument("--log", type=str, help="Process MoonBot log file")
    parser.add_argument("--watch", type=str, help="Watch log file for real-time collection")
    parser.add_argument("--output", type=str, default="state/ai/training_data.jsonl", help="Output file")
    parser.add_argument("--analyze", type=str, help="Analyze collected data file")
    parser.add_argument("--suggest", type=str, help="Suggest thresholds from data")
    parser.add_argument("--test", type=str, help="Test parse a single line")
    
    args = parser.parse_args()
    
    if args.test:
        parser_inst = MoonBotParser()
        
        # Try as signal
        features = parser_inst.parse_signal(args.test)
        if features:
            print("=== SIGNAL PARSED ===")
            print(json.dumps(features.to_dict(), indent=2))
            return
        
        # Try as AutoClose
        autoclose = parser_inst.parse_autoclose(args.test)
        if autoclose:
            print("=== AUTOCLOSE PARSED ===")
            print(f"Timestamp: {autoclose[0]}")
            print(f"Symbol: {autoclose[1]}")
            print(f"Duration: {autoclose[2]} sec")
            return
        
        print("Failed to parse line")
        return
    
    if args.log:
        collector = DataCollector(args.output)
        collector.process_file(args.log)
        print(f"\nStats: {json.dumps(collector.get_stats(), indent=2)}")
    
    elif args.watch:
        collector = DataCollector(args.output)
        collector.watch_file(args.watch)
    
    elif args.analyze:
        analyzer = DataAnalyzer(args.analyze)
        analysis = analyzer.analyze()
        print(json.dumps(analysis, indent=2))
    
    elif args.suggest:
        analyzer = DataAnalyzer(args.suggest)
        suggestions = analyzer.suggest_thresholds()
        print("\n=== SUGGESTED THRESHOLDS ===")
        print(json.dumps(suggestions, indent=2))
    
    else:
        parser.print_help()
        
        # Demo
        print("\n" + "=" * 60)
        print("  DEMO: Parse MoonBot signal")
        print("=" * 60)
        
        demo_line = """17:08:22   Signal USDT-ENJ Ask:0.030300  dBTC: -1.33 dBTC5m: 0.67 dBTC1m: 0.25 24hBTC: -2.53 72hBTC: -2.81 dMarkets: -2.07 dMarkets24: -4.58  AutoStart: FALSE (manual mode)  AutoBuy is off by strategy settings <Delta_1_SIGNAL> Autodetect ON;  Autodetected  [ Delta: USDT-ENJ  DailyVol: 3.6m  HourlyVol: 994 k  Delta: 2.5%  LastDelta: 0.5%  Vol: 120.6 k BTC  VolRaise: 242.5%  Buyers: 100   Vol/Sec: 3.54 k USDT] (strategy <Delta_1_SIGNAL>)"""
        
        parser_inst = MoonBotParser()
        features = parser_inst.parse_signal(demo_line)
        
        if features:
            print(f"\nSymbol: {features.symbol}")
            print(f"Strategy: {features.strategy}")
            print(f"Price: {features.price}")
            print(f"Delta: {features.delta_pct}%")
            print(f"VolRaise: {features.vol_raise_pct}%")
            print(f"Buyers: {features.buyers_count}")
            print(f"dBTC: {features.dBTC} (BTC correlation)")
            print(f"dMarkets: {features.dMarkets} (Market sentiment)")
            print(f"Hour: {features.hour}")
            
            print("\n=== FULL FEATURES ===")
            print(json.dumps(features.to_dict(), indent=2))


if __name__ == "__main__":
    main()
