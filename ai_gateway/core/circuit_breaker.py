# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 13:35:00 UTC
# Purpose: Circuit Breaker - fail-closed protection for HOPE AI Trading
# sha256: circuit_breaker_v1.0
# === END SIGNATURE ===
"""
HOPE AI - Circuit Breaker v1.0

Fail-closed protection that stops trading when conditions degrade.

TRIGGERS:
- MAE avg < -3% on 10+ trades → STOP
- Win rate < 30% on 20+ trades → STOP  
- 3 consecutive losses → STOP
- Manual trip → STOP

RESET:
- Requires manual approval (fail-closed principle)
- Reset command: POST /circuit-breaker/reset with admin token

Usage:
    from circuit_breaker import CircuitBreaker
    
    cb = CircuitBreaker()
    if cb.is_open():
        return  # Don't trade
    
    # After trade outcome
    cb.record_outcome(win=True, mae=-0.5)
"""

import json
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"  # Normal operation, trading allowed
    OPEN = "open"      # Tripped, trading blocked
    HALF_OPEN = "half_open"  # Testing with limited trades


@dataclass
class TripReason:
    reason: str
    triggered_at: str
    metric_value: float
    threshold: float
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CircuitBreakerConfig:
    # MAE threshold
    mae_threshold: float = -3.0  # Stop if avg MAE < -3%
    mae_min_trades: int = 10     # Minimum trades to evaluate
    
    # Win rate threshold
    win_rate_threshold: float = 0.30  # Stop if win rate < 30%
    win_rate_min_trades: int = 20     # Minimum trades to evaluate
    
    # Consecutive losses
    max_consecutive_losses: int = 3
    
    # Half-open testing
    half_open_test_trades: int = 5  # Trades before full reset
    half_open_win_rate: float = 0.40  # Required to close
    
    # State file
    state_file: str = "state/ai/circuit_breaker.json"


@dataclass
class CircuitBreakerState:
    state: CircuitState = CircuitState.CLOSED
    trip_reasons: List[TripReason] = field(default_factory=list)
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    consecutive_losses: int = 0
    mae_sum: float = 0.0
    recent_outcomes: List[Dict] = field(default_factory=list)  # Last 50 outcomes
    last_trip_time: Optional[str] = None
    last_reset_time: Optional[str] = None
    half_open_trades: int = 0
    half_open_wins: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['state'] = self.state.value
        d['trip_reasons'] = [r.to_dict() if isinstance(r, TripReason) else r for r in self.trip_reasons]
        return d
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'CircuitBreakerState':
        d['state'] = CircuitState(d.get('state', 'closed'))
        d['trip_reasons'] = [TripReason(**r) if isinstance(r, dict) else r for r in d.get('trip_reasons', [])]
        return cls(**d)


