# -*- coding: utf-8 -*-
"""
BINANCE LIVE CLIENT v1.0
========================

Реальный клиент для Binance API.
Fail-closed: если ключи не найдены или невалидны — исключение.

ИСПОЛЬЗОВАНИЕ:
    from execution.binance_live_client import get_binance_client
    
    client = await get_binance_client()
    ticker = await client.get_symbol_ticker(symbol="BTCUSDT")
"""

import os
import asyncio
import logging
from typing import Optional, Dict, Any

log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

class BinanceConfig:
    """Конфигурация Binance API."""
    
    def __init__(self):
        self.api_key = os.environ.get("BINANCE_API_KEY", "")
        self.api_secret = os.environ.get("BINANCE_API_SECRET", "")
        self.testnet = os.environ.get("BINANCE_TESTNET", "1") == "1"
        
        # Endpoints
        self.base_url = "https://testnet.binancefuture.com" if self.testnet else "https://fapi.binance.com"
        self.ws_url = "wss://fstream.binance.com" if not self.testnet else "wss://stream.binancefuture.com"
    
    def validate(self) -> bool:
        """Проверить наличие ключей."""
        if not self.api_key:
            log.error("BINANCE_API_KEY not set!")
            return False
        if not self.api_secret:
            log.error("BINANCE_API_SECRET not set!")
            return False
        if len(self.api_key) < 20:
            log.error("BINANCE_API_KEY looks invalid (too short)")
            return False
        return True
    
    def __str__(self):
        return f"BinanceConfig(testnet={self.testnet}, key={'*'*8 + self.api_key[-4:] if self.api_key else 'MISSING'})"


# ══════════════════════════════════════════════════════════════════════════════
# CLIENT WRAPPER
# ══════════════════════════════════════════════════════════════════════════════

class BinanceLiveClient:
    """
    Обёртка над python-binance с fail-closed семантикой.
    """
    
    def __init__(self, config: Optional[BinanceConfig] = None):
        self.config = config or BinanceConfig()
        self._client = None
        self._connected = False
    
    async def connect(self):
        """Подключиться к Binance API."""
        if self._connected:
            return
        
        # Fail-closed: требуем валидные ключи
        if not self.config.validate():
            raise ValueError("Invalid Binance credentials - cannot connect")
        
        try:
            from binance import AsyncClient
            
            self._client = await AsyncClient.create(
                api_key=self.config.api_key,
                api_secret=self.config.api_secret,
                testnet=self.config.testnet
            )
            
            # Verify connection
            await self._client.ping()
            
            self._connected = True
            log.info(f"Connected to Binance {'TESTNET' if self.config.testnet else 'MAINNET'}")
            
        except ImportError:
            log.error("python-binance not installed! Run: pip install python-binance")
            raise
        except Exception as e:
            log.error(f"Failed to connect to Binance: {e}")
            raise
    
    async def disconnect(self):
        """Отключиться."""
        if self._client:
            await self._client.close_connection()
            self._connected = False
            log.info("Disconnected from Binance")
    
    async def get_price(self, symbol: str) -> float:
        """Получить текущую цену."""
        if not self._connected:
            await self.connect()
        
        ticker = await self._client.get_symbol_ticker(symbol=symbol)
        return float(ticker["price"])
    
    async def get_balance(self, asset: str = "USDT") -> float:
        """Получить баланс."""
        if not self._connected:
            await self.connect()
        
        account = await self._client.get_account()
        for bal in account.get("balances", []):
            if bal["asset"] == asset:
                return float(bal["free"])
        return 0.0
    
    async def place_market_order(
        self,
        symbol: str,
        side: str,  # "BUY" or "SELL"
        quantity: float
    ) -> Dict[str, Any]:
        """Разместить MARKET ордер."""
        if not self._connected:
            await self.connect()
        
        order = await self._client.create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=quantity
        )
        
        log.info(f"MARKET {side} {symbol} qty={quantity} -> orderId={order.get('orderId')}")
        return order
    
    async def place_oco_order(
        self,
        symbol: str,
        side: str,  # "SELL" for closing long, "BUY" for closing short
        quantity: float,
        take_profit_price: float,
        stop_loss_price: float
    ) -> Dict[str, Any]:
        """Разместить OCO ордер (TP + SL)."""
        if not self._connected:
            await self.connect()
        
        # OCO = One Cancels Other
        order = await self._client.create_oco_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=take_profit_price,           # Limit price (TP)
            stopPrice=stop_loss_price,         # Stop trigger
            stopLimitPrice=stop_loss_price,    # Stop limit price
            stopLimitTimeInForce="GTC"
        )
        
        log.info(f"OCO {side} {symbol} qty={quantity} TP={take_profit_price} SL={stop_loss_price}")
        return order
    
    async def cancel_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        """Отменить ордер."""
        if not self._connected:
            await self.connect()
        
        result = await self._client.cancel_order(symbol=symbol, orderId=order_id)
        log.info(f"Cancelled order {order_id} for {symbol}")
        return result
    
    async def get_open_orders(self, symbol: Optional[str] = None) -> list:
        """Получить открытые ордера."""
        if not self._connected:
            await self.connect()
        
        if symbol:
            return await self._client.get_open_orders(symbol=symbol)
        return await self._client.get_open_orders()
    
    @property
    def is_connected(self) -> bool:
        return self._connected
    
    @property
    def is_testnet(self) -> bool:
        return self.config.testnet


