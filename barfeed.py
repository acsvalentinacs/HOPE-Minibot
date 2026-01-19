from __future__ import annotations
import pandas as pd
from datetime import timedelta
from typing import Optional

_TF_SEC = {
    "1m":60, "3m":180, "5m":300, "15m":900, "30m":1800,
    "1h":3600, "2h":7200, "4h":14400, "6h":21600, "8h":28800, "12h":43200,
    "1d":86400,
}

class BarFeeder:
    """
    Анти-lookahead: отдаёт только ЗАКРЫТЫЕ свечи.
    Кэш: держим последнюю закрытую и обновляемся, только когда пришла новая закрытая.
    """
    def __init__(self, exchange, symbol: str, timeframe: str):
        self.exchange = exchange
        self.symbol = symbol
        self.timeframe = timeframe
        self.tf_sec = _TF_SEC.get(timeframe, 3600)
        self._cache_df: Optional[pd.DataFrame] = None
        self._cache_last_closed: Optional[pd.Timestamp] = None

    def _fetch_raw_df(self, limit: int = 600) -> pd.DataFrame:
        ohlcv = self.exchange.fetch_ohlcv(self.symbol, timeframe=self.timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)  # tz-aware UTC
        return df

    def _drop_unclosed_tail(self, df: pd.DataFrame) -> pd.DataFrame:
        # ✅ современный способ получить tz-aware UTC без повторной локализации
        now = pd.Timestamp.now(tz="UTC")

        last_ts = df["ts"].iloc[-1]
        # Анти-lookahead: если свеча ещё формируется (или только что закрылась < 1 сек назад)
        if now < (last_ts + timedelta(seconds=self.tf_sec)):
            return df.iloc[:-1].copy()
        return df

    def get_closed_df(self, limit: int = 600) -> pd.DataFrame:
        df = self._drop_unclosed_tail(self._fetch_raw_df(limit=limit))
        if df.empty:
            # Если мы отбросили *единственную* свечу, вернем кэш (если он есть)
            return self._cache_df if self._cache_df is not None else df

        last_closed = df["ts"].iloc[-1]

        # первый прогон
        if self._cache_df is None:
            self._cache_df = df.copy()
            self._cache_last_closed = last_closed
            return df

        # если закрытая свеча не изменилась — возвращаем кэш
        if self._cache_last_closed is not None and last_closed == self._cache_last_closed:
            return self._cache_df

        # пришла новая закрытая свеча — мерджим
        new_rows = df[df["ts"] > self._cache_last_closed] if self._cache_last_closed is not None else df
        if not new_rows.empty:
            self._cache_df = pd.concat([self._cache_df, new_rows], ignore_index=True).drop_duplicates(subset=["ts"])
            self._cache_df = self._cache_df.iloc[-limit:].reset_index(drop=True)
            self._cache_last_closed = self._cache_df["ts"].iloc[-1]

        return self._cache_df

    def last_closed_ts(self, limit: int = 600) -> pd.Timestamp:
        df = self.get_closed_df(limit=limit)
        return df["ts"].iloc[-1]
