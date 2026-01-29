# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 17:00:00 UTC
# Purpose: Overnight AI-Gateway test with model training
# === END SIGNATURE ===
"""
Overnight AI-Gateway Test & Training Script.

Runs for specified hours, exercises all AI modules:
- Sentiment: Fetches RSS, analyzes with Claude API
- Regime: Classifies market from Binance klines
- Anomaly: Collects data, trains IsolationForest
- Doctor: Monitors mock strategy health

Usage:
    python scripts/overnight_ai_test.py --hours 2
    python scripts/overnight_ai_test.py --hours 6 --no-sentiment  # without Claude API

Requirements:
    - ANTHROPIC_API_KEY (for Sentiment/Doctor)
    - Internet connection (Binance, RSS feeds)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TEST] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# === Configuration ===

GATEWAY_URL = "http://127.0.0.1:8100"
STATE_DIR = Path("state/ai")
TRAINING_DATA_FILE = STATE_DIR / "training_data.jsonl"

# Test intervals (seconds)
INTERVALS = {
    "sentiment": 900,    # 15 min (RSS + Claude)
    "regime": 300,       # 5 min (Binance klines)
    "anomaly": 60,       # 1 min (market scan)
    "health_check": 120, # 2 min
    "data_collect": 30,  # 30 sec (for training)
}

# Binance symbols for data collection
SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]


class OvernightTester:
    """
    Overnight test runner for AI-Gateway.

    Features:
    - Periodic module execution
    - Market data collection for Anomaly training
    - Health monitoring
    - Statistics tracking
    """

    def __init__(
        self,
        hours: float = 2.0,
        enable_sentiment: bool = True,
        gateway_url: str = GATEWAY_URL,
    ):
        self.hours = hours
        self.enable_sentiment = enable_sentiment
        self.gateway_url = gateway_url
        self.end_time = time.time() + (hours * 3600)

        self._stop_event = asyncio.Event()
        self._client: Optional[httpx.AsyncClient] = None

        # Statistics
        self.stats = {
            "start_time": datetime.now(timezone.utc).isoformat(),
            "sentiment_runs": 0,
            "sentiment_success": 0,
            "regime_runs": 0,
            "regime_success": 0,
            "anomaly_runs": 0,
            "anomaly_success": 0,
            "data_points_collected": 0,
            "errors": [],
        }

        # Training data buffer
        self._training_buffer: List[Dict[str, Any]] = []

        # Ensure state dir exists
        STATE_DIR.mkdir(parents=True, exist_ok=True)

    async def start(self) -> None:
        """Start the overnight test."""
        logger.info("=" * 60)
        logger.info("OVERNIGHT AI-GATEWAY TEST STARTING")
        logger.info(f"Duration: {self.hours} hours")
        logger.info(f"End time: {datetime.fromtimestamp(self.end_time)}")
        logger.info(f"Sentiment enabled: {self.enable_sentiment}")
        logger.info("=" * 60)

        # Check prerequisites
        if not await self._check_prerequisites():
            logger.error("Prerequisites check failed, aborting")
            return

        # Setup signal handlers
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                asyncio.get_event_loop().add_signal_handler(
                    sig, lambda: self._stop_event.set()
                )
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

        async with httpx.AsyncClient(timeout=60.0) as client:
            self._client = client

            # Enable modules
            await self._enable_modules()

            # Run tasks concurrently
            tasks = [
                asyncio.create_task(self._run_regime_loop()),
                asyncio.create_task(self._run_anomaly_loop()),
                asyncio.create_task(self._run_data_collection_loop()),
                asyncio.create_task(self._run_health_check_loop()),
            ]

            if self.enable_sentiment:
                tasks.append(asyncio.create_task(self._run_sentiment_loop()))

            # Wait for completion or stop
            try:
                while not self._stop_event.is_set() and time.time() < self.end_time:
                    await asyncio.sleep(10)
                    self._print_progress()
            except asyncio.CancelledError:
                pass
            finally:
                self._stop_event.set()

                # Cancel all tasks
                for task in tasks:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        # Final report
        await self._finalize()

    async def _check_prerequisites(self) -> bool:
        """Check all prerequisites are met."""
        errors = []

        # Check Anthropic API key
        if self.enable_sentiment:
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key or not api_key.startswith("sk-ant-"):
                errors.append("ANTHROPIC_API_KEY not set or invalid")
                logger.warning("Sentiment/Doctor will be disabled")
                self.enable_sentiment = False

        # Check Gateway is running
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.gateway_url}/health")
                if resp.status_code != 200:
                    errors.append(f"Gateway health check failed: {resp.status_code}")
        except Exception as e:
            errors.append(f"Gateway not reachable: {e}")
            logger.error("Start Gateway first: python -m ai_gateway --port 8100")
            return False

        # Check Binance connectivity
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get("https://api.binance.com/api/v3/ping")
                if resp.status_code != 200:
                    errors.append("Binance API not reachable")
        except Exception as e:
            errors.append(f"Binance connectivity failed: {e}")

        for error in errors:
            logger.warning(f"PREREQ: {error}")

        return len([e for e in errors if "Gateway" in e]) == 0

    async def _enable_modules(self) -> None:
        """Enable AI modules via Gateway API."""
        modules = ["regime", "anomaly"]
        if self.enable_sentiment:
            modules.extend(["sentiment", "doctor"])

        for module in modules:
            try:
                resp = await self._client.post(
                    f"{self.gateway_url}/modules/{module}/enable"
                )
                if resp.status_code == 200:
                    logger.info(f"Module {module} enabled")
                else:
                    logger.warning(f"Failed to enable {module}: {resp.status_code}")
            except Exception as e:
                logger.error(f"Error enabling {module}: {e}")

    async def _run_sentiment_loop(self) -> None:
        """Run sentiment analysis periodically."""
        await asyncio.sleep(5)  # Initial delay

        while not self._stop_event.is_set():
            try:
                self.stats["sentiment_runs"] += 1
                logger.info("Running sentiment analysis...")

                resp = await self._client.post(
                    f"{self.gateway_url}/modules/sentiment/run-now"
                )

                if resp.status_code == 200:
                    self.stats["sentiment_success"] += 1
                    data = resp.json()
                    logger.info(f"Sentiment: {data}")
                else:
                    self._log_error(f"Sentiment failed: {resp.status_code}")

            except Exception as e:
                self._log_error(f"Sentiment error: {e}")

            # Wait for next interval
            await self._wait_or_stop(INTERVALS["sentiment"])

    async def _run_regime_loop(self) -> None:
        """Run regime detection periodically."""
        await asyncio.sleep(10)  # Initial delay

        while not self._stop_event.is_set():
            try:
                self.stats["regime_runs"] += 1
                logger.info("Running regime detection...")

                resp = await self._client.post(
                    f"{self.gateway_url}/modules/regime/run-now"
                )

                if resp.status_code == 200:
                    self.stats["regime_success"] += 1
                    data = resp.json()
                    logger.info(f"Regime: {data}")
                else:
                    self._log_error(f"Regime failed: {resp.status_code}")

            except Exception as e:
                self._log_error(f"Regime error: {e}")

            await self._wait_or_stop(INTERVALS["regime"])

    async def _run_anomaly_loop(self) -> None:
        """Run anomaly detection periodically."""
        await asyncio.sleep(15)  # Initial delay

        while not self._stop_event.is_set():
            try:
                self.stats["anomaly_runs"] += 1
                logger.info("Running anomaly scan...")

                resp = await self._client.post(
                    f"{self.gateway_url}/modules/anomaly/run-now"
                )

                if resp.status_code == 200:
                    self.stats["anomaly_success"] += 1
                    data = resp.json()
                    logger.info(f"Anomaly: {data}")
                else:
                    self._log_error(f"Anomaly failed: {resp.status_code}")

            except Exception as e:
                self._log_error(f"Anomaly error: {e}")

            await self._wait_or_stop(INTERVALS["anomaly"])

    async def _run_data_collection_loop(self) -> None:
        """Collect market data for Anomaly model training."""
        await asyncio.sleep(3)  # Initial delay

        while not self._stop_event.is_set():
            try:
                # Fetch ticker data from Binance
                resp = await self._client.get(
                    "https://api.binance.com/api/v3/ticker/24hr"
                )

                if resp.status_code == 200:
                    tickers = resp.json()

                    # Filter USDT pairs
                    usdt_tickers = [
                        t for t in tickers
                        if t.get("symbol", "").endswith("USDT")
                    ][:50]

                    # Extract features for training
                    data_point = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "btc_price": next(
                            (float(t["lastPrice"]) for t in usdt_tickers
                             if t["symbol"] == "BTCUSDT"), 0
                        ),
                        "btc_volume": next(
                            (float(t["quoteVolume"]) for t in usdt_tickers
                             if t["symbol"] == "BTCUSDT"), 0
                        ),
                        "btc_change_24h": next(
                            (float(t["priceChangePercent"]) for t in usdt_tickers
                             if t["symbol"] == "BTCUSDT"), 0
                        ),
                        "top_gainers": [
                            t["symbol"] for t in sorted(
                                usdt_tickers,
                                key=lambda x: float(x.get("priceChangePercent", 0)),
                                reverse=True
                            )[:5]
                        ],
                        "top_losers": [
                            t["symbol"] for t in sorted(
                                usdt_tickers,
                                key=lambda x: float(x.get("priceChangePercent", 0))
                            )[:5]
                        ],
                        "avg_volume_ratio": sum(
                            float(t.get("quoteVolume", 0)) / max(float(t.get("prevClosePrice", 1)), 0.01)
                            for t in usdt_tickers[:20]
                        ) / 20 if usdt_tickers else 0,
                    }

                    # Save to buffer
                    self._training_buffer.append(data_point)
                    self.stats["data_points_collected"] += 1

                    # Flush buffer periodically
                    if len(self._training_buffer) >= 10:
                        await self._flush_training_data()

                    logger.debug(f"Data collected: BTC=${data_point['btc_price']:.2f}")

            except Exception as e:
                self._log_error(f"Data collection error: {e}")

            await self._wait_or_stop(INTERVALS["data_collect"])

    async def _run_health_check_loop(self) -> None:
        """Monitor Gateway health."""
        while not self._stop_event.is_set():
            try:
                # Gateway health
                resp = await self._client.get(f"{self.gateway_url}/health")
                if resp.status_code != 200:
                    self._log_error(f"Gateway unhealthy: {resp.status_code}")

                # Diagnostics
                resp = await self._client.get(f"{self.gateway_url}/diagnostics")
                if resp.status_code == 200:
                    diag = resp.json()
                    status = diag.get("overall_status", "unknown")
                    if status != "healthy":
                        logger.warning(f"Gateway status: {status}")
                        for err in diag.get("errors", []):
                            logger.warning(f"  - {err}")

            except Exception as e:
                self._log_error(f"Health check error: {e}")

            await self._wait_or_stop(INTERVALS["health_check"])

    async def _flush_training_data(self) -> None:
        """Flush training data buffer to file."""
        if not self._training_buffer:
            return

        try:
            with open(TRAINING_DATA_FILE, "a", encoding="utf-8") as f:
                for point in self._training_buffer:
                    f.write(json.dumps(point, ensure_ascii=False) + "\n")

            logger.info(f"Flushed {len(self._training_buffer)} data points to {TRAINING_DATA_FILE}")
            self._training_buffer.clear()

        except Exception as e:
            self._log_error(f"Failed to flush training data: {e}")

    async def _wait_or_stop(self, seconds: float) -> None:
        """Wait for interval or stop event."""
        try:
            await asyncio.wait_for(
                self._stop_event.wait(),
                timeout=seconds
            )
        except asyncio.TimeoutError:
            pass

    def _log_error(self, error: str) -> None:
        """Log and track error."""
        logger.error(error)
        self.stats["errors"].append({
            "time": datetime.now(timezone.utc).isoformat(),
            "error": error,
        })

    def _print_progress(self) -> None:
        """Print progress update."""
        elapsed = time.time() - (self.end_time - self.hours * 3600)
        remaining = max(0, self.end_time - time.time())

        elapsed_min = int(elapsed / 60)
        remaining_min = int(remaining / 60)

        success_rate = lambda runs, success: (
            f"{success}/{runs} ({100*success/runs:.0f}%)" if runs > 0 else "0/0"
        )

        logger.info(
            f"Progress: {elapsed_min}m elapsed, {remaining_min}m remaining | "
            f"Regime: {success_rate(self.stats['regime_runs'], self.stats['regime_success'])} | "
            f"Anomaly: {success_rate(self.stats['anomaly_runs'], self.stats['anomaly_success'])} | "
            f"Data: {self.stats['data_points_collected']} points"
        )

    async def _finalize(self) -> None:
        """Finalize test and generate report."""
        # Flush remaining training data
        await self._flush_training_data()

        # Calculate final stats
        self.stats["end_time"] = datetime.now(timezone.utc).isoformat()
        self.stats["duration_hours"] = self.hours
        self.stats["error_count"] = len(self.stats["errors"])

        # Save report
        report_file = STATE_DIR / f"test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(self.stats, f, indent=2, ensure_ascii=False)

        # Print summary
        logger.info("=" * 60)
        logger.info("OVERNIGHT TEST COMPLETE")
        logger.info("=" * 60)
        logger.info(f"Duration: {self.hours} hours")
        logger.info(f"Regime runs: {self.stats['regime_runs']} (success: {self.stats['regime_success']})")
        logger.info(f"Anomaly runs: {self.stats['anomaly_runs']} (success: {self.stats['anomaly_success']})")
        if self.enable_sentiment:
            logger.info(f"Sentiment runs: {self.stats['sentiment_runs']} (success: {self.stats['sentiment_success']})")
        logger.info(f"Data points collected: {self.stats['data_points_collected']}")
        logger.info(f"Errors: {self.stats['error_count']}")
        logger.info(f"Training data: {TRAINING_DATA_FILE}")
        logger.info(f"Report saved: {report_file}")
        logger.info("=" * 60)

        # Train anomaly model if we have data
        if self.stats["data_points_collected"] >= 50:
            logger.info("Training Anomaly model with collected data...")
            await self._train_anomaly_model()

    async def _train_anomaly_model(self) -> None:
        """Train IsolationForest on collected data."""
        try:
            # Read training data
            if not TRAINING_DATA_FILE.exists():
                logger.warning("No training data file found")
                return

            data_points = []
            with open(TRAINING_DATA_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        data_points.append(json.loads(line))

            if len(data_points) < 50:
                logger.warning(f"Not enough data points ({len(data_points)}), need 50+")
                return

            # Extract features
            import numpy as np

            features = []
            for dp in data_points:
                features.append([
                    dp.get("btc_price", 0),
                    dp.get("btc_volume", 0) / 1e9,  # Scale to billions
                    dp.get("btc_change_24h", 0),
                    dp.get("avg_volume_ratio", 0),
                ])

            X = np.array(features)

            # Train IsolationForest
            from sklearn.ensemble import IsolationForest
            import joblib

            model = IsolationForest(
                contamination=0.1,
                random_state=42,
                n_estimators=100,
            )
            model.fit(X)

            # Save model
            model_path = STATE_DIR / "anomaly_model.joblib"
            joblib.dump(model, model_path)

            logger.info(f"Anomaly model trained on {len(data_points)} points")
            logger.info(f"Model saved: {model_path}")

        except ImportError as e:
            logger.warning(f"sklearn/joblib not installed: {e}")
        except Exception as e:
            logger.error(f"Model training failed: {e}")


async def main():
    parser = argparse.ArgumentParser(
        description="Overnight AI-Gateway Test & Training"
    )
    parser.add_argument(
        "--hours",
        type=float,
        default=2.0,
        help="Test duration in hours (default: 2)"
    )
    parser.add_argument(
        "--no-sentiment",
        action="store_true",
        help="Disable Sentiment module (no Claude API needed)"
    )
    parser.add_argument(
        "--gateway-url",
        default=GATEWAY_URL,
        help=f"Gateway URL (default: {GATEWAY_URL})"
    )

    args = parser.parse_args()

    tester = OvernightTester(
        hours=args.hours,
        enable_sentiment=not args.no_sentiment,
        gateway_url=args.gateway_url,
    )

    await tester.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Test interrupted by user")
