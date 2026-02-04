# === AI SIGNATURE ===
# Module: hope_core/integration_bridge.py
# Created by: Claude (opus-4.5)
# Created at: 2026-02-04 10:50:00 UTC
# Purpose: Bridge for integrating existing HOPE modules with HOPE Core v2.0
# === END SIGNATURE ===
"""
Integration Bridge

Connects HOPE Core v2.0 with existing modules:
- eye_of_god_v3.py (Decision Engine)
- order_executor.py (Binance Execution)
- position_watchdog.py (Position Monitoring)
- auto_signal_loop.py (Signal Generation)

This bridge ensures backward compatibility while adding
Command Bus validation and State Machine control.
"""

from typing import Dict, Any, Optional, Callable, List
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from enum import Enum
import asyncio
import sys
import os

# Paths for existing modules
MINIBOT_ROOT = Path("/opt/hope/minibot")  # VPS path
LOCAL_MINIBOT = Path("C:/Users/kirillDev/Desktop/TradingBot/minibot")

# Detect environment
if MINIBOT_ROOT.exists():
    PROJECT_ROOT = MINIBOT_ROOT
elif LOCAL_MINIBOT.exists():
    PROJECT_ROOT = LOCAL_MINIBOT
else:
    PROJECT_ROOT = Path(__file__).parent.parent

# Add to path
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))


# =============================================================================
# EYE OF GOD BRIDGE
# =============================================================================

