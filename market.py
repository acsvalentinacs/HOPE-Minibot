from __future__ import annotations
from typing import Optional, List

class DataSource:
    def get_price(self, symbol: str) -> Optional[float]:
        """Текущая цена (close). Верни None, если недоступно."""
        raise NotImplementedError

    def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> Optional[List[List[float]]]:
        """
        Дай OHLCV: [[ts, o, h, l, c, v], ...] длиной <= limit.
        Верни None при недоступности.
        """
        raise NotImplementedError

class DemoDataSource(DataSource):
    """Заглушка: всегда None. Не падает; цикл просто пропускает действия."""
    def get_price(self, symbol: str) -> Optional[float]:
        return None

    def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> Optional[List[List[float]]]:
        return None
