# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 14:20:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-30 00:45:00 UTC
# Purpose: HOPE AI AutoTrader - Complete autonomous trading loop with Eye of God integration
# sha256: autotrader_v2.0_eye_of_god
# === END SIGNATURE ===
"""
HOPE AI - AutoTrader v1.0

⚠️ COMPLETE AUTONOMOUS TRADING SYSTEM - REAL MONEY

This is the MAIN ENTRY POINT that connects:
1. MoonBot signal parsing
2. AI Decision Engine
3. Order Executor
4. Position Management
5. Risk Management

TRADING LOOP:
┌─────────────────────────────────────────────────────────────────────────────┐
│                          AUTOTRADER LOOP                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌──────────────┐         ┌──────────────┐         ┌──────────────┐       │
│   │   MoonBot    │────────>│   Decision   │────────>│    Order     │       │
│   │   Signals    │         │    Engine    │  BUY    │   Executor   │       │
│   └──────────────┘         └──────────────┘         └──────────────┘       │
│                                   │                        │                │
│                                   │ SKIP                   │                │
│                                   v                        v                │
│                            ┌──────────────┐         ┌──────────────┐       │
│                            │     Log      │         │   Position   │       │
│                            │   Skipped    │         │   Manager    │       │
│                            └──────────────┘         └──────────────┘       │
│                                                            │                │
│                                   ┌────────────────────────┘                │
│                                   │                                         │
│                                   v                                         │
│   ┌──────────────┐         ┌──────────────┐         ┌──────────────┐       │
│   │   Binance    │────────>│    Check     │────────>│    Close     │       │
│   │   Prices     │         │   Targets    │  HIT    │   Position   │       │
│   └──────────────┘         └──────────────┘         └──────────────┘       │
│                                                            │                │
│                                                            v                │
│                                                     ┌──────────────┐       │
│                                                     │   Calculate  │       │
│                                                     │     P&L      │       │
│                                                     └──────────────┘       │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

Usage:
    # DRY mode (no real orders)
    python autotrader.py --mode DRY
    
    # TESTNET mode (fake money)
    python autotrader.py --mode TESTNET
    
    # LIVE mode (REAL MONEY!)
    python autotrader.py --mode LIVE --confirm
"""

import json
import time
import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional, Set
from pathlib import Path
from enum import Enum
import threading
from collections import deque

try:
    import httpx
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "httpx", "-q"])
    import httpx

try:
    import websockets
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "websockets", "-q"])
    import websockets

# Import our modules (will be in same directory)
try:
    from order_executor import OrderExecutor, TradingMode, OrderResult, Position
except ImportError:
    # Fallback for standalone testing
    TradingMode = Enum('TradingMode', ['DRY', 'TESTNET', 'LIVE'])

# Import Eye of God AI Oracle
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from scripts.eye_of_god import EyeOfGod
except ImportError:
    EyeOfGod = None

# Import sha256 logging contracts
try:
    from core.io_atomic import log_trade_event, append_jsonl
except ImportError:
    log_trade_event = None
    append_jsonl = None

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AutoTraderConfig:
    """AutoTrader configuration"""
    mode: str = "TESTNET"
    gateway_url: str = "http://127.0.0.1:8100"
    
    # Trading parameters
    default_position_usdt: float = 10.0    # Default position size
    max_position_usdt: float = 50.0        # Max position size
    
    # Signal thresholds
    min_confidence: float = 0.70           # Minimum confidence to trade
    pump_override_buys_sec: float = 100    # Instant buy threshold
    scalp_buys_sec: float = 30             # SCALP threshold
    min_buys_sec: float = 10               # Minimum to consider
    
    # Risk parameters
    default_target_pct: float = 1.0        # Default take profit
    default_stop_pct: float = -0.5         # Default stop loss
    default_timeout_sec: int = 60          # Default timeout
    
    # Loop settings
    loop_interval_sec: float = 1.0         # Main loop interval
    position_check_interval: float = 0.5   # Position check interval
    
    # State
    state_dir: str = "state/ai/autotrader"
    log_file: str = "state/ai/autotrader/trades.jsonl"


# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL PROCESSOR
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ProcessedSignal:
    """Processed trading signal"""
    signal_id: str
    symbol: str
    strategy: str
    direction: str  # Long/Short
    price: float
    buys_per_sec: float
    delta_pct: float
    vol_raise_pct: float
    confidence: float
    mode: str  # PUMP_OVERRIDE, SUPER_SCALP, SCALP, SKIP
    target_pct: float
    stop_pct: float
    timeout_sec: int
    reasons: List[str]
    timestamp: str
    
    def should_trade(self) -> bool:
        return self.mode != "SKIP" and self.direction == "Long"


class SignalProcessor:
    """
    Processes raw MoonBot signals into trading decisions.

    NOTE: When Eye of God is enabled (default), this class is used only for
    signal extraction. Actual trading decisions are made by Eye of God.
    """

    def __init__(self, config: AutoTraderConfig):
        self.config = config
        self.recent_signals: deque = deque(maxlen=100)

        # Get filters from unified config
        try:
            from core.oracle_config import get_config
            cfg = get_config()
            self.BLACKLIST_SYMBOLS = cfg.blacklist
            self.WHITELIST_SYMBOLS = {s: {"win_rate": 1.0} for s in cfg.whitelist}
        except ImportError:
            # Fallback if unified config not available
            self.BLACKLIST_SYMBOLS = {"SYNUSDT", "DODOUSDT", "AXSUSDT", "ARPAUSDT"}
            self.WHITELIST_SYMBOLS = {
                "KITEUSDT": {"win_rate": 1.0, "avg_mfe": 2.81},
                "DUSKUSDT": {"win_rate": 1.0, "avg_mfe": 3.87},
                "XVSUSDT": {"win_rate": 1.0, "avg_mfe": 3.38},
            }

    def process(self, raw_signal: Dict) -> Optional[ProcessedSignal]:
        """Process raw signal from MoonBot/Gateway"""

        # Extract fields
        symbol = raw_signal.get("symbol", "")
        if not symbol:
            return None

        # Check for duplicate
        signal_key = f"{symbol}:{raw_signal.get('timestamp', '')}"
        if signal_key in self.recent_signals:
            return None
        self.recent_signals.append(signal_key)

        # Parse signal details
        strategy = raw_signal.get("strategy", "unknown")
        direction = raw_signal.get("direction", "Long")
        price = float(raw_signal.get("price", 0))
        buys_sec = float(raw_signal.get("buys_per_sec", 0))
        delta_pct = float(raw_signal.get("delta_pct", 0))
        vol_raise = float(raw_signal.get("vol_raise_pct", 0))

        # === PRIORITY 0: BLACKLIST CHECK (ALWAYS SKIP) ===
        if symbol in self.BLACKLIST_SYMBOLS:
            logger.warning(f"BLACKLIST: {symbol} -> FORCED SKIP (0% win rate)")
            return ProcessedSignal(
                signal_id=f"sig_{int(time.time()*1000)}_{symbol}",
                symbol=symbol,
                strategy=strategy,
                direction=direction,
                price=price,
                buys_per_sec=buys_sec,
                delta_pct=delta_pct,
                vol_raise_pct=vol_raise,
                confidence=0.0,
                mode="SKIP",
                target_pct=0,
                stop_pct=0,
                timeout_sec=0,
                reasons=[f"BLACKLIST:{symbol}"],
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        # === CLASSIFICATION ===
        reasons = []
        mode = "SKIP"
        confidence = 0.0
        target_pct = self.config.default_target_pct
        stop_pct = self.config.default_stop_pct
        timeout = self.config.default_timeout_sec

        # === PRIORITY 1: WHITELIST CHECK (FORCE TRADE) ===
        is_whitelist = symbol in self.WHITELIST_SYMBOLS
        if is_whitelist:
            stats = self.WHITELIST_SYMBOLS.get(symbol, {})
            win_rate = stats.get('win_rate', 1.0)
            reasons.append(f"WHITELIST:{symbol}(win={win_rate*100:.0f}%)")
            # Whitelist ALWAYS trades with SCALP mode and high confidence
            mode = "SCALP"
            confidence = 0.85  # Above min_confidence threshold
            target_pct = 0.5
            stop_pct = -0.3
            timeout = 30
            logger.info(f"WHITELIST: {symbol} -> FORCE TRADE (conf={confidence:.0%})")
            # Skip normal classification for whitelist
            return ProcessedSignal(
                signal_id=f"sig_{int(time.time()*1000)}_{symbol}",
                symbol=symbol,
                strategy=strategy,
                direction=direction,
                price=price,
                buys_per_sec=buys_sec,
                delta_pct=delta_pct,
                vol_raise_pct=vol_raise,
                confidence=confidence,
                mode=mode,
                target_pct=target_pct,
                stop_pct=stop_pct,
                timeout_sec=timeout,
                reasons=reasons,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        # Filter SHORT
        if direction == "Short":
            reasons.append("SHORT_DIRECTION_SKIP")
            mode = "SKIP"
            confidence = 0.1

        # Filter DropsDetection alone
        elif "Drop" in strategy and buys_sec < 20:
            reasons.append("DROP_WITHOUT_PUMP_CONFIRMATION")
            mode = "SKIP"
            confidence = 0.2

        # PUMP_OVERRIDE: buys/sec > 100
        elif buys_sec >= self.config.pump_override_buys_sec:
            mode = "PUMP_OVERRIDE"
            confidence = 0.95
            target_pct = 0.5
            stop_pct = -0.3
            timeout = 30
            reasons.append(f"PUMP_OVERRIDE:buys_sec={buys_sec:.1f}>100")
        
        # SUPER_SCALP: buys > 50 OR delta > 5%
        elif buys_sec >= 50 or delta_pct >= 5.0:
            mode = "SUPER_SCALP"
            confidence = 0.85
            target_pct = 0.5
            stop_pct = -0.3
            timeout = 30
            reasons.append(f"SUPER_SCALP:buys={buys_sec:.1f},delta={delta_pct:.1f}%")
        
        # SCALP: buys > 30 AND delta > 2%
        elif buys_sec >= self.config.scalp_buys_sec and delta_pct >= 2.0:
            mode = "SCALP"
            confidence = 0.75
            reasons.append(f"SCALP:buys={buys_sec:.1f},delta={delta_pct:.1f}%")
        
        # Volume spike
        elif vol_raise >= 100 and buys_sec >= self.config.min_buys_sec:
            mode = "SCALP"
            confidence = 0.70
            reasons.append(f"VOLUME_SPIKE:vol_raise={vol_raise:.1f}%")
        
        # Weak signal
        elif buys_sec >= self.config.min_buys_sec:
            mode = "SKIP"
            confidence = 0.40
            reasons.append(f"WEAK_SIGNAL:buys={buys_sec:.1f}")
        
        else:
            reasons.append("NO_CRITERIA_MET")
            confidence = 0.1
        
        return ProcessedSignal(
            signal_id=f"sig_{int(time.time()*1000)}_{symbol}",
            symbol=symbol,
            strategy=strategy,
            direction=direction,
            price=price,
            buys_per_sec=buys_sec,
            delta_pct=delta_pct,
            vol_raise_pct=vol_raise,
            confidence=confidence,
            mode=mode,
            target_pct=target_pct,
            stop_pct=stop_pct,
            timeout_sec=timeout,
            reasons=reasons,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# PRICE FEED
# ═══════════════════════════════════════════════════════════════════════════════

class PriceFeed:
    """
    Real-time price feed from Gateway with PriceFeed V1 Contract support.

    V1 Contract:
    - Subscribed symbols always have entry (price can be null)
    - Stale prices (> 60s) are marked
    - get_all_prices() returns only VALID prices (not null, not stale)
    """

    MAX_STALE_SEC = 60  # Prices older than this are considered stale

    def __init__(self, gateway_url: str):
        self.gateway_url = gateway_url
        self.prices: Dict[str, float] = {}
        self.stale: Dict[str, bool] = {}
        self.last_update: Dict[str, datetime] = {}
        self.subscribed: Set[str] = set()
        self.client = httpx.Client(timeout=5)

    def get_price(self, symbol: str) -> Optional[float]:
        """
        Get current price for symbol.

        Returns None if:
        - Price not available
        - Price is stale
        """
        if symbol not in self.prices:
            return None
        if self.stale.get(symbol, True):
            return None
        return self.prices.get(symbol)

    def get_all_prices(self) -> Dict[str, float]:
        """
        Get all VALID prices (not null, not stale).

        This is what Eye of God uses for trading decisions.
        FAIL-CLOSED: only returns prices we can trust.
        """
        valid = {}
        for symbol, price in self.prices.items():
            if price is not None and price > 0 and not self.stale.get(symbol, True):
                valid[symbol] = price
        return valid

    def update_from_gateway(self):
        """
        Update prices from AI Gateway (PriceFeed V1 Contract).

        Handles:
        - null prices (subscribed but not yet received)
        - stale indicators
        - subscribed symbol tracking
        """
        try:
            resp = self.client.get(f"{self.gateway_url}/price-feed/prices")
            if resp.status_code == 200:
                data = resp.json()
                now = datetime.now(timezone.utc)

                # Track subscribed symbols
                self.subscribed = set(data.get("subscribed", []))

                for symbol, price_data in data.get("prices", {}).items():
                    if isinstance(price_data, dict):
                        # V1 Contract format
                        price = price_data.get("price")
                        stale = price_data.get("stale", True)

                        if price is not None:
                            self.prices[symbol] = float(price)
                        else:
                            self.prices[symbol] = 0  # Mark as missing

                        self.stale[symbol] = stale
                    else:
                        # Legacy format (direct price value)
                        self.prices[symbol] = float(price_data) if price_data else 0
                        self.stale[symbol] = False

                    self.last_update[symbol] = now

        except Exception as e:
            logger.warning(f"Failed to update prices from gateway: {e}")

    def update_price(self, symbol: str, price: float):
        """Update single price (for testing/fallback)."""
        self.prices[symbol] = price
        self.stale[symbol] = False
        self.last_update[symbol] = datetime.now(timezone.utc)

    def is_price_valid(self, symbol: str) -> bool:
        """Check if price is valid for trading."""
        return (
            symbol in self.prices and
            self.prices[symbol] > 0 and
            not self.stale.get(symbol, True)
        )
        self.last_update[symbol] = datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE LOGGER
# ═══════════════════════════════════════════════════════════════════════════════

class TradeLogger:
    """Logs all trading activity to JSONL file with sha256 contracts"""

    def __init__(self, log_file: str):
        self.log_file = Path(log_file)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event_type: str, data: Dict) -> str:
        """Log trading event with sha256 contract"""
        # Use sha256 logging if available
        if log_trade_event is not None:
            return log_trade_event(event_type, data, trades_file=self.log_file)

        # Fallback to simple logging
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            "data": data,
        }

        with open(self.log_file, 'a') as f:
            f.write(json.dumps(entry) + '\n')
        return ""

    def log_signal(self, signal: ProcessedSignal, action: str, oracle_result: Dict = None) -> str:
        """Log signal processing with oracle decision"""
        data = {
            "action": action,
            "signal": asdict(signal),
        }
        if oracle_result:
            data["oracle"] = oracle_result
        return self.log("SIGNAL", data)

    def log_order(self, order_result, signal: ProcessedSignal, oracle_result: Dict = None) -> str:
        """Log order execution"""
        data = {
            "order": order_result.to_dict() if hasattr(order_result, 'to_dict') else str(order_result),
            "signal_id": signal.signal_id,
            "symbol": signal.symbol,
        }
        if oracle_result:
            data["oracle"] = oracle_result
        return self.log("ORDER", data)

    def log_position_close(self, position, reason: str, pnl: float) -> str:
        """Log position close"""
        return self.log("CLOSE", {
            "position_id": position.position_id if hasattr(position, 'position_id') else str(position),
            "symbol": position.symbol if hasattr(position, 'symbol') else "",
            "reason": reason,
            "pnl_pct": pnl,
        })

    def log_skip(self, symbol: str, reasons: List[str], oracle_result: Dict = None) -> str:
        """Log skipped signal with oracle reasoning"""
        data = {
            "symbol": symbol,
            "reasons": reasons,
        }
        if oracle_result:
            data["oracle"] = oracle_result
        return self.log("SKIP", data)


# ═══════════════════════════════════════════════════════════════════════════════
# CIRCUIT BREAKER
# ═══════════════════════════════════════════════════════════════════════════════

class CircuitBreaker:
    """Trading circuit breaker - stops trading on bad conditions"""
    
    def __init__(self):
        self.is_open = False
        self.trip_reason = ""
        self.trip_time = None
        
        # Tracking
        self.consecutive_losses = 0
        self.daily_loss = 0.0
        self.daily_trades = 0
        
        # Limits
        self.max_consecutive_losses = 3
        self.max_daily_loss = 50.0  # $50
        self.max_daily_trades = 50
    
    def check(self) -> bool:
        """Check if trading is allowed"""
        if self.is_open:
            return False
        
        if self.consecutive_losses >= self.max_consecutive_losses:
            self.trip(f"Consecutive losses: {self.consecutive_losses}")
            return False
        
        if self.daily_loss <= -self.max_daily_loss:
            self.trip(f"Daily loss limit: ${self.daily_loss:.2f}")
            return False
        
        if self.daily_trades >= self.max_daily_trades:
            self.trip(f"Daily trade limit: {self.daily_trades}")
            return False
        
        return True
    
    def trip(self, reason: str):
        """Trip the circuit breaker"""
        self.is_open = True
        self.trip_reason = reason
        self.trip_time = datetime.now(timezone.utc)
        logger.critical(f"🔴 CIRCUIT BREAKER TRIPPED: {reason}")
    
    def record_trade(self, pnl: float):
        """Record trade outcome"""
        self.daily_trades += 1
        self.daily_loss += pnl
        
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
    
    def reset(self):
        """Reset circuit breaker (manual)"""
        self.is_open = False
        self.trip_reason = ""
        self.trip_time = None
        logger.info("Circuit breaker reset")


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO TRADER
# ═══════════════════════════════════════════════════════════════════════════════

class AutoTrader:
    """
    Main autonomous trading system
    
    Connects all components into a single trading loop.
    """
    
    def __init__(self, config: AutoTraderConfig):
        self.config = config
        self.running = False

        # Initialize components
        self.mode = TradingMode[config.mode]
        self.signal_processor = SignalProcessor(config)
        self.price_feed = PriceFeed(config.gateway_url)
        self.trade_logger = TradeLogger(config.log_file)
        self.circuit_breaker = CircuitBreaker()

        # === EYE OF GOD AI ORACLE ===
        if EyeOfGod is not None:
            self.oracle = EyeOfGod(
                state_dir=str(Path(config.state_dir) / "oracle"),
                min_confidence=config.min_confidence
            )
            logger.info("Eye of God AI Oracle initialized")
        else:
            self.oracle = None
            logger.warning("Eye of God not available, using legacy SignalProcessor")

        # Order executor
        try:
            from order_executor import OrderExecutor
            self.executor = OrderExecutor(mode=self.mode)
        except ImportError:
            logger.warning("OrderExecutor not available, using DRY mode")
            self.executor = None

        # Stats
        self.stats = {
            "signals_received": 0,
            "signals_traded": 0,
            "signals_skipped": 0,
            "oracle_buys": 0,
            "oracle_skips": 0,
            "positions_opened": 0,
            "positions_closed": 0,
            "total_pnl": 0.0,
        }

        # Active symbols to watch
        self.watch_symbols: Set[str] = set()

        # Signal queue
        self.signal_queue: deque = deque(maxlen=100)

        logger.info(f"AutoTrader initialized in {config.mode} mode")
    
    def add_signal(self, raw_signal: Dict):
        """Add raw signal to processing queue"""
        self.signal_queue.append(raw_signal)
        self.stats["signals_received"] += 1
    
    def _process_signals(self):
        """Process queued signals with Eye of God AI Oracle"""
        # Update prices FIRST (P0: price verification requires fresh prices)
        self.price_feed.update_from_gateway()
        prices = self.price_feed.get_all_prices()

        while self.signal_queue:
            raw_signal = self.signal_queue.popleft()

            # Process signal with legacy processor (for metadata extraction)
            signal = self.signal_processor.process(raw_signal)
            if not signal:
                continue

            symbol = signal.symbol

            # === EYE OF GOD DECISION (P0: PRICE VERIFICATION) ===
            if self.oracle is not None:
                # Oracle handles P0 price check, BLACKLIST, WHITELIST, multi-factor scoring
                oracle_result = self.oracle.predict(raw_signal, prices)
                oracle_dict = oracle_result.to_dict()

                logger.info(f"Oracle: {symbol} | {oracle_result.action} | conf={oracle_result.confidence:.0%} | reasons={oracle_result.reasons}")

                if oracle_result.action == "SKIP":
                    self.trade_logger.log_skip(symbol, oracle_result.reasons, oracle_dict)
                    self.stats["signals_skipped"] += 1
                    self.stats["oracle_skips"] += 1
                    continue

                # Oracle says BUY - use oracle confidence and adaptive params
                signal.confidence = oracle_result.confidence
                if oracle_result.adaptive_params:
                    signal.target_pct *= oracle_result.adaptive_params.get("target_mult", 1.0)
                    signal.stop_pct *= oracle_result.adaptive_params.get("stop_mult", 1.0)

                self.stats["oracle_buys"] += 1

            else:
                # Fallback: Legacy SignalProcessor logic
                oracle_dict = None
                logger.info(f"Signal: {signal.symbol} | {signal.mode} | conf={signal.confidence:.0%}")

                # Check if should trade
                if not signal.should_trade():
                    self.trade_logger.log_signal(signal, "SKIP")
                    self.stats["signals_skipped"] += 1
                    continue

                # Check confidence threshold
                if signal.confidence < self.config.min_confidence:
                    self.trade_logger.log_signal(signal, "LOW_CONFIDENCE")
                    self.stats["signals_skipped"] += 1
                    logger.info(f"  → Skip: confidence {signal.confidence:.0%} < {self.config.min_confidence:.0%}")
                    continue

            # Check circuit breaker
            if not self.circuit_breaker.check():
                self.trade_logger.log_signal(signal, "CIRCUIT_BREAKER", oracle_dict)
                logger.warning(f"  → Skip: circuit breaker open")
                continue

            # === EXECUTE TRADE ===
            self._execute_trade(signal, oracle_dict)
    
    def _execute_trade(self, signal: ProcessedSignal, oracle_result: Dict = None):
        """Execute trade for signal with Eye of God oracle result"""
        # Calculate position size based on confidence and oracle adaptive params
        position_size = self.config.default_position_usdt
        if oracle_result and oracle_result.get("adaptive_params"):
            size_mult = oracle_result["adaptive_params"].get("size_mult", 1.0)
            position_size *= size_mult
        elif signal.confidence >= 0.90:
            position_size *= 1.5
        position_size = min(position_size, self.config.max_position_usdt)

        logger.info(f"🔥 EXECUTING: {signal.symbol} | conf={signal.confidence:.0%} | ${position_size:.2f}")

        # Subscribe to price feed
        self.watch_symbols.add(signal.symbol)
        self._subscribe_symbol(signal.symbol)

        # Execute order
        if self.executor:
            result = self.executor.market_buy(
                symbol=signal.symbol,
                quote_amount=position_size,
                target_pct=signal.target_pct,
                stop_pct=signal.stop_pct,
                timeout_seconds=signal.timeout_sec,
            )

            self.trade_logger.log_order(result, signal, oracle_result)

            if result.success:
                self.stats["signals_traded"] += 1
                self.stats["positions_opened"] += 1
                logger.info(f"  ✅ Order filled: {result.filled_quantity} @ {result.avg_price}")

                # Record outcome for oracle learning
                if self.oracle:
                    # Will be updated on position close with actual win/loss
                    pass
            else:
                logger.error(f"  ❌ Order failed: {result.error}")
        else:
            # DRY mode without executor
            logger.info(f"  [DRY] Would buy ${position_size:.2f} of {signal.symbol}")
            self.trade_logger.log_signal(signal, "DRY_TRADE", oracle_result)
            self.stats["signals_traded"] += 1
    
    def _check_positions(self):
        """Check all open positions for target/stop/timeout"""
        if not self.executor:
            return

        # Update prices
        self.price_feed.update_from_gateway()
        prices = self.price_feed.get_all_prices()

        # Check positions
        closed = self.executor.check_positions(prices)

        for close_info in closed:
            position = close_info["position"]
            reason = close_info["reason"]
            pnl = position.realized_pnl if hasattr(position, 'realized_pnl') else 0

            self.trade_logger.log_position_close(position, reason, pnl)
            self.stats["positions_closed"] += 1
            self.stats["total_pnl"] += pnl

            # Update circuit breaker
            pnl_usdt = pnl / 100 * position.notional_value if hasattr(position, 'notional_value') else 0
            self.circuit_breaker.record_trade(pnl_usdt)

            # === EYE OF GOD LEARNING ===
            # Record outcome for adaptive calibration
            if self.oracle:
                symbol = position.symbol if hasattr(position, 'symbol') else ""
                is_win = pnl > 0
                self.oracle.record_outcome(symbol, is_win, pnl)
                logger.info(f"Oracle learning: {symbol} | {'WIN' if is_win else 'LOSS'} | PnL={pnl:+.2f}%")

            logger.info(f"Position closed: {position.symbol} | {reason} | PnL: {pnl:+.2f}%")
    
    def _subscribe_symbol(self, symbol: str):
        """Subscribe to price feed for symbol"""
        try:
            resp = httpx.post(
                f"{self.config.gateway_url}/price-feed/subscribe",
                json=[symbol],
                timeout=5
            )
            if resp.status_code != 200:
                logger.warning(f"Failed to subscribe to {symbol}")
        except Exception as e:
            logger.warning(f"Subscribe error: {e}")
    
    def _poll_gateway_signals(self):
        """Poll AI Gateway for new signals"""
        try:
            # This would be connected to MoonBot watcher or gateway endpoint
            # For now, we rely on external signal injection
            pass
        except Exception as e:
            logger.warning(f"Poll error: {e}")
    
    def run_once(self):
        """Run single iteration of trading loop"""
        # Process queued signals
        self._process_signals()
        
        # Check open positions
        self._check_positions()
        
        # Poll for new signals
        self._poll_gateway_signals()
    
    def run(self):
        """Run main trading loop"""
        self.running = True
        logger.info(f"🚀 AutoTrader starting in {self.config.mode} mode")
        
        if self.mode == TradingMode.LIVE:
            logger.warning("⚠️ LIVE MODE - REAL MONEY AT RISK!")
        
        # Setup signal handler
        def signal_handler(sig, frame):
            logger.info("Shutdown signal received")
            self.running = False
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        try:
            while self.running:
                try:
                    self.run_once()
                except Exception as e:
                    logger.error(f"Loop error: {e}")
                
                time.sleep(self.config.loop_interval_sec)
                
        finally:
            logger.info("AutoTrader stopped")
            self._print_stats()
    
    def _print_stats(self):
        """Print trading statistics"""
        print("\n" + "=" * 60)
        print("  AUTOTRADER SESSION SUMMARY")
        print("=" * 60)
        print(f"  Mode:             {self.config.mode}")
        print(f"  Eye of God:       {'ACTIVE' if self.oracle else 'DISABLED'}")
        print(f"  Signals Received: {self.stats['signals_received']}")
        print(f"  Signals Traded:   {self.stats['signals_traded']}")
        print(f"  Signals Skipped:  {self.stats['signals_skipped']}")
        print(f"  Oracle BUYs:      {self.stats['oracle_buys']}")
        print(f"  Oracle SKIPs:     {self.stats['oracle_skips']}")
        print(f"  Positions Opened: {self.stats['positions_opened']}")
        print(f"  Positions Closed: {self.stats['positions_closed']}")
        print(f"  Total PnL:        {self.stats['total_pnl']:+.2f}%")
        if self.oracle:
            print(f"  Oracle Calibration: {self.oracle.calibration:.2f}")
        print("=" * 60 + "\n")
    
    def get_status(self) -> Dict:
        """Get current status"""
        status = {
            "mode": self.config.mode,
            "running": self.running,
            "circuit_breaker_open": self.circuit_breaker.is_open,
            "stats": self.stats,
            "open_positions": len(self.executor.get_open_positions()) if self.executor else 0,
            "watched_symbols": list(self.watch_symbols),
            "oracle_enabled": self.oracle is not None,
        }
        if self.oracle:
            status["oracle_calibration"] = self.oracle.calibration
            status["oracle_symbol_stats"] = len(self.oracle.symbol_stats)
        return status


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP API FOR SIGNAL INJECTION
# ═══════════════════════════════════════════════════════════════════════════════

def create_api(autotrader: AutoTrader):
    """Create HTTP API for signal injection"""
    try:
        from fastapi import FastAPI, HTTPException
        from pydantic import BaseModel
        import uvicorn
    except ImportError:
        logger.warning("FastAPI not available, API disabled")
        return None
    
    app = FastAPI(title="HOPE AI AutoTrader API")
    
    class SignalRequest(BaseModel):
        symbol: str
        strategy: str = "unknown"
        direction: str = "Long"
        price: float = 0
        buys_per_sec: float = 0
        delta_pct: float = 0
        vol_raise_pct: float = 0
    
    @app.post("/signal")
    async def inject_signal(signal: SignalRequest):
        autotrader.add_signal(signal.dict())
        return {"status": "queued", "queue_size": len(autotrader.signal_queue)}
    
    @app.get("/status")
    async def get_status():
        return autotrader.get_status()
    
    @app.post("/circuit-breaker/reset")
    async def reset_circuit_breaker():
        autotrader.circuit_breaker.reset()
        return {"status": "reset"}
    
    return app


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="HOPE AI AutoTrader")
    parser.add_argument("--mode", type=str, default="DRY", choices=["DRY", "TESTNET", "LIVE"])
    parser.add_argument("--confirm", action="store_true", help="Confirm LIVE mode")
    parser.add_argument("--gateway", type=str, default="http://127.0.0.1:8100")
    parser.add_argument("--position-size", type=float, default=10.0, help="Default position size USD")
    parser.add_argument("--api-port", type=int, default=8200, help="API port for signal injection")
    parser.add_argument("--no-api", action="store_true", help="Disable API")
    
    args = parser.parse_args()
    
    # Safety check for LIVE mode
    if args.mode == "LIVE" and not args.confirm:
        print("⚠️ LIVE MODE requires --confirm flag")
        print("This will trade with REAL MONEY!")
        sys.exit(1)
    
    if args.mode == "LIVE":
        confirm = input("⚠️ LIVE MODE - Type 'I UNDERSTAND' to confirm: ")
        if confirm != "I UNDERSTAND":
            print("Cancelled.")
            sys.exit(1)

    # Single-instance check (prevents port conflicts)
    from core.lockfile import ProcessLock
    lock = ProcessLock("autotrader")
    if not lock.acquire():
        owner_pid = lock.get_owner()
        print(f"ERROR: Another AutoTrader instance is running (PID {owner_pid})")
        print("Kill the other instance first or wait for it to exit.")
        sys.exit(1)

    # Create config
    config = AutoTraderConfig(
        mode=args.mode,
        gateway_url=args.gateway,
        default_position_usdt=args.position_size,
    )
    
    # Create autotrader
    autotrader = AutoTrader(config)
    
    # Start API in background
    if not args.no_api:
        app = create_api(autotrader)
        if app:
            def run_api():
                import uvicorn
                uvicorn.run(app, host="127.0.0.1", port=args.api_port, log_level="warning")
            
            api_thread = threading.Thread(target=run_api, daemon=True)
            api_thread.start()
            logger.info(f"API started on http://127.0.0.1:{args.api_port}")
    
    # Run main loop
    autotrader.run()


if __name__ == "__main__":
    main()
