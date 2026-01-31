# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# sha256:signal_schema_v1_prod
# Created by: Claude (opus-4)
# Created at: 2026-01-30T04:45:00Z
# Purpose: Signal Schema Contract V1 - строгая валидация входных сигналов
# Contract: schema-invalid ⇒ SKIP, причина SIGNAL_SCHEMA_INVALID
# === END SIGNATURE ===
"""
═══════════════════════════════════════════════════════════════════════════════
  SIGNAL SCHEMA CONTRACT V1 - Строгая валидация входных сигналов
═══════════════════════════════════════════════════════════════════════════════

P0 ПРОБЛЕМА (из критики):
"Нет строгого контракта входного сигнала (schema).
Если поля типа delta_pct/buys_per_sec/daily_volume/timestamp иногда отсутствуют/
строки/NaN — текущий скоринг может молча превратить это в 0 и дать ложный confidence."

РЕШЕНИЕ:
Signal Schema V1 определяет:
1. REQUIRED поля - без них сигнал невалиден
2. OPTIONAL поля - могут отсутствовать, есть defaults
3. TYPE CHECK - каждое поле должно быть правильного типа
4. RANGE CHECK - значения в допустимых диапазонах
5. TTL CHECK - сигнал не старше max_age_sec

ПРАВИЛО: schema-invalid ⇒ SKIP с причиной SIGNAL_SCHEMA_INVALID

ИСПОЛЬЗОВАНИЕ:
    from signal_schema import SignalSchemaV1, validate_signal
    
    result = validate_signal(raw_data)
    if not result.valid:
        return SKIP(f"SIGNAL_SCHEMA_INVALID: {result.errors}")
    
    signal = result.signal  # Validated Signal object

═══════════════════════════════════════════════════════════════════════════════
"""

import json
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional, Tuple, Set
from enum import Enum

log = logging.getLogger("SIGNAL-SCHEMA")


# ═══════════════════════════════════════════════════════════════════════════
# SCHEMA DEFINITION
# ═══════════════════════════════════════════════════════════════════════════

# Required fields - must be present and valid
REQUIRED_FIELDS = {
    "symbol": str,
    "timestamp": str,  # ISO format
}

# Optional fields with defaults
OPTIONAL_FIELDS = {
    "strategy": (str, "Unknown"),
    "direction": (str, "Long"),
    "delta_pct": (float, 0.0),
    "buys_per_sec": (float, 0.0),
    "vol_per_sec": (float, 0.0),
    "vol_raise_pct": (float, 0.0),
    "price": (float, 0.0),
    "daily_volume_m": (float, 0.0),
    "buyers_count": (int, 0),
    "dBTC": (float, 0.0),
    "dBTC5m": (float, 0.0),
    "dBTC1m": (float, 0.0),
    "dMarkets": (float, 0.0),
    "signal_id": (str, ""),
    "raw_text": (str, ""),
    # Momentum detection fields - CRITICAL for Eye of God V3
    "signal_type": (str, ""),  # MOMENTUM_24H, TRENDING, etc.
    "type": (str, ""),  # Alias for compatibility
    "ai_override": (bool, False),  # Force trade despite low score
    "confidence": (float, 0.5),  # AI confidence score
}

# Valid ranges for numeric fields
VALID_RANGES = {
    "delta_pct": (-100.0, 1000.0),      # -100% to +1000%
    "buys_per_sec": (0.0, 10000.0),     # 0 to 10k
    "vol_per_sec": (0.0, 1000000.0),    # 0 to 1M
    "vol_raise_pct": (-100.0, 10000.0), # -100% to 10000%
    "price": (0.0, 1000000.0),          # 0 to 1M
    "daily_volume_m": (0.0, 100000.0),  # 0 to 100B
    "buyers_count": (0, 1000000),       # 0 to 1M
    "dBTC": (-50.0, 50.0),              # -50% to +50%
    "dBTC5m": (-20.0, 20.0),            # -20% to +20%
    "dBTC1m": (-10.0, 10.0),            # -10% to +10%
    "dMarkets": (-50.0, 50.0),          # -50% to +50%
}

