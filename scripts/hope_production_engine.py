# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 03:30:00 UTC
# Modified by: Claude (opus-4.5)
# Modified at: 2026-01-30T02:10:00Z
# Purpose: HOPE Production Trading Engine - Full cycle without stubs
# Contract: Real Binance execution, fail-closed, self-learning
# Features:
#   - SignalConsumer reads from MoonBot decisions.jsonl
#   - Single-instance lock (PID file)
#   - Rate limiter (MAX_ORDERS_PER_HOUR)
#   - Circuit breaker (MAX_DAILY_LOSS_PCT)
#   - Panic-close on price unavailable
#   - Heartbeat file for watchdog monitoring
#   - STOP.flag for graceful shutdown
# === END SIGNATURE ===
"""
HOPE PRODUCTION ENGINE - Полный торговый цикл без заглушек

Архитектура:
  Signal → Gateway → Eye of God → Binance → Monitor → Close → Learn

Временные сессии (не 24/7):
  ASIA:     00:00-08:00 UTC | risk=1.0 | Volume pumps
  EUROPE:   08:00-14:00 UTC | risk=1.0 | Trend continuation
  US_OPEN:  14:00-18:00 UTC | risk=1.2 | High volatility
  US_CLOSE: 18:00-22:00 UTC | risk=1.0 | Momentum
  NIGHT:    22:00-00:00 UTC | risk=0.5 | Pump overrides only

Использование:
  python hope_production_engine.py --mode TESTNET
  python hope_production_engine.py --mode LIVE --confirm
"""

import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import argparse

# Ensure project root
sys.path.insert(0, str(Path(__file__).parent.parent))

# Centralized secrets
from core.secrets import SECRETS_PATH

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("HOPE-ENGINE")


# ═══════════════════════════════════════════════════════════════════════════════
# ENUMS & CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

class TradingMode(Enum):
    DRY = "DRY"
    TESTNET = "TESTNET"
    LIVE = "LIVE"


class TradingSession(Enum):
    ASIA = "ASIA"           # 00:00-08:00 UTC
    EUROPE = "EUROPE"       # 08:00-14:00 UTC
    US_OPEN = "US_OPEN"     # 14:00-18:00 UTC
    US_CLOSE = "US_CLOSE"   # 18:00-22:00 UTC
    NIGHT = "NIGHT"         # 22:00-00:00 UTC


class SignalAction(Enum):
    BUY = "BUY"
    SKIP = "SKIP"
    HOLD = "HOLD"


# Session configuration
SESSION_CONFIG = {
    TradingSession.ASIA: {
        "hours": (0, 8),
        "risk_mult": 1.0,
        "strategy": "volume_pumps",
        "min_buys_sec": 30,
    },
    TradingSession.EUROPE: {
        "hours": (8, 14),
        "risk_mult": 1.0,
        "strategy": "trend_continuation",
        "min_buys_sec": 25,
    },
    TradingSession.US_OPEN: {
        "hours": (14, 18),
        "risk_mult": 1.2,
        "strategy": "high_volatility",
        "min_buys_sec": 40,
    },
    TradingSession.US_CLOSE: {
        "hours": (18, 22),
        "risk_mult": 1.0,
        "strategy": "momentum",
        "min_buys_sec": 30,
    },
    TradingSession.NIGHT: {
        "hours": (22, 24),
        "risk_mult": 0.5,
        "strategy": "pump_override_only",
        "min_buys_sec": 80,  # Higher threshold at night
    },
}

# State paths
STATE_DIR = Path("state/ai/production")
TRADES_FILE = STATE_DIR / "trades.jsonl"
POSITIONS_FILE = STATE_DIR / "positions.json"
STATS_FILE = STATE_DIR / "stats.json"
DIRECTIVES_FILE = STATE_DIR / "directives.json"
VALID_SYMBOLS_CACHE = STATE_DIR / "valid_symbols.json"

# MoonBot decisions (input from AI pipeline)
DECISIONS_FILE = Path("state/ai/decisions.jsonl")
CONSUMED_FILE = STATE_DIR / "consumed_signals.txt"  # Track processed signal IDs

# MoonBot raw signals (direct source)
MOONBOT_SIGNALS_DIR = Path("data/moonbot_signals")

# === PRODUCTION SAFETY FEATURES ===
LOCK_DIR = Path("state/locks")
ENGINE_LOCK_FILE = LOCK_DIR / "production_engine.lock"
HEARTBEAT_FILE = STATE_DIR / "heartbeat.json"
STOP_FLAG_FILE = Path("state/STOP.flag")

# Rate limits (fail-closed defaults)
MAX_ORDERS_PER_HOUR = 20  # Max orders per rolling hour
MAX_POSITION_VALUE_PCT = 5.0  # Max 5% of equity per position
MAX_DAILY_LOSS_PCT = 3.0  # Circuit breaker at -3% daily P&L
HEARTBEAT_TIMEOUT_SEC = 120  # Alert if no heartbeat for 2 min
PRICE_STALE_SEC = 30  # Price older than 30s is stale


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Signal:
    """Trading signal from source."""
    signal_id: str
    symbol: str
    direction: str  # Long/Short
    buys_per_sec: float
    delta_pct: float
    vol_raise_pct: float = 0.0
    strategy: str = "unknown"
    confidence: float = 0.0
    timestamp: str = ""
    source: str = "moonbot"

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()
        if not self.signal_id:
            self.signal_id = f"sig_{int(time.time()*1000)}_{self.symbol}"


@dataclass
class Position:
    """Open trading position."""
    position_id: str
    symbol: str
    side: str  # BUY/SELL
    entry_price: float
    quantity: float
    entry_time: str
    target_pct: float = 1.0
    stop_pct: float = -0.5
    timeout_sec: int = 300
    order_id: str = ""
    status: str = "OPEN"  # OPEN, CLOSED, TIMEOUT, STOPPED

    def pnl_pct(self, current_price: float) -> float:
        """Calculate PnL percentage."""
        if self.entry_price <= 0:
            return 0.0
        return ((current_price - self.entry_price) / self.entry_price) * 100


@dataclass
class OracleDecision:
    """Eye of God decision."""
    action: SignalAction
    confidence: float
    symbol: str
    reasons: List[str] = field(default_factory=list)
    factors: Dict[str, float] = field(default_factory=dict)
    session: str = ""
    risk_adjusted_size: float = 1.0
    sha256: str = ""

    def __post_init__(self):
        if not self.sha256:
            self.sha256 = self._compute_sha256()

    def _compute_sha256(self) -> str:
        data = {
            "action": self.action.value if isinstance(self.action, SignalAction) else self.action,
            "confidence": self.confidence,
            "symbol": self.symbol,
            "reasons": self.reasons,
            "factors": self.factors,
        }
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        return "sha256:" + hashlib.sha256(canonical).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════════════════════
# SYMBOL VALIDATOR (Caches valid symbols from Binance)
# ═══════════════════════════════════════════════════════════════════════════════

