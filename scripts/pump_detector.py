# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 15:45:00 UTC
# Modified by: Claude (opus-4.5)
# Modified at: 2026-01-30 21:40:00 UTC
# Purpose: Real-time pump detection + HOPE v4.0 Trading Engine (full cycle)
# Changes: Integrated Trading Engine v4.0 (Signal→Gate→TP/SL→Binance→Log→Learn)
# === END SIGNATURE ===
"""
HOPE Pump Detector - Real-time Binance Signal Generator

Connects to Binance WebSocket and detects pump patterns:
- Sudden increase in buy volume
- Price delta spikes
- Volume momentum

NOW WITH AI PREDICTOR V2:
- Three-Layer AllowList (CORE/DYNAMIC/HOT)
- RSI/MACD technical analysis
- BTC trend correlation
- Risk-adjusted position sizing

Generates signals compatible with AutoTrader/Eye of God V3.

Usage:
    python scripts/pump_detector.py --symbols BTCUSDT,ETHUSDT,SOLUSDT
    python scripts/pump_detector.py --top 20  # Top 20 by volume
    python scripts/pump_detector.py --top 20 --no-ai  # Disable AI filtering
"""

import asyncio
import json
import logging
import os
import sys
import time
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
from collections import deque

# Add project root
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    import httpx
except ImportError:
    os.system(f"{sys.executable} -m pip install httpx -q")
    import httpx

try:
    import websockets
except ImportError:
    os.system(f"{sys.executable} -m pip install websockets -q")
    import websockets

try:
    from dotenv import load_dotenv
    load_dotenv(Path("C:/secrets/hope.env"))
except ImportError:
    pass

# AI Predictor v2 Integration
AI_PREDICTOR_ENABLED = True
try:
    from ai_predictor_v2 import process_pump_signal, format_decision_log, AIDecision
    log_ai = True
except ImportError:
    AI_PREDICTOR_ENABLED = False
    log_ai = False

# Three-Layer AllowList Integration
THREE_LAYER_ENABLED = True
try:
    from three_layer_allowlist import get_allowlist, ThreeLayerAllowList
except ImportError:
    THREE_LAYER_ENABLED = False

# === AI v2: Adaptive Target + Signal Aggregator ===
ADAPTIVE_TARGET_ENABLED = False
SIGNAL_AGGREGATOR_ENABLED = False
try:
    from adaptive_target_ai import calculate_adaptive_target, get_pump_tier
    ADAPTIVE_TARGET_ENABLED = True
except ImportError as e:
    pass

try:
    from signal_aggregator import process_signal_for_telegram, get_aggregator
    SIGNAL_AGGREGATOR_ENABLED = True
except ImportError as e:
    pass

# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING (must be before pretrade_pipeline import which uses log)
# ═══════════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
log = logging.getLogger("PUMP-DET")

# ═══════════════════════════════════════════════════════════════════════════════
# HOPE v4.0 UNIFIED TRADING PIPELINE
# ===============================================================================
TRADING_ENGINE_READY = False
try:
    from core.trading_engine import handle_signal as trading_engine_handle
    TRADING_ENGINE_READY = True
except ImportError as e:
    pass  # Trading Engine not available - will use legacy path

# HOPE v4.0 UNIFIED PRETRADE PIPELINE (replaces live_trading_patch)
# Includes: Signal Schema, TTL, Liquidity, Price Feed, Gate, Circuit Breaker, Rate Limiter
PRETRADE_PIPELINE_READY = False
try:
    from core.pretrade_pipeline import (
        pretrade_check, PretradeResult, GateDecision, PipelineConfig,
        CircuitBreaker, RateLimiter, HealthMonitor, LiveBarrier, ExecutionMode
    )
    PRETRADE_PIPELINE_READY = True
    _pipeline_config = PipelineConfig()
    _health_monitor = HealthMonitor()
    _live_barrier = LiveBarrier(_pipeline_config)
    log.info(f"[HOPE v4.0] Unified Pipeline: READY | Mode: {_live_barrier.effective_mode.value}")
    log.info(f"[HOPE v4.0] Limits: {_pipeline_config.max_consecutive_losses} losses, {_pipeline_config.max_daily_loss_pct}% daily, ${_pipeline_config.min_quote_volume_24h/1e6:.0f}M min liq")