# Valid values for categorical fields
VALID_STRATEGIES = {
    "PumpDetection", "Delta", "TopMarket", "DropsDetection", 
    "Volumes", "Unknown", "Pump", "Drop"
}
VALID_DIRECTIONS = {"Long", "Short", "Unknown"}

# Symbol pattern
VALID_SYMBOL_PATTERN = r"^[A-Z]{2,10}USDT$"

# TTL configuration
DEFAULT_MAX_SIGNAL_AGE_SEC = 60  # 1 minute default


# ═══════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ValidatedSignal:
    """Signal that passed schema validation"""
    # Required
    symbol: str
    timestamp: str

    # Optional with validated values
    strategy: str = "Unknown"
    direction: str = "Long"
    delta_pct: float = 0.0
    buys_per_sec: float = 0.0
    vol_per_sec: float = 0.0
    vol_raise_pct: float = 0.0
    price: float = 0.0
    daily_volume_m: float = 0.0
    buyers_count: int = 0
    dBTC: float = 0.0
    dBTC5m: float = 0.0
    dBTC1m: float = 0.0
    dMarkets: float = 0.0
    signal_id: str = ""
    raw_text: str = ""

    # Momentum detection fields - CRITICAL for Eye of God V3
    signal_type: str = ""  # MOMENTUM_24H, TRENDING, etc.
    type: str = ""  # Alias for compatibility
    ai_override: bool = False  # Force trade despite low score
    confidence: float = 0.5  # AI confidence score

    # Validation metadata
    schema_version: str = "V1"
    validated_at: str = ""
    age_sec: float = 0.0
    validation_warnings: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        if not self.signal_id:
            data = f"{self.symbol}{self.timestamp}{self.delta_pct}"
            self.signal_id = hashlib.sha256(data.encode()).hexdigest()[:16]
        if not self.validated_at:
            self.validated_at = datetime.now(timezone.utc).isoformat()
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ValidationResult:
    """Result of signal validation"""
    valid: bool
    signal: Optional[ValidatedSignal]
    errors: List[str]
    warnings: List[str]
    
    def to_dict(self) -> Dict:
        return {
            "valid": self.valid,
            "signal": self.signal.to_dict() if self.signal else None,
            "errors": self.errors,
            "warnings": self.warnings,
        }


class ValidationError(Enum):
    """Types of validation errors"""
    MISSING_REQUIRED = "MISSING_REQUIRED"
    WRONG_TYPE = "WRONG_TYPE"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    INVALID_VALUE = "INVALID_VALUE"
    TTL_EXPIRED = "TTL_EXPIRED"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    INVALID_SYMBOL = "INVALID_SYMBOL"


# ═══════════════════════════════════════════════════════════════════════════
# VALIDATION FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def _parse_timestamp(ts: Any) -> Optional[datetime]:
    """Parse timestamp to datetime"""
    if isinstance(ts, datetime):
        return ts
    if not isinstance(ts, str):
        return None
    
    try:
        # Try ISO format
        if 'T' in ts:
            ts = ts.replace('Z', '+00:00')
            return datetime.fromisoformat(ts)
        # Try Unix timestamp
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except:
        return None


def _check_type(value: Any, expected_type: type) -> Tuple[bool, Any]:
    """Check and coerce type"""
    if value is None:
        return False, None
    
    if isinstance(value, expected_type):
        return True, value
    
    # Try coercion
    try:
        if expected_type == float:
            return True, float(value)
        elif expected_type == int:
            return True, int(float(value))
        elif expected_type == str:
            return True, str(value)
    except:
        pass
    
    return False, None


def _check_range(value: float, field_name: str) -> Tuple[bool, Optional[str]]:
    """Check if value is in valid range"""
    if field_name not in VALID_RANGES:
        return True, None
    
    min_val, max_val = VALID_RANGES[field_name]
    if value < min_val or value > max_val:
        return False, f"{field_name}={value} out of range [{min_val}, {max_val}]"
    return True, None


