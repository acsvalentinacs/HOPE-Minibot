# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 03:40:00 UTC
# Purpose: Start script for HOPE Production System
# Contract: Coordinates Gateway + Engine + Controller
# === END SIGNATURE ===
"""
START HOPE PRODUCTION

Запускает полную production систему:
1. Policy preflight check
2. Gateway (price feed)
3. Production Engine (trading)
4. Monitoring loop

Использование:
  python start_hope_production.py --mode DRY       # Симуляция
  python start_hope_production.py --mode TESTNET   # Тестовая сеть
  python start_hope_production.py --mode LIVE --confirm  # РЕАЛЬНЫЕ ДЕНЬГИ
"""

import argparse
import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("HOPE-START")


def load_env():
    """Load environment from secrets."""
    env_path = Path("C:/secrets/hope.env")
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path)
            logger.info(f"Loaded env from {env_path}")
        except ImportError:
            # Manual load
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    key, _, value = line.partition("=")
                    os.environ[key.strip()] = value.strip()
            logger.info("Loaded env manually")


def run_preflight(mode: str) -> bool:
    """Run policy preflight check."""
    logger.info("Running preflight checks...")

    try:
        from scripts.gates.policy_preflight import run_preflight as preflight
        passed, results = preflight(mode)

        errors = [r for r in results if not r.passed and r.severity == "ERROR"]
        if errors:
            logger.error(f"Preflight FAILED: {len(errors)} errors")
            for e in errors:
                logger.error(f"  - {e.name}: {e.message}")
            return False

        logger.info("Preflight PASSED")
        return True

    except Exception as e:
        logger.error(f"Preflight error: {e}")
        return False


def check_gateway() -> bool:
    """Check if Gateway is running."""
    try:
        import urllib.request
        resp = urllib.request.urlopen("http://127.0.0.1:8100/health", timeout=5)
        return resp.status == 200
    except Exception:
        return False


def start_gateway():
    """Start Gateway if not running."""
    if check_gateway():
        logger.info("Gateway already running")
        return None

    logger.info("Starting Gateway...")

    # Start gateway in background
    process = subprocess.Popen(
        [sys.executable, "-m", "ai_gateway.server"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    )

    # Wait for startup
    for i in range(10):
        time.sleep(1)
        if check_gateway():
            logger.info(f"Gateway started (PID {process.pid})")
            return process

    logger.error("Gateway failed to start")
    return None


async def run_production_engine(mode: str, position_size: float):
    """Run the production engine."""
    from scripts.hope_production_engine import HopeProductionEngine, TradingMode

    trading_mode = TradingMode[mode]
    engine = HopeProductionEngine(trading_mode, position_size)
    engine.running = True

    logger.info(f"Production Engine started in {mode} mode")
    logger.info(f"Session: {engine.oracle.get_current_session().value}")

    # Main loop
    cycle = 0
    while engine.running:
        try:
            await engine.run_cycle()
            cycle += 1

            # Status every 60 cycles
            if cycle % 60 == 0:
                status = engine.get_status()
                logger.info(
                    f"Cycle {cycle} | Session: {status['session']} | "
                    f"Positions: {status['open_positions']} | "
                    f"Traded: {status['stats']['signals_traded']}"
                )

            await asyncio.sleep(1.0)

        except KeyboardInterrupt:
            logger.info("Shutdown requested")
            engine.running = False
            break
        except Exception as e:
            logger.error(f"Cycle error: {e}")
            await asyncio.sleep(5.0)


def main():
    parser = argparse.ArgumentParser(description="Start HOPE Production System")
    parser.add_argument("--mode", type=str, default="DRY", choices=["DRY", "TESTNET", "LIVE"])
    parser.add_argument("--confirm", action="store_true", help="Confirm LIVE mode")
    parser.add_argument("--position-size", type=float, default=10.0, help="Position size USD")
    parser.add_argument("--skip-preflight", action="store_true", help="Skip preflight checks")
    parser.add_argument("--skip-gateway", action="store_true", help="Skip gateway start")
    args = parser.parse_args()

    print("=" * 60)
    print("HOPE PRODUCTION SYSTEM")
    print("=" * 60)
    print(f"Mode: {args.mode}")
    print(f"Position size: ${args.position_size}")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    # Safety check for LIVE
    if args.mode == "LIVE":
        if not args.confirm:
            print("\nERROR: LIVE mode requires --confirm flag")
            print("This will trade with REAL MONEY!")
            sys.exit(1)

        confirm = input("\nType 'I UNDERSTAND LIVE TRADING' to confirm: ")
        if confirm != "I UNDERSTAND LIVE TRADING":
            print("Cancelled.")
            sys.exit(1)

        print("\n" + "!" * 60)
        print("WARNING: LIVE MODE - REAL MONEY AT RISK")
        print("!" * 60)

    # Load environment
    load_env()

    # Preflight
    if not args.skip_preflight:
        if not run_preflight(args.mode):
            print("\nPreflight FAILED - fix issues before starting")
            sys.exit(1)

    # Gateway
    gateway_process = None
    if not args.skip_gateway:
        gateway_process = start_gateway()
        if not check_gateway():
            print("\nGateway not available - cannot start")
            sys.exit(1)

    # Run engine
    try:
        asyncio.run(run_production_engine(args.mode, args.position_size))
    except KeyboardInterrupt:
        print("\nShutdown...")
    finally:
        # Cleanup
        if gateway_process:
            gateway_process.terminate()
            print("Gateway stopped")


if __name__ == "__main__":
    main()