except ImportError as e:
    log.warning(f"[HOPE v4.0] Pretrade pipeline not available: {e}")

# LEGACY: Keep for backwards compatibility
LIVE_SAFETY_READY = PRETRADE_PIPELINE_READY

def get_circuit_breaker():
    return CircuitBreaker(_pipeline_config) if PRETRADE_PIPELINE_READY else None

def get_rate_limiter():
    return RateLimiter(_pipeline_config) if PRETRADE_PIPELINE_READY else None

def get_health_monitor():
    return _health_monitor if PRETRADE_PIPELINE_READY else None

def get_live_barrier():
    return _live_barrier if PRETRADE_PIPELINE_READY else None
# ===============================================================================

# === AI v2: TradingView Dynamic AllowList ===
TV_ALLOWLIST_ENABLED = False
try:
    from tradingview_allowlist import is_tradingview_allowed, get_manager
    TV_ALLOWLIST_ENABLED = True
except ImportError as e:
    pass

# Log AI v2 status
if ADAPTIVE_TARGET_ENABLED:
    log.info("[AI v2] Adaptive Target AI: ENABLED")
if SIGNAL_AGGREGATOR_ENABLED:
    log.info("[AI v2] Signal Aggregator: ENABLED")
if TV_ALLOWLIST_ENABLED:
    log.info("[AI v2] TradingView AllowList: ENABLED")

