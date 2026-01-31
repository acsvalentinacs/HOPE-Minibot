# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 14:55:00 UTC
# Purpose: Adaptive Target Calculator - dynamic TP based on pump strength
# === END SIGNATURE ===
"""
Adaptive Target AI v1.0

Calculates optimal take-profit percentage based on:
- Pump strength (delta%)
- Coin volatility history (ATR)
- Market conditions
- Historical max drawdown after pumps

Formula: target% = base × pump_mult × volatility_factor × safety_margin
"""

import asyncio
import math
import logging
from dataclasses import dataclass
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta
from pathlib import Path
import json

log = logging.getLogger("adaptive_target")

# === TIER DEFINITIONS ===
@dataclass
class PumpTier:
    """Pump classification tier with trading parameters."""
    name: str
    min_delta: float
    max_delta: float
    base_target: float
    base_stop: float
    timeout_sec: int
    pump_multiplier: float  # How much delta affects target

PUMP_TIERS = [
    PumpTier("NOISE",     0.0,   0.3,  0.0,  0.0,   0,   0.0),  # Don't trade
    PumpTier("MICRO",     0.3,   1.0,  0.3,  0.2,  20,   0.3),  # Micro scalp
    PumpTier("SCALP",     1.0,   3.0,  1.0,  0.5,  30,   0.4),  # Standard scalp
    PumpTier("STRONG",    3.0,   7.0,  2.5,  1.0,  45,   0.5),  # Strong pump
    PumpTier("EXPLOSION", 7.0,  15.0,  4.0,  2.0,  60,   0.6),  # Explosive
    PumpTier("MOONSHOT", 15.0,  30.0,  6.0,  3.0,  90,   0.7),  # Moon!
    PumpTier("EXTREME",  30.0, 100.0, 10.0,  5.0, 120,   0.8),  # Extreme
]

# === COIN VOLATILITY DATABASE ===
# ATR multipliers based on historical data
COIN_VOLATILITY = {
    # Low volatility (stable)
    "BTCUSDT": 0.8,
    "ETHUSDT": 0.9,
    "BNBUSDT": 0.85,

    # Medium volatility
    "SOLUSDT": 1.0,
    "XRPUSDT": 1.0,
    "ADAUSDT": 1.0,
    "DOGEUSDT": 1.1,
    "LINKUSDT": 1.0,
    "AVAXUSDT": 1.05,

    # High volatility (meme/small caps)
    "PEPEUSDT": 1.4,
    "SHIBUSDT": 1.3,
    "FLOKIUSDT": 1.5,
    "BONKUSDT": 1.5,
    "WIFUSDT": 1.6,
    "MEMEUSDT": 1.5,

    # Very high volatility (new/small)
    "KITEUSDT": 1.7,
    "DUSKUSDT": 1.4,
    "XVSUSDT": 1.5,
    "ARPAUSDT": 1.6,
    "SYNUSDT": 1.5,
}

# === HISTORICAL DRAWDOWN DATA ===
# Max drawdown observed after pump peaks (for safety calculation)
HISTORICAL_DRAWDOWN = {
    "BTCUSDT": 0.15,   # 15% max drawdown after pump
    "ETHUSDT": 0.18,
    "SOLUSDT": 0.25,
    "DOGEUSDT": 0.35,
    "PEPEUSDT": 0.50,  # Very volatile - 50% drawdowns
    "DEFAULT": 0.30,
}


def get_pump_tier(delta_pct: float) -> PumpTier:
    """Get the pump tier based on delta percentage."""
    for tier in PUMP_TIERS:
        if tier.min_delta <= delta_pct < tier.max_delta:
            return tier
    return PUMP_TIERS[-1]  # EXTREME for anything > 30%


def get_volatility_factor(symbol: str) -> float:
    """Get volatility multiplier for a symbol."""
    return COIN_VOLATILITY.get(symbol, 1.0)


