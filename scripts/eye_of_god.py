# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 00:35:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-30 01:15:00 UTC
# Purpose: Eye of God - Multi-factor AI Oracle with unified config and self-learning
# === END SIGNATURE ===

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.io_atomic import append_jsonl, compute_sha256_prefix
from core.oracle_config import get_config_manager, get_config, OracleConfig

logger = logging.getLogger("EYE-OF-GOD")


@dataclass
class PredictionResult:
    action: str
    confidence: float
    symbol: str
    reasons: List[str]
    factors: Dict[str, float]
    adaptive_params: Dict[str, float]
    sha256: str
    timestamp: str

    def to_dict(self):
        return asdict(self)


class EyeOfGod:
    """
    Multi-factor AI Oracle with fail-closed contracts and self-learning.

    Uses UNIFIED CONFIG from core.oracle_config - single source of truth.
    """

    def __init__(self, state_dir=None, min_confidence=0.50):
        self.state_dir = Path(state_dir or "state/ai/oracle")
        self.state_dir.mkdir(parents=True, exist_ok=True)

        # Use unified config manager
        self.config_manager = get_config_manager()
        self.predictions_file = self.state_dir / "predictions.jsonl"

        # Override min_confidence if provided (for backward compat)
        self._min_confidence_override = min_confidence

    @property
    def config(self) -> OracleConfig:
        """Get current config from unified source."""
        return self.config_manager.get()

    @property
    def min_confidence(self) -> float:
        """Get min confidence from config or override."""
        return self._min_confidence_override or self.config.min_confidence

    @property
    def calibration(self) -> float:
        """Get calibration from config."""
        return self.config.calibration

    @property
    def symbol_stats(self) -> Dict:
        """Get symbol stats from config."""
        return self.config.symbol_stats

    def predict(self, signal, prices, regime="NEUTRAL"):
        """
        Generate prediction with fail-closed guarantees.

        Uses UNIFIED CONFIG for whitelist/blacklist/thresholds.
        """
        ts = datetime.now(timezone.utc).isoformat()
        symbol = signal.get("symbol", "").upper().strip()
        cfg = self.config  # Get current config

        if not symbol:
            return self._skip("", ["NO_SYMBOL"], {}, ts)

        # P0: PRICE VERIFICATION (FAIL-CLOSED)
        if symbol not in prices or prices.get(symbol, 0) <= 0:
            logger.warning(f"[FAIL-CLOSED] PRICE_MISSING:{symbol}")
            return self._skip(symbol, [f"PRICE_MISSING:{symbol}"], {}, ts)

        # P0: PAUSED SYMBOL CHECK
        if symbol in cfg.paused:
            return self._skip(symbol, [f"PAUSED:{symbol}"], {"paused": -1.0}, ts)

        # P0: BLACKLIST (from unified config)
        if symbol in cfg.blacklist:
            return self._skip(symbol, [f"BLACKLIST:{symbol}"], {"blacklist": -1.0}, ts)

        # Direction filter
        if signal.get("direction", "Long") != "Long":
            return self._skip(symbol, ["SHORT_DIRECTION"], {}, ts)

        # === MULTI-FACTOR SCORING ===
        factors = {}
        reasons = []
        base = 0.50

        buys = float(signal.get("buys_per_sec", 0))
        delta = float(signal.get("delta_pct", 0))
        vol = float(signal.get("vol_raise_pct", 0))
        strategy = signal.get("strategy", "unknown")

        # WHITELIST BOOST (from unified config)
        if symbol in cfg.whitelist:
            factors["whitelist"] = 0.25
            reasons.append(f"WHITELIST:{symbol}")

        # SYMBOL STATS FACTOR (learned from outcomes)
        symbol_factor = self.config_manager.get_symbol_factor(symbol)
        if symbol_factor != 0:
            factors["symbol_history"] = symbol_factor

        # STRATEGY MODIFIER (from config)
        strategy_mod = self.config_manager.get_strategy_modifier(strategy)
        if strategy_mod != 0:
            factors["strategy"] = strategy_mod

        # SIGNAL STRENGTH FACTORS
        if delta >= 5.0:
            factors["delta_strong"] = 0.15
        elif delta >= cfg.min_delta_pct:
            factors["delta_med"] = 0.10
        elif delta < cfg.min_delta_pct:
            factors["delta_weak"] = -0.10

        if buys >= cfg.pump_override_buys:
            factors["buys_pump"] = 0.20
            reasons.append(f"PUMP_OVERRIDE:buys={buys:.0f}")
        elif buys >= 50:
            factors["buys_high"] = 0.10
        elif buys >= cfg.scalp_buys:
            factors["buys_med"] = 0.05
        elif buys < cfg.scalp_buys:
            factors["buys_low"] = -0.05

        if vol >= 150:
            factors["vol_spike"] = 0.10

        # CALCULATE FINAL CONFIDENCE
        conf = base + sum(factors.values())
        conf = max(0.0, min(1.0, conf * cfg.calibration))

        # APPLY RISK MULTIPLIER to size, not confidence
        effective_min_conf = self.min_confidence

        if conf >= effective_min_conf:
            action = "BUY"
            adapt = self._adaptive_params(conf, cfg.risk_multiplier)
            reasons.extend([f for f in factors.keys() if factors[f] > 0])
        else:
            action = "SKIP"
            reasons.append(f"LOW_CONF:{conf*100:.0f}%<{effective_min_conf*100:.0f}%")
            adapt = {}

        data = {
            "action": action,
            "confidence": round(conf, 4),
            "symbol": symbol,
            "reasons": reasons,
            "factors": factors,
            "adaptive_params": adapt,
            "timestamp": ts
        }
        sha = compute_sha256_prefix(json.dumps(data, sort_keys=True).encode())
        data["sha256"] = sha

        result = PredictionResult(**data)
        self._log_prediction(result)
        logger.info(f"[{action}] {symbol}: conf={conf*100:.0f}%")
        return result

    def _skip(self, symbol, reasons, factors, ts):
        data = {"action": "SKIP", "confidence": 0.0, "symbol": symbol,
                "reasons": reasons, "factors": factors, "adaptive_params": {}, "timestamp": ts}
        sha = compute_sha256_prefix(json.dumps(data, sort_keys=True).encode())
        data["sha256"] = sha
        return PredictionResult(**data)

    def _adaptive_params(self, conf: float, risk_mult: float = 1.0) -> Dict:
        """Calculate adaptive params based on confidence and risk multiplier."""
        if conf >= 0.75:
            base = {"target_mult": 1.5, "stop_mult": 0.8, "size_mult": 1.5}
        elif conf >= 0.60:
            base = {"target_mult": 1.0, "stop_mult": 1.0, "size_mult": 1.0}
        else:
            base = {"target_mult": 0.7, "stop_mult": 1.2, "size_mult": 0.5}

        # Apply risk multiplier to size
        base["size_mult"] *= risk_mult
        return base

    def _log_prediction(self, result):
        """Log prediction with sha256 contract."""
        append_jsonl(self.predictions_file, result.to_dict(), add_sha256=False)

    def record_outcome(self, symbol: str, is_win: bool, pnl: float = 0) -> Dict:
        """
        Record trade outcome and trigger self-learning.

        Uses UNIFIED CONFIG for atomic updates and auto-whitelist/blacklist.
        """
        return self.config_manager.record_outcome(symbol, is_win, pnl)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("\n" + "=" * 60)
    print("  EYE OF GOD v2.0 - UNIFIED CONFIG TEST")
    print("=" * 60)

    oracle = EyeOfGod()
    cfg = oracle.config

    print(f"\nConfig Source: {oracle.config_manager.path}")
    print(f"Whitelist: {sorted(cfg.whitelist)}")
    print(f"Blacklist: {sorted(cfg.blacklist)}")
    print(f"Min Confidence: {cfg.min_confidence}")
    print(f"Risk Multiplier: {cfg.risk_multiplier}")
    print(f"Calibration: {cfg.calibration}")

    print("\n--- TEST PREDICTIONS ---")

    # Test WHITELIST
    r = oracle.predict(
        {"symbol": "KITEUSDT", "buys_per_sec": 55, "delta_pct": 2.8, "direction": "Long"},
        {"KITEUSDT": 0.145}
    )
    print(f"WHITELIST (KITEUSDT): {r.action} conf={r.confidence*100:.0f}%")

    # Test BLACKLIST
    r = oracle.predict(
        {"symbol": "SYNUSDT", "buys_per_sec": 100, "delta_pct": 5.0, "direction": "Long"},
        {"SYNUSDT": 0.06}
    )
    print(f"BLACKLIST (SYNUSDT): {r.action} conf={r.confidence*100:.0f}%")

    # Test NO_PRICE (P0 fail-closed)
    r = oracle.predict(
        {"symbol": "NEWUSDT", "buys_per_sec": 50, "delta_pct": 3.0, "direction": "Long"},
        {}
    )
    print(f"NO_PRICE (NEWUSDT): {r.action} conf={r.confidence*100:.0f}%")

    # Test neutral symbol
    r = oracle.predict(
        {"symbol": "BTCUSDT", "buys_per_sec": 80, "delta_pct": 4.0, "direction": "Long"},
        {"BTCUSDT": 100000.0}
    )
    print(f"NEUTRAL (BTCUSDT): {r.action} conf={r.confidence*100:.0f}%")

    print("\n--- SELF-LEARNING TEST ---")
    # Simulate outcomes
    changes = oracle.record_outcome("BTCUSDT", True, 0.5)
    print(f"Recorded WIN for BTCUSDT: {changes}")
    changes = oracle.record_outcome("BTCUSDT", True, 0.8)
    print(f"Recorded WIN for BTCUSDT: {changes}")
    changes = oracle.record_outcome("BTCUSDT", True, 0.3)
    print(f"Recorded WIN for BTCUSDT: {changes}")
    # After 3 wins, should auto-whitelist
    if "auto_whitelist" in changes:
        print(f"AUTO-WHITELIST triggered: {changes['auto_whitelist']}")

    print("\n" + "=" * 60)
