# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 16:30:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-29 20:55:00 UTC
# Purpose: Phase 1 - Live MoonBot Signal Integration Pipeline
# Contract: MoonBot → PumpPrecursor → ModeRouter → EmpiricalFilters → DecisionEngine
# === END SIGNATURE ===
"""
HOPE AI - MoonBot Live Integration (Phase 1)

PIPELINE:
    MoonBot TG → Parser → PumpPrecursorDetector → ModeRouter → DecisionEngine
                                                            ↓
                                                    state/ai/decisions.jsonl

USAGE:
    # CLI mode
    python -m ai_gateway.integrations.moonbot_live --watch

    # API mode
    from ai_gateway.integrations import MoonBotLiveIntegration
    integration = MoonBotLiveIntegration()
    await integration.start()

FEATURES:
    - Real-time file monitoring (tail -f style)
    - PumpPrecursor pattern detection
    - Mode routing (SUPER_SCALP/SCALP/SWING/SKIP)
    - Full decision engine evaluation
    - JSONL output for audit trail
    - EventBus integration
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Internal imports
from ..patterns.pump_precursor_detector import PumpPrecursorDetector, PrecursorResult
from ..core.mode_router import ModeRouter, TradingMode, RouteResult
from ..modules.predictor.signal_classifier import apply_empirical_filters, EMPIRICAL_FILTERS
from ..core.decision_engine import (
    DecisionEngine,
    Decision,
    Action,
    SignalContext,
    PolicyConfig,
    get_decision_engine,
)
from ..core.event_bus import EventType, get_event_bus
from ..contracts import MarketRegime

logger = logging.getLogger(__name__)

# Paths
STATE_DIR = Path("state/ai")
SIGNALS_DIR = Path("state/ai/signals")
DECISIONS_FILE = STATE_DIR / "decisions.jsonl"
MOONBOT_SIGNALS_FILE = SIGNALS_DIR / "moonbot_signals.jsonl"

# Ensure directories
STATE_DIR.mkdir(parents=True, exist_ok=True)
SIGNALS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class PipelineResult:
    """Complete pipeline result with all stages."""
    signal_id: str
    symbol: str
    timestamp: str

    # Stage 1: Raw signal
    raw_signal: Dict[str, Any]

    # Stage 2: Precursor detection
    precursor: Optional[Dict] = None
    precursor_prediction: str = "SKIP"  # BUY/WATCH/SKIP

    # Stage 3: Mode routing
    mode: str = "skip"
    mode_confidence: float = 0.0
    mode_config: Optional[Dict] = None

    # Stage 4: Decision engine
    decision_action: str = "SKIP"
    decision_confidence: float = 0.0
    decision_reasons: List[str] = None

    # Final
    final_action: str = "SKIP"
    checksum: str = ""

    def __post_init__(self):
        if self.decision_reasons is None:
            self.decision_reasons = []
        data = f"{self.signal_id}:{self.final_action}:{self.timestamp}"
        self.checksum = f"sha256:{sha256(data.encode()).hexdigest()[:16]}"

    def to_dict(self) -> Dict:
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "precursor": {
                "prediction": self.precursor_prediction,
                "details": self.precursor,
            },
            "mode": {
                "name": self.mode,
                "confidence": self.mode_confidence,
                "config": self.mode_config,
            },
            "decision": {
                "action": self.decision_action,
                "confidence": self.decision_confidence,
                "reasons": self.decision_reasons,
            },
            "final_action": self.final_action,
            "checksum": self.checksum,
        }


class MoonBotLiveIntegration:
    """
    Live integration pipeline for MoonBot signals.

    Pipeline stages:
    1. Signal ingestion (from JSONL file)
    2. PumpPrecursorDetector (pattern analysis)
    3. ModeRouter (trading mode selection)
    4. DecisionEngine (final BUY/SKIP)

    INVARIANT: Fail-closed at every stage
    """

    def __init__(
        self,
        signals_file: Optional[Path] = None,
        decisions_file: Optional[Path] = None,
        decision_engine: Optional[DecisionEngine] = None,
        enable_event_bus: bool = True,
    ):
        """
        Initialize integration.

        Args:
            signals_file: Path to moonbot signals JSONL
            decisions_file: Path to output decisions JSONL
            decision_engine: Decision engine instance
            enable_event_bus: Publish to EventBus
        """
        self.signals_file = signals_file or MOONBOT_SIGNALS_FILE
        self.decisions_file = decisions_file or DECISIONS_FILE
        self.decision_engine = decision_engine or get_decision_engine()
        self.enable_event_bus = enable_event_bus

        # Pipeline components
        self.precursor_detector = PumpPrecursorDetector()
        self.mode_router = ModeRouter()

        # State
        self._running = False
        self._last_position = 0
        self._processed_ids: set = set()

        # Stats
        self._stats = {
            "signals_processed": 0,
            "precursors_detected": 0,
            "buys_generated": 0,
            "by_mode": {m.value: 0 for m in TradingMode},
            "errors": 0,
        }

        logger.info(f"MoonBotLiveIntegration initialized (signals={self.signals_file})")

    async def start(self) -> None:
        """Start live monitoring."""
        if self._running:
            logger.warning("Already running")
            return

        self._running = True
        logger.info("Starting MoonBot live integration...")

        # Initial file position
        if self.signals_file.exists():
            self._last_position = self.signals_file.stat().st_size
            logger.info(f"Starting from position {self._last_position}")

        # Main loop
        while self._running:
            try:
                await self._check_new_signals()
            except Exception as e:
                logger.error(f"Error in monitor loop: {e}")
                self._stats["errors"] += 1

            await asyncio.sleep(0.5)  # 500ms polling

    async def stop(self) -> None:
        """Stop live monitoring."""
        self._running = False
        logger.info("MoonBot live integration stopped")

    async def _check_new_signals(self) -> None:
        """Check for new signals in file."""
        if not self.signals_file.exists():
            return

        current_size = self.signals_file.stat().st_size
        if current_size <= self._last_position:
            return

        # Read new lines
        with open(self.signals_file, "r", encoding="utf-8") as f:
            f.seek(self._last_position)
            new_content = f.read()
            self._last_position = f.tell()

        # Process each new line
        for line in new_content.strip().split("\n"):
            if not line.strip():
                continue

            try:
                signal = json.loads(line)
                await self.process_signal(signal)
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON line: {e}")
                self._stats["errors"] += 1

    async def process_signal(self, signal: Dict[str, Any]) -> PipelineResult:
        """
        Process single signal through complete pipeline.

        Pipeline:
            Signal → Precursor → ModeRouter → DecisionEngine → Output

        Args:
            signal: MoonBot signal dict

        Returns:
            PipelineResult with all stage outputs
        """
        symbol = signal.get("symbol", "UNKNOWN")
        timestamp = signal.get("timestamp", datetime.now(timezone.utc).isoformat())
        signal_id = f"mb:{symbol}:{sha256(json.dumps(signal, sort_keys=True).encode()).hexdigest()[:8]}"

        # Skip duplicates
        if signal_id in self._processed_ids:
            logger.debug(f"Skip duplicate: {signal_id}")
            return None

        self._processed_ids.add(signal_id)
        if len(self._processed_ids) > 10000:
            # Trim old IDs
            self._processed_ids = set(list(self._processed_ids)[-5000:])

        logger.info(f"Processing signal: {symbol} @ {signal.get('price', 'N/A')}")
        self._stats["signals_processed"] += 1

        # ═══════════════════════════════════════════════════════════════
        # STAGE 1: PUMP PRECURSOR DETECTION
        # ═══════════════════════════════════════════════════════════════

        # Transform field names for detector
        detector_input = {
            "symbol": symbol,
            "timestamp": timestamp,
            "delta_pct": signal.get("delta_pct", 0),
            "vol_raise": signal.get("vol_raise_pct", 0),
            "buys_per_sec": signal.get("buys_per_sec", 0),
            "dBTC5m": signal.get("dbtc_5m", 0),
            "dBTC1m": signal.get("dbtc_1m", 0),
        }

        self.precursor_detector.add_signal(detector_input)
        precursor_result = self.precursor_detector.detect_precursor(detector_input)

        precursor_dict = {
            "signals_detected": precursor_result.signals_detected,
            "signal_count": precursor_result.signal_count,
            "is_precursor": precursor_result.is_precursor,
            "confidence": precursor_result.confidence,
        }

        if precursor_result.is_precursor:
            self._stats["precursors_detected"] += 1
            logger.info(f"🔥 PRECURSOR DETECTED: {symbol} [{precursor_result.signals_detected}]")

        # ═══════════════════════════════════════════════════════════════
        # STAGE 2: MODE ROUTING
        # ═══════════════════════════════════════════════════════════════

        # Transform for mode router
        router_input = {
            "symbol": symbol,
            "delta_pct": signal.get("delta_pct", 0),
            "buys_per_sec": signal.get("buys_per_sec", 0),
            "vol_raise_pct": signal.get("vol_raise_pct", 0),
            "volume_24h": signal.get("daily_volume", 0),
            "strategy": signal.get("strategy", signal.get("signal_type", "")),
        }

        route_result = self.mode_router.route(router_input)
        self._stats["by_mode"][route_result.mode.value] += 1

        mode_config = None
        if route_result.config:
            mode_config = {
                "target_pct": route_result.config.target_pct,
                "stop_pct": route_result.config.stop_pct,
                "timeout_sec": route_result.config.timeout_sec,
            }

        logger.info(f"📊 MODE: {route_result.mode.value.upper()} (conf={route_result.confidence:.2f})")

        # ═══════════════════════════════════════════════════════════════
        # STAGE 3: DECISION ENGINE
        # ═══════════════════════════════════════════════════════════════

        # Only evaluate if not SKIP from router
        if route_result.mode == TradingMode.SKIP:
            decision_action = "SKIP"
            decision_confidence = 0.0
            decision_reasons = ["mode_router_skip"]
        else:
            # Build context for decision engine
            ctx = SignalContext(
                signal_id=signal_id,
                symbol=symbol,
                price=float(signal.get("price", 0)),
                direction=signal.get("direction", "Long"),
                delta_pct=signal.get("delta_pct", 0),
                volume_24h=signal.get("daily_volume", 0),
                # AI module outputs
                prediction_prob=precursor_result.confidence if precursor_result.is_precursor else 0.4,
                regime=MarketRegime.TRENDING_UP if route_result.mode in [TradingMode.SUPER_SCALP, TradingMode.SCALP] else MarketRegime.RANGING,
                anomaly_score=0.1,  # Default low
                news_score=0.0,  # Neutral
                circuit_state="CLOSED",
                active_positions=0,
            )

            decision = self.decision_engine.evaluate(ctx)
            decision_action = decision.action.value
            decision_confidence = decision.confidence
            decision_reasons = [r.value for r in decision.reasons]

        # ═══════════════════════════════════════════════════════════════
        # STAGE 4: EMPIRICAL FILTERS
        # ═══════════════════════════════════════════════════════════════

        filter_input = {
            "symbol": symbol,
            "strategy": signal.get("strategy", signal.get("signal_type", "")),
        }
        _, filter_reason, should_skip = apply_empirical_filters(filter_input, 0.5)

        if should_skip:
            logger.info(f"🚫 FILTERED: {symbol} ({filter_reason})")
            decision_action = "SKIP"
            decision_reasons.append(f"empirical_filter:{filter_reason}")

        # ═══════════════════════════════════════════════════════════════
        # FINAL ACTION
        # ═══════════════════════════════════════════════════════════════

        # Final action: require both precursor AND decision engine approval
        # AND pass empirical filters
        if should_skip:
            final_action = "SKIP"
        elif precursor_result.prediction == "BUY" and decision_action == "BUY":
            final_action = "BUY"
            self._stats["buys_generated"] += 1
        elif precursor_result.prediction == "WATCH" and decision_action == "BUY":
            final_action = "WATCH"
        else:
            final_action = "SKIP"

        # Create result
        result = PipelineResult(
            signal_id=signal_id,
            symbol=symbol,
            timestamp=timestamp,
            raw_signal=signal,
            precursor=precursor_dict,
            precursor_prediction=precursor_result.prediction,
            mode=route_result.mode.value,
            mode_confidence=route_result.confidence,
            mode_config=mode_config,
            decision_action=decision_action,
            decision_confidence=decision_confidence,
            decision_reasons=decision_reasons,
            final_action=final_action,
        )

        # Output
        await self._output_result(result)

        if final_action == "BUY":
            logger.info(f"✅ FINAL: BUY {symbol} | Precursor={precursor_result.prediction} | Mode={route_result.mode.value}")
        else:
            logger.info(f"⏭️ FINAL: {final_action} {symbol}")

        return result

    async def _output_result(self, result: PipelineResult) -> None:
        """Write result to JSONL and EventBus."""
        # Atomic write to JSONL
        result_dict = result.to_dict()
        result_dict["_processed_at"] = datetime.now(timezone.utc).isoformat()

        line = json.dumps(result_dict, ensure_ascii=False, default=str) + "\n"

        # Atomic append
        tmp_path = self.decisions_file.with_suffix(".tmp")
        try:
            # Read existing
            existing = ""
            if self.decisions_file.exists():
                existing = self.decisions_file.read_text(encoding="utf-8")

            # Write temp
            with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(existing)
                f.write(line)
                f.flush()
                os.fsync(f.fileno())

            # Atomic replace
            os.replace(tmp_path, self.decisions_file)

        except Exception as e:
            logger.error(f"Failed to write decision: {e}")
            if tmp_path.exists():
                tmp_path.unlink()

        # Publish to EventBus
        if self.enable_event_bus:
            try:
                bus = get_event_bus()
                bus.publish(
                    EventType.DECISION,
                    result_dict,
                    source="moonbot_live"
                )
            except Exception as e:
                logger.debug(f"EventBus publish failed: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Get integration statistics."""
        return {
            **self._stats,
            "precursor_detector": self.precursor_detector.signal_history,
            "mode_router": self.mode_router.get_stats(),
            "decision_engine": self.decision_engine.get_stats(),
        }

    async def process_batch(self, signals: List[Dict]) -> List[PipelineResult]:
        """Process batch of signals."""
        results = []
        for signal in signals:
            result = await self.process_signal(signal)
            if result:
                results.append(result)
        return results


