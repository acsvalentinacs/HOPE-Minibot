#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HOPEminiBOT Live Engine (run_live_turbo)

Версия: 4.2.0-execsafe

Фокус:
- ExecutionEngine (ExecutionContext + idempotent-ордера) — уже используется
- Стратегия входа v4.0 (RSI + ATR 5m, R-based sizing, limit entry)
- Честный unrealized_pnl в health.json
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import ccxt  # type: ignore

# dotenv опционален
try:
    from dotenv import load_dotenv  # type: ignore
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore


# ───────────────────────── базовые пути ─────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"
EXPORTS_DIR = PROJECT_ROOT / "exports"

LOG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

ENV_PATH = Path(r"C:\secrets\hope\.env")
HEALTH_PATH = LOG_DIR / "health.json"

VERSION = "v4.2.0-execsafe"


# ───────────────────────── логирование ─────────────────────────

def setup_logging() -> None:
    log_file = LOG_DIR / "run_live_turbo.log"
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter(fmt=fmt, datefmt=datefmt))
    root.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(fmt=fmt, datefmt=datefmt))
    root.addHandler(ch)


setup_logging()
log = logging.getLogger("run_live_turbo")


# ───────────────────────── импорт execution/health ─────────────────────────

try:
    from minibot.execution_layer import (
        ExecutionContext,
        ExecutionEngine,
        TradeMode,
        ExecutionOrder,
        OrderState,
        dump_execution_order,
    )
    from minibot.health import (
        build_health_snapshot,
        write_health_snapshot,
    )
except ImportError:  # прямой запуск без пакета
    from execution_layer import (
        ExecutionContext,
        ExecutionEngine,
        TradeMode,
        ExecutionOrder,
        OrderState,
        dump_execution_order,
    )
    from health import (
        build_health_snapshot,
        write_health_snapshot,
    )


# ───────────────────────── утилиты ─────────────────────────

def load_env() -> Dict[str, str]:
    """
    Читаем env, при наличии C:/secrets/hope/.env — подгружаем, НО не меняем его.
    """
    if load_dotenv is not None and ENV_PATH.exists():
        load_dotenv(ENV_PATH)
    return dict(os.environ)


def parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    v = value.strip().lower()
    if v in ("1", "true", "yes", "y", "on"):
        return True
    if v in ("0", "false", "no", "n", "off"):
        return False
    return default


def parse_float(value: Optional[str], default: float) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:  # noqa: BLE001
        return default


def parse_symbols(value: Optional[str]) -> List[str]:
    if not value:
        return ["BTCUSDT"]
    items = [x.strip().upper() for x in value.split(",")]
    return [x for x in items if x]


# ───────────────────────── стейт позиций ─────────────────────────

@dataclass
class LivePosition:
    symbol: str
    side: str  # "long" (для спота)
    qty: float
    entry_price: float
    sl_price: float
    tp_price: float
    opened_at: datetime
    client_order_id: Optional[str] = None
    exchange_order_id: Optional[str] = None


