#!/usr/bin/env python3
# === AI SIGNATURE ===
# Module: hope_core/main.py
# Created by: Claude (opus-4.5)
# Created at: 2026-02-04 12:00:00 UTC
# Purpose: Main entry point for HOPE Core v2.0 on VPS
# === END SIGNATURE ===
"""
HOPE Core v2.0 - Main Entry Point

Configures paths and starts the trading core with HTTP API.

Usage:
    python -m hope_core.main --mode LIVE --port 8200
    python hope_core/main.py --mode DRY --port 8300
"""

import sys
import os
from pathlib import Path

# Configure paths BEFORE any imports
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent

# Add paths
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(SCRIPT_DIR))

# Set PYTHONPATH
os.environ["PYTHONPATH"] = f"{PROJECT_ROOT}:{PROJECT_ROOT / 'scripts'}:{SCRIPT_DIR}"

import asyncio
import signal
import argparse
from datetime import datetime, timezone


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="HOPE Core v2.0 Trading System")
    parser.add_argument(
        "--mode",
        choices=["DRY", "TESTNET", "LIVE"],
        default="DRY",
        help="Trading mode (default: DRY)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="API host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8200,
        help="API port (default: 8200)",
    )
    parser.add_argument(
        "--config",
        type=str,
        help="Path to config file (optional)",
    )
    parser.add_argument(
        "--telegram-token",
        type=str,
        help="Telegram bot token for alerts",
    )
    parser.add_argument(
        "--telegram-chat",
        type=str,
        help="Telegram chat ID for alerts",
    )
    
    args = parser.parse_args()
    
    # Print banner
    print()
    print("=" * 60)
    print("         HOPE CORE v2.0 - AI Trading System")
    print("=" * 60)
    print(f"  Mode:     {args.mode}")
    print(f"  API:      http://{args.host}:{args.port}")
    print(f"  Started:  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 60)
    print()
    
    # Import after paths are set
    from hope_core import HopeCore, HopeCoreConfig
    from api_server import HopeCoreAPIServer, HAS_FASTAPI
    
    if not HAS_FASTAPI:
        print("ERROR: FastAPI not installed. Run: pip install fastapi uvicorn")
        sys.exit(1)
    
    # Configure
    config = HopeCoreConfig(
        mode=args.mode,
        api_host=args.host,
        api_port=args.port,
    )
    
    # Load from file if provided
    if args.config:
        config = HopeCoreConfig.from_file(Path(args.config))
        config.mode = args.mode  # Override mode from CLI
    
    # Set Telegram if provided
    if args.telegram_token:
        os.environ["TELEGRAM_BOT_TOKEN"] = args.telegram_token
    if args.telegram_chat:
        os.environ["TELEGRAM_CHAT_ID"] = args.telegram_chat
    
    # Create core
    core = HopeCore(config)
    
    # Create API server
    server = HopeCoreAPIServer(core, args.host, args.port)
    
    # Signal handlers
    def handle_signal(signum, frame):
        print(f"\n[HOPE CORE] Received signal {signum}, shutting down...")
        core.stop()
    
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    
    # Run
    async def run():
        # Start core loop in background
        async def core_loop():
            core._running = True
            await core.start()
        
        core_task = asyncio.create_task(core_loop())
        
        try:
            # Run API server (blocking)
            await server.run()
        finally:
            core.stop()
            core_task.cancel()
    
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n[HOPE CORE] Interrupted by user")
    except Exception as e:
        print(f"\n[HOPE CORE] Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    print("[HOPE CORE] Shutdown complete")


if __name__ == "__main__":
    main()
