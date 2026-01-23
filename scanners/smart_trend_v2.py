#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SMART TREND SCANNER V2.4 (PRODUCTION)
Config-driven trend-following with auto-recovery, per-symbol state and scaling.
"""

import time
import json
import logging
import argparse
from pathlib import Path

import yaml
import ccxt
import pandas as pd
import pandas_ta as ta  # noqa: F401  # needed to register .ta accessor

SMART_TREND_VERSION = "2.4.0"

# Root paths
ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT_DIR / "config" / "smart_trend_v5.yaml"

STATE_DIR = ROOT_DIR / "state"
SIGNALS_FILE = STATE_DIR / "signals_v5.jsonl"
POSITIONS_FILE = STATE_DIR / "exec_positions_v5.json"


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Config not found: {CONFIG_FILE}")
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


CFG = load_config()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)


class SmartScannerPro:
    def __init__(self, symbol_binance: str, symbol_signal: str):
        self.symbol_binance = symbol_binance
        self.symbol_signal = symbol_signal

        # отдельное состояние на каждую пару
        self.state_file = STATE_DIR / f"smart_scanner_state_{symbol_signal}.json"
        self.logger = logging.getLogger(f"Smart_{symbol_signal}")

        self.exchange = ccxt.binance(
            {
                "enableRateLimit": True,
                "timeout": 10000,  # 10 секунд на сетевой запрос
            }
        )

        self.trailing_stop_price: float = 0.0
        self.in_position: bool = False

        self._load_internal_state()

    # ---------- internal state ----------

    def _load_internal_state(self) -> None:
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                self.trailing_stop_price = float(data.get("trailing_sl", 0.0)) or 0.0
            except Exception as e:
                self.logger.error(f"State Load Error: {e}")

    def _save_internal_state(self) -> None:
        data = {"trailing_sl": self.trailing_stop_price}
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:
            self.logger.error(f"State Save Error: {e}")

    # ---------- core integration ----------

    def check_core_position(self) -> bool:
        """Check exec_positions_v5.json for an OPEN position in this symbol."""
        if not POSITIONS_FILE.exists():
            return False
        try:
            content = POSITIONS_FILE.read_text(encoding="utf-8").strip()
            if not content:
                return False
            data = json.loads(content)
            if not isinstance(data, list):
                return False
            for p in data:
                if (
                    isinstance(p, dict)
                    and p.get("symbol") == self.symbol_signal
                    and p.get("state") == "OPEN"
                ):
                    return True
        except Exception as e:
            self.logger.error(f"Core Position Check Error: {e}")
        return False

    # ---------- market data & indicators ----------

    def get_market_data(self) -> pd.DataFrame:
        try:
            tf = str(CFG.get("timeframe", "15m"))
            limit = int(CFG.get("lookback_candles", 300))
            bars = self.exchange.fetch_ohlcv(self.symbol_binance, tf, limit=limit)
            df = pd.DataFrame(
                bars,
                columns=["time", "open", "high", "low", "close", "vol"],
            )

            ind = CFG.get("indicators", {})
            ema_period = int(ind.get("ema_period", 200))
            rsi_period = int(ind.get("rsi_period", 14))
            atr_period = int(ind.get("atr_period", 14))
            adx_period = int(ind.get("adx_period", 14))

            df["ema200"] = df.ta.ema(length=ema_period)
            df["rsi"] = df.ta.rsi(length=rsi_period)
            df["atr"] = df.ta.atr(length=atr_period)

            adx_df = df.ta.adx(length=adx_period)
            if adx_df is not None:
                col_name = f"ADX_{adx_period}"
                if col_name in adx_df.columns:
                    df = pd.concat([df, adx_df[col_name]], axis=1)
                    df.rename(columns={col_name: "adx"}, inplace=True)
                else:
                    df["adx"] = 0.0
            else:
                df["adx"] = 0.0

            filters = CFG.get("filters", {})
            vol_sma_period = int(filters.get("volume_sma_period", 20))
            if vol_sma_period > 1:
                df["vol_sma"] = df["vol"].rolling(window=vol_sma_period).mean()
            else:
                df["vol_sma"] = df["vol"]

            return df
        except Exception as e:
            self.logger.error(f"Data Error: {e}")
            return pd.DataFrame()

    # ---------- signal handling ----------

    @staticmethod
    def _compute_confidence(rsi: float, adx: float) -> float:
        """Heuristic confidence based on momentum & trend strength."""
        conf = 0.7
        if adx >= 30:
            conf += 0.15
        elif adx >= 25:
            conf += 0.10

        if rsi <= 40:
            conf += 0.10
        elif rsi <= 45:
            conf += 0.05

        return max(0.5, min(conf, 1.0))

    def send_signal(self, side: str, price: float, reason: str, rsi: float, adx: float):
        risk_cfg = CFG.get("risk", {})
        risk_usd = float(risk_cfg.get("risk_per_trade_usd", 15.0)) if side == "LONG" else 0.0

        confidence = self._compute_confidence(rsi, adx)
        ts = time.time()
        signal = {
            "v": 1,
            "ts": ts,
            "symbol": self.symbol_signal,
            "side": side,
            "risk_usd": risk_usd,
            "price": float(price),
            "source": f"smart_v{SMART_TREND_VERSION}_{self.symbol_signal}",
            "confidence": confidence,
            "reason": reason,
            "signal_id": f"SMART_{side}_{self.symbol_signal}_{int(ts)}",
        }

        try:
            with open(SIGNALS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(signal, ensure_ascii=False) + "\n")
            self.logger.info(
                f"📡 SIGNAL: {side} @ {price:.2f} | conf={confidence:.2f} | reason={reason}"
            )
        except Exception as e:
            self.logger.error(f"Signal Failed: {e}")

    # ---------- main loop ----------

    def run(self) -> None:
        ind = CFG.get("indicators", {})
        atr_mult = float(ind.get("atr_multiplier_sl", 2.5))
        adx_threshold = float(ind.get("adx_threshold", 20.0))
        rsi_thresh = float(ind.get("rsi_buy_threshold", 50.0))

        filters = CFG.get("filters", {})
        use_volume_filter = bool(filters.get("use_volume_filter", True))

        self.logger.info(
            f"🚀 START: {self.symbol_binance} | TF: {CFG.get('timeframe', '15m')} "
            f"| v{SMART_TREND_VERSION}"
        )

        while True:
            try:
                # 1. sync with core (auto-recovery)
                core_has_pos = self.check_core_position()

                if self.in_position and not core_has_pos:
                    self.logger.info("ℹ️ Position closed in core. Reset local state.")
                    self.in_position = False
                    self.trailing_stop_price = 0.0
                    self._save_internal_state()

                if not self.in_position and core_has_pos:
                    self.logger.info("⚠️ Detected active position in core (Cold Start). Resuming.")
                    self.in_position = True
                    # trailing_stop_price инициализируем позже, когда прочитаем рынок

                # 2. market data
                df = self.get_market_data()
                if df.empty or len(df) < 200:
                    self.logger.warning("Not enough candles, waiting...")
                    time.sleep(15)
                    continue

                last = df.iloc[-1]

                price = float(last["close"])
                ema = float(last["ema200"])
                rsi = float(last["rsi"])
                adx = float(last.get("adx", 0.0))
                atr = float(last["atr"])
                vol = float(last["vol"])
                vol_sma = float(last.get("vol_sma", vol))

                # NaN guard
                if any(pd.isna([price, ema, rsi, adx, atr, vol_sma])):
                    self.logger.warning("NaN in indicators, skipping tick.")
                    time.sleep(10)
                    continue

                status = (
                    f"P:{price:.1f}|EMA:{ema:.1f}|ADX:{adx:.1f}|RSI:{rsi:.1f}"
                    f"|ATR:{atr:.1f}|V:{vol:.0f}"
                )

                if self.in_position:
                    # -------- HOLD / TRAIL --------
                    if self.trailing_stop_price <= 0.0:
                        self.trailing_stop_price = price - atr * atr_mult
                        self._save_internal_state()
                        self.logger.warning(
                            f"🛡️ Init SL (cold start) at {self.trailing_stop_price:.2f}"
                        )

                    # trail only upwards
                    potential_sl = price - atr * atr_mult
                    if potential_sl > self.trailing_stop_price:
                        self.trailing_stop_price = potential_sl
                        self._save_internal_state()
                        self.logger.info(
                            f"⛓️ TRAIL UP → SL={self.trailing_stop_price:.2f}"
                        )

                    self.logger.info(f"{status} | 🛡️ SL:{self.trailing_stop_price:.2f}")

                    if price <= self.trailing_stop_price:
                        self.logger.info("🛑 STOP HIT → sending CLOSE")
                        self.send_signal("CLOSE", price, "TRAILING_STOP", rsi=rsi, adx=adx)
                        self.in_position = False
                        self.trailing_stop_price = 0.0
                        self._save_internal_state()
                        time.sleep(60)  # небольшая пауза после выхода

                else:
                    # -------- ENTRY LOGIC --------
                    self.logger.info(f"{status} | Wait for setup...")

                    # basic trend & direction
                    if price <= ema:
                        time.sleep(30)
                        continue

                    if adx < adx_threshold:
                        time.sleep(30)
                        continue

                    if use_volume_filter:
                        if vol < vol_sma and adx < (adx_threshold + 10):
                            time.sleep(30)
                            continue

                    # entry trigger
                    if rsi < rsi_thresh:
                        self.logger.info("🚀 ENTRY SETUP CONFIRMED → LONG")
                        self.trailing_stop_price = price - atr * atr_mult
                        self._save_internal_state()
                        self.send_signal("LONG", price, "SMART_BUY", rsi=rsi, adx=adx)
                        self.in_position = True
                        time.sleep(60)  # пауза, чтобы не спамить сигналы подряд

            except KeyboardInterrupt:
                self.logger.info("KeyboardInterrupt → exit.")
                break
            except Exception as e:
                self.logger.error(f"Loop Error: {e}")
                time.sleep(15)

            time.sleep(10)


def _symbol_to_ccxt(sym: str) -> str:
    sym = sym.upper()
    if "/" in sym:
        return sym
    if sym.endswith("USDT"):
        base = sym[:-4]
        return f"{base}/USDT"
    # fallback, no smart split
    return sym


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--symbol",
        default="BTCUSDT",
        help="Trading symbol (e.g. BTCUSDT, ETHUSDT, BTC/USDT)",
    )
    args = parser.parse_args()

    symbol_signal = args.symbol.upper()
    symbol_ccxt = _symbol_to_ccxt(symbol_signal)

    try:
        scanner = SmartScannerPro(symbol_ccxt, symbol_signal)
        scanner.run()
    except Exception as e:
        print(f"CRASH: {e}")
