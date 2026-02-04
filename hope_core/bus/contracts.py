# === AI SIGNATURE ===
# Module: hope_core/bus/contracts.py
# Created by: Claude (opus-4.5)
# Created at: 2026-02-04 09:45:00 UTC
# Purpose: JSON Schema contracts for Command Bus validation
# === END SIGNATURE ===
"""
HOPE Core - Command Contracts

JSON Schema definitions for all commands.
Every command MUST pass validation before execution.

FAIL-CLOSED: Invalid command = REJECTED (not crash)
"""

from typing import Dict, Any, Optional
from enum import Enum
from dataclasses import dataclass
from datetime import datetime
import json

# =============================================================================
# COMMAND TYPES
# =============================================================================

class CommandType(Enum):
    """All valid command types in HOPE Core."""
    SIGNAL = "SIGNAL"           # New trading signal
    DECIDE = "DECIDE"           # Request decision from Eye of God
    ORDER = "ORDER"             # Execute order on Binance
    CANCEL = "CANCEL"           # Cancel pending order
    CLOSE = "CLOSE"             # Close open position
    SYNC = "SYNC"               # Sync state with Binance
    HEALTH = "HEALTH"           # Health check
    HEARTBEAT = "HEARTBEAT"     # Heartbeat signal
    EMERGENCY_STOP = "EMERGENCY_STOP"  # Emergency stop all trading


class SignalSource(Enum):
    """Signal sources."""
    MOMENTUM = "MOMENTUM"       # From momentum_trader
    PUMP = "PUMP"               # From pump_detector
    EXTERNAL = "EXTERNAL"       # From Telegram/external
    MANUAL = "MANUAL"           # Manual signal
    SCANNER = "SCANNER"         # From auto_signal_loop
    TEST = "TEST"               # For testing
    API = "API"                 # From HTTP API
    AUTO = "AUTO"               # Auto-generated


