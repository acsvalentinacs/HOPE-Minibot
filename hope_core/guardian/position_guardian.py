# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Module: hope_core/guardian/position_guardian.py
# Created by: Claude (opus-4.5)
# Created at: 2026-02-05T12:55:00Z
# Purpose: AI-powered Position Guardian with hybrid TP/SL management
# === END SIGNATURE ===
"""
Position Guardian - AI-powered position monitoring and management.

Features:
- Real-time position monitoring via Binance API
- AI-powered exit decisions via Eye of God
- Dynamic TP/SL based on market conditions
- BTC correlation filter
- Hard SL safety net (rules-based)
- Secret Sauce integration
- Telegram alerts

Usage:
    guardian = PositionGuardian(config)
    await guardian.start()
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum

log = logging.getLogger(__name__)


class ExitReason(Enum):
    """Why position was closed."""
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    AI_SIGNAL = "ai_signal"
    BTC_CRASH = "btc_crash"
    TIME_DECAY = "time_decay"
    MANUAL = "manual"
    PANIC = "panic"


@dataclass
class Position:
    """Tracked position data."""
    symbol: str
    quantity: float
    entry_price: float
    entry_time: float
    current_price: float = 0.0
    pnl_pct: float = 0.0
    mfe: float = 0.0  # Max Favorable Excursion
    mae: float = 0.0  # Max Adverse Excursion
    trailing_high: float = 0.0
    ai_score: float = 0.5

    def update_price(self, price: float) -> None:
        """Update current price and calculate metrics."""
        self.current_price = price
        self.pnl_pct = ((price - self.entry_price) / self.entry_price) * 100

        # Track MFE/MAE
        if self.pnl_pct > self.mfe:
            self.mfe = self.pnl_pct
            self.trailing_high = price
        if self.pnl_pct < self.mae:
            self.mae = self.pnl_pct

    @property
    def hold_time_minutes(self) -> float:
        """Minutes since entry."""
        return (time.time() - self.entry_time) / 60

    @property
    def value_usd(self) -> float:
        """Current position value in USD."""
        return self.quantity * self.current_price


@dataclass
class GuardianConfig:
    """Position Guardian configuration."""
    # Basic TP/SL (safety net)
    hard_sl_pct: float = -2.0  # Hard stop loss - NEVER exceeded
    base_tp_pct: float = 1.5   # Base take profit

    # Dynamic TP/SL
    enable_dynamic: bool = True
    min_tp_pct: float = 0.5
    max_tp_pct: float = 5.0

    # Trailing stop
    enable_trailing: bool = True
    trailing_activation_pct: float = 1.0  # Activate after +1%
    trailing_distance_pct: float = 0.5    # Trail by 0.5%

    # Time-based
    max_hold_minutes: int = 240  # 4 hours max
    time_decay_start_minutes: int = 60  # Start reducing TP after 1h

    # AI integration
    enable_ai: bool = True
    ai_close_threshold: float = 0.3  # Close if AI score < 0.3
    ai_hold_threshold: float = 0.7   # Hold longer if AI score > 0.7

    # BTC correlation
    enable_btc_filter: bool = True
    btc_crash_threshold_pct: float = -2.0  # Close all if BTC drops 2%
    btc_lookback_minutes: int = 15

    # Partial profits (Secret Thought #1)
    enable_partial_profits: bool = True
    partial_levels: List[Tuple[float, float]] = field(default_factory=lambda: [
        (1.0, 0.30),  # At +1%, close 30%
        (2.0, 0.30),  # At +2%, close another 30%
        (3.0, 0.40),  # At +3%, close remaining 40%
    ])

    # Monitoring
    check_interval_sec: int = 30
    state_dir: Path = Path("state/guardian")

    # API
    api_port: int = 8106


class PositionGuardian:
    """
    AI-powered Position Guardian.

    Monitors open positions and makes intelligent exit decisions
    using a hybrid approach: AI for optimal exits, rules for safety.
    """

    def __init__(
        self,
        config: Optional[GuardianConfig] = None,
        binance_client: Any = None,
        eye_of_god: Any = None,
        secret_sauce: Any = None,
    ):
        self.config = config or GuardianConfig()
        self.binance = binance_client
        self.eye_of_god = eye_of_god
        self.secret_sauce = secret_sauce

        # State
        self.positions: Dict[str, Position] = {}
        self.btc_prices: List[Tuple[float, float]] = []  # (timestamp, price)
        self.closed_positions: List[Dict] = []
        self._running = False
        self._last_check = 0.0

        # Stats
        self.stats = {
            "positions_monitored": 0,
            "positions_closed": 0,
            "total_pnl": 0.0,
            "wins": 0,
            "losses": 0,
            "by_reason": {},
        }

        # Ensure state dir exists
        self.config.state_dir.mkdir(parents=True, exist_ok=True)

        log.info(f"[GUARDIAN] Initialized with config: hard_sl={self.config.hard_sl_pct}%, base_tp={self.config.base_tp_pct}%")

    # =========================================================================
    # POSITION TRACKING
    # =========================================================================

    def track_position(
        self,
        symbol: str,
        quantity: float,
        entry_price: float,
        entry_time: Optional[float] = None,
    ) -> Position:
        """Start tracking a new position."""
        pos = Position(
            symbol=symbol,
            quantity=quantity,
            entry_price=entry_price,
            entry_time=entry_time or time.time(),
            current_price=entry_price,
            trailing_high=entry_price,
        )
        self.positions[symbol] = pos
        self.stats["positions_monitored"] += 1

        log.info(f"[GUARDIAN] Tracking: {symbol} | qty={quantity} | entry=${entry_price:.6f}")
        self._save_state()
        return pos

    def untrack_position(self, symbol: str) -> Optional[Position]:
        """Stop tracking a position."""
        return self.positions.pop(symbol, None)

    async def sync_positions_from_binance(self) -> int:
        """Sync positions from Binance account."""
        if not self.binance:
            log.warning("[GUARDIAN] No Binance client, skipping sync")
            return 0

        try:
            # Get account balances
            account = await self._get_account_balances()

            synced = 0
            for asset, balance in account.items():
                if asset in ("USDT", "USDC", "BUSD", "BTC", "ETH"):
                    continue  # Skip base currencies

                quantity = float(balance.get("free", 0)) + float(balance.get("locked", 0))
                if quantity <= 0:
                    continue

                symbol = f"{asset}USDT"

                # Get current price
                price = await self._get_price(symbol)
                if not price:
                    continue

                value = quantity * price
                if value < 1.0:  # Skip dust
                    continue

                # Track if not already tracked
                if symbol not in self.positions:
                    # Try to get entry price from trades
                    entry_price = await self._get_entry_price(symbol) or price
                    self.track_position(symbol, quantity, entry_price)

                synced += 1

            log.info(f"[GUARDIAN] Synced {synced} positions from Binance")
            return synced

        except Exception as e:
            log.error(f"[GUARDIAN] Sync error: {e}")
            return 0

    # =========================================================================
    # PRICE UPDATES
    # =========================================================================

    async def update_prices(self) -> None:
        """Update prices for all tracked positions."""
        if not self.positions:
            return

        symbols = list(self.positions.keys())
        symbols.append("BTCUSDT")  # Always track BTC

        try:
            prices = await self._get_prices_batch(symbols)

            for symbol, pos in self.positions.items():
                if symbol in prices:
                    pos.update_price(prices[symbol])

            # Track BTC for correlation filter
            if "BTCUSDT" in prices:
                self.btc_prices.append((time.time(), prices["BTCUSDT"]))
                # Keep only last 30 minutes
                cutoff = time.time() - 1800
                self.btc_prices = [(t, p) for t, p in self.btc_prices if t > cutoff]

        except Exception as e:
            log.error(f"[GUARDIAN] Price update error: {e}")

    # =========================================================================
    # EXIT DECISION ENGINE (AI + RULES HYBRID)
    # =========================================================================

    async def evaluate_position(self, pos: Position) -> Tuple[bool, ExitReason, str]:
        """
        Evaluate if position should be closed.

        Returns:
            (should_close, reason, details)
        """
        symbol = pos.symbol
        pnl = pos.pnl_pct

        # =====================================================================
        # LAYER 1: HARD RULES (Safety Net - Cannot be bypassed)
        # =====================================================================

        # Hard Stop Loss - ABSOLUTE PROTECTION
        if pnl <= self.config.hard_sl_pct:
            return True, ExitReason.STOP_LOSS, f"Hard SL hit: {pnl:.2f}% <= {self.config.hard_sl_pct}%"

        # BTC Crash Filter
        if self.config.enable_btc_filter:
            btc_change = self._get_btc_change()
            if btc_change and btc_change <= self.config.btc_crash_threshold_pct:
                return True, ExitReason.BTC_CRASH, f"BTC crashed {btc_change:.2f}%"

        # Panic mode from Secret Sauce
        if self.secret_sauce:
            panic, reason = self.secret_sauce.panic.is_panic()
            if panic:
                return True, ExitReason.PANIC, f"Panic mode: {reason}"

        # =====================================================================
        # LAYER 2: AI-POWERED DECISIONS
        # =====================================================================

        if self.config.enable_ai and self.eye_of_god:
            ai_score = await self._get_ai_score(symbol)
            pos.ai_score = ai_score

            # AI says SELL
            if ai_score < self.config.ai_close_threshold and pnl > -0.5:
                return True, ExitReason.AI_SIGNAL, f"AI score low: {ai_score:.2f}"

            # AI says HOLD - extend targets
            if ai_score > self.config.ai_hold_threshold:
                # Don't close on base TP if AI is bullish
                pass  # Will be handled in dynamic TP

        # =====================================================================
        # LAYER 3: DYNAMIC TP/SL
        # =====================================================================

        if self.config.enable_dynamic:
            dynamic_tp = self._calculate_dynamic_tp(pos)

            if pnl >= dynamic_tp:
                return True, ExitReason.TAKE_PROFIT, f"Dynamic TP: {pnl:.2f}% >= {dynamic_tp:.2f}%"
        else:
            # Simple base TP
            if pnl >= self.config.base_tp_pct:
                return True, ExitReason.TAKE_PROFIT, f"Base TP: {pnl:.2f}% >= {self.config.base_tp_pct}%"

        # =====================================================================
        # LAYER 4: TRAILING STOP
        # =====================================================================

        if self.config.enable_trailing and pos.mfe >= self.config.trailing_activation_pct:
            trailing_stop = pos.trailing_high * (1 - self.config.trailing_distance_pct / 100)

            if pos.current_price <= trailing_stop:
                return True, ExitReason.TRAILING_STOP, f"Trailing stop: price {pos.current_price:.6f} <= {trailing_stop:.6f}"

        # =====================================================================
        # LAYER 5: TIME DECAY
        # =====================================================================

        hold_minutes = pos.hold_time_minutes

        if hold_minutes >= self.config.max_hold_minutes:
            if pnl > 0:
                return True, ExitReason.TIME_DECAY, f"Max hold time ({hold_minutes:.0f}m) with profit"
            elif pnl > -0.5:
                return True, ExitReason.TIME_DECAY, f"Max hold time ({hold_minutes:.0f}m), cutting small loss"

        # =====================================================================
        # DEFAULT: HOLD
        # =====================================================================

        return False, ExitReason.TAKE_PROFIT, "HOLD"

    def _calculate_dynamic_tp(self, pos: Position) -> float:
        """
        Calculate dynamic take profit based on conditions.

        Secret Thought #2: Time Decay TP
        """
        base_tp = self.config.base_tp_pct

        # Adjust by AI score
        if pos.ai_score > 0.7:
            base_tp *= 1.5  # Extend TP if AI bullish
        elif pos.ai_score < 0.4:
            base_tp *= 0.7  # Lower TP if AI bearish

        # Time decay: reduce TP over time
        hold_minutes = pos.hold_time_minutes
        if hold_minutes > self.config.time_decay_start_minutes:
            decay_factor = max(0.5, 1 - (hold_minutes - self.config.time_decay_start_minutes) / 180)
            base_tp *= decay_factor

        # Clamp to bounds
        return max(self.config.min_tp_pct, min(self.config.max_tp_pct, base_tp))

    def _get_btc_change(self) -> Optional[float]:
        """Get BTC price change over lookback period."""
        if len(self.btc_prices) < 2:
            return None

        lookback_sec = self.config.btc_lookback_minutes * 60
        cutoff = time.time() - lookback_sec

        old_prices = [p for t, p in self.btc_prices if t <= cutoff + 60]
        if not old_prices:
            return None

        old_price = old_prices[0]
        current_price = self.btc_prices[-1][1]

        return ((current_price - old_price) / old_price) * 100

    async def _get_ai_score(self, symbol: str) -> float:
        """Get AI score from Eye of God."""
        if not self.eye_of_god:
            return 0.5

        try:
            # Call Eye of God for analysis
            analysis = await self.eye_of_god.analyze_symbol(symbol)
            return analysis.get("score", 0.5)
        except Exception as e:
            log.warning(f"[GUARDIAN] AI score error for {symbol}: {e}")
            return 0.5

    # =========================================================================
    # EXECUTION
    # =========================================================================

    async def close_position(
        self,
        symbol: str,
        reason: ExitReason,
        details: str,
        partial_pct: float = 1.0,
    ) -> bool:
        """
        Close a position (fully or partially).

        Args:
            symbol: Trading pair
            reason: Why closing
            details: Human-readable explanation
            partial_pct: Fraction to close (1.0 = full)
        """
        if symbol not in self.positions:
            log.warning(f"[GUARDIAN] Cannot close {symbol}: not tracked")
            return False

        pos = self.positions[symbol]
        close_qty = pos.quantity * partial_pct

        log.info(f"[GUARDIAN] CLOSING {symbol} | qty={close_qty:.6f} | reason={reason.value} | {details}")

        try:
            # Execute market sell
            if self.binance:
                order = await self._market_sell(symbol, close_qty)
                if not order:
                    log.error(f"[GUARDIAN] Failed to close {symbol}")
                    return False

            # Record close
            close_record = {
                "symbol": symbol,
                "quantity": close_qty,
                "entry_price": pos.entry_price,
                "exit_price": pos.current_price,
                "pnl_pct": pos.pnl_pct,
                "pnl_usd": (pos.current_price - pos.entry_price) * close_qty,
                "mfe": pos.mfe,
                "mae": pos.mae,
                "hold_minutes": pos.hold_time_minutes,
                "reason": reason.value,
                "details": details,
                "ai_score": pos.ai_score,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            self.closed_positions.append(close_record)
            self._log_close(close_record)

            # Update stats
            self.stats["positions_closed"] += 1
            self.stats["total_pnl"] += close_record["pnl_usd"]
            if pos.pnl_pct >= 0:
                self.stats["wins"] += 1
            else:
                self.stats["losses"] += 1
            self.stats["by_reason"][reason.value] = self.stats["by_reason"].get(reason.value, 0) + 1

            # Remove from tracking if fully closed
            if partial_pct >= 0.99:
                self.untrack_position(symbol)
            else:
                pos.quantity -= close_qty

            # Send Telegram alert
            await self._send_alert(close_record)

            # Notify Secret Sauce
            if self.secret_sauce:
                self.secret_sauce.record_result(symbol, close_record["pnl_usd"], pos.pnl_pct >= 0)

            self._save_state()
            return True

        except Exception as e:
            log.error(f"[GUARDIAN] Close error for {symbol}: {e}")
            return False

    def _log_close(self, record: Dict) -> None:
        """Log close to JSONL file."""
        log_file = self.config.state_dir / "closes.jsonl"
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as e:
            log.error(f"[GUARDIAN] Log error: {e}")

    # =========================================================================
    # MAIN LOOP
    # =========================================================================

    async def run_once(self) -> Dict[str, Any]:
        """Run one monitoring cycle."""
        results = {
            "checked": 0,
            "closed": 0,
            "errors": 0,
            "positions": [],
        }

        # Update prices
        await self.update_prices()

        # Evaluate each position
        for symbol, pos in list(self.positions.items()):
            results["checked"] += 1

            try:
                should_close, reason, details = await self.evaluate_position(pos)

                pos_status = {
                    "symbol": symbol,
                    "pnl_pct": pos.pnl_pct,
                    "mfe": pos.mfe,
                    "mae": pos.mae,
                    "hold_minutes": pos.hold_time_minutes,
                    "ai_score": pos.ai_score,
                    "decision": "CLOSE" if should_close else "HOLD",
                    "reason": reason.value if should_close else None,
                }
                results["positions"].append(pos_status)

                if should_close:
                    # Check for partial profits first
                    if self.config.enable_partial_profits and reason == ExitReason.TAKE_PROFIT:
                        closed = await self._handle_partial_profits(pos)
                        if closed:
                            results["closed"] += 1
                            continue

                    # Full close
                    success = await self.close_position(symbol, reason, details)
                    if success:
                        results["closed"] += 1

            except Exception as e:
                log.error(f"[GUARDIAN] Error evaluating {symbol}: {e}")
                results["errors"] += 1

        self._last_check = time.time()
        return results

    async def _handle_partial_profits(self, pos: Position) -> bool:
        """
        Handle partial profit taking.

        Secret Thought #1: Scale out at multiple levels.
        """
        for level_pct, close_pct in self.config.partial_levels:
            if pos.pnl_pct >= level_pct:
                # Check if we already took profit at this level
                level_key = f"{pos.symbol}_partial_{level_pct}"
                if hasattr(self, '_partial_taken') and level_key in self._partial_taken:
                    continue

                if not hasattr(self, '_partial_taken'):
                    self._partial_taken = set()

                success = await self.close_position(
                    pos.symbol,
                    ExitReason.TAKE_PROFIT,
                    f"Partial profit at +{level_pct}%",
                    partial_pct=close_pct,
                )

                if success:
                    self._partial_taken.add(level_key)
                    return True

        return False

    async def start(self) -> None:
        """Start the guardian loop."""
        self._running = True
        log.info(f"[GUARDIAN] Starting (interval={self.config.check_interval_sec}s)")

        # Initial sync
        await self.sync_positions_from_binance()

        while self._running:
            try:
                results = await self.run_once()

                if results["closed"] > 0:
                    log.info(f"[GUARDIAN] Cycle: checked={results['checked']}, closed={results['closed']}")

            except Exception as e:
                log.error(f"[GUARDIAN] Loop error: {e}")

            await asyncio.sleep(self.config.check_interval_sec)

    def stop(self) -> None:
        """Stop the guardian loop."""
        self._running = False
        log.info("[GUARDIAN] Stopped")

    # =========================================================================
    # STATE PERSISTENCE
    # =========================================================================

    def _save_state(self) -> None:
        """Save current state to disk."""
        state = {
            "positions": {
                sym: {
                    "symbol": pos.symbol,
                    "quantity": pos.quantity,
                    "entry_price": pos.entry_price,
                    "entry_time": pos.entry_time,
                    "current_price": pos.current_price,
                    "pnl_pct": pos.pnl_pct,
                    "mfe": pos.mfe,
                    "mae": pos.mae,
                }
                for sym, pos in self.positions.items()
            },
            "stats": self.stats,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        state_file = self.config.state_dir / "guardian_state.json"
        try:
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, default=str)
        except Exception as e:
            log.error(f"[GUARDIAN] State save error: {e}")

    def _load_state(self) -> None:
        """Load state from disk."""
        state_file = self.config.state_dir / "guardian_state.json"
        if not state_file.exists():
            return

        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)

            for sym, data in state.get("positions", {}).items():
                self.positions[sym] = Position(**data)

            self.stats.update(state.get("stats", {}))
            log.info(f"[GUARDIAN] Loaded {len(self.positions)} positions from state")

        except Exception as e:
            log.error(f"[GUARDIAN] State load error: {e}")

    # =========================================================================
    # BINANCE API HELPERS
    # =========================================================================

    async def _get_account_balances(self) -> Dict[str, Dict]:
        """Get account balances from Binance."""
        if not self.binance:
            return {}

        try:
            if hasattr(self.binance, 'get_account'):
                account = await self.binance.get_account()
                return {b['asset']: b for b in account.get('balances', [])}
            return {}
        except Exception as e:
            log.error(f"[GUARDIAN] Balance fetch error: {e}")
            return {}

    async def _get_price(self, symbol: str) -> Optional[float]:
        """Get current price for symbol."""
        if not self.binance:
            return None

        try:
            if hasattr(self.binance, 'get_symbol_ticker'):
                ticker = await self.binance.get_symbol_ticker(symbol=symbol)
                return float(ticker.get('price', 0))
            return None
        except Exception:
            return None

    async def _get_prices_batch(self, symbols: List[str]) -> Dict[str, float]:
        """Get prices for multiple symbols."""
        if not self.binance:
            return {}

        try:
            if hasattr(self.binance, 'get_all_tickers'):
                tickers = await self.binance.get_all_tickers()
                return {t['symbol']: float(t['price']) for t in tickers if t['symbol'] in symbols}
            return {}
        except Exception as e:
            log.error(f"[GUARDIAN] Batch price error: {e}")
            return {}

    async def _get_entry_price(self, symbol: str) -> Optional[float]:
        """Get entry price from recent trades."""
        if not self.binance:
            return None

        try:
            if hasattr(self.binance, 'get_my_trades'):
                trades = await self.binance.get_my_trades(symbol=symbol, limit=10)
                buys = [t for t in trades if t.get('isBuyer')]
                if buys:
                    return float(buys[-1]['price'])
            return None
        except Exception:
            return None

    async def _market_sell(self, symbol: str, quantity: float) -> Optional[Dict]:
        """Execute market sell order."""
        if not self.binance:
            log.warning(f"[GUARDIAN] DRY RUN: would sell {quantity} {symbol}")
            return {"orderId": "DRY_RUN", "status": "FILLED"}

        try:
            if hasattr(self.binance, 'create_order'):
                order = await self.binance.create_order(
                    symbol=symbol,
                    side="SELL",
                    type="MARKET",
                    quantity=quantity,
                )
                log.info(f"[GUARDIAN] Sold {symbol}: orderId={order.get('orderId')}")
                return order
            return None
        except Exception as e:
            log.error(f"[GUARDIAN] Sell error for {symbol}: {e}")
            return None

    # =========================================================================
    # TELEGRAM ALERTS
    # =========================================================================

    async def _send_alert(self, close_record: Dict) -> None:
        """Send Telegram alert for position close."""
        try:
            # Try to use existing Telegram bot
            from hope_core.alerts.telegram import send_alert

            pnl = close_record["pnl_pct"]
            emoji = "🟢" if pnl >= 0 else "🔴"

            msg = (
                f"{emoji} <b>POSITION CLOSED</b>\n\n"
                f"Symbol: {close_record['symbol']}\n"
                f"PnL: {pnl:+.2f}% (${close_record['pnl_usd']:+.2f})\n"
                f"Hold: {close_record['hold_minutes']:.0f} min\n"
                f"Reason: {close_record['reason']}\n"
                f"MFE: {close_record['mfe']:.2f}% | MAE: {close_record['mae']:.2f}%"
            )

            await send_alert(msg, parse_mode="HTML")

        except Exception as e:
            log.debug(f"[GUARDIAN] Alert error: {e}")

    # =========================================================================
    # API ENDPOINTS
    # =========================================================================

    def get_status(self) -> Dict[str, Any]:
        """Get guardian status for API."""
        return {
            "running": self._running,
            "positions": len(self.positions),
            "stats": self.stats,
            "config": {
                "hard_sl": self.config.hard_sl_pct,
                "base_tp": self.config.base_tp_pct,
                "trailing_enabled": self.config.enable_trailing,
                "ai_enabled": self.config.enable_ai,
            },
            "last_check": self._last_check,
            "positions_detail": [
                {
                    "symbol": pos.symbol,
                    "quantity": pos.quantity,
                    "entry_price": pos.entry_price,
                    "current_price": pos.current_price,
                    "pnl_pct": pos.pnl_pct,
                    "value_usd": pos.value_usd,
                    "hold_minutes": pos.hold_time_minutes,
                    "mfe": pos.mfe,
                    "mae": pos.mae,
                }
                for pos in self.positions.values()
            ],
        }


# =============================================================================
# STANDALONE API SERVER
# =============================================================================

def create_api_app(guardian: PositionGuardian):
    """Create FastAPI app for guardian."""
    try:
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse
    except ImportError:
        return None

    app = FastAPI(title="Position Guardian API", version="1.0.0")

    @app.get("/health")
    async def health():
        return {"status": "ok", "positions": len(guardian.positions)}

    @app.get("/status")
    async def status():
        return guardian.get_status()

    @app.get("/positions")
    async def positions():
        return guardian.get_status()["positions_detail"]

    @app.post("/sync")
    async def sync():
        count = await guardian.sync_positions_from_binance()
        return {"synced": count}

    @app.post("/close/{symbol}")
    async def close(symbol: str):
        success = await guardian.close_position(
            symbol.upper(),
            ExitReason.MANUAL,
            "Manual close via API"
        )
        return {"success": success}

    return app


# =============================================================================
# MAIN
# =============================================================================

async def main():
    """Run Position Guardian standalone."""
    import argparse

    parser = argparse.ArgumentParser(description="Position Guardian")
    parser.add_argument("--port", type=int, default=8106)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    config = GuardianConfig(api_port=args.port)
    guardian = PositionGuardian(config)

    # Load existing state
    guardian._load_state()

    # Create API
    app = create_api_app(guardian)

    if app:
        import uvicorn

        # Run API and guardian concurrently
        async def run_both():
            guardian_task = asyncio.create_task(guardian.start())

            config = uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning")
            server = uvicorn.Server(config)
            api_task = asyncio.create_task(server.serve())

            await asyncio.gather(guardian_task, api_task)

        await run_both()
    else:
        await guardian.start()


if __name__ == "__main__":
    asyncio.run(main())
