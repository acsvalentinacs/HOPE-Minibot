#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HOPE Engine v4 - unified live/DRY runner

Фокус этой версии:
- Execution-слой: minQty/stepSize/minNotional skeleton, slippage-guard, partial fill awareness.
- Recovery-проверка при старте (не торгуем, если на бирже висят незакрытые ордера).
- health_v4.json с полем last_error.
- Валидация базовых конфигов и лимитов.
- Пер-символьные и портфельные лимиты по риску.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import ccxt  # type: ignore
except Exception:  # pragma: no cover
    ccxt = None  # будет проверено в runtime

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "logs"
FLAGS_DIR = PROJECT_ROOT / "flags"
LOG_DIR.mkdir(parents=True, exist_ok=True)
FLAGS_DIR.mkdir(parents=True, exist_ok=True)

HEALTH_V4_PATH = LOG_DIR / "health_v4.json"
SIGNALS_PATH = LOG_DIR / "turbo_signals.jsonl"

ENGINE_VERSION = "v4.2.0-execsafe"

log = logging.getLogger("run_live_v4")
if not log.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s: %(message)s",
        datefmt="[%Y-%m-%d %H:%M:%S]",
    )


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_env_from_file(path: Path) -> None:
    """
    Загрузить переменные окружения из C:\\secrets\\hope\\.env + os.environ.
    Формат .env: KEY=VALUE, строки с # игнорируются.
    """
    try:
        if not path.exists():
            log.warning("Env file not found: %s", path)
            return
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                if not k:
                    continue
                # Не перезаписываем уже выставленные переменные
                if k not in os.environ:
                    os.environ[k] = v
    except Exception as e:  # pragma: no cover
        log.error("Failed to load .env: %s", e)


@dataclass
class HopeConfig:
    symbols: List[str]
    daily_stop_usd: float
    max_open_positions: int
    max_risk_per_symbol_usd: float
    max_portfolio_risk_usd: float
    max_slippage_pct: float
    max_equity_per_trade_usd: float

    @classmethod
    def from_env(cls) -> "HopeConfig":
        allowed = os.environ.get("HOPE_ALLOWED_SYMBOLS", "BTCUSDT,ETHUSDT")
        # принимаем и BTCUSDT, и BTC/USDT
        symbols = []
        for raw in allowed.split(","):
            s = raw.strip()
            if not s:
                continue
            if "/" not in s and s.endswith("USDT"):
                s = s[:-4] + "/USDT"
            symbols.append(s)

        def _f(name: str, default: float) -> float:
            val = os.environ.get(name)
            if not val:
                return float(default)
            try:
                return float(val.replace(",", "."))
            except ValueError:
                raise ValueError(f"Invalid float for {name}: {val!r}")

        def _i(name: str, default: int) -> int:
            val = os.environ.get(name)
            if not val:
                return int(default)
            try:
                return int(val)
            except ValueError:
                raise ValueError(f"Invalid int for {name}: {val!r}")

        daily_stop_usd = _f("HOPE_DAILY_STOP_USD", -50.0)
        max_equity_per_trade_usd = _f("HOPE_MAX_EQUITY_PER_TRADE", 50.0)
        max_open_positions = _i("HOPE_MAX_OPEN_POSITIONS", 5)
        max_risk_per_symbol_usd = _f(
            "HOPE_MAX_RISK_PER_SYMBOL_USD", max_equity_per_trade_usd
        )
        max_portfolio_risk_usd = _f(
            "HOPE_MAX_PORTFOLIO_RISK_USD", max_equity_per_trade_usd * 3
        )
        max_slippage_pct = _f("HOPE_MAX_SLIPPAGE_PCT", 0.5)

        return cls(
            symbols=symbols,
            daily_stop_usd=daily_stop_usd,
            max_open_positions=max_open_positions,
            max_risk_per_symbol_usd=max_risk_per_symbol_usd,
            max_portfolio_risk_usd=max_portfolio_risk_usd,
            max_slippage_pct=max_slippage_pct,
            max_equity_per_trade_usd=max_equity_per_trade_usd,
        )

    def validate(self, live: bool) -> None:
        errors: List[str] = []

        if not self.symbols:
            errors.append("HOPE_ALLOWED_SYMBOLS пуст, нет ни одного символа.")

        if self.daily_stop_usd >= 0:
            errors.append(
                f"HOPE_DAILY_STOP_USD должен быть отрицательным (например -50), сейчас={self.daily_stop_usd}"
            )

        if self.max_open_positions <= 0:
            errors.append(
                f"HOPE_MAX_OPEN_POSITIONS должен быть >0, сейчас={self.max_open_positions}"
            )

        if self.max_risk_per_symbol_usd <= 0:
            errors.append(
                f"HOPE_MAX_RISK_PER_SYMBOL_USD должен быть >0, сейчас={self.max_risk_per_symbol_usd}"
            )

        if self.max_portfolio_risk_usd < self.max_risk_per_symbol_usd:
            errors.append(
                "HOPE_MAX_PORTФOLIO_RISK_USD должен быть >= HOPE_MAX_RISK_PER_SYMBOL_USD"
            )

        if self.max_slippage_pct <= 0 or self.max_slippage_pct > 5:
            errors.append(
                f"HOPE_MAX_SLIPPAGE_PCT должен быть в (0;5], сейчас={self.max_slippage_pct}"
            )

        if live:
            api_key = os.environ.get("BINANCE_API_KEY") or os.environ.get("API_KEY")
            api_secret = os.environ.get("BINANCE_API_SECRET") or os.environ.get(
                "API_SECRET"
            )
            if not api_key or not api_secret:
                errors.append(
                    "Для LIVE-режима нужны BINANCE_API_KEY/BINANCE_API_SECRET (или API_KEY/API_SECRET) в .env"
                )
            if ccxt is None:
                errors.append("Библиотека ccxt не установлена (нужна для LIVE).")

        if errors:
            msg = "Config validation failed:\n- " + "\n- ".join(errors)
            raise ValueError(msg)


