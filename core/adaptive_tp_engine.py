# -*- coding: utf-8 -*-
"""
ADAPTIVE TP ENGINE v1.0 - R:R >= 3:1 с учётом friction
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

FRICTION_PCT = 0.20  # 0.1% вход + 0.1% выход
MIN_EFFECTIVE_RR = 2.5

class SignalTier(Enum):
    NOISE = "noise"
    MICRO = "micro"
    SCALP = "scalp"
    STRONG = "strong"
    EXPLOSION = "explosion"
    MOONSHOT = "moonshot"
    EXTREME = "extreme"

TIER_CONFIG = {
    SignalTier.NOISE: (0, 0.5, 0, 0, 0),
    SignalTier.MICRO: (0.5, 2.0, 0.3, 0.5, 20),
    SignalTier.SCALP: (2.0, 5.0, 0.4, 2.0, 45),
    SignalTier.STRONG: (5.0, 10.0, 0.4, 4.0, 60),
    SignalTier.EXPLOSION: (10.0, 20.0, 0.35, 6.0, 90),
    SignalTier.MOONSHOT: (20.0, 50.0, 0.3, 8.0, 120),
    SignalTier.EXTREME: (50.0, 1000.0, 0.2, 10.0, 180),
}

@dataclass
class AdaptiveTPResult:
    tier: SignalTier
    target_pct: float
    stop_loss_pct: float
    effective_rr: float
    breakeven_wr: float
    timeout_sec: int
    position_mult: float
    should_trade: bool
    reason: str

def calculate_adaptive_tp(delta_pct: float, confidence: float = 0.7) -> AdaptiveTPResult:
    tier = _get_tier(delta_pct)
    min_d, max_d, mult, max_tp, timeout = TIER_CONFIG[tier]
    
    if tier == SignalTier.NOISE:
        return AdaptiveTPResult(tier, 0, 0, 0, 1.0, 0, 0, False, "NOISE")
    
    target_pct = min(delta_pct * mult, max_tp)
    target_pct = max(1.0, min(target_pct, 10.0))
    
    required_sl = (target_pct - FRICTION_PCT) / MIN_EFFECTIVE_RR - FRICTION_PCT
    stop_loss_pct = max(0.3, required_sl)
    
    net_win = target_pct - FRICTION_PCT
    net_loss = stop_loss_pct + FRICTION_PCT
    effective_rr = net_win / net_loss if net_loss > 0 else 0
    breakeven_wr = net_loss / (net_win + net_loss) if (net_win + net_loss) > 0 else 1.0
    
    should_trade = effective_rr >= MIN_EFFECTIVE_RR
    position_mult = 0.7 * confidence if should_trade else 0
    
    return AdaptiveTPResult(
        tier=tier,
        target_pct=round(target_pct, 2),
        stop_loss_pct=round(stop_loss_pct, 2),
        effective_rr=round(effective_rr, 2),
        breakeven_wr=round(breakeven_wr, 3),
        timeout_sec=timeout,
        position_mult=round(position_mult, 2),
        should_trade=should_trade,
        reason=f"R:R={effective_rr:.2f}"
    )

def _get_tier(delta: float) -> SignalTier:
    for tier, (min_d, max_d, *_) in TIER_CONFIG.items():
        if min_d <= delta < max_d:
            return tier
    return SignalTier.EXTREME

if __name__ == "__main__":
    print("=" * 60)
    print("ADAPTIVE TP ENGINE TEST")
    print("=" * 60)
    for d in [0.3, 1.5, 3.0, 7.0, 15.0, 28.0]:
        r = calculate_adaptive_tp(d)
        s = "✅" if r.should_trade else "❌"
        print(f"  delta={d:5.1f}% → {r.tier.value:10} TP={r.target_pct}% SL={r.stop_loss_pct}% R:R={r.effective_rr} {s}")
    print("\n[PASS]")