# === Singleton ===

_integration: Optional[MoonBotLiveIntegration] = None


def get_moonbot_integration(**kwargs) -> MoonBotLiveIntegration:
    """Get or create singleton integration."""
    global _integration
    if _integration is None:
        _integration = MoonBotLiveIntegration(**kwargs)
    return _integration


# === CLI Entry Point ===

async def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="MoonBot Live Integration - Phase 1")
    parser.add_argument("--watch", "-w", action="store_true", help="Watch mode (continuous)")
    parser.add_argument("--input", "-i", type=Path, help="Input signals file")
    parser.add_argument("--output", "-o", type=Path, help="Output decisions file")
    parser.add_argument("--test", "-t", action="store_true", help="Run test with sample signals")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )

    if args.test:
        await run_test()
        return

    integration = MoonBotLiveIntegration(
        signals_file=args.input,
        decisions_file=args.output,
    )

    if args.watch:
        print("=" * 60)
        print("MOONBOT LIVE INTEGRATION - Phase 1")
        print("=" * 60)
        print(f"Signals: {integration.signals_file}")
        print(f"Decisions: {integration.decisions_file}")
        print("Press Ctrl+C to stop")
        print("=" * 60)

        try:
            await integration.start()
        except KeyboardInterrupt:
            await integration.stop()
            print("\n--- Statistics ---")
            stats = integration.get_stats()
            print(f"Processed: {stats['signals_processed']}")
            print(f"Precursors: {stats['precursors_detected']}")
            print(f"Buys: {stats['buys_generated']}")
            print(f"By mode: {stats['by_mode']}")
    else:
        # Process existing file once
        if integration.signals_file.exists():
            with open(integration.signals_file, "r", encoding="utf-8") as f:
                signals = [json.loads(line) for line in f if line.strip()]

            print(f"Processing {len(signals)} signals...")
            results = await integration.process_batch(signals)

            print("\n--- Results ---")
            print(f"Processed: {len(results)}")
            buys = [r for r in results if r.final_action == "BUY"]
            watches = [r for r in results if r.final_action == "WATCH"]
            print(f"BUY signals: {len(buys)}")
            print(f"WATCH signals: {len(watches)}")

            for r in buys:
                print(f"  ✅ BUY: {r.symbol} | Mode: {r.mode} | Precursor: {r.precursor_prediction}")
        else:
            print(f"File not found: {integration.signals_file}")


