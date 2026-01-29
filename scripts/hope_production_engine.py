# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 03:30:00 UTC
# Purpose: HOPE Production Trading Engine - Full cycle without stubs
# Contract: Real Binance execution, fail-closed, self-learning
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
            order = self.client.order_market_sell(
                symbol=symbol,
                quantity=quantity
            )
            logger.info(f"SELL executed: {symbol} qty={quantity} -> order_id={order['orderId']}")
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
                "whitelist": {"BTCUSDT", "ETHUSDT", "KITEUSDT"},
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
    """Atomic trade logging with sha256."""

    def __init__(self):
        TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)

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

        # Initialize components
        self.executor = BinanceExecutor(mode)
        self.oracle = EyeOfGod()
        self.position_manager = PositionManager(self.executor)
        self.trade_logger = TradeLogger()
        self.learner = SelfLearner()

        # Stats
        self.cycle_stats = {
            "signals_received": 0,
            "signals_traded": 0,
            "signals_skipped": 0,
        }

        logger.info(f"HopeProductionEngine initialized in {mode.value} mode")

    async def process_signal(self, signal: Signal) -> Dict:
        """
        Process a trading signal through the full cycle.

        Signal → Oracle Decision → Execution → Logging
        """
        self.cycle_stats["signals_received"] += 1

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
        """Check positions for exit conditions."""
        to_close = self.position_manager.check_positions()

        for position, reason in to_close:
            result = self.position_manager.close_position(position, reason)

            if result.get("success"):
                pnl_pct = result.get("pnl_pct", 0)
                is_win = pnl_pct > 0

                # Log close
                self.trade_logger.log("CLOSE", {
                    "position_id": position.position_id,
                    "symbol": position.symbol,
                    "reason": reason,
                    "pnl_pct": pnl_pct,
                    "exit_price": result.get("exit_price"),
                })

                # Record for learning
                session = self.oracle.get_current_session().value
                self.learner.record_outcome(position.symbol, is_win, pnl_pct, session)

    async def run_cycle(self):
        """Run single monitoring cycle."""
        await self.monitor_positions()

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
    args = parser.parse_args()

    # Load env
    try:
        from dotenv import load_dotenv
        load_dotenv(Path("C:/secrets/hope.env"))
    except ImportError:
        pass

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

    # Create engine
    engine = HopeProductionEngine(mode, args.position_size)

    if args.status:
        status = engine.get_status()
        print(json.dumps(status, indent=2))
        return

    # Run
    engine.running = True
    logger.info(f"Engine starting in {mode.value} mode")
    logger.info(f"Session: {engine.oracle.get_current_session().value}")
    logger.info(f"Position size: ${args.position_size}")

    try:
        while engine.running:
            await engine.run_cycle()
            await asyncio.sleep(1.0)
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
        engine.running = False


if __name__ == "__main__":
    asyncio.run(main())
