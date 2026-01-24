#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
minibot.replay — v3.4-backtest

Оффлайновый реплей для стратегии HOPE:
  - читает 1m CSV из data/historical/SYMBOL_1m.csv (как download_data.py)
  - использует логику, максимально близкую к run_live v3.4:
        * SMA10 > SMA40
        * gap = (close - SMA40) / SMA40 > 0.003
        * TP / SL / BE (положительные проценты)
        * дневной стоп HOPE_DAILY_STOP_USD
        * лимит на сделку HOPE_MAX_EQUITY_PER_TRADE
  - пишет сделки в state/pnl_history.csv в том же формате, что live

Запуск:
    python -m minibot.replay --reset
или:
    python minibot/replay.py --reset
"""

from __future__ import annotations

import csv
import json
import sys
import time
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd  # type: ignore

BASE_DIR = Path(__file__).resolve().parent       # ...\TradingBot\minibot
ROOT = BASE_DIR.parent                           # ...\TradingBot
DATA_DIR = ROOT / "data" / "historical"
STATE_DIR = ROOT / "state"

STATE_DIR.mkdir(parents=True, exist_ok=True)

PNL_HISTORY_FILE = STATE_DIR / "pnl_history.csv"
ENV_PATH = Path(r"C:\secrets\hope\.env")

LOGGER = logging.getLogger("REPLAY")
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [REPLAY] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ======== ENV / UTILS ========

def load_env() -> Dict[str, str]:
    env: Dict[str, str] = {}
    if ENV_PATH.exists():
        try:
            with ENV_PATH.open(encoding="utf-8-sig") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
        except Exception as e:
            LOGGER.warning("load_env: .env read error: %s", e)
    import os

    env.update(os.environ)
    return env


def f2(x: object, default: float = 0.0) -> float:
    try:
        return float(str(x).replace(",", "."))
    except Exception:
        return default


def log_pnl_history(
    tid: str,
    ts: float,
    symbol: str,
    side: str,
    qty: float,
    entry: float,
    exit_price: float,
    pnl_abs: float,
    pnl_pct: float,
    reason: str,
    mode: str,
) -> None:
    """
    Пишет строку в state/pnl_history.csv в формате, совместимом с run_live.
    """
    try:
        is_new = not PNL_HISTORY_FILE.exists()
        with PNL_HISTORY_FILE.open("a", newline="", encoding="utf-8") as f:
            fieldnames = [
                "tid",
                "ts_iso",
                "symbol",
                "side",
                "qty",
                "entry",
                "exit",
                "pnl_abs",
                "pnl_pct",
                "reason",
                "mode",
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if is_new:
                writer.writeheader()
            ts_iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            writer.writerow(
                {
                    "tid": tid,
                    "ts_iso": ts_iso,
                    "symbol": symbol,
                    "side": side,
                    "qty": f"{qty:.8f}",
                    "entry": f"{entry:.4f}",
                    "exit": f"{exit_price:.4f}",
                    "pnl_abs": f"{pnl_abs:.4f}",
                    "pnl_pct": f"{pnl_pct:.2f}",
                    "reason": reason,
                    "mode": mode,
                }
            )
    except Exception as e:
        LOGGER.warning("log_pnl_history error: %s", e)


@dataclass
class DailyState:
    date: str
    pnl: float = 0.0
    trades: int = 0
    daily_stop_hit: bool = False


@dataclass
class BTPosition:
    id: str
    symbol: str
    side: str
    qty: float
    entry_price: float
    tp_pct: float
    sl_pct: float
    be_active: bool
    ts_open: float


class BacktestEngine:
    """
    Упрощённая offline-версия логики run_live:
      - общая equity
      - дневной PnL и дневной стоп
      - одна позиция на символ
      - TP/SL/BE по тем же порогам
      - cooldown между сделками (по времени баров)
    """

    def __init__(
        self,
        start_equity: float,
        max_eq_frac: float,
        daily_stop_usd: float,
        tp_pct: float,
        sl_pct: float,
        be_trigger_pct: float,
        be_sl_pct: float,
        cooldown_sec: int,
        mode: str = "REPLAY",
        risk: str = "Backtest",
    ) -> None:
        self.mode = mode
        self.risk = risk

        self.equity_start = float(start_equity)
        self.equity = float(start_equity)

        self.max_eq_frac = float(max_eq_frac)
        self.daily_stop_usd = float(abs(daily_stop_usd))

        self.tp_pct = float(abs(tp_pct))
        self.sl_pct = float(abs(sl_pct))
        self.be_trigger_pct = float(abs(be_trigger_pct))
        self.be_sl_pct = float(abs(be_sl_pct))

        self.cooldown_sec = int(cooldown_sec)

        today = datetime.now(timezone.utc).date().isoformat()
        self.daily = DailyState(date=today)

        self.positions: Dict[str, BTPosition] = {}
        self.last_trade_ts: float = 0.0

        self.trades_total: int = 0
        self.trades_win: int = 0
        self.trades_loss: int = 0

    # ---- вспомогательные методы ----

    def _ensure_day(self, d: date) -> None:
        day_str = d.isoformat()
        if day_str != self.daily.date:
            LOGGER.info("New day in replay: %s (reset daily PnL/stop)", day_str)
            self.daily = DailyState(date=day_str)

    def _can_open_for_day(self) -> bool:
        if self.daily_stop_usd <= 0:
            return True
        return self.daily.pnl > -self.daily_stop_usd

    # ---- основной API ----

    def step_symbol(
        self,
        symbol: str,
        df: pd.DataFrame,
    ) -> None:
        """
        Прогоняет один CSV для конкретного символа.
        df обязательно должен содержать:
          - 'date' (datetime64)
          - 'close'
          - 'sma_fast'
          - 'sma_slow'
        """
        LOGGER.info("Start replay for %s, bars=%d", symbol, len(df))

        if len(df) < 50:
            LOGGER.warning("Too few bars for %s, skip", symbol)
            return

        # сортировка на всякий случай
        df = df.sort_values("date").reset_index(drop=True)

        # ключ для позиции этого символа
        pos_key = symbol

        for i in range(len(df)):
            row = df.iloc[i]
            ts_dt: datetime = row["date"].to_pydatetime()
            ts = ts_dt.replace(tzinfo=timezone.utc).timestamp()
            self._ensure_day(ts_dt.date())

            price = f2(row["close"])
            if not price or price <= 0:
                continue

            sma_fast = f2(row.get("sma_fast", 0.0))
            sma_slow = f2(row.get("sma_slow", 0.0))

            # --- сперва управляем открытой позицией ---
            pos = self.positions.get(pos_key)

            if pos:
                direction = 1.0 if pos.side.lower() == "long" else -1.0
                unreal = direction * (price - pos.entry_price) / pos.entry_price  # доля, не %

                # BE-триггер
                if (not pos.be_active) and unreal >= self.be_trigger_pct:
                    pos.be_active = True
                    pos.sl_pct = self.be_sl_pct
                    LOGGER.info(
                        "BE activated for %s at %s, sl_pct=%.4f",
                        symbol,
                        ts_dt,
                        self.be_sl_pct,
                    )

                # TP/SL решение
                reason: Optional[str] = None
                sl_this = pos.sl_pct

                if unreal >= self.tp_pct:
                    reason = "TP-REPLAY"
                elif unreal <= -sl_this:
                    reason = "SL-REPLAY" if not pos.be_active else "BE-REPLAY"

                if reason:
                    pnl_abs = direction * (price - pos.entry_price) * pos.qty
                    pnl_pct = 0.0
                    if pos.entry_price > 0:
                        pnl_pct = (
                            direction
                            * (price - pos.entry_price)
                            / pos.entry_price
                            * 100.0
                        )

                    self.equity += pnl_abs
                    self.daily.pnl += pnl_abs
                    self.daily.trades += 1
                    self.trades_total += 1
                    if pnl_abs >= 0:
                        self.trades_win += 1
                    else:
                        self.trades_loss += 1

                    tid = pos.id
                    log_pnl_history(
                        tid=tid,
                        ts=ts,
                        symbol=symbol,
                        side=pos.side,
                        qty=pos.qty,
                        entry=pos.entry_price,
                        exit_price=price,
                        pnl_abs=pnl_abs,
                        pnl_pct=pnl_pct,
                        reason=reason,
                        mode=self.mode,
                    )
                    LOGGER.info(
                        "CLOSE [%s] %s %s qty=%.6f entry=%.4f exit=%.4f pnl=%+.4f (%.2f%%) %s",
                        tid,
                        symbol,
                        pos.side,
                        pos.qty,
                        pos.entry_price,
                        price,
                        pnl_abs,
                        pnl_pct,
                        reason,
                    )
                    self.positions.pop(pos_key, None)
                    self.last_trade_ts = ts

            # --- потом проверяем вход, если позиции по этому символу нет ---
            pos = self.positions.get(pos_key)
            if pos:
                continue

            if not self._can_open_for_day():
                continue

            # cooldown в секундах
            if self.last_trade_ts and (ts - self.last_trade_ts) < self.cooldown_sec:
                continue

            # условия входа (как в run_live)
            if sma_fast <= 0 or sma_slow <= 0:
                continue

            is_uptrend = price > sma_fast > sma_slow
            gap = (price - sma_slow) / sma_slow

            if not (is_uptrend and gap > 0.003):
                continue

            # считаем бюджет
            budget = self.equity * self.max_eq_frac
            if budget <= 5.0:
                continue

            qty = budget / price
            tid = f"{int(ts)}_{symbol.replace('/', '')}"

            new_pos = BTPosition(
                id=tid,
                symbol=symbol,
                side="long",
                qty=qty,
                entry_price=price,
                tp_pct=self.tp_pct,
                sl_pct=self.sl_pct,
                be_active=False,
                ts_open=ts,
            )
            self.positions[pos_key] = new_pos
            LOGGER.info(
                "OPEN [%s] %s long qty=%.6f entry=%.4f budget≈%.2f",
                tid,
                symbol,
                qty,
                price,
                budget,
            )

    # ---- итоговая печать ----

    def summary(self) -> None:
        net_pnl = self.equity - self.equity_start
        ret_pct = (net_pnl / self.equity_start * 100.0) if self.equity_start else 0.0
        win_rate = (
            self.trades_win / self.trades_total * 100.0 if self.trades_total else 0.0
        )
        LOGGER.info("====== REPLAY SUMMARY ======")
        LOGGER.info("Start equity: %.2f", self.equity_start)
        LOGGER.info("End equity:   %.2f", self.equity)
        LOGGER.info("Net PnL:      %+.2f (%.2f%%)", net_pnl, ret_pct)
        LOGGER.info(
            "Trades: %d (wins=%d, losses=%d, win_rate=%.2f%%)",
            self.trades_total,
            self.trades_win,
            self.trades_loss,
            win_rate,
        )


def _prepare_dataframe_for_symbol(symbol: str) -> Optional[pd.DataFrame]:
    base = symbol.replace("/", "")
    path = DATA_DIR / f"{base}_1m.csv"
    if not path.exists():
        LOGGER.warning("CSV for %s not found: %s", symbol, path)
        return None

    df = pd.read_csv(path)
    if "date" not in df.columns or "close" not in df.columns:
        LOGGER.warning("CSV %s has no 'date'/'close' columns", path)
        return None

    df["date"] = pd.to_datetime(df["date"], utc=True)
    df = df.sort_values("date").reset_index(drop=True)

    # расчитываем SMA10 / SMA40
    df["sma_fast"] = df["close"].rolling(window=10, min_periods=10).mean()
    df["sma_slow"] = df["close"].rolling(window=40, min_periods=40).mean()

    return df


def main(argv: List[str]) -> None:
    env = load_env()

    # стартовый капитал для реплея
    start_equity = f2(env.get("HOPE_REPLAY_START_EQUITY"), 1000.0)
    max_eq_frac = f2(env.get("HOPE_MAX_EQUITY_PER_TRADE"), 0.10)
    daily_stop_usd = f2(env.get("HOPE_DAILY_STOP_USD"), 50.0)

    tp_pct = f2(env.get("HOPE_TP_PCT"), 0.01)
    sl_pct = f2(env.get("HOPE_SL_PCT"), 0.004)
    be_trig_pct = f2(env.get("HOPE_BE_TRIGGER_PCT"), 0.006)
    be_sl_pct = f2(env.get("HOPE_BE_SL_PCT"), 0.001)
    cooldown_sec = int(f2(env.get("HOPE_COOLDOWN_SEC"), 15.0))

    # символы
    symbols_raw = env.get("HOPE_ALLOWED_SYMBOLS") or "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT"
    if len(argv) > 1 and not argv[1].startswith("-"):
        # можно передать вручную список пар
        syms_raw = [s.strip().upper() for s in argv[1:] if s.strip()]
    else:
        syms_raw = [s.strip().upper() for s in symbols_raw.split(",") if s.strip()]

    symbols = [s.replace("USDT", "/USDT") if "/" not in s else s for s in syms_raw]

    reset = "--reset" in argv
    if reset and PNL_HISTORY_FILE.exists():
        LOGGER.info("Reset requested: removing %s", PNL_HISTORY_FILE)
        try:
            PNL_HISTORY_FILE.unlink()
        except Exception as e:
            LOGGER.warning("Cannot remove %s: %s", PNL_HISTORY_FILE, e)

    LOGGER.info("Replay config:")
    LOGGER.info("Symbols: %r", symbols)
    LOGGER.info("Start equity: %.2f", start_equity)
    LOGGER.info(
        "TP=%.2f%% SL=%.2f%% BE_TRIGGER=%.2f%% BE_SL=%.2f%%",
        tp_pct * 100.0,
        sl_pct * 100.0,
        be_trig_pct * 100.0,
        be_sl_pct * 100.0,
    )
    LOGGER.info(
        "MAX_EQUITY_PER_TRADE=%.4f DAILY_STOP_USD=%.2f COOLDOWN_SEC=%d",
        max_eq_frac,
        daily_stop_usd,
        cooldown_sec,
    )

    engine = BacktestEngine(
        start_equity=start_equity,
        max_eq_frac=max_eq_frac,
        daily_stop_usd=daily_stop_usd,
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        be_trigger_pct=be_trig_pct,
        be_sl_pct=be_sl_pct,
        cooldown_sec=cooldown_sec,
        mode="REPLAY",
        risk="Backtest",
    )

    for sym in symbols:
        df = _prepare_dataframe_for_symbol(sym)
        if df is None:
            continue
        engine.step_symbol(sym, df)

    engine.summary()
    LOGGER.info("Replay finished. You can now inspect %s or /history /stats.", PNL_HISTORY_FILE)
    

if __name__ == "__main__":
    main(sys.argv)
