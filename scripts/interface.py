# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-03T20:30:00Z
# Purpose: Interface - Telegram bot + HTTP API for HOPE
# Contract: User interaction layer, optional (system works without it)
# === END SIGNATURE ===
#
# === DEPENDENCIES ===
# READS FROM: state/events/journal_*.jsonl, Trading Core status, Guardian status
# WRITES TO: Telegram messages, HTTP responses
# CALLS: Trading Core :8100/status, Guardian :8101/status
# NEXT IN CHAIN: User (via Telegram/Dashboard)
# === END DEPENDENCIES ===
"""
INTERFACE - User Interaction Layer

WHAT THIS IS:
    Combined Telegram bot and HTTP API for:
    - Status monitoring
    - Manual controls
    - Alerts and notifications

WHY SEPARATE PROCESS:
    Interface is OPTIONAL - system works without it.
    If TG bot crashes, trading continues.
    User can restart interface without affecting trading.

FEATURES:
    - /status - Show system status
    - /positions - Show open positions
    - /panic - Emergency close all (requires confirm)
    - /ai - Show AI module status
    - Real-time alerts on fills, closes, errors

ARCHITECTURE:
    +--------------------+
    |     INTERFACE      |
    |                    |
    |   Telegram Bot     |
    |        ↓           |
    |   HTTP API         |
    |        ↓           |
    |   Event Subscriber |
    +--------------------+
           ↑
    Events from Core/Guardian

USAGE:
    python -m scripts.interface
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-5s | INTERFACE | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("INTERFACE")


# === Status Aggregator ===

class StatusAggregator:
    """
    Aggregate status from Trading Core and Guardian.
    """

    def __init__(
        self,
        core_url: str = "http://127.0.0.1:8100",
        guardian_url: str = "http://127.0.0.1:8101",
    ):
        self.core_url = core_url
        self.guardian_url = guardian_url
        self._session = None
        self._cache: Dict[str, Any] = {}
        self._cache_time: float = 0
        self._cache_ttl = 5.0  # 5 second cache

    async def start(self):
        """Initialize HTTP session."""
        try:
            import aiohttp
            self._session = aiohttp.ClientSession()
        except ImportError:
            log.warning("aiohttp not installed")

    async def stop(self):
        """Close session."""
        if self._session:
            await self._session.close()

    async def _fetch(self, url: str) -> Optional[Dict]:
        """Fetch status from URL."""
        if not self._session:
            return None

        try:
            async with self._session.get(url, timeout=5.0) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            log.debug(f"Failed to fetch {url}: {e}")
        return None

    async def get_status(self) -> Dict[str, Any]:
        """Get aggregated status from all services."""
        # Check cache
        if time.time() - self._cache_time < self._cache_ttl:
            return self._cache

        status = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "services": {},
        }

        # Fetch Trading Core status
        core_status = await self._fetch(f"{self.core_url}/status")
        if core_status:
            status["services"]["trading_core"] = {
                "status": "healthy",
                "data": core_status,
            }
        else:
            status["services"]["trading_core"] = {
                "status": "unreachable",
            }

        # Fetch Guardian status
        guardian_status = await self._fetch(f"{self.guardian_url}/status")
        if guardian_status:
            status["services"]["guardian"] = {
                "status": "healthy",
                "data": guardian_status,
            }
        else:
            status["services"]["guardian"] = {
                "status": "unreachable",
            }

        # Overall health
        all_healthy = all(
            s.get("status") == "healthy"
            for s in status["services"].values()
        )
        status["overall"] = "healthy" if all_healthy else "degraded"

        self._cache = status
        self._cache_time = time.time()

        return status


# === Telegram Bot ===

class TelegramInterface:
    """
    Telegram bot interface for HOPE.
    """

    def __init__(self, status_aggregator: StatusAggregator, admin_ids: List[int] = None):
        self.status_aggregator = status_aggregator
        self.admin_ids = admin_ids or []
        self._bot = None
        self._app = None

    async def start(self):
        """Start Telegram bot."""
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            log.warning("TELEGRAM_BOT_TOKEN not set, Telegram disabled")
            return

        try:
            from telegram import Update, BotCommand
            from telegram.ext import Application, CommandHandler, ContextTypes

            self._app = Application.builder().token(token).build()

            # Register commands
            self._app.add_handler(CommandHandler("start", self._cmd_start))
            self._app.add_handler(CommandHandler("status", self._cmd_status))
            self._app.add_handler(CommandHandler("positions", self._cmd_positions))
            self._app.add_handler(CommandHandler("help", self._cmd_help))

            # Set command list
            commands = [
                BotCommand("status", "Show system status"),
                BotCommand("positions", "Show open positions"),
                BotCommand("help", "Show help"),
            ]
            await self._app.bot.set_my_commands(commands)

            # Start polling
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling()

            log.info("Telegram bot started")

        except ImportError:
            log.warning("python-telegram-bot not installed")
        except Exception as e:
            log.error(f"Failed to start Telegram bot: {e}")

    async def stop(self):
        """Stop Telegram bot."""
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    def _is_admin(self, user_id: int) -> bool:
        """Check if user is admin."""
        if not self.admin_ids:
            return True  # No restrictions if no admin list
        return user_id in self.admin_ids

    async def _cmd_start(self, update, context):
        """Handle /start command."""
        await update.message.reply_text(
            "HOPE Trading Bot Interface\n\n"
            "Commands:\n"
            "/status - System status\n"
            "/positions - Open positions\n"
            "/help - Show help"
        )

    async def _cmd_status(self, update, context):
        """Handle /status command."""
        status = await self.status_aggregator.get_status()

        # Format status message
        msg = "HOPE System Status\n"
        msg += "=" * 30 + "\n\n"

        overall = status.get("overall", "unknown")
        emoji = "🟢" if overall == "healthy" else "🟡" if overall == "degraded" else "🔴"
        msg += f"Overall: {emoji} {overall.upper()}\n\n"

        for service, info in status.get("services", {}).items():
            s_status = info.get("status", "unknown")
            s_emoji = "🟢" if s_status == "healthy" else "🔴"
            msg += f"{s_emoji} {service}: {s_status}\n"

            if info.get("data"):
                data = info["data"]
                if "active_positions" in data:
                    msg += f"   Positions: {data['active_positions']}\n"
                if "mode" in data:
                    msg += f"   Mode: {data['mode']}\n"

        msg += f"\nUpdated: {status.get('timestamp', 'N/A')}"

        await update.message.reply_text(msg)

    async def _cmd_positions(self, update, context):
        """Handle /positions command."""
        positions_file = PROJECT_ROOT / "state" / "positions" / "active.json"

        if not positions_file.exists():
            await update.message.reply_text("No positions file found")
            return

        try:
            data = json.loads(positions_file.read_text(encoding="utf-8"))
            positions = data.get("positions", [])

            if not positions:
                await update.message.reply_text("No open positions")
                return

            msg = f"Open Positions ({len(positions)})\n"
            msg += "=" * 30 + "\n\n"

            for pos in positions:
                msg += f"📊 {pos.get('symbol')}\n"
                msg += f"   Side: {pos.get('side')}\n"
                msg += f"   Entry: ${pos.get('entry_price', 0):.4f}\n"
                msg += f"   Qty: {pos.get('quantity', 0):.6f}\n"
                msg += f"   Opened: {pos.get('opened_at', 'N/A')[:19]}\n\n"

            await update.message.reply_text(msg)

        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def _cmd_help(self, update, context):
        """Handle /help command."""
        msg = (
            "HOPE Interface Commands\n\n"
            "/status - Show system status\n"
            "/positions - Show open positions\n"
            "/help - Show this help\n\n"
            "System: HOPE Trading Bot\n"
            "Mode: Production"
        )
        await update.message.reply_text(msg)

    async def send_alert(self, message: str):
        """Send alert to all admins."""
        if not self._app:
            return

        for admin_id in self.admin_ids:
            try:
                await self._app.bot.send_message(chat_id=admin_id, text=message)
            except Exception as e:
                log.error(f"Failed to send alert to {admin_id}: {e}")


# === Event Listener ===

class EventListener:
    """
    Listen to events and send alerts.
    """

    def __init__(self, telegram: TelegramInterface):
        self.telegram = telegram
        self._running = False

    async def start(self):
        """Start listening for events."""
        self._running = True

        try:
            from core.events.transport import EventTransport

            transport = EventTransport(source_name="interface")

            log.info("EventListener started")

            for event in transport.subscribe(["FILL", "CLOSE", "PANIC", "STOPLOSS_FAILURE"]):
                if not self._running:
                    break

                event_type = event.event_type
                payload = event.payload

                # Format alert message
                if event_type == "FILL":
                    msg = (
                        f"🔔 FILL\n"
                        f"Symbol: {payload.get('symbol')}\n"
                        f"Side: {payload.get('side')}\n"
                        f"Price: ${payload.get('price', 0):.4f}\n"
                        f"Qty: {payload.get('quantity', 0):.6f}"
                    )
                elif event_type == "CLOSE":
                    msg = (
                        f"📤 POSITION CLOSED\n"
                        f"Symbol: {payload.get('symbol')}\n"
                        f"Reason: {payload.get('reason')}\n"
                        f"PnL: {payload.get('pnl_pct', 0):.2f}%"
                    )
                elif event_type == "PANIC":
                    msg = f"🚨 PANIC\n{payload.get('reason', 'Unknown')}"
                elif event_type == "STOPLOSS_FAILURE":
                    msg = (
                        f"⚠️ STOP-LOSS FAILURE\n"
                        f"Symbol: {payload.get('symbol')}\n"
                        f"Error: {payload.get('error')}"
                    )
                else:
                    continue

                await self.telegram.send_alert(msg)

        except Exception as e:
            log.error(f"EventListener error: {e}")

    def stop(self):
        """Stop listening."""
        self._running = False


# === HTTP API ===

class HTTPInterface:
    """
    HTTP API for Interface.
    """

    def __init__(self, status_aggregator: StatusAggregator):
        self.status_aggregator = status_aggregator
        self.app = None

    def create_app(self):
        """Create FastAPI app."""
        try:
            from fastapi import FastAPI
            from fastapi.responses import JSONResponse

            app = FastAPI(title="HOPE Interface", version="1.0")

            @app.get("/health")
            async def health():
                return {"status": "healthy", "service": "interface"}

            @app.get("/status")
            async def status():
                return await self.status_aggregator.get_status()

            @app.get("/positions")
            async def positions():
                positions_file = PROJECT_ROOT / "state" / "positions" / "active.json"
                if not positions_file.exists():
                    return {"positions": []}
                data = json.loads(positions_file.read_text(encoding="utf-8"))
                return data

            self.app = app
            return app

        except ImportError:
            log.warning("FastAPI not installed")
            return None


# === Main Interface ===

class Interface:
    """
    Main Interface class - combines Telegram and HTTP.
    """

    def __init__(self):
        self.status_aggregator = StatusAggregator()
        self.telegram = TelegramInterface(
            self.status_aggregator,
            admin_ids=[int(os.getenv("TELEGRAM_ADMIN_ID", "0"))],
        )
        self.event_listener = EventListener(self.telegram)
        self.http = HTTPInterface(self.status_aggregator)
        self._running = False

    async def start(self):
        """Start Interface."""
        log.info("=" * 60)
        log.info("  INTERFACE - STARTING")
        log.info("=" * 60)

        self._running = True

        # Start components
        await self.status_aggregator.start()
        await self.telegram.start()

        # Start event listener in background
        asyncio.create_task(self.event_listener.start())

        # Start HTTP server
        app = self.http.create_app()
        if app:
            try:
                import uvicorn
                config = uvicorn.Config(app, host="127.0.0.1", port=8102, log_level="warning")
                server = uvicorn.Server(config)
                asyncio.create_task(server.serve())
                log.info("HTTP API started on :8102")
            except ImportError:
                pass

        log.info("Interface started")

    async def run_forever(self):
        """Run until shutdown."""
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def stop(self):
        """Stop Interface."""
        log.info("Interface stopping...")
        self._running = False
        self.event_listener.stop()
        await self.telegram.stop()
        await self.status_aggregator.stop()
        log.info("Interface stopped")


# === Main ===

async def main():
    """Main entry point."""
    interface = Interface()

    # Start interface
    await interface.start()

    # Run forever
    await interface.run_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HOPE Interface")
    args = parser.parse_args()

    asyncio.run(main())
