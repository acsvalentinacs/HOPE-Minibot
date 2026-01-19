#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HOPE Live Engine — run_live.py
Версия: v3.6.1-dryexec

Задачи:
- DRY-ядро для HOPE (без реальных ордеров).
- Чтение сигналов из logs/turbo_signals.jsonl.
- Виртуальные позиции и PnL в logs/dry_state.json.
- Обновление logs/health.json для tg-бота HOPEminiBOT.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional

# ────────────────────────────────────────────────────────────
# Пути и базовые константы
# ────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "logs"
FLAGS_DIR = PROJECT_ROOT / "flags"

HEALTH_PATH = LOG_DIR / "health.json"
DRY_STATE_PATH = LOG_DIR / "dry_state.json"
SIGNALS_PATH = LOG_DIR / "turbo_signals.jsonl"
STOP_FLAG_PATH = FLAGS_DIR / "STOP.flag"

ENGINE_VERSION = "v3.6.1-dryexec"
ENGINE_NAME = "HOPE Live Engine"

# ────────────────────────────────────────────────────────────
# Логирование
# ────────────────────────────────────────────────────────────


def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "run_live.log"

    logger = logging.getLogger("run_live")
    logger.setLevel(logging.INFO)

    if logger.handlers:
        logger.handlers.clear()

    fmt = logging.Formatter(
        "[%(asctime)s] run_live: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    logger.info("===================================================")
    logger.info("%s %s starting up", ENGINE_NAME, ENGINE_VERSION)
    logger.info("PROJECT_ROOT=%s", PROJECT_ROOT)
    logger.info("LOG_DIR=%s", LOG_DIR)
    logger.info("FLAGS_DIR=%s", FLAGS_DIR)
    logger.info("SIGNALS_PATH=%s", SIGNALS_PATH)
    logger.info("===================================================")

    return logger


log = setup_logging()

# ────────────────────────────────────────────────────────────
# Загрузка .env
# ────────────────────────────────────────────────────────────


def load_env_from_file(env_path: Path) -> None:
    """
    Загрузить переменные окружения из C:\\secrets\\hope\\.env + os.environ.
    Формат файла: KEY=VALUE, строки с # и пустые игнорируются.
    """
    if not env_path.exists():
        log.warning("ENV файл %s не найден, продолжаем с текущим окружением", env_path)
        return

    try:
        content = env_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log.error("Не удалось прочитать %s: %s", env_path, exc)
        return

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if key not in os.environ:
            os.environ[key] = value

    log.info("ENV подгружен из %s", env_path)


def init_env() -> None:
    env_path = Path(r"C:\secrets\hope\.env")
    load_env_from_file(env_path)


# ────────────────────────────────────────────────────────────
# DRY-позиции и состояние
# ────────────────────────────────────────────────────────────


@dataclass
class DryPosition:
    id: int
    symbol: str
    side: str  # "long" или "short"
    qty: float
    entry_price: float
    ts_open: str
    ts_close: Optional[str] = None
    realized_pnl: float = 0.0
    closed: bool = False
    close_reason: str = ""


@dataclass
class DryState:
    last_position_id: int = 0
    positions: List[DryPosition] = field(default_factory=list)
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    daily_pnl: float = 0.0

    def open_position(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        ts: datetime,
    ) -> DryPosition:
        self.last_position_id += 1
        pos = DryPosition(
            id=self.last_position_id,
            symbol=symbol,
            side=side,
            qty=qty,
            entry_price=price,
            ts_open=ts.isoformat(),
        )
        self.positions.append(pos)
        return pos

    def close_positions_for_symbol(
        self,
        symbol: str,
        new_price: float,
        reason: str,
        ts: datetime,
    ) -> float:
        total_pnl = 0.0
        for pos in self.positions:
            if pos.symbol != symbol or pos.closed:
                continue
            direction = 1.0 if pos.side == "long" else -1.0
            pnl = (new_price - pos.entry_price) * pos.qty * direction
            pos.closed = True
            pos.ts_close = ts.isoformat()
            pos.close_reason = reason
            pos.realized_pnl = pnl
            total_pnl += pnl

        if total_pnl != 0.0:
            self.realized_pnl += total_pnl
            self.daily_pnl += total_pnl

        return total_pnl

    @property
    def open_positions(self) -> List[DryPosition]:
        return [p for p in self.positions if not p.closed]


def load_dry_state(path: Path) -> DryState:
    if not path.exists():
        return DryState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        positions = [DryPosition(**p) for p in raw.get("positions", [])]
        state = DryState(
            last_position_id=raw.get("last_position_id", 0),
            positions=positions,
            realized_pnl=raw.get("realized_pnl", 0.0),
            unrealized_pnl=raw.get("unrealized_pnl", 0.0),
            daily_pnl=raw.get("daily_pnl", 0.0),
        )
        log.info(
            "Загружено DRY-состояние: %d позиций, realized=%.2f, daily=%.2f",
            len(state.positions),
            state.realized_pnl,
            state.daily_pnl,
        )
        return state
    except Exception as exc:  # noqa: BLE001
        log.error("Не удалось загрузить DRY-состояние из %s: %s", path, exc)
        return DryState()


def atomic_write_json(path: Path, data: Any) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp_path, path)
    except Exception as exc:  # noqa: BLE001
        log.error("Ошибка атомарной записи JSON в %s: %s", path, exc)


