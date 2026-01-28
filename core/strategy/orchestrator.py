# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T19:30:00Z
# Purpose: Strategy orchestrator
# Security: Fail-closed, Spot-only
# === END SIGNATURE ===
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple
import time
from core.ai.signal_engine import TradingSignal, MarketData, SignalDirection
from core.ai.technical_indicators import TechnicalIndicators
from core.strategy.regime import Regime, RegimeResult, detect_regime
from core.strategy.base import BaseStrategy, Position

class DecisionAction(str, Enum):
    ENTER = 'ENTER'
    EXIT = 'EXIT'
    HOLD = 'HOLD'

class DenyReason(str, Enum):
    SPOT_SHORT_FORBIDDEN = 'SPOT_SHORT_FORBIDDEN'
    DEDUP_WITHIN_TTL = 'DEDUP_WITHIN_TTL'
    NO_STRATEGY_MATCH = 'NO_STRATEGY_MATCH'
    STRATEGY_ERROR = 'STRATEGY_ERROR'
    INSUFFICIENT_DATA = 'INSUFFICIENT_DATA'

@dataclass(frozen=True)
class OrchestratorDecision:
    action: DecisionAction
    signal: Optional[TradingSignal]
    strategy_name: str
    regime: Regime
    confidence: float
    reason: str
    timestamp: int
    @property
    def is_actionable(self) -> bool:
        return self.action in (DecisionAction.ENTER, DecisionAction.EXIT)

@dataclass
class OrchestratorConfig:
    spot_only: bool = True
    dedup_ttl_seconds: int = 300
    trending_strategies: List[str] = field(default_factory=lambda: ['momentum', 'breakout'])
    ranging_strategies: List[str] = field(default_factory=lambda: ['mean_reversion', 'momentum'])
    volatile_strategies: List[str] = field(default_factory=lambda: ['breakout', 'momentum'])  # momentum as fallback
    exit_priority: bool = True
    min_confidence: float = 0.40  # Lowered for backtest (was 0.55)

