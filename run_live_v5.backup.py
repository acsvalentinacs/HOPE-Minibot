#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv

try:
    from minibot.notifications_schema_v5 import (
        build_open_notification,
        build_close_notification,
        append_notification,
        build_info_notification,
    )
    from minibot.core.types import (
        EngineMode,
        EngineStatus,
        TradeSide,
        PositionInfo,
        PositionState,
    )
    from minibot.core.storage_v5 import PositionStorageV5
    from minibot.core.monitoring import HealthMonitor
    from minibot.core.exchange_client import ExchangeClient
    from minibot.core.risk_manager_v1 import RiskManagerV1
except ImportError:
    print("CRITICAL: Minibot modules not found. Run from project root.")
    raise

ROOT_DIR = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT_DIR / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

SIGNALS_FILE = STATE_DIR / "signals_v5.jsonl"
HEALTH_FILE = STATE_DIR / "health_v5.json"
STOP_FLAG_FILE = STATE_DIR / "STOP.flag"
EXEC_POS_FILE = STATE_DIR / "exec_positions_v5.json"
ENV_FILE = ROOT_DIR / ".env"

logger = logging.getLogger("run_live_v5")


class HOPEEngineV5:
    def __init__(
        self,
        mode: EngineMode,
        storage: PositionStorageV5,
        health: HealthMonitor,
        exchange: ExchangeClient,
        risk: RiskManagerV1,
        start_ts: float,
    ) -> None:
        self.mode = mode
        self.storage = storage
        self.health = health
        self.exchange = exchange
        self.risk = risk
        self.start_ts = start_ts

        self._positions = self.storage.load_positions()
        self._last_queue_size: int | None = None
        self._trading_paused: bool = False
        self._seen_ids: set[str] = set()

        logger.info("HOPEEngineV5 initialized with %d positions", len(self._positions))

    # -------------------------------------------------------------------------
    # HEALTH
    # -------------------------------------------------------------------------
    def update_health(self, now_ts: float) -> None:
        """Минимально совместимый вызов EngineStatus без лишних аргументов."""
        open_positions = [p for p in self._positions if p.state == PositionState.OPEN]

        st = EngineStatus(
            mode=self.mode,
            open_positions=open_positions,
            last_heartbeat_ts=now_ts,
        )
        # Никаких extra-полей сюда не пихаем — пусть types.py решает сам,
        # что он умеет, а что нет.
        uptime_sec = now_ts - self.start_ts
        self.health.update(st, uptime_sec, self._last_queue_size, now_ts)

    # -------------------------------------------------------------------------
    # SIGNALS
    # -------------------------------------------------------------------------
    def process_signals(self, raw_signals: list[dict]) -> None:
        # STOP.flag → пауза автоторговли
        if STOP_FLAG_FILE.exists() and not self._trading_paused:
            self._trading_paused = True
            logger.warning("⛔ PAUSED (STOP.flag detected)")
        elif not STOP_FLAG_FILE.exists() and self._trading_paused:
            self._trading_paused = False
            logger.info("✅ RESUMED (STOP.flag cleared)")

        for raw in raw_signals:
            try:
                sig_id = str(raw.get("signal_id") or f"{raw.get('ts')}_{raw.get('symbol')}")
                if sig_id in self._seen_ids:
                    continue
                self._seen_ids.add(sig_id)

                side = str(raw.get("side", "")).upper()
                symbol = raw.get("symbol")

                if not symbol:
                    logger.warning("Signal without symbol: %s", raw)
                    continue

                # Если стоп-флаг или риск-лок — пропускаем все, кроме CLOSE
                if (self._trading_paused or self.risk.is_locked) and side != "CLOSE":
                    logger.info("Skip %s %s due to pause/risk lock", side, symbol)
                    continue

                if side == "LONG":
                    self._do_long(raw)
                elif side == "CLOSE":
                    self._do_close(raw)
                else:
                    logger.debug("Unknown side in signal: %s", raw)
            except Exception as e:
                logger.exception("Signal processing error: %s", e)

    # -------------------------------------------------------------------------
    # LONG ENTRY
    # -------------------------------------------------------------------------
    def _do_long(self, raw: dict) -> None:
        symbol = raw.get("symbol")
        assert symbol, "Symbol must be present"

        # Баланс / эквити
        try:
            bal = self.exchange.fetch_balance()
            equity = float(getattr(bal, "total_usd", 0.0) or 1000.0)
        except Exception as e:
            logger.error("fetch_balance failed, using fallback equity: %s", e)
            equity = 1000.0

        # Проверка RiskManager
        curr_pos = len([p for p in self._positions if p.state == PositionState.OPEN])
        allowed, reason = self.risk.can_open_position(curr_pos, equity)

        if not allowed:
            logger.warning("🛡️ Risk Block %s: %s", symbol, reason)
            # Можно послать инфо-нотификацию
            try:
                notif = build_info_notification(
                    title="Risk Block",
                    message=f"{symbol}: {reason}",
                    mode=self.mode.value,
                )
                append_notification(notif)
            except Exception:
                pass
            return

        risk_usd = self.risk.get_risk_per_trade()

        # Цена
        price = float(raw.get("price") or 0.0)
        if price <= 0:
            price = float(self.exchange.fetch_last_price(symbol))

        # Размер позиции по фиксированному риску
        qty = risk_usd / price
        if "BTC" in symbol:
            qty = round(qty, 5)
        else:
            qty = round(qty, 1)

        try:
            logger.info("🔄 BUY %s (risk=%.2f USD, qty=%s)", symbol, risk_usd, qty)
            if self.mode == EngineMode.DRY:
                fill_price = price
                fill_qty = qty
            else:
                order = self.exchange.create_market_order(symbol, "BUY", qty)
                fill_price = float(getattr(order, "price", price))
                fill_qty = float(getattr(order, "qty", qty))

            now = time.time()
            new_pos = PositionInfo(
                symbol=symbol,
                side=TradeSide.LONG,
                qty=fill_qty,
                avg_price=fill_price,
                size_usd=fill_qty * fill_price,
                tags={"src": raw.get("source", "signal_v5")},
                state=PositionState.OPEN,
                created_at=now,
                updated_at=now,
            )
            self._positions.append(new_pos)
            self.storage.save_positions(self._positions)

            try:
                notif = build_open_notification(symbol=symbol, side="LONG", price=fill_price, qty=fill_qty, mode=self.mode.value, reason="OPEN")
                append_notification(notif)
            except Exception:
                pass

            logger.info("✅ OPEN %s @ %.2f x %s", symbol, fill_price, fill_qty)
        except Exception as e:
            logger.exception("Exec BUY error: %s", e)

    # -------------------------------------------------------------------------
    # CLOSE
    # -------------------------------------------------------------------------
    def _do_close(self, raw: dict) -> None:
        symbol = raw.get("symbol")
        if not symbol:
            logger.warning("CLOSE without symbol: %s", raw)
            return

        target = next(
            (p for p in self._positions if p.symbol == symbol and p.state == PositionState.OPEN),
            None,
        )
        if not target:
            logger.info("No open position to close for %s", symbol)
            return

        try:
            logger.info("🔄 SELL %s", symbol)
            if self.mode == EngineMode.DRY:
                exit_price = float(raw.get("price") or 0.0)
                if exit_price <= 0:
                    exit_price = float(self.exchange.fetch_last_price(symbol))
            else:
                order = self.exchange.create_market_order(symbol, "SELL", float(target.qty))
                exit_price = float(getattr(order, "price", 0.0) or self.exchange.fetch_last_price(symbol))

            pnl = (exit_price - target.avg_price) * float(target.qty)

            # Обновляем риск-менеджер (daily_pnl)
            try:
                self.risk.update_pnl(pnl)
            except Exception as e:
                logger.error("RiskManager update_pnl error: %s", e)

            rec = {
                "ts": time.time(),
                "symbol": symbol,
                "side": "CLOSE",
                "pnl": pnl,
            }
            self.storage.append_trade_record(rec)

            target.state = PositionState.CLOSED
            self._positions = [p for p in self._positions if p.state == PositionState.OPEN]
            self.storage.save_positions(self._positions)

            try:
                notif = build_close_notification(
                    symbol,
                    "LONG",
                    exit_price,
                    float(target.qty),
                    pnl,
                    self.mode.value,
                    "CLOSE",
                )
                append_notification(notif)
            except Exception:
                pass

            logger.info("💰 CLOSE %s @ %.2f  PnL=%.2f", symbol, exit_price, pnl)
        except Exception as e:
            logger.exception("Exec SELL error: %s", e)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="DRY", help="DRY or LIVE")
    args = parser.parse_args()

    # .env: приоритет — C:\secrets\hope\.env
    secrets_path = Path(r"C:\secrets\hope\.env")
    if secrets_path.exists():
        load_dotenv(str(secrets_path))
    else:
        load_dotenv(ENV_FILE)

    secrets = {
        "BINANCE_API_KEY": os.getenv("BINANCE_API_KEY"),
        "BINANCE_API_SECRET": os.getenv("BINANCE_API_SECRET"),
    }

    risk = RiskManagerV1()
    storage = PositionStorageV5(path_exec_positions=str(EXEC_POS_FILE))
    health = HealthMonitor(str(HEALTH_FILE))
    exch = ExchangeClient(EngineMode(args.mode), secrets)

    engine = HOPEEngineV5(
        mode=EngineMode(args.mode),
        storage=storage,
        health=health,
        exchange=exch,
        risk=risk,
        start_ts=time.time(),
    )

    logger.info("=== HOPE V5.1 ENGINE STARTED (mode=%s) ===", args.mode)

    try:
        while True:
            now = time.time()
            engine.update_health(now)

            if SIGNALS_FILE.exists():
                try:
                    text = SIGNALS_FILE.read_text(encoding="utf-8")
                    lines = [ln for ln in text.splitlines() if ln.strip()]
                    sigs = [json.loads(ln) for ln in lines]
                except Exception as e:
                    logger.error("Error reading/parsing signals: %s", e)
                    sigs = []

                if sigs:
                    engine.process_signals(sigs)

            time.sleep(1.0)
    except KeyboardInterrupt:
        logger.info("Graceful shutdown requested by user.")


if __name__ == "__main__":
    main()