def save_dry_state(path: Path, state: DryState) -> None:
    payload = {
        "last_position_id": state.last_position_id,
        "positions": [asdict(p) for p in state.positions],
        "realized_pnl": state.realized_pnl,
        "unrealized_pnl": state.unrealized_pnl,
        "daily_pnl": state.daily_pnl,
    }
    atomic_write_json(path, payload)


def compute_unrealized_pnl(state: DryState, last_prices: Dict[str, float]) -> float:
    total = 0.0
    for pos in state.open_positions:
        price = last_prices.get(pos.symbol, pos.entry_price)
        direction = 1.0 if pos.side == "long" else -1.0
        pnl = (price - pos.entry_price) * pos.qty * direction
        total += pnl
    state.unrealized_pnl = total
    return total


# ────────────────────────────────────────────────────────────
# Чтение сигналов
# ────────────────────────────────────────────────────────────


def normalize_side(raw_side: str) -> Optional[str]:
    if not raw_side:
        return None
    s = str(raw_side).lower().strip()
    if s in {"long", "buy", "bull", "up"}:
        return "long"
    if s in {"short", "sell", "bear", "down"}:
        return "short"
    return None


def extract_symbol(sig: Dict[str, Any]) -> Optional[str]:
    for key in ("symbol", "pair", "ticker", "asset"):
        v = sig.get(key)
        if v:
            return str(v)
    return None


def read_new_signals(
    path: Path,
    last_offset: int,
) -> Tuple[int, List[Dict[str, Any]]]:
    if not path.exists():
        return last_offset, []

    signals: List[Dict[str, Any]] = []

    try:
        with path.open("r", encoding="utf-8") as f:
            file_size = f.seek(0, 2)
            if last_offset > file_size:
                last_offset = 0

            f.seek(last_offset)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    sig = json.loads(line)
                    signals.append(sig)
                except json.JSONDecodeError as exc:
                    log.warning("Не удалось распарсить сигнал '%s': %s", line, exc)
            new_offset = f.tell()
    except Exception as exc:  # noqa: BLE001
        log.error("Ошибка чтения сигналов из %s: %s", path, exc)
        return last_offset, []

    if signals:
        log.info("Прочитано новых сигналов: %d", len(signals))
    return new_offset, signals


def process_signal(
    sig: Dict[str, Any],
    state: DryState,
    last_prices: Dict[str, float],
    now: datetime,
    stop_flag: bool,
) -> str:
    """
    Обработка одного сигнала.

    Возвращает статус:
    - "applied"        — открыта новая позиция (и, возможно, закрыта старая),
    - "skipped_stop"   — проигнорировано из-за STOP.flag,
    - "skipped_invalid"— некорректный формат сигнала.
    """
    if stop_flag:
        return "skipped_stop"

    symbol = extract_symbol(sig)
    raw_side = sig.get("side") or sig.get("direction") or sig.get("signal_side") or sig.get("action")
    side = normalize_side(raw_side)

    if not symbol or not side:
        return "skipped_invalid"

    price_val = sig.get("price") or sig.get("entry_price") or sig.get("close")
    try:
        price = float(price_val) if price_val is not None else 1.0
    except (TypeError, ValueError):
        price = 1.0

    risk_val = sig.get("risk_usd") or sig.get("risk") or 10.0
    try:
        risk_usd = float(risk_val)
    except (TypeError, ValueError):
        risk_usd = 10.0

    if risk_usd <= 0.0:
        risk_usd = 10.0

    qty = risk_usd / price if price > 0 else 0.0
    if qty <= 0.0:
        return "skipped_invalid"

    last_prices[symbol] = price

    closed_pnl = state.close_positions_for_symbol(
        symbol=symbol,
        new_price=price,
        reason=f"flip_to_{side}",
        ts=now,
    )

    if closed_pnl != 0.0:
        log.info(
            "Закрыты позиции по %s на flip, реализованный PnL=%.4f",
            symbol,
            closed_pnl,
        )

    pos = state.open_position(
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        ts=now,
    )
    log.info(
        "Открыта DRY-позиция #%d: %s %s qty=%.6f price=%.4f risk≈%.2f",
        pos.id,
        side,
        symbol,
        qty,
        price,
        risk_usd,
    )
    return "applied"