class CircuitBreaker:
    """
    Fail-closed circuit breaker for trading protection.
    
    When tripped, NO trades are allowed until manual reset.
    This is a safety mechanism to prevent cascading losses.
    """
    
    def __init__(self, config: Optional[CircuitBreakerConfig] = None, state_dir: Optional[str] = None):
        self.config = config or CircuitBreakerConfig()
        
        if state_dir:
            self.config.state_file = str(Path(state_dir) / "circuit_breaker.json")
        
        self.state = self._load_state()
        logger.info(f"CircuitBreaker initialized: state={self.state.state.value}")
    
    def _load_state(self) -> CircuitBreakerState:
        """Load state from file or create new"""
        state_path = Path(self.config.state_file)
        
        if state_path.exists():
            try:
                with open(state_path, 'r') as f:
                    data = json.load(f)
                return CircuitBreakerState.from_dict(data)
            except Exception as e:
                logger.warning(f"Failed to load circuit breaker state: {e}")
        
        return CircuitBreakerState()
    
    def _save_state(self):
        """Persist state to file (atomic write)"""
        state_path = Path(self.config.state_file)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        
        temp_path = state_path.with_suffix('.tmp')
        
        try:
            with open(temp_path, 'w') as f:
                json.dump(self.state.to_dict(), f, indent=2)
                f.flush()
                import os
                os.fsync(f.fileno())
            
            temp_path.replace(state_path)
            
        except Exception as e:
            logger.error(f"Failed to save circuit breaker state: {e}")
            if temp_path.exists():
                temp_path.unlink()
            raise
    
    def is_open(self) -> bool:
        """Check if circuit is open (trading blocked)"""
        return self.state.state == CircuitState.OPEN
    
    def is_closed(self) -> bool:
        """Check if circuit is closed (trading allowed)"""
        return self.state.state == CircuitState.CLOSED
    
    def is_half_open(self) -> bool:
        """Check if circuit is in testing mode"""
        return self.state.state == CircuitState.HALF_OPEN
    
    def can_trade(self) -> bool:
        """Check if trading is allowed"""
        return self.state.state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)
    
    def record_outcome(self, win: bool, mae: float, mfe: float = 0.0, 
                       symbol: str = "", signal_id: str = "") -> bool:
        """
        Record trade outcome and check if circuit should trip.
        
        Args:
            win: True if trade was profitable
            mae: Maximum Adverse Excursion (negative = loss)
            mfe: Maximum Favorable Excursion (positive = gain)
            symbol: Trading symbol
            signal_id: Signal identifier
            
        Returns:
            True if trading can continue, False if circuit tripped
        """
        now = datetime.now(timezone.utc).isoformat()
        
        # Record outcome
        outcome = {
            "timestamp": now,
            "win": win,
            "mae": mae,
            "mfe": mfe,
            "symbol": symbol,
            "signal_id": signal_id,
        }
        
        self.state.recent_outcomes.append(outcome)
        if len(self.state.recent_outcomes) > 50:
            self.state.recent_outcomes = self.state.recent_outcomes[-50:]
        
        # Update stats
        self.state.total_trades += 1
        self.state.mae_sum += mae
        
        if win:
            self.state.wins += 1
            self.state.consecutive_losses = 0
        else:
            self.state.losses += 1
            self.state.consecutive_losses += 1
        
        # Half-open mode tracking
        if self.state.state == CircuitState.HALF_OPEN:
            self.state.half_open_trades += 1
            if win:
                self.state.half_open_wins += 1
            
            # Check if half-open test passed
            if self.state.half_open_trades >= self.config.half_open_test_trades:
                half_open_wr = self.state.half_open_wins / self.state.half_open_trades
                if half_open_wr >= self.config.half_open_win_rate:
                    logger.info(f"Half-open test PASSED: {half_open_wr:.1%} >= {self.config.half_open_win_rate:.1%}")
                    self._close_circuit()
                else:
                    logger.warning(f"Half-open test FAILED: {half_open_wr:.1%} < {self.config.half_open_win_rate:.1%}")
                    self._trip_circuit(TripReason(
                        reason="half_open_test_failed",
                        triggered_at=now,
                        metric_value=half_open_wr,
                        threshold=self.config.half_open_win_rate,
                    ))
                    return False
        
        # Check trip conditions (only if currently closed)
        if self.state.state == CircuitState.CLOSED:
            trip_reason = self._check_trip_conditions()
            if trip_reason:
                self._trip_circuit(trip_reason)
                return False
        
        self._save_state()
        return True
    
    def _check_trip_conditions(self) -> Optional[TripReason]:
        """Check if any trip conditions are met"""
        now = datetime.now(timezone.utc).isoformat()
        
        # Check consecutive losses
        if self.state.consecutive_losses >= self.config.max_consecutive_losses:
            return TripReason(
                reason="consecutive_losses",
                triggered_at=now,
                metric_value=float(self.state.consecutive_losses),
                threshold=float(self.config.max_consecutive_losses),
            )
        
        # Check MAE threshold
        if self.state.total_trades >= self.config.mae_min_trades:
            avg_mae = self.state.mae_sum / self.state.total_trades
            if avg_mae < self.config.mae_threshold:
                return TripReason(
                    reason="mae_threshold",
                    triggered_at=now,
                    metric_value=avg_mae,
                    threshold=self.config.mae_threshold,
                )
        
        # Check win rate threshold
        if self.state.total_trades >= self.config.win_rate_min_trades:
            win_rate = self.state.wins / self.state.total_trades
            if win_rate < self.config.win_rate_threshold:
                return TripReason(
                    reason="win_rate_threshold",
                    triggered_at=now,
                    metric_value=win_rate,
                    threshold=self.config.win_rate_threshold,
                )
        
        return None
    
    def _trip_circuit(self, reason: TripReason):
        """Trip the circuit breaker"""
        self.state.state = CircuitState.OPEN
        self.state.trip_reasons.append(reason)
        self.state.last_trip_time = reason.triggered_at
        self.state.half_open_trades = 0
        self.state.half_open_wins = 0
        
        self._save_state()
        
        logger.critical(
            f"🔴 CIRCUIT BREAKER TRIPPED: {reason.reason} "
            f"(value={reason.metric_value:.2f}, threshold={reason.threshold:.2f})"
        )
    
    def _close_circuit(self):
        """Close the circuit (resume normal trading)"""
        self.state.state = CircuitState.CLOSED
        self.state.last_reset_time = datetime.now(timezone.utc).isoformat()
        self.state.half_open_trades = 0
        self.state.half_open_wins = 0
        
        self._save_state()
        
        logger.info("🟢 CIRCUIT BREAKER CLOSED: Trading resumed")
    
    def trip_manual(self, reason: str = "manual_trip"):
        """Manually trip the circuit breaker"""
        now = datetime.now(timezone.utc).isoformat()
        self._trip_circuit(TripReason(
            reason=reason,
            triggered_at=now,
            metric_value=0,
            threshold=0,
        ))
    
    def reset(self, admin_token: str, mode: str = "half_open") -> bool:
        """
        Reset the circuit breaker (requires admin approval).
        
        Args:
            admin_token: Admin authentication token
            mode: "half_open" for testing mode, "full" for immediate close
            
        Returns:
            True if reset successful
        """
        # TODO: Implement proper admin token validation
        # For now, accept any non-empty token
        if not admin_token:
            logger.warning("Reset attempted without admin token")
            return False
        
        if self.state.state != CircuitState.OPEN:
            logger.info("Reset called but circuit is not open")
            return True
        
        now = datetime.now(timezone.utc).isoformat()
        
        if mode == "full":
            # Full reset - immediately close
            self._close_circuit()
            logger.info(f"Circuit breaker FULL RESET by admin at {now}")
        else:
            # Half-open mode - test with limited trades
            self.state.state = CircuitState.HALF_OPEN
            self.state.half_open_trades = 0
            self.state.half_open_wins = 0
            self.state.last_reset_time = now
            self._save_state()
            logger.info(f"Circuit breaker entering HALF-OPEN mode at {now}")
        
        return True
    
    def get_status(self) -> Dict[str, Any]:
        """Get current circuit breaker status"""
        avg_mae = self.state.mae_sum / self.state.total_trades if self.state.total_trades > 0 else 0
        win_rate = self.state.wins / self.state.total_trades if self.state.total_trades > 0 else 0
        
        return {
            "state": self.state.state.value,
            "can_trade": self.can_trade(),
            "total_trades": self.state.total_trades,
            "wins": self.state.wins,
            "losses": self.state.losses,
            "win_rate": win_rate,
            "avg_mae": avg_mae,
            "consecutive_losses": self.state.consecutive_losses,
            "trip_reasons": [r.to_dict() if isinstance(r, TripReason) else r for r in self.state.trip_reasons],
            "last_trip_time": self.state.last_trip_time,
            "last_reset_time": self.state.last_reset_time,
            "thresholds": {
                "mae": self.config.mae_threshold,
                "win_rate": self.config.win_rate_threshold,
                "consecutive_losses": self.config.max_consecutive_losses,
            },
            "half_open": {
                "trades": self.state.half_open_trades,
                "wins": self.state.half_open_wins,
                "required_trades": self.config.half_open_test_trades,
                "required_win_rate": self.config.half_open_win_rate,
            } if self.state.state == CircuitState.HALF_OPEN else None,
        }
    
    def clear_stats(self):
        """Clear statistics (for testing only)"""
        self.state = CircuitBreakerState()
        self._save_state()
        logger.info("Circuit breaker stats cleared")


