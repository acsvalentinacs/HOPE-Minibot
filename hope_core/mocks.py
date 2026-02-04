# === AI SIGNATURE ===
# Module: hope_core/mocks.py
# Created by: Claude (opus-4.5)
# Created at: 2026-02-04 11:15:00 UTC
# Purpose: Mock modules for testing without real Binance/Eye of God
# === END SIGNATURE ===
"""
Mock Modules for HOPE Core Testing

Provides mock implementations:
- MockEyeOfGodV3: Simulates two-chamber decision system
- MockOrderExecutor: Simulates Binance order execution
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import random
import time


# =============================================================================
# MOCK EYE OF GOD V3
# =============================================================================

class MockEyeOfGodV3:
    """
    Mock implementation of Eye of God V3.
    
    Simulates the two-chamber decision system for testing.
    """
    
    # Confidence thresholds (matching real system)
    MIN_CONFIDENCE_TO_TRADE = 0.50
    MIN_CONFIDENCE_AI_OVERRIDE = 0.35
    MIN_CONFIDENCE_MOMENTUM = 0.25
    
    def __init__(self, mode: str = "BALANCED"):
        """
        Initialize mock Eye of God.
        
        Args:
            mode: Decision mode
                - AGGRESSIVE: Lower thresholds, more trades
                - BALANCED: Normal thresholds
                - CONSERVATIVE: Higher thresholds, fewer trades
                - ALWAYS_BUY: Always returns BUY (for testing)
                - ALWAYS_HOLD: Always returns HOLD (for testing)
        """
        self.mode = mode
        self._decisions_made = 0
        self._decisions_buy = 0
        self._decisions_hold = 0
        
        # Mode-specific thresholds
        self._thresholds = {
            "AGGRESSIVE": 0.30,
            "BALANCED": 0.50,
            "CONSERVATIVE": 0.70,
            "ALWAYS_BUY": 0.0,
            "ALWAYS_HOLD": 1.0,
        }
    
    def evaluate_signal(
        self,
        symbol: str,
        score: float,
        source: str,
        market_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate trading signal (mock implementation).
        
        Args:
            symbol: Trading symbol
            score: Signal score (0-1)
            source: Signal source
            market_data: Optional market data
            
        Returns:
            Decision with action, confidence, reasons
        """
        self._decisions_made += 1
        
        threshold = self._thresholds.get(self.mode, 0.50)
        
        # Simulate two-chamber voting
        chamber1_vote = score >= threshold
        chamber2_vote = random.random() < score  # Probabilistic
        
        # Both chambers must agree for BUY
        if self.mode == "ALWAYS_BUY":
            action = "BUY"
            confidence = max(0.75, score)
        elif self.mode == "ALWAYS_HOLD":
            action = "HOLD"
            confidence = score * 0.5
        elif chamber1_vote and chamber2_vote:
            action = "BUY"
            confidence = (score + random.uniform(0, 0.1))
            confidence = min(1.0, confidence)
        else:
            action = "HOLD"
            confidence = score * 0.8
        
        # Track stats
        if action == "BUY":
            self._decisions_buy += 1
        else:
            self._decisions_hold += 1
        
        # Build reasons
        reasons = []
        if action == "BUY":
            reasons.append(f"Score {score:.0%} >= threshold {threshold:.0%}")
            reasons.append(f"Source: {source}")
            if market_data:
                reasons.append("Market data confirms")
        else:
            reasons.append(f"Score {score:.0%} < threshold {threshold:.0%}")
            if not chamber1_vote:
                reasons.append("Chamber 1 rejected")
            if not chamber2_vote:
                reasons.append("Chamber 2 rejected")
        
        return {
            "action": action,
            "confidence": confidence,
            "symbol": symbol,
            "reasons": reasons,
            "chamber_votes": {
                "chamber1": chamber1_vote,
                "chamber2": chamber2_vote,
            },
            "mode": self.mode,
            "mock": True,
        }
    
    def decide(self, signal_data: Dict[str, Any]) -> Dict[str, Any]:
        """Alias for evaluate_signal."""
        return self.evaluate_signal(
            symbol=signal_data.get("symbol", "UNKNOWN"),
            score=signal_data.get("score", 0),
            source=signal_data.get("source", "UNKNOWN"),
            market_data=signal_data.get("market_data"),
        )
    
    def get_stats(self) -> Dict[str, Any]:
        """Get decision statistics."""
        return {
            "total": self._decisions_made,
            "buy": self._decisions_buy,
            "hold": self._decisions_hold,
            "buy_rate": self._decisions_buy / max(1, self._decisions_made),
            "mode": self.mode,
        }


# =============================================================================
# MOCK ORDER EXECUTOR
# =============================================================================

class TradingMode(Enum):
    """Trading mode."""
    DRY = "DRY"
    TESTNET = "TESTNET"
    LIVE = "LIVE"