# ────────────────────────────────────────────────────────────
# health.json
# ────────────────────────────────────────────────────────────


def get_daily_loss_limit() -> float:
    raw = os.environ.get("HOPE_DAILY_STOP_USD") or os.environ.get("HOPE_DAILY_STOP")
    try:
        val = float(raw)
    except (TypeError, ValueError):
        val = 50.0
    return -abs(val)


def build_health_payload(
    mode: str,
    state: DryState,
    stop_flag: bool,
    daily_loss_limit: float,
    api_status: str,
    circuit_status: str,
    last_error: str,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()

    payload = {
        "version": ENGINE_VERSION,
        "mode": mode,
        "positions_count": len(state.open_positions),
        "realized_pnl": round(state.realized_pnl, 4),
        "unrealized_pnl": round(state.unrealized_pnl, 4),
        "daily_pnl": round(state.daily_pnl, 4),
        "daily_loss_limit": daily_loss_limit,
        "api_status": api_status,
        "circuit_status": circuit_status,
        "last_error": last_error,
        "stop_flag": stop_flag,
        "ts": now,
    }
    return payload


def update_health_json(
    mode: str,
    state: DryState,
    stop_flag: bool,
    daily_loss_limit: float,
    api_status: str,
    circuit_status: str,
    last_error: str,
) -> None:
    payload = build_health_payload(
        mode=mode,
        state=state,
        stop_flag=stop_flag,
        daily_loss_limit=daily_loss_limit,
        api_status=api_status,
        circuit_status=circuit_status,
        last_error=last_error,
    )
    atomic_write_json(HEALTH_PATH, payload)


# ────────────────────────────────────────────────────────────
# Основной цикл
# ────────────────────────────────────────────────────────────


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HOPE Live Engine (DRY/LIVE)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Запустить в DRY-режиме (без реальных ордеров).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    dry_run = bool(args.dry_run)

    if not dry_run:
        log.warning(
            "Запущено без --dry-run. На этой версии движка LIVE-режим не реализован, работаем как DRY."
        )
        dry_run = True

    mode = "dry"

    FLAGS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    init_env()

    daily_loss_limit = get_daily_loss_limit()
    state = load_dry_state(DRY_STATE_PATH)
    last_prices: Dict[str, float] = {}
    last_error = ""
    circuit_status = "normal"
    api_status = "dry"

    last_signal_offset = 0

    log.info("===================================================")
    log.info("HOPE Live Engine %s запущен. mode=%s", ENGINE_VERSION, mode)
    log.info("DRY-режим: все сделки виртуальные, ордера на биржу не отправляются.")
    log.info("===================================================")

    try:
        while True:
            loop_start = time.time()
            total_signals = 0
            applied = 0
            skipped_stop = 0
            skipped_invalid = 0

            try:
                stop_flag = STOP_FLAG_PATH.exists()

                last_signal_offset, signals = read_new_signals(
                    SIGNALS_PATH,
                    last_signal_offset,
                )
                total_signals = len(signals)
                now = datetime.now(timezone.utc)

                for sig in signals:
                    status = process_signal(
                        sig=sig,
                        state=state,
                        last_prices=last_prices,
                        now=now,
                        stop_flag=stop_flag,
                    )
                    if status == "applied":
                        applied += 1
                    elif status == "skipped_stop":
                        skipped_stop += 1
                    elif status == "skipped_invalid":
                        skipped_invalid += 1

                if total_signals:
                    log.info(
                        "Итерация сигналов: total=%d, applied=%d, skipped_stop=%d, skipped_invalid=%d",
                        total_signals,
                        applied,
                        skipped_stop,
                        skipped_invalid,
                    )

                compute_unrealized_pnl(state, last_prices)
                save_dry_state(DRY_STATE_PATH, state)

                update_health_json(
                    mode=mode,
                    state=state,
                    stop_flag=stop_flag,
                    daily_loss_limit=daily_loss_limit,
                    api_status=api_status,
                    circuit_status=circuit_status,
                    last_error=last_error,
                )

            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                circuit_status = "error"
                log.exception("Ошибка в основном цикле: %s", exc)
                update_health_json(
                    mode=mode,
                    state=state,
                    stop_flag=STOP_FLAG_PATH.exists(),
                    daily_loss_limit=daily_loss_limit,
                    api_status=api_status,
                    circuit_status=circuit_status,
                    last_error=last_error,
                )

            elapsed = time.time() - loop_start
            sleep_s = max(1.0, 3.0 - elapsed)
            time.sleep(sleep_s)

    except KeyboardInterrupt:
        log.info("Остановка по Ctrl+C")
    except Exception as exc:  # noqa: BLE001
        log.critical("Критическая ошибка движка: %s", exc, exc_info=True)


if __name__ == "__main__":
    main()
