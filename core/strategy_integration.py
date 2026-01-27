# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T20:00:00Z
# Modified by: Claude (opus-4)
# Modified at: 2026-01-27T21:05:00Z
# Purpose: Integration bridge between StrategyOrchestrator and SignalsPipeline
# Security: Spot-only enforcement, fail-closed, real OHLCV required
# === END SIGNATURE ===
from __future__ import annotations
import logging
import time
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
import numpy as np
from core.ai.signal_engine import MarketData, SignalDirection
from core.ai.signal_engine import TradingSignal as DetailedSignal
from core.market_intel import TradingSignal as SimpleSignal
from core.strategy.orchestrator import StrategyOrchestrator, OrchestratorConfig, OrchestratorDecision, DecisionAction
from core.strategy.momentum import MomentumStrategy
from core.strategy.mean_reversion import MeanReversionStrategy
from core.strategy.breakout import BreakoutStrategy
from core.strategy.base import Position
from core.market.klines_provider import KlinesProvider, get_klines_provider, KlinesResult

logger = logging.getLogger(__name__)

@dataclass
class IntegrationConfig:
    spot_only: bool = True
    min_confidence: float = 0.55
    enable_momentum: bool = True
    enable_mean_reversion: bool = True
    enable_breakout: bool = True
    candle_count: int = 100
    timeframe: str = '15m'
    use_real_ohlcv: bool = True  # Phase 2.5: require real OHLCV (fail-closed if unavailable)
    allow_synthetic_fallback: bool = False  # Set True for testing only