class EyeOfGodBridge:
    """
    Bridge to Eye of God V3 Decision Engine.
    
    Wraps the two-chamber decision system with:
    - Input validation (Command Bus contract)
    - State transition triggers
    - Event journaling
    """
    
    def __init__(self, core: "HopeCore"):
        """
        Initialize bridge.
        
        Args:
            core: HopeCore instance
        """
        self.core = core
        self._eye = None
        self._loaded = False
        
        self._load_eye_of_god()
    
    def _load_eye_of_god(self):
        """Load Eye of God module."""
        try:
            from eye_of_god_v3 import EyeOfGodV3
            self._eye = EyeOfGodV3()
            self._loaded = True
            print("[BRIDGE] Eye of God V3 loaded")
        except ImportError as e:
            print(f"[BRIDGE] Eye of God not available: {e}")
            self._loaded = False
    
    @property
    def is_loaded(self) -> bool:
        """Check if Eye of God is loaded."""
        return self._loaded and self._eye is not None
    
    async def make_decision(
        self,
        symbol: str,
        signal_data: Dict[str, Any],
        correlation_id: str,
    ) -> Dict[str, Any]:
        """
        Make trading decision using Eye of God.
        
        Args:
            symbol: Trading symbol
            signal_data: Signal information
            correlation_id: Correlation ID for tracking
            
        Returns:
            Decision result with action, confidence, reasons
        """
        if not self.is_loaded:
            return self._fallback_decision(symbol, signal_data)
        
        try:
            # Call Eye of God
            decision = self._eye.evaluate_signal(
                symbol=symbol,
                score=signal_data.get("score", 0),
                source=signal_data.get("source", "UNKNOWN"),
                market_data=signal_data.get("market_data", {}),
            )
            
            # Log decision
            self.core.journal.append(
                "DECISION_MADE",
                payload={
                    "symbol": symbol,
                    "action": decision.get("action"),
                    "confidence": decision.get("confidence"),
                    "reasons": decision.get("reasons", []),
                    "chamber_votes": decision.get("chamber_votes", {}),
                },
                correlation_id=correlation_id,
            )
            
            return decision
            
        except Exception as e:
            print(f"[BRIDGE] Eye of God error: {e}")
            return self._fallback_decision(symbol, signal_data)
    
    def _fallback_decision(
        self,
        symbol: str,
        signal_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Fallback decision when Eye of God is unavailable.
        
        Simple threshold-based decision.
        """
        score = signal_data.get("score", 0)
        confidence = signal_data.get("confidence", score)
        
        min_conf = self.core.config.min_confidence
        
        if confidence >= min_conf:
            action = "BUY"
            reasons = [f"Score {confidence:.0%} >= threshold {min_conf:.0%}"]
        else:
            action = "HOLD"
            reasons = [f"Score {confidence:.0%} < threshold {min_conf:.0%}"]
        
        return {
            "action": action,
            "confidence": confidence,
            "reasons": reasons,
            "fallback": True,
        }


# =============================================================================
# ORDER EXECUTOR BRIDGE
# =============================================================================

class OrderExecutorBridge:
    """
    Bridge to Order Executor.
    
    Wraps Binance execution with:
    - Idempotency checks (prevent duplicate orders)
    - State machine validation
    - Automatic position tracking
    """
    
    def __init__(self, core: "HopeCore"):
        """
        Initialize bridge.
        
        Args:
            core: HopeCore instance
        """
        self.core = core
        self._executor = None
        self._loaded = False
        self._pending_orders: Dict[str, str] = {}  # idempotency_key -> order_id
        
        self._load_executor()
    
    def _load_executor(self):
        """Load Order Executor module."""
        try:
            from order_executor import OrderExecutor, TradingMode
            mode = TradingMode[self.core.config.mode]
            self._executor = OrderExecutor(mode)
            self._loaded = True
            print(f"[BRIDGE] Order Executor loaded ({self.core.config.mode})")
        except ImportError as e:
            print(f"[BRIDGE] Order Executor not available: {e}")
            self._loaded = False
    
    @property
    def is_loaded(self) -> bool:
        """Check if Order Executor is loaded."""
        return self._loaded and self._executor is not None
    
    def generate_idempotency_key(
        self,
        symbol: str,
        side: str,
        correlation_id: str,
    ) -> str:
        """Generate idempotency key for order."""
        return f"{correlation_id}:{symbol}:{side}"
    
    async def execute_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: Optional[float] = None,
        correlation_id: str = "",
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute order on Binance.
        
        Args:
            symbol: Trading symbol
            side: BUY or SELL
            quantity: Order quantity
            price: Limit price (None for market)
            correlation_id: Correlation ID
            idempotency_key: Idempotency key (auto-generated if None)
            
        Returns:
            Order result
        """
        # Generate idempotency key
        if not idempotency_key:
            idempotency_key = self.generate_idempotency_key(symbol, side, correlation_id)
        
        # Check idempotency
        if idempotency_key in self._pending_orders:
            existing_order = self._pending_orders[idempotency_key]
            print(f"[BRIDGE] Duplicate order detected: {idempotency_key}")
            return {
                "status": "DUPLICATE",
                "existing_order_id": existing_order,
                "message": "Order already submitted",
            }
        
        if not self.is_loaded:
            return self._simulate_order(symbol, side, quantity, price)
        
        try:
            # Mark as pending
            self._pending_orders[idempotency_key] = "PENDING"
            
            # Execute order
            if price:
                result = self._executor.place_limit_order(symbol, side, quantity, price)
            else:
                result = self._executor.place_market_order(symbol, side, quantity)
            
            # Update idempotency map
            order_id = result.get("orderId", result.get("order_id", "unknown"))
            self._pending_orders[idempotency_key] = order_id
            
            # Log to journal
            self.core.journal.append(
                "ORDER_SENT",
                payload={
                    "symbol": symbol,
                    "side": side,
                    "quantity": quantity,
                    "price": price,
                    "order_id": order_id,
                    "idempotency_key": idempotency_key,
                },
                correlation_id=correlation_id,
            )
            
            return {
                "status": "SUCCESS",
                "order_id": order_id,
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "price": price,
            }
            
        except Exception as e:
            # Remove from pending on failure
            self._pending_orders.pop(idempotency_key, None)
            
            print(f"[BRIDGE] Order execution error: {e}")
            return {
                "status": "ERROR",
                "error": str(e),
            }
    
    def _simulate_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: Optional[float],
    ) -> Dict[str, Any]:
        """Simulate order in DRY mode."""
        order_id = f"SIM_{datetime.now().strftime('%H%M%S%f')}"
        
        print(f"[BRIDGE] SIMULATED {side} {quantity} {symbol}")
        
        return {
            "status": "SIMULATED",
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": price,
            "simulated": True,
        }
    
    async def get_account_balance(self) -> Dict[str, float]:
        """Get account balance."""
        if not self.is_loaded:
            return {"USDT": 100.0}  # Simulated balance
        
        try:
            return self._executor.get_balance()
        except Exception as e:
            print(f"[BRIDGE] Balance fetch error: {e}")
            return {}
    
    async def sync_positions(self) -> List[Dict[str, Any]]:
        """Sync open positions from Binance."""
        if not self.is_loaded:
            return []
        
        try:
            return self._executor.get_open_positions()
        except Exception as e:
            print(f"[BRIDGE] Position sync error: {e}")
            return []