# === INTEGRATION WITH AI GATEWAY ===

def create_circuit_breaker_routes(app, circuit_breaker: CircuitBreaker):
    """
    Add circuit breaker routes to FastAPI app.
    
    Usage:
        from circuit_breaker import CircuitBreaker, create_circuit_breaker_routes
        
        cb = CircuitBreaker()
        create_circuit_breaker_routes(app, cb)
    """
    from fastapi import HTTPException
    from pydantic import BaseModel
    
    class ResetRequest(BaseModel):
        admin_token: str
        mode: str = "half_open"
    
    @app.get("/circuit-breaker/status")
    async def get_circuit_status():
        return circuit_breaker.get_status()
    
    @app.post("/circuit-breaker/trip")
    async def trip_circuit(reason: str = "manual"):
        circuit_breaker.trip_manual(reason)
        return {"status": "tripped", "reason": reason}
    
    @app.post("/circuit-breaker/reset")
    async def reset_circuit(request: ResetRequest):
        if not request.admin_token:
            raise HTTPException(status_code=401, detail="Admin token required")
        
        success = circuit_breaker.reset(request.admin_token, request.mode)
        if success:
            return {"status": "reset", "mode": request.mode}
        else:
            raise HTTPException(status_code=403, detail="Reset failed")


# === CLI ===

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="HOPE AI Circuit Breaker")
    parser.add_argument("--status", action="store_true", help="Show current status")
    parser.add_argument("--trip", type=str, help="Manually trip with reason")
    parser.add_argument("--reset", type=str, help="Reset with admin token")
    parser.add_argument("--clear", action="store_true", help="Clear stats (testing only)")
    parser.add_argument("--state-dir", type=str, default="state/ai", help="State directory")
    
    args = parser.parse_args()
    
    cb = CircuitBreaker(state_dir=args.state_dir)
    
    if args.status:
        status = cb.get_status()
        print(json.dumps(status, indent=2))
    
    elif args.trip:
        cb.trip_manual(args.trip)
        print(f"Circuit tripped: {args.trip}")
    
    elif args.reset:
        success = cb.reset(args.reset, "half_open")
        print(f"Reset {'successful' if success else 'failed'}")
    
    elif args.clear:
        cb.clear_stats()
        print("Stats cleared")
    
    else:
        parser.print_help()
