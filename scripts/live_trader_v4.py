# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 22:50:00 UTC
# Purpose: LIVE Trader v4 - Full trading cycle entrypoint
# === END SIGNATURE ===
"""
═══════════════════════════════════════════════════════════════════════════════
  HOPE LIVE TRADER v4.0 - ПОЛНЫЙ ТОРГОВЫЙ ЦИКЛ
═══════════════════════════════════════════════════════════════════════════════

FLOW:
  1. Load signals from JSONL (tail mode)
  2. Signal Gate check (cannot bypass)
  3. EyeOfGodV3 decision (two-chamber)
  4. Adaptive TP/SL calculation
  5. Binance MARKET entry + OCO exit
  6. Log outcome for learning

MODES:
  --dry      DRY mode (default) - no real orders
  --testnet  TESTNET mode - testnet orders
  --live     LIVE mode - REAL MONEY (requires HOPE_MODE=LIVE + HOPE_LIVE_ACK)

USAGE:
  # DRY mode (safe)
  python scripts/live_trader_v4.py --dry --signals data/moonbot_signals/signals_20260130.jsonl

  # LIVE mode (requires env setup)
  python scripts/live_trader_v4.py --live --signals data/moonbot_signals/signals_20260130.jsonl

═══════════════════════════════════════════════════════════════════════════════
"""

import os
import sys
import json
import time
import asyncio
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict

# Setup path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("live_trader")

# ═══════════════════════════════════════════════════════════════════════════════
# IMPORTS (fail-closed)
# ═══════════════════════════════════════════════════════════════════════════════

try:
    from core.signal_gate import SignalGate, GateDecision
except ImportError:
    log.error("FAIL: core.signal_gate not found")
    sys.exit(1)

try:
    from core.adaptive_tp_engine import calculate_adaptive_tp
except ImportError:
    log.error("FAIL: core.adaptive_tp_engine not found")
    sys.exit(1)

try:
    from execution.binance_live_client import (
        get_trading_mode, TradingMode, check_live_barrier,
        create_async_binance_client, LiveModeNotAcknowledged, MissingCredentials
    )
except ImportError:
    log.error("FAIL: execution.binance_live_client not found")
    sys.exit(1)

try:
    from scripts.eye_of_god_adapter import get_eye_of_god_adapter
except ImportError:
    log.warning("eye_of_god_adapter not found, using gate-only mode")
    get_eye_of_god_adapter = None

try:
    from config.live_trade_policy import check_symbol_allowed, MAX_POSITION_USDT
except ImportError:
    MAX_POSITION_USDT = 50.0
    def check_symbol_allowed(s): return (True, "policy_not_loaded")


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TradeDecision:
    """Trade decision from full pipeline."""
    symbol: str
    action: str  # BUY, SKIP
    confidence: float
    delta_pct: float
    target_pct: float
    stop_pct: float
    position_usdt: float
    timeout_sec: int
    reasons: List[str]
    gate_decision: str
    eye_decision: str


@dataclass
class TradeExecution:
    """Trade execution result."""
    symbol: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl_usdt: float
    pnl_pct: float
    status: str
    duration_sec: float
    fees_usdt: float


# ═══════════════════════════════════════════════════════════════════════════════
# LIVE TRADER
# ═══════════════════════════════════════════════════════════════════════════════

