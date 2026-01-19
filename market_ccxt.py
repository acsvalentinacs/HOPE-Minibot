from __future__ import annotations
import os, time, math
from typing import Optional, List, Dict, Any, Tuple

try:
    import ccxt
except Exception as e:
    ccxt = None

from minibot.market import DataSource

def _now_ms() -> int:
    return int(time.time() * 1000)

class _TTL:
    def __init__(self, ttl_ms: int):
        self.ttl = ttl_ms
        self.value = None
        self.expire = 0
    def get(self):
        if _now_ms() < self.expire:
            return self.value
        return None
    def set(self, v):
        self.value = v
        self.expire = _now_ms() + self.ttl

class CcxtBinanceSource(DataSource):
    """
    CCXT Spot Binance с ограничениями рынка, прайсами/свечами с TTL и кэшем баланса.
    """
    def __init__(self, rate_limit_ms: int = 250, retries: int = 3, timeout_ms: int = 10000,
                 price_ttl_ms: int = 1500, ohlcv_ttl_ms: int = 5000, balance_ttl_ms: int = 2000):
        if ccxt is None:
            raise RuntimeError("ccxt не установлен в .venv")
        api_key = os.getenv("BINANCE_API_KEY", "") or os.getenv("API_KEY", "")
        secret  = os.getenv("BINANCE_API_SECRET", "") or os.getenv("API_SECRET", "")
        testnet = (os.getenv("TESTNET", "0") == "1")

        params = {
            "apiKey": api_key, "secret": secret,
            "enableRateLimit": True, "timeout": timeout_ms, "rateLimit": rate_limit_ms,
            "options": {"defaultType": "spot"},
        }
        if testnet:
            params["urls"] = {"api": {"public": "https://testnet.binance.vision/api",
                                      "private": "https://testnet.binance.vision/api"}}
        self.ex = ccxt.binance(params)
        self.retries = max(0, int(retries))
        self._markets_cache: Dict[str, Any] = {}
        self._load_markets_safe()
        self._price_cache: Dict[Tuple[str], _TTL] = {}
        self._ohlcv_cache: Dict[Tuple[str,str,int], _TTL] = {}
        self._price_ttl = price_ttl_ms
        self._ohlcv_ttl = ohlcv_ttl_ms

        self._balance_ttl_ms = balance_ttl_ms
        self._balance_cache: _TTL = _TTL(balance_ttl_ms)

        # Фи по умолчанию
        try:
            self.taker_fee = float(self.ex.fees.get("trading", {}).get("taker", 0.001))
        except Exception:
            self.taker_fee = 0.001

    # ---------- markets ----------
    def _load_markets_safe(self) -> None:
        for i in range(self.retries + 1):
            try:
                self._markets_cache = self.ex.load_markets()
                return
            except Exception:
                if i >= self.retries:
                    raise
                time.sleep(0.5 * (i + 1))

    @staticmethod
    def _normalize(symbol: str) -> str:
        s = symbol.strip().upper()
        return s if "/" in s else f"{s[:-4]}/{s[-4:]}" if s.endswith("USDT") else s

    @staticmethod
    def _map_tf(tf: str) -> str:
        return tf.strip()

    def get_symbol_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        return self._markets_cache.get(self._normalize(symbol))

    # ---------- cached fetch ----------
    def get_price(self, symbol: str) -> Optional[float]:
        sym = self._normalize(symbol)
        key = (sym,)
        ttl = self._price_cache.get(key)
        if ttl:
            v = ttl.get()
            if v is not None: return v
        for i in range(self.retries + 1):
            try:
                t = self.ex.fetch_ticker(sym)
                px = t.get("last") or t.get("close") or t.get("bid") or t.get("ask")
                v = float(px) if px is not None else None
                if key not in self._price_cache: self._price_cache[key] = _TTL(self._price_ttl)
                self._price_cache[key].set(v)
                return v
            except Exception:
                if i >= self.retries:
                    return None
                time.sleep(0.25 * (i + 1))
        return None

    def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> Optional[List[List[float]]]:
        sym = self._normalize(symbol)
        lim = min(1000, max(10, int(limit)))
        tf = self._map_tf(timeframe)
        key = (sym, tf, lim)
        ttl = self._ohlcv_cache.get(key)
        if ttl:
            v = ttl.get()
            if v is not None: return v
        for i in range(self.retries + 1):
            try:
                o = self.ex.fetch_ohlcv(sym, timeframe=tf, limit=lim)
                if key not in self._ohlcv_cache: self._ohlcv_cache[key] = _TTL(self._ohlcv_ttl)
                self._ohlcv_cache[key].set(o)
                return o
            except Exception:
                if i >= self.retries:
                    return None
                time.sleep(0.3 * (i + 1))
        return None

    # ---------- balance with TTL ----------
    def _fetch_balance_safe(self) -> Dict[str, Any] | None:
        cached = self._balance_cache.get()
        if cached is not None:
            return cached
        for i in range(self.retries + 1):
            try:
                b = self.ex.fetch_balance()
                self._balance_cache.set(b)
                return b
            except Exception:
                if i >= self.retries:
                    return None
                time.sleep(0.35 * (i + 1))
        return None

    def free_currency(self, currency: str) -> float:
        b = self._fetch_balance_safe() or {}
        try:
            return float(((b.get("free") or {}).get(currency) or 0.0))
        except Exception:
            return 0.0

    def free_quote_for_symbol(self, symbol: str) -> float:
        m = self.get_symbol_info(symbol) or {}
        q = (m.get("quote") or "USDT")
        return self.free_currency(q)

    def free_base_for_symbol(self, symbol: str) -> float:
        m = self.get_symbol_info(symbol) or {}
        base = (m.get("base") or None)
        if not base:
            sym = self._normalize(symbol)
            base = sym.split("/")[0]
        return self.free_currency(base)

    # ---------- ограничения/округления ----------
    def amount_to_precision(self, symbol: str, amount: float) -> float:
        try:
            return float(self.ex.amount_to_precision(self._normalize(symbol), amount))
        except Exception:
            return amount

    def price_to_precision(self, symbol: str, price: float) -> float:
        try:
            return float(self.ex.price_to_precision(self._normalize(symbol), price))
        except Exception:
            return price

    def min_amount(self, symbol: str) -> float:
        m = self.get_symbol_info(symbol) or {}
        return float(((m.get("limits") or {}).get("amount") or {}).get("min") or 0.0)

    def min_cost(self, symbol: str) -> float:
        m = self.get_symbol_info(symbol) or {}
        return float(((m.get("limits") or {}).get("cost") or {}).get("min") or 0.0)

    def taker_fee_rate(self) -> float:
        return float(self.taker_fee)

    # ---------- песочница проверки ордера ----------
    def sandbox_check_order(self, symbol: str, side: str, qty: float, price: float) -> Tuple[bool, str]:
        if qty <= 0 or price <= 0:
            return False, "qty/price <= 0"
        qty_q = self.amount_to_precision(symbol, qty)
        price_q = self.price_to_precision(symbol, price)
        if qty_q <= 0:
            return False, "qty rounds to zero"

        min_amt = self.min_amount(symbol) or 0.0
        if min_amt and qty_q < min_amt:
            return False, f"qty<{min_amt}"

        notional = qty_q * price_q
        fee = notional * self.taker_fee_rate()
        min_cost = self.min_cost(symbol) or 0.0
        if min_cost and (notional - fee) < min_cost:
            return False, f"notional<{min_cost} after fee"

        return True, f"ok qty={qty_q:.12f} price={price_q:.12f}"
