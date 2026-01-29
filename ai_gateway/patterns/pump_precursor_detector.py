# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 10:30:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-29 13:30:00 UTC
# Purpose: Pump Precursor Pattern Detector - "Предвестник пампа"
# === END SIGNATURE ===
"""
HOPE AI - Pump Precursor Pattern Detector
Реализация паттерна "Предвестник пампа"

ГИПОТЕЗА: За 30-60 секунд ДО пампа появляются сигналы:
1. VolRaise > 50% (объём растёт)
2. Buys/sec > 3 (активные покупки)
3. dBTC5m > dBTC1m (ускорение)
4. Delta растёт последовательно: 0.5% → 1% → 2%

ДЕЙСТВИЕ: Если 3 из 4 условий = BUY SIGNAL
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional


class PrecursorSignal(Enum):
    """Типы сигналов предвестника."""
    VOLUME_RAISE = "volume_raise"      # VolRaise > 50%
    ACTIVE_BUYS = "active_buys"        # Buys/sec > 3
    ACCELERATING = "accelerating"      # dBTC5m > dBTC1m
    DELTA_SEQUENCE = "delta_sequence"  # Sequential delta growth


@dataclass
class PrecursorResult:
    """Результат детекции предвестника."""
    symbol: str
    timestamp: str
    signals_detected: List[str]
    signal_count: int
    is_precursor: bool
    confidence: float
    prediction: str  # BUY | WATCH | SKIP
    raw_data: Dict


class PumpPrecursorDetector:
    """
    Детектор паттерна "Предвестник пампа".

    Usage:
        detector = PumpPrecursorDetector()
        result = detector.detect_precursor(signal)
        if result.prediction == 'BUY':
            execute_trade(signal)
    """

    # Пороговые значения (tunable)
    THRESHOLDS = {
        'vol_raise_min': 50,           # % рост объёма
        'buys_per_sec_min': 3,         # минимум покупок/сек
        'delta_sequence': [0.5, 1.0, 2.0],  # последовательность delta
        'min_signals_for_buy': 3,      # минимум сигналов для BUY
    }

    def __init__(self):
        self.signal_history: Dict[str, List[Dict]] = {}

    def add_signal(self, signal: Dict) -> None:
        """Добавить сигнал в историю."""
        symbol = signal.get('symbol', 'UNKNOWN')
        if symbol not in self.signal_history:
            self.signal_history[symbol] = []
        self.signal_history[symbol].append(signal)

        # Храним только последние 100 сигналов на символ
        if len(self.signal_history[symbol]) > 100:
            self.signal_history[symbol] = self.signal_history[symbol][-100:]

    def detect_precursor(self, signal: Dict) -> PrecursorResult:
        """
        Проверить сигнал на паттерн "Предвестник пампа".

        Returns:
            PrecursorResult с детекцией
        """
        symbol = signal.get('symbol', 'UNKNOWN')
        detected_signals = []

        # 1. Volume Raise > 50%
        vol_raise = signal.get('vol_raise', 0)
        if vol_raise >= self.THRESHOLDS['vol_raise_min']:
            detected_signals.append(PrecursorSignal.VOLUME_RAISE.value)

        # 2. Buys/sec > 3
        buys_per_sec = signal.get('buys_per_sec', 0)
        if buys_per_sec >= self.THRESHOLDS['buys_per_sec_min']:
            detected_signals.append(PrecursorSignal.ACTIVE_BUYS.value)

        # 3. Acceleration: dBTC5m > dBTC1m
        dbtc5m = signal.get('dBTC5m', 0)
        dbtc1m = signal.get('dBTC1m', 0)
        if dbtc5m > dbtc1m and dbtc5m > 0:
            detected_signals.append(PrecursorSignal.ACCELERATING.value)

        # 4. Delta sequence check (need history)
        if self._check_delta_sequence(symbol, signal):
            detected_signals.append(PrecursorSignal.DELTA_SEQUENCE.value)

        # Calculate result
        signal_count = len(detected_signals)
        is_precursor = signal_count >= self.THRESHOLDS['min_signals_for_buy']

        # Confidence based on signal count
        confidence = signal_count / 4.0
        if buys_per_sec > 30:  # Boost for high activity
            confidence = min(confidence * 1.2, 1.0)

        prediction = 'BUY' if is_precursor else 'WATCH' if signal_count >= 2 else 'SKIP'

        return PrecursorResult(
            symbol=symbol,
            timestamp=signal.get('timestamp', ''),
            signals_detected=detected_signals,
            signal_count=signal_count,
            is_precursor=is_precursor,
            confidence=round(confidence, 3),
            prediction=prediction,
            raw_data=signal
        )

    def _check_delta_sequence(self, symbol: str, current: Dict) -> bool:
        """Проверить последовательный рост delta."""
        history = self.signal_history.get(symbol, [])
        if len(history) < 3:
            return False

        recent = history[-3:]
        deltas = [s.get('delta_pct', 0) for s in recent]
        current_delta = current.get('delta_pct', 0)

        all_deltas = deltas + [current_delta]
        return all(all_deltas[i] < all_deltas[i+1] for i in range(len(all_deltas)-1))

    def analyze_batch(self, signals: List[Dict]) -> Dict:
        """Анализ пакета сигналов."""
        results = {
            'total': len(signals),
            'precursors': 0,
            'watch': 0,
            'skip': 0,
            'by_symbol': {},
            'details': []
        }

        for sig in signals:
            self.add_signal(sig)
            result = self.detect_precursor(sig)
            results['details'].append(asdict(result))

            if result.prediction == 'BUY':
                results['precursors'] += 1
            elif result.prediction == 'WATCH':
                results['watch'] += 1
            else:
                results['skip'] += 1

            sym = result.symbol
            if sym not in results['by_symbol']:
                results['by_symbol'][sym] = {'precursors': 0, 'total': 0}
            results['by_symbol'][sym]['total'] += 1
            if result.is_precursor:
                results['by_symbol'][sym]['precursors'] += 1

        return results
