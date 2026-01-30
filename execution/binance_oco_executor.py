# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 12:00:00 UTC
# Modified by: Claude (opus-4.5)
# Modified at: 2026-01-31 10:30:00 UTC
# Purpose: Binance OCO Executor with LAZY CLIENT INITIALIZATION
# FIX: _client was always None - now properly initializes from credentials
# === END SIGNATURE ===
"""
BINANCE OCO EXECUTOR v1.1 - LIVE TRADING (FIXED)
MARKET entry → OCO (TP+SL) → Trailing → Exit → Log

CRITICAL FIX: _client теперь инициализируется через lazy loading!
"""

import os
import time
import json
import logging
import asyncio
import hashlib
import hmac
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Tuple
from enum import Enum
from decimal import Decimal, ROUND_DOWN
from urllib.parse import urlencode
from datetime import datetime, timezone

try:
    import httpx
except ImportError:
    httpx = None

log = logging.getLogger(__name__)

# Secrets path (SSoT)
SECRETS_PATH = Path(r"C:\secrets\hope.env")

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

class SpotApiClient:
    """
    Minimal synchronous SPOT API client for Binance.
    Uses httpx for HTTP requests with proper signature.
    """

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        if not httpx:
            raise ImportError("httpx required: pip install httpx")

        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet

        if testnet:
            self.base_url = "https://testnet.binance.vision"
        else:
            self.base_url = "https://api.binance.com"

        self._http = httpx.AsyncClient(timeout=30.0)
        self._http.headers["X-MBX-APIKEY"] = api_key
        log.info(f"SpotApiClient initialized: testnet={testnet}, key={api_key[:8]}...")

    def _sign(self, params: Dict) -> Dict:
        """Add timestamp and signature to params."""
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode(),
            query.encode(),
            hashlib.sha256
        ).hexdigest()
        params["signature"] = signature
        return params

    async def get_symbol_ticker(self, symbol: str) -> Dict:
        """Get current price for symbol."""
        resp = await self._http.get(
            f"{self.base_url}/api/v3/ticker/price",
            params={"symbol": symbol}
        )
        resp.raise_for_status()
        return resp.json()

    async def create_order(self, symbol: str, side: str, type: str,
                           quantity: float, **kwargs) -> Dict:
        """Place an order."""
        params = self._sign({
            "symbol": symbol,
            "side": side,
            "type": type,
            "quantity": f"{quantity:.8f}".rstrip('0').rstrip('.'),
            **kwargs
        })
        resp = await self._http.post(f"{self.base_url}/api/v3/order", params=params)
        resp.raise_for_status()
        return resp.json()

    async def create_oco_order(self, symbol: str, side: str, quantity: float,
                                price: float, stopPrice: float,
                                stopLimitPrice: float, stopLimitTimeInForce: str = "GTC") -> Dict:
        """Place OCO order."""
        params = self._sign({
            "symbol": symbol,
            "side": side,
            "quantity": f"{quantity:.8f}".rstrip('0').rstrip('.'),
            "price": f"{price:.8f}".rstrip('0').rstrip('.'),
            "stopPrice": f"{stopPrice:.8f}".rstrip('0').rstrip('.'),
            "stopLimitPrice": f"{stopLimitPrice:.8f}".rstrip('0').rstrip('.'),
            "stopLimitTimeInForce": stopLimitTimeInForce,
        })
        resp = await self._http.post(f"{self.base_url}/api/v3/order/oco", params=params)
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        """Close HTTP client."""
        await self._http.aclose()


def _load_credentials() -> Tuple[str, str, bool]:
    """
    Load Binance credentials from secrets file.

    Returns: (api_key, api_secret, is_testnet)
    FAIL-CLOSED: raises if credentials not found
    """
    api_key = os.environ.get("BINANCE_API_KEY", "")
    api_secret = os.environ.get("BINANCE_API_SECRET", "")
    is_testnet = os.environ.get("BINANCE_TESTNET", "true").lower() == "true"

    # Load from secrets file if not in env
    if not api_key and SECRETS_PATH.exists():
        for line in SECRETS_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()
                if key == "BINANCE_API_KEY":
                    api_key = value
                elif key == "BINANCE_API_SECRET":
                    api_secret = value
                elif key == "BINANCE_TESTNET":
                    is_testnet = value.lower() == "true"

    if not api_key or not api_secret:
        raise ValueError(f"Binance credentials not found in env or {SECRETS_PATH}")

    return api_key, api_secret, is_testnet


