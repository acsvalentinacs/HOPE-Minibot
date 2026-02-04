# -*- coding: utf-8 -*-
"""
HOPE v4.0 BINANCE OCO EXECUTOR (FIXED)
======================================

CRITICAL FIX: _client теперь инициализируется!

Предыдущая проблема:
    self._client = None  # Всегда None!
    
    if self._client:  # Всегда False!
        return await self._client.create_order(...)
    return None  # Всегда None → ордера не размещаются!

ИСПРАВЛЕНИЕ:
    - Lazy initialization через _ensure_client()
    - Fail-closed: если client не создан → RuntimeError
    - Emergency close при любых ошибках

USAGE:
    from execution.binance_oco_executor_fixed import BinanceOCOExecutor, ExecutionMode
    
    executor = BinanceOCOExecutor(
        api_key="...",
        api_secret="...",
        mode=ExecutionMode.TESTNET  # или LIVE или DRY
    )
    
    result = await executor.execute_buy_with_oco(
        symbol="PEPEUSDT",
        quote_amount=25.0,
        tp_pct=5.0,
        sl_pct=2.0
    )
"""

import os
import sys
import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any
from enum import Enum
from decimal import Decimal, ROUND_DOWN

log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# EXECUTION MODE
# ══════════════════════════════════════════════════════════════════════════════

class ExecutionMode(str, Enum):
    DRY = "DRY"
    TESTNET = "TESTNET"
    LIVE = "LIVE"


# ══════════════════════════════════════════════════════════════════════════════
# EXECUTION RESULT
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ExecutionResult:
    success: bool
    order_id: str = ""
    symbol: str = ""
    side: str = ""
    quantity: float = 0.0
    avg_price: float = 0.0
    oco_order_id: str = ""
    error: str = ""
    mode: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "avg_price": self.avg_price,
            "oco_order_id": self.oco_order_id,
            "error": self.error,
            "mode": self.mode,
        }


# ══════════════════════════════════════════════════════════════════════════════
# BINANCE OCO EXECUTOR
# ══════════════════════════════════════════════════════════════════════════════