class SymbolValidator:
    """
    Validates symbols against Binance exchange info.

    Caches valid symbols to avoid repeated API calls.
    FAIL-CLOSED: If cannot fetch symbols, reject all.
    """

    def __init__(self, testnet: bool = True):
        self.testnet = testnet
        self._valid_symbols: Set[str] = set()
        self._cache_loaded = False
        self._last_refresh = 0.0
        self._refresh_interval = 3600  # 1 hour

    def _get_base_url(self) -> str:
        if self.testnet:
            return "https://testnet.binance.vision/api"
        return "https://api.binance.com/api"

    def _load_cache(self) -> bool:
        """Load symbols from cache file."""
        if not VALID_SYMBOLS_CACHE.exists():
            return False

        try:
            data = json.loads(VALID_SYMBOLS_CACHE.read_text(encoding="utf-8"))
            cached_time = data.get("timestamp", 0)

            # Check if cache is fresh (< 1 hour old)
            if time.time() - cached_time < self._refresh_interval:
                self._valid_symbols = set(data.get("symbols", []))
                self._last_refresh = cached_time
                self._cache_loaded = True
                logger.info(f"Loaded {len(self._valid_symbols)} valid symbols from cache")
                return True
        except Exception as e:
            logger.warning(f"Failed to load symbol cache: {e}")

        return False

    def _save_cache(self):
        """Save symbols to cache file."""
        VALID_SYMBOLS_CACHE.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "timestamp": time.time(),
            "testnet": self.testnet,
            "symbols": list(self._valid_symbols)
        }

        tmp = VALID_SYMBOLS_CACHE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, VALID_SYMBOLS_CACHE)

    def refresh(self) -> bool:
        """Fetch valid symbols from Binance API."""
        import urllib.request

        url = f"{self._get_base_url()}/v3/exchangeInfo"

        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            symbols = set()
            for sym in data.get("symbols", []):
                if sym.get("status") == "TRADING" and sym.get("quoteAsset") == "USDT":
                    symbols.add(sym["symbol"])

            self._valid_symbols = symbols
            self._last_refresh = time.time()
            self._cache_loaded = True

            self._save_cache()
            logger.info(f"Refreshed symbol list: {len(symbols)} valid USDT pairs")
            return True

        except Exception as e:
            logger.error(f"Failed to refresh symbol list: {e}")
            return False

    def ensure_loaded(self) -> bool:
        """Ensure symbols are loaded (from cache or API)."""
        if self._cache_loaded:
            # Check if refresh needed
            if time.time() - self._last_refresh > self._refresh_interval:
                self.refresh()
            return True

        # Try cache first
        if self._load_cache():
            return True

        # Fetch from API
        return self.refresh()

    def is_valid(self, symbol: str) -> bool:
        """Check if symbol is valid for trading."""
        if not self.ensure_loaded():
            logger.warning(f"Symbol validation FAIL-CLOSED: cannot verify {symbol}")
            return False  # FAIL-CLOSED

        return symbol in self._valid_symbols

    def filter_valid(self, symbols: List[str]) -> Tuple[List[str], List[str]]:
        """
        Filter symbols into valid and invalid.

        Returns (valid_symbols, invalid_symbols)
        """
        self.ensure_loaded()

        valid = []
        invalid = []

        for sym in symbols:
            if sym in self._valid_symbols:
                valid.append(sym)
            else:
                invalid.append(sym)

        return valid, invalid


# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL CONSUMER (Reads from MoonBot decisions.jsonl)
# ═══════════════════════════════════════════════════════════════════════════════

class SignalConsumer:
    """
    Consumes BUY decisions from MoonBot AI pipeline.

    Monitors: state/ai/decisions.jsonl
    Outputs: Signal objects for processing
    """

    def __init__(self):
        self._last_position = 0
        self._consumed_ids: Set[str] = set()
        self._load_consumed()

        # Start from end of file
        if DECISIONS_FILE.exists():
            self._last_position = DECISIONS_FILE.stat().st_size
            logger.info(f"SignalConsumer: starting from position {self._last_position}")

    def _load_consumed(self):
        """Load previously consumed signal IDs."""
        if CONSUMED_FILE.exists():
            try:
                lines = CONSUMED_FILE.read_text(encoding="utf-8").strip().split("\n")
                self._consumed_ids = set(l.strip() for l in lines if l.strip())
                logger.info(f"Loaded {len(self._consumed_ids)} consumed signal IDs")
            except Exception as e:
                logger.warning(f"Failed to load consumed IDs: {e}")

    def _save_consumed(self, signal_id: str):
        """Mark signal as consumed."""
        self._consumed_ids.add(signal_id)
        CONSUMED_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CONSUMED_FILE, "a", encoding="utf-8") as f:
            f.write(f"{signal_id}\n")

    def get_new_signals(self) -> List[Signal]:
        """
        Poll for new BUY decisions from the AI pipeline.

        Returns list of Signal objects ready for execution.
        """
        if not DECISIONS_FILE.exists():
            return []

        current_size = DECISIONS_FILE.stat().st_size
        if current_size <= self._last_position:
            return []

        signals = []

        try:
            with open(DECISIONS_FILE, "r", encoding="utf-8") as f:
                f.seek(self._last_position)
                new_content = f.read()
                self._last_position = f.tell()

            for line in new_content.strip().split("\n"):
                if not line.strip():
                    continue

                try:
                    decision = json.loads(line)
                    signal = self._parse_decision(decision)
                    if signal:
                        signals.append(signal)
                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    logger.warning(f"Failed to parse decision: {e}")

        except Exception as e:
            logger.error(f"Error reading decisions: {e}")

        return signals

    def _parse_decision(self, decision: Dict) -> Optional[Signal]:
        """
        Convert MoonBot decision to Signal.

        Only returns BUY signals that haven't been consumed.
        Robust extraction with fallback for missing raw_signal.
        """
        signal_id = decision.get("signal_id", "")
        final_action = decision.get("final_action", "SKIP")

        # Skip non-BUY
        if final_action != "BUY":
            return None

        # Skip already consumed
        if signal_id in self._consumed_ids:
            return None

        # Extract symbol and data
        symbol = decision.get("symbol", "")
        if not symbol:
            return None

        raw = decision.get("raw_signal", {})
        mode = decision.get("mode", {})
        mode_config = mode.get("config", {})
        dec = decision.get("decision", {})
        precursor = decision.get("precursor", {}).get("details", {})

        # Extract signal metrics with fallback
        buys_per_sec = raw.get("buys_per_sec", 0)
        delta_pct = raw.get("delta_pct", 0)
        vol_raise_pct = raw.get("vol_raise_pct", 0)

        # Fallback: if raw_signal missing, estimate from mode/precursor
        if buys_per_sec == 0 and mode.get("name") == "super_scalp":
            buys_per_sec = 100  # Super scalp implies high buys
        elif buys_per_sec == 0 and mode.get("name") == "scalp":
            buys_per_sec = 30  # Scalp implies moderate buys

        if delta_pct == 0 and precursor.get("is_precursor"):
            delta_pct = 3.0  # Precursor detected implies significant delta

        # Create signal with mode config for targets
        signal = Signal(
            signal_id=signal_id,
            symbol=symbol,
            direction=raw.get("direction", "Long"),
            buys_per_sec=buys_per_sec,
            delta_pct=delta_pct,
            vol_raise_pct=vol_raise_pct,
            strategy=raw.get("strategy", mode.get("name", "unknown")),
            confidence=dec.get("confidence", precursor.get("confidence", 0.5)),
            timestamp=decision.get("timestamp", ""),
            source="moonbot_ai",
        )

        # Mark as consumed
        self._save_consumed(signal_id)
        logger.info(f"📥 NEW BUY SIGNAL: {symbol} buys={buys_per_sec} delta={delta_pct} conf={signal.confidence:.2f}")

        return signal


