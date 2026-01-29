# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 10:25:00 UTC
# Purpose: Test script for Self-Improving Loop module
# === END SIGNATURE ===
"""
Test Self-Improving Loop.

This script:
1. Loads existing MoonBot signals
2. Feeds them to the self-improver
3. Simulates price updates
4. Tracks outcomes
5. Shows training progress

Usage:
    cd C:/Users/kirillDev/Desktop/TradingBot/minibot
    python scripts/test_self_improver.py
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_gateway.base_module import ModuleConfig
from ai_gateway.modules.self_improver import (
    SelfImprovingLoop,
    OutcomeTracker,
    ModelRegistry,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def load_moonbot_signals(signals_dir: Path) -> list:
    """Load MoonBot signals from JSONL files."""
    signals = []

    for jsonl_file in signals_dir.glob("*.jsonl"):
        logger.info(f"Loading signals from {jsonl_file.name}")

        with open(jsonl_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    signal = json.loads(line)
                    # Skip header lines
                    if "timestamp" in signal and "symbol" in signal:
                        signals.append(signal)
                except json.JSONDecodeError:
                    continue

    logger.info(f"Loaded {len(signals)} signals total")
    return signals


async def test_self_improving_loop():
    """Test the Self-Improving Loop module."""

    logger.info("=" * 60)
    logger.info("SELF-IMPROVING LOOP TEST")
    logger.info("=" * 60)

    # Setup paths
    project_root = Path(__file__).parent.parent
    signals_dir = project_root / "data" / "moonbot_signals"
    state_dir = project_root / "state" / "ai"

    # Load signals
    signals = load_moonbot_signals(signals_dir)
    if not signals:
        logger.error("No signals found!")
        return

    # Create module config
    config = ModuleConfig(
        module_id="self_improver",
        interval_seconds=60,
        enabled=True,
        extra={
            "retrain_threshold": 20,   # Retrain after 20 outcomes
            "min_train_samples": 10,   # Minimum 10 samples
            "max_consecutive_losses": 5,
            "auto_ab_test": False,     # Disable A/B for testing
            "horizon": "5m",
        }
    )

    # Initialize loop
    loop = SelfImprovingLoop(config, state_dir=state_dir)

    # Start module
    logger.info("\n[1] Starting Self-Improving Loop...")
    await loop.start()

    # Process signals
    logger.info(f"\n[2] Processing {len(signals)} signals...")
    predictions = []

    for i, signal in enumerate(signals[:30], 1):  # Process first 30
        prediction = loop.predict(signal)
        predictions.append(prediction)

        symbol = signal.get("symbol", "UNKNOWN")
        win_prob = prediction.get("win_probability", 0)
        rec = prediction.get("recommendation", "SKIP")
        version = prediction.get("model_version", "N/A")

        logger.info(
            f"  [{i:02d}] {symbol:12s} → "
            f"P(WIN)={win_prob:.2%}, Rec={rec:5s}, Model=v{version}"
        )

    # Simulate price updates
    logger.info("\n[3] Simulating price updates...")

    # Create fake price data (simulate some wins/losses)
    symbols = list(loop.outcome_tracker.active_symbols)
    logger.info(f"  Tracking {len(symbols)} symbols: {', '.join(symbols[:5])}...")

    for cycle in range(5):
        # Simulate price movement (random-ish)
        prices = {}
        for symbol in symbols:
            # Get entry price from first signal
            for sig in signals:
                if sig.get("symbol", "").upper().replace("USDT", "") == symbol.replace("USDT", ""):
                    base_price = sig.get("price", 1.0)
                    # Add some movement (±2%)
                    movement = 1.0 + ((hash(f"{symbol}{cycle}") % 100) - 50) / 2500
                    prices[symbol] = base_price * movement
                    break

        if prices:
            completed = loop.update_prices(prices)
            logger.info(f"  Cycle {cycle + 1}: updated {len(prices)} prices, {completed} completed")

    # Show stats
    logger.info("\n[4] Current Statistics:")
    stats = loop.outcome_tracker.get_stats()
    for key, value in stats.items():
        if isinstance(value, float):
            logger.info(f"  {key}: {value:.4f}")
        else:
            logger.info(f"  {key}: {value}")

    # Show model info
    logger.info("\n[5] Model Info:")
    info = loop.get_info()
    for key, value in info.items():
        logger.info(f"  {key}: {value}")

    # Run one iteration
    logger.info("\n[6] Running one iteration...")
    artifact = await loop.run_once()
    if artifact:
        logger.info(f"  Artifact ID: {artifact.artifact_id}")
        logger.info(f"  Status: {artifact.status_message}")

    # Stop module
    logger.info("\n[7] Stopping module...")
    await loop.stop()

    logger.info("\n" + "=" * 60)
    logger.info("TEST COMPLETE")
    logger.info("=" * 60)


async def test_outcome_tracker():
    """Test outcome tracker separately."""

    logger.info("=" * 60)
    logger.info("OUTCOME TRACKER TEST")
    logger.info("=" * 60)

    project_root = Path(__file__).parent.parent
    state_dir = project_root / "state" / "ai" / "outcomes"

    tracker = OutcomeTracker(state_dir=state_dir)

    # Register a test signal
    test_signal = {
        "symbol": "BTCUSDT",
        "price": 42000.0,
        "direction": "Long",
        "delta_pct": 2.5,
        "signal_type": "pump",
    }

    signal_id = tracker.register_signal(test_signal)
    logger.info(f"Registered signal: {signal_id}")

    # Simulate price updates
    for i in range(10):
        price = 42000.0 + (i * 50)  # Price goes up
        completed = tracker.update_prices({"BTCUSDT": price})
        logger.info(f"  Price {price:.2f}, completed: {completed}")

    # Show stats
    stats = tracker.get_stats()
    logger.info(f"Stats: {stats}")


async def test_model_registry():
    """Test model registry separately."""

    logger.info("=" * 60)
    logger.info("MODEL REGISTRY TEST")
    logger.info("=" * 60)

    project_root = Path(__file__).parent.parent
    models_dir = project_root / "state" / "ai" / "models"

    registry = ModelRegistry(models_dir=models_dir)

    # Show current state
    stats = registry.get_stats()
    logger.info(f"Registry stats: {stats}")

    # List all versions
    versions = registry.get_all_versions()
    for v in versions:
        logger.info(f"  v{v.version}: samples={v.trained_samples}, active={v.is_active}")


async def main():
    """Run tests."""
    import argparse

    parser = argparse.ArgumentParser(description="Test Self-Improving Loop")
    parser.add_argument(
        "--test",
        choices=["loop", "tracker", "registry", "all"],
        default="loop",
        help="Which test to run",
    )

    args = parser.parse_args()

    if args.test == "loop" or args.test == "all":
        await test_self_improving_loop()

    if args.test == "tracker" or args.test == "all":
        await test_outcome_tracker()

    if args.test == "registry" or args.test == "all":
        await test_model_registry()


if __name__ == "__main__":
    asyncio.run(main())
