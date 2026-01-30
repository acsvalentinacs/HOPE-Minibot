# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-01-30T08:30:00Z
# Purpose: PANIC CLOSE - Emergency position liquidation
# Contract: Close ALL positions immediately, create STOP.flag, log everything
# === END SIGNATURE ===
"""
HOPE AI - PANIC CLOSE SYSTEM

Аварийное закрытие ВСЕХ позиций:
1. Получить список всех открытых позиций
2. Закрыть каждую MARKET ордером
3. Создать STOP.flag (остановить торговлю)
4. Записать лог в panic_events.jsonl
5. Отправить отчёт в Telegram

Usage:
    python scripts/panic_close.py                    # DRY RUN
    python scripts/panic_close.py --execute         # LIVE EXECUTE
    python scripts/panic_close.py --testnet         # TESTNET MODE

From Python:
    from scripts.panic_close import execute_panic_close
    result = await execute_panic_close(testnet=True, dry_run=False)
"""

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import List, Dict, Optional, Any

# Ensure project root
sys.path.insert(0, str(Path(__file__).parent.parent))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("PANIC")

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

SECRETS_PATH = Path("C:/secrets/hope.env")
STATE_DIR = Path("state/ai/production")
STOP_FLAG = Path("state/STOP.flag")
PANIC_LOG = STATE_DIR / "panic_events.jsonl"

# Symbols to check for positions
TRADE_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
]

# Minimum quantity to consider as position
MIN_POSITION_VALUE_USD = 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Position:
    symbol: str
    asset: str
    quantity: float
    value_usd: float
    price: float


@dataclass
class CloseResult:
    symbol: str
    success: bool
    order_id: Optional[int]
    quantity: float
    price: float
    pnl_usd: float
    error: Optional[str]


@dataclass
class PanicResult:
    timestamp: str
    mode: str  # TESTNET / MAINNET
    dry_run: bool
    reason: str
    positions_found: int
    positions_closed: int
    total_value_usd: float
    total_pnl_usd: float
    close_results: List[CloseResult]
    stop_flag_created: bool
    errors: List[str]

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["close_results"] = [asdict(r) for r in self.close_results]
        return d


