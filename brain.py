from __future__ import annotations
import logging
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional

# Fix import path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hope.ai.regime_filter import RegimeFilter
from hope.ai.regime import MarketRegime
from hope.ai.risk_scorer import RiskScorer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('Brain')

@dataclass
class TradeDecision:
    action: str
    symbol: str
    quantity: float
    regime: str
    confidence: float
    risk_score: float
    reason: str

class Brain:
    def __init__(self, base_position_size: float = 100.0):
        self.regime_filter = RegimeFilter(cache_size=100, cache_ttl=120)
        self.risk_scorer = RiskScorer(base_risk=50.0)
        self.base_size = base_position_size
        logger.info('Brain initialized')

    def decide(self, symbol: str, closes: List[float], highs: List[float], lows: List[float], volumes: List[float], strategy_signal: Optional[str] = None) -> TradeDecision:
        regime_sig = self.regime_filter.get_regime(symbol, closes, highs, lows, volumes)
        if not regime_sig.allows_trading:
            return TradeDecision('HOLD', symbol, 0.0, regime_sig.regime.name, regime_sig.confidence, 100.0, 'Blocked: ' + regime_sig.regime.name)
        risk = self.risk_scorer.assess(regime_sig)
        if risk.multiplier <= 0.0:
            return TradeDecision('HOLD', symbol, 0.0, regime_sig.regime.name, regime_sig.confidence, risk.score, 'High risk')
        if strategy_signal == 'LONG' and self.regime_filter.should_enter_long(symbol, closes, highs, lows, volumes):
            qty = self.base_size * risk.multiplier
            return TradeDecision('BUY', symbol, qty, regime_sig.regime.name, regime_sig.confidence, risk.score, 'LONG OK')
        if strategy_signal == 'SHORT' and self.regime_filter.should_enter_short(symbol, closes, highs, lows, volumes):
            qty = self.base_size * risk.multiplier
            return TradeDecision('SELL', symbol, qty, regime_sig.regime.name, regime_sig.confidence, risk.score, 'SHORT OK')
        return TradeDecision('HOLD', symbol, 0.0, regime_sig.regime.name, regime_sig.confidence, risk.score, 'No match')

if __name__ == '__main__':
    brain = Brain(100.0)
    c = [100 + i * 1.5 for i in range(120)]
    h = [x + 1 for x in c]
    l = [x - 0.5 for x in c]
    v = [1000.0] * 120
    print('=' * 50)
    print('BRAIN TEST')
    print('=' * 50)
    for sig in ['LONG', 'SHORT', None]:
        d = brain.decide('TEST', c, h, l, v, sig)
        print(str(sig) + ': ' + d.action + ' qty=' + str(round(d.quantity, 1)) + ' regime=' + d.regime)
    print('=' * 50)