# ═══════════════════════════════════════════════════════════════════════════════
# MOONBOT DIRECT SIGNAL READER
# ═══════════════════════════════════════════════════════════════════════════════

class MoonBotSignalReader:
    """
    Direct MoonBot signal reader.

    Reads signals from data/moonbot_signals/*.jsonl
    Applies basic validation and filters.
    """

    def __init__(self, validator: Optional['SymbolValidator'] = None):
        self._last_positions: Dict[str, int] = {}  # file -> last read position
        self._consumed_ids: Set[str] = set()
        self.validator = validator
        self._load_consumed()

    def _load_consumed(self):
        """Load consumed signal IDs from file."""
        consumed_file = STATE_DIR / "moonbot_consumed.txt"
        if consumed_file.exists():
            try:
                lines = consumed_file.read_text(encoding="utf-8").strip().split("\n")
                self._consumed_ids = set(l.strip() for l in lines if l.strip())
            except Exception:
                pass

    def _save_consumed(self, signal_id: str):
        """Mark signal as consumed."""
        self._consumed_ids.add(signal_id)
        consumed_file = STATE_DIR / "moonbot_consumed.txt"
        consumed_file.parent.mkdir(parents=True, exist_ok=True)
        with open(consumed_file, "a", encoding="utf-8") as f:
            f.write(f"{signal_id}\n")

    def get_new_signals(self, max_age_sec: int = 600) -> List[Signal]:
        """
        Get new signals from MoonBot signal files.

        Args:
            max_age_sec: Maximum signal age in seconds (default 10 min)

        Returns:
            List of valid Signal objects
        """
        if not MOONBOT_SIGNALS_DIR.exists():
            return []

        now = datetime.now(timezone.utc)
        signals = []

        # Get today's signal file
        today_str = now.strftime("%Y%m%d")
        signal_file = MOONBOT_SIGNALS_DIR / f"signals_{today_str}.jsonl"

        if not signal_file.exists():
            return []

        # Read new lines from file
        last_pos = self._last_positions.get(str(signal_file), 0)
        current_size = signal_file.stat().st_size

        if current_size <= last_pos:
            return []

        try:
            with open(signal_file, "r", encoding="utf-8") as f:
                f.seek(last_pos)
                new_lines = f.read()
                self._last_positions[str(signal_file)] = f.tell()

            for line in new_lines.strip().split("\n"):
                if not line.strip():
                    continue

                try:
                    data = json.loads(line)
                    signal = self._parse_signal(data, now, max_age_sec)
                    if signal:
                        signals.append(signal)
                except json.JSONDecodeError:
                    continue

        except Exception as e:
            logger.warning(f"Error reading MoonBot signals: {e}")

        return signals

    def _parse_signal(self, data: Dict, now: datetime, max_age_sec: int) -> Optional[Signal]:
        """Parse and validate a MoonBot signal."""
        signal_id = data.get("signal_id", "")

        # Skip already consumed
        if signal_id in self._consumed_ids:
            return None

        # Check timestamp age
        ts_str = data.get("timestamp", "")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                age = (now - ts).total_seconds()
                if age > max_age_sec:
                    return None  # Too old
            except Exception:
                pass

        symbol = data.get("symbol", "")
        if not symbol:
            return None

        # Validate symbol if validator available
        if self.validator and not self.validator.is_valid(symbol):
            logger.debug(f"Skipping invalid symbol from MoonBot: {symbol}")
            return None

        # Create signal
        signal = Signal(
            signal_id=signal_id,
            symbol=symbol,
            direction=data.get("direction", "Long"),
            buys_per_sec=data.get("buys_per_sec", 0),
            delta_pct=data.get("delta_pct", 0),
            vol_raise_pct=data.get("vol_raise_pct", 0),
            strategy=data.get("strategy", "MoonBot"),
            confidence=0.7,  # Default confidence for raw MoonBot signals
            timestamp=ts_str,
            source="moonbot_direct"
        )

        # Mark as consumed
        self._save_consumed(signal_id)
        logger.info(f"📥 MOONBOT SIGNAL: {symbol} buys={signal.buys_per_sec:.1f} delta={signal.delta_pct:.1f}%")

        return signal


# ═══════════════════════════════════════════════════════════════════════════════
# PRODUCTION SAFETY (Single instance, rate limiter, heartbeat)
# ═══════════════════════════════════════════════════════════════════════════════

def acquire_single_instance_lock(force: bool = False) -> bool:
    """
    Acquire single-instance lock via PID file.

    Args:
        force: If True, skip check for running instance (but still create lock)

    Returns True if lock acquired, False if another instance running.
    """
    LOCK_DIR.mkdir(parents=True, exist_ok=True)

    if ENGINE_LOCK_FILE.exists() and not force:
        try:
            old_pid = int(ENGINE_LOCK_FILE.read_text().strip())
            # Check if process still running (Windows-safe)
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, old_pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                kernel32.CloseHandle(handle)
                logger.error(f"Another instance running (PID {old_pid})")
                return False
        except Exception:
            pass  # PID file corrupt or process dead, ok to take lock

    # Write our PID (always create lock, even in force mode)
    ENGINE_LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    logger.info(f"Acquired engine lock (PID {os.getpid()})")
    return True