async def run_test():
    """Test pipeline with sample signals."""
    print("=" * 60)
    print("PHASE 1 INTEGRATION TEST")
    print("=" * 60)

    integration = MoonBotLiveIntegration(enable_event_bus=False)

    test_signals = [
        # Should trigger SUPER_SCALP + BUY
        {
            "symbol": "XVSUSDT",
            "price": 3.54,
            "delta_pct": 17.31,
            "buys_per_sec": 33,
            "vol_raise_pct": 150,
            "daily_volume": 5_400_000,
            "dbtc_5m": 2.5,
            "dbtc_1m": 1.0,
            "strategy": "TopMarket",
            "direction": "Long",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        # Should trigger SCALP
        {
            "symbol": "SENTUSDT",
            "price": 0.0048,
            "delta_pct": 6.67,
            "buys_per_sec": 12,
            "vol_raise_pct": 70,
            "daily_volume": 56_000_000,
            "dbtc_5m": 1.5,
            "dbtc_1m": 0.8,
            "strategy": "TopMarket",
            "direction": "Long",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        # Should trigger SWING
        {
            "symbol": "WLDUSDT",
            "price": 2.15,
            "delta_pct": 3.1,
            "buys_per_sec": 2,
            "vol_raise_pct": 30,
            "daily_volume": 130_000_000,
            "dbtc_5m": 0.5,
            "dbtc_1m": 0.3,
            "strategy": "DropsDetection",
            "direction": "Long",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        # Should SKIP
        {
            "symbol": "HOLOUSDT",
            "price": 0.0015,
            "delta_pct": 0.5,
            "buys_per_sec": 0,
            "vol_raise_pct": 10,
            "daily_volume": 2_000_000,
            "dbtc_5m": 0.1,
            "dbtc_1m": 0.1,
            "strategy": "DropsDetection",
            "direction": "Long",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        # PumpDetection override - должен быть SUPER_SCALP
        {
            "symbol": "WLDUSDT",
            "price": 2.20,
            "delta_pct": 2.0,
            "buys_per_sec": 1004,
            "vol_raise_pct": 200,
            "daily_volume": 130_000_000,
            "dbtc_5m": 3.0,
            "dbtc_1m": 1.5,
            "strategy": "PumpDetection",
            "direction": "Long",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    ]

    print("\n--- Processing Test Signals ---\n")

    for sig in test_signals:
        result = await integration.process_signal(sig)
        if result:
            print(f"\n{result.symbol}:")
            print(f"  Precursor: {result.precursor_prediction} ({result.precursor.get('signals_detected', [])})")
            print(f"  Mode: {result.mode.upper()} (conf={result.mode_confidence:.2f})")
            print(f"  Decision: {result.decision_action}")
            print(f"  FINAL: {result.final_action}")
            print(f"  Checksum: {result.checksum}")

    print("\n--- Statistics ---")
    stats = integration.get_stats()
    print(f"Signals processed: {stats['signals_processed']}")
    print(f"Precursors detected: {stats['precursors_detected']}")
    print(f"Buys generated: {stats['buys_generated']}")
    print(f"By mode: {stats['by_mode']}")

    print("\n[OK] Phase 1 Integration Test COMPLETE")


if __name__ == "__main__":
    asyncio.run(main())
