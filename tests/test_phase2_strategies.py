# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T19:35:00Z
# Purpose: Unit tests for Phase 2 - Strategies and Orchestrator
# Security: Test-only, no production impact
# === END SIGNATURE ===
import pytest
import numpy as np
from core.ai.signal_engine import MarketData, SignalDirection
from core.strategy.regime import Regime, RegimeResult, RegimeConfig, detect_regime
from core.strategy.base import BaseStrategy, StrategyConfig, PositionSide
from core.strategy.momentum import MomentumStrategy
from core.strategy.mean_reversion import MeanReversionStrategy
from core.strategy.breakout import BreakoutStrategy
from core.strategy.orchestrator import StrategyOrchestrator, OrchestratorConfig, DecisionAction

def make_market_data(symbol: str, n: int = 100, trend: str = "up") -> MarketData:
    """Create synthetic market data for testing."""
    base = 100.0
    if trend == "up":
        closes = np.array([base + i * 0.5 + np.random.randn() * 0.1 for i in range(n)])
    elif trend == "down":
        closes = np.array([base + 50 - i * 0.5 + np.random.randn() * 0.1 for i in range(n)])
    else:  # sideways
        closes = np.array([base + np.sin(i * 0.2) * 2 + np.random.randn() * 0.1 for i in range(n)])
    highs = closes + np.abs(np.random.randn(n)) * 0.5
    lows = closes - np.abs(np.random.randn(n)) * 0.5
    opens = (closes + lows) / 2
    volumes = np.array([1000 + np.random.randint(0, 500) for _ in range(n)], dtype=float)
    return MarketData(symbol=symbol, timestamp=1700000000, opens=opens, highs=highs, lows=lows, closes=closes, volumes=volumes)

class TestRegimeDetection:
    def test_trending_up(self):
        closes = np.array([100 + i * 0.5 for i in range(100)])
        atr = np.array([2.0] * 100)
        ema = closes.copy()
        result = detect_regime(closes, atr, ema)
        assert result.regime in (Regime.TRENDING_UP, Regime.RANGING)
    
    def test_volatile(self):
        closes = np.array([100.0] * 100)
        atr = np.array([5.0] * 100)  # 5% ATR = volatile
        ema = closes.copy()
        result = detect_regime(closes, atr, ema)
        assert result.regime == Regime.VOLATILE
    
    def test_insufficient_data(self):
        closes = np.array([100.0] * 10)
        atr = np.array([1.0] * 10)
        ema = closes.copy()
        result = detect_regime(closes, atr, ema)
        assert result.regime == Regime.UNKNOWN

class TestSpotOnlyPolicy:
    def test_base_strategy_blocks_short(self):
        """Verify BaseStrategy blocks SHORT in Spot mode."""
        from core.ai.signal_engine import TradingSignal
        class TestStrategy(BaseStrategy):
            def generate_signal(self, market_data):
                return None
            def should_exit(self, position, market_data):
                return None
        
        config = StrategyConfig(spot_only=True)
        strategy = TestStrategy(config)
        
        # Create SHORT signal
        short_signal = TradingSignal(
            signal_id="sha256:test123",
            symbol="BTCUSDT",
            direction=SignalDirection.SHORT,
            confidence=0.8,
            entry_price=100.0,
            stop_loss=105.0,
            take_profit=90.0,
            timestamp=1700000000,
            technical_score=0.7,
            ml_score=0.0,
            sentiment_score=0.0,
            volume_score=0.5,
            rsi=75.0,
            macd_histogram=-0.5,
            bollinger_position=0.9,
            atr=2.0,
        )
        
        # Should return None (blocked)
        position = strategy.open_position(short_signal, 1700000000)
        assert position is None
    
    def test_long_allowed(self):
        """Verify LONG signals are allowed in Spot mode."""
        from core.ai.signal_engine import TradingSignal
        class TestStrategy(BaseStrategy):
            def generate_signal(self, market_data):
                return None
            def should_exit(self, position, market_data):
                return None
        
        config = StrategyConfig(spot_only=True, min_confidence=0.5)
        strategy = TestStrategy(config)
        strategy.set_capital(10000.0)
        
        long_signal = TradingSignal(
            signal_id="sha256:test456",
            symbol="BTCUSDT",
            direction=SignalDirection.LONG,
            confidence=0.8,
            entry_price=100.0,
            stop_loss=95.0,
            take_profit=110.0,
            timestamp=1700000000,
            technical_score=0.7,
            ml_score=0.0,
            sentiment_score=0.0,
            volume_score=0.5,
            rsi=25.0,
            macd_histogram=0.5,
            bollinger_position=0.1,
            atr=2.0,
        )
        
        position = strategy.open_position(long_signal, 1700000000)
        assert position is not None
        assert position.side == PositionSide.LONG

class TestOrchestratorSpotOnly:
    def test_orchestrator_blocks_short(self):
        """Verify orchestrator blocks SHORT signals in Spot mode."""
        momentum = MomentumStrategy()
        config = OrchestratorConfig(spot_only=True)
        orchestrator = StrategyOrchestrator([momentum], config)
        
        market_data = make_market_data("BTCUSDT", n=100, trend="up")
        decision = orchestrator.decide(market_data, [])
        
        # Should never have SHORT signal
        if decision.signal:
            assert decision.signal.direction != SignalDirection.SHORT

class TestStrategies:
    def test_momentum_strategy_creates(self):
        strategy = MomentumStrategy()
        assert strategy is not None
        assert strategy.momentum_config.rsi_oversold == 30.0
    
    def test_mean_reversion_creates(self):
        strategy = MeanReversionStrategy()
        assert strategy is not None
        assert strategy.mr_config.bb_period == 20
    
    def test_breakout_creates(self):
        strategy = BreakoutStrategy()
        assert strategy is not None
        assert strategy.bo_config.lookback_period == 20

class TestOrchestrator:
    def test_orchestrator_creates(self):
        strategies = [MomentumStrategy(), MeanReversionStrategy(), BreakoutStrategy()]
        orchestrator = StrategyOrchestrator(strategies)
        assert len(orchestrator._strategies) == 3
    
    def test_orchestrator_hold_on_insufficient_data(self):
        strategies = [MomentumStrategy()]
        orchestrator = StrategyOrchestrator(strategies)

        # 40 candles: enough for MarketData (35 min) but not for regime (50 min)
        market_data = make_market_data("BTCUSDT", n=40)
        decision = orchestrator.decide(market_data, [])
        assert decision.action == DecisionAction.HOLD
    
    def test_dedup_cache_works(self):
        strategies = [MomentumStrategy()]
        orchestrator = StrategyOrchestrator(strategies)
        
        orchestrator._dedup_cache[("BTCUSDT", "15m", "LONG")] = 1700000000
        
        # Clear should work
        orchestrator.clear_dedup_cache()
        assert len(orchestrator._dedup_cache) == 0

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