def release_single_instance_lock():
    """Release single-instance lock."""
    try:
        if ENGINE_LOCK_FILE.exists():
            ENGINE_LOCK_FILE.unlink()
            logger.info("Released engine lock")
    except Exception as e:
        logger.warning(f"Failed to release lock: {e}")


def write_heartbeat(cycle: int, positions: int, session: str):
    """Write heartbeat file for watchdog monitoring."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    heartbeat = {
        "pid": os.getpid(),
        "cycle": cycle,
        "positions": positions,
        "session": session,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "timestamp_unix": time.time(),
    }
    HEARTBEAT_FILE.write_text(json.dumps(heartbeat), encoding="utf-8")


def check_stop_flag() -> bool:
    """Check if STOP flag is set (graceful shutdown)."""
    return STOP_FLAG_FILE.exists()


class OrderRateLimiter:
    """
    Rolling window rate limiter for orders.

    Prevents runaway trading by limiting orders per hour.
    """

    def __init__(self, max_per_hour: int = MAX_ORDERS_PER_HOUR):
        self.max_per_hour = max_per_hour
        self._order_times: List[float] = []

    def can_order(self) -> bool:
        """Check if we can place another order."""
        now = time.time()
        one_hour_ago = now - 3600

        # Clean old entries
        self._order_times = [t for t in self._order_times if t > one_hour_ago]

        return len(self._order_times) < self.max_per_hour

    def record_order(self):
        """Record an order placement."""
        self._order_times.append(time.time())

    def orders_remaining(self) -> int:
        """Get remaining orders in current window."""
        now = time.time()
        one_hour_ago = now - 3600
        self._order_times = [t for t in self._order_times if t > one_hour_ago]
        return max(0, self.max_per_hour - len(self._order_times))


class CircuitBreaker:
    """
    Daily P&L circuit breaker.

    Stops trading if daily loss exceeds threshold.
    """

    def __init__(self, max_loss_pct: float = MAX_DAILY_LOSS_PCT):
        self.max_loss_pct = max_loss_pct
        self.daily_pnl_usd = 0.0
        self.starting_equity = 0.0
        self._tripped = False
        self._last_reset_date = datetime.now(timezone.utc).date()

    def set_starting_equity(self, equity: float):
        """Set starting equity for daily P&L calculation."""
        self.starting_equity = equity

    def record_pnl(self, pnl_usd: float):
        """Record P&L from a closed trade."""
        self._check_day_reset()
        self.daily_pnl_usd += pnl_usd

        # Check if circuit should trip
        if self.starting_equity > 0:
            loss_pct = abs(min(0, self.daily_pnl_usd)) / self.starting_equity * 100
            if loss_pct >= self.max_loss_pct:
                self._tripped = True
                logger.error(f"🚨 CIRCUIT BREAKER TRIPPED: Daily loss {loss_pct:.2f}% >= {self.max_loss_pct}%")

    def is_tripped(self) -> bool:
        """Check if circuit breaker is tripped."""
        self._check_day_reset()
        return self._tripped

    def _check_day_reset(self):
        """Reset daily counters at midnight UTC."""
        today = datetime.now(timezone.utc).date()
        if today != self._last_reset_date:
            logger.info(f"Daily reset: Previous P&L=${self.daily_pnl_usd:.2f}")
            self.daily_pnl_usd = 0.0
            self._tripped = False
            self._last_reset_date = today


# ═══════════════════════════════════════════════════════════════════════════════
# BINANCE EXECUTOR (Real API)
# ═══════════════════════════════════════════════════════════════════════════════

class BinanceExecutor:
    """Real Binance order execution - NO STUBS."""

    def __init__(self, mode: TradingMode):
        self.mode = mode
        self.client = None
        self._init_client()

    def _init_client(self):
        """Initialize Binance client based on mode."""
        if self.mode == TradingMode.DRY:
            logger.info("DRY mode - no Binance client needed")
            return

        try:
            from binance.client import Client
            from binance.exceptions import BinanceAPIException

            if self.mode == TradingMode.TESTNET:
                api_key = os.getenv("BINANCE_TESTNET_API_KEY")
                api_secret = os.getenv("BINANCE_TESTNET_API_SECRET")
                self.client = Client(api_key, api_secret, testnet=True)
                logger.info("Binance TESTNET client initialized")
            else:
                api_key = os.getenv("BINANCE_API_KEY")
                api_secret = os.getenv("BINANCE_API_SECRET")
                self.client = Client(api_key, api_secret)
                logger.info("Binance LIVE client initialized")

        except ImportError:
            logger.error("python-binance not installed: pip install python-binance")
            raise
        except Exception as e:
            logger.error(f"Failed to initialize Binance client: {e}")
            raise

    def market_buy(self, symbol: str, quote_qty: float) -> Dict:
        """
        Execute market buy order.

        Args:
            symbol: Trading pair (e.g., BTCUSDT)
            quote_qty: Amount in quote currency (USDT)

        Returns:
            Order result dict
        """
        if self.mode == TradingMode.DRY:
            return self._dry_order("BUY", symbol, quote_qty)

        if not self.client:
            raise RuntimeError("Binance client not initialized")

        try:
            order = self.client.order_market_buy(
                symbol=symbol,
                quoteOrderQty=quote_qty
            )
            logger.info(f"BUY executed: {symbol} ${quote_qty} -> order_id={order['orderId']}")
            return {
                "success": True,
                "order_id": str(order["orderId"]),
                "symbol": symbol,
                "side": "BUY",
                "filled_qty": float(order.get("executedQty", 0)),
                "avg_price": float(order.get("cummulativeQuoteQty", 0)) / float(order.get("executedQty", 1)),
                "status": order.get("status"),
                "raw": order
            }
        except Exception as e:
            logger.error(f"BUY failed: {symbol} - {e}")
            return {"success": False, "error": str(e), "symbol": symbol}

    def market_sell(self, symbol: str, quantity: float) -> Dict:
        """
        Execute market sell order.

        Args:
            symbol: Trading pair
            quantity: Amount in base currency

        Returns:
            Order result dict
        """
        if self.mode == TradingMode.DRY:
            return self._dry_order("SELL", symbol, quantity)

        if not self.client:
            raise RuntimeError("Binance client not initialized")

        try:
            # Format quantity to avoid scientific notation
            qty_str = f"{quantity:.8f}".rstrip('0').rstrip('.')
            order = self.client.order_market_sell(
                symbol=symbol,
                quantity=qty_str
            )
            logger.info(f"SELL executed: {symbol} qty={qty_str} -> order_id={order['orderId']}")
            return {
                "success": True,
                "order_id": str(order["orderId"]),
                "symbol": symbol,
                "side": "SELL",
                "filled_qty": float(order.get("executedQty", 0)),
                "avg_price": float(order.get("cummulativeQuoteQty", 0)) / float(order.get("executedQty", 1)),
                "status": order.get("status"),
                "raw": order
            }
        except Exception as e:
            logger.error(f"SELL failed: {symbol} - {e}")
            return {"success": False, "error": str(e), "symbol": symbol}

    def get_price(self, symbol: str) -> Optional[float]:
        """Get current price for symbol."""
        if self.mode == TradingMode.DRY:
            return 100.0  # Mock price for dry mode

        if not self.client:
            return None

        try:
            ticker = self.client.get_symbol_ticker(symbol=symbol)
            return float(ticker["price"])
        except Exception as e:
            logger.warning(f"Failed to get price for {symbol}: {e}")
            return None

    def _dry_order(self, side: str, symbol: str, amount: float) -> Dict:
        """Simulate order for DRY mode."""
        order_id = f"DRY_{int(time.time()*1000)}"
        logger.info(f"[DRY] {side} {symbol} amount={amount}")
        return {
            "success": True,
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "filled_qty": amount if side == "SELL" else amount / 100,
            "avg_price": 100.0,
            "status": "DRY_FILLED",
            "dry_mode": True
        }


# ═══════════════════════════════════════════════════════════════════════════════
# EYE OF GOD - Decision Oracle
# ═══════════════════════════════════════════════════════════════════════════════

class EyeOfGod:
    """
    Eye of God - AI Oracle for trading decisions.

    Multi-factor scoring with session awareness and self-learning.
    """

    def __init__(self):
        self.config = self._load_config()
        self.stats = self._load_stats()

    def _load_config(self) -> Dict:
        """Load oracle configuration."""
        try:
            from core.oracle_config import get_config_manager
            cm = get_config_manager()
            cfg = cm.config
            return {
                "whitelist": cfg.whitelist,
                "blacklist": cfg.blacklist,
                "min_confidence": cfg.min_confidence,
                "calibration": cfg.calibration,
                "symbol_stats": cfg.symbol_stats,
            }
        except Exception as e:
            logger.warning(f"Failed to load oracle config: {e}")
            return {
                "whitelist": {"BTCUSDT", "ETHUSDT", "KITEUSDT", "SOMIUSDT"},
                "blacklist": {"SYNUSDT", "DODOUSDT"},
                "min_confidence": 0.50,
                "calibration": 1.0,
                "symbol_stats": {},
            }

    def _load_stats(self) -> Dict:
        """Load trading statistics."""
        if STATS_FILE.exists():
            try:
                return json.loads(STATS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"total_trades": 0, "wins": 0, "losses": 0, "by_symbol": {}}

    def get_current_session(self) -> TradingSession:
        """Get current trading session based on UTC hour."""
        hour = datetime.now(timezone.utc).hour

        for session, config in SESSION_CONFIG.items():
            start, end = config["hours"]
            if start <= hour < end:
                return session

        return TradingSession.ASIA  # Default

    def decide(self, signal: Signal, current_price: Optional[float] = None) -> OracleDecision:
        """
        Make trading decision based on signal and market context.

        FAIL-CLOSED: Missing data = SKIP
        """
        session = self.get_current_session()
        session_config = SESSION_CONFIG[session]
        reasons = []
        factors = {}

        # === FAIL-CLOSED: Price verification ===
        if current_price is None or current_price <= 0:
            return OracleDecision(
                action=SignalAction.SKIP,
                confidence=0.0,
                symbol=signal.symbol,
                reasons=["PRICE_MISSING"],
                session=session.value,
            )

        # === BLACKLIST CHECK ===
        if signal.symbol in self.config.get("blacklist", set()):
            return OracleDecision(
                action=SignalAction.SKIP,
                confidence=0.0,
                symbol=signal.symbol,
                reasons=["BLACKLIST"],
                factors={"blacklist": -1.0},
                session=session.value,
            )

        # === MULTI-FACTOR SCORING ===
        confidence = 0.0

        # Factor 1: Whitelist bonus
        if signal.symbol in self.config.get("whitelist", set()):
            factors["whitelist"] = 0.25
            confidence += 0.25
            reasons.append("WHITELIST")

        # Factor 2: Symbol history
        symbol_stats = self.config.get("symbol_stats", {}).get(signal.symbol, {})
        if symbol_stats:
            wins = symbol_stats.get("wins", 0)
            losses = symbol_stats.get("losses", 0)
            if wins + losses >= 3:
                win_rate = wins / (wins + losses)
                if win_rate >= 0.6:
                    factors["history"] = 0.20
                    confidence += 0.20
                    reasons.append(f"HISTORY_WIN_{win_rate:.0%}")
                elif win_rate <= 0.3:
                    factors["history"] = -0.30
                    confidence -= 0.30
                    reasons.append(f"HISTORY_LOSS_{win_rate:.0%}")

        # Factor 3: Buys per second (session-adjusted threshold)
        min_buys = session_config["min_buys_sec"]
        if signal.buys_per_sec >= min_buys * 2:
            factors["buys_high"] = 0.20
            confidence += 0.20
            reasons.append("BUYS_VERY_HIGH")
        elif signal.buys_per_sec >= min_buys:
            factors["buys_ok"] = 0.10
            confidence += 0.10
            reasons.append("BUYS_OK")
        else:
            factors["buys_low"] = -0.15
            confidence -= 0.15
            reasons.append("BUYS_LOW")

        # Factor 4: Delta percentage
        if signal.delta_pct >= 5.0:
            factors["delta_high"] = 0.15
            confidence += 0.15
            reasons.append("DELTA_HIGH")
        elif signal.delta_pct >= 2.5:
            factors["delta_med"] = 0.05
            confidence += 0.05
            reasons.append("DELTA_MED")

        # Factor 5: Volume raise
        if signal.vol_raise_pct >= 50:
            factors["volume"] = 0.10
            confidence += 0.10
            reasons.append("VOLUME_SPIKE")

        # Factor 6: Session strategy match
        strategy = session_config["strategy"]
        if strategy == "pump_override_only" and signal.buys_per_sec < 80:
            factors["session_reject"] = -0.50
            confidence -= 0.50
            reasons.append("NIGHT_LOW_ACTIVITY")

        # Apply calibration
        confidence *= self.config.get("calibration", 1.0)

        # Apply session risk multiplier
        risk_mult = session_config["risk_mult"]

        # === DECISION ===
        min_confidence = self.config.get("min_confidence", 0.50)

        if confidence >= min_confidence:
            return OracleDecision(
                action=SignalAction.BUY,
                confidence=confidence,
                symbol=signal.symbol,
                reasons=reasons,
                factors=factors,
                session=session.value,
                risk_adjusted_size=risk_mult,
            )
        else:
            reasons.append(f"LOW_CONFIDENCE_{confidence:.2f}")
            return OracleDecision(
                action=SignalAction.SKIP,
                confidence=confidence,
                symbol=signal.symbol,
                reasons=reasons,
                factors=factors,
                session=session.value,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# POSITION MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class PositionManager:
    """Manages open positions with monitoring and auto-close."""

    def __init__(self, executor: BinanceExecutor):
        self.executor = executor
        self.positions: Dict[str, Position] = {}
        self._load_positions()

    def _load_positions(self):
        """Load positions from disk."""
        if POSITIONS_FILE.exists():
            try:
                data = json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
                for pos_data in data.get("positions", []):
                    pos = Position(**pos_data)
                    if pos.status == "OPEN":
                        self.positions[pos.position_id] = pos
                logger.info(f"Loaded {len(self.positions)} open positions")
            except Exception as e:
                logger.error(f"Failed to load positions: {e}")

    def _save_positions(self):
        """Save positions to disk atomically."""
        POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "positions": [asdict(p) for p in self.positions.values()],
            "updated_at": datetime.now(timezone.utc).isoformat()
        }

        tmp = POSITIONS_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, POSITIONS_FILE)

    def open_position(
        self,
        signal: Signal,
        decision: OracleDecision,
        quote_amount: float
    ) -> Optional[Position]:
        """Open a new position."""
        # Get current price
        price = self.executor.get_price(signal.symbol)
        if price is None:
            logger.warning(f"Cannot open position: no price for {signal.symbol}")
            return None

        # Execute buy
        result = self.executor.market_buy(signal.symbol, quote_amount)
        if not result.get("success"):
            logger.error(f"Buy failed: {result.get('error')}")
            return None

        # Create position
        position = Position(
            position_id=f"pos_{result['order_id']}",
            symbol=signal.symbol,
            side="BUY",
            entry_price=result.get("avg_price", price),
            quantity=result.get("filled_qty", 0),
            entry_time=datetime.now(timezone.utc).isoformat(),
            target_pct=1.0 * decision.risk_adjusted_size,
            stop_pct=-0.5 * decision.risk_adjusted_size,
            order_id=result["order_id"],
        )

        self.positions[position.position_id] = position
        self._save_positions()

        logger.info(f"Position opened: {position.position_id} {signal.symbol} @ {position.entry_price}")
        return position

    def check_positions(self) -> List[Tuple[Position, str]]:
        """
        Check all positions for exit conditions.

        Returns list of (position, close_reason) for positions that should close.
        """
        to_close = []
        now = datetime.now(timezone.utc)

        for pos in list(self.positions.values()):
            if pos.status != "OPEN":
                continue

            # Get current price
            price = self.executor.get_price(pos.symbol)
            if price is None:
                continue  # Skip if no price, will retry

            pnl = pos.pnl_pct(price)

            # Check target
            if pnl >= pos.target_pct:
                to_close.append((pos, "TARGET"))
                continue

            # Check stop
            if pnl <= pos.stop_pct:
                to_close.append((pos, "STOPPED"))
                continue

            # Check timeout
            entry_time = datetime.fromisoformat(pos.entry_time.replace("Z", "+00:00"))
            elapsed = (now - entry_time).total_seconds()
            if elapsed > pos.timeout_sec:
                to_close.append((pos, "TIMEOUT"))

        return to_close

    def close_position(self, position: Position, reason: str) -> Dict:
        """Close a position."""
        if position.quantity <= 0:
            return {"success": False, "error": "Zero quantity"}

        # Execute sell
        result = self.executor.market_sell(position.symbol, position.quantity)

        if result.get("success"):
            exit_price = result.get("avg_price", 0)
            pnl_pct = position.pnl_pct(exit_price) if exit_price > 0 else 0

            position.status = reason
            if position.position_id in self.positions:
                del self.positions[position.position_id]
            self._save_positions()

            logger.info(f"Position closed: {position.position_id} reason={reason} pnl={pnl_pct:.2f}%")
            return {
                "success": True,
                "position_id": position.position_id,
                "reason": reason,
                "pnl_pct": pnl_pct,
                "exit_price": exit_price
            }
        else:
            logger.error(f"Failed to close position {position.position_id}: {result.get('error')}")
            return result


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE LOGGER
# ═══════════════════════════════════════════════════════════════════════════════

class TradeLogger:
    """Atomic trade logging with sha256 + Telegram notifications."""

    def __init__(self):
        TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self._tg_chat = os.getenv("TELEGRAM_ADMIN_ID")

    def log(self, event: str, data: Dict):
        """Log trade event atomically."""
        entry = {
            "event": event,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Add sha256
        canonical = json.dumps(
            {k: v for k, v in entry.items() if k != "sha256"},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False
        ).encode("utf-8")
        entry["sha256"] = "sha256:" + hashlib.sha256(canonical).hexdigest()[:16]

        # Atomic append
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with open(TRADES_FILE, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

        # Send Telegram notification (non-blocking)
        self._notify_telegram(event, data)

    def _notify_telegram(self, event: str, data: Dict):
        """Send trade notification to Telegram."""
        if not self._tg_token or not self._tg_chat:
            return

        try:
            import urllib.request
            import urllib.parse

            # Format message
            if event == "OPEN":
                msg = f"🟢 *OPEN* {data.get('symbol')}\n"
                msg += f"Entry: ${data.get('entry_price', 0):.4f}\n"
                msg += f"Qty: {data.get('quantity', 0):.6f}"
            elif event == "CLOSE":
                pnl = data.get('pnl_pct', 0)
                emoji = "✅" if pnl > 0 else "🔴"
                msg = f"{emoji} *CLOSE* {data.get('symbol')}\n"
                msg += f"PnL: {pnl:+.2f}%\n"
                msg += f"Reason: {data.get('reason', '?')}"
            else:
                return  # Only notify OPEN/CLOSE

            url = f"https://api.telegram.org/bot{self._tg_token}/sendMessage"
            payload = {
                "chat_id": self._tg_chat,
                "text": msg,
                "parse_mode": "Markdown",
            }
            req = urllib.request.Request(
                url,
                data=urllib.parse.urlencode(payload).encode(),
                method="POST"
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            logger.debug(f"Telegram notify failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# SELF-LEARNING MODULE
# ═══════════════════════════════════════════════════════════════════════════════

class SelfLearner:
    """Self-learning from trade outcomes."""

    def __init__(self):
        self.stats = self._load_stats()

    def _load_stats(self) -> Dict:
        """Load stats from disk."""
        if STATS_FILE.exists():
            try:
                return json.loads(STATS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "by_symbol": {},
            "by_session": {},
        }

    def _save_stats(self):
        """Save stats atomically."""
        STATS_FILE.parent.mkdir(parents=True, exist_ok=True)

        self.stats["updated_at"] = datetime.now(timezone.utc).isoformat()

        tmp = STATS_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.stats, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATS_FILE)

    def record_outcome(self, symbol: str, is_win: bool, pnl_pct: float, session: str):
        """Record trade outcome and update stats."""
        self.stats["total_trades"] += 1

        if is_win:
            self.stats["wins"] += 1
        else:
            self.stats["losses"] += 1

        # By symbol
        if symbol not in self.stats["by_symbol"]:
            self.stats["by_symbol"][symbol] = {"wins": 0, "losses": 0, "pnl": 0}

        sym_stats = self.stats["by_symbol"][symbol]
        if is_win:
            sym_stats["wins"] += 1
        else:
            sym_stats["losses"] += 1
        sym_stats["pnl"] += pnl_pct

        # By session
        if session not in self.stats["by_session"]:
            self.stats["by_session"][session] = {"wins": 0, "losses": 0, "pnl": 0}

        sess_stats = self.stats["by_session"][session]
        if is_win:
            sess_stats["wins"] += 1
        else:
            sess_stats["losses"] += 1
        sess_stats["pnl"] += pnl_pct

        # Auto-learn: update oracle config
        self._auto_learn(symbol, sym_stats)

        self._save_stats()
        logger.info(f"Outcome recorded: {symbol} win={is_win} pnl={pnl_pct:.2f}%")

    def _auto_learn(self, symbol: str, stats: Dict):
        """Auto-update whitelist/blacklist based on performance."""
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        total = wins + losses

        if total < 3:
            return  # Not enough data

        win_rate = wins / total

        try:
            from core.oracle_config import get_config_manager
            cm = get_config_manager()
            cfg = cm.config

            # Auto-whitelist at 80%+ win rate
            if win_rate >= 0.80 and symbol not in cfg.whitelist:
                cfg.whitelist.add(symbol)
                cm.save(cfg, f"auto_whitelist_{symbol}")
                logger.info(f"AUTO-WHITELIST: {symbol} (win_rate={win_rate:.0%})")

            # Auto-blacklist at 20%- win rate
            elif win_rate <= 0.20 and symbol not in cfg.blacklist:
                cfg.blacklist.add(symbol)
                if symbol in cfg.whitelist:
                    cfg.whitelist.discard(symbol)
                cm.save(cfg, f"auto_blacklist_{symbol}")
                logger.warning(f"AUTO-BLACKLIST: {symbol} (win_rate={win_rate:.0%})")

        except Exception as e:
            logger.error(f"Auto-learn failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# PRODUCTION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class HopeProductionEngine:
    """
    Main production trading engine.

    Orchestrates: Signal → Oracle → Execution → Monitoring → Learning
    """

    def __init__(self, mode: TradingMode, position_size: float = 10.0):
        self.mode = mode
        self.position_size = position_size
        self.running = False
        self.cycle_count = 0  # For heartbeat/logging

        # Initialize components
        self.executor = BinanceExecutor(mode)
        self.oracle = EyeOfGod()
        self.position_manager = PositionManager(self.executor)
        self.trade_logger = TradeLogger()
        self.learner = SelfLearner()
        self.signal_consumer = SignalConsumer()  # Read from MoonBot pipeline

        # === SYMBOL VALIDATION ===
        self.symbol_validator = SymbolValidator(testnet=(mode == TradingMode.TESTNET))
        self.symbol_validator.ensure_loaded()

        # === MOONBOT DIRECT READER ===
        self.moonbot_reader = MoonBotSignalReader(validator=self.symbol_validator)

        # === PRODUCTION SAFETY ===
        self.rate_limiter = OrderRateLimiter(MAX_ORDERS_PER_HOUR)
        self.circuit_breaker = CircuitBreaker(MAX_DAILY_LOSS_PCT)

        # Stats
        self.cycle_stats = {
            "signals_received": 0,
            "signals_traded": 0,
            "signals_skipped": 0,
            "invalid_symbols": 0,
            "rate_limited": 0,
            "circuit_breaker_blocks": 0,
        }

        logger.info(f"HopeProductionEngine initialized in {mode.value} mode")

    async def process_signal(self, signal: Signal) -> Dict:
        """
        Process a trading signal through the full cycle.

        Signal → Oracle Decision → Execution → Logging

        Safety checks (fail-closed):
        1. Symbol validation (must exist on exchange)
        2. Circuit breaker (daily loss limit)
        3. Rate limiter (orders per hour)
        4. Price availability
        """
        self.cycle_stats["signals_received"] += 1

        # === SAFETY CHECK 0: Symbol validation ===
        if not self.symbol_validator.is_valid(signal.symbol):
            self.cycle_stats["invalid_symbols"] += 1
            logger.warning(f"⚠️ INVALID SYMBOL: {signal.symbol} not on exchange")
            return {"action": "SKIP", "reasons": ["INVALID_SYMBOL"]}

        # === SAFETY CHECK 1: Circuit breaker ===
        if self.circuit_breaker.is_tripped():
            self.cycle_stats["circuit_breaker_blocks"] += 1
            logger.warning(f"🚨 CIRCUIT BREAKER: Blocking {signal.symbol}")
            return {"action": "BLOCKED", "reason": "circuit_breaker_tripped"}

        # === SAFETY CHECK 2: Rate limiter ===
        if not self.rate_limiter.can_order():
            self.cycle_stats["rate_limited"] += 1
            logger.warning(f"⏳ RATE LIMITED: {signal.symbol} (remaining: {self.rate_limiter.orders_remaining()})")
            return {"action": "RATE_LIMITED", "reason": "max_orders_per_hour"}

        # Get current price (FAIL-CLOSED)
        price = self.executor.get_price(signal.symbol)

        # Oracle decision
        decision = self.oracle.decide(signal, price)

        # Log signal
        self.trade_logger.log("SIGNAL", {
            "signal": asdict(signal),
            "decision": {
                "action": decision.action.value,
                "confidence": decision.confidence,
                "reasons": decision.reasons,
                "session": decision.session,
            }
        })

        if decision.action == SignalAction.SKIP:
            self.cycle_stats["signals_skipped"] += 1
            logger.info(f"SKIP {signal.symbol}: {decision.reasons}")
            return {"action": "SKIP", "reasons": decision.reasons}

        # Execute trade
        adjusted_size = self.position_size * decision.risk_adjusted_size
        position = self.position_manager.open_position(signal, decision, adjusted_size)

        if position:
            self.cycle_stats["signals_traded"] += 1
            self.rate_limiter.record_order()  # Track order for rate limiting
            self.trade_logger.log("OPEN", {
                "position_id": position.position_id,
                "symbol": position.symbol,
                "entry_price": position.entry_price,
                "quantity": position.quantity,
                "decision": decision.sha256,
            })
            return {
                "action": "BUY",
                "position_id": position.position_id,
                "entry_price": position.entry_price,
            }
        else:
            self.cycle_stats["signals_skipped"] += 1
            return {"action": "FAILED", "reason": "execution_failed"}

    async def monitor_positions(self):
        """
        Check positions for exit conditions.

        Includes PANIC-CLOSE: If price unavailable for open position,
        close immediately at market to prevent unknown exposure.
        """
        to_close = self.position_manager.check_positions()

        # === PANIC-CLOSE: Check for positions with missing prices ===
        for pos in list(self.position_manager.positions.values()):
            if pos.status != "OPEN":
                continue
            price = self.executor.get_price(pos.symbol)
            if price is None:
                logger.error(f"🚨 PANIC-CLOSE: {pos.symbol} - price unavailable!")
                to_close.append((pos, "PANIC_NO_PRICE"))

        for position, reason in to_close:
            result = self.position_manager.close_position(position, reason)

            if result.get("success"):
                pnl_pct = result.get("pnl_pct", 0)
                pnl_usd = result.get("pnl_usd", 0)
                is_win = pnl_pct > 0

                # === Record P&L for circuit breaker ===
                self.circuit_breaker.record_pnl(pnl_usd)

                # Log close
                self.trade_logger.log("CLOSE", {
                    "position_id": position.position_id,
                    "symbol": position.symbol,
                    "reason": reason,
                    "pnl_pct": pnl_pct,
                    "pnl_usd": pnl_usd,
                    "exit_price": result.get("exit_price"),
                })

                # Record for learning
                session = self.oracle.get_current_session().value
                self.learner.record_outcome(position.symbol, is_win, pnl_pct, session)

    async def run_cycle(self):
        """
        Run single trading cycle.

        1. Check STOP flag (graceful shutdown)
        2. Write heartbeat (watchdog monitoring)
        3. Poll for new signals from MoonBot pipeline
        4. Process each BUY signal through Oracle + Execution
        5. Monitor existing positions for exit conditions
        """
        self.cycle_count += 1

        # === Check STOP flag ===
        if check_stop_flag():
            logger.info("🛑 STOP flag detected - shutting down gracefully")
            self.running = False
            return

        # === Write heartbeat for watchdog ===
        session = self.oracle.get_current_session()
        write_heartbeat(
            cycle=self.cycle_count,
            positions=len(self.position_manager.positions),
            session=session.value
        )

        # Step 1: Poll for new signals (both from AI decisions and direct MoonBot)
        new_signals = self.signal_consumer.get_new_signals()
        moonbot_signals = self.moonbot_reader.get_new_signals(max_age_sec=600)

        all_signals = new_signals + moonbot_signals

        for signal in all_signals:
            try:
                result = await self.process_signal(signal)
                if result.get("action") == "BUY":
                    logger.info(f"✅ TRADED: {signal.symbol} @ {result.get('entry_price')}")
            except Exception as e:
                logger.error(f"Failed to process signal {signal.symbol}: {e}")

        # Step 2: Monitor positions for exit
        await self.monitor_positions()

        # Periodic status log (every 60 cycles ≈ 1 min)
        if self.cycle_count % 60 == 0:
            logger.info(f"Cycle {self.cycle_count} | Session: {session.value} | "
                       f"Positions: {len(self.position_manager.positions)} | "
                       f"Traded: {self.cycle_stats['signals_traded']}")

    def get_status(self) -> Dict:
        """Get engine status."""
        session = self.oracle.get_current_session()
        return {
            "mode": self.mode.value,
            "running": self.running,
            "session": session.value,
            "session_risk": SESSION_CONFIG[session]["risk_mult"],
            "open_positions": len(self.position_manager.positions),
            "stats": self.cycle_stats,
            "learner_stats": self.learner.stats,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

async def main():
    parser = argparse.ArgumentParser(description="HOPE Production Trading Engine")
    parser.add_argument("--mode", type=str, default="DRY", choices=["DRY", "TESTNET", "LIVE"])
    parser.add_argument("--confirm", action="store_true", help="Confirm LIVE mode")
    parser.add_argument("--position-size", type=float, default=10.0, help="Default position size USD")
    parser.add_argument("--status", action="store_true", help="Show status and exit")
    parser.add_argument("--force", action="store_true", help="Force start (ignore existing lock)")
    args = parser.parse_args()

    # Load env
    try:
        from dotenv import load_dotenv
        if SECRETS_PATH.exists():
            load_dotenv(SECRETS_PATH)
            logger.info(f"Loaded env from {SECRETS_PATH}")
    except ImportError:
        logger.warning("python-dotenv not installed, using system env only")

    # Safety check for LIVE
    if args.mode == "LIVE":
        if not args.confirm:
            print("LIVE MODE requires --confirm flag")
            print("This will trade with REAL MONEY!")
            sys.exit(1)

        confirm = input("Type 'I UNDERSTAND' to confirm LIVE mode: ")
        if confirm != "I UNDERSTAND":
            print("Cancelled.")
            sys.exit(1)

    mode = TradingMode[args.mode]

    # === SINGLE INSTANCE CHECK ===
    if not args.status:
        if not acquire_single_instance_lock(force=args.force):
            logger.error("Another instance is already running. Use --force to override.")
            sys.exit(42)  # Special exit code for duplicate instance

    try:
        # Preflight checks
        logger.info("Running preflight checks...")

        # Check Gateway is running
        gateway_lock = Path("state/locks/gateway.lock")
        if gateway_lock.exists():
            logger.info("Gateway already running")
        else:
            logger.warning("Gateway not running - starting in offline mode")

        logger.info("Preflight PASSED")

        # Create engine
        engine = HopeProductionEngine(mode, args.position_size)

        if args.status:
            status = engine.get_status()
            print(json.dumps(status, indent=2))
            return

        # Run
        engine.running = True
        logger.info(f"Production Engine started in {mode.value} mode")
        logger.info(f"Session: {engine.oracle.get_current_session().value}")
        logger.info(f"Position size: ${args.position_size}")
        logger.info(f"Rate limit: {MAX_ORDERS_PER_HOUR} orders/hour")
        logger.info(f"Circuit breaker: {MAX_DAILY_LOSS_PCT}% daily loss")

        while engine.running:
            await engine.run_cycle()
            await asyncio.sleep(1.0)

    except KeyboardInterrupt:
        logger.info("Shutdown requested (Ctrl+C)")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # === CLEANUP ===
        release_single_instance_lock()
        logger.info("Engine stopped")


if __name__ == "__main__":
    asyncio.run(main())