# ═══════════════════════════════════════════════════════════════════════════════
# CREDENTIAL LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def load_credentials(testnet: bool = True) -> tuple[Optional[str], Optional[str]]:
    """Load Binance credentials"""

    # Check environment first
    if testnet:
        api_key = os.environ.get("BINANCE_TESTNET_API_KEY") or os.environ.get("BINANCE_API_KEY")
        api_secret = os.environ.get("BINANCE_TESTNET_API_SECRET") or os.environ.get("BINANCE_API_SECRET")
    else:
        api_key = os.environ.get("BINANCE_MAINNET_API_KEY") or os.environ.get("BINANCE_API_KEY")
        api_secret = os.environ.get("BINANCE_MAINNET_API_SECRET") or os.environ.get("BINANCE_API_SECRET")

    if api_key and api_secret:
        return api_key, api_secret

    # Load from secrets file
    if SECRETS_PATH.exists():
        try:
            for line in SECRETS_PATH.read_text(encoding="utf-8").split("\n"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")

            if testnet:
                api_key = os.environ.get("BINANCE_TESTNET_API_KEY") or os.environ.get("BINANCE_API_KEY")
                api_secret = os.environ.get("BINANCE_TESTNET_API_SECRET") or os.environ.get("BINANCE_API_SECRET")
            else:
                api_key = os.environ.get("BINANCE_MAINNET_API_KEY") or os.environ.get("BINANCE_API_KEY")
                api_secret = os.environ.get("BINANCE_MAINNET_API_SECRET") or os.environ.get("BINANCE_API_SECRET")

            return api_key, api_secret
        except Exception as e:
            logger.error(f"Failed to load credentials: {e}")

    return None, None


# ═══════════════════════════════════════════════════════════════════════════════
# POSITION DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

def get_open_positions(client, testnet: bool = True) -> List[Position]:
    """Get all open positions (non-zero balances)"""

    positions = []

    try:
        # Get account balances
        account = client.get_account()
        balances = account.get("balances", [])

        # Get prices for all symbols
        prices = {}
        tickers = client.get_all_tickers()
        for t in tickers:
            prices[t["symbol"]] = float(t["price"])

        # Find non-zero balances (excluding stablecoins)
        stablecoins = {"USDT", "USDC", "BUSD", "TUSD", "FDUSD", "USD1"}

        for bal in balances:
            asset = bal["asset"]
            free = float(bal["free"])
            locked = float(bal["locked"])
            total = free + locked

            if total <= 0 or asset in stablecoins:
                continue

            # Find price
            symbol = f"{asset}USDT"
            price = prices.get(symbol, 0)

            if price > 0:
                value_usd = total * price

                if value_usd >= MIN_POSITION_VALUE_USD:
                    positions.append(Position(
                        symbol=symbol,
                        asset=asset,
                        quantity=total,
                        value_usd=value_usd,
                        price=price,
                    ))

        return positions

    except Exception as e:
        logger.error(f"Failed to get positions: {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# PANIC CLOSE EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

async def execute_panic_close(
    testnet: bool = True,
    dry_run: bool = True,
    reason: str = "MANUAL",
) -> PanicResult:
    """
    Execute PANIC CLOSE - close ALL positions immediately.

    Args:
        testnet: Use testnet (True) or mainnet (False)
        dry_run: If True, don't actually place orders
        reason: Reason for panic close (for logging)

    Returns:
        PanicResult with all details
    """

    from binance.client import Client
    from binance.enums import SIDE_SELL, ORDER_TYPE_MARKET

    result = PanicResult(
        timestamp=datetime.now(timezone.utc).isoformat(),
        mode="TESTNET" if testnet else "MAINNET",
        dry_run=dry_run,
        reason=reason,
        positions_found=0,
        positions_closed=0,
        total_value_usd=0,
        total_pnl_usd=0,
        close_results=[],
        stop_flag_created=False,
        errors=[],
    )

    # Load credentials
    api_key, api_secret = load_credentials(testnet)
    if not api_key or not api_secret:
        result.errors.append("Failed to load credentials")
        return result

    # Create client
    try:
        client = Client(api_key, api_secret, testnet=testnet)
    except Exception as e:
        result.errors.append(f"Failed to create client: {e}")
        return result

    # Get positions
    logger.info("=" * 60)
    logger.info("PANIC CLOSE INITIATED")
    logger.info("=" * 60)
    logger.info(f"Mode: {result.mode}")
    logger.info(f"Dry Run: {dry_run}")
    logger.info(f"Reason: {reason}")
    logger.info("")

    positions = get_open_positions(client, testnet)
    result.positions_found = len(positions)
    result.total_value_usd = sum(p.value_usd for p in positions)

    logger.info(f"Positions found: {result.positions_found}")
    logger.info(f"Total value: ${result.total_value_usd:.2f}")
    logger.info("")

    if not positions:
        logger.info("No positions to close")
        result.stop_flag_created = _create_stop_flag(reason)
        _log_panic_event(result)
        return result

    # Close each position
    for pos in positions:
        logger.info(f"Closing {pos.symbol}: {pos.quantity:.6f} (${pos.value_usd:.2f})")

        if dry_run:
            close_result = CloseResult(
                symbol=pos.symbol,
                success=True,
                order_id=None,
                quantity=pos.quantity,
                price=pos.price,
                pnl_usd=0,
                error="DRY_RUN",
            )
        else:
            close_result = await _close_position(client, pos)

        result.close_results.append(close_result)

        if close_result.success:
            result.positions_closed += 1
            result.total_pnl_usd += close_result.pnl_usd
            logger.info(f"  [OK] Order #{close_result.order_id} @ ${close_result.price:.2f}")
        else:
            result.errors.append(f"{pos.symbol}: {close_result.error}")
            logger.error(f"  [FAIL] {close_result.error}")

    # Create STOP flag
    result.stop_flag_created = _create_stop_flag(reason)

    # Log event
    _log_panic_event(result)

    # Send Telegram notification
    await _notify_telegram_panic(result)

    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("PANIC CLOSE COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Positions closed: {result.positions_closed}/{result.positions_found}")
    logger.info(f"Total P&L: ${result.total_pnl_usd:.2f}")
    logger.info(f"STOP flag: {'CREATED' if result.stop_flag_created else 'FAILED'}")
    logger.info(f"Errors: {len(result.errors)}")
    logger.info("=" * 60)

    return result


async def _close_position(client, pos: Position) -> CloseResult:
    """Close a single position with market sell order"""

    from binance.enums import SIDE_SELL, ORDER_TYPE_MARKET

    try:
        # Get lot size filter for symbol
        info = client.get_symbol_info(pos.symbol)
        step_size = 0.00001  # default
        min_qty = 0.00001

        for f in info.get("filters", []):
            if f["filterType"] == "LOT_SIZE":
                step_size = float(f["stepSize"])
                min_qty = float(f["minQty"])
                break

        # Round quantity to step size
        qty = pos.quantity
        if step_size > 0:
            qty = float(int(qty / step_size) * step_size)

        if qty < min_qty:
            return CloseResult(
                symbol=pos.symbol,
                success=False,
                order_id=None,
                quantity=qty,
                price=pos.price,
                pnl_usd=0,
                error=f"Quantity {qty} below minimum {min_qty}",
            )

        # Place market sell order
        order = client.create_order(
            symbol=pos.symbol,
            side=SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=qty,
        )

        # Get fill price
        fills = order.get("fills", [])
        if fills:
            fill_price = sum(float(f["price"]) * float(f["qty"]) for f in fills) / sum(float(f["qty"]) for f in fills)
        else:
            fill_price = pos.price

        # Calculate P&L (approximate - we don't know entry price)
        pnl_usd = 0  # Would need entry price for accurate P&L

        return CloseResult(
            symbol=pos.symbol,
            success=True,
            order_id=order.get("orderId"),
            quantity=float(order.get("executedQty", qty)),
            price=fill_price,
            pnl_usd=pnl_usd,
            error=None,
        )

    except Exception as e:
        return CloseResult(
            symbol=pos.symbol,
            success=False,
            order_id=None,
            quantity=pos.quantity,
            price=pos.price,
            pnl_usd=0,
            error=str(e),
        )


def _create_stop_flag(reason: str) -> bool:
    """Create STOP.flag to halt trading"""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STOP_FLAG.parent.mkdir(parents=True, exist_ok=True)

        content = json.dumps({
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reason": f"PANIC_CLOSE: {reason}",
            "created_by": "panic_close.py",
        }, indent=2)

        STOP_FLAG.write_text(content, encoding="utf-8")
        logger.info(f"STOP.flag created: {STOP_FLAG}")
        return True
    except Exception as e:
        logger.error(f"Failed to create STOP.flag: {e}")
        return False


def _log_panic_event(result: PanicResult):
    """Log panic event to JSONL file"""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)

        with open(PANIC_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(result.to_dict(), default=str) + "\n")

        logger.info(f"Event logged: {PANIC_LOG}")
    except Exception as e:
        logger.error(f"Failed to log event: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM NOTIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

async def _notify_telegram_panic(result: PanicResult):
    """Send panic close notification to Telegram."""
    import urllib.request
    import urllib.parse

    try:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_ADMIN_ID")

        if not token or not chat_id:
            logger.debug("Telegram credentials not configured - skipping notification")
            return

        # Format message
        msg = format_panic_result(result, use_emoji=True)

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": "HTML",
        }).encode()

        req = urllib.request.Request(url, data=data, method="POST")
        resp = urllib.request.urlopen(req, timeout=10)

        if resp.status == 200:
            logger.info("Telegram notification sent")
        else:
            logger.warning(f"Telegram notification failed: {resp.status}")

    except Exception as e:
        logger.error(f"Failed to send Telegram notification: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM MESSAGE FORMATTER
# ═══════════════════════════════════════════════════════════════════════════════

def format_panic_result(result: PanicResult, use_emoji: bool = True) -> str:
    """Format panic result for Telegram message"""

    if use_emoji:
        header = "🚨 PANIC CLOSE EXECUTED"
        ok = "✅"
        fail = "❌"
        warn = "⚠️"
    else:
        header = "[!] PANIC CLOSE EXECUTED"
        ok = "[OK]"
        fail = "[X]"
        warn = "[!]"

    lines = [
        header,
        "=" * 25,
        f"Mode: {result.mode}",
        f"Reason: {result.reason}",
        f"Dry Run: {'Yes' if result.dry_run else 'No'}",
        "",
        f"Positions found: {result.positions_found}",
        f"Positions closed: {result.positions_closed}",
        f"Total value: ${result.total_value_usd:.2f}",
        "",
    ]

    if result.close_results:
        lines.append("Closed:")
        for r in result.close_results[:10]:  # Max 10 shown
            status = ok if r.success else fail
            lines.append(f"  {status} {r.symbol}: {r.quantity:.6f} @ ${r.price:.2f}")
        if len(result.close_results) > 10:
            lines.append(f"  ... and {len(result.close_results) - 10} more")

    if result.errors:
        lines.append("")
        lines.append("Errors:")
        for e in result.errors[:3]:  # Max 3 errors
            lines.append(f"  {warn} {e}")

    lines.extend([
        "",
        f"STOP flag: {ok + ' Created' if result.stop_flag_created else fail + ' Failed'}",
        "=" * 25,
        f"Time: {result.timestamp[:19]}",
    ])

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    import argparse

    parser = argparse.ArgumentParser(description="HOPE AI - PANIC CLOSE")
    parser.add_argument("--execute", action="store_true", help="Actually execute (not dry run)")
    parser.add_argument("--testnet", action="store_true", default=True, help="Use testnet")
    parser.add_argument("--mainnet", action="store_true", help="Use mainnet (DANGEROUS)")
    parser.add_argument("--reason", type=str, default="MANUAL", help="Reason for panic")

    args = parser.parse_args()

    testnet = not args.mainnet
    dry_run = not args.execute

    if not testnet and not dry_run:
        print("⚠️  WARNING: This will close ALL positions on MAINNET!")
        confirm = input("Type 'CONFIRM PANIC' to proceed: ")
        if confirm != "CONFIRM PANIC":
            print("Aborted.")
            sys.exit(1)

    result = asyncio.run(execute_panic_close(
        testnet=testnet,
        dry_run=dry_run,
        reason=args.reason,
    ))

    print("")
    print(format_panic_result(result, use_emoji=False))

    sys.exit(0 if result.positions_closed == result.positions_found else 1)


if __name__ == "__main__":
    main()
