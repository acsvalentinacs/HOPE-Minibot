# === AI SIGNATURE ===
# Module: hope_core/bus/command_bus.py
# Created by: Claude (opus-4.5)
# Created at: 2026-02-04 10:10:00 UTC
# Purpose: Command Bus - single entry point for all commands
# === END SIGNATURE ===
"""
HOPE Core - Command Bus

Single entry point for all commands.
Every command is validated, authorized, routed, and executed.

Flow:
1. RECEIVE → Parse incoming command
2. VALIDATE → Check against JSON Schema contract
3. AUTHORIZE → Check rate limits, circuit breaker
4. ROUTE → Find appropriate handler
5. EXECUTE → Run handler with timeout
6. LOG → Record to Event Journal
"""

from typing import Dict, Any, Optional, Callable, Awaitable, List
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import asyncio
import threading
import time
import uuid

# Local imports
from .contracts import (
    Command, CommandType, ValidationResult,
    validate_command, COMMAND_SCHEMAS
)


# =============================================================================
# COMMAND RESULT
# =============================================================================

class CommandStatus(Enum):
    """Command execution status."""
    SUCCESS = "SUCCESS"
    REJECTED = "REJECTED"       # Validation failed
    UNAUTHORIZED = "UNAUTHORIZED"  # Authorization failed
    TIMEOUT = "TIMEOUT"         # Execution timeout
    ERROR = "ERROR"             # Handler error
    NOT_FOUND = "NOT_FOUND"     # No handler found


@dataclass
class CommandResult:
    """Result of command execution."""
    status: CommandStatus
    command_id: str
    correlation_id: str
    data: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    execution_time_ms: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    @property
    def success(self) -> bool:
        return self.status == CommandStatus.SUCCESS
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "command_id": self.command_id,
            "correlation_id": self.correlation_id,
            "data": self.data,
            "errors": self.errors,
            "execution_time_ms": self.execution_time_ms,
            "timestamp": self.timestamp.isoformat(),
        }


# =============================================================================
# RATE LIMITER
# =============================================================================