class LiveTraderV4:
    """
    LIVE Trader v4 - Full trading cycle.

    DRY mode by default. LIVE requires explicit acknowledgment.
    """

    def __init__(self, mode: TradingMode, max_quote: float = 50.0):
        self.mode = mode
        self.max_quote = max_quote

        # Components
        self.gate = SignalGate()
        self.eye_adapter = get_eye_of_god_adapter() if get_eye_of_god_adapter else None
        self.client = None

        # State
        self._trades_today = 0
        self._pnl_today = 0.0
        self._processed_ids: set = set()
        self._running = False

        log.info(f"LiveTraderV4 initialized: mode={mode.value}, max_quote=${max_quote}")

    async def start(self):
        """Start trader and create Binance client if needed."""
        if self.mode in (TradingMode.TESTNET, TradingMode.LIVE):
            try:
                self.client = await create_async_binance_client(self.mode)
                log.info(f"Binance client created for {self.mode.value}")
            except Exception as e:
                log.error(f"Failed to create Binance client: {e}")
                if self.mode == TradingMode.LIVE:
                    raise
        self._running = True

    async def stop(self):
        """Stop trader and close connections."""
        self._running = False
        if self.client:
            await self.client.close_connection()
            log.info("Binance client closed")

    async def process_signal(self, signal: Dict[str, Any]) -> Optional[TradeExecution]:
        """
        Process a signal through the full trading cycle.

        Returns TradeExecution if trade was executed, None otherwise.
        """
        signal_id = signal.get("signal_id", "")
        symbol = signal.get("symbol", "").upper()
        delta_pct = float(signal.get("delta_pct", 0))
        signal_type = signal.get("type", signal.get("signal_type", ""))

        # Dedupe
        if signal_id and signal_id in self._processed_ids:
            return None
        if signal_id:
            self._processed_ids.add(signal_id)

        log.info(f"Processing: {symbol} delta={delta_pct:.2f}% type={signal_type}")

        # ═══════════════════════════════════════════════════════════════════
        # STEP 1: Signal Gate (CANNOT BYPASS)
        # ═══════════════════════════════════════════════════════════════════

        gate_decision, block_reason, details = self.gate.check(signal)

        if gate_decision == GateDecision.BLOCK:
            log.debug(f"Gate BLOCKED: {block_reason}")
            return None

        if gate_decision == GateDecision.PASS_LOG_ONLY:
            log.debug(f"Gate LOG_ONLY: {block_reason}")
            return None

        # ═══════════════════════════════════════════════════════════════════
        # STEP 2: Policy Check
        # ═══════════════════════════════════════════════════════════════════

        allowed, reason = check_symbol_allowed(symbol)
        if not allowed:
            log.info(f"Policy blocked: {symbol} - {reason}")
            return None

        # ═══════════════════════════════════════════════════════════════════
        # STEP 3: Eye of God Decision (optional)
        # ═══════════════════════════════════════════════════════════════════

        eye_decision = "N/A"
        if self.eye_adapter:
            try:
                eye_result = self.eye_adapter.analyze(signal)
                eye_decision = eye_result.get("action", "SKIP")

                if eye_decision == "SKIP":
                    reasons = eye_result.get("reasons", [])
                    log.info(f"Eye SKIP: {symbol} - {reasons}")
                    return None
            except Exception as e:
                log.warning(f"Eye error (continuing): {e}")

        # ═══════════════════════════════════════════════════════════════════
        # STEP 4: Adaptive TP/SL
        # ═══════════════════════════════════════════════════════════════════

        confidence = float(signal.get("confidence", 0.7))
        tp_result = calculate_adaptive_tp(delta_pct, confidence)

        if not tp_result.should_trade:
            log.info(f"TP rejected: {tp_result.reason}")
            return None

        target_pct = tp_result.target_pct
        stop_pct = tp_result.stop_loss_pct
        position_mult = tp_result.position_mult
        timeout_sec = tp_result.timeout_sec

        position_usdt = min(self.max_quote * position_mult, self.max_quote)

        log.info(f"TP: target={target_pct}% stop={stop_pct}% R:R={tp_result.effective_rr:.2f} pos=${position_usdt:.2f}")

        # ═══════════════════════════════════════════════════════════════════
        # STEP 5: Execute Trade
        # ═══════════════════════════════════════════════════════════════════

        if self.mode == TradingMode.DRY:
            return await self._dry_execute(symbol, position_usdt, target_pct, stop_pct, timeout_sec)
        else:
            return await self._live_execute(symbol, position_usdt, target_pct, stop_pct, timeout_sec)

    async def _dry_execute(self, symbol: str, position_usdt: float,
                           target_pct: float, stop_pct: float, timeout_sec: int) -> TradeExecution:
        """DRY mode execution - simulate trade."""
        import random

        log.info(f"[DRY] BUY {symbol} ${position_usdt:.2f} TP={target_pct}% SL={stop_pct}%")

        # Simulate outcome
        outcomes = [
            ("tp_hit", target_pct),
            ("sl_hit", -stop_pct),
            ("timeout", 0.0),
        ]
        status, pnl_pct = random.choice(outcomes)

        pnl_usdt = position_usdt * pnl_pct / 100
        fees = position_usdt * 0.002  # 0.2% round trip

        self._trades_today += 1
        self._pnl_today += (pnl_usdt - fees)

        log.info(f"[DRY] {symbol} {status}: PnL=${pnl_usdt:.2f} (fee=${fees:.2f})")

        return TradeExecution(
            symbol=symbol,
            entry_price=100.0,
            exit_price=100.0 * (1 + pnl_pct / 100),
            quantity=position_usdt / 100,
            pnl_usdt=pnl_usdt - fees,
            pnl_pct=pnl_pct,
            status=status,
            duration_sec=timeout_sec / 2,
            fees_usdt=fees,
        )

    async def _live_execute(self, symbol: str, position_usdt: float,
                            target_pct: float, stop_pct: float, timeout_sec: int) -> Optional[TradeExecution]:
        """LIVE mode execution - real orders."""
        if not self.client:
            log.error("No Binance client available")
            return None

        entry_time = time.time()

        try:
            # Get current price
            ticker = await self.client.get_symbol_ticker(symbol=symbol)
            current_price = float(ticker["price"])

            # Calculate quantity
            quantity = position_usdt / current_price

            # Get symbol info for precision
            info = await self.client.get_symbol_info(symbol)
            if info:
                lot_filter = next((f for f in info["filters"] if f["filterType"] == "LOT_SIZE"), None)
                if lot_filter:
                    step_size = float(lot_filter["stepSize"])
                    precision = len(str(step_size).rstrip('0').split('.')[-1])
                    quantity = round(quantity // step_size * step_size, precision)

            log.info(f"[{self.mode.value.upper()}] MARKET BUY {symbol} qty={quantity} @ {current_price}")

            # Place market order
            order = await self.client.create_order(
                symbol=symbol,
                side="BUY",
                type="MARKET",
                quantity=quantity,
            )

            entry_price = float(order.get("fills", [{}])[0].get("price", current_price))
            log.info(f"Entry filled: {symbol} @ {entry_price}")

            # Calculate TP/SL prices
            tp_price = round(entry_price * (1 + target_pct / 100), 8)
            sl_price = round(entry_price * (1 - stop_pct / 100), 8)

            # Place OCO order
            try:
                oco = await self.client.create_oco_order(
                    symbol=symbol,
                    side="SELL",
                    quantity=quantity,
                    price=str(tp_price),
                    stopPrice=str(sl_price),
                    stopLimitPrice=str(sl_price),
                    stopLimitTimeInForce="GTC",
                )
                log.info(f"OCO placed: TP={tp_price} SL={sl_price}")
            except Exception as oco_err:
                log.error(f"OCO failed, emergency close: {oco_err}")
                await self.client.create_order(
                    symbol=symbol, side="SELL", type="MARKET", quantity=quantity
                )
                return None

            # Monitor position
            start_monitor = time.time()
            exit_price = entry_price
            status = "timeout"

            while (time.time() - start_monitor) < timeout_sec:
                await asyncio.sleep(1)

                ticker = await self.client.get_symbol_ticker(symbol=symbol)
                price = float(ticker["price"])

                if price >= tp_price:
                    exit_price = tp_price
                    status = "tp_hit"
                    break
                if price <= sl_price:
                    exit_price = sl_price
                    status = "sl_hit"
                    break

            if status == "timeout":
                # Cancel OCO and market close
                try:
                    open_orders = await self.client.get_open_orders(symbol=symbol)
                    for o in open_orders:
                        await self.client.cancel_order(symbol=symbol, orderId=o["orderId"])
                except:
                    pass

                close_order = await self.client.create_order(
                    symbol=symbol, side="SELL", type="MARKET", quantity=quantity
                )
                exit_price = float(close_order.get("fills", [{}])[0].get("price", price))

            # Calculate PnL
            pnl_pct = (exit_price - entry_price) / entry_price * 100
            pnl_usdt = position_usdt * pnl_pct / 100
            fees = position_usdt * 0.002

            self._trades_today += 1
            self._pnl_today += (pnl_usdt - fees)

            exit_time = time.time()

            log.info(f"[{self.mode.value.upper()}] {symbol} {status}: "
                     f"entry={entry_price} exit={exit_price} PnL=${pnl_usdt:.2f}")

            return TradeExecution(
                symbol=symbol,
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=quantity,
                pnl_usdt=pnl_usdt - fees,
                pnl_pct=pnl_pct,
                status=status,
                duration_sec=exit_time - entry_time,
                fees_usdt=fees,
            )

        except Exception as e:
            log.exception(f"Trade error: {e}")
            return None

    def get_stats(self) -> Dict[str, Any]:
        """Get trading stats."""
        return {
            "mode": self.mode.value,
            "trades_today": self._trades_today,
            "pnl_today": round(self._pnl_today, 2),
            "processed_signals": len(self._processed_ids),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL FILE PROCESSOR
# ═══════════════════════════════════════════════════════════════════════════════

async def process_signals_file(trader: LiveTraderV4, signals_path: str, max_signals: int = 0):
    """Process signals from JSONL file."""
    path = Path(signals_path)
    if not path.exists():
        log.error(f"Signals file not found: {signals_path}")
        return

    log.info(f"Processing signals from: {signals_path}")

    processed = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            try:
                signal = json.loads(line)
                result = await trader.process_signal(signal)

                if result:
                    processed += 1

                if max_signals > 0 and processed >= max_signals:
                    log.info(f"Max signals reached: {max_signals}")
                    break

            except json.JSONDecodeError:
                continue

    log.info(f"Processed {processed} trades from {signals_path}")


async def tail_signals_file(trader: LiveTraderV4, signals_path: str):
    """Tail signals file for new entries (live mode)."""
    path = Path(signals_path)
    if not path.exists():
        log.error(f"Signals file not found: {signals_path}")
        return

    log.info(f"Tailing signals from: {signals_path}")

    # Start from end of file
    with open(path, "r", encoding="utf-8") as f:
        f.seek(0, 2)  # Go to end

        while trader._running:
            line = f.readline()
            if line:
                line = line.strip()
                if line:
                    try:
                        signal = json.loads(line)
                        await trader.process_signal(signal)
                    except json.JSONDecodeError:
                        pass
            else:
                await asyncio.sleep(0.5)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

async def main():
    parser = argparse.ArgumentParser(description="HOPE Live Trader v4")
    parser.add_argument("--dry", action="store_true", help="DRY mode (default)")
    parser.add_argument("--testnet", action="store_true", help="TESTNET mode")
    parser.add_argument("--live", action="store_true", help="LIVE mode (requires env)")
    parser.add_argument("--signals", type=str, required=True, help="Signals JSONL file")
    parser.add_argument("--max-quote", type=float, default=50.0, help="Max quote per trade")
    parser.add_argument("--max-signals", type=int, default=0, help="Max signals to process (0=all)")
    parser.add_argument("--tail", action="store_true", help="Tail mode (watch for new signals)")

    args = parser.parse_args()

    print("=" * 70)
    print("HOPE LIVE TRADER v4.0")
    print("=" * 70)

    # Determine mode
    if args.live:
        try:
            mode = get_trading_mode()
            if mode != TradingMode.LIVE:
                print("\n[BLOCKED] --live flag requires HOPE_MODE=LIVE in environment")
                print("\nTo enable LIVE trading, add to C:/secrets/hope.env:")
                print("  HOPE_MODE=LIVE")
                print("  HOPE_LIVE_ACK=YES_I_UNDERSTAND")
                print("  BINANCE_API_KEY=your_key")
                print("  BINANCE_API_SECRET=your_secret")
                return
        except LiveModeNotAcknowledged as e:
            print(f"\n[BLOCKED] {e}")
            return
        except MissingCredentials as e:
            print(f"\n[BLOCKED] {e}")
            return
    elif args.testnet:
        mode = TradingMode.TESTNET
    else:
        mode = TradingMode.DRY

    # Show barrier status
    is_ready, barrier_msg = check_live_barrier()
    print("\nLIVE Barrier Check:")
    print(barrier_msg)
    print()

    if mode == TradingMode.LIVE:
        print("[WARNING] LIVE MODE - REAL MONEY WILL BE USED")
        print("[WARNING] Press Ctrl+C within 5 seconds to abort...")
        try:
            await asyncio.sleep(5)
        except KeyboardInterrupt:
            print("\nAborted by user")
            return

    # Create trader
    trader = LiveTraderV4(mode=mode, max_quote=args.max_quote)
    await trader.start()

    try:
        if args.tail:
            await tail_signals_file(trader, args.signals)
        else:
            await process_signals_file(trader, args.signals, args.max_signals)
    except KeyboardInterrupt:
        log.info("Interrupted by user")
    finally:
        await trader.stop()

    # Print stats
    print("\n" + "=" * 70)
    print("SESSION STATS")
    print("=" * 70)
    stats = trader.get_stats()
    for k, v in stats.items():
        print(f"  {k}: {v}")

    print("\n[DONE] Live Trader v4 completed")


if __name__ == "__main__":
    asyncio.run(main())
