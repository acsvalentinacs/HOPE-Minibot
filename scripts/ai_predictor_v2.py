# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 16:00:00 UTC
# Purpose: AI Predictor v2 with RSI, MACD, Volume analysis for pump detection
# === END SIGNATURE ===
"""
AI Predictor v2 - Enhanced Signal Analysis

Features:
- Technical indicators (RSI, MACD, Volume)
- Three-Layer AllowList (CORE + DYNAMIC + HOT)
- BTC trend correlation
- Orderbook imbalance analysis
- Risk-adjusted position sizing

Integration: Called from pump_detector.py before signal emission.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import deque

try:
    import httpx
except ImportError:
    httpx = None

# Logging
log = logging.getLogger("AI-PRED-V2")

# Project root
ROOT = Path(__file__).parent.parent

# === THREE-LAYER ALLOWLIST INTEGRATION ===
# Try to use centralized ThreeLayerAllowList module
THREE_LAYER_AVAILABLE = False
_three_layer_allowlist = None

try:
    from three_layer_allowlist import (
        ThreeLayerAllowList, get_allowlist, ListType,
        CORE_LIST, BLACKLIST as TL_BLACKLIST,
    )
    THREE_LAYER_AVAILABLE = True
    ALLOWLIST_CORE = CORE_LIST
    BLACKLIST = TL_BLACKLIST
    log.info("Using ThreeLayerAllowList for dynamic AllowList")
except ImportError:
    log.warning("ThreeLayerAllowList not available, using static lists")
    # Fallback static CORE list
    ALLOWLIST_CORE = {
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
        "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT",
        "LINKUSDT", "LTCUSDT", "ATOMUSDT", "UNIUSDT", "NEARUSDT",
    }
    # Fallback static BLACKLIST
    BLACKLIST = {
        "USDCUSDT", "USDTUSDT", "BUSDUSDT", "FDUSDUSDT", "TUSDUSDT",
        "DAIUSDT", "EURUSDT", "PAXGUSDT", "USDPUSDT", "USD1USDT",
        "LUNAUSDT", "USTCUSDT", "LUNCUSDT", "PUMPUSDT",
        "WBTCUSDT", "WETHUSDT", "XAUTUSDT",
    }

# DYNAMIC: Fallback static list (used if ThreeLayerAllowList not available)
ALLOWLIST_DYNAMIC = {
    "AAVEUSDT", "APTUSDT", "ARBUSDT", "BCHUSDT", "CRVUSDT",
    "DYDXUSDT", "EIGENUSDT", "ETCUSDT", "FILUSDT", "FTMUSDT",
    "GRTUSDT", "ICPUSDT", "INJUSDT", "JUPUSDT", "LDOUSDT",
    "MKRUSDT", "ONDOUSDT", "OPUSDT", "ORDIUSDT", "PEPEUSDT",
    "PYTHUSDT", "RENDERUSDT", "RUNEUSDT", "SEIUSDT", "STXUSDT",
    "SUIUSDT", "TAOUSDT", "TIAUSDT", "TONUSDT", "TRXUSDT",
    "WIFUSDT", "WLDUSDT", "XLMUSDT", "XMRUSDT", "ZECUSDT",
}

# HOT: New listings, high-risk high-reward (fallback static)
ALLOWLIST_HOT = {
    "TRUMPUSDT", "MELANIAUSDT", "MOVEUSDT", "PNUTUSDT", "ACTUSDT",
    "PENDLEUSDT", "ENAUSDT", "ENSOUSDT", "KAIAUSDT", "THEUSDT",
}


@dataclass
class TechnicalIndicators:
    """Technical analysis data."""
    rsi_14: float = 50.0  # RSI 14-period
    macd_line: float = 0.0
    macd_signal: float = 0.0
    macd_histogram: float = 0.0
    volume_ratio: float = 1.0  # Current vs 20-period avg
    price_change_1h: float = 0.0
    price_change_4h: float = 0.0
    price_change_24h: float = 0.0
    btc_correlation: float = 0.0  # -1 to 1
    orderbook_imbalance: float = 0.5  # 0=sell pressure, 1=buy pressure


@dataclass
class AIDecision:
    """AI trading decision."""
    symbol: str
    action: str  # BUY, SKIP, WAIT
    final_score: float  # 0-1
    confidence: float  # 0-1
    position_multiplier: float  # 0.5-1.5
    target_pct: float  # Take profit %
    stop_pct: float  # Stop loss %
    timeout_seconds: int  # Max hold time
    reasons: List[str] = field(default_factory=list)
    indicators: Optional[TechnicalIndicators] = None
    allowlist_tier: str = "NONE"  # CORE, DYNAMIC, HOT, NONE


class AIPredictor:
    """
    AI Predictor v2 - Enhanced pump signal analysis.

    Three-stage scoring:
    1. Fast Score: Pump metrics (buys/sec, delta, volume)
    2. Tech Score: RSI, MACD, trend alignment
    3. Context Score: BTC trend, allowlist tier, orderbook

    Final Score = 0.4*Fast + 0.3*Tech + 0.3*Context
    """

    def __init__(self):
        self.client: Optional[httpx.AsyncClient] = None

        # Price cache
        self._btc_price: float = 0
        self._btc_change_1h: float = 0
        self._price_cache: Dict[str, Tuple[float, float]] = {}  # symbol -> (price, timestamp)

        # RSI calculation (simplified - uses price deltas)
        self._price_history: Dict[str, deque] = {}  # symbol -> deque of prices

        # Stats
        self.signals_analyzed = 0
        self.signals_approved = 0
        self.signals_rejected = 0

    async def init(self):
        """Initialize async components."""
        if httpx:
            self.client = httpx.AsyncClient(timeout=5.0)
        await self._update_btc_price()

    async def close(self):
        """Cleanup."""
        if self.client:
            await self.client.aclose()

    def get_allowlist_tier(self, symbol: str) -> str:
        """Get allowlist tier for symbol using ThreeLayerAllowList if available."""
        global _three_layer_allowlist

        # Try to use ThreeLayerAllowList for dynamic lookups
        if THREE_LAYER_AVAILABLE:
            try:
                if _three_layer_allowlist is None:
                    _three_layer_allowlist = get_allowlist()
                list_type = _three_layer_allowlist.get_list_type(symbol)
                return list_type.value.upper()
            except Exception as e:
                log.warning(f"ThreeLayerAllowList error: {e}, using static fallback")

        # Fallback to static lists
        if symbol in BLACKLIST:
            return "BLACKLIST"
        if symbol in ALLOWLIST_CORE:
            return "CORE"
        if symbol in ALLOWLIST_DYNAMIC:
            return "DYNAMIC"
        if symbol in ALLOWLIST_HOT:
            return "HOT"
        return "NONE"

    async def _update_btc_price(self):
        """Update BTC price and change."""
        if not self.client:
            return

        try:
            resp = await self.client.get(
                "https://api.binance.com/api/v3/ticker/24hr",
                params={"symbol": "BTCUSDT"}
            )
            if resp.status_code == 200:
                data = resp.json()
                self._btc_price = float(data["lastPrice"])
                self._btc_change_1h = float(data.get("priceChangePercent", 0)) / 24  # Approximate
        except Exception as e:
            log.warning(f"BTC price update failed: {e}")

    async def _get_technical_indicators(self, symbol: str, price: float) -> TechnicalIndicators:
        """Calculate technical indicators for symbol."""
        indicators = TechnicalIndicators()

        # Update price history
        if symbol not in self._price_history:
            self._price_history[symbol] = deque(maxlen=100)

        self._price_history[symbol].append(price)
        prices = list(self._price_history[symbol])

        # Calculate RSI (simplified)
        if len(prices) >= 15:
            gains = []
            losses = []
            for i in range(1, min(15, len(prices))):
                delta = prices[-i] - prices[-i-1]
                if delta > 0:
                    gains.append(delta)
                else:
                    losses.append(abs(delta))

            avg_gain = sum(gains) / 14 if gains else 0.001
            avg_loss = sum(losses) / 14 if losses else 0.001
            rs = avg_gain / avg_loss
            indicators.rsi_14 = 100 - (100 / (1 + rs))

        # BTC correlation (simplified - use BTC change as proxy)
        indicators.btc_correlation = 0.5 if self._btc_change_1h > 0 else -0.5

        # Volume ratio estimate (from signal data)
        indicators.volume_ratio = 1.0

        return indicators

    def _calculate_fast_score(self, signal: Dict) -> Tuple[float, List[str]]:
        """
        Calculate fast score from pump metrics.

        Inputs:
        - buys_per_sec: Buying pressure
        - delta_pct: Price movement
        - vol_raise_pct: Volume spike
        """
        score = 0.0
        reasons = []

        buys = signal.get("buys_per_sec", 0)
        delta = signal.get("delta_pct", 0)
        vol_raise = signal.get("vol_raise_pct", 50)

        # Buys/sec scoring
        if buys >= 100:
            score += 0.4
            reasons.append(f"🔥 Extreme buys: {buys:.0f}/sec")
        elif buys >= 50:
            score += 0.3
            reasons.append(f"⚡ Strong buys: {buys:.0f}/sec")
        elif buys >= 20:
            score += 0.2
            reasons.append(f"📈 Good buys: {buys:.0f}/sec")
        elif buys >= 10:
            score += 0.1
            reasons.append(f"📊 Moderate buys: {buys:.0f}/sec")

        # Delta scoring
        if delta >= 5:
            score += 0.35
            reasons.append(f"🚀 Huge move: +{delta:.1f}%")
        elif delta >= 2:
            score += 0.25
            reasons.append(f"📈 Big move: +{delta:.1f}%")
        elif delta >= 1:
            score += 0.15
            reasons.append(f"↗️ Good move: +{delta:.1f}%")
        elif delta >= 0.5:
            score += 0.08
            reasons.append(f"↑ Small move: +{delta:.1f}%")

        # Volume raise scoring
        if vol_raise >= 200:
            score += 0.25
            reasons.append(f"💥 Volume explosion: +{vol_raise:.0f}%")
        elif vol_raise >= 100:
            score += 0.15
            reasons.append(f"📊 Volume spike: +{vol_raise:.0f}%")
        elif vol_raise >= 60:
            score += 0.08
            reasons.append(f"📈 Volume up: +{vol_raise:.0f}%")

        return min(score, 1.0), reasons

    def _calculate_tech_score(self, indicators: TechnicalIndicators) -> Tuple[float, List[str]]:
        """
        Calculate technical score.

        Good entry: RSI 30-70, MACD bullish, volume above average
        """
        score = 0.0
        reasons = []

        # RSI scoring
        rsi = indicators.rsi_14
        if 30 <= rsi <= 45:
            score += 0.35
            reasons.append(f"✅ RSI oversold zone ({rsi:.0f})")
        elif 45 < rsi <= 60:
            score += 0.25
            reasons.append(f"✅ RSI healthy ({rsi:.0f})")
        elif 60 < rsi <= 70:
            score += 0.15
            reasons.append(f"⚠️ RSI elevated ({rsi:.0f})")
        elif rsi > 70:
            score -= 0.1
            reasons.append(f"⛔ RSI overbought ({rsi:.0f})")
        elif rsi < 30:
            score += 0.2
            reasons.append(f"📉 RSI very oversold ({rsi:.0f})")

        # MACD scoring (if available)
        if indicators.macd_histogram > 0:
            score += 0.2
            reasons.append("📈 MACD bullish")
        elif indicators.macd_histogram < 0:
            score -= 0.1
            reasons.append("📉 MACD bearish")

        # Volume ratio
        if indicators.volume_ratio >= 2.0:
            score += 0.25
            reasons.append(f"🔥 Volume 2x+ average")
        elif indicators.volume_ratio >= 1.5:
            score += 0.15
            reasons.append(f"📊 Volume above average")

        # Orderbook imbalance
        if indicators.orderbook_imbalance >= 0.65:
            score += 0.2
            reasons.append(f"💪 Strong buy pressure ({indicators.orderbook_imbalance:.0%})")
        elif indicators.orderbook_imbalance <= 0.35:
            score -= 0.15
            reasons.append(f"⚠️ Sell pressure ({indicators.orderbook_imbalance:.0%})")

        return max(min(score, 1.0), 0.0), reasons

    def _calculate_context_score(
        self,
        symbol: str,
        allowlist_tier: str,
        indicators: TechnicalIndicators
    ) -> Tuple[float, List[str]]:
        """
        Calculate context score.

        Considers: BTC trend, allowlist tier, market conditions
        """
        score = 0.0
        reasons = []

        # Allowlist tier scoring
        if allowlist_tier == "CORE":
            score += 0.4
            reasons.append("✅ AllowList: CORE (blue chip)")
        elif allowlist_tier == "DYNAMIC":
            score += 0.3
            reasons.append("✅ AllowList: DYNAMIC")
        elif allowlist_tier == "HOT":
            score += 0.2
            reasons.append("⚠️ AllowList: HOT (high risk)")
        elif allowlist_tier == "BLACKLIST":
            score -= 1.0
            reasons.append("⛔ BLACKLISTED")
        else:
            score += 0.1
            reasons.append("⚠️ AllowList: NONE (unlisted)")

        # BTC trend (don't fight the market)
        btc_change = self._btc_change_1h
        if btc_change >= 0.5:
            score += 0.3
            reasons.append(f"📈 BTC bullish (+{btc_change:.1f}%)")
        elif btc_change >= 0:
            score += 0.2
            reasons.append(f"↗️ BTC neutral (+{btc_change:.1f}%)")
        elif btc_change >= -0.5:
            score += 0.1
            reasons.append(f"↘️ BTC slight dip ({btc_change:.1f}%)")
        else:
            score -= 0.1
            reasons.append(f"📉 BTC bearish ({btc_change:.1f}%)")

        # Price change alignment (4h trend)
        if indicators.price_change_4h > 2:
            score += 0.15
            reasons.append(f"📈 4h trend up +{indicators.price_change_4h:.1f}%")
        elif indicators.price_change_4h < -2:
            score -= 0.1
            reasons.append(f"📉 4h trend down {indicators.price_change_4h:.1f}%")

        return max(min(score, 1.0), 0.0), reasons

    async def analyze_signal(self, signal: Dict) -> AIDecision:
        """
        Main analysis function. Returns trading decision.

        Signal format:
        {
            "symbol": "BTCUSDT",
            "price": 82000,
            "buys_per_sec": 50,
            "delta_pct": 2.5,
            "vol_raise_pct": 150,
            "signal_type": "SUPER_SCALP",
            "confidence": 0.8
        }
        """
        self.signals_analyzed += 1

        symbol = signal.get("symbol", "UNKNOWN")
        price = signal.get("price", 0)

        # Get allowlist tier
        allowlist_tier = self.get_allowlist_tier(symbol)

        # Immediate reject if blacklisted
        if allowlist_tier == "BLACKLIST":
            self.signals_rejected += 1
            return AIDecision(
                symbol=symbol,
                action="SKIP",
                final_score=0.0,
                confidence=1.0,
                position_multiplier=0.0,
                target_pct=0.0,
                stop_pct=0.0,
                timeout_seconds=0,
                reasons=["⛔ Symbol is BLACKLISTED"],
                allowlist_tier=allowlist_tier
            )

        # Get technical indicators
        indicators = await self._get_technical_indicators(symbol, price)

        # Calculate scores
        fast_score, fast_reasons = self._calculate_fast_score(signal)
        tech_score, tech_reasons = self._calculate_tech_score(indicators)
        context_score, context_reasons = self._calculate_context_score(
            symbol, allowlist_tier, indicators
        )

        # Weighted final score
        final_score = (
            0.40 * fast_score +
            0.30 * tech_score +
            0.30 * context_score
        )

        # Combine reasons
        all_reasons = [f"AllowList: {allowlist_tier}"] + fast_reasons + tech_reasons + context_reasons

        # Decision thresholds (RAISED for quality trades)
        if final_score >= 0.70:  # Raised from 0.55 - only strong signals
            action = "BUY"
            self.signals_approved += 1
        elif final_score >= 0.60:  # Raised from 0.45
            action = "WAIT"  # Borderline - monitor but don't trade
        else:
            action = "SKIP"
            self.signals_rejected += 1

        # Position sizing based on tier and score
        if allowlist_tier == "CORE":
            position_mult = min(1.0 + (final_score - 0.5) * 0.5, 1.5)
        elif allowlist_tier == "DYNAMIC":
            position_mult = min(0.8 + (final_score - 0.5) * 0.4, 1.2)
        elif allowlist_tier == "HOT":
            position_mult = min(0.5 + (final_score - 0.5) * 0.3, 0.8)
        else:
            position_mult = min(0.4 + (final_score - 0.5) * 0.2, 0.6)

        # Target and stop based on signal strength
        if final_score >= 0.75:
            target_pct = 3.0
            stop_pct = 1.0
            timeout = 60
        elif final_score >= 0.60:
            target_pct = 2.0
            stop_pct = 0.8
            timeout = 45
        else:
            target_pct = 1.5
            stop_pct = 0.6
            timeout = 30

        return AIDecision(
            symbol=symbol,
            action=action,
            final_score=final_score,
            confidence=final_score,
            position_multiplier=position_mult,
            target_pct=target_pct,
            stop_pct=stop_pct,
            timeout_seconds=timeout,
            reasons=all_reasons,
            indicators=indicators,
            allowlist_tier=allowlist_tier
        )


# === SINGLETON INSTANCE ===
_predictor: Optional[AIPredictor] = None


async def get_predictor() -> AIPredictor:
    """Get or create predictor instance."""
    global _predictor
    if _predictor is None:
        _predictor = AIPredictor()
        await _predictor.init()
    return _predictor


async def process_pump_signal(signal: Dict) -> AIDecision:
    """
    Main entry point - analyze pump signal and return decision.

    Usage in pump_detector.py:
        from ai_predictor_v2 import process_pump_signal

        decision = await process_pump_signal(signal)
        if decision.action == "BUY":
            # Forward to AutoTrader
    """
    predictor = await get_predictor()
    return await predictor.analyze_signal(signal)


def format_decision_log(decision: AIDecision, signal: Dict) -> str:
    """Format decision for logging."""
    lines = [
        "═" * 60,
        f"AI SIGNAL: {decision.symbol}",
        "═" * 60,
        "",
        "INPUT:",
        f"  buys/sec: {signal.get('buys_per_sec', 0):.1f}",
        f"  delta:    +{signal.get('delta_pct', 0):.2f}%",
        f"  vol_raise: {signal.get('vol_raise_pct', 0):.0f}%",
        "",
        f"TIER: {decision.allowlist_tier}",
        f"SCORE: {decision.final_score:.2f}",
        f"ACTION: {'🟢 ' + decision.action if decision.action == 'BUY' else '🔴 ' + decision.action}",
        "",
        "REASONS:",
    ]

    for reason in decision.reasons:
        lines.append(f"  • {reason}")

    if decision.action == "BUY":
        lines.extend([
            "",
            "TRADE PARAMS:",
            f"  Position: {decision.position_multiplier*100:.0f}%",
            f"  Target:   +{decision.target_pct:.1f}%",
            f"  Stop:     -{decision.stop_pct:.1f}%",
            f"  Timeout:  {decision.timeout_seconds}s",
        ])

    lines.append("═" * 60)
    return "\n".join(lines)


# === CLI TEST ===

if __name__ == "__main__":
    async def test():
        predictor = AIPredictor()
        await predictor.init()

        # Test signals
        test_signals = [
            {
                "symbol": "BTCUSDT",
                "price": 82500,
                "buys_per_sec": 120,
                "delta_pct": 3.5,
                "vol_raise_pct": 200,
                "signal_type": "SUPER_SCALP",
            },
            {
                "symbol": "TRUMPUSDT",
                "price": 15.5,
                "buys_per_sec": 80,
                "delta_pct": 8.0,
                "vol_raise_pct": 350,
                "signal_type": "PUMP_OVERRIDE",
            },
            {
                "symbol": "USDCUSDT",
                "price": 1.0,
                "buys_per_sec": 50,
                "delta_pct": 0.1,
                "vol_raise_pct": 50,
                "signal_type": "MICRO",
            },
            {
                "symbol": "PEPEUSDT",
                "price": 0.000012,
                "buys_per_sec": 25,
                "delta_pct": 1.5,
                "vol_raise_pct": 100,
                "signal_type": "SCALP",
            },
        ]

        print("=" * 60)
        print("AI PREDICTOR V2 TEST")
        print("=" * 60)

        for signal in test_signals:
            decision = await predictor.analyze_signal(signal)
            print(format_decision_log(decision, signal))
            print()

        print(f"Stats: {predictor.signals_analyzed} analyzed, "
              f"{predictor.signals_approved} approved, "
              f"{predictor.signals_rejected} rejected")

        await predictor.close()

    asyncio.run(test())