def _is_nan(value: Any) -> bool:
    """Check if value is NaN"""
    try:
        import math
        if isinstance(value, float):
            return math.isnan(value)
    except:
        pass
    return False


def validate_signal(
    raw_data: Dict[str, Any],
    max_age_sec: float = DEFAULT_MAX_SIGNAL_AGE_SEC,
) -> ValidationResult:
    """
    Validate signal against schema V1.
    
    Returns ValidationResult with:
    - valid: bool
    - signal: ValidatedSignal if valid
    - errors: list of error strings if invalid
    - warnings: list of warning strings
    
    FAIL-CLOSED: Any validation error → valid=False
    """
    errors = []
    warnings = []
    
    # === 1. Check required fields ===
    for field_name, field_type in REQUIRED_FIELDS.items():
        if field_name not in raw_data:
            errors.append(f"{ValidationError.MISSING_REQUIRED.value}: {field_name}")
            continue
        
        value = raw_data[field_name]
        ok, _ = _check_type(value, field_type)
        if not ok:
            errors.append(f"{ValidationError.WRONG_TYPE.value}: {field_name} "
                         f"expected {field_type.__name__}, got {type(value).__name__}")
    
    # Early exit if required fields missing
    if errors:
        return ValidationResult(valid=False, signal=None, errors=errors, warnings=warnings)
    
    # === 2. Validate symbol ===
    symbol = raw_data["symbol"]
    import re
    if not re.match(VALID_SYMBOL_PATTERN, symbol):
        errors.append(f"{ValidationError.INVALID_SYMBOL.value}: {symbol}")
    
    # === 3. Validate timestamp + TTL ===
    ts_str = raw_data["timestamp"]
    ts = _parse_timestamp(ts_str)
    if not ts:
        errors.append(f"{ValidationError.INVALID_TIMESTAMP.value}: {ts_str}")
    else:
        now = datetime.now(timezone.utc)
        age_sec = (now - ts).total_seconds()
        
        if age_sec > max_age_sec:
            errors.append(f"{ValidationError.TTL_EXPIRED.value}: "
                         f"signal is {age_sec:.1f}s old (max={max_age_sec}s)")
        elif age_sec < 0:
            warnings.append(f"Signal timestamp is in future by {-age_sec:.1f}s")
            age_sec = 0
    
    # Early exit if timestamp/symbol invalid
    if errors:
        return ValidationResult(valid=False, signal=None, errors=errors, warnings=warnings)
    
    # === 4. Process optional fields ===
    validated_data = {
        "symbol": symbol,
        "timestamp": ts_str,
        "age_sec": age_sec,
    }
    
    for field_name, (field_type, default_value) in OPTIONAL_FIELDS.items():
        if field_name not in raw_data:
            validated_data[field_name] = default_value
            continue
        
        value = raw_data[field_name]
        
        # Check for NaN
        if _is_nan(value):
            warnings.append(f"NaN value for {field_name}, using default")
            validated_data[field_name] = default_value
            continue
        
        # Type check
        ok, coerced = _check_type(value, field_type)
        if not ok:
            warnings.append(f"Type mismatch for {field_name}, using default")
            validated_data[field_name] = default_value
            continue
        
        # Range check for numeric fields
        if field_type in (float, int):
            in_range, range_error = _check_range(float(coerced), field_name)
            if not in_range:
                warnings.append(range_error)
                # Clamp to range
                min_val, max_val = VALID_RANGES[field_name]
                coerced = max(min_val, min(max_val, coerced))
        
        validated_data[field_name] = coerced
    
    # === 5. Validate categorical fields ===
    strategy = validated_data.get("strategy", "Unknown")
    if strategy not in VALID_STRATEGIES:
        warnings.append(f"Unknown strategy '{strategy}', mapping to 'Unknown'")
        validated_data["strategy"] = "Unknown"
    
    direction = validated_data.get("direction", "Long")
    if direction not in VALID_DIRECTIONS:
        warnings.append(f"Unknown direction '{direction}', mapping to 'Unknown'")
        validated_data["direction"] = "Unknown"
    
    # === 6. Build validated signal ===
    try:
        signal = ValidatedSignal(
            symbol=validated_data["symbol"],
            timestamp=validated_data["timestamp"],
            strategy=validated_data.get("strategy", "Unknown"),
            direction=validated_data.get("direction", "Long"),
            delta_pct=validated_data.get("delta_pct", 0.0),
            buys_per_sec=validated_data.get("buys_per_sec", 0.0),
            vol_per_sec=validated_data.get("vol_per_sec", 0.0),
            vol_raise_pct=validated_data.get("vol_raise_pct", 0.0),
            price=validated_data.get("price", 0.0),
            daily_volume_m=validated_data.get("daily_volume_m", 0.0),
            buyers_count=validated_data.get("buyers_count", 0),
            dBTC=validated_data.get("dBTC", 0.0),
            dBTC5m=validated_data.get("dBTC5m", 0.0),
            dBTC1m=validated_data.get("dBTC1m", 0.0),
            dMarkets=validated_data.get("dMarkets", 0.0),
            signal_id=validated_data.get("signal_id", ""),
            raw_text=validated_data.get("raw_text", ""),
            # Momentum detection fields - CRITICAL for Eye of God V3
            signal_type=validated_data.get("signal_type", ""),
            type=validated_data.get("type", ""),
            ai_override=validated_data.get("ai_override", False),
            confidence=validated_data.get("confidence", 0.5),
            age_sec=validated_data.get("age_sec", 0.0),
            validation_warnings=warnings,
        )
        
        return ValidationResult(valid=True, signal=signal, errors=[], warnings=warnings)
        
    except Exception as e:
        errors.append(f"Failed to create signal: {e}")
        return ValidationResult(valid=False, signal=None, errors=errors, warnings=warnings)


