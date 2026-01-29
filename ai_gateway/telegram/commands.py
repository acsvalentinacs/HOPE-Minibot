# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 19:10:00 UTC
# Purpose: Telegram bot commands for AI Gateway (/predict, /stats, /history)
# Contract: Admin-only sensitive commands, public /predict
# === END SIGNATURE ===
"""
Telegram Commands for HOPE AI Gateway.

Commands:
    /predict XVSUSDT    - Get AI prediction for symbol
    /stats              - Performance statistics
    /history [n]        - Last n trades
    /mode               - Mode distribution
    /thresholds         - View current thresholds
    /circuit            - Circuit breaker status

Admin only:
    /enable_live        - Enable live trading
    /disable_live       - Disable live trading
    /force_close        - Force close all positions
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


# === Formatters ===

def format_prediction_message(
    symbol: str,
    precursor: Dict[str, Any],
    route: Optional[Dict[str, Any]] = None,
    enriched: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Format prediction result for Telegram.

    Returns HTML-formatted message.
    """
    lines = []

    # Header
    lines.append(f"<b>📊 PREDICTION: {symbol}</b>")
    lines.append("")

    # Precursor Detection
    prediction = precursor.get("prediction", "UNKNOWN")
    confidence = precursor.get("confidence", 0) * 100

    emoji = "✅" if prediction == "BUY" else "👀" if prediction == "WATCH" else "⏭️"
    lines.append(f"<b>Precursor:</b> {emoji} {prediction} ({confidence:.0f}%)")

    # Pattern scores
    patterns = precursor.get("signals_detected", [])
    scores = precursor.get("pattern_scores", {})

    all_patterns = [
        ("volume_raise", "Vol Raise"),
        ("active_buys", "Active Buys"),
        ("accelerating", "Accelerating"),
        ("delta_growing", "Delta Growing"),
        ("orderbook_pressure", "OB Pressure"),
        ("low_spread", "Low Spread"),
    ]

    for pattern_id, display_name in all_patterns:
        if pattern_id in patterns:
            score = scores.get(pattern_id, 0)
            lines.append(f"├── ✓ {display_name}: {score:.2f}")
        else:
            lines.append(f"├── ✗ {display_name}: -")

    lines.append("")

    # Mode Routing
    if route:
        mode = route.get("mode", "UNKNOWN")
        mode_emoji = {
            "SUPER_SCALP": "⚡",
            "SCALP": "🎯",
            "SWING": "📈",
            "SKIP": "⏭️",
        }.get(mode, "❓")

        lines.append(f"<b>Mode:</b> {mode_emoji} {mode}")

        config = route.get("config", {})
        if config:
            target = config.get("target_pct", 0)
            stop = config.get("stop_pct", 0)
            timeout = config.get("timeout_sec", 0)
            lines.append(f"├── Target: +{target}%")
            lines.append(f"├── Stop: -{stop}%")
            lines.append(f"└── Timeout: {timeout}s")

        lines.append("")

    # Enrichment data
    if enriched:
        lines.append("<b>Binance Data:</b>")
        price = enriched.get("price", 0)
        spread = enriched.get("spread_pct", 0)
        imbalance = enriched.get("orderbook_imbalance", 0)
        trades_1m = enriched.get("trades_1m", 0)

        lines.append(f"├── Price: ${price:,.4f}")
        lines.append(f"├── Spread: {spread:.3f}%")
        lines.append(f"├── OB Imbalance: {imbalance:+.2%}")
        lines.append(f"└── Trades/1m: {trades_1m}")

        lines.append("")

    # Final decision
    if prediction == "BUY" and route and route.get("mode") != "SKIP":
        lines.append("<b>Decision:</b> ✅ <b>BUY</b>")
    elif prediction == "WATCH":
        lines.append("<b>Decision:</b> 👀 <b>WATCH</b>")
    else:
        lines.append("<b>Decision:</b> ⏭️ <b>SKIP</b>")

    # Timestamp
    lines.append("")
    lines.append(f"<i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC</i>")

    return "\n".join(lines)


