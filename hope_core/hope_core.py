# === AI SIGNATURE ===
# Module: hope_core/hope_core.py
# Created by: Claude (opus-4.5)
# Created at: 2026-02-04 10:30:00 UTC
# Modified by: Claude (opus-4.5)
# Modified at: 2026-02-05 09:30:00 UTC
# Purpose: HOPE Core v2.0 - Main trading core with Command Bus + State Machine + AI Gate
# === END SIGNATURE ===
"""
HOPE Core v2.0 - Main Trading Core

Unified trading core with:
- Command Bus for all commands
- State Machine for state control
- Event Journal for audit trail
- Integration with Eye of God and Order Executor

This is the SINGLE process that handles the entire trading cycle:
Signal → Decision → Order → Position → Close
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from enum import Enum
import asyncio
import threading
import time
import json
import sys
import os

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

# Local imports - try relative first (when running directly), then absolute (when importing as package)
try:
    from bus.command_bus import (
        CommandBus, CommandType, CommandStatus, CommandResult,
        Command, RateLimiter, CircuitBreaker
    )
    from bus.contracts import validate_command, SignalSource
    from state.machine import (
        StateMachine, StateMachineManager, TradingState, StateTransition
    )
    from journal.event_journal import (
        EventJournal, EventType, EventLevel, Event,
        create_state_change_event, create_heartbeat_event
    )
except ImportError:
    from hope_core.bus.command_bus import (
        CommandBus, CommandType, CommandStatus, CommandResult,
        Command, RateLimiter, CircuitBreaker
    )
    from hope_core.bus.contracts import validate_command, SignalSource
    from hope_core.state.machine import (
        StateMachine, StateMachineManager, TradingState, StateTransition
    )
    from hope_core.journal.event_journal import (
        EventJournal, EventType, EventLevel, Event,
        create_state_change_event, create_heartbeat_event
    )
except ImportError:
    from journal.event_journal import (
        EventJournal, EventType, EventLevel, Event,
        create_state_change_event, create_heartbeat_event
    )


# Secret Sauce
try:
    from hope_core.secret_sauce import SecretSauce
except ImportError:
    try:
        from secret_sauce import SecretSauce
    except ImportError:
        SecretSauce = None


# AI Gate Integration
try:
    from hope_core.ai_integration import get_ai_gate, ai_signal_filter, ai_record_fill
except ImportError:
    try:
        from ai_integration import get_ai_gate, ai_signal_filter, ai_record_fill
    except ImportError:
        get_ai_gate = None
        ai_signal_filter = None
        ai_record_fill = None


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class HopeCoreConfig:
    """Configuration for HOPE Core."""
    
    # Mode
    mode: str = "DRY"  # DRY, TESTNET, LIVE
    
    # Trading parameters
    min_confidence: float = 0.35
    position_size_usd: float = 20.0
    max_positions: int = 3
    daily_loss_limit_percent: float = 5.0
    
    # Timing
    heartbeat_interval_sec: float = 60.0
    sync_interval_sec: float = 60.0
    scan_interval_sec: float = 10.0
    
    # Paths
    state_dir: Path = field(default_factory=lambda: Path("state/core"))
    journal_path: Path = field(default_factory=lambda: Path("state/events/journal.jsonl"))
    
    # API
    api_host: str = "127.0.0.1"
    api_port: int = 8200
    
    # External integrations
    eye_of_god_enabled: bool = True
    binance_enabled: bool = True
    telegram_enabled: bool = False
    
    @classmethod
    def from_file(cls, path: Path) -> "HopeCoreConfig":
        """Load config from JSON file."""
        if path.exists():
            with open(path) as f:
                data = json.load(f)
                return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})
        return cls()


# =============================================================================
# HOPE CORE
# =============================================================================

class HopeCore:
    """
    HOPE Trading Core v2.0
    
    Single process handling entire trading cycle with:
    - Command Bus for validated commands
    - State Machine for state control
    - Event Journal for audit
    """
    
    def __init__(self, config: Optional[HopeCoreConfig] = None):
        """
        Initialize HOPE Core.
        
        Args:
            config: Core configuration
        """
        self.config = config or HopeCoreConfig()
        self._running = False
        self._start_time: Optional[datetime] = None
        self._lock = threading.RLock()
        
        # Ensure directories exist
        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        self.config.journal_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize components
        self._init_journal()
        self._init_state_machines()
        self._init_command_bus()
        self._init_integrations()
        
        # Statistics
        self._stats = {
            "signals_received": 0,
            "signals_traded": 0,
            "positions_opened": 0,
            "positions_closed": 0,
            "total_pnl": 0.0,
            "daily_pnl": 0.0,
            "win_count": 0,
            "loss_count": 0,
        }
        
        # Current state
        self._open_positions: Dict[str, Any] = {}
        self._pending_signals: Dict[str, Any] = {}
        
        print(f"[HOPE CORE] Initialized in {self.config.mode} mode")
    
    def _init_journal(self):
        """Initialize Event Journal."""
        self.journal = EventJournal(self.config.journal_path)

        # Secret Sauce - Advanced Trading Intelligence
        if SecretSauce:
            self.secret_sauce = SecretSauce(self.config.state_dir / "secret_sauce")
        else:
            self.secret_sauce = None
            print(f"[GUARDIAN] Init failed: {e}")
        # Log startup
        self.journal.append(
            EventType.STARTUP,
            payload={
                "mode": self.config.mode,
                "version": "2.0",
                "config": {
                    "min_confidence": self.config.min_confidence,
                    "position_size": self.config.position_size_usd,
                    "max_positions": self.config.max_positions,
                }
            },
            correlation_id="startup",
            level=EventLevel.INFO,
        )
    
    def _init_state_machines(self):
        """Initialize State Machines."""
        self.state_manager = StateMachineManager(
            on_transition=self._on_state_transition,
        )
        
        # Log initial state
        print(f"[HOPE CORE] State Machine initialized: {self.state_manager.global_machine.state.value}")
    
    def _init_command_bus(self):
        """Initialize Command Bus."""
        self.command_bus = CommandBus(
            rate_limiter=RateLimiter(rate=10.0, burst=20),
            circuit_breaker=CircuitBreaker(failure_threshold=5),
            on_command_received=self._on_command_received,
            on_command_completed=self._on_command_completed,
        )
        
        # Register handlers
        self.command_bus.register_handler(CommandType.SIGNAL, self._handle_signal)
        self.command_bus.register_handler(CommandType.DECIDE, self._handle_decide)
        self.command_bus.register_handler(CommandType.ORDER, self._handle_order)
        self.command_bus.register_handler(CommandType.CLOSE, self._handle_close)
        self.command_bus.register_handler(CommandType.SYNC, self._handle_sync)
        self.command_bus.register_handler(CommandType.HEALTH, self._handle_health)
        self.command_bus.register_handler(CommandType.HEARTBEAT, self._handle_heartbeat)
        self.command_bus.register_handler(CommandType.EMERGENCY_STOP, self._handle_emergency_stop)
        
        print("[HOPE CORE] Command Bus initialized with handlers")
    
    def _init_integrations(self):
        """Initialize external integrations."""
        self.eye_of_god = None
        self.ai_gate = None  # AI Gate for Command Bus filtering
        self.order_executor = None
        self.binance_client = None
        self.alert_manager = None
        
        # Initialize alert manager
        try:
            from alerts import AlertManager, AlertLevel
            self.alert_manager = AlertManager(min_level=AlertLevel.INFO)
            print("[HOPE CORE] Alert Manager initialized")
        except ImportError:
            print("[HOPE CORE] Alert Manager not available")
        
        # Try to load Eye of God
        if self.config.eye_of_god_enabled:
            try:
                from eye_of_god_v3 import EyeOfGodV3
                self.eye_of_god = EyeOfGodV3()
                print("[HOPE CORE] Eye of God V3 loaded")
            except ImportError:
                # Try mock
                try:
                    from mocks import MockEyeOfGodV3
                    self.eye_of_god = MockEyeOfGodV3(mode="BALANCED")
                    print("[HOPE CORE] Mock Eye of God loaded (BALANCED mode)")
                except ImportError as e:
                    print(f"[HOPE CORE] Eye of God not available: {e}")

        # Initialize AI Gate for Command Bus filtering
        if get_ai_gate is not None:
            try:
                self.ai_gate = get_ai_gate()
                print("[HOPE CORE] AI Gate loaded for Command Bus filtering")
            except Exception as e:
                print(f"[HOPE CORE] AI Gate not available: {e}")

        # Try to load Order Executor
        if self.config.binance_enabled:
            try:
                from order_executor import OrderExecutor, TradingMode
                mode = TradingMode[self.config.mode]
                self.order_executor = OrderExecutor(mode)
                print(f"[HOPE CORE] Order Executor loaded ({self.config.mode})")
            except ImportError:
                # Try mock
                try:
                    from mocks import MockOrderExecutor, TradingMode
                    mode = TradingMode[self.config.mode]
                    self.order_executor = MockOrderExecutor(mode)
                    print(f"[HOPE CORE] Mock Order Executor loaded ({self.config.mode})")
                except ImportError as e:
                    print(f"[HOPE CORE] Order Executor not available: {e}")
        
        # Initialize Position Guardian (after Order Executor)
        self.position_guardian = None
        try:
            import importlib.util
            from pathlib import Path
            
            guardian_file = Path(__file__).parent / "guardian" / "position_guardian.py"
            if guardian_file.exists():
                spec = importlib.util.spec_from_file_location("position_guardian", guardian_file)
                guardian_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(guardian_module)
                
                PositionGuardian = guardian_module.PositionGuardian
                GuardianConfig = guardian_module.GuardianConfig
                
                guardian_config = GuardianConfig(
                    state_dir=self.config.state_dir / "guardian",
                    enable_ai=True,
                    enable_trailing=True,
                    hard_sl_pct=-2.0,
                    base_tp_pct=1.5,
                )
                
                binance_client = None
                if self.order_executor and hasattr(self.order_executor, 'client'):
                    binance_client = self.order_executor.client
                    print(f"[HOPE CORE] Guardian will use binance client: {type(binance_client)}")
                else:
                    print(f"[HOPE CORE] WARNING: No binance client! order_executor={self.order_executor}")
                    if self.order_executor:
                        print(f"[HOPE CORE] order_executor attrs: {[a for a in dir(self.order_executor) if not a.startswith('_')]}")

                self.position_guardian = PositionGuardian(
                    config=guardian_config,
                    binance_client=binance_client,
                    eye_of_god=self.eye_of_god,
                    secret_sauce=self.secret_sauce,
                )
                print(f"[HOPE CORE] Position Guardian initialized (binance={binance_client is not None})")
        except Exception as e:
            print(f"[HOPE CORE] Position Guardian init failed: {e}")
    
    # =========================================================================
    # CALLBACKS
    # =========================================================================
    
    def _on_state_transition(self, machine_id: str, transition: StateTransition):
        """Handle state transition event."""
        # Log to journal
        create_state_change_event(
            self.journal,
            transition.correlation_id,
            transition.from_state.value,
            transition.to_state.value,
            transition.reason,
            **transition.data,
        )
    
    def _on_command_received(self, command: Command):
        """Handle command received event."""
        self.journal.append(
            EventType.COMMAND_RECEIVED,
            payload={"type": command.type.value, "source": command.source},
            correlation_id=command.correlation_id,
            level=EventLevel.DEBUG,
            command_type=command.type.value,
        )
    
    def _on_command_completed(self, command: Command, result: CommandResult):
        """Handle command completed event."""
        event_type = (
            EventType.COMMAND_EXECUTED if result.success
            else EventType.COMMAND_FAILED
        )
        self.journal.append(
            event_type,
            payload={
                "type": command.type.value,
                "status": result.status.value,
                "execution_time_ms": result.execution_time_ms,
                "errors": result.errors,
            },
            correlation_id=command.correlation_id,
            level=EventLevel.INFO if result.success else EventLevel.WARNING,
            command_type=command.type.value,
        )
    
    # =========================================================================
    # COMMAND HANDLERS
    # =========================================================================
    
    async def _handle_signal(self, command: Command, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Handle SIGNAL command."""
        payload = command.payload
        symbol = payload["symbol"]
        score = payload["score"]
        source = payload["source"]
        
        self._stats["signals_received"] += 1

        # === AI GATE CHECK ===
        if ai_signal_filter is not None and self.ai_gate is not None:
            try:
                ai_passed, ai_reason = await ai_signal_filter({
                    "symbol": symbol,
                    "confidence": score,
                    "price": payload.get("price", 0.0),
                    "signal_type": source,
                })
                if not ai_passed:
                    self._stats["signals_skipped"] = self._stats.get("signals_skipped", 0) + 1
                    print(f"[AI_GATE] Signal {symbol} rejected: {ai_reason}")
                    self.journal.append(
                        EventType.SIGNAL_REJECTED,
                        payload={"symbol": symbol, "reason": ai_reason},
                        correlation_id=command.correlation_id,
                    )
                    return {"status": "REJECTED", "reason": ai_reason, "ai_gate": True}
            except Exception as e:
                print(f"[AI_GATE] Error checking signal: {e}")
        # === END AI GATE CHECK ===

        # Log signal
        self.journal.append(
            EventType.SIGNAL_RECEIVED,
            payload=payload,
            correlation_id=command.correlation_id,
            symbol=symbol,
        )
        
        # Store pending signal
        signal_id = f"sig_{command.id}"
        self._pending_signals[signal_id] = {
            "id": signal_id,
            "symbol": symbol,
            "score": score,
            "source": source,
            "timestamp": command.timestamp.isoformat(),
            "correlation_id": command.correlation_id,
        }
        
        # Transition state
        sm = self.state_manager.global_machine
        if sm.state == TradingState.IDLE or sm.state == TradingState.SCANNING:
            sm.transition(
                TradingState.SIGNAL_RECEIVED,
                reason=f"Signal received: {symbol} score={score}",
                correlation_id=command.correlation_id,
                symbol=symbol,
                signal_id=signal_id,
            )
        
        # Auto-trigger decision if enabled
        if self.eye_of_god:
            decide_result = await self._handle_decide(
                Command(
                    id=f"auto_decide_{signal_id}",
                    type=CommandType.DECIDE,
                    payload={
                        "signal_id": signal_id,
                        "symbol": symbol,
                        "score": score,
                    },
                    correlation_id=command.correlation_id,
                    timestamp=datetime.now(timezone.utc),
                    source="auto",
                ),
                ctx,
            )
            return {"signal_id": signal_id, "auto_decide": decide_result}
        
        return {"signal_id": signal_id, "status": "pending_decision"}
    
    async def _handle_decide(self, command: Command, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Handle DECIDE command."""
        payload = command.payload
        signal_id = payload["signal_id"]
        symbol = payload["symbol"]
        score = payload["score"]
        
        # Transition to DECIDING
        sm = self.state_manager.global_machine
        sm.transition(
            TradingState.DECIDING,
            reason=f"Deciding on {symbol}",
            correlation_id=command.correlation_id,
        )
        
        # Check conditions
        if len(self._open_positions) >= self.config.max_positions:
            sm.transition(
                TradingState.IDLE,
                reason="Max positions reached",
                correlation_id=command.correlation_id,
            )
            return {"decision": "REJECT", "reason": "MAX_POSITIONS"}
        
        # Use Eye of God if available
        if self.eye_of_god:
            try:
                decision = self.eye_of_god.decide({
                    "symbol": symbol,
                    "score": score,
                    "source": payload.get("source", "SCANNER"),
                })
                
                # Handle both dict and object response
                if isinstance(decision, dict):
                    action = decision.get("action", "HOLD")
                    confidence = decision.get("confidence", score)
                else:
                    action = getattr(decision, 'action', "HOLD")
                    confidence = getattr(decision, 'confidence', score)
                
                self.journal.append(
                    EventType.DECISION_MADE,
                    payload={
                        "symbol": symbol,
                        "decision": action,
                        "confidence": confidence,
                    },
                    correlation_id=command.correlation_id,
                    symbol=symbol,
                )
                
                if action == "BUY":
                    # Proceed to order
                    sm.transition(
                        TradingState.ORDERING,
                        reason=f"Decision: BUY {symbol}",
                        correlation_id=command.correlation_id,
                    )
                    
                    # Execute order if executor available
                    if self.order_executor:
                        order_result = await self._execute_order(symbol, "BUY", command.correlation_id)
                        return {
                            "decision": "BUY",
                            "symbol": symbol,
                            "confidence": confidence,
                            "order": order_result,
                            "position_id": order_result.get("position_id"),
                        }
                    else:
                        return {
                            "decision": "BUY",
                            "symbol": symbol,
                            "confidence": confidence,
                            "order": "NO_EXECUTOR",
                        }
                else:
                    sm.transition(
                        TradingState.IDLE,
                        reason=f"Decision: {action}",
                        correlation_id=command.correlation_id,
                    )
                    return {"decision": action, "reason": decision.get("reasons", []) if isinstance(decision, dict) else []}
                    return {"decision": action, "reason": "EYE_OF_GOD"}
                    
            except Exception as e:
                print(f"[HOPE CORE] Eye of God error: {e}")
        
        # Fallback: simple confidence check
        confidence = score / 100.0
        # Secret Sauce pre-check
        if self.secret_sauce:
            sauce_ok, sauce_reason, sauce_meta = self.secret_sauce.should_trade(symbol, confidence)
            if not sauce_ok:
                sm.transition(
                    TradingState.IDLE,
                    reason=f"Secret Sauce: {sauce_reason}",
                    correlation_id=command.correlation_id,
                )
                return {"decision": "REJECT", "reason": f"SECRET_SAUCE: {sauce_reason}"}
        
        if confidence >= self.config.min_confidence:
            sm.transition(
                TradingState.ORDERING,
                reason=f"Confidence {confidence:.2%} >= {self.config.min_confidence:.2%}",
                correlation_id=command.correlation_id,
            )
            return {"decision": "BUY", "symbol": symbol, "confidence": confidence}
        else:
            sm.transition(
                TradingState.IDLE,
                reason=f"Confidence {confidence:.2%} < {self.config.min_confidence:.2%}",
                correlation_id=command.correlation_id,
            )
            return {"decision": "REJECT", "reason": "LOW_CONFIDENCE"}
    
    async def _execute_order(
        self,
        symbol: str,
        side: str,
        correlation_id: str,
    ) -> Dict[str, Any]:
        """
        Execute order through Order Executor.
        
        Args:
            symbol: Trading symbol
            side: BUY or SELL
            correlation_id: Correlation ID
            
        Returns:
            Order result with position_id if successful
        """
        sm = self.state_manager.global_machine
        
        # Log order intent
        self.journal.append(
            EventType.ORDER_SENT,
            payload={"symbol": symbol, "side": side, "size_usd": self.config.position_size_usd},
            correlation_id=correlation_id,
            symbol=symbol,
        )
        
        try:
            if self.order_executor:
                # Execute order
                result = self.order_executor.execute_order(
                    symbol=symbol,
                    side=side,
                    quote_quantity=self.config.position_size_usd,
                )
                
                if result.get("status") in ["FILLED", "SIMULATED"]:
                    # Order filled - transition states
                    sm.transition(
                        TradingState.PENDING_FILL,
                        reason="Order submitted",
                        correlation_id=correlation_id,
                    )
                    sm.transition(
                        TradingState.POSITION_OPEN,
                        reason="Order filled",
                        correlation_id=correlation_id,
                    )
                    
                    # Track position
                    position_id = f"pos_{result.get('orderId', int(time.time() * 1000))}"
                    self._open_positions[position_id] = {
                        "id": position_id,
                        "symbol": symbol,
                        "side": side,
                        "entry_price": float(result.get("avgPrice", 0)),
                        "quantity": float(result.get("executedQty", 0)),
                        "size_usd": self.config.position_size_usd,
                        "opened_at": datetime.now(timezone.utc).isoformat(),
                        "correlation_id": correlation_id,
                        "order_id": result.get("orderId"),
                        "mock": result.get("mock", False),
                    }
                    
                    self._stats["positions_opened"] += 1
                    self._stats["signals_traded"] += 1
                    
                    # Log position opened
                    self.journal.append(
                        EventType.POSITION_OPENED,
                        payload=self._open_positions[position_id],
                        correlation_id=correlation_id,
                        symbol=symbol,
                    )
                    
                    # Alert if available
                    if self.alert_manager:
                        asyncio.create_task(
                            self.alert_manager.trade_opened(
                                symbol=symbol,
                                side=side,
                                quantity=float(result.get("executedQty", 0)),
                                price=float(result.get("avgPrice", 0)),
                            )
                        )
                    
                    return {
                        "status": "FILLED",
                        "position_id": position_id,
                        **result,
                    }
                else:
                    # Order failed
                    sm.transition(
                        TradingState.IDLE,
                        reason=f"Order failed: {result.get('status')}",
                        correlation_id=correlation_id,
                    )
                    return {"status": "FAILED", **result}
            else:
                # No executor - DRY mode simulation
                sm.transition(TradingState.PENDING_FILL, reason="DRY: Simulated", correlation_id=correlation_id)
                sm.transition(TradingState.POSITION_OPEN, reason="DRY: Simulated", correlation_id=correlation_id)
                
                position_id = f"pos_dry_{int(time.time() * 1000)}"
                self._open_positions[position_id] = {
                    "id": position_id,
                    "symbol": symbol,
                    "side": side,
                    "mode": "DRY",
                    "correlation_id": correlation_id,
                }
                self._stats["positions_opened"] += 1
                self._stats["signals_traded"] += 1
                
                return {"status": "SIMULATED", "position_id": position_id}
                
        except Exception as e:
            sm.transition(
                TradingState.ERROR,
                reason=f"Order error: {e}",
                correlation_id=correlation_id,
            )
            return {"status": "ERROR", "error": str(e)}
    
    async def _handle_order(self, command: Command, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Handle ORDER command."""
        payload = command.payload
        symbol = payload["symbol"]
        side = payload["side"]
        
        sm = self.state_manager.global_machine
        
        # Log order intent
        self.journal.append(
            EventType.ORDER_SENT,
            payload=payload,
            correlation_id=command.correlation_id,
            symbol=symbol,
        )
        
        # Execute order
        if self.order_executor and self.config.mode != "DRY":
            try:
                result = self.order_executor.execute_order(
                    symbol=symbol,
                    side=side,
                    quote_quantity=payload.get("quote_quantity", self.config.position_size_usd),
                )
                
                if result.get("status") == "FILLED":
                    # Order filled
                    sm.transition(
                        TradingState.PENDING_FILL,
                        reason="Order sent",
                        correlation_id=command.correlation_id,
                    )
                    sm.transition(
                        TradingState.POSITION_OPEN,
                        reason="Order filled",
                        correlation_id=command.correlation_id,
                        order_id=result.get("orderId"),
                    )
                    
                    # Track position
                    position_id = f"pos_{result.get('orderId', 'unknown')}"
                    self._open_positions[position_id] = {
                        "id": position_id,
                        "symbol": symbol,
                        "side": side,
                        "entry_price": result.get("avgPrice", 0),
                        "quantity": result.get("executedQty", 0),
                        "opened_at": datetime.now(timezone.utc).isoformat(),
                        "correlation_id": command.correlation_id,
                    }
                    
                    self._stats["positions_opened"] += 1
                    self._stats["signals_traded"] += 1
                    
                    self.journal.append(
                        EventType.POSITION_OPENED,
                        payload=self._open_positions[position_id],
                        correlation_id=command.correlation_id,
                        symbol=symbol,
                        position_id=position_id,
                    )
                    
                    return {"status": "FILLED", "position_id": position_id, **result}
                else:
                    sm.transition(
                        TradingState.IDLE,
                        reason=f"Order not filled: {result.get('status')}",
                        correlation_id=command.correlation_id,
                    )
                    return {"status": result.get("status", "FAILED"), **result}
                    
            except Exception as e:
                sm.transition(
                    TradingState.ERROR,
                    reason=f"Order error: {e}",
                    correlation_id=command.correlation_id,
                )
                return {"status": "ERROR", "error": str(e)}
        
        # DRY mode - simulate
        sm.transition(TradingState.PENDING_FILL, reason="DRY: Order simulated", correlation_id=command.correlation_id)
        sm.transition(TradingState.POSITION_OPEN, reason="DRY: Fill simulated", correlation_id=command.correlation_id)
        
        position_id = f"pos_dry_{int(time.time() * 1000)}"
        self._open_positions[position_id] = {
            "id": position_id,
            "symbol": symbol,
            "side": side,
            "mode": "DRY",
        }
        
        return {"status": "SIMULATED", "position_id": position_id}
    
    async def _handle_close(self, command: Command, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Handle CLOSE command."""
        payload = command.payload
        position_id = payload["position_id"]
        reason = payload.get("reason", "MANUAL")
        
        sm = self.state_manager.global_machine
        
        if position_id not in self._open_positions:
            return {"status": "NOT_FOUND", "position_id": position_id}
        
        position = self._open_positions[position_id]
        symbol = position["symbol"]
        
        sm.transition(
            TradingState.CLOSING,
            reason=f"Closing {position_id}: {reason}",
            correlation_id=command.correlation_id,
            position_id=position_id,
        )
        
        # Execute close
        if self.order_executor and self.config.mode != "DRY":
            try:
                result = self.order_executor.close_position(
                    symbol=symbol,
                    quantity=position.get("quantity"),
                )
                
                # Calculate PnL
                entry_price = float(position.get("entry_price", 0))
                exit_price = float(result.get("avgPrice", 0))
                quantity = float(position.get("quantity", 0))
                pnl = (exit_price - entry_price) * quantity
                
                # Update stats
                self._stats["total_pnl"] += pnl
                self._stats["daily_pnl"] += pnl
                self._stats["positions_closed"] += 1
                if pnl > 0:
                    self._stats["win_count"] += 1
                else:
                    self._stats["loss_count"] += 1
                
            except Exception as e:
                return {"status": "ERROR", "error": str(e)}
        
        # Remove position
        del self._open_positions[position_id]
        
        sm.transition(
            TradingState.CLOSED,
            reason=f"Position closed: {reason}",
            correlation_id=command.correlation_id,
        )
        sm.transition(
            TradingState.IDLE,
            reason="Ready for next cycle",
            correlation_id=command.correlation_id,
        )
        
        self.journal.append(
            EventType.POSITION_CLOSED,
            payload={"position_id": position_id, "reason": reason},
            correlation_id=command.correlation_id,
            symbol=symbol,
            position_id=position_id,
        )
        
        return {"status": "CLOSED", "position_id": position_id, "reason": reason}
    
    async def _handle_sync(self, command: Command, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Handle SYNC command."""
        # Sync with Binance
        if self.order_executor:
            try:
                # Get actual positions from Binance
                # This would need implementation in order_executor
                print(f"[GUARDIAN] Init failed: {e}")
            except Exception as e:
                return {"status": "ERROR", "error": str(e)}
        
        return {
            "status": "OK",
            "open_positions": len(self._open_positions),
            "mode": self.config.mode,
        }
    
    async def _handle_health(self, command: Command, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Handle HEALTH command."""
        return {
            "status": "healthy",
            "mode": self.config.mode,
            "uptime_seconds": self.uptime,
            "state": self.state_manager.global_machine.state.value,
            "open_positions": len(self._open_positions),
            "stats": self._stats,
            "command_bus_stats": self.command_bus.get_stats(),
            "circuit_breaker": self.command_bus.circuit_state.value,
            "eye_of_god_loaded": self.eye_of_god is not None,
            "executor_loaded": self.order_executor is not None,
        }
    
    async def _handle_heartbeat(self, command: Command, ctx: Dict[str, Any]) -> Dict[str, Any]:
        # Update Secret Sauce heartbeat
        if self.secret_sauce:
            self.secret_sauce.heartbeat()
        """Handle HEARTBEAT command."""
        import psutil
        
        process = psutil.Process()
        memory_mb = process.memory_info().rss / (1024 * 1024)
        
        create_heartbeat_event(
            self.journal,
            state=self.state_manager.global_machine.state.value,
            memory_mb=memory_mb,
            open_positions=len(self._open_positions),
        )
        
        return {
            "status": "OK",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": self.state_manager.global_machine.state.value,
            "memory_mb": memory_mb,
        }
    
    async def _handle_emergency_stop(self, command: Command, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Handle EMERGENCY_STOP command."""
        reason = command.payload.get("reason", "Manual stop")
        close_positions = command.payload.get("close_positions", True)
        
        print(f"[HOPE CORE] EMERGENCY STOP: {reason}")
        
        # Transition to emergency stop
        sm = self.state_manager.global_machine
        sm.transition(
            TradingState.EMERGENCY_STOP,
            reason=f"Emergency stop: {reason}",
            correlation_id=command.correlation_id,
        )
        
        # Close all positions if requested
        closed_count = 0
        if close_positions:
            for position_id in list(self._open_positions.keys()):
                try:
                    await self._handle_close(
                        Command(
                            id=f"emergency_close_{position_id}",
                            type=CommandType.CLOSE,
                            payload={"position_id": position_id, "reason": "EMERGENCY"},
                            correlation_id=command.correlation_id,
                            timestamp=datetime.now(timezone.utc),
                            source="emergency",
                        ),
                        ctx,
                    )
                    closed_count += 1
                except Exception as e:
                    print(f"[HOPE CORE] Failed to close {position_id}: {e}")
        
        # Log
        self.journal.append(
            EventType.EMERGENCY_STOP,
            payload={"reason": reason, "closed_positions": closed_count},
            correlation_id=command.correlation_id,
            level=EventLevel.CRITICAL,
        )
        
        self._running = False
        
        return {"status": "STOPPED", "reason": reason, "closed_positions": closed_count}
    
    # =========================================================================
    # PUBLIC API
    # =========================================================================
    
    @property
    def uptime(self) -> float:
        """Get uptime in seconds."""
        if self._start_time:
            return (datetime.now(timezone.utc) - self._start_time).total_seconds()
        return 0.0
    
    @property
    def state(self) -> TradingState:
        """Get current state."""
        return self.state_manager.global_machine.state
    
    async def submit_signal(
        self,
        symbol: str,
        score: float,
        source: str = "SCANNER",
        correlation_id: Optional[str] = None,
    ) -> CommandResult:
        """Submit a trading signal."""
        return await self.command_bus.dispatch_simple(
            CommandType.SIGNAL,
            {
                "symbol": symbol,
                "score": score,
                "source": source,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            correlation_id=correlation_id,
            source="api",
        )
    
    async def get_health(self) -> Dict[str, Any]:
        """Get health status."""
        result = await self.command_bus.dispatch_simple(
            CommandType.HEALTH,
            {},
            source="api",
        )
        return result.data
    
    async def emergency_stop(self, reason: str) -> CommandResult:
        """Trigger emergency stop."""
        return await self.command_bus.dispatch_simple(
            CommandType.EMERGENCY_STOP,
            {"reason": reason, "close_positions": True},
            source="api",
            priority=2,  # Critical
        )
    
    async def start(self):
        """Start HOPE Core main loop."""
        self._running = True
        self._start_time = datetime.now(timezone.utc)
        
        print(f"[HOPE CORE] Starting at {self._start_time.isoformat()}")
        
        # Start API server (would need FastAPI/aiohttp)
        # For now, just run the main loop
        
        last_heartbeat = time.monotonic()
        last_sync = time.monotonic()
        
        while self._running:
            try:
                now = time.monotonic()
                
                # Heartbeat
                if now - last_heartbeat >= self.config.heartbeat_interval_sec:
                    await self.command_bus.dispatch_simple(
                        CommandType.HEARTBEAT,
                        {"timestamp": datetime.now(timezone.utc).isoformat()},
                        source="loop",
                    )
                    last_heartbeat = now
                
                # Sync
                if now - last_sync >= self.config.sync_interval_sec:
                    await self.command_bus.dispatch_simple(
                        CommandType.SYNC,
                        {},
                        source="loop",
                    )
                    last_sync = now
                
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[HOPE CORE] Error in main loop: {e}")
                await asyncio.sleep(1)
        
        # Shutdown
        self.journal.append(
            EventType.SHUTDOWN,
            payload={"reason": "Normal shutdown"},
            correlation_id="shutdown",
            level=EventLevel.INFO,
        )
        
        print("[HOPE CORE] Stopped")
    
    def stop(self):
        """Stop HOPE Core."""
        self._running = False


# =============================================================================
# MAIN
# =============================================================================

async def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="HOPE Core v2.0")
    parser.add_argument("--mode", choices=["DRY", "TESTNET", "LIVE"], default="DRY")
    parser.add_argument("--config", type=str, help="Path to config file")
    args = parser.parse_args()
    
    # Load config
    config = HopeCoreConfig(mode=args.mode)
    if args.config:
        config = HopeCoreConfig.from_file(Path(args.config))
        config.mode = args.mode
    
    # Create and run core
    core = HopeCore(config)
    
    # Handle signals
    import signal
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, core.stop)
    
    await core.start()


if __name__ == "__main__":
    asyncio.run(main())