# ═══════════════════════════════════════════════════════════════════════════
# BATCH VALIDATION
# ═══════════════════════════════════════════════════════════════════════════

def validate_signals_batch(
    signals: List[Dict[str, Any]],
    max_age_sec: float = DEFAULT_MAX_SIGNAL_AGE_SEC,
) -> Tuple[List[ValidatedSignal], List[Dict]]:
    """
    Validate multiple signals.
    
    Returns:
        (valid_signals, rejected_signals)
    """
    valid = []
    rejected = []
    
    for raw in signals:
        result = validate_signal(raw, max_age_sec)
        if result.valid:
            valid.append(result.signal)
        else:
            rejected.append({
                "raw": raw,
                "errors": result.errors,
            })
    
    return valid, rejected


# ═══════════════════════════════════════════════════════════════════════════
# LIQUIDITY GUARDRAIL
# ═══════════════════════════════════════════════════════════════════════════

# Minimum daily volume for trading (in millions USD)
MIN_DAILY_VOLUME_M = 5.0  # $5M minimum
SAFE_DAILY_VOLUME_M = 20.0  # $20M for full confidence


def check_liquidity(
    signal: ValidatedSignal,
    min_volume_m: float = MIN_DAILY_VOLUME_M,
) -> Tuple[bool, str, float]:
    """
    Check if symbol has sufficient liquidity.
    
    Returns:
        (tradeable, reason, liquidity_factor)
        
    liquidity_factor: 0.0-1.0 multiplier for confidence
    """
    volume = signal.daily_volume_m
    
    if volume <= 0:
        return False, "UNKNOWN_VOLUME", 0.0
    
    if volume < min_volume_m:
        return False, f"LOW_VOLUME:{volume}M<{min_volume_m}M", 0.0
    
    # Calculate factor
    if volume >= SAFE_DAILY_VOLUME_M:
        factor = 1.0
    else:
        factor = 0.5 + 0.5 * (volume - min_volume_m) / (SAFE_DAILY_VOLUME_M - min_volume_m)
    
    return True, "OK", factor