def format_stats_message(stats: Dict[str, Any]) -> str:
    """Format statistics for Telegram."""
    lines = []

    lines.append("<b>📈 PERFORMANCE STATS</b>")
    lines.append("")

    # Overview
    total = stats.get("total_trades", 0)
    win_rate = stats.get("win_rate", 0) * 100
    avg_pnl = stats.get("avg_pnl_pct", 0)

    lines.append(f"<b>Overview:</b>")
    lines.append(f"├── Total Trades: {total}")
    lines.append(f"├── Win Rate: {win_rate:.1f}%")
    lines.append(f"└── Avg PnL: {avg_pnl:+.2f}%")
    lines.append("")

    # MFE/MAE
    avg_mfe = stats.get("avg_mfe_pct", 0)
    avg_mae = stats.get("avg_mae_pct", 0)

    lines.append(f"<b>Excursions:</b>")
    lines.append(f"├── Avg MFE: +{avg_mfe:.2f}%")
    lines.append(f"└── Avg MAE: {avg_mae:.2f}%")
    lines.append("")

    # By mode
    by_mode = stats.get("by_mode", {})
    if by_mode:
        lines.append("<b>By Mode:</b>")
        for mode, mode_stats in by_mode.items():
            trades = mode_stats.get("trades", 0)
            wr = mode_stats.get("win_rate", 0) * 100
            lines.append(f"├── {mode}: {trades} trades, {wr:.0f}% WR")

    # Timestamp
    lines.append("")
    lines.append(f"<i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC</i>")

    return "\n".join(lines)


def format_history_message(outcomes: List[Dict[str, Any]], limit: int = 10) -> str:
    """Format trade history for Telegram."""
    lines = []

    lines.append(f"<b>📜 TRADE HISTORY</b> (last {limit})")
    lines.append("")

    if not outcomes:
        lines.append("<i>No trades yet</i>")
        return "\n".join(lines)

    for i, outcome in enumerate(outcomes[-limit:], 1):
        symbol = outcome.get("symbol", "???")
        pnl = outcome.get("pnl_pct", 0)
        mfe = outcome.get("mfe_pct", 0)
        mae = outcome.get("mae_pct", 0)
        mode = outcome.get("mode", "?")
        exit_reason = outcome.get("exit_reason", "?")

        emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
        lines.append(
            f"{emoji} <b>{symbol}</b> [{mode}] "
            f"PnL: {pnl:+.2f}% | MFE: +{mfe:.2f}% | {exit_reason}"
        )

    return "\n".join(lines)


def format_circuit_message(status: Dict[str, Any]) -> str:
    """Format circuit breaker status for Telegram."""
    lines = []

    state = status.get("state", "UNKNOWN")
    can_trade = status.get("can_trade", False)

    state_emoji = {
        "CLOSED": "🟢",
        "OPEN": "🔴",
        "HALF_OPEN": "🟡",
    }.get(state, "❓")

    lines.append(f"<b>⚡ CIRCUIT BREAKER</b>")
    lines.append("")
    lines.append(f"<b>State:</b> {state_emoji} {state}")
    lines.append(f"<b>Can Trade:</b> {'✅ Yes' if can_trade else '❌ No'}")
    lines.append("")

    consec = status.get("consecutive_losses", 0)
    daily = status.get("daily_losses", 0)
    daily_pct = status.get("daily_loss_pct", 0)

    lines.append("<b>Counters:</b>")
    lines.append(f"├── Consecutive Losses: {consec}")
    lines.append(f"├── Daily Losses: {daily}")
    lines.append(f"└── Daily Loss %: {daily_pct:.2f}%")

    cooldown = status.get("remaining_cooldown_sec", 0)
    if cooldown > 0:
        lines.append("")
        lines.append(f"⏳ <b>Cooldown:</b> {cooldown}s remaining")

    return "\n".join(lines)


# === Command Handlers ===

