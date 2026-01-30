# -*- coding: utf-8 -*-
"""
BINANCE OCO EXECUTOR v1.0 - LIVE TRADING
MARKET entry → OCO (TP+SL) → Trailing → Exit → Log
"""

import os
import time
import json
import logging
import asyncio
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Tuple
from enum import Enum
from decimal import Decimal, ROUND_DOWN

log = logging.getLogger(__name__)

class OrderStatus(Enum):
    PENDING = "pending"
    ENTRY_FILLED = "entry_filled"
    TP_HIT = "tp_hit"
    SL_HIT = "sl_hit"
    TRAILING_EXIT = "trailing_exit"
    TIMEOUT = "timeout"
    ERROR = "error"
    CANCELLED = "cancelled"

class ExecutionMode(Enum):
    DRY = "dry"
    TESTNET = "testnet"
    LIVE = "live"

@dataclass
class ExecutorConfig:
    mode: ExecutionMode = ExecutionMode.LIVE
    max_position_usdt: float = 50.0
    max_daily_loss_usdt: float = 100.0
    max_concurrent_positions: int = 3
    trailing_activation_pct: float = 0.5
    trailing_step_pct: float = 0.3

@dataclass
class TradeResult:
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl_usdt: float
    pnl_pct: float
    status: OrderStatus
    entry_time: float
    exit_time: float
    duration_sec: float
    fees_usdt: float
    signal_data: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol, "side": self.side,
            "entry_price": self.entry_price, "exit_price": self.exit_price,
            "pnl_usdt": round(self.pnl_usdt, 4), "pnl_pct": round(self.pnl_pct, 4),
            "status": self.status.value, "duration_sec": round(self.duration_sec, 1),
        }