@dataclass
class HealthState:
    engine_version: str
    mode: str
    guards: List[str]
    daily_pnl: float
    daily_stop_usd: float
    pnl_realized: float
    pnl_unrealized: float
    api_status: str
    circuit: str
    timestamp: str
    last_error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "engine_version": self.engine_version,
            "mode": self.mode,
            "guards": self.guards,
            "pnl_daily": self.daily_pnl,
            "daily_stop_usd": self.daily_stop_usd,
            "pnl_realized": self.pnl_realized,
            "pnl_unrealized": self.pnl_unrealized,
            "api_status": self.api_status,
            "circuit": self.circuit,
            "timestamp": self.timestamp,
            "last_error": self.last_error,
        }


def atomic_write_json(path: Path, payload: dict, logger: logging.Logger) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
    except Exception as e:  # pragma: no cover
        logger.error("Atomic write error (%s): %s", path, e)


@dataclass
class Position:
    symbol: str
    side: str  # "long"
    amount: float
    entry_price: float
    realized_pnl: float = 0.0

    @property
    def notional(self) -> float:
        return self.amount * self.entry_price


class ExecutionEngine:
    """
    Слой работы с биржей / DRY-симуляцией.
    Здесь живут:
    - minQty/stepSize/minNotional проверки,
    - slippage-guard,
    - базовая обработка partial fill / canceled+partial,
    - recovery-проверка при старте.
    """

    def __init__(self, config: HopeConfig, live: bool):
        self.config = config
        self.live = live
        self.exchange = None
        self.markets: Dict[str, dict] = {}
        self.last_error: Optional[str] = None

        if self.live:
            assert ccxt is not None, "ccxt is required in LIVE mode"
            api_key = os.environ.get("BINANCE_API_KEY") or os.environ.get("API_KEY")
            api_secret = os.environ.get("BINANCE_API_SECRET") or os.environ.get(
                "API_SECRET"
            )
            self.exchange = ccxt.binance(
                {
                    "apiKey": api_key,
                    "secret": api_secret,
                    "enableRateLimit": True,
                }
            )
            testnet_flag = (os.environ.get("TESTNET") or "").lower() in (
                "1",
                "true",
                "yes",
            )
            if testnet_flag:
                self.exchange.set_sandbox_mode(True)  # type: ignore[union-attr]

            self.markets = self.exchange.load_markets()  # type: ignore[union-attr]
        else:
            self.exchange = None
            self.markets = {}

    # --- helpers ---

    def _get_market(self, symbol: str) -> Optional[dict]:
        return self.markets.get(symbol)

    def normalize_amount_price(
        self, symbol: str, amount: float, price: float
    ) -> Tuple[float, float]:
        """
        Учитываем precision и minQty/minNotional из markets.
        Если данных нет — делаем только безопасное округление.
        """
        market = self._get_market(symbol)
        amt = float(amount)
        pr = float(price)

        if market:
            prec = market.get("precision", {})
            amount_prec = prec.get("amount")
            price_prec = prec.get("price")
            if amount_prec is not None:
                step = 10 ** (-amount_prec)
                amt = math.floor(amt / step) * step
            if price_prec is not None:
                step = 10 ** (-price_prec)
                pr = round(pr / step) * step

            limits = market.get("limits", {})
            min_amount = (limits.get("amount") or {}).get("min")
            min_cost = (limits.get("cost") or {}).get("min")

            if min_amount is not None and amt < float(min_amount):
                raise ValueError(
                    f"Amount {amt} < min_amount {min_amount} for {symbol} (minQty guard)"
                )
            notional = amt * pr
            # Если min_cost неизвестен, используем консервативный минимум 10 USDT
            min_cost_eff = float(min_cost) if min_cost is not None else 10.0
            if notional < min_cost_eff:
                raise ValueError(
                    f"Notional {notional:.4f} < minNotional {min_cost_eff} for {symbol}"
                )
        else:
            # Без информации по рынку — только грубая защита
            notional = amt * pr
            if notional < 10.0:
                raise ValueError(
                    f"Notional {notional:.4f} < 10.0 (fallback minNotional guard) for {symbol}"
                )

        if amt <= 0 or pr <= 0:
            raise ValueError(f"Non-positive amount/price after normalize: {amt}, {pr}")

        return amt, pr

    # --- slippage / orders / recovery ---

    def get_ticker_price(self, symbol: str, default: float) -> float:
        if not self.live or self.exchange is None:
            return default
        try:
            ticker = self.exchange.fetch_ticker(symbol)  # type: ignore[union-attr]
            price = ticker.get("last") or ticker.get("close")
            if price:
                return float(price)
        except Exception as e:  # pragma: no cover
            self.last_error = f"fetch_ticker failed: {e}"
            log.warning("fetch_ticker failed for %s: %s", symbol, e)
        return default

    def slippage_ok(self, signal_price: float, market_price: float) -> bool:
        if signal_price <= 0 or market_price <= 0:
            return True
        diff_pct = abs(market_price - signal_price) / signal_price * 100.0
        if diff_pct > self.config.max_slippage_pct:
            self.last_error = (
                f"Slippage {diff_pct:.2f}% > limit {self.config.max_slippage_pct:.2f}%"
            )
            log.warning(
                "Slippage guard: signal=%.8f market=%.8f diff=%.2f%% > %.2f%%",
                signal_price,
                market_price,
                diff_pct,
                self.config.max_slippage_pct,
            )
            return False
        return True

    def place_market_buy(
        self, symbol: str, amount: float, price_hint: float
    ) -> Tuple[float, float, str]:
        """
        Вернуть (filled, avg_price, status).
        DRY: считаем, что ордер исполнился полностью по price_hint.
        LIVE: создаём market-ордер и отслеживаем partial/closed/canceled.
        """
        if not self.live or self.exchange is None:
            # DRY: считаем полный fill
            return amount, price_hint, "dry"

        try:
            order = self.exchange.create_order(  # type: ignore[union-attr]
                symbol, "market", "buy", amount
            )
        except Exception as e:  # pragma: no cover
            self.last_error = f"create_order failed: {e}"
            log.error("create_order failed: %s", e)
            return 0.0, price_hint, "rejected"

        order_id = order.get("id")
        status = order.get("status") or "open"
        filled = float(order.get("filled") or 0.0)
        avg_price = float(order.get("average") or price_hint)
        start_price = price_hint

        # Простой цикл наблюдения за ордером
        while status in ("open", None):
            time.sleep(1.0)
            try:
                cur = self.exchange.fetch_order(order_id, symbol)  # type: ignore[union-attr]
                status = cur.get("status") or status
                filled = float(cur.get("filled") or filled)
                avg_price = float(cur.get("average") or avg_price)
            except Exception as e:  # pragma: no cover
                self.last_error = f"fetch_order failed: {e}"
                log.warning("fetch_order failed for %s: %s", order_id, e)
                break

            # Slippage-guard: если цена сильно убежала и ещё ничего не залито — отменяем
            if filled == 0:
                mprice = self.get_ticker_price(symbol, start_price)
                if not self.slippage_ok(start_price, mprice):
                    try:
                        self.exchange.cancel_order(order_id, symbol)  # type: ignore[union-attr]
                        status = "canceled"
                        log.warning(
                            "Order %s canceled by slippage guard (symbol=%s)",
                            order_id,
                            symbol,
                        )
                    except Exception as e:  # pragma: no cover
                        self.last_error = f"cancel_order failed after slippage: {e}"
                        log.error("cancel_order failed: %s", e)
                    break

        # Финальный статус
        if status == "canceled" and filled > 0:
            log.warning(
                "Order %s canceled with partial fill: filled=%.8f avg=%.8f",
                order_id,
                filled,
                avg_price,
            )
        elif status != "closed" and status not in ("dry", "rejected"):
            log.warning(
                "Order %s finished with status=%s filled=%.8f avg=%.8f",
                order_id,
                status,
                filled,
                avg_price,
            )

        return filled, avg_price, status

    def recovery_check(self) -> Tuple[bool, Optional[str]]:
        """
        Проверка при старте LIVE:
        - если на бирже висят открытые ордера, НЕ торгуем (возвращаем False, msg).
        Это честный и безопасный минимум: биржа первична.
        """
        if not self.live or self.exchange is None:
            return True, None
        try:
            open_orders = self.exchange.fetch_open_orders()  # type: ignore[union-attr]
        except Exception as e:  # pragma: no cover
            msg = f"Recovery: fetch_open_orders failed: {e}"
            self.last_error = msg
            log.error(msg)
            return False, msg

        if open_orders:
            msg = (
                f"Recovery: обнаружено {len(open_orders)} открытых ордеров на бирже. "
                "Нужна ручная проверка и закрытие/синхронизация перед автоторговлей."
            )
            self.last_error = msg
            log.error(msg)
            return False, msg

        return True, None


