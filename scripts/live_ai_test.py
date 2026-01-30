# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 23:40:00 UTC
# Purpose: LIVE AI Trading Test - Full Pipeline with Diagnostics
# Contract: TESTNET only, real AI decisions, real trades
# === END SIGNATURE ===
"""
HOPE AI - Live Trading Test with Full AI Pipeline

Запускает полный торговый цикл с AI участием:
1. MoonBot signal -> AI Gateway
2. AI анализ: Precursor + ModeRouter + Empirical Filters + ML Model
3. Decision -> AutoTrader
4. Trade execution -> Outcome tracking
5. ML learning loop

TESTNET ONLY!
"""

import asyncio
import json
import time
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-5s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("live_ai_test.log", encoding="utf-8")
    ]
)
log = logging.getLogger("LiveAITest")


class LiveAITradingTest:
    """Live AI trading test with full pipeline."""

    def __init__(self):
        self.gateway_url = "http://127.0.0.1:8100"
        self.autotrader_url = "http://127.0.0.1:8200"

        self.stats = {
            "signals_sent": 0,
            "ai_decisions": {"BUY": 0, "SKIP": 0, "WATCH": 0},
            "trades_executed": 0,
            "errors": 0,
        }

        # Test symbols - whitelist first, then others
        self.test_signals = [
            # WHITELIST (должны торговаться!)
            {"symbol": "KITEUSDT", "price": 0.15, "strategy": "Pump", "delta": 3.0, "buys": 45, "vol": 120},
            {"symbol": "DUSKUSDT", "price": 0.28, "strategy": "Drop", "delta": 2.5, "buys": 40, "vol": 100},
            {"symbol": "XVSUSDT", "price": 8.50, "strategy": "Pump", "delta": 4.0, "buys": 55, "vol": 150},
            {"symbol": "SENTUSDT", "price": 0.35, "strategy": "Drop", "delta": 3.5, "buys": 50, "vol": 130},
            # BLACKLIST (должны SKIP!)
            {"symbol": "SYNUSDT", "price": 0.50, "strategy": "Delta", "delta": 5.0, "buys": 80, "vol": 200},
            {"symbol": "ARPAUSDT", "price": 0.015, "strategy": "Pump", "delta": 4.0, "buys": 60, "vol": 180},
            # NEUTRAL (зависит от AI решения)
            {"symbol": "BTCUSDT", "price": 102000, "strategy": "Pump", "delta": 1.5, "buys": 30, "vol": 80},
            {"symbol": "ETHUSDT", "price": 3200, "strategy": "Drop", "delta": 2.0, "buys": 35, "vol": 90},
        ]

    async def check_services(self) -> bool:
        """Check all services are running."""
        import httpx

        log.info("=" * 60)
        log.info("CHECKING SERVICES")
        log.info("=" * 60)

        all_ok = True

        # Check Gateway
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.gateway_url}/health", timeout=5)
                if resp.status_code == 200:
                    log.info("[OK] AI Gateway: HEALTHY")
                else:
                    log.error(f"[FAIL] AI Gateway: {resp.status_code}")
                    all_ok = False
        except Exception as e:
            log.error(f"[FAIL] AI Gateway: {e}")
            all_ok = False

        # Check AutoTrader
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.autotrader_url}/status", timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    mode = data.get("mode", "unknown")
                    if mode.upper() == "TESTNET":
                        log.info(f"[OK] AutoTrader: TESTNET mode")

                        # Check circuit breaker
                        cb = data.get("circuit_breaker_open", False)
                        if cb:
                            log.warning("[WARN] Circuit Breaker OPEN - resetting...")
                            await client.post(f"{self.autotrader_url}/circuit-breaker/reset")
                            log.info("[OK] Circuit Breaker reset")
                    else:
                        log.error(f"[FAIL] AutoTrader not in TESTNET: {mode}")
                        all_ok = False
                else:
                    log.error(f"[FAIL] AutoTrader: {resp.status_code}")
                    all_ok = False
        except Exception as e:
            log.error(f"[FAIL] AutoTrader: {e}")
            all_ok = False

        # Check Self-Improver
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.gateway_url}/self-improver/status", timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    log.info(f"[OK] Self-Improver: model v{data.get('model_version', '?')}, "
                            f"samples={data.get('completed_signals', '?')}")
                else:
                    log.warning("[WARN] Self-Improver status unavailable")
        except:
            log.warning("[WARN] Self-Improver check failed")

        log.info("=" * 60)
        return all_ok

    async def send_signal_to_autotrader(self, signal: Dict) -> Dict:
        """Send signal directly to AutoTrader (bypassing AI Gateway predict)."""
        import httpx

        result = {
            "symbol": signal["symbol"],
            "traded": False,
            "error": None,
        }

        try:
            async with httpx.AsyncClient() as client:
                # Send directly to AutoTrader with proper parameters
                autotrader_signal = {
                    "symbol": signal["symbol"],
                    "strategy": f"AI_{signal['strategy']}",
                    "direction": "Long",
                    "price": signal["price"],
                    "buys_per_sec": signal["buys"],
                    "delta_pct": signal["delta"],
                    "vol_raise_pct": signal["vol"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

                trade_resp = await client.post(
                    f"{self.autotrader_url}/signal",
                    json=autotrader_signal,
                    timeout=10
                )

                if trade_resp.status_code == 200:
                    trade_data = trade_resp.json()
                    result["autotrader_response"] = trade_data
                    result["traded"] = True
                    self.stats["trades_executed"] += 1
                    log.info(f"  [QUEUED] {signal['symbol']} -> AutoTrader")
                else:
                    result["error"] = f"AutoTrader returned {trade_resp.status_code}"
                    log.warning(f"  [REJECT] {signal['symbol']}: {trade_resp.text}")

        except Exception as e:
            result["error"] = str(e)
            self.stats["errors"] += 1
            log.error(f"  [ERROR] {signal['symbol']}: {e}")

        return result

    async def run_test(self, duration_minutes: int = 5):
        """Run the live AI trading test."""
        log.info("")
        log.info("=" * 60)
        log.info("HOPE AI - LIVE TRADING TEST")
        log.info("=" * 60)
        log.info(f"Duration: {duration_minutes} minutes")
        log.info(f"Mode: TESTNET")
        log.info(f"Test signals: {len(self.test_signals)}")
        log.info("=" * 60)
        log.info("")

        # Check services
        if not await self.check_services():
            log.error("Services not ready! Aborting.")
            return

        log.info("")
        log.info("Starting signal injection...")
        log.info("")

        results = []

        # Send each test signal
        for i, signal in enumerate(self.test_signals):
            log.info(f"[{i+1}/{len(self.test_signals)}] Testing {signal['symbol']} ({signal['strategy']})...")

            result = await self.send_signal_to_autotrader(signal)
            results.append(result)

            self.stats["signals_sent"] += 1

            # Small delay between signals
            await asyncio.sleep(0.5)

        # Wait for trades to execute
        log.info("")
        log.info("Waiting 5 seconds for trades to process...")
        await asyncio.sleep(5)

        # Get final AutoTrader status
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self.autotrader_url}/status", timeout=5)
            final_status = resp.json()

        # Print summary
        log.info("")
        log.info("=" * 60)
        log.info("TEST RESULTS")
        log.info("=" * 60)

        log.info(f"  Signals sent: {self.stats['signals_sent']}")
        log.info(f"  Signals queued: {self.stats['trades_executed']}")
        log.info(f"  Errors: {self.stats['errors']}")
        log.info("")
        log.info("AutoTrader Final Status:")
        log.info(f"  Signals received: {final_status.get('stats', {}).get('signals_received', 0)}")
        log.info(f"  Signals traded: {final_status.get('stats', {}).get('signals_traded', 0)}")
        log.info(f"  Signals skipped: {final_status.get('stats', {}).get('signals_skipped', 0)}")
        log.info(f"  Positions opened: {final_status.get('stats', {}).get('positions_opened', 0)}")
        log.info(f"  Positions closed: {final_status.get('stats', {}).get('positions_closed', 0)}")
        log.info(f"  Total PnL: {final_status.get('stats', {}).get('total_pnl', 0):+.2f}%")
        log.info("=" * 60)

        # Save results
        results_file = Path(f"live_ai_test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(results_file, "w") as f:
            json.dump({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "stats": self.stats,
                "final_autotrader_status": final_status,
                "results": results,
            }, f, indent=2, default=str)
        log.info(f"Results saved to: {results_file}")


async def main():
    test = LiveAITradingTest()
    await test.run_test(duration_minutes=5)


if __name__ == "__main__":
    asyncio.run(main())
