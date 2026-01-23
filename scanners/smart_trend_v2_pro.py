#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SmartTrend PRO v2.3

Трендовый сканер для HOPE v5:
- EMA200 фильтр (только LONG по тренду)
- RSI pullback (вход при "скидке" на бычьем рынке)
- ADX фильтр флэта
- ATR-трейлинг стоп
- Volume-фильтр
- Авто-синхронизация с ядром (exec_positions_v5.json)
- Отдельный state-файл на символ: smart_scanner_state_<SYMBOL>.json
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import ccxt
import pandas as pd
import pandas_ta as ta
import yaml


__version__ = "2.3"

ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT_DIR / "config" / "smart_trend_v5.yaml"
STATE_DIR = ROOT_DIR / "state"
LOGS_DIR = ROOT_DIR / "logs"


def load_config() -> Dict[str, Any]:
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Не найден конфиг {CONFIG_FILE}")
    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class SmartTrendScannerPro:
    def __init__(self, symbol_signal: str) -> None:
        """
        symbol_signal: формат для ядра/сигналов, например 'BTCUSDT'
        """
        self.cfg = load_config()
        self.symbol_signal = symbol_signal.upper()
        self.symbol_ccxt = self._to_ccxt_symbol(self.symbol_signal)

        STATE_DIR.mkdir(parents=True, exist_ok=True)
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

        # пути
        self.state_path = STATE_DIR / f"smart_scanner_state_{self.symbol_signal}.json"
        exec_rel = self.cfg["runtime"]["exec_positions_file"]
        signals_rel = self.cfg["runtime"]["signals_file"]
        log_tpl = self.cfg["runtime"].get("log_file_template", "logs/smart_trend_{symbol}.log")

        self.exec_positions_path = ROOT_DIR / exec_rel
        self.signals_path = ROOT_DIR / signals_rel
        self.log_path = ROOT_DIR / log_tpl.format(symbol=self.symbol_signal)

        # логгер
        self.logger = logging.getLogger(f"SmartTrend_{self.symbol_signal}")
        if not self.logger.handlers:
            self._setup_logging()

        self.logger.info("=" * 70)
        self.logger.info("SmartTrend PRO v%s стартует", __version__)
        self.logger.info("Символ: %s (ccxt: %s)", self.symbol_signal, self.symbol_ccxt)

        # биржа
        self.exchange = ccxt.binance({"enableRateLimit": True})

        # состояние сканера
        self.in_position: bool = False
        self.entry_price: float = 0.0
        self.entry_time: float = 0.0
        self.trailing_sl: float = 0.0
        self.last_signal_ts: float = 0.0

        const = self.cfg["constants"]
        self.position_sync_timeout = float(const.get("position_sync_timeout_sec", 600))
        self.min_signal_interval = float(const.get("min_signal_interval_sec", 60))

        self._load_state()
        self.logger.info("Готов к опросу рынка")

    # ------------------------------------------------------------------ #
    # Вспомогательные методы
    # ------------------------------------------------------------------ #

    def _to_ccxt_symbol(self, sym: str) -> str:
        if "/" in sym:
            return sym
        if sym.upper().endswith("USDT"):
            base = sym.upper().replace("USDT", "")
            return f"{base}/USDT"
        return sym

    def _setup_logging(self) -> None:
        self.logger.setLevel(logging.INFO)

        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S",
        )

        fh = logging.FileHandler(self.log_path, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(fmt)

        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt)

        self.logger.addHandler(fh)
        self.logger.addHandler(ch)

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception as e:
            self.logger.error("Ошибка чтения state %s: %s", self.state_path, e)
            return

        age = time.time() - float(data.get("last_sync_ts", 0.0))
        if data.get("in_position") and age <= self.position_sync_timeout:
            self.in_position = True
        else:
            self.in_position = False

        self.entry_price = float(data.get("entry_price", 0.0))
        self.entry_time = float(data.get("entry_time", 0.0))
        self.trailing_sl = float(data.get("trailing_sl", 0.0))

        if self.in_position:
            self.logger.info(
                "State: в позиции, entry=%.2f, SL=%.2f (age=%.0fs)",
                self.entry_price,
                self.trailing_sl,
                age,
            )

    def _save_state(self) -> None:
        data = {
            "in_position": self.in_position,
            "entry_price": self.entry_price,
            "entry_time": self.entry_time,
            "trailing_sl": self.trailing_sl,
            "last_sync_ts": time.time(),
        }
        try:
            self.state_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            self.logger.error("Ошибка записи state %s: %s", self.state_path, e)

    def _read_core_position(self) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Читаем exec_positions_v5.json.
        Поддерживаем 2 формата:
        1) список позиций: [ {...}, {...} ]
        2) dict c ключом "positions": { "positions": [ {...}, ... ] }
        """
        p = self.exec_positions_path
        if not p.exists():
            return False, None
        try:
            raw = p.read_text(encoding="utf-8").strip()
            if not raw:
                return False, None
            data = json.loads(raw)
        except Exception as e:
            self.logger.debug("Не удалось прочитать exec_positions: %s", e)
            return False, None

        # список позиций
        if isinstance(data, list):
            for pos in data:
                if (
                    pos.get("symbol") == self.symbol_signal
                    and pos.get("state") == "OPEN"
                ):
                    return True, pos

        # dict с positions
        if isinstance(data, dict):
            for pos in data.get("positions", []):
                if (
                    pos.get("symbol") == self.symbol_signal
                    and pos.get("state") == "OPEN"
                ):
                    return True, pos

        return False, None

    def _sync_with_core(self) -> None:
        """
        Авто-синхронизация с ядром:
        - если ядро держит позицию, а мы нет → подхватываем.
        - если ядро не держит, а мы думаем, что держим → сбрасываем.
        """
        has_core_pos, pos = self._read_core_position()

        if has_core_pos and not self.in_position and pos is not None:
            self.entry_price = float(pos.get("entry_price", pos.get("price", 0.0)))
            self.entry_time = float(pos.get("opened_ts", time.time()))
            self.trailing_sl = 0.0  # SL пересчитаем по текущему ATR
            self.in_position = True
            self.logger.info(
                "SYNC: ядро имеет открытую позицию %.2f, берем под управление трейлинг",
                self.entry_price,
            )
            self._save_state()

        elif not has_core_pos and self.in_position:
            self.logger.info("SYNC: ядро позицию закрыло, сбрасываем локальное состояние")
            self.in_position = False
            self.entry_price = 0.0
            self.entry_time = 0.0
            self.trailing_sl = 0.0
            self._save_state()

    # ------------------------------------------------------------------ #
    # Данные и индикаторы
    # ------------------------------------------------------------------ #

    def _fetch_ohlcv(self) -> Optional[pd.DataFrame]:
        tf = self.cfg["timeframe"]
        limit = int(self.cfg.get("lookback_candles", 300))
        try:
            ohlcv = self.exchange.fetch_ohlcv(self.symbol_ccxt, tf, limit=limit)
            df = pd.DataFrame(
                ohlcv, columns=["ts", "open", "high", "low", "close", "volume"]
            )
            df["dt"] = pd.to_datetime(df["ts"], unit="ms")
            df.set_index("dt", inplace=True)
            df.drop(columns=["ts"], inplace=True)
            return df
        except Exception as e:
            self.logger.error("Ошибка fetch_ohlcv: %s", e)
            return None

    def _calc_indicators(self, df: pd.DataFrame) -> Optional[Dict[str, float]]:
        ind = self.cfg["indicators"]

        try:
            df["ema200"] = ta.ema(df["close"], length=ind["ema_period"])
            df["rsi"] = ta.rsi(df["close"], length=ind["rsi_period"])
            df["atr"] = ta.atr(
                df["high"],
                df["low"],
                df["close"],
                length=ind["atr_period"],
            )
            adx_df = ta.adx(
                df["high"],
                df["low"],
                df["close"],
                length=ind["adx_period"],
            )
            if adx_df is not None:
                adx_col = f"ADX_{ind['adx_period']}"
                if adx_col in adx_df.columns:
                    df["adx"] = adx_df[adx_col]
                else:
                    df["adx"] = None
            else:
                df["adx"] = None

            df["vol_sma"] = df["volume"].rolling(
                window=ind["volume_sma_window"]
            ).mean()

            last = df.iloc[-1]

            vals = [
                last["ema200"],
                last["rsi"],
                last["atr"],
                last["adx"],
                last["vol_sma"],
            ]
            if any(v is None or pd.isna(v) for v in vals):
                return None

            return {
                "close": float(last["close"]),
                "ema200": float(last["ema200"]),
                "rsi": float(last["rsi"]),
                "atr": float(last["atr"]),
                "adx": float(last["adx"]),
                "volume": float(last["volume"]),
                "vol_sma": float(last["vol_sma"]),
            }

        except Exception as e:
            self.logger.error("Ошибка расчёта индикаторов: %s", e)
            return None

    # ------------------------------------------------------------------ #
    # Логика входа / выхода
    # ------------------------------------------------------------------ #

    def _can_enter_long(self, ind: Dict[str, float]) -> bool:
        cfg_ind = self.cfg["indicators"]
        cfg_f = self.cfg["filters"]

        price = ind["close"]
        ema200 = ind["ema200"]
        rsi = ind["rsi"]
        adx = ind["adx"]
        vol = ind["volume"]
        vol_sma = ind["vol_sma"]

        if self.in_position:
            return False

        # Тренд вверх
        if price <= ema200:
            self.logger.debug("Фильтр EMA: цена %.2f <= EMA200 %.2f", price, ema200)
            return False

        # ADX: нет тренда — нет входа
        if adx < cfg_ind["adx_threshold"]:
            self.logger.debug("Фильтр ADX: %.2f < %.2f", adx, cfg_ind["adx_threshold"])
            return False

        # Объём
        if cfg_f["use_volume_filter"] and vol_sma > 0:
            min_vol = cfg_ind["min_volume_ratio"] * vol_sma
            if vol < min_vol and not (
                cfg_f["allow_low_volume_if_strong_adx"]
                and adx >= cfg_ind["adx_strong_level"]
            ):
                self.logger.debug(
                    "Фильтр объёма: vol=%.0f < min_vol=%.0f (SMA=%.0f, ADX=%.2f)",
                    vol,
                    min_vol,
                    vol_sma,
                    adx,
                )
                return False

        # RSI pullback
        if rsi >= cfg_ind["rsi_buy_threshold"]:
            self.logger.debug(
                "Фильтр RSI: %.2f >= %.2f",
                rsi,
                cfg_ind["rsi_buy_threshold"],
            )
            return False

        self.logger.info(
            "ENTRY SETUP: P=%.2f, EMA=%.2f, RSI=%.2f, ADX=%.2f, VOL=%.0f (SMA=%.0f)",
            price,
            ema200,
            rsi,
            adx,
            vol,
            vol_sma,
        )
        return True

    def _initial_sl(self, price: float, atr: float) -> float:
        mult = self.cfg["indicators"]["atr_multiplier_sl"]
        return price - mult * atr

    def _update_trailing(self, price: float, atr: float) -> bool:
        mult = self.cfg["indicators"]["atr_multiplier_sl"]
        new_sl = price - mult * atr

        if self.trailing_sl == 0.0:
            self.trailing_sl = new_sl
            self.logger.info("Инициализация SL: %.2f", self.trailing_sl)
        elif new_sl > self.trailing_sl:
            old = self.trailing_sl
            self.trailing_sl = new_sl
            self.logger.info("SL UP: %.2f → %.2f", old, self.trailing_sl)

        # выход по стопу
        if price <= self.trailing_sl:
            self.logger.warning(
                "TRAILING STOP HIT: price=%.2f <= SL=%.2f", price, self.trailing_sl
            )
            return True

        return False

    def _check_rsi_tp(self, rsi: float) -> bool:
        thr = self.cfg["indicators"]["rsi_tp_threshold"]
        if rsi > thr:
            self.logger.info("RSI TP: RSI=%.2f > %.2f", rsi, thr)
            return True
        return False

    # ------------------------------------------------------------------ #
    # Сигналы
    # ------------------------------------------------------------------ #

    def _can_send_signal_now(self) -> bool:
        now = time.time()
        if now - self.last_signal_ts < self.min_signal_interval:
            return False
        self.last_signal_ts = now
        return True

    def _send_signal(
        self,
        side: str,
        price: float,
        reason: str,
        include_risk: bool = True,
    ) -> None:
        if not self._can_send_signal_now():
            self.logger.debug("Дедуп: слишком часто сигналы, пропускаю")
            return

        signal: Dict[str, Any] = {
            "v": 1,
            "ts": time.time(),
            "symbol": self.symbol_signal,
            "side": side,
            "price": float(f"{price:.2f}"),
            "source": f"smart_trend_v2_pro_{self.symbol_signal}",
            "reason": reason,
            "signal_id": f"SMART_{side}_{self.symbol_signal}_{int(time.time())}",
            "confidence": 1.0,
        }
        if include_risk and side.upper() == "LONG":
            signal["risk_usd"] = float(self.cfg["risk"]["risk_per_trade_usd"])
        else:
            signal["risk_usd"] = 0.0

        try:
            with self.signals_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(signal, ensure_ascii=False) + "\n")
            self.logger.info(
                "Сигнал %s записан в %s @ %.2f (%s)",
                side,
                self.signals_path,
                price,
                reason,
            )
        except Exception as e:
            self.logger.error("Ошибка записи сигнала: %s", e)

    # ------------------------------------------------------------------ #
    # Основная итерация
    # ------------------------------------------------------------------ #

    def _iteration(self) -> None:
        self._sync_with_core()

        df = self._fetch_ohlcv()
        if df is None or len(df) < 50:
            self.logger.debug("Недостаточно данных, пропуск")
            return

        ind = self._calc_indicators(df)
        if ind is None:
            self.logger.debug("Индикаторы не готовы, пропуск")
            return

        price = ind["close"]

        status = (
            f"P:{price:.1f} | EMA:{ind['ema200']:.1f} | RSI:{ind['rsi']:.1f} | "
            f"ADX:{ind['adx']:.1f}"
        )

        if not self.in_position:
            self.logger.info("%s | Waiting setup...", status)
            if self._can_enter_long(ind):
                self._send_signal("LONG", price, "SMART_PRO_BUY", include_risk=True)
                self.in_position = True
                self.entry_price = price
                self.entry_time = time.time()
                self.trailing_sl = self._initial_sl(price, ind["atr"])
                self.logger.info(
                    "Открытие LONG: entry=%.2f, SL=%.2f",
                    self.entry_price,
                    self.trailing_sl,
                )
                self._save_state()
        else:
            self.logger.info(
                "%s | in_position, SL=%.2f, entry=%.2f",
                status,
                self.trailing_sl,
                self.entry_price,
            )

            if self._update_trailing(price, ind["atr"]):
                self._send_signal("CLOSE", price, "TRAILING_STOP", include_risk=False)
                self._on_position_closed("TRAILING_STOP")
                return

            if self._check_rsi_tp(ind["rsi"]):
                self._send_signal("CLOSE", price, "RSI_TP", include_risk=False)
                self._on_position_closed("RSI_TP")
                return

            self._save_state()

    def _on_position_closed(self, reason: str) -> None:
        dur_min = (time.time() - self.entry_time) / 60 if self.entry_time else 0.0
        self.logger.info(
            "POSITION CLOSED (%s). entry=%.2f, duration=%.1f мин",
            reason,
            self.entry_price,
            dur_min,
        )
        self.in_position = False
        self.entry_price = 0.0
        self.entry_time = 0.0
        self.trailing_sl = 0.0
        self._save_state()

    # ------------------------------------------------------------------ #
    # Цикл
    # ------------------------------------------------------------------ #

    def run(self) -> None:
        poll = int(self.cfg["runtime"]["poll_interval_sec"])
        self.logger.info("Основной цикл запущен, poll_interval=%d c", poll)
        while True:
            try:
                self._iteration()
            except KeyboardInterrupt:
                self.logger.info("Остановка по Ctrl+C")
                break
            except Exception as e:
                self.logger.error("Необработанное исключение: %s", e, exc_info=True)
            time.sleep(poll)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--symbol",
        default="BTCUSDT",
        help="Торгуемый символ (например BTCUSDT, ETHUSDT)",
    )
    args = parser.parse_args()

    scanner = SmartTrendScannerPro(symbol_signal=args.symbol)
    scanner.run()


if __name__ == "__main__":
    main()