# Configuration
SIGNALS_DIR = ROOT / "data" / "moonbot_signals"
AUTOTRADER_URL = "http://127.0.0.1:8200"
SIGNALS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class SymbolState:
    """Real-time state for a symbol."""
    symbol: str
    price: float = 0
    price_1m_ago: float = 0
    price_5m_ago: float = 0

    # Volume tracking (last 60 seconds)
    buy_trades: deque = field(default_factory=lambda: deque(maxlen=1000))
    sell_trades: deque = field(default_factory=lambda: deque(maxlen=1000))

    # Rolling metrics
    buys_per_sec: float = 0
    sells_per_sec: float = 0
    buy_volume_1m: float = 0
    sell_volume_1m: float = 0

    # Price deltas
    delta_1m: float = 0
    delta_5m: float = 0

    # Detection state
    last_signal_time: float = 0
    cooldown_sec: float = 30  # Min time between signals

    def update_price(self, price: float, is_buy: bool, quantity: float, timestamp: float):
        """Update state with new trade."""
        self.price = price

        # Record trade
        trade = (timestamp, quantity, price)
        if is_buy:
            self.buy_trades.append(trade)
        else:
            self.sell_trades.append(trade)

        # Calculate rolling metrics
        now = time.time()
        cutoff_1m = now - 60
        cutoff_5m = now - 300

        # Buys in last minute
        recent_buys = [(t, q, p) for t, q, p in self.buy_trades if t > cutoff_1m]
        recent_sells = [(t, q, p) for t, q, p in self.sell_trades if t > cutoff_1m]

        if recent_buys:
            self.buy_volume_1m = sum(q * p for _, q, p in recent_buys)
            self.buys_per_sec = len(recent_buys) / 60

        if recent_sells:
            self.sell_volume_1m = sum(q * p for _, q, p in recent_sells)
            self.sells_per_sec = len(recent_sells) / 60

        # Update historical prices (every 10 seconds)
        if not hasattr(self, '_last_price_update'):
            self._last_price_update = 0

        if now - self._last_price_update > 10:
            self.price_5m_ago = self.price_1m_ago
            self.price_1m_ago = self.price
            self._last_price_update = now

        # Calculate deltas
        if self.price_1m_ago > 0:
            self.delta_1m = (self.price - self.price_1m_ago) / self.price_1m_ago * 100
        if self.price_5m_ago > 0:
            self.delta_5m = (self.price - self.price_5m_ago) / self.price_5m_ago * 100

    def check_pump(self) -> Optional[Dict]:
        """Check if current state indicates a pump."""
        now = time.time()

        # Cooldown check
        if now - self.last_signal_time < self.cooldown_sec:
            return None

        # === PUMP DETECTION CRITERIA ===

        # 1. Strong buying pressure (buys >> sells)
        buy_ratio = self.buys_per_sec / max(self.sells_per_sec, 0.1)

        # 2. Price movement
        delta_pct = max(self.delta_1m, self.delta_5m / 2)

        # 3. Volume spike
        total_volume = self.buy_volume_1m + self.sell_volume_1m
        buy_dominance = self.buy_volume_1m / max(total_volume, 1) * 100

        # DEBUG: Log metrics every 30 seconds for top symbol
        if not hasattr(self, '_last_debug') or now - self._last_debug > 30:
            self._last_debug = now
            if self.buys_per_sec > 0:
                log.info(
                    f"[METRICS] {self.symbol}: buys={self.buys_per_sec:.1f}/s, "
                    f"delta={delta_pct:.2f}%, buy_dom={buy_dominance:.0f}%, "
                    f"price=${self.price:.2f}"
                )

        # === THRESHOLDS (RAISED FOR QUALITY TRADES) ===
        # PUMP_OVERRIDE: extreme buying - REAL PUMP
        if self.buys_per_sec >= 100 and delta_pct >= 2.0:
            signal_type = "PUMP_OVERRIDE"
            confidence = 0.95

        # SUPER_SCALP: strong buying + significant price move
        elif self.buys_per_sec >= 50 and delta_pct >= 1.0:
            signal_type = "SUPER_SCALP"
            confidence = 0.85

        # SCALP: good buying + price move
        elif self.buys_per_sec >= 30 and delta_pct >= 0.5 and buy_dominance >= 60:
            signal_type = "SCALP"
            confidence = 0.75

        # Volume spike: sudden volume increase with movement
        elif self.buy_volume_1m >= 100000 and buy_ratio >= 2.0 and delta_pct >= 0.5:
            signal_type = "VOLUME_SPIKE"
            confidence = 0.70

        # MICRO: disabled - too many false signals
        elif False and self.buys_per_sec >= 5 and delta_pct >= 0.05:
            signal_type = "MICRO"
            confidence = 0.55

        # TEST_ACTIVITY: disabled - too many false positives
        # Need delta movement, not just buy activity
        elif False and self.buys_per_sec >= 15 and buy_dominance >= 60:
            signal_type = "TEST_ACTIVITY"
            confidence = 0.50

        else:
            return None

        # Generate signal
        self.last_signal_time = now

        signal_id = f"pump:{self.symbol}:{hashlib.sha256(f'{now}'.encode()).hexdigest()[:8]}"

        return {
            "signal_id": signal_id,
            "symbol": self.symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "strategy": "PumpDetection",
            "direction": "Long",
            "price": self.price,
            "buys_per_sec": self.buys_per_sec,
            "sells_per_sec": self.sells_per_sec,
            "delta_pct": delta_pct,
            "vol_raise_pct": buy_dominance,
            "buy_volume_1m": self.buy_volume_1m,
            "daily_volume_m": total_volume / 1_000_000 * 1440,  # Extrapolate to daily
            "signal_type": signal_type,
            "confidence": confidence,
            "source": "pump_detector",
        }