class OrderSide(Enum):
    """Order side."""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    """Order type."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"


# =============================================================================
# JSON SCHEMAS
# =============================================================================

SIGNAL_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["symbol", "score", "source", "timestamp"],
    "additionalProperties": True,  # Allow extra fields
    "properties": {
        "symbol": {
            "type": "string",
            "pattern": "^[A-Z0-9]+USDT$",
            "description": "Trading pair symbol (e.g., BTCUSDT)"
        },
        "score": {
            "type": "number",
            "minimum": 0,
            "maximum": 100,
            "description": "Signal strength 0-100"
        },
        "source": {
            "type": "string",
            "enum": ["MOMENTUM", "PUMP", "EXTERNAL", "MANUAL", "SCANNER", "TEST", "API", "AUTO"],
            "description": "Signal source"
        },
        "timestamp": {
            "type": "string",
            "format": "date-time",
            "description": "Signal timestamp ISO format"
        },
        "metadata": {
            "type": "object",
            "description": "Optional metadata"
        }
    }
}

DECIDE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["signal_id", "symbol", "score"],
    "properties": {
        "signal_id": {
            "type": "string",
            "description": "ID of signal to decide on"
        },
        "symbol": {
            "type": "string",
            "pattern": "^[A-Z0-9]+USDT$"
        },
        "score": {
            "type": "number",
            "minimum": 0,
            "maximum": 100
        },
        "price": {
            "type": "number",
            "minimum": 0,
            "description": "Current price"
        },
        "volume_24h": {
            "type": "number",
            "minimum": 0,
            "description": "24h volume in USDT"
        }
    }
}

ORDER_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["symbol", "side", "quantity", "order_type"],
    "properties": {
        "symbol": {
            "type": "string",
            "pattern": "^[A-Z0-9]+USDT$"
        },
        "side": {
            "type": "string",
            "enum": ["BUY", "SELL"]
        },
        "quantity": {
            "type": "number",
            "exclusiveMinimum": 0,
            "description": "Order quantity in base asset"
        },
        "order_type": {
            "type": "string",
            "enum": ["MARKET", "LIMIT", "STOP_MARKET"]
        },
        "price": {
            "type": "number",
            "minimum": 0,
            "description": "Limit price (required for LIMIT orders)"
        },
        "stop_price": {
            "type": "number",
            "minimum": 0,
            "description": "Stop price (required for STOP_MARKET)"
        },
        "quote_quantity": {
            "type": "number",
            "minimum": 0,
            "description": "Quote order quantity in USDT"
        },
        "position_id": {
            "type": "string",
            "description": "Associated position ID"
        }
    }
}

CANCEL_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["order_id", "symbol"],
    "properties": {
        "order_id": {
            "type": "string",
            "description": "Binance order ID"
        },
        "symbol": {
            "type": "string",
            "pattern": "^[A-Z0-9]+USDT$"
        }
    }
}

CLOSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["position_id"],
    "properties": {
        "position_id": {
            "type": "string",
            "description": "Position ID to close"
        },
        "reason": {
            "type": "string",
            "enum": ["TP_HIT", "SL_HIT", "MANUAL", "TIMEOUT", "EMERGENCY"],
            "description": "Reason for closing"
        },
        "force": {
            "type": "boolean",
            "default": False,
            "description": "Force close even if in invalid state"
        }
    }
}

SYNC_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "full_sync": {
            "type": "boolean",
            "default": False,
            "description": "Full sync vs incremental"
        },
        "symbols": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Specific symbols to sync"
        }
    }
}

HEALTH_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "include_positions": {
            "type": "boolean",
            "default": True
        },
        "include_balance": {
            "type": "boolean",
            "default": True
        },
        "include_stats": {
            "type": "boolean",
            "default": True
        }
    }
}

HEARTBEAT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["timestamp"],
    "properties": {
        "timestamp": {
            "type": "string",
            "format": "date-time"
        },
        "state": {
            "type": "string",
            "description": "Current state machine state"
        },
        "memory_mb": {
            "type": "number",
            "description": "Memory usage in MB"
        }
    }
}

EMERGENCY_STOP_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["reason"],
    "properties": {
        "reason": {
            "type": "string",
            "description": "Reason for emergency stop"
        },
        "close_positions": {
            "type": "boolean",
            "default": True,
            "description": "Close all open positions"
        },
        "cancel_orders": {
            "type": "boolean",
            "default": True,
            "description": "Cancel all pending orders"
        }
    }
}

# =============================================================================
# SCHEMA REGISTRY
# =============================================================================

COMMAND_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "SIGNAL": SIGNAL_SCHEMA,
    "DECIDE": DECIDE_SCHEMA,
    "ORDER": ORDER_SCHEMA,
    "CANCEL": CANCEL_SCHEMA,
    "CLOSE": CLOSE_SCHEMA,
    "SYNC": SYNC_SCHEMA,
    "HEALTH": HEALTH_SCHEMA,
    "HEARTBEAT": HEARTBEAT_SCHEMA,
    "EMERGENCY_STOP": EMERGENCY_STOP_SCHEMA,
}


# =============================================================================
# COMMAND DATACLASS
# =============================================================================

@dataclass
class Command:
    """
    Command object that flows through the Command Bus.
    
    Immutable after creation. Contains all information needed for execution.
    """
    id: str                     # Unique command ID
    type: CommandType           # Command type
    payload: Dict[str, Any]     # Command payload (validated against schema)
    correlation_id: str         # Links related commands/events
    timestamp: datetime         # Creation timestamp
    source: str                 # Who/what created this command
    priority: int = 0           # 0=normal, 1=high, 2=critical
    timeout_ms: int = 30000     # Command timeout in milliseconds
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "type": self.type.value,
            "payload": self.payload,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "priority": self.priority,
            "timeout_ms": self.timeout_ms,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Command":
        """Create Command from dictionary."""
        return cls(
            id=data["id"],
            type=CommandType(data["type"]),
            payload=data["payload"],
            correlation_id=data["correlation_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            source=data["source"],
            priority=data.get("priority", 0),
            timeout_ms=data.get("timeout_ms", 30000),
        )


# =============================================================================
# VALIDATION RESULT
# =============================================================================

@dataclass
class ValidationResult:
    """Result of command validation."""
    valid: bool
    errors: list[str]
    warnings: list[str]
    
    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0
    
    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0


# =============================================================================
# VALIDATOR
# =============================================================================

class ContractValidator:
    """
    Validates commands against JSON Schema contracts.
    
    FAIL-CLOSED: Any doubt = REJECT
    """
    
    def __init__(self):
        try:
            import jsonschema
            self._jsonschema = jsonschema
            self._available = True
        except ImportError:
            self._available = False
            print("WARNING: jsonschema not installed, using basic validation")
    
    def validate(self, command_type: str, payload: Dict[str, Any]) -> ValidationResult:
        """
        Validate payload against schema for command type.
        
        Args:
            command_type: Type of command (SIGNAL, ORDER, etc.)
            payload: Command payload to validate
            
        Returns:
            ValidationResult with errors and warnings
        """
        errors = []
        warnings = []
        
        # Check command type exists
        if command_type not in COMMAND_SCHEMAS:
            errors.append(f"Unknown command type: {command_type}")
            return ValidationResult(valid=False, errors=errors, warnings=warnings)
        
        schema = COMMAND_SCHEMAS[command_type]
        
        if self._available:
            # Full JSON Schema validation
            try:
                self._jsonschema.validate(payload, schema)
            except self._jsonschema.ValidationError as e:
                errors.append(f"Schema validation failed: {e.message}")
            except self._jsonschema.SchemaError as e:
                errors.append(f"Invalid schema: {e.message}")
        else:
            # Basic validation without jsonschema
            errors.extend(self._basic_validate(schema, payload))
        
        # Additional semantic validation
        semantic_errors, semantic_warnings = self._semantic_validate(command_type, payload)
        errors.extend(semantic_errors)
        warnings.extend(semantic_warnings)
        
        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings
        )
    
    def _basic_validate(self, schema: Dict, payload: Dict) -> list[str]:
        """Basic validation without jsonschema library."""
        errors = []
        
        # Check required fields
        required = schema.get("required", [])
        for field in required:
            if field not in payload:
                errors.append(f"Missing required field: {field}")
        
        # Check field types
        properties = schema.get("properties", {})
        for field, value in payload.items():
            if field in properties:
                expected_type = properties[field].get("type")
                if expected_type == "string" and not isinstance(value, str):
                    errors.append(f"Field {field} must be string, got {type(value).__name__}")
                elif expected_type == "number" and not isinstance(value, (int, float)):
                    errors.append(f"Field {field} must be number, got {type(value).__name__}")
                elif expected_type == "boolean" and not isinstance(value, bool):
                    errors.append(f"Field {field} must be boolean, got {type(value).__name__}")
                elif expected_type == "object" and not isinstance(value, dict):
                    errors.append(f"Field {field} must be object, got {type(value).__name__}")
                elif expected_type == "array" and not isinstance(value, list):
                    errors.append(f"Field {field} must be array, got {type(value).__name__}")
        
        return errors
    
    def _semantic_validate(
        self, 
        command_type: str, 
        payload: Dict[str, Any]
    ) -> tuple[list[str], list[str]]:
        """
        Semantic validation beyond schema.
        
        Returns:
            Tuple of (errors, warnings)
        """
        errors = []
        warnings = []
        
        if command_type == "SIGNAL":
            # Score should be reasonable
            score = payload.get("score", 0)
            if score < 20:
                warnings.append(f"Very low signal score: {score}")
            if score > 95:
                warnings.append(f"Suspiciously high score: {score}")
        
        elif command_type == "ORDER":
            # Quantity checks
            qty = payload.get("quantity", 0)
            quote_qty = payload.get("quote_quantity", 0)
            
            if qty <= 0 and quote_qty <= 0:
                errors.append("Order must have quantity or quote_quantity > 0")
            
            # LIMIT order needs price
            if payload.get("order_type") == "LIMIT" and not payload.get("price"):
                errors.append("LIMIT order requires price")
            
            # STOP_MARKET needs stop_price
            if payload.get("order_type") == "STOP_MARKET" and not payload.get("stop_price"):
                errors.append("STOP_MARKET order requires stop_price")
        
        elif command_type == "CLOSE":
            # Position ID format check
            pos_id = payload.get("position_id", "")
            if pos_id and not pos_id.startswith("pos_"):
                warnings.append(f"Non-standard position ID format: {pos_id}")
        
        return errors, warnings


# =============================================================================
# SINGLETON VALIDATOR
# =============================================================================

_validator: Optional[ContractValidator] = None


def get_validator() -> ContractValidator:
    """Get singleton validator instance."""
    global _validator
    if _validator is None:
        _validator = ContractValidator()
    return _validator


def validate_command(command_type: str, payload: Dict[str, Any]) -> ValidationResult:
    """Convenience function to validate command."""
    return get_validator().validate(command_type, payload)


# =============================================================================
# TESTING
# =============================================================================

if __name__ == "__main__":
    print("=== Contract Validation Tests ===\n")
    
    validator = ContractValidator()
    
    # Test 1: Valid SIGNAL
    print("Test 1: Valid SIGNAL")
    result = validator.validate("SIGNAL", {
        "symbol": "BTCUSDT",
        "score": 75,
        "source": "MOMENTUM",
        "timestamp": "2026-02-04T09:00:00Z"
    })
    print(f"  Valid: {result.valid}")
    print(f"  Errors: {result.errors}")
    print()
    
    # Test 2: Invalid SIGNAL (missing required)
    print("Test 2: Invalid SIGNAL (missing symbol)")
    result = validator.validate("SIGNAL", {
        "score": 75,
        "source": "MOMENTUM",
        "timestamp": "2026-02-04T09:00:00Z"
    })
    print(f"  Valid: {result.valid}")
    print(f"  Errors: {result.errors}")
    print()
    
    # Test 3: Valid ORDER
    print("Test 3: Valid ORDER")
    result = validator.validate("ORDER", {
        "symbol": "DOGEUSDT",
        "side": "BUY",
        "quantity": 100,
        "order_type": "MARKET"
    })
    print(f"  Valid: {result.valid}")
    print(f"  Errors: {result.errors}")
    print()
    
    # Test 4: Invalid ORDER (LIMIT without price)
    print("Test 4: Invalid ORDER (LIMIT without price)")
    result = validator.validate("ORDER", {
        "symbol": "DOGEUSDT",
        "side": "BUY",
        "quantity": 100,
        "order_type": "LIMIT"
    })
    print(f"  Valid: {result.valid}")
    print(f"  Errors: {result.errors}")
    print()
    
    # Test 5: Unknown command type
    print("Test 5: Unknown command type")
    result = validator.validate("UNKNOWN", {"foo": "bar"})
    print(f"  Valid: {result.valid}")
    print(f"  Errors: {result.errors}")
    print()
    
    print("=== All Tests Completed ===")
