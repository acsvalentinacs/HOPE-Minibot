# -*- coding: utf-8 -*-
"""
HOPE v4.0 AUTOTRADER WATCHDOG INTEGRATION
=========================================

КРИТИЧЕСКАЯ ПРОБЛЕМА (была):
    autotrader.py BUY → OK →
    process crash (OOM/exception) →
    NAKED POSITION →
    нет OCO, нет watchdog →
    ПОТЕНЦИАЛЬНАЯ ПОТЕРЯ

РЕШЕНИЕ:
    1. После каждого BUY - немедленная регистрация в watchdog
    2. Если регистрация fails - emergency close
    3. Интеграция с Event Ledger для трассировки

ИСПОЛЬЗОВАНИЕ В autotrader.py:

    # В начале файла:
    from core.autotrader_watchdog_integration import (
        register_with_watchdog,
        execute_with_ledger,
        AutotraderWatchdogError
    )
    
    # После успешного BUY:
    await register_with_watchdog(
        executor=self.executor,
        symbol=decision.symbol,
        entry_price=result.avg_price,
        quantity=result.filled_quantity,
        target_pct=fee_adjusted_target,
        stop_pct=abs(fee_adjusted_stop),
        timeout_sec=decision.timeout_sec,
        order_id=result.order_id,
        decision_id=decision.decision_id,
    )
"""

import os
import json
import time
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass

log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

WATCHDOG_POSITIONS_PATH = Path(os.environ.get(
    "HOPE_WATCHDOG_POSITIONS",
    "state/ai/watchdog/positions.json"
))

# ══════════════════════════════════════════════════════════════════════════════
# EXCEPTIONS
# ══════════════════════════════════════════════════════════════════════════════

class AutotraderWatchdogError(Exception):
    """Raised when watchdog registration fails."""
    pass


# ══════════════════════════════════════════════════════════════════════════════
# WATCHDOG REGISTRATION
# ══════════════════════════════════════════════════════════════════════════════

def _generate_position_id() -> str:
    """Generate unique position ID."""
    return f"pos_{int(time.time() * 1000)}"