class StrategyOrchestrator:
    def __init__(self, strategies: List[BaseStrategy], config: Optional[OrchestratorConfig] = None):
        self.config = config or OrchestratorConfig()
        self._strategies: Dict[str, BaseStrategy] = {}
        for s in strategies:
            name = getattr(s, 'name', s.__class__.__name__.lower())
            self._strategies[name] = s
        self._dedup_cache: Dict[Tuple[str, str, str], int] = {}
        self._decision_history: List[OrchestratorDecision] = []
    
    def decide(self, market_data: MarketData, current_positions: List[Position], timeframe: str = '15m') -> OrchestratorDecision:
        ts = market_data.timestamp or int(time.time())
        try:
            return self._decide_impl(market_data, current_positions, timeframe, ts)
        except Exception as e:
            return OrchestratorDecision(action=DecisionAction.HOLD, signal=None, strategy_name='orchestrator', regime=Regime.UNKNOWN, confidence=0.0, reason=f'{DenyReason.STRATEGY_ERROR}:{str(e)[:50]}', timestamp=ts)
    
    def _decide_impl(self, market_data: MarketData, current_positions: List[Position], timeframe: str, ts: int) -> OrchestratorDecision:
        regime_result = self._detect_regime(market_data)
        regime = regime_result.regime
        if regime == Regime.UNKNOWN:
            return OrchestratorDecision(action=DecisionAction.HOLD, signal=None, strategy_name='orchestrator', regime=regime, confidence=0.0, reason=f'{DenyReason.INSUFFICIENT_DATA}:{regime_result.reason}', timestamp=ts)
        ordered = self._get_strategies_for_regime(regime)
        if not ordered:
            return OrchestratorDecision(action=DecisionAction.HOLD, signal=None, strategy_name='orchestrator', regime=regime, confidence=0.0, reason=f'{DenyReason.NO_STRATEGY_MATCH}', timestamp=ts)
        symbol_pos = [p for p in current_positions if p.symbol == market_data.symbol]
        for pos in symbol_pos:
            for name, strat in ordered:
                exit_reason = strat.should_exit(pos, market_data)
                if exit_reason:
                    return OrchestratorDecision(action=DecisionAction.EXIT, signal=None, strategy_name=name, regime=regime, confidence=0.9, reason=f'EXIT:{exit_reason}', timestamp=ts)
        if not symbol_pos:
            candidates = []
            for name, strat in ordered:
                sig = strat.generate_signal(market_data)
                if sig:
                    if self.config.spot_only and sig.direction == SignalDirection.SHORT:
                        continue
                    candidates.append((name, sig))
            if candidates:
                best_name, best_sig = max(candidates, key=lambda x: x[1].confidence)
                if best_sig.confidence < self.config.min_confidence:
                    return OrchestratorDecision(action=DecisionAction.HOLD, signal=None, strategy_name=best_name, regime=regime, confidence=best_sig.confidence, reason='LOW_CONFIDENCE', timestamp=ts)
                dedup_key = (market_data.symbol, timeframe, best_sig.direction.value)
                last_ts = self._dedup_cache.get(dedup_key)
                if last_ts and (ts - last_ts) < self.config.dedup_ttl_seconds:
                    return OrchestratorDecision(action=DecisionAction.HOLD, signal=None, strategy_name=best_name, regime=regime, confidence=best_sig.confidence, reason=f'{DenyReason.DEDUP_WITHIN_TTL}', timestamp=ts)
                self._dedup_cache[dedup_key] = ts
                decision = OrchestratorDecision(action=DecisionAction.ENTER, signal=best_sig, strategy_name=best_name, regime=regime, confidence=best_sig.confidence, reason=f'ENTER:{best_sig.direction.value}', timestamp=ts)
                self._decision_history.append(decision)
                return decision
        return OrchestratorDecision(action=DecisionAction.HOLD, signal=None, strategy_name='orchestrator', regime=regime, confidence=0.0, reason='NO_SIGNAL', timestamp=ts)
    
    def _detect_regime(self, market_data: MarketData) -> RegimeResult:
        try:
            atr_vals, ema_vals = [], []
            for i in range(15, len(market_data.closes)):
                atr = TechnicalIndicators.atr(market_data.highs[:i+1], market_data.lows[:i+1], market_data.closes[:i+1], period=14)
                atr_vals.append(atr)
                ema = TechnicalIndicators.ema(market_data.closes[:i+1], period=20)
                ema_vals.append(ema)
            if len(atr_vals) < 50:
                return RegimeResult(regime=Regime.UNKNOWN, atr_pct=0.0, slope=0.0, confidence=0.0, reason='INSUFFICIENT')
            return detect_regime(closes=market_data.closes, atr_values=atr_vals, ema_values=ema_vals)
        except:
            return RegimeResult(regime=Regime.UNKNOWN, atr_pct=0.0, slope=0.0, confidence=0.0, reason='ERROR')
    
    def _get_strategies_for_regime(self, regime: Regime) -> List[Tuple[str, BaseStrategy]]:
        if regime in (Regime.TRENDING_UP, Regime.TRENDING_DOWN):
            names = self.config.trending_strategies
        elif regime == Regime.RANGING:
            names = self.config.ranging_strategies
        elif regime == Regime.VOLATILE:
            names = self.config.volatile_strategies
        else:
            names = list(self._strategies.keys())
        return [(n, self._strategies[n]) for n in names if n in self._strategies]
    
    def get_decision_history(self, limit: int = 100) -> List[OrchestratorDecision]:
        return self._decision_history[-limit:]
    
    def clear_dedup_cache(self) -> None:
        self._dedup_cache.clear()

    def detect_market_regime(self, market_data: MarketData) -> RegimeResult:
        """
        Public method to detect current market regime.

        Args:
            market_data: MarketData with OHLCV

        Returns:
            RegimeResult with regime classification
        """
        return self._detect_regime(market_data)

    def select_strategy(self, regime: Regime) -> Optional[BaseStrategy]:
        """
        Select best strategy for given market regime.

        Args:
            regime: Current market regime

        Returns:
            Selected BaseStrategy instance or None
        """
        strategies = self._get_strategies_for_regime(regime)
        if strategies:
            return strategies[0][1]  # Return first (highest priority) strategy
        return None

    def run_cycle(self, market_data: MarketData, current_positions: List[Position], timeframe: str = '15m') -> OrchestratorDecision:
        """
        Run single trading cycle. Alias for decide() for TZ compatibility.

        Args:
            market_data: Current market data
            current_positions: List of open positions
            timeframe: Trading timeframe

        Returns:
            OrchestratorDecision with action to take
        """
        return self.decide(market_data, current_positions, timeframe)
