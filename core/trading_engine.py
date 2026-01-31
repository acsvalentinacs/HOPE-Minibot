# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 16:00:00 UTC
# Modified by: Claude (opus-4.5)
# Modified at: 2026-01-31 10:40:00 UTC
# Purpose: HOPE Trading Engine with WATCHDOG INTEGRATION
# FIX: Added register_position_for_watching() - no more naked positions
# sha256: trading_engine_v1.1_watchdog
# === END SIGNATURE ===
"""
══════════════════════════════════════════════════════════════════════════════
HOPE TRADING ENGINE v1.1 - ПОЛНЫЙ ТОРГОВЫЙ ЦИКЛ + WATCHDOG
══════════════════════════════════════════════════════════════════════════════

Signal → Signal Gate → Adaptive TP → Binance Executor → WATCHDOG → Logger

ИНТЕГРАЦИЯ ВСЕХ КОМПОНЕНТОВ:
  - core/signal_gate.py
  - core/adaptive_tp_engine.py
  - execution/binance_oco_executor.py
  - scripts/position_watchdog.py  ← NEW: Position watchdog registration
  - learning/trade_outcome_logger.py
  - config/live_trade_policy.py

══════════════════════════════════════════════════════════════════════════════
"""

import os
import sys
import time
import json
import logging
import asyncio
from typing import Dict, Any, Optional
from dataclasses import dataclass

# Setup path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.signal_gate import SignalGate, SignalGateConfig, GateDecision
from core.adaptive_tp_engine import calculate_adaptive_tp, SignalTier
from execution.binance_oco_executor import BinanceOCOExecutor, ExecutorConfig, ExecutionMode, TradeResult
from learning.trade_outcome_logger import TradeOutcomeLogger, TradeOutcome, get_trade_logger
from config.live_trade_policy import check_symbol_allowed, MAX_POSITION_USDT

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# TRADING ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class HopeTradingEngine:
    """
    Полный торговый цикл.
    
    FLOW:
      1. Receive signal
      2. Signal Gate check (cannot bypass)
      3. Adaptive TP/SL calculation
      4. Execute on Binance
      5. Log outcome
      6. Update learning
    """
    
    def __init__(self, mode: str = "LIVE"):
        self.mode = ExecutionMode[mode.upper()]
        
        # Components
        self.gate = SignalGate()
        self.executor = BinanceOCOExecutor(ExecutorConfig(
            mode=self.mode,
            max_position_usdt=MAX_POSITION_USDT,
        ))
        self.logger = get_trade_logger()
        
        # State
        self._running = False
        self._trades_today = 0
        self._pnl_today = 0.0
    
    async def process_signal(self, signal: Dict[str, Any]) -> Optional[TradeResult]:
        """
        Обработать сигнал через полный торговый цикл.
        
        Returns:
            TradeResult если сделка выполнена, None если сигнал отклонён
        """
        
        symbol = signal.get("symbol", "").upper()
        delta_pct = float(signal.get("delta_pct", 0))
        signal_type = signal.get("type", "")
        confidence = float(signal.get("confidence", 0.7))
        
        log.info(f"Processing signal: {symbol} delta={delta_pct:.2f}% type={signal_type}")
        
        # ═══════════════════════════════════════════════════════════════════
        # STEP 1: Signal Gate (CANNOT BYPASS)
        # ═══════════════════════════════════════════════════════════════════
        
        decision, block_reason, details = self.gate.check(signal)

        # Check for momentum override (24h trending signals bypass low-delta gate)
        is_momentum = signal_type in ("MOMENTUM_24H", "TRENDING")
        has_ai_override = signal.get("ai_override", False)

        if decision == GateDecision.BLOCK:
            # BLOCK is absolute - no override
            log.info(f"Signal BLOCKED: {block_reason.value if block_reason else 'unknown'}")
            self.logger.log_signal(signal, "blocked", str(block_reason.value if block_reason else ""))
            return None

        if decision == GateDecision.PASS_LOG_ONLY:
            # LOG_ONLY can be overridden by momentum signals
            if is_momentum or has_ai_override:
                log.info(f"[MOMENTUM-OVERRIDE] {symbol} | type={signal_type} | "
                        f"ai_override={has_ai_override} - bypassing LOG_ONLY")
            else:
                log.info(f"Signal LOG_ONLY: {block_reason.value if block_reason else details}")
                self.logger.log_signal(signal, "log_only", str(block_reason.value if block_reason else ""))
                return None
        
        # ═══════════════════════════════════════════════════════════════════
        # STEP 2: Live Trade Policy Check
        # ═══════════════════════════════════════════════════════════════════
        
        allowed, reason = check_symbol_allowed(symbol)
        if not allowed:
            log.info(f"Symbol not allowed: {reason}")
            self.logger.log_signal(signal, "policy_blocked", reason)
            return None
        
        # ═══════════════════════════════════════════════════════════════════
        # STEP 3: Adaptive TP/SL
        # ═══════════════════════════════════════════════════════════════════
        
        tp_result = calculate_adaptive_tp(delta_pct, confidence)
        
        if not tp_result.should_trade:
            log.info(f"Trade skipped by TP engine: {tp_result.reason}")
            self.logger.log_signal(signal, "tp_rejected", tp_result.reason)
            return None
        
        log.info(f"TP calculated: TP={tp_result.target_pct}% SL={tp_result.stop_loss_pct}% "
                 f"R:R={tp_result.effective_rr} Position={tp_result.position_mult*100:.0f}%")
        
        # ═══════════════════════════════════════════════════════════════════
        # STEP 4: Calculate Position Size
        # ═══════════════════════════════════════════════════════════════════
        
        position_usdt = MAX_POSITION_USDT * tp_result.position_mult
        
        # ═══════════════════════════════════════════════════════════════════
        # STEP 5: Execute Trade
        # ═══════════════════════════════════════════════════════════════════
        
        log.info(f"Executing: {symbol} BUY ${position_usdt:.2f}")
        
        result = await self.executor.execute_trade(
            symbol=symbol,
            side="BUY",
            position_usdt=position_usdt,
            tp_pct=tp_result.target_pct,
            sl_pct=tp_result.stop_loss_pct,
            timeout_sec=tp_result.timeout_sec,
            signal_data=signal,
        )

        # ═══════════════════════════════════════════════════════════════════
        # STEP 5.5: Register with Watchdog (CRITICAL - FAIL-CLOSED)
        # ═══════════════════════════════════════════════════════════════════

        if result.entry_price > 0 and result.quantity > 0:
            try:
                from scripts.position_watchdog import register_position_for_watching
                register_position_for_watching(
                    position_id=f"pos_{int(result.entry_time * 1000)}_{symbol}",
                    symbol=symbol,
                    entry_price=result.entry_price,
                    quantity=result.quantity,
                    target_pct=tp_result.target_pct,
                    stop_pct=abs(tp_result.stop_loss_pct),
                    timeout_sec=tp_result.timeout_sec,
                )
                log.info(f"Registered with watchdog: {symbol}")
            except Exception as e:
                log.error(f"CRITICAL: Watchdog registration failed: {e}")
                # FAIL-CLOSED: Activate kill switch if cannot register
                self.emergency_stop()
                log.critical(f"Kill switch activated due to watchdog failure")

        # ═══════════════════════════════════════════════════════════════════
        # STEP 6: Log Outcome
        # ═══════════════════════════════════════════════════════════════════
        
        outcome = TradeOutcome(
            timestamp=result.entry_time,
            symbol=result.symbol,
            side=result.side,
            entry_price=result.entry_price,
            exit_price=result.exit_price,
            pnl_usdt=result.pnl_usdt,
            pnl_pct=result.pnl_pct,
            status=result.status.value,
            duration_sec=result.duration_sec,
            signal_delta_pct=delta_pct,
            signal_type=signal_type,
            tp_target_pct=tp_result.target_pct,
            sl_target_pct=tp_result.stop_loss_pct,
        )
        
        self.logger.log_trade(outcome)
        
        # Update daily stats
        self._trades_today += 1
        self._pnl_today += result.pnl_usdt
        
        log.info(f"Trade completed: {result.status.value} PnL=${result.pnl_usdt:.2f} "
                 f"Daily: {self._trades_today} trades, ${self._pnl_today:.2f}")
        
        return result
    
    def get_status(self) -> Dict[str, Any]:
        """Получить статус engine."""
        return {
            "mode": self.mode.value,
            "trades_today": self._trades_today,
            "pnl_today": round(self._pnl_today, 2),
            "active_positions": len(self.executor.get_active_positions()),
            "daily_pnl": round(self.executor.get_daily_pnl(), 2),
            "stats": self.logger.get_stats(),
        }
    
    def emergency_stop(self):
        """Аварийная остановка."""
        self.executor.activate_kill_switch()
        log.critical("🛑 EMERGENCY STOP ACTIVATED")