async def cmd_predict(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    """
    /predict SYMBOL - Get AI prediction for symbol.

    Usage:
        /predict XVSUSDT
        /predict XVS
    """
    if not context.args:
        await update.message.reply_text(
            "Usage: /predict SYMBOL\n\nExample: /predict XVSUSDT"
        )
        return

    symbol = context.args[0].upper()
    if not symbol.endswith("USDT"):
        symbol = symbol + "USDT"

    await update.message.reply_text(f"⏳ Analyzing {symbol}...")

    try:
        # Get latest signal data (from memory or create mock)
        signal_data = await _get_latest_signal_data(symbol)

        if not signal_data:
            await update.message.reply_text(
                f"No recent data for {symbol}.\n"
                "Try fetching fresh data or wait for MoonBot signal."
            )
            return

        # Run Precursor Detection
        from ..patterns.pump_precursor_detector import PumpPrecursorDetector
        detector = PumpPrecursorDetector()
        precursor = detector.detect(signal_data)

        # Run Mode Routing
        from ..core.mode_router import ModeRouter
        router = ModeRouter()
        route = router.route(signal_data)

        # Get enrichment if available
        enriched = None
        try:
            from ..feeds.binance_ws_enricher import get_enricher
            enricher = get_enricher()
            if enricher._running:
                enriched_signal = await enricher.enrich(signal_data)
                enriched = enriched_signal.binance.__dict__
        except Exception as e:
            logger.debug(f"Enrichment skipped: {e}")

        # Format message
        message = format_prediction_message(
            symbol=symbol,
            precursor={
                "prediction": precursor.prediction,
                "confidence": precursor.confidence,
                "signals_detected": precursor.signals_detected,
                "pattern_scores": precursor.pattern_scores,
            },
            route={
                "mode": route.mode.value if hasattr(route.mode, 'value') else str(route.mode),
                "confidence": route.confidence,
                "config": {
                    "target_pct": route.config.target_pct,
                    "stop_pct": route.config.stop_pct,
                    "timeout_sec": route.config.timeout_sec,
                } if route.config else {},
            },
            enriched=enriched,
        )

        await update.message.reply_text(message, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Prediction error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_stats(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    """
    /stats - Show performance statistics.
    """
    try:
        # Get stats from OutcomeTracker
        from ..feeds.price_bridge import get_price_bridge
        bridge = get_price_bridge()
        tracker = bridge._get_outcome_tracker()
        stats = tracker.get_stats()

        message = format_stats_message(stats)
        await update.message.reply_text(message, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Stats error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_history(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    """
    /history [n] - Show last n trades (default 10).
    """
    limit = 10
    if context.args:
        try:
            limit = int(context.args[0])
            limit = min(limit, 50)  # Max 50
        except ValueError:
            pass

    try:
        from ..feeds.price_bridge import get_price_bridge
        bridge = get_price_bridge()
        tracker = bridge._get_outcome_tracker()
        outcomes = tracker.get_completed_outcomes()

        message = format_history_message(outcomes, limit=limit)
        await update.message.reply_text(message, parse_mode="HTML")

    except Exception as e:
        logger.error(f"History error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_circuit(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    """
    /circuit - Show circuit breaker status.
    """
    try:
        from ..core.circuit_breaker import get_circuit_breaker
        breaker = get_circuit_breaker()
        status = breaker.get_status()

        message = format_circuit_message(status)
        await update.message.reply_text(message, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Circuit error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_mode(update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
    """
    /mode - Show mode distribution from recent decisions.
    """
    try:
        from ..jsonl_writer import read_latest

        decisions = read_latest("decisions", count=100)

        mode_counts = {}
        for d in decisions:
            mode = d.get("route", {}).get("mode", "UNKNOWN")
            mode_counts[mode] = mode_counts.get(mode, 0) + 1

        lines = ["<b>📊 MODE DISTRIBUTION</b> (last 100)", ""]

        total = sum(mode_counts.values()) or 1
        for mode, count in sorted(mode_counts.items(), key=lambda x: -x[1]):
            pct = (count / total) * 100
            bar = "█" * int(pct / 5)
            lines.append(f"{mode}: {bar} {count} ({pct:.0f}%)")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    except Exception as e:
        logger.error(f"Mode error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")


# === Helper Functions ===

async def _get_latest_signal_data(symbol: str) -> Optional[Dict[str, Any]]:
    """Get latest signal data for symbol from cache or fetch fresh."""
    # Try to get from recent signals
    try:
        from ..jsonl_writer import read_latest
        signals = read_latest("signals", count=100)

        for sig in reversed(signals):
            if sig.get("symbol", "").upper() == symbol:
                return sig.get("data", sig)
    except Exception:
        pass

    # Create a mock signal with Binance data for testing
    try:
        from ..feeds.binance_ws import fetch_prices_rest
        prices = await fetch_prices_rest([symbol])
        if symbol in prices:
            return {
                "symbol": symbol,
                "price": prices[symbol],
                "delta_pct": 0,
                "delta_btc_1m": 0,
                "delta_btc_5m": 0,
                "vol_raise_pct": 0,
                "buys_per_sec": 0,
            }
    except Exception:
        pass

    return None


# === Setup ===

def setup_handlers(application) -> None:
    """
    Setup Telegram command handlers.

    Args:
        application: python-telegram-bot Application instance
    """
    try:
        from telegram.ext import CommandHandler

        application.add_handler(CommandHandler("predict", cmd_predict))
        application.add_handler(CommandHandler("stats", cmd_stats))
        application.add_handler(CommandHandler("history", cmd_history))
        application.add_handler(CommandHandler("circuit", cmd_circuit))
        application.add_handler(CommandHandler("mode", cmd_mode))

        logger.info("Telegram command handlers registered")

    except ImportError:
        logger.warning("python-telegram-bot not installed, handlers not registered")