@dataclass
class LiveState:
    positions: Dict[str, LivePosition] = field(default_factory=dict)
    realized_pnl: float = 0.0
    daily_pnl: float = 0.0
    current_day: date = field(default_factory=lambda: datetime.now(timezone.utc).date())
    daily_loss_limit: float = -50.0

    def reset_daily_if_needed(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self.current_day:
            log.info(
                "Новый торговый день: %s → %s, сбрасываем daily_pnl",
                self.current_day,
                today,
            )
            self.current_day = today
            self.daily_pnl = 0.0

    def can_open_new_trade(self) -> bool:
        if self.daily_pnl <= self.daily_loss_limit:
            log.warning(
                "Достигнут дневной лимит потерь: daily_pnl=%.2f, лимит=%.2f. "
                "Новые сделки запрещены.",
                self.daily_pnl,
                self.daily_loss_limit,
            )
            return False
        return True

    def register_closed_position(self, pos: LivePosition, close_price: float) -> float:
        """
        Обновляем realized_pnl и daily_pnl при закрытии позиции.
        """
        if pos.side != "long":
            return 0.0

        pnl = (close_price - pos.entry_price) * pos.qty
        self.realized_pnl += pnl
        self.daily_pnl += pnl
        log.info(
            "Закрыта позиция %s qty=%.6f entry=%.4f close=%.4f PnL=%.4f "
            "(realized=%.4f, daily=%.4f)",
            pos.symbol,
            pos.qty,
            pos.entry_price,
            close_price,
            pnl,
            self.realized_pnl,
            self.daily_pnl,
        )
        return pnl


# ───────────────────────── Binance / режимы ─────────────────────────

def create_exchange(env: Dict[str, str], testnet: bool) -> ccxt.Exchange:
    api_key = env.get("BINANCE_API_KEY") or env.get("API_KEY")
    api_secret = env.get("BINANCE_API_SECRET") or env.get("API_SECRET")

    params: Dict[str, object] = {
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": True,
        "options": {
            "defaultType": "spot",
        },
    }

    exchange = ccxt.binance(params)

    if testnet:
        try:
            exchange.set_sandbox_mode(True)
            log.info("Binance: включён TESTNET (sandbox_mode=True)")
        except Exception as exc:  # noqa: BLE001
            log.error("Не удалось включить sandbox_mode для Binance: %r", exc)

    return exchange


def detect_trade_mode(env: Dict[str, str]) -> "TradeMode":
    raw = (env.get("HOPE_MODE") or "").strip().upper()
    testnet_flag = parse_bool(env.get("TESTNET"), False)

    if raw == "DRY":
        return TradeMode.DRY
    if raw == "TESTNET" or testnet_flag:
        return TradeMode.TESTNET
    if raw in ("LIVE_SAFE", "SAFE"):
        return TradeMode.LIVE_SAFE
    if raw in ("LIVE_FULL", "LIVE"):
        return TradeMode.LIVE_FULL

    # по умолчанию — DRY
    return TradeMode.DRY


# ───────────────────────── индикаторы и сигналы ─────────────────────────

@dataclass
class EntrySignal:
    symbol: str
    side: str   # "long"
    price: float
    atr: float
    risk_usd: float
    sl_mult: float
    tp_mult: float


def _calc_rsi(closes: List[float], period: int) -> Optional[float]:
    """
    Классический RSI (без сглаживания) по последним period барам.
    """
    if len(closes) < period + 1:
        return None

    gains = 0.0
    losses = 0.0
    # берём последние period шагов
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses -= diff

    if losses == 0:
        return 100.0

    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def _calc_atr(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int,
) -> Optional[float]:
    """
    ATR на основе True Range.
    """
    n = len(closes)
    if n < period + 1:
        return None

    trs: List[float] = []
    for i in range(1, n):
        high = highs[i]
        low = lows[i]
        prev_close = closes[i - 1]
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        trs.append(tr)

    if len(trs) < period:
        return None

    atr = sum(trs[-period:]) / float(period)
    return atr


def generate_entry_signals(
    exchange: ccxt.Exchange,
    symbols: List[str],
    env: Dict[str, str],
    state: LiveState,
) -> List[EntrySignal]:
    """
    v4.0 "Smart Money Lite":
    - Таймфрейм: 5m
    - Фильтр по ATR: не торгуем, если волатильность слишком низкая
    - Фильтр по RSI: не входим, если актив перекуплен (RSI > HOPE_RSI_MAX)
    - R-based sizing: риск в долларах → qty через ATR*SL_mult
    """
    _ = state  # пока не используем для фильтра по корреляции и т.п.

    signals: List[EntrySignal] = []

    rsi_period = int(parse_float(env.get("HOPE_RSI_PERIOD"), 14))
    rsi_max = parse_float(env.get("HOPE_RSI_MAX"), 70.0)

    atr_period = int(parse_float(env.get("HOPE_ATR_PERIOD"), 14))
    atr_min_pct = parse_float(env.get("HOPE_ATR_MIN_PCT"), 0.003)  # 0.3%

    # R в долларах (риск на сделку)
    risk_usd_default = parse_float(env.get("HOPE_RISK_USD"), 20.0)

    sl_mult = parse_float(env.get("HOPE_SL_ATR_MULT"), 2.0)
    tp_mult = parse_float(env.get("HOPE_TP_ATR_MULT"), 3.0)

    lookback = max(atr_period + 2, rsi_period + 2, 40)

    for sym in symbols:
        try:
            ohlcv = exchange.fetch_ohlcv(sym, "5m", limit=lookback)
        except Exception as exc:  # noqa: BLE001
            log.warning("generate_entry_signals: не удалось получить OHLCV для %s: %r", sym, exc)
            continue

        if len(ohlcv) < lookback // 2:
            continue

        closes = [float(c[4]) for c in ohlcv]
        highs = [float(c[2]) for c in ohlcv]
        lows = [float(c[3]) for c in ohlcv]
        last_price = closes[-1]

        # RSI
        rsi = _calc_rsi(closes, rsi_period)
        if rsi is None:
            continue

        # ATR
        atr = _calc_atr(highs, lows, closes, atr_period)
        if atr is None or last_price <= 0:
            continue

        atr_pct = atr / last_price
        if atr_pct < atr_min_pct:
            # слишком "тихий" рынок — пропускаем
            continue

        # Не покупаем перекупленное
        if rsi > rsi_max:
            log.info("⛔ Skip %s: RSI overbought (%.1f)", sym, rsi)
            continue

        # Уже есть позиция — не открываем вторую
        if sym in state.positions:
            continue

        sig = EntrySignal(
            symbol=sym,
            side="long",
            price=last_price,
            atr=atr,
            risk_usd=risk_usd_default,
            sl_mult=sl_mult,
            tp_mult=tp_mult,
        )
        signals.append(sig)

    return signals


# ───────────────────────── основной цикл ─────────────────────────

_shutdown_flag = False


def _signal_handler(signum: int, frame: object) -> None:  # noqa: ARG001
    global _shutdown_flag
    log.info("Получен сигнал %s, запрашиваем мягкое выключение...", signum)
    _shutdown_flag = True


def main(argv: List[str] | None = None) -> None:  # noqa: ARG001
    global _shutdown_flag

    env = load_env()

    symbols = parse_symbols(env.get("HOPE_ALLOWED_SYMBOLS"))
    daily_loss_limit = parse_float(env.get("HOPE_DAILY_STOP_USD"), -50.0)
    max_equity_per_trade = parse_float(env.get("HOPE_MAX_EQUITY_PER_TRADE"), 50.0)
    limit_discount_pct = parse_float(env.get("HOPE_LIMIT_DISCOUNT_PCT"), 0.001)  # 0.1%

    mode = detect_trade_mode(env)
    testnet = mode == TradeMode.TESTNET or parse_bool(env.get("TESTNET"), False)

    log.info("Запуск run_live_turbo %s", VERSION)
    log.info(
        "Режим: %s | TESTNET=%s | symbols=%s | daily_loss_limit=%.2f | max_equity_per_trade=%.2f",
        mode.value,
        testnet,
        ",".join(symbols),
        daily_loss_limit,
        max_equity_per_trade,
    )

    exchange = create_exchange(env, testnet=testnet)
    ctx = ExecutionContext(
        exchange=exchange,
        mode=mode,
        testnet=testnet,
    )
    engine = ExecutionEngine(ctx)

    state = LiveState(
        positions={},
        realized_pnl=0.0,
        daily_pnl=0.0,
        current_day=datetime.now(timezone.utc).date(),
        daily_loss_limit=daily_loss_limit,
    )

    # сигналы ОС
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    last_health_ts = 0.0
    health_interval_sec = 10.0

    unrealized_pnl_cached = 0.0

    while not _shutdown_flag:
        loop_started = time.time()

        # 1) дневной сброс
        state.reset_daily_if_needed()

        # 2) проверка дневного стопа
        can_trade = state.can_open_new_trade()

        # 3) апдейт открытых позиций + расчёт unrealized_pnl
        unrealized_pnl_total = 0.0

        for sym, pos in list(state.positions.items()):
            try:
                ticker = exchange.fetch_ticker(sym)
            except Exception as exc:  # noqa: BLE001
                log.warning("Не удалось получить ticker для %s: %r", sym, exc)
                continue

            last_price = float(ticker.get("last") or ticker.get("close") or 0.0)
            if last_price <= 0:
                continue

            # честный нереализованный PnL
            unrealized = (last_price - pos.entry_price) * pos.qty
            unrealized_pnl_total += unrealized

            # Примитивные SL/TP (позже заменим на trailing/BE)
            if last_price <= pos.sl_price:
                order = engine.close_position(
                    symbol=sym,
                    side="sell",
                    qty=pos.qty,
                    position_id=sym,
                )
                log.info("SL close: %s", dump_execution_order(order))
                state.register_closed_position(pos, close_price=last_price)
                state.positions.pop(sym, None)
            elif last_price >= pos.tp_price:
                order = engine.close_position(
                    symbol=sym,
                    side="sell",
                    qty=pos.qty,
                    position_id=sym,
                )
                log.info("TP close: %s", dump_execution_order(order))
                state.register_closed_position(pos, close_price=last_price)
                state.positions.pop(sym, None)

        unrealized_pnl_cached = unrealized_pnl_total

        # 4) health.json (каждые N секунд)
        now_ts = time.time()
        if now_ts - last_health_ts >= health_interval_sec:
            last_health_ts = now_ts
            try:
                snapshot = build_health_snapshot(
                    ctx,
                    version=VERSION,
                    positions=list(state.positions.values()),
                    realized_pnl=state.realized_pnl,
                    unrealized_pnl=unrealized_pnl_cached,
                    daily_pnl=state.daily_pnl,
                    daily_loss_limit=state.daily_loss_limit,
                )
                write_health_snapshot(snapshot, HEALTH_PATH)
            except Exception as exc:  # noqa: BLE001
                log.error("Ошибка записи health.json: %r", exc)

        # 5) Входы (стратегия v4.0), НО не в DRY
        if can_trade and mode != TradeMode.DRY:
            try:
                entry_signals = generate_entry_signals(exchange, symbols, env, state)
            except Exception as exc:  # noqa: BLE001
                log.error("Ошибка в generate_entry_signals: %r", exc)
                entry_signals = []

            for sig in entry_signals:
                if sig.symbol in state.positions:
                    continue

                if state.daily_pnl <= state.daily_loss_limit:
                    log.warning(
                        "Дневной лимит достигнут, пропускаем сигнал %s",
                        sig.symbol,
                    )
                    continue

                # R в долларах: ограничиваем сверху max_equity_per_trade
                risk_usd = min(sig.risk_usd, max_equity_per_trade)
                if risk_usd <= 0:
                    continue

                # R-based sizing: риск = SL_mult * ATR * qty ≈ risk_usd
                qty = risk_usd / (sig.sl_mult * sig.atr)
                if qty <= 0:
                    continue

                # лимитка чуть ниже рынка
                limit_price = sig.price * (1.0 - limit_discount_pct)

                order = engine.open_position(
                    symbol=sig.symbol,
                    side="buy",
                    qty=qty,
                    limit_price=limit_price,
                    position_id=sig.symbol,
                    allow_market_fallback=True,
                )
                log.info("ENTRY order: %s", dump_execution_order(order))

                if order.state in (OrderState.FILLED, OrderState.PARTIALLY_FILLED):
                    entry_price = order.avg_price or sig.price
                    sl_price = entry_price - sig.sl_mult * sig.atr
                    tp_price = entry_price + sig.tp_mult * sig.atr
                    pos = LivePosition(
                        symbol=sig.symbol,
                        side="long",
                        qty=order.filled_qty or qty,
                        entry_price=entry_price,
                        sl_price=sl_price,
                        tp_price=tp_price,
                        opened_at=datetime.now(timezone.utc),
                        client_order_id=order.client_order_id,
                        exchange_order_id=order.exchange_order_id,
                    )
                    state.positions[sig.symbol] = pos

        # 6) пауза цикла
        loop_spent = time.time() - loop_started
        sleep_sec = max(1.0, 5.0 - loop_spent)
        time.sleep(sleep_sec)

    log.info("Главный цикл run_live_turbo завершён, выходим.")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except KeyboardInterrupt:
        log.info("Остановлено пользователем (KeyboardInterrupt).")
    except Exception as e:  # noqa: BLE001
        log.critical("КРИТИЧЕСКАЯ ОШИБКА в run_live_turbo: %r", e, exc_info=True)
        sys.exit(1)