class BinanceOCOExecutor:
    """
    Binance OCO Executor with LAZY CLIENT INITIALIZATION.

    FIX v1.1: _client now properly initializes from credentials!
    """

    def __init__(self, config: Optional[ExecutorConfig] = None):
        self.config = config or ExecutorConfig()
        self._client: Optional[SpotApiClient] = None
        self._client_initialized = False
        self._daily_pnl: float = 0.0
        self._active_positions: Dict[str, Any] = {}
        self._kill_switch: bool = False

        # Credentials (loaded lazily)
        self._api_key: str = ""
        self._api_secret: str = ""
        self._is_testnet: bool = True

    async def _ensure_client(self) -> bool:
        """
        Lazy initialization of Binance client.

        FAIL-CLOSED: Returns False if cannot initialize.
        """
        if self._client_initialized:
            return self._client is not None

        self._client_initialized = True

        if self.config.mode == ExecutionMode.DRY:
            log.info("[DRY MODE] No client needed")
            return True

        try:
            self._api_key, self._api_secret, self._is_testnet = _load_credentials()

            # Override testnet based on mode
            if self.config.mode == ExecutionMode.TESTNET:
                self._is_testnet = True
            elif self.config.mode == ExecutionMode.LIVE:
                self._is_testnet = False

            self._client = SpotApiClient(
                self._api_key,
                self._api_secret,
                testnet=self._is_testnet
            )

            log.info(f"Client initialized: mode={self.config.mode.value}, testnet={self._is_testnet}")
            return True

        except Exception as e:
            log.error(f"Failed to initialize client: {e}")
            self._client = None
            return False

    def get_active_positions(self) -> Dict[str, Any]:
        """Return active positions."""
        return self._active_positions.copy()

    def get_daily_pnl(self) -> float:
        """Return daily PnL."""
        return self._daily_pnl
    
    async def execute_trade(
        self, symbol: str, side: str, position_usdt: float,
        tp_pct: float, sl_pct: float, timeout_sec: int,
        signal_data: Optional[Dict[str, Any]] = None,
    ) -> TradeResult:
        entry_time = time.time()
        signal_data = signal_data or {}

        # === LAZY CLIENT INITIALIZATION (FIX v1.1) ===
        if not await self._ensure_client():
            log.error("FAIL-CLOSED: Client initialization failed")
            return self._error_result(symbol, side, "client_init_failed", entry_time, signal_data)

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
    import sys

    print("=" * 60)
    print("  BINANCE OCO EXECUTOR v1.1 (FIXED)")
    print("=" * 60)

    async def test_client_init():
        """Test that client actually initializes."""
        executor = BinanceOCOExecutor(ExecutorConfig(mode=ExecutionMode.TESTNET))
        success = await executor._ensure_client()

        if success and executor._client is not None:
            print("[PASS] Client initialization successful")
            print(f"  Testnet: {executor._is_testnet}")
            print(f"  API Key: {executor._api_key[:8]}...")

            # Test price fetch
            try:
                ticker = await executor._client.get_symbol_ticker("BTCUSDT")
                print(f"  BTC Price: ${float(ticker['price']):,.2f}")
                print("[PASS] API connection works")
            except Exception as e:
                print(f"[WARN] Price fetch failed: {e}")

            await executor._client.close()
            return True
        else:
            print("[FAIL] Client initialization failed!")
            return False

    # Run test
    try:
        result = asyncio.run(test_client_init())
        sys.exit(0 if result else 1)
    except Exception as e:
        print(f"[FAIL] Test error: {e}")
        sys.exit(1)