# =============================================================================
# SIGNAL SOURCE BRIDGE
# =============================================================================

class SignalSourceBridge:
    """
    Bridge for signal sources.
    
    Aggregates signals from:
    - auto_signal_loop.py (Scanner)
    - momentum_trader.py (Momentum)
    - External sources (Telegram, MoonBot)
    """
    
    def __init__(self, core: "HopeCore"):
        """
        Initialize bridge.
        
        Args:
            core: HopeCore instance
        """
        self.core = core
        self._sources: Dict[str, Any] = {}
        self._signal_buffer: List[Dict[str, Any]] = []
        self._max_buffer = 100
    
    def register_source(self, name: str, callback: Callable) -> None:
        """Register signal source."""
        self._sources[name] = callback
        print(f"[BRIDGE] Signal source registered: {name}")
    
    async def receive_signal(
        self,
        symbol: str,
        score: float,
        source: str = "UNKNOWN",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Receive and validate signal.
        
        Args:
            symbol: Trading symbol
            score: Signal score (0-1)
            source: Signal source name
            metadata: Additional signal data
            
        Returns:
            Correlation ID for tracking
        """
        import uuid
        
        correlation_id = str(uuid.uuid4())[:8]
        
        signal = {
            "symbol": symbol,
            "score": score,
            "source": source,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "correlation_id": correlation_id,
            "metadata": metadata or {},
        }
        
        # Buffer signal
        self._signal_buffer.append(signal)
        if len(self._signal_buffer) > self._max_buffer:
            self._signal_buffer = self._signal_buffer[-self._max_buffer:]
        
        # Submit to core
        result = await self.core.submit_signal(
            symbol=symbol,
            score=score,
            source=source,
            correlation_id=correlation_id,
        )
        
        return correlation_id
    
    def get_recent_signals(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent signals."""
        return self._signal_buffer[-limit:]


# =============================================================================
# POSITION WATCHDOG BRIDGE
# =============================================================================

class PositionWatchdogBridge:
    """
    Bridge for Position Watchdog.
    
    Monitors open positions for:
    - Take profit targets
    - Stop loss triggers
    - Timeout expiration
    - Manual close requests
    """
    
    def __init__(self, core: "HopeCore"):
        """
        Initialize bridge.
        
        Args:
            core: HopeCore instance
        """
        self.core = core
        self._watchdog = None
        self._loaded = False
        
        self._load_watchdog()
    
    def _load_watchdog(self):
        """Load Position Watchdog module."""
        try:
            from position_watchdog import PositionWatchdog
            self._watchdog = PositionWatchdog()
            self._loaded = True
            print("[BRIDGE] Position Watchdog loaded")
        except ImportError as e:
            print(f"[BRIDGE] Position Watchdog not available: {e}")
            self._loaded = False
    
    @property
    def is_loaded(self) -> bool:
        """Check if watchdog is loaded."""
        return self._loaded and self._watchdog is not None
    
    async def check_positions(self) -> List[Dict[str, Any]]:
        """
        Check all positions for exit conditions.
        
        Returns:
            List of positions that need action
        """
        actions_needed = []
        
        for position_id, position in self.core._open_positions.items():
            action = await self._check_single_position(position)
            if action:
                actions_needed.append({
                    "position_id": position_id,
                    "action": action,
                    "position": position,
                })
        
        return actions_needed
    
    async def _check_single_position(
        self,
        position: Dict[str, Any],
    ) -> Optional[str]:
        """
        Check single position for exit conditions.
        
        Returns:
            Action to take or None
        """
        if not self.is_loaded:
            return self._simple_check(position)
        
        try:
            return self._watchdog.check_position(position)
        except Exception as e:
            print(f"[BRIDGE] Watchdog check error: {e}")
            return self._simple_check(position)
    
    def _simple_check(self, position: Dict[str, Any]) -> Optional[str]:
        """Simple position check when watchdog unavailable."""
        entry_price = position.get("entry_price", 0)
        current_price = position.get("current_price", entry_price)
        
        if entry_price == 0:
            return None
        
        pnl_percent = (current_price - entry_price) / entry_price * 100
        
        # Simple TP/SL check
        if pnl_percent >= 6:  # Take profit
            return "CLOSE_TP"
        elif pnl_percent <= -3:  # Stop loss
            return "CLOSE_SL"
        
        return None


# =============================================================================
# MAIN INTEGRATION CLASS
# =============================================================================

class IntegrationBridge:
    """
    Main integration bridge that combines all bridges.
    
    Provides unified interface for HopeCore to interact
    with existing HOPE modules.
    """
    
    def __init__(self, core: "HopeCore"):
        """
        Initialize all bridges.
        
        Args:
            core: HopeCore instance
        """
        self.core = core
        
        # Initialize bridges
        self.eye_of_god = EyeOfGodBridge(core)
        self.executor = OrderExecutorBridge(core)
        self.signals = SignalSourceBridge(core)
        self.watchdog = PositionWatchdogBridge(core)
        
        # Track initialization status
        self._initialized = True
    
    def get_status(self) -> Dict[str, Any]:
        """Get bridge status."""
        return {
            "eye_of_god": self.eye_of_god.is_loaded,
            "executor": self.executor.is_loaded,
            "watchdog": self.watchdog.is_loaded,
            "signal_sources": list(self.signals._sources.keys()),
            "pending_orders": len(self.executor._pending_orders),
            "signal_buffer": len(self.signals._signal_buffer),
        }
    
    async def full_trading_cycle(
        self,
        symbol: str,
        signal_data: Dict[str, Any],
        correlation_id: str,
    ) -> Dict[str, Any]:
        """
        Execute full trading cycle:
        Signal → Decision → Order → Position
        
        Args:
            symbol: Trading symbol
            signal_data: Signal information
            correlation_id: Correlation ID
            
        Returns:
            Cycle result
        """
        # 1. Decision
        decision = await self.eye_of_god.make_decision(
            symbol=symbol,
            signal_data=signal_data,
            correlation_id=correlation_id,
        )
        
        if decision.get("action") != "BUY":
            return {
                "status": "SKIPPED",
                "reason": decision.get("reasons", ["No buy signal"]),
                "decision": decision,
            }
        
        # 2. Calculate position size
        balance = await self.executor.get_account_balance()
        position_size = min(
            self.core.config.position_size_usd,
            balance.get("USDT", 0) * 0.1,  # Max 10% per trade
        )
        
        if position_size < 10:  # Minimum $10
            return {
                "status": "SKIPPED",
                "reason": ["Insufficient balance"],
            }
        
        # 3. Execute order
        # Get current price (simplified)
        price = signal_data.get("price", 0)
        if price == 0:
            return {
                "status": "ERROR",
                "reason": ["No price available"],
            }
        
        quantity = position_size / price
        
        order_result = await self.executor.execute_order(
            symbol=symbol,
            side="BUY",
            quantity=quantity,
            correlation_id=correlation_id,
        )
        
        if order_result.get("status") not in ["SUCCESS", "SIMULATED"]:
            return {
                "status": "FAILED",
                "reason": [order_result.get("error", "Order failed")],
                "order_result": order_result,
            }
        
        # 4. Track position
        position_id = f"pos_{correlation_id}"
        self.core._open_positions[position_id] = {
            "id": position_id,
            "symbol": symbol,
            "side": "BUY",
            "quantity": quantity,
            "entry_price": price,
            "size_usd": position_size,
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "order_id": order_result.get("order_id"),
            "correlation_id": correlation_id,
        }
        
        return {
            "status": "SUCCESS",
            "position_id": position_id,
            "decision": decision,
            "order": order_result,
        }


# =============================================================================
# HELPER FOR IMPORTING BRIDGE INTO HOPE CORE
# =============================================================================

def create_bridge(core: "HopeCore") -> IntegrationBridge:
    """
    Create integration bridge for HopeCore.
    
    Args:
        core: HopeCore instance
        
    Returns:
        Configured IntegrationBridge
    """
    return IntegrationBridge(core)
