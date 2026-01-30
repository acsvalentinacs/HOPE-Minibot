# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 13:22:00 UTC
# Purpose: Hourly AllowList update scheduler
# === END SIGNATURE ===

"""
AllowList Scheduler

Runs as background task to update AllowList every hour.
Can be integrated into production or run standalone.

Usage:
    python scripts/allowlist_scheduler.py --daemon
    python scripts/allowlist_scheduler.py --once
"""

import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.allowlist_manager import DynamicAllowListManager, AllowListConfig

logger = logging.getLogger("ALLOWLIST-SCHEDULER")

# Shutdown flag
_shutdown = False


def handle_shutdown(signum, frame):
    """Handle shutdown signals."""
    global _shutdown
    logger.info(f"Received signal {signum}, shutting down...")
    _shutdown = True


class AllowListScheduler:
    """Scheduler for periodic AllowList updates."""

    def __init__(self, config: AllowListConfig = None):
        self.config = config or AllowListConfig()
        self.manager = DynamicAllowListManager(self.config)
        self.update_count = 0
        self.last_error: str = None

    async def run_once(self) -> bool:
        """Run single update cycle."""
        try:
            logger.info(f"Running AllowList update #{self.update_count + 1}")
            symbols = await self.manager.update(force=True)
            self.update_count += 1
            self.last_error = None

            logger.info(f"Update complete: {len(symbols)} symbols")
            logger.info(f"Top 5: {', '.join(symbols[:5])}")
            return True

        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Update failed: {e}")
            return False

    async def run_daemon(self):
        """Run as daemon with periodic updates."""
        logger.info("=== ALLOWLIST SCHEDULER STARTED ===")
        logger.info(f"Update interval: {self.config.update_interval_hours}h")
        logger.info(f"Min volume: ${self.config.min_volume_usd/1e6:.0f}M")
        logger.info(f"Max symbols: {self.config.max_symbols}")

        # Initial update
        await self.run_once()

        # Main loop
        interval_sec = self.config.update_interval_hours * 3600

        while not _shutdown:
            try:
                # Wait for next update
                logger.info(f"Next update in {interval_sec/60:.0f} minutes")

                # Sleep in small increments to check shutdown flag
                for _ in range(int(interval_sec / 10)):
                    if _shutdown:
                        break
                    await asyncio.sleep(10)

                if _shutdown:
                    break

                # Run update
                await self.run_once()

            except asyncio.CancelledError:
                logger.info("Scheduler cancelled")
                break
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                await asyncio.sleep(60)  # Wait 1 min on error

        logger.info("=== ALLOWLIST SCHEDULER STOPPED ===")

    def status(self) -> dict:
        """Get scheduler status."""
        manager_status = self.manager.status()
        return {
            **manager_status,
            "scheduler_updates": self.update_count,
            "last_error": self.last_error,
            "interval_hours": self.config.update_interval_hours
        }


async def start_scheduler_task(config: AllowListConfig = None) -> AllowListScheduler:
    """Start scheduler as background task (for integration)."""
    scheduler = AllowListScheduler(config)
    asyncio.create_task(scheduler.run_daemon())
    return scheduler


# === CLI ===

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-5s | %(name)-20s | %(message)s",
        datefmt="%H:%M:%S"
    )

    parser = argparse.ArgumentParser(description="AllowList Update Scheduler")
    parser.add_argument("--daemon", action="store_true", help="Run as daemon")
    parser.add_argument("--once", action="store_true", help="Run single update")
    parser.add_argument("--interval", type=float, default=1.0, help="Update interval in hours")
    parser.add_argument("--min-volume", type=float, default=50, help="Min volume in millions USD")
    parser.add_argument("--max-symbols", type=int, default=20, help="Max symbols in list")
    args = parser.parse_args()

    # Setup signal handlers
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # Create config
    config = AllowListConfig(
        update_interval_hours=args.interval,
        min_volume_usd=args.min_volume * 1_000_000,
        max_symbols=args.max_symbols
    )

    scheduler = AllowListScheduler(config)

    if args.daemon:
        print(f"Starting AllowList daemon (Ctrl+C to stop)")
        print(f"  Interval: {args.interval}h")
        print(f"  Min volume: ${args.min_volume}M")
        print(f"  Max symbols: {args.max_symbols}")
        asyncio.run(scheduler.run_daemon())

    elif args.once:
        success = asyncio.run(scheduler.run_once())
        sys.exit(0 if success else 1)

    else:
        parser.print_help()
