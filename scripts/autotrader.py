# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 14:20:00 UTC
# Modified by: Claude (opus-4.5)
# Modified at: 2026-02-02 17:30:00 UTC
# Purpose: HOPE AI AutoTrader - Complete autonomous trading loop
# Changes: Event Bus P0 integration (SignalReceived, Decision, OrderIntent, Fill events)
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

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Centralized secrets (cross-platform)
try:
    from core.secrets import SECRETS_PATH
except ImportError:
    import platform
    if platform.system() == "Windows":
        SECRETS_PATH = Path("C:/secrets/hope.env")
    else:
        SECRETS_PATH = Path("/opt/hope/secrets/hope.env")

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

# Binance client for position verification
try:
    from binance.client import Client as BinanceClient
    from dotenv import load_dotenv
    import os
    BINANCE_AVAILABLE = True
except ImportError:
    BINANCE_AVAILABLE = False
    BinanceClient = None

# Import Eye of God V3 for two-chamber decision making
try:
    from eye_of_god_v3 import EyeOfGodV3, DecisionAction
    EYE_OF_GOD_AVAILABLE = True
except ImportError:
    EYE_OF_GOD_AVAILABLE = False
    EyeOfGodV3 = None
    logging.warning("EyeOfGodV3 not available, using simple SignalProcessor")

# Import Event Bus for event-driven architecture (P0 integration)
try:
    from core.events.integration import get_publisher
    EVENT_BUS_AVAILABLE = True
except ImportError:
    EVENT_BUS_AVAILABLE = False
    get_publisher = None
    logging.warning("Event Bus not available")

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

    # === COOLDOWN SETTINGS (NEW - FIX #4) ===
    symbol_cooldown_sec: int = 300         # 5 min cooldown after closing position
    max_same_symbol_per_hour: int = 2      # Max trades per symbol per hour

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
    """Processes raw MoonBot signals into trading decisions"""
    
    def __init__(self, config: AutoTraderConfig):
        self.config = config
        self.recent_signals: deque = deque(maxlen=100)  # Prevent duplicate processing
        
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
        
        # === CLASSIFICATION ===
        reasons = []
        mode = "SKIP"
        confidence = 0.0
        target_pct = self.config.default_target_pct
        stop_pct = self.config.default_stop_pct
        timeout = self.config.default_timeout_sec
        
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
    """Real-time price feed from Gateway or Binance"""
    
    def __init__(self, gateway_url: str):
        self.gateway_url = gateway_url
        self.prices: Dict[str, float] = {}
        self.last_update: Dict[str, datetime] = {}
        self.client = httpx.Client(timeout=5)
    
    def get_price(self, symbol: str) -> float:
        """Get current price for symbol"""
        return self.prices.get(symbol, 0)
    
    def get_all_prices(self) -> Dict[str, float]:
        """Get all current prices"""
        return self.prices.copy()
    
    def update_from_gateway(self):
        """Update prices from AI Gateway"""
        try:
            resp = self.client.get(f"{self.gateway_url}/price-feed/prices")
            if resp.status_code == 200:
                data = resp.json()
                now = datetime.now(timezone.utc)
                
                for symbol, price_data in data.get("prices", {}).items():
                    if isinstance(price_data, dict):
                        self.prices[symbol] = float(price_data.get("price", 0))
                    else:
                        self.prices[symbol] = float(price_data)
                    self.last_update[symbol] = now
                    
        except Exception as e:
            logger.warning(f"Failed to update prices from gateway: {e}")
    
    def update_price(self, symbol: str, price: float):
        """Update single price"""
        self.prices[symbol] = price
        self.last_update[symbol] = datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE LOGGER
# ═══════════════════════════════════════════════════════════════════════════════

