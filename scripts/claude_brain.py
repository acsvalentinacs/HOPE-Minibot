# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 01:30:00 UTC
# Purpose: Claude Brain - Analytics & Directive Interface using UNIFIED CONFIG
# Contract: Single source of truth, no duplicate state
# === END SIGNATURE ===
"""
🧠 CLAUDE BRAIN v2.0 - Integrated with Unified Oracle Config

This module provides:
1. Analytics reports for Claude to analyze trading performance
2. Directive interface to modify trading parameters
3. Self-learning integration via unified config

IMPORTANT: This uses UNIFIED CONFIG as single source of truth.
No duplicate whitelist/blacklist/state storage.

Usage:
    # Show report
    python scripts/claude_brain.py report

    # Apply directive
    python scripts/claude_brain.py directive --type UPDATE_FILTER --action add_whitelist --target NEWUSDT

    # Run API server
    python scripts/claude_brain.py server --port 8300
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.oracle_config import ConfigManager, get_config_manager, get_config

log = logging.getLogger("CLAUDE-BRAIN")


@dataclass
class DirectiveResult:
    """Result of applying a directive."""
    directive_id: str
    success: bool
    message: str
    changes: Dict


class ClaudeBrain:
    """
    🧠 Claude Brain - Analytics & Control Interface

    Uses UNIFIED CONFIG - no duplicate state.
    All changes go through ConfigManager → atomic writes → single source of truth.
    """

    def __init__(self):
        self.config_manager = get_config_manager()
        log.info("Claude Brain initialized (unified config)")

    def get_report(self) -> Dict:
        """Generate analytics report from unified config."""
        return self.config_manager.generate_report()

    def get_state(self) -> Dict:
        """Get current state from unified config."""
        cfg = self.config_manager.get()
        return cfg.to_dict()

    def issue_directive(
        self,
        directive_type: str,
        action: str,
        target: str,
        value: Any = None,
        reason: str = "",
    ) -> DirectiveResult:
        """
        Issue directive to unified config.

        Examples:
            # Add to whitelist
            brain.issue_directive("UPDATE_FILTER", "add_whitelist", "NEWUSDT")

            # Set risk
            brain.issue_directive("SET_RISK", "set", "risk_multiplier", 0.7)

            # Pause symbol
            brain.issue_directive("PAUSE_SYMBOL", "pause", "BTCUSDT")
        """
        directive = {
            "directive_id": f"claude_{int(datetime.now().timestamp())}",
            "type": directive_type,
            "action": action,
            "target": target,
            "value": value,
            "reason": reason,
        }

        result = self.config_manager.apply_directive(directive)

        return DirectiveResult(
            directive_id=directive["directive_id"],
            success=result.get("success", False),
            message=result.get("message", ""),
            changes=result,
        )

    def suggest_directives(self) -> List[Dict]:
        """
        Generate directive suggestions based on performance data.

        Returns list of suggested directives that Claude can review and apply.
        """
        report = self.get_report()
        suggestions = []

        for sym in report.get("symbol_performance", []):
            if sym["recommendation"] == "whitelist" and not sym["in_whitelist"]:
                suggestions.append({
                    "type": "UPDATE_FILTER",
                    "action": "add_whitelist",
                    "target": sym["symbol"],
                    "reason": f"Win rate {sym['win_rate']*100:.0f}% over {sym['total']} trades",
                    "confidence": min(0.95, 0.5 + sym["win_rate"] * 0.5),
                })

            if sym["recommendation"] == "blacklist" and not sym["in_blacklist"]:
                suggestions.append({
                    "type": "UPDATE_FILTER",
                    "action": "add_blacklist",
                    "target": sym["symbol"],
                    "reason": f"Win rate {sym['win_rate']*100:.0f}% over {sym['total']} trades",
                    "confidence": min(0.95, 0.5 + (1 - sym["win_rate"]) * 0.5),
                })

        return suggestions


# ═══════════════════════════════════════════════════════════════════════════
# HTTP API (optional)
# ═══════════════════════════════════════════════════════════════════════════

async def create_brain_api(brain: ClaudeBrain, port: int = 8300):
    """Create HTTP API for Claude Brain."""
    try:
        from aiohttp import web
    except ImportError:
        log.error("aiohttp not installed, API disabled")
        return None

    async def handle_report(request):
        return web.json_response(brain.get_report())

    async def handle_state(request):
        return web.json_response(brain.get_state())

    async def handle_directive(request):
        data = await request.json()
        result = brain.issue_directive(
            directive_type=data.get("type", ""),
            action=data.get("action", ""),
            target=data.get("target", ""),
            value=data.get("value"),
            reason=data.get("reason", ""),
        )
        return web.json_response(asdict(result))

    async def handle_suggestions(request):
        return web.json_response({"suggestions": brain.suggest_directives()})

    app = web.Application()
    app.router.add_get("/brain/report", handle_report)
    app.router.add_get("/brain/state", handle_state)
    app.router.add_post("/brain/directive", handle_directive)
    app.router.add_get("/brain/suggestions", handle_suggestions)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()

    log.info(f"Claude Brain API on http://127.0.0.1:{port}")
    return runner


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def show_report():
    """Show analytics report."""
    brain = ClaudeBrain()
    report = brain.get_report()

    print("=" * 70)
    print("  CLAUDE BRAIN - ANALYTICS REPORT")
    print("=" * 70)

    summary = report.get("summary", {})
    print(f"\n[SUMMARY]")
    print(f"  Total Trades: {summary.get('total_trades', 0)}")
    print(f"  Wins: {summary.get('total_wins', 0)}")
    print(f"  Losses: {summary.get('total_losses', 0)}")
    print(f"  Win Rate: {summary.get('win_rate', 0)*100:.1f}%")
    print(f"  Calibration: {summary.get('calibration', 1.0):.2f}")

    filters = report.get("filters", {})
    print(f"\n[FILTERS]")
    print(f"  Whitelist: {filters.get('whitelist', [])}")
    print(f"  Blacklist: {filters.get('blacklist', [])}")

    thresholds = report.get("thresholds", {})
    print(f"\n[THRESHOLDS]")
    print(f"  Min Confidence: {thresholds.get('min_confidence', 0.5)}")
    print(f"  Risk Multiplier: {thresholds.get('risk_multiplier', 1.0)}")

    print(f"\n[SYMBOL PERFORMANCE]")
    for s in report.get("symbol_performance", [])[:10]:
        status = "[+]" if s["win_rate"] >= 0.5 else "[-]"
        wl = " WL" if s["in_whitelist"] else ""
        bl = " BL" if s["in_blacklist"] else ""
        print(f"  {status} {s['symbol']}: {s['win_rate']*100:.0f}% ({s['total']} trades){wl}{bl} -> {s['recommendation']}")

    print(f"\n[SUGGESTIONS]")
    for s in brain.suggest_directives():
        print(f"  * {s['type']}: {s['action']} {s['target']}")
        print(f"    Reason: {s['reason']}")

    print("=" * 70)


def apply_directive(args):
    """Apply directive from CLI."""
    brain = ClaudeBrain()

    value = args.value
    if value:
        try:
            value = json.loads(value)
        except:
            try:
                value = float(value)
            except:
                pass

    result = brain.issue_directive(
        directive_type=args.type,
        action=args.action,
        target=args.target,
        value=value,
        reason=args.reason or "",
    )

    status = "✅" if result.success else "❌"
    print(f"\n{status} Directive Result:")
    print(f"  ID: {result.directive_id}")
    print(f"  Success: {result.success}")
    print(f"  Message: {result.message}")


async def run_server(port: int = 8300):
    """Run API server."""
    brain = ClaudeBrain()
    runner = await create_brain_api(brain, port)

    if not runner:
        print("Failed to start API server")
        return

    print(f"\nClaude Brain API on http://127.0.0.1:{port}")
    print("\nEndpoints:")
    print("  GET  /brain/report      - Analytics report")
    print("  GET  /brain/state       - Current state")
    print("  POST /brain/directive   - Apply directive")
    print("  GET  /brain/suggestions - AI suggestions")
    print("\nPress Ctrl+C to stop...")

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()


def main():
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )

    parser = argparse.ArgumentParser(description="Claude Brain - AI Control Interface")
    subparsers = parser.add_subparsers(dest="command")

    # Report
    subparsers.add_parser("report", help="Show analytics report")

    # State
    subparsers.add_parser("state", help="Show current state")

    # Directive
    dir_parser = subparsers.add_parser("directive", help="Apply directive")
    dir_parser.add_argument("--type", required=True, help="Directive type")
    dir_parser.add_argument("--action", required=True, help="Action")
    dir_parser.add_argument("--target", required=True, help="Target")
    dir_parser.add_argument("--value", help="Value (JSON or number)")
    dir_parser.add_argument("--reason", help="Reason")

    # Server
    srv_parser = subparsers.add_parser("server", help="Run API server")
    srv_parser.add_argument("--port", type=int, default=8300, help="Port")

    # Suggestions
    subparsers.add_parser("suggestions", help="Show AI suggestions")

    args = parser.parse_args()

    if args.command == "report":
        show_report()

    elif args.command == "state":
        brain = ClaudeBrain()
        print(json.dumps(brain.get_state(), indent=2))

    elif args.command == "directive":
        apply_directive(args)

    elif args.command == "server":
        asyncio.run(run_server(args.port))

    elif args.command == "suggestions":
        brain = ClaudeBrain()
        for s in brain.suggest_directives():
            print(f"• {s['type']}: {s['action']} {s['target']}")
            print(f"  Reason: {s['reason']}")
            print(f"  Confidence: {s['confidence']:.2f}")
            print()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