# ══════════════════════════════════════════════════════════════════════════════
# FACTORY
# ══════════════════════════════════════════════════════════════════════════════

_engine: Optional[HopeTradingEngine] = None

def get_trading_engine(mode: str = "LIVE") -> HopeTradingEngine:
    """Получить trading engine."""
    global _engine
    if _engine is None:
        _engine = HopeTradingEngine(mode)
    return _engine


# ══════════════════════════════════════════════════════════════════════════════
# INTEGRATION POINT FOR PUMP_DETECTOR
# ══════════════════════════════════════════════════════════════════════════════

async def handle_signal(signal: Dict[str, Any]) -> Optional[Dict]:
    """
    Entry point для pump_detector.
    
    Использование в pump_detector.py:
    
        from core.trading_engine import handle_signal
        
        async def _emit_signal(self, signal):
            result = await handle_signal(signal)
            if result:
                # Сделка выполнена
                pass
    """
    engine = get_trading_engine()
    result = await engine.process_signal(signal)
    if result:
        return result.to_dict()
    return None


# ══════════════════════════════════════════════════════════════════════════════
# TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    
    async def test():
        print("=" * 60)
        print("HOPE TRADING ENGINE TEST (DRY MODE)")
        print("=" * 60)
        
        engine = HopeTradingEngine(mode="DRY")
        
        # Test signals
        signals = [
            {"symbol": "PEPEUSDT", "delta_pct": 15.0, "type": "EXPLOSION", "confidence": 0.8},
            {"symbol": "BTCUSDT", "delta_pct": 5.0, "type": "PUMP", "confidence": 0.7},
            {"symbol": "ENSOUSDT", "delta_pct": 28.0, "type": "MOONSHOT", "confidence": 0.9},
            {"symbol": "ADAUSDT", "delta_pct": 0.5, "type": "MICRO", "confidence": 0.5},
        ]
        
        for sig in signals:
            print(f"\n--- Processing: {sig['symbol']} delta={sig['delta_pct']}% ---")
            result = await engine.process_signal(sig)
            if result:
                print(f"Result: {result.status.value} PnL=${result.pnl_usdt:.2f}")
            else:
                print("Signal rejected")
        
        print("\n" + "=" * 60)
        print("ENGINE STATUS:")
        print(json.dumps(engine.get_status(), indent=2))
        print("\n[PASS] Trading Engine test")
    
    asyncio.run(test())