class TradeLogger:
    """Logs all trading activity to JSONL file"""
    
    def __init__(self, log_file: str):
        self.log_file = Path(log_file)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
    
    def log(self, event_type: str, data: Dict):
        """Log trading event"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            "data": data,
        }
        
        with open(self.log_file, 'a') as f:
            f.write(json.dumps(entry) + '\n')
    
    def log_signal(self, signal: ProcessedSignal, action: str):
        """Log signal processing"""
        self.log("SIGNAL", {
            "action": action,
            "signal": asdict(signal),
        })
    
    def log_order(self, order_result, signal: ProcessedSignal):
        """Log order execution"""
        self.log("ORDER", {
            "order": order_result.to_dict() if hasattr(order_result, 'to_dict') else str(order_result),
            "signal_id": signal.signal_id,
            "symbol": signal.symbol,
        })
    
    def log_position_close(self, position, reason: str, pnl: float):
        """Log position close"""
        self.log("CLOSE", {
            "position_id": position.position_id if hasattr(position, 'position_id') else str(position),
            "symbol": position.symbol if hasattr(position, 'symbol') else "",
            "reason": reason,
            "pnl_pct": pnl,
        })


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
        self.signal_processor = SignalProcessor(config)  # Simple fallback
        self.price_feed = PriceFeed(config.gateway_url)
        self.trade_logger = TradeLogger(config.log_file)
        self.circuit_breaker = CircuitBreaker()

        # Eye of God V3 - Two-chamber decision system (PREFERRED)
        self.eye_of_god = None
        if EYE_OF_GOD_AVAILABLE:
            try:
                self.eye_of_god = EyeOfGodV3(base_position_size=config.default_position_usdt)
                logger.info("EyeOfGodV3 initialized - using two-chamber decisions")
            except Exception as e:
                logger.warning(f"EyeOfGodV3 init failed: {e}, using simple processor")

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
            "positions_opened": 0,
            "positions_closed": 0,
            "total_pnl": 0.0,
        }

        # Active symbols to watch
        self.watch_symbols: Set[str] = set()

        # Signal queue
        self.signal_queue: deque = deque(maxlen=100)

        # === COOLDOWN TRACKING (NEW - FIX #4) ===
        # Maps symbol -> timestamp when it was last closed
        self.symbol_cooldown: Dict[str, datetime] = {}
        # Maps symbol -> list of trade timestamps in last hour
        self.symbol_trade_times: Dict[str, List[datetime]] = {}
        # Currently open positions (symbol set)
        self.open_positions: Set[str] = set()

        # Binance client for verification
        self.binance_client = None
        self._init_binance_client()

        # CRITICAL: Sync state with Binance on startup
        self._sync_with_binance()

        logger.info(f"AutoTrader initialized in {config.mode} mode")

    def _init_binance_client(self):
        """Initialize Binance client for position verification."""
        if not BINANCE_AVAILABLE:
            logger.warning("Binance client not available - cannot verify positions")
            return
        try:
            load_dotenv(SECRETS_PATH)
            api_key = os.getenv('BINANCE_API_KEY')
            api_secret = os.getenv('BINANCE_API_SECRET')
            if api_key and api_secret:
                self.binance_client = BinanceClient(api_key, api_secret)
                logger.info("Binance client initialized for position verification")
            else:
                logger.warning("Binance API keys not found")
        except Exception as e:
            logger.error(f"Failed to init Binance client: {e}")

    def _sync_with_binance(self):
        """
        CRITICAL: Sync internal state with real Binance positions.

        This prevents showing fake positions that don't exist on Binance.
        Called on startup and periodically.
        """
        if not self.binance_client:
            logger.warning("Cannot sync - no Binance client")
            return

        try:
            # Get real account balances
            account = self.binance_client.get_account()
            real_positions = set()

            # Assets to IGNORE (not trading positions, just holdings)
            IGNORE_ASSETS = {'USDT', 'USDC', 'BNB', 'AUD', 'SLF', 'FDUSD', 'BUSD', 'EUR', 'GBP', 'RUB'}

            for balance in account['balances']:
                asset = balance['asset']
                free = float(balance['free'])
                locked = float(balance['locked'])

                # Only consider assets that are NOT in ignore list
                # and have meaningful balance (> 0.001)
                if asset not in IGNORE_ASSETS and (free > 0.001 or locked > 0.001):
                    symbol = f"{asset}USDT"
                    real_positions.add(symbol)

            # Compare with internal state
            internal_positions = self.open_positions.copy()

            # Positions in internal state but NOT on Binance = STALE, remove them
            stale_positions = internal_positions - real_positions
            for symbol in stale_positions:
                logger.warning(f"[SYNC] Removing stale position: {symbol} (not on Binance)")
                self.open_positions.discard(symbol)

            # Positions on Binance but NOT in internal state = add them
            missing_positions = real_positions - internal_positions
            for symbol in missing_positions:
                logger.info(f"[SYNC] Adding missing position: {symbol} (found on Binance)")
                self.open_positions.add(symbol)

            if stale_positions or missing_positions:
                logger.info(f"[SYNC] State synchronized. Open positions: {self.open_positions}")
            else:
                logger.info(f"[SYNC] State OK. Open positions: {len(self.open_positions)}")

        except Exception as e:
            logger.error(f"[SYNC] Failed to sync with Binance: {e}")

    def _run_protocol_check(self):
        """
        Run HOPE Protocol Check - verifies system health and recalculates position sizes.

        Called every 5 minutes to ensure:
        1. All services are running (STARTUP PROTOCOL)
        2. Position sizes are adjusted for current balance (CYCLIC TRADING PROTOCOL)
        """
        try:
            from protocol_checker import ProtocolChecker

            checker = ProtocolChecker()
            results = checker.run_full_check()

            if results.get("overall_status") == "PASS":
                logger.info("[PROTOCOL] Check PASS - system healthy")

                # Update config if position size changed
                cyclic = results.get("cyclic_protocol", {})
                if cyclic.get("can_trade"):
                    new_size = cyclic.get("position_size_usd", 0)
                    if new_size > 0 and hasattr(self.config, 'default_position_usdt'):
                        old_size = self.config.default_position_usdt
                        if abs(new_size - old_size) > 0.5:  # Only if changed by >$0.50
                            self.config.default_position_usdt = new_size
                            logger.info(f"[PROTOCOL] Position size updated: ${old_size:.2f} → ${new_size:.2f}")
            else:
                logger.warning(f"[PROTOCOL] Check FAIL - review system")

        except ImportError:
            logger.debug("[PROTOCOL] protocol_checker not available")
        except Exception as e:
            logger.error(f"[PROTOCOL] Check error: {e}")
    
    def add_signal(self, raw_signal: Dict):
        """Add raw signal to processing queue"""
        symbol = raw_signal.get("symbol", "UNKNOWN")

        # Emit SignalReceivedEvent
        corr_id = None
        if EVENT_BUS_AVAILABLE and get_publisher:
            try:
                publisher = get_publisher()
                corr_id = publisher.signal_received(
                    symbol=symbol,
                    source_type=raw_signal.get("source", "moonbot"),
                    raw_data=raw_signal,
                )
                raw_signal["_correlation_id"] = corr_id  # Attach for tracing
            except Exception as e:
                logger.warning(f"Event publish failed: {e}")

        self.signal_queue.append(raw_signal)
        self.stats["signals_received"] += 1

    def _is_symbol_blocked(self, symbol: str) -> tuple[bool, str]:
        """
        Check if symbol is blocked from trading.

        Returns: (is_blocked, reason)

        Blocks if:
        1. Symbol already has open position
        2. Symbol is in cooldown after recent close
        3. Symbol exceeded max trades per hour
        """
        now = datetime.now(timezone.utc)

        # Block 1: Already has open position
        if symbol in self.open_positions:
            return True, "ALREADY_HAS_POSITION"

        # Block 2: Cooldown after close
        if symbol in self.symbol_cooldown:
            cooldown_until = self.symbol_cooldown[symbol] + timedelta(seconds=self.config.symbol_cooldown_sec)
            if now < cooldown_until:
                remaining = (cooldown_until - now).total_seconds()
                return True, f"COOLDOWN_{int(remaining)}s_remaining"

        # Block 3: Max trades per hour
        if symbol in self.symbol_trade_times:
            # Clean old entries (older than 1 hour)
            hour_ago = now - timedelta(hours=1)
            self.symbol_trade_times[symbol] = [
                t for t in self.symbol_trade_times[symbol] if t > hour_ago
            ]
            if len(self.symbol_trade_times[symbol]) >= self.config.max_same_symbol_per_hour:
                return True, f"MAX_TRADES_PER_HOUR_{len(self.symbol_trade_times[symbol])}"

        return False, ""

    def _record_trade_open(self, symbol: str):
        """Record that a trade was opened for symbol."""
        now = datetime.now(timezone.utc)
        self.open_positions.add(symbol)
        if symbol not in self.symbol_trade_times:
            self.symbol_trade_times[symbol] = []
        self.symbol_trade_times[symbol].append(now)
        logger.info(f"[COOLDOWN] Recorded open: {symbol}")

    def _record_trade_close(self, symbol: str):
        """Record that a trade was closed for symbol - starts cooldown."""
        now = datetime.now(timezone.utc)
        self.open_positions.discard(symbol)
        self.symbol_cooldown[symbol] = now
        logger.info(f"[COOLDOWN] Recorded close: {symbol} - cooldown {self.config.symbol_cooldown_sec}s")
    
    def _process_signals(self):
        """Process queued signals using Eye of God V3 or fallback SignalProcessor"""
        while self.signal_queue:
            raw_signal = self.signal_queue.popleft()
            logger.info(f"Processing signal: {raw_signal.get('symbol')} | eye_of_god={self.eye_of_god is not None}")

            # === TWO-CHAMBER DECISION (Eye of God V3) ===
            if self.eye_of_god is not None:
                decision = self._process_with_eye_of_god(raw_signal)
                if decision is None:
                    logger.warning("Eye of God returned None")
                    continue
                logger.info(f"Decision: {decision.action} | conf={decision.confidence:.0%}")
                if decision.action == "SKIP":  # action is str, not Enum
                    self.stats["signals_skipped"] += 1
                    logger.info(f"SKIP reasons: {decision.reasons[:3]}")
                    continue
                # BUY decision - execute
                self._execute_trade_from_eye(decision)
                continue

            # === FALLBACK: Simple SignalProcessor ===
            signal = self.signal_processor.process(raw_signal)
            if not signal:
                continue

            logger.info(f"Signal: {signal.symbol} | {signal.mode} | conf={signal.confidence:.0%}")

            if not signal.should_trade():
                self.trade_logger.log_signal(signal, "SKIP")
                self.stats["signals_skipped"] += 1
                continue

            if signal.confidence < self.config.min_confidence:
                self.trade_logger.log_signal(signal, "LOW_CONFIDENCE")
                self.stats["signals_skipped"] += 1
                logger.info(f"  → Skip: confidence {signal.confidence:.0%} < {self.config.min_confidence:.0%}")
                continue

            if not self.circuit_breaker.check():
                self.trade_logger.log_signal(signal, "CIRCUIT_BREAKER")
                logger.warning(f"  → Skip: circuit breaker open")
                continue

            self._execute_trade(signal)

    def _process_with_eye_of_god(self, raw_signal: Dict):
        """
        Process signal with Eye of God V3 two-chamber system.

        Alpha Committee: "Do we WANT to trade?"
        Risk Committee: "Are we ALLOWED to trade?"
        """
        symbol = raw_signal.get("symbol", "")
        if not symbol:
            return None

        # Update price in Eye of God
        price = raw_signal.get("price")
        if price:
            self.eye_of_god.update_price(symbol, float(price))

        # Get two-chamber decision
        decision = self.eye_of_god.decide(raw_signal)

        # Log decision
        logger.info(
            f"EYE: {decision.symbol} | {decision.action} | "
            f"conf={decision.confidence:.0%} | mode={decision.mode} | "
            f"reasons={decision.reasons[:2]}"
        )

        # Emit DecisionEvent
        corr_id = raw_signal.get("_correlation_id")
        if EVENT_BUS_AVAILABLE and get_publisher and corr_id:
            try:
                publisher = get_publisher()
                # Split reasons into alpha/risk (Eye of God provides combined)
                alpha_reasons = [r for r in decision.reasons if not r.startswith("RISK_")]
                risk_reasons = [r for r in decision.reasons if r.startswith("RISK_")]
                publisher.decision(
                    corr_id=corr_id,
                    symbol=decision.symbol,
                    action=decision.action,
                    confidence=decision.confidence,
                    alpha_reasons=alpha_reasons[:3],
                    risk_reasons=risk_reasons[:3],
                    mode=decision.mode,
                    position_size_usdt=decision.position_size_usdt,
                    target_pct=decision.target_pct,
                    stop_pct=decision.stop_pct,
                    timeout_sec=decision.timeout_sec,
                )
            except Exception as e:
                logger.warning(f"Decision event publish failed: {e}")

        # Attach correlation_id to decision for downstream tracing
        decision._correlation_id = corr_id

        return decision

    def _execute_trade_from_eye(self, decision):
        """Execute trade based on Eye of God decision."""
        # === COOLDOWN CHECK (FIX #4) ===
        is_blocked, block_reason = self._is_symbol_blocked(decision.symbol)
        if is_blocked:
            logger.warning(f"🚫 BLOCKED: {decision.symbol} | {block_reason}")
            self.stats["signals_skipped"] += 1
            return

        logger.info(f"🔥 EYE DECISION: BUY {decision.symbol} | ${decision.position_size_usdt}")

        # Subscribe to price feed
        self.watch_symbols.add(decision.symbol)
        self._subscribe_symbol(decision.symbol)

        # === FEE-ADJUSTED TARGETS ===
        # Binance VIP0: 0.1% taker fee per side = 0.2% round-trip
        TAKER_FEE_PCT = 0.10
        fee_adjusted_target = decision.target_pct + (TAKER_FEE_PCT * 2)  # Add RT fee to target
        fee_adjusted_stop = decision.stop_pct - TAKER_FEE_PCT  # Subtract exit fee from stop

        logger.info(
            f"  Targets: raw={decision.target_pct:+.2f}%/{decision.stop_pct:+.2f}% | "
            f"fee-adj={fee_adjusted_target:+.2f}%/{fee_adjusted_stop:+.2f}%"
        )

        # Get correlation_id for event tracing
        corr_id = getattr(decision, '_correlation_id', None)

        # Emit OrderIntentEvent
        if EVENT_BUS_AVAILABLE and get_publisher and corr_id:
            try:
                publisher = get_publisher()
                publisher.order_intent(
                    corr_id=corr_id,
                    symbol=decision.symbol,
                    side="BUY",
                    order_type="MARKET",
                    quantity=0,  # Will be calculated by executor
                    price=None,
                    take_profit=fee_adjusted_target,
                    stop_loss=abs(fee_adjusted_stop),
                    position_size_usdt=decision.position_size_usdt,
                )
            except Exception as e:
                logger.warning(f"OrderIntent event failed: {e}")

        # Execute order
        if self.executor:
            result = self.executor.market_buy(
                symbol=decision.symbol,
                quote_amount=decision.position_size_usdt,
                target_pct=fee_adjusted_target,  # Use decision target + fee adjustment
                stop_pct=fee_adjusted_stop,       # Use decision stop - fee adjustment
                timeout_seconds=decision.timeout_sec,
                trailing_activation_pct=0.5,      # Activate trailing at +0.5%
                trailing_delta_pct=0.3,           # Trail by 0.3%
            )

            if result.success:
                self.stats["signals_traded"] += 1
                self.stats["positions_opened"] += 1
                # === RECORD TRADE OPEN (FIX #4) ===
                self._record_trade_open(decision.symbol)
                logger.info(f"  ✅ Order filled: {result.filled_quantity} @ {result.avg_price}")

                # Emit FillEvent
                if EVENT_BUS_AVAILABLE and get_publisher and corr_id:
                    try:
                        publisher = get_publisher()
                        order_id = getattr(result, 'order_id', str(int(time.time()*1000)))
                        publisher.fill(
                            corr_id=corr_id,
                            order_id=str(order_id),
                            symbol=decision.symbol,
                            side="BUY",
                            filled_qty=result.filled_quantity,
                            avg_price=result.avg_price,
                            commission=getattr(result, 'commission', 0),
                        )
                    except Exception as e:
                        logger.warning(f"Fill event failed: {e}")

                # === WATCHDOG REGISTRATION (CRITICAL - FAIL-CLOSED) ===
                try:
                    from scripts.position_watchdog import register_position_for_watching
                    register_position_for_watching(
                        position_id=f"pos_{result.order_id if hasattr(result, 'order_id') else int(time.time()*1000)}",
                        symbol=decision.symbol,
                        entry_price=result.avg_price,
                        quantity=result.filled_quantity,
                        target_pct=fee_adjusted_target,
                        stop_pct=abs(fee_adjusted_stop),
                        timeout_sec=decision.timeout_sec,
                    )
                    logger.info(f"  ✅ Registered with watchdog")
                except Exception as e:
                    logger.error(f"  ❌ CRITICAL: Watchdog registration failed: {e}")
                    # FAIL-CLOSED: Try emergency close if watchdog fails
                    try:
                        if hasattr(self.executor, 'emergency_close'):
                            self.executor.emergency_close(decision.symbol)
                            self._record_trade_close(decision.symbol)  # Record close
                            logger.warning(f"  ⚠️ Emergency close triggered for {decision.symbol}")
                    except Exception as close_err:
                        logger.critical(f"  🛑 EMERGENCY CLOSE ALSO FAILED: {close_err}")
            else:
                logger.error(f"  ❌ Order failed: {result.error}")
        else:
            # DRY mode
            logger.info(f"  [DRY] Would buy ${decision.position_size_usdt} of {decision.symbol}")
            self._record_trade_open(decision.symbol)  # Track even in DRY mode
            self.stats["signals_traded"] += 1
    
    def _execute_trade(self, signal: ProcessedSignal):
        """Execute trade for signal (fallback when Eye of God not available)"""
        # === COOLDOWN CHECK (FIX #4) ===
        is_blocked, block_reason = self._is_symbol_blocked(signal.symbol)
        if is_blocked:
            logger.warning(f"🚫 BLOCKED: {signal.symbol} | {block_reason}")
            self.stats["signals_skipped"] += 1
            return

        logger.info(f"🔥 EXECUTING: {signal.symbol} | {signal.mode} | ${self.config.default_position_usdt}")

        # Calculate position size based on confidence
        position_size = self.config.default_position_usdt
        if signal.confidence >= 0.90:
            position_size = min(position_size * 1.5, self.config.max_position_usdt)

        # === FEE-ADJUSTED TARGETS ===
        TAKER_FEE_PCT = 0.10  # Binance VIP0: 0.1%
        fee_adjusted_target = signal.target_pct + (TAKER_FEE_PCT * 2)  # Add RT fee
        fee_adjusted_stop = signal.stop_pct - TAKER_FEE_PCT  # Subtract exit fee

        logger.info(
            f"  Targets: raw={signal.target_pct:+.2f}%/{signal.stop_pct:+.2f}% | "
            f"fee-adj={fee_adjusted_target:+.2f}%/{fee_adjusted_stop:+.2f}%"
        )

        # Subscribe to price feed
        self.watch_symbols.add(signal.symbol)
        self._subscribe_symbol(signal.symbol)

        # Execute order
        if self.executor:
            result = self.executor.market_buy(
                symbol=signal.symbol,
                quote_amount=position_size,
                target_pct=fee_adjusted_target,
                stop_pct=fee_adjusted_stop,
                timeout_seconds=signal.timeout_sec,
                trailing_activation_pct=0.5,  # Activate trail at +0.5%
                trailing_delta_pct=0.3,       # Trail by 0.3%
            )

            self.trade_logger.log_order(result, signal)

            if result.success:
                self.stats["signals_traded"] += 1
                self.stats["positions_opened"] += 1
                # === RECORD TRADE OPEN (FIX #4) ===
                self._record_trade_open(signal.symbol)
                logger.info(f"  ✅ Order filled: {result.filled_quantity} @ {result.avg_price}")

                # === WATCHDOG REGISTRATION (CRITICAL - FAIL-CLOSED) ===
                try:
                    from scripts.position_watchdog import register_position_for_watching
                    register_position_for_watching(
                        position_id=f"pos_{result.order_id if hasattr(result, 'order_id') else int(time.time()*1000)}",
                        symbol=signal.symbol,
                        entry_price=result.avg_price,
                        quantity=result.filled_quantity,
                        target_pct=fee_adjusted_target,
                        stop_pct=abs(fee_adjusted_stop),
                        timeout_sec=signal.timeout_sec,
                    )
                    logger.info(f"  ✅ Registered with watchdog")
                except Exception as e:
                    logger.error(f"  ❌ CRITICAL: Watchdog registration failed: {e}")
                    try:
                        if hasattr(self.executor, 'emergency_close'):
                            self.executor.emergency_close(signal.symbol)
                            self._record_trade_close(signal.symbol)  # Record close
                            logger.warning(f"  ⚠️ Emergency close triggered for {signal.symbol}")
                    except Exception as close_err:
                        logger.critical(f"  🛑 EMERGENCY CLOSE ALSO FAILED: {close_err}")
            else:
                logger.error(f"  ❌ Order failed: {result.error}")
        else:
            # DRY mode without executor
            logger.info(f"  [DRY] Would buy ${position_size} of {signal.symbol}")
            self._record_trade_open(signal.symbol)  # Track even in DRY mode
            self.trade_logger.log_signal(signal, "DRY_TRADE")
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
            symbol = position.symbol if hasattr(position, 'symbol') else ""
            pnl = position.realized_pnl if hasattr(position, 'realized_pnl') else 0

            self.trade_logger.log_position_close(position, reason, pnl)
            self.stats["positions_closed"] += 1
            self.stats["total_pnl"] += pnl

            # === RECORD TRADE CLOSE - START COOLDOWN (FIX #4) ===
            if symbol:
                self._record_trade_close(symbol)

            # Update circuit breaker
            pnl_usdt = pnl / 100 * position.notional_value if hasattr(position, 'notional_value') else 0
            self.circuit_breaker.record_trade(pnl_usdt)

            logger.info(f"Position closed: {symbol} | {reason} | PnL: {pnl:+.2f}%")
    
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
        
        sync_counter = 0
        protocol_counter = 0
        SYNC_INTERVAL = 60  # Sync with Binance every 60 loops (~1 min at 1s interval)
        PROTOCOL_INTERVAL = 300  # Protocol check every 300 loops (~5 min)

        try:
            while self.running:
                try:
                    self.run_once()

                    # Periodic Binance sync to prevent stale state
                    sync_counter += 1
                    if sync_counter >= SYNC_INTERVAL:
                        self._sync_with_binance()
                        sync_counter = 0

                    # Periodic protocol check + position size recalculation
                    protocol_counter += 1
                    if protocol_counter >= PROTOCOL_INTERVAL:
                        self._run_protocol_check()
                        protocol_counter = 0

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
        print(f"  Signals Received: {self.stats['signals_received']}")
        print(f"  Signals Traded:   {self.stats['signals_traded']}")
        print(f"  Signals Skipped:  {self.stats['signals_skipped']}")
        print(f"  Positions Opened: {self.stats['positions_opened']}")
        print(f"  Positions Closed: {self.stats['positions_closed']}")
        print(f"  Total PnL:        {self.stats['total_pnl']:+.2f}%")
        print("=" * 60 + "\n")
    
    def get_status(self) -> Dict:
        """Get current status - uses Binance-synced open_positions"""
        return {
            "mode": self.config.mode,
            "running": self.running,
            "circuit_breaker_open": self.circuit_breaker.is_open,
            "stats": self.stats,
            "open_positions": len(self.open_positions),  # Synced with Binance
            "open_symbols": list(self.open_positions),   # Actual symbols
            "watched_symbols": list(self.watch_symbols),
            "binance_synced": self.binance_client is not None,
        }


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
        timestamp: str = ""  # ISO format, auto-filled if empty
        strategy: str = "unknown"
        direction: str = "Long"
        price: float = 0
        buys_per_sec: float = 0
        delta_pct: float = 0
        vol_raise_pct: float = 0
        daily_volume_m: float = 100  # Default daily volume in millions
        # Momentum detection fields - CRITICAL for Eye of God V3
        signal_type: str = ""  # MOMENTUM_24H, TRENDING, etc.
        type: str = ""  # Alias for compatibility
        ai_override: bool = False  # Force trade despite low score
        confidence: float = 0.5  # AI confidence score
    
    @app.post("/signal")
    async def inject_signal(signal: SignalRequest):
        from datetime import datetime, timezone
        data = signal.model_dump()
        # Auto-fill timestamp if empty
        if not data.get("timestamp"):
            data["timestamp"] = datetime.now(timezone.utc).isoformat()
        autotrader.add_signal(data)
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
    parser.add_argument("--yes", "-y", action="store_true", help="Skip interactive confirmation (for scripts)")
    parser.add_argument("--gateway", type=str, default="http://127.0.0.1:8100")
    parser.add_argument("--position-size", type=float, default=10.0, help="Default position size USD")
    parser.add_argument("--api-port", type=int, default=8200, help="API port for signal injection")
    parser.add_argument("--no-api", action="store_true", help="Disable API")

    args = parser.parse_args()

    # Safety check for LIVE mode
    if args.mode == "LIVE" and not args.confirm:
        print("[WARNING] LIVE MODE requires --confirm flag")
        print("This will trade with REAL MONEY!")
        sys.exit(1)

    if args.mode == "LIVE" and not args.yes:
        confirm = input("[WARNING] LIVE MODE - Type 'I UNDERSTAND' to confirm: ")
        if confirm != "I UNDERSTAND":
            print("Cancelled.")
            sys.exit(1)
    elif args.mode == "LIVE" and args.yes:
        print("[WARNING] LIVE MODE enabled with --yes flag (no interactive confirmation)")
    
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