class RateLimiter:
    """
    Token bucket rate limiter.
    
    Limits command execution rate.
    """
    
    def __init__(
        self,
        rate: float = 10.0,      # Commands per second
        burst: int = 20,         # Max burst
    ):
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last_update = time.monotonic()
        self._lock = threading.Lock()
    
    def acquire(self) -> bool:
        """Try to acquire a token. Returns True if allowed."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_update
            self._last_update = now
            
            # Add tokens
            self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
            
            if self._tokens >= 1:
                self._tokens -= 1
                return True
            return False
    
    def reset(self):
        """Reset rate limiter."""
        with self._lock:
            self._tokens = float(self._burst)
            self._last_update = time.monotonic()


# =============================================================================
# CIRCUIT BREAKER
# =============================================================================

class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "CLOSED"       # Normal operation
    OPEN = "OPEN"           # Circuit open, rejecting
    HALF_OPEN = "HALF_OPEN" # Testing recovery


class CircuitBreaker:
    """
    Circuit breaker for fault tolerance.
    
    Opens on repeated failures, prevents cascade.
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_requests: int = 3,
    ):
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_requests = half_open_requests
        
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._lock = threading.Lock()
    
    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._check_recovery()
            return self._state
    
    @property
    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN
    
    def _check_recovery(self):
        """Check if circuit should recover."""
        if self._state == CircuitState.OPEN and self._last_failure_time:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._success_count = 0
    
    def allow_request(self) -> bool:
        """Check if request is allowed."""
        with self._lock:
            self._check_recovery()
            
            if self._state == CircuitState.CLOSED:
                return True
            elif self._state == CircuitState.HALF_OPEN:
                return True
            else:  # OPEN
                return False
    
    def record_success(self):
        """Record successful request."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self._half_open_requests:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0
    
    def record_failure(self):
        """Record failed request."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self._failure_threshold:
                    self._state = CircuitState.OPEN
    
    def reset(self):
        """Force reset circuit breaker."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._last_failure_time = None


# =============================================================================
# COMMAND HANDLER TYPE
# =============================================================================

# Handler signature: async (command, context) -> result_data
CommandHandler = Callable[[Command, Dict[str, Any]], Awaitable[Dict[str, Any]]]


# =============================================================================
# COMMAND BUS
# =============================================================================

class CommandBus:
    """
    Central command bus for HOPE Core.
    
    All commands flow through this bus:
    1. Validation against contracts
    2. Authorization (rate limit, circuit breaker)
    3. Routing to handler
    4. Execution with timeout
    5. Logging to journal
    """
    
    def __init__(
        self,
        rate_limiter: Optional[RateLimiter] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        default_timeout_ms: int = 30000,
        on_command_received: Optional[Callable[[Command], None]] = None,
        on_command_completed: Optional[Callable[[Command, CommandResult], None]] = None,
    ):
        """
        Initialize Command Bus.
        
        Args:
            rate_limiter: Rate limiter instance
            circuit_breaker: Circuit breaker instance
            default_timeout_ms: Default command timeout
            on_command_received: Callback when command received
            on_command_completed: Callback when command completed
        """
        self._handlers: Dict[CommandType, CommandHandler] = {}
        self._rate_limiter = rate_limiter or RateLimiter()
        self._circuit_breaker = circuit_breaker or CircuitBreaker()
        self._default_timeout = default_timeout_ms
        
        self._on_received = on_command_received
        self._on_completed = on_command_completed
        
        self._stats = {
            "received": 0,
            "executed": 0,
            "rejected": 0,
            "errors": 0,
            "timeouts": 0,
        }
        self._lock = threading.Lock()
    
    def register_handler(
        self,
        command_type: CommandType,
        handler: CommandHandler,
    ):
        """
        Register handler for command type.
        
        Args:
            command_type: Type of command
            handler: Async handler function
        """
        self._handlers[command_type] = handler
    
    def create_command(
        self,
        command_type: CommandType,
        payload: Dict[str, Any],
        correlation_id: Optional[str] = None,
        source: str = "unknown",
        priority: int = 0,
        timeout_ms: Optional[int] = None,
    ) -> Command:
        """
        Create a command object.
        
        Args:
            command_type: Type of command
            payload: Command payload
            correlation_id: Correlation ID (generated if not provided)
            source: Command source
            priority: Priority (0=normal, 1=high, 2=critical)
            timeout_ms: Timeout in milliseconds
            
        Returns:
            Command object
        """
        return Command(
            id=f"cmd_{uuid.uuid4().hex[:12]}",
            type=command_type,
            payload=payload,
            correlation_id=correlation_id or f"corr_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.now(timezone.utc),
            source=source,
            priority=priority,
            timeout_ms=timeout_ms or self._default_timeout,
        )
    
    async def dispatch(self, command: Command) -> CommandResult:
        """
        Dispatch command through the bus.
        
        Args:
            command: Command to dispatch
            
        Returns:
            CommandResult with execution outcome
        """
        start_time = time.monotonic()
        
        with self._lock:
            self._stats["received"] += 1
        
        # Callback
        if self._on_received:
            self._on_received(command)
        
        try:
            # 1. VALIDATE
            cmd_type_str = command.type.value if hasattr(command.type, 'value') else str(command.type)
            validation = validate_command(cmd_type_str, command.payload)
            if not validation.valid:
                result = CommandResult(
                    status=CommandStatus.REJECTED,
                    command_id=command.id,
                    correlation_id=command.correlation_id,
                    errors=validation.errors,
                )
                with self._lock:
                    self._stats["rejected"] += 1
                return self._finalize(command, result, start_time)
            
            # 2. AUTHORIZE - Rate limit
            if not self._rate_limiter.acquire():
                result = CommandResult(
                    status=CommandStatus.UNAUTHORIZED,
                    command_id=command.id,
                    correlation_id=command.correlation_id,
                    errors=["Rate limit exceeded"],
                )
                with self._lock:
                    self._stats["rejected"] += 1
                return self._finalize(command, result, start_time)
            
            # 2. AUTHORIZE - Circuit breaker
            if not self._circuit_breaker.allow_request():
                result = CommandResult(
                    status=CommandStatus.UNAUTHORIZED,
                    command_id=command.id,
                    correlation_id=command.correlation_id,
                    errors=["Circuit breaker open"],
                )
                with self._lock:
                    self._stats["rejected"] += 1
                return self._finalize(command, result, start_time)
            
            # 3. ROUTE - Find handler
            handler = self._handlers.get(command.type)
            if not handler:
                result = CommandResult(
                    status=CommandStatus.NOT_FOUND,
                    command_id=command.id,
                    correlation_id=command.correlation_id,
                    errors=[f"No handler for {cmd_type_str}"],
                )
                with self._lock:
                    self._stats["errors"] += 1
                return self._finalize(command, result, start_time)
            
            # 4. EXECUTE with timeout
            try:
                timeout_sec = command.timeout_ms / 1000.0
                context = {
                    "command_id": command.id,
                    "correlation_id": command.correlation_id,
                    "timestamp": command.timestamp,
                    "source": command.source,
                }
                
                data = await asyncio.wait_for(
                    handler(command, context),
                    timeout=timeout_sec,
                )
                
                # Success
                self._circuit_breaker.record_success()
                result = CommandResult(
                    status=CommandStatus.SUCCESS,
                    command_id=command.id,
                    correlation_id=command.correlation_id,
                    data=data or {},
                )
                with self._lock:
                    self._stats["executed"] += 1
                return self._finalize(command, result, start_time)
                
            except asyncio.TimeoutError:
                self._circuit_breaker.record_failure()
                result = CommandResult(
                    status=CommandStatus.TIMEOUT,
                    command_id=command.id,
                    correlation_id=command.correlation_id,
                    errors=[f"Timeout after {command.timeout_ms}ms"],
                )
                with self._lock:
                    self._stats["timeouts"] += 1
                return self._finalize(command, result, start_time)
                
            except Exception as e:
                self._circuit_breaker.record_failure()
                result = CommandResult(
                    status=CommandStatus.ERROR,
                    command_id=command.id,
                    correlation_id=command.correlation_id,
                    errors=[f"Handler error: {str(e)}"],
                )
                with self._lock:
                    self._stats["errors"] += 1
                return self._finalize(command, result, start_time)
                
        except Exception as e:
            result = CommandResult(
                status=CommandStatus.ERROR,
                command_id=command.id,
                correlation_id=command.correlation_id,
                errors=[f"Bus error: {str(e)}"],
            )
            with self._lock:
                self._stats["errors"] += 1
            return self._finalize(command, result, start_time)
    
    def _finalize(
        self,
        command: Command,
        result: CommandResult,
        start_time: float,
    ) -> CommandResult:
        """Finalize command execution."""
        result.execution_time_ms = (time.monotonic() - start_time) * 1000
        
        if self._on_completed:
            self._on_completed(command, result)
        
        return result
    
    async def dispatch_simple(
        self,
        command_type: CommandType | str,
        payload: Dict[str, Any],
        **kwargs,
    ) -> CommandResult:
        """
        Convenience method to create and dispatch command.
        
        Args:
            command_type: Type of command (CommandType or string)
            payload: Command payload
            **kwargs: Additional command options
            
        Returns:
            CommandResult
        """
        # Convert string to CommandType if needed
        if isinstance(command_type, str):
            try:
                command_type = CommandType(command_type)
            except ValueError:
                # Unknown command type
                return CommandResult(
                    status=CommandStatus.REJECTED,
                    command_id="unknown",
                    correlation_id=kwargs.get("correlation_id", "unknown"),
                    errors=[f"Unknown command type: {command_type}"],
                )
        
        command = self.create_command(command_type, payload, **kwargs)
        return await self.dispatch(command)
    
    def get_stats(self) -> Dict[str, int]:
        """Get bus statistics."""
        with self._lock:
            return dict(self._stats)
    
    def reset_stats(self):
        """Reset statistics."""
        with self._lock:
            self._stats = {k: 0 for k in self._stats}
    
    @property
    def circuit_state(self) -> CircuitState:
        """Get circuit breaker state."""
        return self._circuit_breaker.state
    
    def reset_circuit_breaker(self):
        """Reset circuit breaker."""
        self._circuit_breaker.reset()


# =============================================================================
# TESTING
# =============================================================================

if __name__ == "__main__":
    import asyncio
    
    print("=== Command Bus Tests ===\n")
    
    # Track commands
    received_commands = []
    completed_commands = []
    
    def on_received(cmd):
        received_commands.append(cmd)
        print(f"  Received: {cmd.type.value} ({cmd.id})")
    
    def on_completed(cmd, result):
        completed_commands.append((cmd, result))
        print(f"  Completed: {result.status.value} in {result.execution_time_ms:.2f}ms")
    
    # Create bus
    bus = CommandBus(
        on_command_received=on_received,
        on_command_completed=on_completed,
    )
    
    # Register test handlers
    async def handle_signal(cmd: Command, ctx: Dict[str, Any]) -> Dict[str, Any]:
        await asyncio.sleep(0.01)  # Simulate work
        return {"processed": True, "symbol": cmd.payload["symbol"]}
    
    async def handle_health(cmd: Command, ctx: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "healthy", "uptime": 3600}
    
    async def handle_slow(cmd: Command, ctx: Dict[str, Any]) -> Dict[str, Any]:
        await asyncio.sleep(5)  # Simulate slow operation
        return {}
    
    bus.register_handler(CommandType.SIGNAL, handle_signal)
    bus.register_handler(CommandType.HEALTH, handle_health)
    bus.register_handler(CommandType.SYNC, handle_slow)  # For timeout test
    
    async def run_tests():
        # Test 1: Valid SIGNAL command
        print("Test 1: Valid SIGNAL command")
        result = await bus.dispatch_simple(
            CommandType.SIGNAL,
            {
                "symbol": "BTCUSDT",
                "score": 75,
                "source": "MOMENTUM",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            source="test",
        )
        print(f"  Result: {result.status.value}")
        print(f"  Data: {result.data}")
        print()
        
        # Test 2: Invalid command (missing required field)
        print("Test 2: Invalid command (validation failure)")
        result = await bus.dispatch_simple(
            CommandType.SIGNAL,
            {"score": 75},  # Missing symbol, source, timestamp
            source="test",
        )
        print(f"  Result: {result.status.value}")
        print(f"  Errors: {result.errors}")
        print()
        
        # Test 3: HEALTH command
        print("Test 3: HEALTH command")
        result = await bus.dispatch_simple(
            CommandType.HEALTH,
            {},
            source="test",
        )
        print(f"  Result: {result.status.value}")
        print(f"  Data: {result.data}")
        print()
        
        # Test 4: No handler
        print("Test 4: No handler registered")
        result = await bus.dispatch_simple(
            CommandType.ORDER,
            {"symbol": "BTCUSDT", "side": "BUY", "quantity": 0.1, "order_type": "MARKET"},
            source="test",
        )
        print(f"  Result: {result.status.value}")
        print(f"  Errors: {result.errors}")
        print()
        
        # Test 5: Timeout
        print("Test 5: Command timeout")
        cmd = bus.create_command(
            CommandType.SYNC,
            {},
            timeout_ms=100,  # 100ms timeout
        )
        result = await bus.dispatch(cmd)
        print(f"  Result: {result.status.value}")
        print(f"  Errors: {result.errors}")
        print()
        
        # Test 6: Statistics
        print("Test 6: Statistics")
        stats = bus.get_stats()
        print(f"  Stats: {stats}")
        print()
        
        print(f"Total received: {len(received_commands)}")
        print(f"Total completed: {len(completed_commands)}")
    
    asyncio.run(run_tests())
    print("\n=== All Tests Completed ===")
