import time
import json
import logging
import ccxt
import pandas as pd
import numpy as np
from pathlib import Path

# --- НАСТРОЙКИ СТРАТЕГИИ ---
SYMBOL = "BTC/USDT"     # Пара для CCXT
SYMBOL_SIG = "BTCUSDT"  # Пара для сигнала в ядро
TIMEFRAME = "1m"        # Таймфрейм (1 минута)
RSI_PERIOD = 14         # Длина RSI
RSI_BUY = 30            # LONG, если ниже 30
RSI_SELL = 70           # CLOSE, если выше 70
RISK_USD = 15.0         # Размер сделки в долларах

# --- ПУТИ ---
ROOT_DIR = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT_DIR / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

SIGNALS_FILE = STATE_DIR / "signals_v5.jsonl"
POSITIONS_FILE = STATE_DIR / "exec_positions_v5.json"

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [RSI_SCALPER] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("RSI_Scalper")


def get_binance_data() -> pd.DataFrame:
    """Качаем свечи с Binance (Public API, ключи не нужны)."""
    exchange = ccxt.binance()
    try:
        ohlcv = exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=50)
        df = pd.DataFrame(
            ohlcv,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["close"] = df["close"].astype(float)
        return df
    except Exception as e:
        logger.error(f"Error fetching data: {e}")
        return pd.DataFrame()


def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Ручной расчёт RSI по закрытиям."""
    if df.empty or len(df) < period + 1:
        return pd.Series(dtype=float)

    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def is_in_position(symbol_search: str) -> bool:
    """Проверяем, есть ли уже открытая позиция по символу."""
    if not POSITIONS_FILE.exists():
        return False
    try:
        raw = POSITIONS_FILE.read_text(encoding="utf-8").strip() or "[]"
        data = json.loads(raw)
        if not isinstance(data, list):
            return False
        for p in data:
            if p.get("symbol") == symbol_search and p.get("state") == "OPEN":
                return True
    except Exception:
        return False
    return False


def send_signal(side: str, price: float, reason: str) -> None:
    """Пишем сигнал в JSONL файл, откуда его заберёт ядро v5."""
    side = side.upper()
    payload = {
        "v": 1,
        "ts": time.time(),
        "symbol": SYMBOL_SIG,
        "side": side,
        "risk_usd": RISK_USD if side == "LONG" else 0.0,
        "price": float(price),
        "source": "rsi_scalper_v5",
        "confidence": 1.0,
        "reason": f"RSI Strategy: {reason}",
        "signal_id": f"RSI_{int(time.time())}",
    }

    try:
        with open(SIGNALS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
        logger.info(f"🚀 SENT SIGNAL: {side} {SYMBOL_SIG} @ {price} ({reason})")
    except Exception as e:
        logger.error(f"Failed to write signal: {e}")


def run_strategy() -> None:
    logger.info(f"=== RSI SCALPER STARTED ({SYMBOL} {TIMEFRAME}) ===")

    while True:
        try:
            df = get_binance_data()
            if df.empty:
                time.sleep(5)
                continue

            df["rsi"] = calculate_rsi(df, RSI_PERIOD)
            if df["rsi"].isna().all():
                logger.warning("RSI not available yet, waiting...")
                time.sleep(5)
                continue

            current_rsi = float(df["rsi"].iloc[-1])
            current_price = float(df["close"].iloc[-1])

            has_pos = is_in_position(SYMBOL_SIG)

            logger.info(
                f"Price: {current_price} | RSI: {current_rsi:.2f} | In Position: {has_pos}"
            )

            # Логика входа: нет позиции, RSI ниже порога
            if not has_pos and current_rsi < RSI_BUY:
                logger.info(
                    f"📉 RSI OVERSOLD ({current_rsi:.2f} < {RSI_BUY}) -> LONG SIGNAL"
                )
                send_signal("LONG", current_price, f"RSI {current_rsi:.2f} < {RSI_BUY}")
                time.sleep(60)  # защита от спама

            # Логика выхода: есть позиция, RSI выше порога
            elif has_pos and current_rsi > RSI_SELL:
                logger.info(
                    f"📈 RSI OVERBOUGHT ({current_rsi:.2f} > {RSI_SELL}) -> CLOSE SIGNAL"
                )
                send_signal("CLOSE", current_price, f"RSI {current_rsi:.2f} > {RSI_SELL}")
                time.sleep(60)

        except KeyboardInterrupt:
            logger.info("Stopping RSI scanner...")
            break
        except Exception as e:
            logger.error(f"Cycle error: {e}")

        # Базовая пауза между циклами
        time.sleep(10)


if __name__ == "__main__":
    run_strategy()