@dataclass
class MockOrder:
    """Simulated order."""
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    status: str
    filled_qty: float = 0.0
    avg_price: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "orderId": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "origQty": str(self.quantity),
            "price": str(self.price),
            "status": self.status,
            "executedQty": str(self.filled_qty),
            "avgPrice": str(self.avg_price),
        }


class MockOrderExecutor:
    """
    Mock implementation of Order Executor.
    
    Simulates Binance order execution for testing.
    """
    
    def __init__(self, mode: TradingMode = TradingMode.DRY):
        """
        Initialize mock executor.
        
        Args:
            mode: Trading mode
        """
        self.mode = mode
        self._balance = {"USDT": 100.0}
        self._orders: Dict[str, MockOrder] = {}
        self._positions: Dict[str, Dict[str, Any]] = {}
        self._order_counter = 0
        
        # Simulated prices (updated on each call)
        self._prices = {
            "BTCUSDT": 45000.0,
            "ETHUSDT": 2500.0,
            "SOLUSDT": 100.0,
            "BNBUSDT": 300.0,
            "XRPUSDT": 0.50,
            "DOGEUSDT": 0.08,
            "ADAUSDT": 0.40,
            "PEPEUSDT": 0.000001,
        }
        
        # Success rate (for testing failures)
        self._success_rate = 0.95
        
        # Connected flag
        self.exchange = True  # Simulates ccxt exchange object
    
    def _get_price(self, symbol: str) -> float:
        """Get simulated price with small random variation."""
        base = self._prices.get(symbol, 1.0)
        variation = random.uniform(-0.001, 0.001)
        return base * (1 + variation)
    
    def _generate_order_id(self) -> str:
        """Generate order ID."""
        self._order_counter += 1
        return f"MOCK_{int(time.time() * 1000)}_{self._order_counter}"
    
    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
    ) -> Dict[str, Any]:
        """
        Place market order (mock).
        
        Args:
            symbol: Trading symbol
            side: BUY or SELL
            quantity: Order quantity
            
        Returns:
            Order result
        """
        # Simulate random failure
        if random.random() > self._success_rate:
            return {
                "status": "FAILED",
                "error": "Simulated order failure",
            }
        
        price = self._get_price(symbol)
        order_id = self._generate_order_id()
        
        order = MockOrder(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            status="FILLED",
            filled_qty=quantity,
            avg_price=price,
        )
        
        self._orders[order_id] = order
        
        # Update balance
        if side == "BUY":
            cost = quantity * price
            self._balance["USDT"] = max(0, self._balance.get("USDT", 0) - cost)
            self._balance[symbol.replace("USDT", "")] = \
                self._balance.get(symbol.replace("USDT", ""), 0) + quantity
        else:
            self._balance[symbol.replace("USDT", "")] = \
                max(0, self._balance.get(symbol.replace("USDT", ""), 0) - quantity)
            self._balance["USDT"] = self._balance.get("USDT", 0) + quantity * price
        
        # Track position
        if side == "BUY":
            pos_id = f"pos_{order_id}"
            self._positions[pos_id] = {
                "id": pos_id,
                "symbol": symbol,
                "side": "LONG",
                "quantity": quantity,
                "entry_price": price,
                "order_id": order_id,
            }
        
        return {
            **order.to_dict(),
            "status": "FILLED",
            "mock": True,
        }
    
    def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
    ) -> Dict[str, Any]:
        """Place limit order (mock)."""
        # For simplicity, treat as market order with specified price
        return self.place_market_order(symbol, side, quantity)
    
    def execute_order(
        self,
        symbol: str,
        side: str,
        quote_quantity: float = 20.0,
    ) -> Dict[str, Any]:
        """
        Execute order with quote quantity.
        
        Args:
            symbol: Trading symbol
            side: BUY or SELL
            quote_quantity: Amount in USDT
            
        Returns:
            Order result
        """
        price = self._get_price(symbol)
        quantity = quote_quantity / price
        
        return self.place_market_order(symbol, side, quantity)
    
    def get_balance(self) -> Dict[str, float]:
        """Get account balance."""
        return dict(self._balance)
    
    def get_open_positions(self) -> List[Dict[str, Any]]:
        """Get open positions."""
        return list(self._positions.values())
    
    def close_position(self, position_id: str) -> Dict[str, Any]:
        """Close position."""
        if position_id not in self._positions:
            return {"status": "NOT_FOUND", "error": "Position not found"}
        
        pos = self._positions.pop(position_id)
        return self.place_market_order(pos["symbol"], "SELL", pos["quantity"])


# =============================================================================
# FACTORY FUNCTION
# =============================================================================

def create_mock_eye_of_god(mode: str = "BALANCED") -> MockEyeOfGodV3:
    """Create mock Eye of God instance."""
    return MockEyeOfGodV3(mode)


def create_mock_executor(mode: str = "DRY") -> MockOrderExecutor:
    """Create mock Order Executor instance."""
    trading_mode = TradingMode[mode] if isinstance(mode, str) else mode
    return MockOrderExecutor(trading_mode)