class BinanceOCOExecutor:
    def __init__(self, config: Optional[ExecutorConfig] = None):
        self.config = config or ExecutorConfig()
        self._client = None
        self._daily_pnl: float = 0.0
        self._active_positions: Dict[str, Any] = {}
        self._kill_switch: bool = False
    
    async def execute_trade(
        self, symbol: str, side: str, position_usdt: float,
        tp_pct: float, sl_pct: float, timeout_sec: int,
        signal_data: Optional[Dict[str, Any]] = None,
    ) -> TradeResult:
        entry_time = time.time()
        signal_data = signal_data or {}
        
        # Safety checks
        if self._kill_switch:
            return self._error_result(symbol, side, "kill_switch", entry_time, signal_data)
        
        position_usdt = min(position_usdt, self.config.max_position_usdt)
        
        if self._daily_pnl <= -self.config.max_daily_loss_usdt:
            return self._error_result(symbol, side, "daily_loss_limit", entry_time, signal_data)
        
        if len(self._active_positions) >= self.config.max_concurrent_positions:
            return self._error_result(symbol, side, "max_positions", entry_time, signal_data)
        
        # DRY mode simulation
        if self.config.mode == ExecutionMode.DRY:
            log.info(f"[DRY] {symbol} {side} ${position_usdt:.2f} TP={tp_pct}% SL={sl_pct}%")
            import random
            outcomes = [(OrderStatus.TP_HIT, tp_pct), (OrderStatus.SL_HIT, -sl_pct), (OrderStatus.TIMEOUT, 0)]
            status, pnl_pct = random.choice(outcomes)
            return TradeResult(
                symbol=symbol, side=side, entry_price=100.0,
                exit_price=100.0*(1+pnl_pct/100), quantity=position_usdt/100,
                pnl_usdt=position_usdt*pnl_pct/100, pnl_pct=pnl_pct,
                status=status, entry_time=entry_time, exit_time=time.time(),
                duration_sec=timeout_sec/2, fees_usdt=position_usdt*0.002, signal_data=signal_data
            )
        
        # LIVE/TESTNET execution
        try:
            current_price = await self._get_price(symbol)
            if current_price <= 0:
                return self._error_result(symbol, side, "price_error", entry_time, signal_data)
            
            quantity = self._calculate_quantity(position_usdt, current_price)
            entry_order = await self._place_market_order(symbol, side, quantity)
            if not entry_order:
                return self._error_result(symbol, side, "entry_failed", entry_time, signal_data)
            
            entry_price = float(entry_order.get("avgPrice", current_price))
            log.info(f"ENTRY: {symbol} {side} qty={quantity} @ {entry_price}")
            
            # Calculate TP/SL prices
            if side == "BUY":
                tp_price = entry_price * (1 + tp_pct / 100)
                sl_price = entry_price * (1 - sl_pct / 100)
            else:
                tp_price = entry_price * (1 - tp_pct / 100)
                sl_price = entry_price * (1 + sl_pct / 100)
            
            # Place OCO
            oco = await self._place_oco_order(symbol, side, quantity, tp_price, sl_price)
            if not oco:
                await self._emergency_close(symbol, side, quantity)
                return self._error_result(symbol, side, "oco_failed", entry_time, signal_data)
            
            self._active_positions[symbol] = {"entry": entry_price, "qty": quantity}
            
            # Monitor
            exit_price, status = await self._monitor_position(
                symbol, side, entry_price, tp_price, sl_price, timeout_sec
            )
            
            exit_time = time.time()
            pnl_pct = ((exit_price - entry_price) / entry_price * 100) if side == "BUY" else ((entry_price - exit_price) / entry_price * 100)
            pnl_usdt = position_usdt * pnl_pct / 100
            fees_usdt = position_usdt * 0.002
            
            self._daily_pnl += (pnl_usdt - fees_usdt)
            self._active_positions.pop(symbol, None)
            
            return TradeResult(
                symbol=symbol, side=side, entry_price=entry_price,
                exit_price=exit_price, quantity=quantity, pnl_usdt=pnl_usdt-fees_usdt,
                pnl_pct=pnl_pct, status=status, entry_time=entry_time,
                exit_time=exit_time, duration_sec=exit_time-entry_time,
                fees_usdt=fees_usdt, signal_data=signal_data
            )
        except Exception as e:
            log.exception(f"Trade error: {e}")
            return self._error_result(symbol, side, str(e), entry_time, signal_data)
    
    def activate_kill_switch(self):
        self._kill_switch = True
        log.critical("🛑 KILL SWITCH ACTIVATED")
    
    def deactivate_kill_switch(self):
        self._kill_switch = False
    
    async def _get_price(self, symbol: str) -> float:
        if self._client:
            ticker = await self._client.get_symbol_ticker(symbol=symbol)
            return float(ticker["price"])
        return 0.0
    
    def _calculate_quantity(self, position_usdt: float, price: float) -> float:
        return float(Decimal(str(position_usdt / price)).quantize(Decimal("0.001"), rounding=ROUND_DOWN))
    
    async def _place_market_order(self, symbol: str, side: str, quantity: float):
        if self._client:
            return await self._client.create_order(symbol=symbol, side=side, type="MARKET", quantity=quantity)
        return None
    
    async def _place_oco_order(self, symbol: str, side: str, quantity: float, tp: float, sl: float):
        if self._client:
            close_side = "SELL" if side == "BUY" else "BUY"
            return await self._client.create_oco_order(
                symbol=symbol, side=close_side, quantity=quantity,
                price=tp, stopPrice=sl, stopLimitPrice=sl, stopLimitTimeInForce="GTC"
            )
        return None
    
    async def _emergency_close(self, symbol: str, side: str, quantity: float):
        close_side = "SELL" if side == "BUY" else "BUY"
        log.warning(f"EMERGENCY CLOSE: {symbol}")
        if self._client:
            await self._client.create_order(symbol=symbol, side=close_side, type="MARKET", quantity=quantity)
    
    async def _monitor_position(self, symbol: str, side: str, entry: float, tp: float, sl: float, timeout: int):
        start = time.time()
        trailing = None
        max_profit = 0.0
        
        while (time.time() - start) < timeout:
            if self._kill_switch:
                return entry, OrderStatus.CANCELLED
            
            price = await self._get_price(symbol)
            if price <= 0:
                await asyncio.sleep(1)
                continue
            
            profit = ((price - entry) / entry * 100) if side == "BUY" else ((entry - price) / entry * 100)
            
            # Check TP/SL
            if (side == "BUY" and price >= tp) or (side == "SELL" and price <= tp):
                return price, OrderStatus.TP_HIT
            if (side == "BUY" and price <= sl) or (side == "SELL" and price >= sl):
                return price, OrderStatus.SL_HIT
            
            # Trailing stop
            if profit > max_profit:
                max_profit = profit
            if profit >= self.config.trailing_activation_pct:
                new_trail = entry * (1 + (max_profit - self.config.trailing_step_pct) / 100)
                if trailing is None or new_trail > trailing:
                    trailing = new_trail
            if trailing and side == "BUY" and price <= trailing:
                return price, OrderStatus.TRAILING_EXIT
            
            await asyncio.sleep(0.5)
        
        return await self._get_price(symbol) or entry, OrderStatus.TIMEOUT
    
    def _error_result(self, symbol: str, side: str, reason: str, entry_time: float, signal_data: Dict):
        return TradeResult(
            symbol=symbol, side=side, entry_price=0, exit_price=0, quantity=0,
            pnl_usdt=0, pnl_pct=0, status=OrderStatus.ERROR, entry_time=entry_time,
            exit_time=time.time(), duration_sec=0, fees_usdt=0,
            signal_data={**signal_data, "error": reason}
        )

if __name__ == "__main__":
    print("BINANCE OCO EXECUTOR - Use with BinanceClient")
    print("[PASS] Module loaded")