class BinanceOCOExecutor:
    """
    Binance executor с правильной инициализацией клиента.
    Поддерживает DRY / TESTNET / LIVE режимы.
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        mode: ExecutionMode = ExecutionMode.DRY,
    ):
        self._api_key = api_key or os.environ.get("BINANCE_API_KEY", "")
        self._api_secret = api_secret or os.environ.get("BINANCE_API_SECRET", "")
        self._mode = mode
        self._client = None  # Will be initialized lazily
        self._client_lock = asyncio.Lock()
        
        # Testnet credentials override
        if mode == ExecutionMode.TESTNET:
            testnet_key = os.environ.get("BINANCE_TESTNET_API_KEY", "")
            testnet_secret = os.environ.get("BINANCE_TESTNET_API_SECRET", "")
            if testnet_key and testnet_secret:
                self._api_key = testnet_key
                self._api_secret = testnet_secret
        
        # Validate credentials for non-DRY modes
        if mode != ExecutionMode.DRY:
            if not self._api_key or not self._api_secret:
                raise ValueError(f"API credentials required for {mode.value} mode")
        
        log.info(f"[EXECUTOR] Initialized in {mode.value} mode")
    
    async def _ensure_client(self):
        """
        Lazy initialization of Binance async client.
        CRITICAL FIX: This was missing before!
        """
        if self._mode == ExecutionMode.DRY:
            return  # No client needed for DRY mode
        
        async with self._client_lock:
            if self._client is not None:
                return
            
            try:
                from binance import AsyncClient
                
                if self._mode == ExecutionMode.TESTNET:
                    self._client = await AsyncClient.create(
                        self._api_key,
                        self._api_secret,
                        testnet=True
                    )
                    log.info("[EXECUTOR] Binance TESTNET client initialized")
                else:
                    self._client = await AsyncClient.create(
                        self._api_key,
                        self._api_secret,
                        testnet=False
                    )
                    log.info("[EXECUTOR] Binance LIVE client initialized")
                    
            except ImportError:
                log.error("[EXECUTOR] python-binance not installed!")
                raise RuntimeError("python-binance package required for trading")
            except Exception as e:
                log.error(f"[EXECUTOR] Client initialization failed: {e}")
                raise RuntimeError(f"Binance client init failed: {e}") from e
    
    async def close(self):
        """Close client connection."""
        if self._client:
            await self._client.close_connection()
            self._client = None
    
    async def get_price(self, symbol: str) -> float:
        """Get current price for symbol."""
        if self._mode == ExecutionMode.DRY:
            # Use REST API for price even in DRY mode
            try:
                import httpx
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}",
                        timeout=5.0
                    )
                    data = resp.json()
                    return float(data["price"])
            except Exception as e:
                log.error(f"[EXECUTOR] Price fetch failed: {e}")
                return 0.0
        
        await self._ensure_client()
        
        try:
            ticker = await self._client.get_symbol_ticker(symbol=symbol)
            return float(ticker["price"])
        except Exception as e:
            log.error(f"[EXECUTOR] Price fetch failed: {e}")
            return 0.0
    
    async def get_balance(self, asset: str = "USDT") -> float:
        """Get asset balance."""
        if self._mode == ExecutionMode.DRY:
            return 1000.0  # Simulated balance
        
        await self._ensure_client()
        
        try:
            account = await self._client.get_account()
            for balance in account["balances"]:
                if balance["asset"] == asset:
                    return float(balance["free"])
            return 0.0
        except Exception as e:
            log.error(f"[EXECUTOR] Balance fetch failed: {e}")
            return 0.0
    
    async def execute_buy_with_oco(
        self,
        symbol: str,
        quote_amount: float,
        tp_pct: float,
        sl_pct: float,
        timeout_sec: int = 300,
    ) -> ExecutionResult:
        """
        Execute BUY order and place OCO for exit.
        
        Args:
            symbol: Trading pair (e.g. "PEPEUSDT")
            quote_amount: Amount in USDT to spend
            tp_pct: Take profit percentage (e.g. 5.0 for 5%)
            sl_pct: Stop loss percentage (e.g. 2.0 for 2%)
            timeout_sec: Timeout for position (for watchdog)
            
        Returns:
            ExecutionResult with order details
        """
        log.info(f"[EXECUTOR] BUY {symbol} ${quote_amount} TP:{tp_pct}% SL:{sl_pct}%")
        
        # DRY MODE
        if self._mode == ExecutionMode.DRY:
            price = await self.get_price(symbol)
            if price <= 0:
                return ExecutionResult(
                    success=False,
                    error="Price fetch failed",
                    mode=self._mode.value
                )
            
            quantity = quote_amount / price
            
            log.info(f"[DRY] Would BUY {quantity:.8f} {symbol} @ ${price}")
            log.info(f"[DRY] Would place OCO: TP=${price * (1 + tp_pct/100):.8f}, SL=${price * (1 - sl_pct/100):.8f}")
            
            return ExecutionResult(
                success=True,
                order_id=f"dry_{int(time.time() * 1000)}",
                symbol=symbol,
                side="BUY",
                quantity=quantity,
                avg_price=price,
                oco_order_id=f"dry_oco_{int(time.time() * 1000)}",
                mode=self._mode.value
            )
        
        # TESTNET / LIVE MODE
        await self._ensure_client()
        
        if self._client is None:
            return ExecutionResult(
                success=False,
                error="Client not initialized",
                mode=self._mode.value
            )
        
        try:
            # 1. Get current price
            price = await self.get_price(symbol)
            if price <= 0:
                return ExecutionResult(success=False, error="Price fetch failed", mode=self._mode.value)
            
            # 2. Calculate quantity
            quantity = quote_amount / price
            
            # 3. Get symbol info for precision
            info = await self._client.get_symbol_info(symbol)
            if not info:
                return ExecutionResult(success=False, error="Symbol info not found", mode=self._mode.value)
            
            # Find LOT_SIZE filter for quantity precision
            lot_size = None
            price_filter = None
            for f in info["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    lot_size = f
                elif f["filterType"] == "PRICE_FILTER":
                    price_filter = f
            
            if lot_size:
                step_size = Decimal(lot_size["stepSize"])
                quantity = float(Decimal(str(quantity)).quantize(step_size, rounding=ROUND_DOWN))
            
            # 4. Execute MARKET BUY
            log.info(f"[{self._mode.value}] Placing MARKET BUY {quantity} {symbol}")
            
            buy_order = await self._client.create_order(
                symbol=symbol,
                side="BUY",
                type="MARKET",
                quantity=quantity
            )
            
            order_id = str(buy_order["orderId"])
            filled_qty = float(buy_order.get("executedQty", quantity))
            avg_price = self._calc_avg_price(buy_order)
            
            log.info(f"[{self._mode.value}] BUY filled: {filled_qty} @ ${avg_price}")
            
            # 5. Place OCO order
            tp_price = avg_price * (1 + tp_pct / 100)
            sl_trigger = avg_price * (1 - sl_pct / 100)
            sl_price = sl_trigger * 0.999  # Limit slightly below trigger
            
            # Round prices
            if price_filter:
                tick_size = Decimal(price_filter["tickSize"])
                tp_price = float(Decimal(str(tp_price)).quantize(tick_size, rounding=ROUND_DOWN))
                sl_trigger = float(Decimal(str(sl_trigger)).quantize(tick_size, rounding=ROUND_DOWN))
                sl_price = float(Decimal(str(sl_price)).quantize(tick_size, rounding=ROUND_DOWN))
            
            log.info(f"[{self._mode.value}] Placing OCO: TP=${tp_price}, SL=${sl_price}")
            
            oco_order = await self._client.create_oco_order(
                symbol=symbol,
                side="SELL",
                quantity=filled_qty,
                price=tp_price,
                stopPrice=sl_trigger,
                stopLimitPrice=sl_price,
                stopLimitTimeInForce="GTC"
            )
            
            oco_order_id = str(oco_order.get("orderListId", ""))
            
            log.info(f"[{self._mode.value}] OCO placed: {oco_order_id}")
            
            return ExecutionResult(
                success=True,
                order_id=order_id,
                symbol=symbol,
                side="BUY",
                quantity=filled_qty,
                avg_price=avg_price,
                oco_order_id=oco_order_id,
                mode=self._mode.value
            )
            
        except Exception as e:
            log.error(f"[{self._mode.value}] Execution error: {e}")
            
            # EMERGENCY: If we bought but OCO failed, try emergency close
            if 'buy_order' in dir() and buy_order:
                log.critical("[EMERGENCY] BUY succeeded but OCO failed - initiating emergency close!")
                try:
                    await self.emergency_close(symbol, float(buy_order.get("executedQty", 0)))
                except Exception as ee:
                    log.critical(f"[EMERGENCY] Emergency close also failed: {ee}")
            
            return ExecutionResult(
                success=False,
                error=str(e),
                mode=self._mode.value
            )
    
    async def emergency_close(self, symbol: str, quantity: float = 0) -> bool:
        """
        Emergency market sell to close position.
        """
        log.warning(f"[EMERGENCY] Closing {symbol} quantity={quantity}")
        
        if self._mode == ExecutionMode.DRY:
            log.info(f"[DRY] Would emergency sell {quantity} {symbol}")
            return True
        
        await self._ensure_client()
        
        if self._client is None:
            log.error("[EMERGENCY] No client for emergency close!")
            return False
        
        try:
            if quantity <= 0:
                # Get balance
                account = await self._client.get_account()
                for balance in account["balances"]:
                    if balance["asset"] == symbol.replace("USDT", ""):
                        quantity = float(balance["free"])
                        break
            
            if quantity <= 0:
                log.warning("[EMERGENCY] No quantity to close")
                return False
            
            order = await self._client.create_order(
                symbol=symbol,
                side="SELL",
                type="MARKET",
                quantity=quantity
            )
            
            log.info(f"[EMERGENCY] Closed: {order}")
            return True
            
        except Exception as e:
            log.error(f"[EMERGENCY] Close failed: {e}")
            return False
    
    def _calc_avg_price(self, order: Dict[str, Any]) -> float:
        """Calculate average fill price from order."""
        fills = order.get("fills", [])
        if not fills:
            return float(order.get("price", 0))
        
        total_qty = sum(float(f["qty"]) for f in fills)
        total_cost = sum(float(f["qty"]) * float(f["price"]) for f in fills)
        
        return total_cost / total_qty if total_qty > 0 else 0


# ══════════════════════════════════════════════════════════════════════════════
# FACTORY
# ══════════════════════════════════════════════════════════════════════════════

def create_executor_from_env() -> BinanceOCOExecutor:
    """
    Create executor based on environment variables.
    
    HOPE_MODE=DRY/TESTNET/LIVE
    BINANCE_TESTNET=true/false
    """
    hope_mode = os.environ.get("HOPE_MODE", "DRY").upper()
    testnet = os.environ.get("BINANCE_TESTNET", "true").lower() in ("true", "1", "yes")
    
    if hope_mode == "LIVE" and not testnet:
        # Additional safety check
        ack = os.environ.get("HOPE_LIVE_ACK", "")
        if ack != "YES_I_UNDERSTAND":
            log.warning("[EXECUTOR] LIVE mode without ACK - falling back to DRY")
            return BinanceOCOExecutor(mode=ExecutionMode.DRY)
        return BinanceOCOExecutor(mode=ExecutionMode.LIVE)
    elif hope_mode == "TESTNET" or testnet:
        return BinanceOCOExecutor(mode=ExecutionMode.TESTNET)
    else:
        return BinanceOCOExecutor(mode=ExecutionMode.DRY)


# ══════════════════════════════════════════════════════════════════════════════
# TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    
    print("=" * 70)
    print("BINANCE OCO EXECUTOR TEST")
    print("=" * 70)
    
    async def test():
        # Test DRY mode
        executor = BinanceOCOExecutor(mode=ExecutionMode.DRY)
        
        # Get price
        price = await executor.get_price("BTCUSDT")
        print(f"\n[OK] BTC price: ${price:,.2f}")
        
        # Test buy with OCO
        result = await executor.execute_buy_with_oco(
            symbol="PEPEUSDT",
            quote_amount=25.0,
            tp_pct=5.0,
            sl_pct=2.0
        )
        
        print(f"\n[OK] Execution result:")
        print(f"  Success: {result.success}")
        print(f"  Mode: {result.mode}")
        print(f"  Order ID: {result.order_id}")
        print(f"  Quantity: {result.quantity:.8f}")
        print(f"  Avg Price: ${result.avg_price:.8f}")
        
        await executor.close()
        
        print("\n[PASS] Executor test complete")
    
    asyncio.run(test())
