# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 04:15:00 UTC
# Purpose: CLI entry point for AI-Gateway server
# === END SIGNATURE ===
"""
AI-Gateway CLI: Run the AI-Gateway server.

Usage:
    python -m ai_gateway              # Start server on default port 8100
    python -m ai_gateway --port 8200  # Start server on port 8200
    python -m ai_gateway --status     # Show module status
"""

import argparse
import logging
import sys


def main():
    parser = argparse.ArgumentParser(
        description="HOPE AI-Gateway Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8100,
        help="Port to bind to (default: 8100)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show module status and exit",
    )
    parser.add_argument(
        "--enable",
        metavar="MODULE",
        help="Enable a module (sentiment, regime, doctor, anomaly)",
    )
    parser.add_argument(
        "--disable",
        metavar="MODULE",
        help="Disable a module",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [ai-gateway] %(levelname)s: %(message)s",
    )
    logger = logging.getLogger("ai_gateway")

    # Handle status command
    if args.status:
        from .status_manager import get_status_manager, MODULE_NAMES_RU

        sm = get_status_manager()
        print("\n🤖 AI-GATEWAY STATUS")
        print("=" * 40)

        gateway = sm.get_gateway_status()
        print(f"Gateway: {gateway.value} ({sm.get_active_count()}/4 active)")
        print()

        for module in ["sentiment", "regime", "doctor", "anomaly"]:
            emoji = sm.get_emoji(module)
            name = MODULE_NAMES_RU.get(module, module)
            enabled = "✓" if sm.is_enabled(module) else "✗"
            tooltip = sm.get_tooltip(module)
            print(f"  {emoji} {name} [{enabled}] - {tooltip}")

        print()
        return 0

    # Handle enable/disable
    if args.enable:
        from .status_manager import get_status_manager

        sm = get_status_manager()
        if sm.enable_module(args.enable):
            print(f"✅ Module '{args.enable}' enabled")
            return 0
        else:
            print(f"❌ Failed to enable module '{args.enable}'")
            return 1

    if args.disable:
        from .status_manager import get_status_manager

        sm = get_status_manager()
        if sm.disable_module(args.disable):
            print(f"⏸️ Module '{args.disable}' disabled")
            return 0
        else:
            print(f"❌ Failed to disable module '{args.disable}'")
            return 1

    # Start server
    try:
        from .server import run_server

        logger.info(f"Starting AI-Gateway server on {args.host}:{args.port}")
        run_server(host=args.host, port=args.port)
    except ImportError as e:
        logger.error(f"Missing dependencies: {e}")
        logger.error("Install with: pip install fastapi uvicorn anthropic")
        return 1
    except Exception as e:
        logger.exception(f"Server failed: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