def get_safety_margin(symbol: str, delta_pct: float) -> float:
    """
    Calculate safety margin based on historical drawdown.

    Higher delta = more risk of reversal = lower safety margin
    """
    base_drawdown = HISTORICAL_DRAWDOWN.get(symbol, HISTORICAL_DRAWDOWN["DEFAULT"])

    # The bigger the pump, the more likely a reversal
    # Use diminishing returns to avoid being too conservative
    # Use abs(delta_pct) to handle momentum signals with negative short-term delta
    reversal_risk = min(0.5, base_drawdown * math.log1p(abs(delta_pct) / 5))

    # Safety margin: 1.0 = full target, 0.5 = half target
    safety = max(0.5, 1.0 - reversal_risk)

    return safety


def calculate_adaptive_target(
    symbol: str,
    delta_pct: float,
    buys_per_sec: float = 0.0,
    volume_raise_pct: float = 0.0,
) -> Dict:
    """
    Calculate adaptive target based on pump strength and coin characteristics.

    Returns:
        {
            "tier": str,
            "target_pct": float,
            "stop_pct": float,
            "timeout_sec": int,
            "reasoning": str,
            "confidence": float
        }
    """
    # Get pump tier
    tier = get_pump_tier(delta_pct)

    # Skip noise
    if tier.name == "NOISE":
        return {
            "tier": "NOISE",
            "target_pct": 0.0,
            "stop_pct": 0.0,
            "timeout_sec": 0,
            "reasoning": f"Delta {delta_pct:.2f}% is noise, not trading",
            "confidence": 0.0,
            "trade": False,
        }

    # Get factors
    volatility = get_volatility_factor(symbol)
    safety = get_safety_margin(symbol, delta_pct)

    # Calculate pump multiplier using diminishing returns
    # sqrt gives diminishing returns - a 4x pump doesn't give 4x target
    # Use abs(delta_pct) to handle negative deltas (momentum signals can have negative short-term delta)
    abs_delta = abs(delta_pct) if delta_pct != 0 else 0.01  # Avoid division by zero
    pump_factor = math.sqrt(abs_delta / tier.min_delta) if tier.min_delta > 0 else 1.0
    pump_factor = min(pump_factor, 3.0)  # Cap at 3x

    # Buys pressure bonus (more buying = more confidence)
    buys_bonus = 1.0
    if buys_per_sec >= 100:
        buys_bonus = 1.3
    elif buys_per_sec >= 50:
        buys_bonus = 1.15
    elif buys_per_sec >= 30:
        buys_bonus = 1.05

    # Volume bonus
    vol_bonus = 1.0
    if volume_raise_pct >= 500:
        vol_bonus = 1.2
    elif volume_raise_pct >= 200:
        vol_bonus = 1.1

    # === FINAL CALCULATION ===
    # target = base × pump_factor × volatility × safety × bonuses
    raw_target = tier.base_target * pump_factor * volatility * safety * buys_bonus * vol_bonus

    # Apply caps based on tier
    max_targets = {
        "MICRO": 0.5,
        "SCALP": 1.5,
        "STRONG": 4.0,
        "EXPLOSION": 7.0,
        "MOONSHOT": 12.0,
        "EXTREME": 20.0,
    }
    max_target = max_targets.get(tier.name, 5.0)
    target_pct = min(raw_target, max_target)

    # Stop loss = half of target, minimum 0.2%
    stop_pct = max(0.2, target_pct * 0.5)

    # Timeout scales with tier
    timeout_sec = tier.timeout_sec

    # Confidence based on signal strength
    confidence = min(0.95, 0.5 + (delta_pct / 20) + (buys_per_sec / 200))

    reasoning = (
        f"Tier={tier.name} | "
        f"delta={delta_pct:.2f}% | "
        f"pump_factor={pump_factor:.2f} | "
        f"volatility={volatility:.2f} | "
        f"safety={safety:.2f} | "
        f"buys_bonus={buys_bonus:.2f}"
    )

    return {
        "tier": tier.name,
        "target_pct": round(target_pct, 2),
        "stop_pct": round(stop_pct, 2),
        "timeout_sec": timeout_sec,
        "reasoning": reasoning,
        "confidence": round(confidence, 3),
        "trade": True,
    }