def _read_watchdog_positions() -> Dict[str, Any]:
    """Read current watchdog positions."""
    if not WATCHDOG_POSITIONS_PATH.exists():
        return {"positions": []}
    
    try:
        return json.loads(WATCHDOG_POSITIONS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        log.error(f"Failed to read watchdog positions: {e}")
        return {"positions": []}


def _write_watchdog_positions(data: Dict[str, Any]) -> bool:
    """
    Atomic write watchdog positions.
    """
    import tempfile
    
    WATCHDOG_POSITIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        fd, tmp_path = tempfile.mkstemp(
            suffix=".tmp",
            prefix="watchdog_",
            dir=WATCHDOG_POSITIONS_PATH.parent
        )
        
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        
        os.replace(tmp_path, WATCHDOG_POSITIONS_PATH)
        return True
        
    except Exception as e:
        log.error(f"Failed to write watchdog positions: {e}")
        if 'tmp_path' in dir() and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return False


async def register_with_watchdog(
    executor: Any,  # BinanceOCOExecutor or similar
    symbol: str,
    entry_price: float,
    quantity: float,
    target_pct: float,
    stop_pct: float,
    timeout_sec: int,
    order_id: str = "",
    decision_id: str = "",
    signal_id: str = "",
) -> str:
    """
    Register position with watchdog for monitoring.
    
    FAIL-CLOSED: If registration fails, triggers emergency close!
    
    Args:
        executor: Trading executor (for emergency close)
        symbol: Trading symbol
        entry_price: Entry price
        quantity: Position quantity
        target_pct: Take profit percentage
        stop_pct: Stop loss percentage (positive value)
        timeout_sec: Position timeout in seconds
        order_id: Optional order ID
        decision_id: Optional decision ID for tracing
        signal_id: Optional signal ID for tracing
        
    Returns:
        position_id
        
    Raises:
        AutotraderWatchdogError: If registration fails and emergency close also fails
    """
    position_id = _generate_position_id()
    
    now = time.time()
    
    position = {
        "position_id": position_id,
        "symbol": symbol,
        "entry_price": entry_price,
        "quantity": quantity,
        "target_pct": target_pct,
        "stop_pct": stop_pct,
        "timeout_sec": timeout_sec,
        "entry_unix": now,
        "deadline_unix": now + timeout_sec,
        "status": "OPEN",
        "order_id": order_id,
        # Tracing
        "trace": {
            "decision_id": decision_id,
            "signal_id": signal_id,
        },
        "registered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }
    
    # Read current positions
    data = _read_watchdog_positions()
    positions = data.get("positions", [])
    
    # Add new position
    positions.append(position)
    data["positions"] = positions
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    
    # Write atomically
    if not _write_watchdog_positions(data):
        log.critical(f"[WATCHDOG] CRITICAL: Failed to register position {position_id}!")
        log.critical(f"[WATCHDOG] Initiating EMERGENCY CLOSE for {symbol}!")
        
        # EMERGENCY CLOSE
        try:
            if hasattr(executor, 'emergency_close'):
                await executor.emergency_close(symbol, quantity)
                log.info(f"[WATCHDOG] Emergency close executed for {symbol}")
            else:
                log.critical(f"[WATCHDOG] Executor has no emergency_close method!")
                raise AutotraderWatchdogError(
                    f"Failed to register position AND no emergency close available!"
                )
        except Exception as e:
            log.critical(f"[WATCHDOG] Emergency close FAILED: {e}")
            raise AutotraderWatchdogError(
                f"Failed to register position AND emergency close failed: {e}"
            ) from e
        
        # Even though we closed, raise to inform caller
        raise AutotraderWatchdogError(
            f"Position registration failed - executed emergency close"
        )
    
    log.info(f"[WATCHDOG] Registered position {position_id} for {symbol}")
    
    # Also log to Event Ledger if available
    try:
        from core.event_ledger import get_ledger
        ledger = get_ledger()
        ledger.open(
            decision_id=decision_id or "unknown",
            position_id=position_id,
            symbol=symbol,
            entry_price=entry_price,
            quantity=quantity,
            order_id=order_id,
            source="autotrader",
            target_pct=target_pct,
            stop_pct=stop_pct,
            timeout_sec=timeout_sec,
        )
    except Exception as e:
        log.warning(f"[WATCHDOG] Event Ledger logging failed (non-critical): {e}")
    
    return position_id


def unregister_from_watchdog(position_id: str) -> bool:
    """
    Remove position from watchdog (after exit).
    """
    data = _read_watchdog_positions()
    positions = data.get("positions", [])
    
    # Find and remove position
    new_positions = [p for p in positions if p.get("position_id") != position_id]
    
    if len(new_positions) == len(positions):
        log.warning(f"[WATCHDOG] Position {position_id} not found in watchdog")
        return False
    
    data["positions"] = new_positions
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    
    return _write_watchdog_positions(data)


def get_open_positions() -> list:
    """Get all open positions from watchdog."""
    data = _read_watchdog_positions()
    return [p for p in data.get("positions", []) if p.get("status") == "OPEN"]


# ══════════════════════════════════════════════════════════════════════════════
# EXECUTE WITH LEDGER (convenience wrapper)
# ══════════════════════════════════════════════════════════════════════════════

async def execute_with_ledger(
    executor: Any,
    signal: Dict[str, Any],
    decision: Dict[str, Any],
    quote_amount: float = 25.0,
) -> Optional[Dict[str, Any]]:
    """
    Execute trade with full Event Ledger integration.
    
    1. Log DECISION event
    2. Execute BUY
    3. Register with watchdog
    4. Log OPEN event
    
    Returns:
        Execution result dict or None if failed
    """
    signal_id = signal.get("signal_id", f"sig_{int(time.time() * 1000)}")
    decision_id = decision.get("decision_id", f"dec_{int(time.time() * 1000)}")
    
    # 1. Log DECISION
    try:
        from core.event_ledger import get_ledger
        ledger = get_ledger()
        ledger.decision(
            signal_id=signal_id,
            decision_id=decision_id,
            action=decision.get("action", "BUY"),
            reason=decision.get("reason", ""),
            confidence=decision.get("confidence", 0),
            source="autotrader"
        )
    except Exception as e:
        log.warning(f"Event Ledger decision logging failed: {e}")
    
    # 2. Execute BUY
    symbol = decision.get("symbol") or signal.get("symbol")
    tp_pct = decision.get("target_pct", 5.0)
    sl_pct = abs(decision.get("stop_pct", 2.0))
    timeout_sec = decision.get("timeout_sec", 300)
    
    try:
        result = await executor.execute_buy_with_oco(
            symbol=symbol,
            quote_amount=quote_amount,
            tp_pct=tp_pct,
            sl_pct=sl_pct,
            timeout_sec=timeout_sec
        )
    except Exception as e:
        log.error(f"Execution failed: {e}")
        return None
    
    if not result.success:
        log.error(f"Execution not successful: {result.error}")
        return None
    
    # 3. Register with watchdog
    try:
        position_id = await register_with_watchdog(
            executor=executor,
            symbol=symbol,
            entry_price=result.avg_price,
            quantity=result.quantity,
            target_pct=tp_pct,
            stop_pct=sl_pct,
            timeout_sec=timeout_sec,
            order_id=result.order_id,
            decision_id=decision_id,
            signal_id=signal_id,
        )
    except AutotraderWatchdogError as e:
        log.error(f"Watchdog registration failed: {e}")
        # Position was already emergency closed
        return None
    
    return {
        "success": True,
        "position_id": position_id,
        "symbol": symbol,
        "entry_price": result.avg_price,
        "quantity": result.quantity,
        "order_id": result.order_id,
        "oco_order_id": result.oco_order_id,
        "target_pct": tp_pct,
        "stop_pct": sl_pct,
        "timeout_sec": timeout_sec,
        "mode": result.mode,
    }


# ══════════════════════════════════════════════════════════════════════════════
# TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import asyncio
    import tempfile
    
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    
    print("=" * 70)
    print("AUTOTRADER WATCHDOG INTEGRATION TEST")
    print("=" * 70)
    
    # Use temp path for test
    with tempfile.TemporaryDirectory() as tmpdir:
        test_path = Path(tmpdir) / "positions.json"
        
        # Write directly to test path
        test_path.parent.mkdir(parents=True, exist_ok=True)
        
        async def test():
            # Mock executor
            class MockExecutor:
                async def emergency_close(self, symbol, qty):
                    print(f"[MOCK] Emergency close {symbol} {qty}")
            
            executor = MockExecutor()
            
            # Test registration
            pos_id = await register_with_watchdog(
                executor=executor,
                symbol="PEPEUSDT",
                entry_price=0.00001,
                quantity=1000000,
                target_pct=5.0,
                stop_pct=2.0,
                timeout_sec=300,
                order_id="test_123",
                decision_id="dec_456",
            )
            
            print(f"\n[OK] Registered position: {pos_id}")
            
            # Check positions
            positions = get_open_positions()
            print(f"[OK] Open positions: {len(positions)}")
            
            # Unregister
            unregister_from_watchdog(pos_id)
            
            positions = get_open_positions()
            print(f"[OK] After unregister: {len(positions)}")
            
            print("\n[PASS] Watchdog integration test complete")
        
        asyncio.run(test())