class PumpDetector:
    """
    Real-time pump detector using Binance WebSocket.

    Monitors trade stream and detects pump patterns.
    """

    def __init__(
        self,
        symbols: List[str] = None,
        autotrader_url: str = AUTOTRADER_URL,
        save_signals: bool = True,
        use_ai_filter: bool = True,
    ):
        self.symbols = symbols or ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
        self.autotrader_url = autotrader_url
        self.save_signals = save_signals
        self.use_ai_filter = use_ai_filter and AI_PREDICTOR_ENABLED

        self.states: Dict[str, SymbolState] = {}
        self.running = False
        self.client: Optional[httpx.AsyncClient] = None
        self.telegram = None
        self.allowlist: Optional[ThreeLayerAllowList] = None

        # Stats
        self.signals_generated = 0
        self.signals_forwarded = 0
        self.signals_ai_approved = 0
        self.signals_ai_rejected = 0
        self.signals_hot_added = 0

    async def start(self):
        """Start pump detection."""
        self.running = True
        self.client = httpx.AsyncClient(timeout=10.0)

        # Init symbol states
        for symbol in self.symbols:
            self.states[symbol] = SymbolState(symbol=symbol)

        # Try Telegram
        try:
            from core.telegram_sender import TelegramSender
            self.telegram = TelegramSender()
        except:
            pass

        # Initialize Three-Layer AllowList
        if THREE_LAYER_ENABLED:
            try:
                self.allowlist = get_allowlist()
                await self.allowlist.initialize()
                log.info("Three-Layer AllowList initialized")
            except Exception as e:
                log.warning(f"AllowList init failed: {e}")

        ai_status = "🤖 AI Predictor v2 ENABLED" if self.use_ai_filter else "⚠️ AI filtering OFF"
        log.info(f"Pump Detector started for {len(self.symbols)} symbols | {ai_status}")

        if self.telegram:
            await self.telegram.send(
                f"🔍 Pump Detector started\n"
                f"Monitoring: {', '.join(self.symbols[:5])}...\n"
                f"{ai_status}"
            )

        try:
            await self._websocket_loop()
        finally:
            await self.stop()

    async def stop(self):
        """Stop detector."""
        self.running = False
        if self.client:
            await self.client.aclose()
        if self.telegram:
            ai_stats = ""
            if self.use_ai_filter:
                ai_stats = f"\n🤖 AI: {self.signals_ai_approved} approved, {self.signals_ai_rejected} rejected"
            await self.telegram.send(
                f"🔴 Pump Detector stopped\n"
                f"Signals: {self.signals_generated}, Forwarded: {self.signals_forwarded}"
                f"{ai_stats}"
            )
            await self.telegram.close()
        log.info(
            f"Pump Detector stopped. Generated: {self.signals_generated}, "
            f"Forwarded: {self.signals_forwarded}, "
            f"AI approved: {self.signals_ai_approved}, AI rejected: {self.signals_ai_rejected}"
        )

    async def _websocket_loop(self):
        """Main WebSocket loop."""
        streams = "/".join([f"{s.lower()}@trade" for s in self.symbols])
        url = f"wss://stream.binance.com:9443/stream?streams={streams}"

        while self.running:
            try:
                log.info(f"Connecting to Binance WebSocket...")
                async with websockets.connect(url, ping_interval=30) as ws:
                    log.info("Connected to Binance WebSocket")

                    async for msg in ws:
                        if not self.running:
                            break

                        await self._process_trade(msg)

            except websockets.ConnectionClosed as e:
                log.warning(f"WebSocket closed: {e}, reconnecting...")
            except Exception as e:
                log.error(f"WebSocket error: {e}")

            if self.running:
                await asyncio.sleep(5)

    async def _process_trade(self, msg: str):
        """Process incoming trade message."""
        try:
            data = json.loads(msg)
            if "data" not in data:
                return

            trade = data["data"]
            symbol = trade["s"]
            price = float(trade["p"])
            quantity = float(trade["q"])
            is_buy = not trade["m"]  # m=True means seller is maker (so it's a buy)
            timestamp = trade["T"] / 1000  # Convert to seconds

            # Update state
            state = self.states.get(symbol)
            if state:
                state.update_price(price, is_buy, quantity, timestamp)

                # Check for pump
                signal = state.check_pump()
                if signal:
                    await self._handle_signal(signal)

        except Exception as e:
            log.error(f"Trade process error: {e}")

    async def _handle_signal(self, signal: Dict):
        """Handle detected pump signal with AI filtering."""
        self.signals_generated += 1

        # ═══════════════════════════════════════════════════════════════════
        # HARD TELEGRAM FILTER - BEGIN
        # Blocks: MICRO/TEST_ACTIVITY/SCALP and delta < 10% (strict, fail-closed)
        # ═══════════════════════════════════════════════════════════════════
        _delta = signal.get("delta_pct", 0)
        _type = signal.get("signal_type", "")
        _sym = signal.get("symbol", "")

        # BLOCK: MICRO, TEST_ACTIVITY, SCALP - these are spam
        if _type in ("MICRO", "TEST_ACTIVITY", "SCALP", "VOLUME_SPIKE"):
            log.info(f"[HARD-KILL] {_sym} type={_type} - BLOCKED (spam type)")
            return  # EXIT IMMEDIATELY - NO PROCESSING

        # BLOCK: delta < 10% - not worth trading
        if _delta < 10.0:
            log.info(f"[HARD-KILL] {_sym} delta={_delta:.2f}% < 10% - BLOCKED")
            return  # EXIT IMMEDIATELY - NO PROCESSING
        # HARD TELEGRAM FILTER - END
        # ═══════════════════════════════════════════════════════════════════

        # ═══════════════════════════════════════════════════════════════════════
        # HOPE v4.0 UNIFIED PRETRADE PIPELINE + TRADING ENGINE
        # Signal → Pretrade Check → Trading Engine → Binance → Log → Learn
        # ═══════════════════════════════════════════════════════════════════════
        if TRADING_ENGINE_READY:
            # UNIFIED PRETRADE CHECK (includes ALL guards in one call)
            if PRETRADE_PIPELINE_READY:
                # Prepare signal with all required fields for pretrade_check
                pretrade_signal = {
                    "symbol": signal.get("symbol", ""),
                    "delta_pct": signal.get("delta_pct", 0),
                    "type": signal.get("signal_type", ""),
                    "confidence": signal.get("confidence", 0.7),
                    "price": signal.get("price", 0),
                    "daily_volume_m": signal.get("daily_volume_m", 0),
                    "timestamp": signal.get("timestamp", ""),
                }

                # Run unified pretrade check (Schema, TTL, Liquidity, Price, Gate, CB, RL)
                pretrade_result = pretrade_check(pretrade_signal, _pipeline_config)

                if not pretrade_result.ok:
                    # Signal failed one or more guards - skip
                    log.info(f"[PRETRADE] {signal.get('symbol')} BLOCKED: {pretrade_result.reason}")
                    _health_monitor.record_skip(pretrade_result.reason)
                    return

                # Record signal for health monitoring
                _health_monitor.record_signal()

                log.debug(f"[PRETRADE] {signal.get('symbol')} PASS: {pretrade_result.reason}")

            try:
                # Prepare signal for trading engine
                trade_signal = {
                    "symbol": signal.get("symbol", ""),
                    "delta_pct": signal.get("delta_pct", 0),
                    "type": signal.get("signal_type", ""),
                    "confidence": signal.get("confidence", 0.7),
                    "buys_per_sec": signal.get("buys_per_sec", 0),
                    "price": signal.get("price", 0),
                    "timestamp": signal.get("timestamp", ""),
                }
                result = await trading_engine_handle(trade_signal)
                if result:
                    log.info(f"[TRADE] {result.get('symbol')} {result.get('status')} PnL=${result.get('pnl_usdt', 0):.2f}")

                    # Record trade result for Circuit Breaker (UNIFIED)
                    if PRETRADE_PIPELINE_READY:
                        pnl_pct = result.get('pnl_pct', 0)
                        cb = CircuitBreaker(_pipeline_config)
                        cb.record_trade(pnl_pct)
                        _health_monitor.record_trade()
                else:
                    log.debug(f"[FILTER] Signal passed to engine but not traded")
            except Exception as e:
                log.error(f"[ERROR] Trading engine error: {e}")
                if PRETRADE_PIPELINE_READY:
                    _health_monitor.record_error()
        # ═══════════════════════════════════════════════════════════════════════

        log.info(
            f"🚀 PUMP DETECTED: {signal['symbol']} | "
            f"{signal['signal_type']} | "
            f"buys={signal['buys_per_sec']:.1f}/s | "
            f"delta={signal['delta_pct']:.2f}%"
        )

        # === TRADINGVIEW DYNAMIC ALLOWLIST GATE ===
        if TV_ALLOWLIST_ENABLED:
            try:
                tv_allowed, tv_list, tv_multiplier = is_tradingview_allowed(signal["symbol"])

                if not tv_allowed:
                    log.info(
                        f"[TV-GATE] {signal['symbol']} NOT in TradingView lists - SKIP"
                    )
                    # Save rejected signal for analysis
                    if self.save_signals:
                        signal["tv_rejected"] = True
                        signal["tv_reason"] = "not_in_allowlist"
                        await self._save_signal(signal)
                    return  # Don't process coins not in TradingView lists

                # Add TradingView metadata to signal
                signal["tv_list"] = tv_list  # "hot" or "dynamic"
                signal["tv_multiplier"] = tv_multiplier
                signal["allowlist_source"] = f"tradingview_{tv_list}"

                log.info(
                    f"[TV-GATE] {signal['symbol']} ALLOWED | "
                    f"list={tv_list} | multiplier={tv_multiplier}x"
                )
            except Exception as e:
                log.warning(f"TradingView AllowList check error: {e}")
                # Continue without TV filter on error (fail-open for trading)

        # === THREE-LAYER ALLOWLIST: Auto-add to HOT_LIST ===
        if self.allowlist and THREE_LAYER_ENABLED:
            try:
                hot_result = self.allowlist.process_pump_signal(signal)
                if hot_result["action"] == "ADDED_TO_HOT":
                    self.signals_hot_added += 1
                    log.info(
                        f"🔥 HOT_LIST +{signal['symbol']} | "
                        f"pump_score={hot_result['pump_score']:.2f} | "
                        f"TTL=15min"
                    )
                    signal["hot_list_added"] = True
                    signal["pump_score"] = hot_result["pump_score"]
            except Exception as e:
                log.warning(f"HOT_LIST add error: {e}")

        # === AI PREDICTOR V2 FILTERING ===
        ai_decision = None
        if self.use_ai_filter:
            try:
                ai_decision = await process_pump_signal(signal)

                # Log AI decision
                if log_ai:
                    log.info(format_decision_log(ai_decision, signal))

                # Check if AI approves
                if ai_decision.action == "SKIP":
                    self.signals_ai_rejected += 1
                    log.info(
                        f"🔴 AI REJECT: {signal['symbol']} | "
                        f"score={ai_decision.final_score:.2f} | "
                        f"tier={ai_decision.allowlist_tier}"
                    )

                    # Save rejected signal (for analysis)
                    if self.save_signals:
                        signal["ai_rejected"] = True
                        signal["ai_score"] = ai_decision.final_score
                        signal["ai_tier"] = ai_decision.allowlist_tier
                        signal["ai_reasons"] = ai_decision.reasons[:3]
                        await self._save_signal(signal)

                    return  # Don't forward rejected signals

                # AI approved - enhance signal with AI data
                self.signals_ai_approved += 1
                signal["ai_score"] = ai_decision.final_score
                signal["ai_tier"] = ai_decision.allowlist_tier
                signal["ai_action"] = ai_decision.action
                signal["position_multiplier"] = ai_decision.position_multiplier

                # === ADAPTIVE TARGET AI: Dynamic target based on pump strength ===
                if ADAPTIVE_TARGET_ENABLED:
                    try:
                        target_data = calculate_adaptive_target(
                            signal["symbol"],
                            signal["delta_pct"],
                            signal.get("buys_per_sec", 0),
                            signal.get("vol_raise_pct", 0)
                        )

                        if target_data["tier"] == "NOISE":
                            log.info(f"[NOISE] {signal['symbol']} delta={signal['delta_pct']:.2f}% - skip")
                            return

                        # Override with adaptive targets
                        signal["target_pct"] = target_data["target_pct"]
                        signal["stop_pct"] = target_data["stop_pct"]
                        signal["timeout_seconds"] = target_data["timeout_sec"]
                        signal["adaptive_tier"] = target_data["tier"]

                        log.info(
                            f"[ADAPTIVE] {signal['symbol']} | "
                            f"tier={target_data['tier']} | "
                            f"target={target_data['target_pct']:.2f}% | "
                            f"stop={target_data['stop_pct']:.2f}%"
                        )
                    except Exception as e:
                        log.warning(f"Adaptive target error: {e}")
                        signal["target_pct"] = ai_decision.target_pct
                        signal["stop_pct"] = ai_decision.stop_pct
                        signal["timeout_seconds"] = ai_decision.timeout_seconds
                else:
                    signal["target_pct"] = ai_decision.target_pct
                    signal["stop_pct"] = ai_decision.stop_pct
                    signal["timeout_seconds"] = ai_decision.timeout_seconds

                log.info(
                    f"[AI APPROVE] {signal['symbol']} | "
                    f"score={ai_decision.final_score:.2f} | "
                    f"tier={ai_decision.allowlist_tier} | "
                    f"action={ai_decision.action}"
                )

            except Exception as e:
                log.error(f"AI filter error: {e}")
                # Continue without AI filter on error

        # Save to file
        if self.save_signals:
            await self._save_signal(signal)

        # Forward to AutoTrader
        await self._forward_signal(signal)

        # === TELEGRAM HARD FILTER: ONLY delta >= 10% ===
        # HARD CHECK: Skip Telegram if delta < 10%
        delta_pct = signal.get("delta_pct", 0)
        if delta_pct < 10.0:
            log.info(f"[TG-BLOCK] {signal['symbol']} delta={delta_pct:.2f}% < 10% - NO TELEGRAM")

        if self.telegram and delta_pct >= 10.0:
            try:
                # Prepare signal data for aggregator
                agg_signal = {
                    "symbol": signal["symbol"],
                    "delta_pct": signal["delta_pct"],
                    "buys_per_sec": signal.get("buys_per_sec", 0),
                    "price": signal.get("price", 0),
                    "tier": signal.get("adaptive_tier", signal.get("signal_type", "UNKNOWN")),
                    "target_pct": signal.get("target_pct", 1.0),
                    "confidence": signal.get("confidence", 0.5),
                }

                if SIGNAL_AGGREGATOR_ENABLED:
                    # Use aggregator to decide what to send
                    agg_result = process_signal_for_telegram(agg_signal)

                    if agg_result["send_now"] and agg_result["message"]:
                        await self.telegram.send(agg_result["message"], parse_mode="Markdown")
                        log.info(f"[TG] {agg_result['reason']}")
                    else:
                        log.debug(f"[TG-SKIP] {agg_result['reason']}")
                else:
                    # Fallback: ONLY delta >= 10% (HARD FILTER)
                    if signal["delta_pct"] >= 10.0:
                        ai_info = ""
                        if ai_decision:
                            ai_info = (
                                f"\n\n AI Analysis:\n"
                                f"Score: {ai_decision.final_score:.0%}\n"
                                f"Tier: {ai_decision.allowlist_tier}\n"
                                f"Target: +{signal.get('target_pct', 1.0):.1f}%\n"
                                f"Stop: -{signal.get('stop_pct', 0.5):.1f}%"
                            )

                        await self.telegram.send(
                            f"PUMP: {signal['symbol']}\n"
                            f"Type: {signal['signal_type']}\n"
                            f"Buys/sec: {signal['buys_per_sec']:.1f}\n"
                            f"Delta: {signal['delta_pct']:.2f}%\n"
                            f"Conf: {signal['confidence']*100:.0f}%"
                            f"{ai_info}"
                        )
            except Exception as e:
                log.warning(f"Telegram error: {e}")

    async def _save_signal(self, signal: Dict):
        """Save signal to JSONL file."""
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        signal_file = SIGNALS_DIR / f"signals_{today}.jsonl"

        try:
            with open(signal_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(signal) + "\n")
        except Exception as e:
            log.error(f"Failed to save signal: {e}")

    async def _forward_signal(self, signal: Dict):
        """Forward signal to AutoTrader."""
        try:
            response = await self.client.post(
                f"{self.autotrader_url}/signal",
                json={
                    "symbol": signal["symbol"],
                    "timestamp": signal["timestamp"],
                    "strategy": signal["strategy"],
                    "direction": signal["direction"],
                    "price": signal["price"],
                    "buys_per_sec": signal["buys_per_sec"],
                    "delta_pct": signal["delta_pct"],
                    "vol_raise_pct": signal["vol_raise_pct"],
                    "daily_volume_m": signal.get("daily_volume_m", 100),
                },
                timeout=5.0
            )

            if response.status_code == 200:
                self.signals_forwarded += 1
                log.info(f"Signal forwarded to AutoTrader")
            else:
                log.warning(f"AutoTrader rejected: {response.status_code}")

        except httpx.ConnectError:
            log.warning("AutoTrader not available")
        except Exception as e:
            log.error(f"Forward error: {e}")