class StrategyIntegration:
    def __init__(self, config: Optional[IntegrationConfig] = None):
        self.config = config or IntegrationConfig()
        strategies = []
        if self.config.enable_momentum:
            strategies.append(MomentumStrategy())
        if self.config.enable_mean_reversion:
            strategies.append(MeanReversionStrategy())
        if self.config.enable_breakout:
            strategies.append(BreakoutStrategy())
        if not strategies:
            strategies = [MomentumStrategy()]
        orch_config = OrchestratorConfig(spot_only=self.config.spot_only, min_confidence=self.config.min_confidence)
        self._orchestrator = StrategyOrchestrator(strategies, orch_config)
        self._positions: List[Position] = []
        # Phase 2.5: Real OHLCV provider
        self._klines_provider: Optional[KlinesProvider] = None
        if self.config.use_real_ohlcv:
            self._klines_provider = get_klines_provider()
    
    def generate_signals(self, snapshot: Any, symbols: Optional[List[str]] = None) -> List[SimpleSignal]:
        signals: List[SimpleSignal] = []
        if snapshot is None:
            return signals
        if symbols is None:
            from core.market_intel import PRIORITY_SYMBOLS
            symbols = PRIORITY_SYMBOLS
        tickers = getattr(snapshot, 'tickers', {})
        for symbol in symbols:
            if symbol not in tickers:
                continue
            try:
                market_data = self._build_market_data(symbol, tickers)
                if market_data is None:
                    continue
                decision = self._orchestrator.decide(market_data, self._positions, self.config.timeframe)
                if decision.action == DecisionAction.ENTER and decision.signal:
                    simple = self._convert_to_simple_signal(decision)
                    if simple:
                        signals.append(simple)
                        logger.info('Signal: %s %s confidence=%.2f strategy=%s', symbol, decision.signal.direction.value, decision.confidence, decision.strategy_name)
            except Exception as e:
                logger.warning('Error processing %s: %s', symbol, e)
        return signals
    
    def _build_market_data(self, symbol: str, tickers: Dict[str, Any]) -> Optional[MarketData]:
        """
        Build MarketData from real OHLCV or fallback to synthetic (fail-closed).

        Phase 2.5: Prefers real klines from Binance API.
        If use_real_ohlcv=True and klines unavailable: returns None (fail-closed).
        """
        ticker = tickers.get(symbol)
        if ticker is None:
            return None
        price = getattr(ticker, 'price', 0.0)
        if price <= 0:
            return None

        ts = int(time.time())
        n = self.config.candle_count

        # Phase 2.5: Try real OHLCV first
        if self._klines_provider is not None:
            klines = self._klines_provider.get_klines(
                symbol=symbol,
                timeframe=self.config.timeframe,
                limit=n,
            )
            if klines is not None and not klines.is_stale and klines.candle_count >= 35:
                logger.debug("Using real OHLCV for %s (%d candles)", symbol, klines.candle_count)
                try:
                    return MarketData(
                        symbol=symbol,
                        timestamp=ts,
                        opens=klines.opens,
                        highs=klines.highs,
                        lows=klines.lows,
                        closes=klines.closes,
                        volumes=klines.volumes,
                    )
                except ValueError as e:
                    logger.warning("MarketData validation failed for %s: %s", symbol, e)
                    return None

            # Real OHLCV required but unavailable
            if self.config.use_real_ohlcv and not self.config.allow_synthetic_fallback:
                logger.warning("Real OHLCV unavailable for %s, fail-closed", symbol)
                return None

            logger.debug("Real OHLCV unavailable for %s, falling back to synthetic", symbol)

        # Fallback: Synthetic OHLCV (only if allow_synthetic_fallback=True or use_real_ohlcv=False)
        if not self.config.allow_synthetic_fallback and self.config.use_real_ohlcv:
            return None  # Fail-closed

        high = getattr(ticker, 'high_24h', price * 1.02)
        low = getattr(ticker, 'low_24h', price * 0.98)
        volume = getattr(ticker, 'volume_24h', 1000000.0)

        np.random.seed(int(price * 1000) % (2**31))
        noise = np.random.randn(n) * (high - low) * 0.1
        closes = np.array([price + noise[i] * (i - n/2) / n for i in range(n)])
        closes[-1] = price
        highs = closes + np.abs(np.random.randn(n)) * (high - low) * 0.05
        lows = closes - np.abs(np.random.randn(n)) * (high - low) * 0.05
        opens = (closes + lows) / 2
        volumes = np.abs(np.array([volume / n + np.random.randn() * volume * 0.1 for _ in range(n)]))

        logger.debug("Using synthetic OHLCV for %s (fallback)", symbol)
        try:
            return MarketData(symbol=symbol, timestamp=ts, opens=opens, highs=highs, lows=lows, closes=closes, volumes=volumes)
        except ValueError:
            return None
    
    def _convert_to_simple_signal(self, decision: OrchestratorDecision) -> Optional[SimpleSignal]:
        if decision.signal is None:
            return None
        sig = decision.signal
        if self.config.spot_only and sig.direction == SignalDirection.SHORT:
            logger.warning('Blocked SHORT at integration: %s', sig.symbol)
            return None
        direction = 'long' if sig.direction == SignalDirection.LONG else 'short'
        type_map = {'momentum': 'momentum', 'momentumstrategy': 'momentum', 'mean_reversion': 'mean_reversion', 'meanreversionstrategy': 'mean_reversion', 'breakout': 'price_breakout', 'breakoutstrategy': 'price_breakout'}
        signal_type = type_map.get(decision.strategy_name.lower(), 'momentum')
        return SimpleSignal(symbol=sig.symbol, signal_type=signal_type, direction=direction, strength=sig.confidence, reason=f'{decision.strategy_name}:{decision.reason}', timestamp=float(sig.timestamp), entry_price=sig.entry_price, invalidation_price=sig.invalidation_price)
    
    def get_orchestrator(self) -> StrategyOrchestrator:
        return self._orchestrator

_integration_instance: Optional[StrategyIntegration] = None

def get_strategy_integration(config: Optional[IntegrationConfig] = None) -> StrategyIntegration:
    global _integration_instance
    if _integration_instance is None:
        _integration_instance = StrategyIntegration(config)
    return _integration_instance