# ═══════════════════════════════════════════════════════════════════════════
# AUDIT TOOL
# ═══════════════════════════════════════════════════════════════════════════

def audit_signals_file(filepath: str, max_signals: int = 100) -> Dict:
    """
    Audit signals from JSONL file.
    
    Returns statistics about validation results.
    """
    from pathlib import Path
    
    path = Path(filepath)
    if not path.exists():
        return {"error": f"File not found: {filepath}"}
    
    stats = {
        "total": 0,
        "valid": 0,
        "invalid": 0,
        "errors_by_type": {},
        "warnings_count": 0,
        "avg_age_sec": 0.0,
        "low_liquidity": 0,
    }
    
    ages = []
    
    with open(path, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f):
            if i >= max_signals:
                break
            
            try:
                raw = json.loads(line.strip())
            except:
                stats["invalid"] += 1
                stats["errors_by_type"]["JSON_PARSE"] = \
                    stats["errors_by_type"].get("JSON_PARSE", 0) + 1
                continue
            
            stats["total"] += 1
            
            # Validate with infinite TTL for historical analysis
            result = validate_signal(raw, max_age_sec=float('inf'))
            
            if result.valid:
                stats["valid"] += 1
                stats["warnings_count"] += len(result.warnings)
                ages.append(result.signal.age_sec)
                
                # Check liquidity
                tradeable, _, _ = check_liquidity(result.signal)
                if not tradeable:
                    stats["low_liquidity"] += 1
            else:
                stats["invalid"] += 1
                for error in result.errors:
                    error_type = error.split(":")[0]
                    stats["errors_by_type"][error_type] = \
                        stats["errors_by_type"].get(error_type, 0) + 1
    
    if ages:
        stats["avg_age_sec"] = sum(ages) / len(ages)
    
    return stats


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Signal Schema Validator")
    parser.add_argument("--audit", type=str, help="Audit JSONL file")
    parser.add_argument("--n", type=int, default=100, help="Max signals to audit")
    parser.add_argument("--validate", type=str, help="Validate single JSON signal")
    
    args = parser.parse_args()
    
    if args.audit:
        stats = audit_signals_file(args.audit, args.n)
        print("\n=== SIGNAL AUDIT RESULTS ===")
        print(f"Total: {stats.get('total', 0)}")
        print(f"Valid: {stats.get('valid', 0)}")
        print(f"Invalid: {stats.get('invalid', 0)}")
        print(f"Warnings: {stats.get('warnings_count', 0)}")
        print(f"Low liquidity: {stats.get('low_liquidity', 0)}")
        print(f"Avg age: {stats.get('avg_age_sec', 0):.1f}s")
        if stats.get("errors_by_type"):
            print("\nErrors by type:")
            for error_type, count in stats["errors_by_type"].items():
                print(f"  {error_type}: {count}")
        return
    
    if args.validate:
        raw = json.loads(args.validate)
        result = validate_signal(raw)
        print("\n=== VALIDATION RESULT ===")
        print(f"Valid: {result.valid}")
        if result.valid:
            print(f"Signal ID: {result.signal.signal_id}")
            print(f"Symbol: {result.signal.symbol}")
            print(f"Age: {result.signal.age_sec:.1f}s")
        else:
            print(f"Errors: {result.errors}")
        if result.warnings:
            print(f"Warnings: {result.warnings}")
        return
    
    # Demo validation
    demo_signal = {
        "symbol": "BTCUSDT",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "strategy": "PumpDetection",
        "direction": "Long",
        "delta_pct": 2.5,
        "buys_per_sec": 45,
        "vol_raise_pct": 150,
        "daily_volume_m": 500,
    }
    
    result = validate_signal(demo_signal)
    print("\n=== DEMO VALIDATION ===")
    print(f"Input: {demo_signal}")
    print(f"Valid: {result.valid}")
    if result.valid:
        print(f"Signal: {result.signal.to_dict()}")


if __name__ == "__main__":
    main()
