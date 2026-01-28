# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T18:35:00Z
# Purpose: Signal generation engine combining multiple data sources
# Security: Fail-closed on any error, input validation
# === END SIGNATURE ===
from __future__ import annotations
import hashlib
from dataclasses import dataclass
from typing import Optional
from enum import Enum
import numpy as np
from core.ai.technical_indicators import (
    TechnicalIndicators, IndicatorResult, MACDResult, BollingerResult, VolumeProfile,
)

class SignalDirection(Enum):
    LONG = 'LONG'
    SHORT = 'SHORT'
    NEUTRAL = 'NEUTRAL'

@dataclass(frozen=True)
class MarketData:
    symbol: str
    timestamp: int
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    volumes: np.ndarray
    def __post_init__(self):
        if len(self.closes) < 35:
            raise ValueError('MarketData requires at least 35 candles')
        if not all(len(arr) == len(self.closes) for arr in [self.opens, self.highs, self.lows, self.volumes]):
            raise ValueError('All price arrays must have same length')

@dataclass(frozen=True)
class TradingSignal:
    signal_id: str
    symbol: str
    direction: SignalDirection
    confidence: float
    entry_price: float
    stop_loss: float
    take_profit: float
    timestamp: int
    technical_score: float
    ml_score: float
    sentiment_score: float
    volume_score: float
    rsi: float
    macd_histogram: float
    bollinger_position: float
    atr: float
    invalidation_price: Optional[float] = None
    notes: str = ''

@dataclass
class SignalEngineConfig:
    technical_weight: float = 0.40
    ml_weight: float = 0.35
    sentiment_weight: float = 0.15
    volume_weight: float = 0.10
    min_confidence: float = 0.60
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bb_period: int = 20
    bb_std: float = 2.0
    atr_period: int = 14
    volume_period: int = 20
    stop_loss_atr_mult: float = 1.5
    take_profit_atr_mult: float = 3.0
    def __post_init__(self):
        total = self.technical_weight + self.ml_weight + self.sentiment_weight + self.volume_weight
        if abs(total - 1.0) > 0.001:
            raise ValueError(f'Weights must sum to 1.0, got {total}')

class SignalEngine:
    def __init__(self, config: Optional[SignalEngineConfig] = None):
        self.config = config or SignalEngineConfig()
        self._ml_model = None
    
    def generate_signal(self, market_data: MarketData, sentiment_score: Optional[float] = None, ml_prediction: Optional[float] = None) -> Optional[TradingSignal]:
        try:
            return self._generate_signal_impl(market_data, sentiment_score, ml_prediction)
        except Exception:
            return None
    
    def _generate_signal_impl(self, market_data: MarketData, sentiment_score: Optional[float], ml_prediction: Optional[float]) -> Optional[TradingSignal]:
        rsi_result = TechnicalIndicators.rsi(market_data.closes, self.config.rsi_period)
        macd_result = TechnicalIndicators.macd(market_data.closes, self.config.macd_fast, self.config.macd_slow, self.config.macd_signal)
        bb_result = TechnicalIndicators.bollinger_bands(market_data.closes, self.config.bb_period, self.config.bb_std)
        atr_value = TechnicalIndicators.atr(market_data.highs, market_data.lows, market_data.closes, self.config.atr_period)
        volume_result = TechnicalIndicators.volume_profile(market_data.volumes, self.config.volume_period)
        technical_score = self._calc_technical_score(rsi_result, macd_result, bb_result)
        volume_score = self._calc_volume_score(volume_result, technical_score)
        sent_score = max(-1.0, min(1.0, sentiment_score or 0.0))
        ml_score = max(-1.0, min(1.0, ml_prediction or 0.0))
        combined_score = (technical_score * self.config.technical_weight + ml_score * self.config.ml_weight + sent_score * self.config.sentiment_weight + volume_score * self.config.volume_weight)
        confidence = abs(combined_score)
        if confidence < self.config.min_confidence:
            return None
        direction = SignalDirection.LONG if combined_score > 0 else SignalDirection.SHORT if combined_score < 0 else None
        if direction is None:
            return None
        current_price = float(market_data.closes[-1])
        if direction == SignalDirection.LONG:
            stop_loss = current_price - (atr_value * self.config.stop_loss_atr_mult)
            take_profit = current_price + (atr_value * self.config.take_profit_atr_mult)
        else:
            stop_loss = current_price + (atr_value * self.config.stop_loss_atr_mult)
            take_profit = current_price - (atr_value * self.config.take_profit_atr_mult)
        signal_id = self._generate_signal_id(market_data, combined_score)
        return TradingSignal(signal_id=signal_id, symbol=market_data.symbol, direction=direction, confidence=confidence, entry_price=current_price, stop_loss=stop_loss, take_profit=take_profit, timestamp=market_data.timestamp, technical_score=technical_score, ml_score=ml_score, sentiment_score=sent_score, volume_score=volume_score, rsi=rsi_result.value, macd_histogram=macd_result.histogram, bollinger_position=bb_result.position, atr=atr_value, invalidation_price=stop_loss)
    
    def _calc_technical_score(self, rsi: IndicatorResult, macd: MACDResult, bb: BollingerResult) -> float:
        score = 0.0
        if rsi.signal == 'BUY':
            score += 0.3 * rsi.strength
        elif rsi.signal == 'SELL':
            score -= 0.3 * rsi.strength
        if macd.crossover == 'BULLISH':
            score += 0.4 * macd.trend_strength
        elif macd.crossover == 'BEARISH':
            score -= 0.4 * macd.trend_strength
        else:
            score += 0.2 * min(1.0, abs(macd.histogram) / 0.01) * (1 if macd.histogram > 0 else -1)
        bb_signal = 0.5 - bb.position
        score += 0.3 * (bb_signal * 2)
        return max(-1.0, min(1.0, score))
    
    def _calc_volume_score(self, volume: VolumeProfile, direction: float) -> float:
        if volume.spike:
            return direction * 0.8
        if volume.trend == 'INCREASING':
            return direction * 0.5
        elif volume.trend == 'DECREASING':
            return direction * 0.2
        return direction * 0.3
    
    @staticmethod
    def _generate_signal_id(market_data: MarketData, score: float) -> str:
        content = f'{market_data.symbol}:{market_data.timestamp}:{score:.6f}'
        hash_val = hashlib.sha256(content.encode()).hexdigest()[:16]
        return f'sha256:{hash_val}'

    def scan_market(self, symbols: list, market_data_map: dict) -> list:
        """
        Scan multiple symbols for trading signals.

        Args:
            symbols: List of symbol strings (e.g., ['BTCUSDT', 'ETHUSDT'])
            market_data_map: Dict mapping symbol to MarketData

        Returns:
            List of TradingSignal objects for symbols with valid signals
        """
        signals = []
        for symbol in symbols:
            if symbol not in market_data_map:
                continue
            market_data = market_data_map[symbol]
            try:
                signal = self.generate_signal(market_data)
                if signal is not None:
                    signals.append(signal)
            except Exception:
                continue
        # Sort by confidence descending
        signals.sort(key=lambda s: s.confidence, reverse=True)
        return signals