class HOPEEngineV4:
    def __init__(self, guards: List[str], live: bool):
        self.live = live
        self.guards = guards
        self.last_error: Optional[str] = None

        env_path = Path("C:/secrets/hope/.env")
        load_env_from_file(env_path)

        self.config = HopeConfig.from_env()
        # Валидация конфигов
        self.config.validate(live=self.live)

        self.exec = ExecutionEngine(self.config, live=self.live)

        self.positions: Dict[str, Position] = {}
        self.realized_pnl: float = 0.0
        self.unrealized_pnl: float = 0.0
        self.daily_pnl: float = 0.0
        self._last_health_ts: float = 0.0
        self._health_interval_sec: float = 5.0

        self._signals_fp = None
        self._signals_pos = 0

    # --- positions / limits ---

    def _portfolio_notional(self) -> float:
        return sum(p.notional for p in self.positions.values())

    def _symbol_notional(self, symbol: str) -> float:
        p = self.positions.get(symbol)
        return p.notional if p else 0.0

    def _can_open_position(self, symbol: str, risk_usd: float) -> bool:
        # Макс. количество позиций
        if len(self.positions) >= self.config.max_open_positions:
            self.last_error = (
                f"max_open_positions reached ({self.config.max_open_positions}), skip {symbol}"
            )
            log.warning(self.last_error)
            return False

        # Пер-символьный лимит
        current_symbol = self._symbol_notional(symbol)
        if current_symbol + risk_usd > self.config.max_risk_per_symbol_usd:
            self.last_error = (
                f"Per-symbol cap exceeded for {symbol}: "
                f"{current_symbol + risk_usd:.2f} > {self.config.max_risk_per_symbol_usd:.2f}"
            )
            log.warning(self.last_error)
            return False

        # Портфельный лимит
        current_portfolio = self._portfolio_notional()
        if current_portfolio + risk_usd > self.config.max_portfolio_risk_usd:
            self.last_error = (
                f"Portfolio cap exceeded: {current_portfolio + risk_usd:.2f} > "
                f"{self.config.max_portfolio_risk_usd:.2f}"
            )
            log.warning(self.last_error)
            return False

        return True

    # --- signals ---

    def _open_signals_tail(self) -> None:
        if self._signals_fp is not None:
            return
        if not SIGNALS_PATH.exists():
            SIGNALS_PATH.touch()
        self._signals_fp = SIGNALS_PATH.open("r", encoding="utf-8")
        # Начинаем читать с конца файла
        self._signals_fp.seek(0, os.SEEK_END)
        self._signals_pos = self._signals_fp.tell()

    def _iter_new_signals(self):
        self._open_signals_tail()
        assert self._signals_fp is not None
        self._signals_fp.seek(self._signals_pos)
        for line in self._signals_fp:
            self._signals_pos = self._signals_fp.tell()
            raw = line.strip()
            if not raw:
                continue
            # Убрать BOM, если есть
            if raw.startswith("\ufeff"):
                raw = raw.lstrip("\ufeff")
            try:
                data = json.loads(raw)
            except Exception as e:
                self.last_error = f"Bad JSON in turbo_signals: {e}"
                log.warning("Bad JSON in turbo_signals: %r (%s)", raw, e)
                continue
            yield data

    # --- health ---

    def _write_health(self, api_status: str, circuit: str) -> None:
        now_iso = _now_utc_iso()
        hs = HealthState(
            engine_version=ENGINE_VERSION,
            mode="live" if self.live else "dry",
            guards=self.guards,
            daily_pnl=self.daily_pnl,
            daily_stop_usd=self.config.daily_stop_usd,
            pnl_realized=self.realized_pnl,
            pnl_unrealized=self.unrealized_pnl,
            api_status=api_status,
            circuit=circuit,
            timestamp=now_iso,
            last_error=self.last_error or self.exec.last_error,
        )
        atomic_write_json(HEALTH_V4_PATH, hs.to_dict(), log)

    # --- core loop ---

    def _handle_signal(self, sig: dict) -> None:
        symbol_raw = sig.get("symbol")
        price = sig.get("price")
        if not symbol_raw or price is None:
            self.last_error = f"Signal without symbol/price: {sig!r}"
            log.warning("Signal without symbol/price: %r", sig)
            return

        # Нормализуем символ
        symbol = symbol_raw
        if "/" not in symbol and symbol.endswith("USDT"):
            symbol = symbol[:-4] + "/USDT"

        # Рискуем не больше max_equity_per_trade_usd
        risk_usd = self.config.max_equity_per_trade_usd

        if not self._can_open_position(symbol, risk_usd):
            return

        # Slippage guard (сравниваем сигнал vs реальная цена)
        market_price = self.exec.get_ticker_price(symbol, float(price))
        if not self.exec.slippage_ok(float(price), market_price):
            self.last_error = self.exec.last_error
            return

        # Считаем количество и проверяем minQty/minNotional
        amount_raw = risk_usd / market_price
        try:
            amount, _ = self.exec.normalize_amount_price(symbol, amount_raw, market_price)
        except ValueError as e:
            self.last_error = str(e)
            log.warning("Normalize failed for %s: %s", symbol, e)
            return

        log.info(
            "💡 Signal: %s Price=%.4f Risk=%.2f → Qty=%.8f",
            symbol,
            market_price,
            risk_usd,
            amount,
        )

        filled, avg_price, status = self.exec.place_market_buy(
            symbol, amount, market_price
        )

        if filled <= 0:
            log.warning("No fill for %s (status=%s)", symbol, status)
            return

        # Открываем / увеличиваем позицию
        pos = self.positions.get(symbol)
        if pos is None:
            pos = Position(symbol=symbol, side="long", amount=filled, entry_price=avg_price)
            self.positions[symbol] = pos
        else:
            # пересчёт средней цены
            total_notional = pos.notional + filled * avg_price
            total_amount = pos.amount + filled
            pos.entry_price = total_notional / total_amount
            pos.amount = total_amount

        log.info(
            "✅ LIVE entry: %s amount=%.8f avg=%.8f (status=%s)",
            symbol,
            filled,
            avg_price,
            status,
        )

    def _loop_dry(self) -> None:
        log.info("HOPE Engine v4 started in DRY mode (no real orders).")
        while True:
            try:
                for sig in self._iter_new_signals():
                    # В DRY просто логируем сигнал + псевдо-сделку
                    symbol = sig.get("symbol")
                    price = sig.get("price")
                    log.info(
                        "💡 DRY signal: %s price=%s source=%s",
                        symbol,
                        price,
                        sig.get("source"),
                    )
                now = time.time()
                if now - self._last_health_ts > self._health_interval_sec:
                    self._last_health_ts = now
                    self._write_health(api_status="dry", circuit="normal")
                time.sleep(1.0)
            except KeyboardInterrupt:
                log.info("DRY loop stopped by user.")
                break
            except Exception as e:
                self.last_error = f"DRY loop error: {e}"
                log.exception("DRY loop error: %s", e)
                self._write_health(api_status="error", circuit="error")
                time.sleep(5.0)

    def _loop_live(self) -> None:
        log.info("HOPE Engine v4 started in LIVE mode.")

        # Recovery check
        ok, msg = self.exec.recovery_check()
        if not ok:
            self.last_error = msg
            log.error("Recovery check failed, trading disabled.")
            # цикл только health-пинга без торговли
            while True:
                try:
                    now = time.time()
                    if now - self._last_health_ts > self._health_interval_sec:
                        self._last_health_ts = now
                        self._write_health(api_status="recovery_block", circuit="recovery_block")
                    time.sleep(5.0)
                except KeyboardInterrupt:
                    log.info("LIVE recovery-block loop stopped by user.")
                    break
                except Exception as e:
                    self.last_error = f"LIVE recovery-block loop error: {e}"
                    log.exception("LIVE recovery-block loop error: %s", e)
                    time.sleep(5.0)
            return

        while True:
            try:
                # STOP.flag — глобальный стоп
                stop_flag = (FLAGS_DIR / "STOP.flag").exists()
                circuit = "normal" if not stop_flag else "stopped"

                if stop_flag:
                    # При активном STOP.flag просто обновляем health и не торгуем
                    now = time.time()
                    if now - self._last_health_ts > self._health_interval_sec:
                        self._last_health_ts = now
                        self._write_health(api_status="ok", circuit=circuit)
                    time.sleep(2.0)
                    continue

                # Читаем новые сигналы и открываем позиции
                for sig in self._iter_new_signals():
                    self._handle_signal(sig)

                # TODO: сюда же позже добавим проверку TP/SL и закрытие позиций

                now = time.time()
                if now - self._last_health_ts > self._health_interval_sec:
                    self._last_health_ts = now
                    self._write_health(api_status="ok", circuit=circuit)

                time.sleep(1.0)

            except KeyboardInterrupt:
                log.info("LIVE loop stopped by user.")
                break
            except Exception as e:
                self.last_error = f"LIVE loop error: {e}"
                log.exception("LIVE loop error: %s", e)
                self._write_health(api_status="error", circuit="error")
                time.sleep(5.0)

    def run(self) -> None:
        log.info("============================================================")
        log.info("HOPE Engine %s", ENGINE_VERSION)
        log.info("Mode: %s", "LIVE" if self.live else "DRY")
        log.info("Guards: %s", ", ".join(self.guards) if self.guards else "None")
        log.info("============================================================")

        if self.live:
            self._loop_live()
        else:
            self._loop_dry()