# === QUICK LOOKUP TABLE ===
# Pre-calculated targets for common scenarios
QUICK_LOOKUP = {}

def _build_lookup_table():
    """Build quick lookup table for common delta values."""
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "PEPEUSDT"]
    deltas = [0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0, 30.0]

    for symbol in symbols:
        for delta in deltas:
            key = f"{symbol}_{delta}"
            QUICK_LOOKUP[key] = calculate_adaptive_target(symbol, delta, 50, 100)

_build_lookup_table()


def quick_target_lookup(symbol: str, delta_pct: float) -> Optional[Dict]:
    """Quick lookup for common scenarios."""
    # Round delta to nearest lookup value
    lookup_deltas = [0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0, 30.0]
    closest_delta = min(lookup_deltas, key=lambda x: abs(x - delta_pct))

    key = f"{symbol}_{closest_delta}"
    return QUICK_LOOKUP.get(key)


# === ASYNC API ===
async def get_adaptive_target(
    symbol: str,
    delta_pct: float,
    buys_per_sec: float = 0.0,
    volume_raise_pct: float = 0.0,
) -> Dict:
    """Async wrapper for calculate_adaptive_target."""
    return calculate_adaptive_target(symbol, delta_pct, buys_per_sec, volume_raise_pct)


# === HISTORY TRACKER ===
class TargetHistory:
    """Track target calculations for analysis and learning."""

    def __init__(self, history_file: Path = None):
        self.history_file = history_file or Path("state/ai/target_history.jsonl")
        self.history_file.parent.mkdir(parents=True, exist_ok=True)

    def record(self, symbol: str, delta: float, target_data: Dict, outcome: str = None):
        """Record a target calculation."""
        record = {
            "ts": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "delta": delta,
            **target_data,
            "outcome": outcome,
        }

        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def get_stats(self, symbol: str = None) -> Dict:
        """Get statistics for analysis."""
        if not self.history_file.exists():
            return {"count": 0}

        records = []
        with open(self.history_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line.strip())
                    if symbol is None or r.get("symbol") == symbol:
                        records.append(r)
                except:
                    pass

        if not records:
            return {"count": 0}

        return {
            "count": len(records),
            "avg_target": sum(r.get("target_pct", 0) for r in records) / len(records),
            "tiers": {t: sum(1 for r in records if r.get("tier") == t) for t in ["MICRO", "SCALP", "STRONG", "EXPLOSION"]},
        }


# === EXAMPLE / TEST ===
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("=" * 70)
    print("ADAPTIVE TARGET AI v1.0 - TEST")
    print("=" * 70)

    test_cases = [
        ("BTCUSDT", 0.2, 10, 50),    # Noise
        ("BTCUSDT", 0.5, 20, 100),   # Micro
        ("ETHUSDT", 2.0, 40, 150),   # Scalp
        ("SOLUSDT", 5.0, 60, 200),   # Strong
        ("DOGEUSDT", 10.0, 80, 300), # Explosion
        ("PEPEUSDT", 20.0, 120, 500),# Moonshot
        ("PEPEUSDT", 35.0, 150, 800),# Extreme
    ]

    print(f"\n{'Symbol':<12} {'Delta%':>8} {'Tier':<12} {'Target%':>10} {'Stop%':>8} {'Timeout':>8} {'Conf':>6}")
    print("-" * 70)

    for symbol, delta, buys, vol in test_cases:
        result = calculate_adaptive_target(symbol, delta, buys, vol)
        print(f"{symbol:<12} {delta:>8.1f} {result['tier']:<12} {result['target_pct']:>10.2f} {result['stop_pct']:>8.2f} {result['timeout_sec']:>8} {result['confidence']:>6.2f}")

    print("\n" + "=" * 70)
    print("QUICK LOOKUP TABLE (pre-calculated):")
    print("=" * 70)

    for key, val in list(QUICK_LOOKUP.items())[:10]:
        print(f"  {key}: target={val['target_pct']:.2f}%, stop={val['stop_pct']:.2f}%")