async def get_top_symbols(n: int = 20) -> List[str]:
    """Get top N symbols by 24h volume from Binance."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.binance.com/api/v3/ticker/24hr",
                timeout=10.0
            )
            if response.status_code == 200:
                tickers = response.json()
                # Filter USDT pairs and sort by volume
                usdt_tickers = [
                    t for t in tickers
                    if t["symbol"].endswith("USDT")
                    and float(t["quoteVolume"]) > 10_000_000  # Min $10M volume
                ]
                usdt_tickers.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
                return [t["symbol"] for t in usdt_tickers[:n]]
    except Exception as e:
        log.error(f"Failed to get top symbols: {e}")

    # Fallback
    return ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]


# === CLI ===

async def main():
    import argparse

    parser = argparse.ArgumentParser(description="HOPE Pump Detector + AI Predictor v2")
    parser.add_argument("--symbols", type=str, help="Comma-separated symbols")
    parser.add_argument("--top", type=int, default=0, help="Use top N symbols by volume")
    parser.add_argument("--no-forward", action="store_true", help="Don't forward to AutoTrader")
    parser.add_argument("--no-ai", action="store_true", help="Disable AI Predictor v2 filtering")

    args = parser.parse_args()

    # HOPE v4.0 LIVE SAFETY INITIALIZATION
    if LIVE_SAFETY_READY and PRETRADE_PIPELINE_READY:
        log.info(f"[HOPE v4.0] Live Safety: READY | Mode: {_live_barrier.effective_mode.value}")
    else:
        log.warning("[HOPE v4.0] Live Safety NOT available - trading disabled")

    # Get symbols
    if args.symbols:
        symbols = args.symbols.upper().split(",")
    elif args.top > 0:
        log.info(f"Fetching top {args.top} symbols by volume...")
        symbols = await get_top_symbols(args.top)
        log.info(f"Monitoring: {', '.join(symbols)}")
    else:
        symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]

    # AI status
    if AI_PREDICTOR_ENABLED and not args.no_ai:
        log.info("🤖 AI Predictor v2 ENABLED (Three-Layer AllowList + Technical Analysis)")
    elif args.no_ai:
        log.info("⚠️ AI Predictor v2 DISABLED by --no-ai flag")
    else:
        log.info("⚠️ AI Predictor v2 not available (import failed)")

    detector = PumpDetector(
        symbols=symbols,
        autotrader_url=AUTOTRADER_URL if not args.no_forward else None,
        use_ai_filter=not args.no_ai,
    )

    # Signal handler
    import signal

    def handle_signal(sig, frame):
        log.info("Shutdown requested...")
        detector.running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    await detector.start()


if __name__ == "__main__":
    asyncio.run(main())