def parse_args(argv: List[str]):
    import argparse

    parser = argparse.ArgumentParser(description="HOPE Engine v4 unified runner")
    parser.add_argument(
        "--live",
        action="store_true",
        help="LIVE mode (real orders). By default DRY.",
    )
    parser.add_argument(
        "--with-guards",
        action="store_true",
        help="Enable default guards set (MarketCache, SpreadGuard, CircuitBreaker, Idempotency, RSI, ATR).",
    )
    parser.add_argument(
        "--market-cache",
        action="store_true",
        help="(legacy flag, сохраняем для совместимости; MarketCache включён в with-guards).",
    )
    parser.add_argument(
        "--spread-guard",
        action="store_true",
        help="(legacy flag, сохраняем для совместимости; SpreadGuard включён в with-guards).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv or sys.argv[1:])

    guards: List[str] = []
    if args.with_guards:
        guards = [
            "MarketCache",
            "SpreadGuard",
            "CircuitBreaker",
            "Idempotency",
            "RSI",
            "ATR",
        ]
    else:
        # Нас минимум интересует, что это v4
        guards = []

    # LIVE confirmation
    if args.live:
        log.warning("⚠️ LIVE MODE - Real money!")
        log.warning("Press Ctrl+C within 5 seconds to cancel...")
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            log.info("Cancelled")
            return

    engine = HOPEEngineV4(guards=guards, live=args.live)
    engine.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.critical("ОШИБКА ДВИЖКА v4: %s", e, exc_info=True)
        # На всякий случай пишем health с ошибкой
        hs = HealthState(
            engine_version=ENGINE_VERSION,
            mode="live",  # режим уже неважен, главное — зафиксировать ошибку
            guards=[],
            daily_pnl=0.0,
            daily_stop_usd=0.0,
            pnl_realized=0.0,
            pnl_unrealized=0.0,
            api_status="fatal",
            circuit="fatal",
            timestamp=_now_utc_iso(),
            last_error=str(e),
        )
        try:
            atomic_write_json(HEALTH_V4_PATH, hs.to_dict(), log)
        except Exception:
            pass
        raise
