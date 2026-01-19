"""Momentum/Pump detector - finds ZEN-style explosive moves."""
import time
from dataclasses import dataclass
from typing import List, Dict, Any, Optional


@dataclass
class MomentumSignal:
    symbol: str
    price_change_pct: float
    volume_ratio: float
    momentum_score: float
    ts_detected: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price_change_pct": round(self.price_change_pct, 2),
            "volume_ratio": round(self.volume_ratio, 2),
            "momentum_score": round(self.momentum_score, 1),
            "ts_detected": self.ts_detected,
        }


class MomentumDetector:
    """
    Детектор momentum/pump сигналов.
    
    Находит монеты с резким ростом цены, как ZEN (+50% за день).
    Binance ccxt не предоставляет avgVolume, поэтому используем
    только price change для основной фильтрации.
    """
    
    def __init__(
        self,
        min_price_change_pct: float = 5.0,
        min_volume_ratio: float = 1.0,  # Изменено: ccxt не даёт avgVolume
        min_score: float = 30.0,         # Изменено: более мягкий порог
    ):
        self.min_price_change_pct = min_price_change_pct
        self.min_volume_ratio = min_volume_ratio
        self.min_score = min_score

    def calculate_score(self, price_change: float, volume_usd: float) -> float:
        """
        Рассчитать momentum score.
        
        Args:
            price_change: Изменение цены в процентах (24h)
            volume_usd: Объём торгов в USD/USDT
            
        Returns:
            Score от 0 до 100
        """
        # Price component: +10% = 40 points, +25% = 100 points
        price_score = min(100, max(0, price_change * 4))
        
        # Volume component: $1M = 20 points, $10M = 60 points, $100M = 100 points
        # log10(1M) = 6, log10(100M) = 8
        import math
        if volume_usd > 0:
            vol_log = math.log10(max(1, volume_usd))
            volume_score = min(100, max(0, (vol_log - 5) * 50))  # 5 = $100K baseline
        else:
            volume_score = 0
        
        # Weighted: price важнее для momentum
        return price_score * 0.7 + volume_score * 0.3

    def detect(self, ticker: Dict[str, Any]) -> Optional[MomentumSignal]:
        """
        Проверить один тикер на momentum сигнал.
        
        Args:
            ticker: Данные тикера от ccxt
            
        Returns:
            MomentumSignal если есть сигнал, иначе None
        """
        try:
            symbol = ticker.get("symbol", "")
            if not symbol:
                return None
            
            # Пропустить стейблкоины и fiat
            base = symbol.split("/")[0] if "/" in symbol else symbol
            if base in ("USDC", "USDT", "BUSD", "DAI", "TUSD", "FDUSD", "USD1", "EUR", "USDE"):
                return None
            
            # Price change (24h percentage)
            change = float(ticker.get("percentage", 0) or 0)
            
            # Volume in quote currency (USDT)
            volume = float(ticker.get("quoteVolume", 0) or 0)
            
            # Calculate score
            score = self.calculate_score(change, volume)
            
            # Filter by thresholds
            if change < self.min_price_change_pct:
                return None
            
            if score < self.min_score:
                return None
            
            # Volume ratio = 1.0 (placeholder, ccxt doesn't provide avgVolume)
            vol_ratio = 1.0
            
            return MomentumSignal(
                symbol=symbol,
                price_change_pct=change,
                volume_ratio=vol_ratio,
                momentum_score=score,
                ts_detected=int(time.time()),
            )
            
        except Exception:
            return None

    def scan(self, tickers: Dict[str, Dict]) -> List[MomentumSignal]:
        """
        Сканировать все тикеры на momentum сигналы.
        
        Args:
            tickers: Словарь {symbol: ticker_data} от ccxt.fetch_tickers()
            
        Returns:
            Список MomentumSignal, отсортированный по score (desc)
        """
        signals = []
        
        for sym, t in tickers.items():
            # Добавить symbol в ticker если отсутствует
            if "symbol" not in t:
                t["symbol"] = sym
            
            sig = self.detect(t)
            if sig:
                signals.append(sig)
        
        # Сортировка по score (лучшие первые)
        signals.sort(key=lambda x: x.momentum_score, reverse=True)
        
        return signals