# ══════════════════════════════════════════════════════════════════════════════
# SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_client: Optional[BinanceLiveClient] = None

async def get_binance_client() -> BinanceLiveClient:
    """Получить единственный экземпляр клиента."""
    global _client
    if _client is None:
        _client = BinanceLiveClient()
    if not _client.is_connected:
        await _client.connect()
    return _client


# ══════════════════════════════════════════════════════════════════════════════
# DRY CLIENT (для тестов без реальных ордеров)
# ══════════════════════════════════════════════════════════════════════════════

class DryBinanceClient:
    """
    Заглушка для DRY mode. Логирует но не выполняет.
    """
    
    def __init__(self):
        self._connected = True
        self._fake_balance = 1000.0
        self._fake_prices = {}
    
    async def connect(self):
        log.info("[DRY] Fake connect to Binance")
    
    async def disconnect(self):
        log.info("[DRY] Fake disconnect")
    
    async def get_price(self, symbol: str) -> float:
        # Return cached or fetch real price
        if symbol not in self._fake_prices:
            try:
                import httpx
                async with httpx.AsyncClient() as client:
                    resp = await client.get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}")
                    self._fake_prices[symbol] = float(resp.json()["price"])
            except:
                self._fake_prices[symbol] = 100.0  # Fallback
        return self._fake_prices[symbol]
    
    async def get_balance(self, asset: str = "USDT") -> float:
        return self._fake_balance
    
    async def place_market_order(self, symbol: str, side: str, quantity: float) -> Dict[str, Any]:
        log.info(f"[DRY] Would MARKET {side} {symbol} qty={quantity}")
        return {"orderId": 0, "status": "DRY_MODE", "symbol": symbol}
    
    async def place_oco_order(self, symbol: str, side: str, quantity: float, tp: float, sl: float) -> Dict[str, Any]:
        log.info(f"[DRY] Would OCO {side} {symbol} qty={quantity} TP={tp} SL={sl}")
        return {"orderId": 0, "status": "DRY_MODE", "symbol": symbol}
    
    @property
    def is_connected(self) -> bool:
        return True
    
    @property
    def is_testnet(self) -> bool:
        return True


def get_client_for_mode(mode: str) -> BinanceLiveClient:
    """Получить клиент в зависимости от режима."""
    if mode == "DRY":
        return DryBinanceClient()
    else:
        return BinanceLiveClient()


# ══════════════════════════════════════════════════════════════════════════════
# TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import asyncio
    
    async def test():
        print("=" * 60)
        print("BINANCE LIVE CLIENT TEST")
        print("=" * 60)
        
        config = BinanceConfig()
        print(f"Config: {config}")
        print(f"Valid: {config.validate()}")
        
        # Test DRY client
        print("\n--- DRY Client Test ---")
        dry = DryBinanceClient()
        price = await dry.get_price("BTCUSDT")
        print(f"BTC price (real fetch): ${price:,.2f}")
        
        balance = await dry.get_balance()
        print(f"Fake balance: ${balance}")
        
        order = await dry.place_market_order("BTCUSDT", "BUY", 0.001)
        print(f"Dry order: {order}")
        
        print("\n[PASS] Client test")
    
    asyncio.run(test())
