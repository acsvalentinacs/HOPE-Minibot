#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HOPE Minibot v5 - run_live_v5.py

Главный оркестратор ExecutionEngine v5:

- читает config/execution_v5.yaml (если есть, иначе дефолт DRY);
- поднимает StorageManager, RiskManager, ExecutionEngine;
- использует SignalQueueHandler для очереди сигналов;
- пишет heartbeat в state/health_v5.json (для tg-бота и watchdog).

Режимы:
- DRY     — без реальных ордеров, только симуляция;
- TESTNET — Binance testnet;
- LIVE    — реальная торговля (ОПАСНО, только после полного теста).
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None

from minibot.core.types import EngineMode, TradeSide
from minibot.core.risk_manager import RiskConfig, RiskManager
from minibot.core.storage import StorageManager
from minibot.execution.execution_engine import ExecutionEngine
from minibot.signal_queue_handler import SignalQueueHandler


LOG = logging.getLogger("minibot.run_live_v5")


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------


def load_config(path: Path) -> Dict[str, Any]:
    """Загрузить execution_v5.yaml или вернуть дефолтный конфиг."""
    if yaml is None:
        raise RuntimeError(
            "Модуль PyYAML не установлен. Установи его:\n"
            "  pip install pyyaml"
        )

    if not path.exists():
        print(f"Конфиг {path} не найден — использую дефолтные настройки для DRY-режима")
        return {
            "engine": {
                "mode": "DRY",
                "state_dir": "state",
                "logs_dir": "logs",
                "heartbeat_file": "state/health_v5.json",
                "signals_file": "state/signals_v5.jsonl",
                "positions_file": "state/exec_positions_v5.json",
                "orders_file": "state/orders_v5.json",
                "trade_journal_file": "logs/trades_v5.jsonl",
                "heartbeat_interval_sec": 5,
                "log_level": "INFO",
                "log_files": {
                    "engine": "logs/engine_v5.log",
                },
            },
            "risk": {
                "max_daily_loss_usd": -50.0,
                "max_positions": 3,
                "max_position_usd": 100.0,
                "max_portfolio_load_pct": 80.0,
            },
            "signals": {
                "file": "state/signals_v5.jsonl",
                "signal_ttl_sec": 300,
                "min_risk_usd": 10.0,
                "max_risk_usd": 500.0,
                "allowed_sources": ["turbo_scanner", "test_manual", "tg_manual"],
            },
            "monitoring": {
                "health_update_interval_sec": 5.0,
                "signal_poll_interval_ms": 200.0,
            },
            "dry_run": {
                "initial_balance_usd": 1000.0,
            },
        }

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return data


def setup_logging(engine_log_path: Path, log_level: str = "INFO") -> None:
    """Консоль + файл engine_v5.log."""
    engine_log_path.parent.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, log_level.upper(), logging.INFO)

    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    logging.basicConfig(level=level, format=fmt)

    file_handler = logging.FileHandler(engine_log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(fmt))
    root = logging.getLogger()
    root.addHandler(file_handler)


def _old_write_health(
    heartbeat_file: Path,
    engine: ExecutionEngine,
    *,
    now: float,
    start_ts: float,
) -> None:
    """Сериализовать EngineStatus в health_v5.json."""
    heartbeat_file.parent.mkdir(parents=True, exist_ok=True)

    status = engine.status

    try:
        status.last_update = now  # type: ignore[attr-defined]
    except Exception:
        pass

    try:
        status.uptime_sec = max(0.0, now - start_ts)  # type: ignore[attr-defined]
    except Exception:
        pass

    if hasattr(status, "to_json"):
        payload = status.to_json()  # type: ignore[attr-defined]
    else:
        payload = asdict(status)

    payload.setdefault("process", {})
    payload["process"].update(
        {
            "pid": getattr(engine, "pid", None) or None,
            "module": "minibot.run_live_v5",
        }
    )

    tmp = heartbeat_file.with_suffix(heartbeat_file.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(heartbeat_file)


# ---------------------------------------------------------------------------
# RiskManager
# ---------------------------------------------------------------------------


def build_risk_manager(cfg: Dict[str, Any]) -> RiskManager:
    """Собрать RiskManager на основе секции risk.

    Ожидаемый RiskConfig:
        - daily_stop_usd
        - max_open_positions
        - max_risk_per_trade_usd
        - max_portfolio_load_pct
    """
    risk_cfg_raw = cfg.get("risk", {}) or {}

    max_open_positions = int(
        risk_cfg_raw.get(
            "max_open_positions",
            risk_cfg_raw.get("max_positions", 3),
        )
    )

    risk_cfg = RiskConfig(
        daily_stop_usd=float(risk_cfg_raw.get("max_daily_loss_usd", -50.0)),
        max_open_positions=max_open_positions,
        max_risk_per_trade_usd=float(risk_cfg_raw.get("max_position_usd", 100.0)),
        max_portfolio_load_pct=float(risk_cfg_raw.get("max_portfolio_load_pct", 80.0)),
    )

    rm = RiskManager(risk_cfg)
    LOG.info(
        "RiskManager initialized: daily_stop=%s, max_open_positions=%s, max_risk_per_trade=%s",
        risk_cfg.daily_stop_usd,
        risk_cfg.max_open_positions,
        risk_cfg.max_risk_per_trade_usd,
    )
    return rm


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="HOPE Minibot ExecutionEngine v5")
    parser.add_argument(
        "--mode",
        choices=["DRY", "TESTNET", "LIVE"],
        default=None,
        help="Режим работы движка (DRY/TESTNET/LIVE). Если не указан — берётся из конфига.",
    )
    parser.add_argument(
        "--config",
        default="config/execution_v5.yaml",
        help="Путь к файлу конфигурации execution_v5.yaml",
    )

    args = parser.parse_args(argv)

    config_path = Path(args.config)
    cfg = load_config(config_path)

    engine_cfg = cfg.get("engine", {}) or {}
    monitoring_cfg = cfg.get("monitoring", {}) or {}
    signals_cfg = cfg.get("signals", {}) or {}
    dry_cfg = cfg.get("dry_run", {}) or {}

    mode_str = args.mode or engine_cfg.get("mode", "DRY").upper()
    try:
        mode = EngineMode(mode_str)
    except ValueError:
        LOG.warning("Неизвестный режим '%s', падаю в DRY", mode_str)
        mode = EngineMode.DRY

    state_dir = Path(engine_cfg.get("state_dir", "state"))
    logs_dir = Path(engine_cfg.get("logs_dir", "logs"))
    state_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    heartbeat_file = Path(engine_cfg.get("heartbeat_file", "state/health_v5.json"))
    signals_file = Path(
        engine_cfg.get("signals_file", signals_cfg.get("file", "state/signals_v5.jsonl"))
    )

    log_files_cfg = engine_cfg.get("log_files", {}) or {}
    engine_log_path = Path(log_files_cfg.get("engine", "logs/engine_v5.log"))
    log_level = engine_cfg.get("log_level", "INFO")
    setup_logging(engine_log_path, log_level=log_level)

    LOG.info("=== HOPE ExecutionEngine v5 стартует ===")
    LOG.info("Mode: %s", mode.value)
    LOG.info("Config: %s", config_path)

    initial_equity = float(dry_cfg.get("initial_balance_usd", 1000.0))
    equity_usd = initial_equity

    storage = StorageManager(state_dir=state_dir, logs_dir=logs_dir)
    risk_manager = build_risk_manager(cfg)

    # ВАЖНО: конструктор ExecutionEngine — как в твоём execution_sandbox,
    # без state_dir/logs_dir/initial_equity параметрами.
    engine = ExecutionEngine(
        mode=mode,
        storage=storage,
        risk_manager=risk_manager,
    )

    signal_queue = SignalQueueHandler(signals_file)

    health_interval = float(monitoring_cfg.get("health_update_interval_sec", 5.0))
    if health_interval <= 0:
        health_interval = 5.0

    signal_poll_interval_ms = float(monitoring_cfg.get("signal_poll_interval_ms", 200.0))
    if signal_poll_interval_ms <= 0:
        signal_poll_interval_ms = 200.0

    signal_ttl_sec = float(signals_cfg.get("signal_ttl_sec", 300.0))
    min_risk_usd = float(signals_cfg.get("min_risk_usd", 0.0))
    max_risk_usd = float(signals_cfg.get("max_risk_usd", 1e9))
    allowed_sources = set(signals_cfg.get("allowed_sources", []) or [])

    LOG.info("State dir: %s", state_dir)
    LOG.info("Logs dir: %s", logs_dir)
    LOG.info("Signals file: %s", signals_file)
    LOG.info("Heartbeat file: %s", heartbeat_file)
    LOG.info("Initial equity (DRY): %.2f", initial_equity)

    start_ts = time.time()
    last_health_ts = 0.0
    running = True

    def handle_sigterm(signum, frame):  # type: ignore[override]
        nonlocal running
        LOG.warning("Получен сигнал %s — останавливаюсь...", signum)
        running = False

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    LOG.info("Запуск главного цикла ExecutionEngine v5 (mode=%s)...", mode.value)

    try:
        while running:
            now = time.time()

            # 1) Читаем новые сигналы
            new_signals = signal_queue.read_new_signals(max_signals=100)

            if new_signals:
                LOG.info("Получено %d новых сигналов из очереди", len(new_signals))

            for sig in new_signals:
                age = now - sig.ts
                if age > signal_ttl_sec:
                    LOG.info(
                        "Пропускаю протухший сигнал %s %s (age=%.1fs > %.1fs)",
                        sig.symbol,
                        sig.side.value,
                        age,
                        signal_ttl_sec,
                    )
                    continue

                if allowed_sources and sig.source not in allowed_sources:
                    LOG.info(
                        "Пропускаю сигнал %s %s из источника '%s' (не в allowed_sources)",
                        sig.symbol,
                        sig.side.value,
                        sig.source,
                    )
                    continue

                if sig.risk_usd < min_risk_usd or sig.risk_usd > max_risk_usd:
                    LOG.info(
                        "Пропускаю сигнал %s %s risk=%.2f (не в диапазоне [%.2f, %.2f])",
                        sig.symbol,
                        sig.side.value,
                        sig.risk_usd,
                        min_risk_usd,
                        max_risk_usd,
                    )
                    continue

                try:
                    if hasattr(engine.status, "last_signal_ts"):
                        engine.status.last_signal_ts = sig.ts  # type: ignore[attr-defined]
                except Exception:
                    pass

                if sig.side in (TradeSide.LONG, TradeSide.SHORT):
                    try:
                        res = engine.open_from_signal(sig, equity_usd=equity_usd)
                        LOG.info(
                            "Обработан сигнал на вход: %s %s, результат: %s",
                            sig.symbol,
                            sig.side.value,
                            res,
                        )
                    except Exception:
                        LOG.exception(
                            "Ошибка при обработке сигнала на вход %s %s",
                            sig.symbol,
                            sig.side.value,
                        )
                elif sig.side == TradeSide.CLOSE:
                    LOG.info(
                        "Получен CLOSE-сигнал для %s (пока только логирую, без действий)",
                        sig.symbol,
                    )
                else:
                    LOG.warning(
                        "Неизвестный тип сигнала side=%r для %s: пропускаю",
                        sig.side,
                        sig.symbol,
                    )

            # 2) Heartbeat
            if now - last_health_ts >= health_interval:
                try:
                    write_health(heartbeat_file, engine, now=now, start_ts=start_ts)
                    last_health_ts = now
                except Exception:
                    LOG.exception("Ошибка при записи heartbeat в %s", heartbeat_file)

            time.sleep(signal_poll_interval_ms / 1000.0)

    finally:
        LOG.info("ExecutionEngine v5 останавливается...")
        try:
            write_health(heartbeat_file, engine, now=time.time(), start_ts=start_ts)
        except Exception:
            LOG.exception("Ошибка при финальной записи heartbeat")
        LOG.info("ExecutionEngine v5 остановлен корректно.")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        LOG.warning("Остановка по Ctrl+C")
        sys.exit(0)
    except Exception:
        LOG.exception("Критическая ошибка в run_live_v5")
        sys.exit(1)


        # Режим работы (DRY / LIVE)
        mode = getattr(engine, "mode", None)

        # Открытые позиции (может быть dict, list, set, число и т.п.)
        open_positions = getattr(engine, "open_positions", None)
        if isinstance(open_positions, (list, tuple, set, dict)):
            open_positions_count = len(open_positions)
        elif isinstance(open_positions, (int, float)):
            open_positions_count = open_positions
        elif open_positions is None:
            open_positions_count = 0
        else:
            open_positions_count = None

        # Дневной PnL через RiskManager (если есть)
        risk_mgr = getattr(engine, "risk_manager", None)
        if risk_mgr is not None:
            daily_pnl = getattr(risk_mgr, "daily_pnl", None)
        else:
            daily_pnl = None

        # Очередь сигналов
        queue = getattr(engine, "signal_queue", None)
        queue_size = None
        if queue is not None:
            # сначала пробуем size()/qsize()/pending_count
            for attr in ("size", "qsize", "pending_count"):
                func = getattr(queue, attr, None)
                if callable(func):
                    try:
                        queue_size = func()
                        break
                    except Exception:
                        pass
            # если не получилось — пробуем len(queue)
            if queue_size is None:
                try:
                    queue_size = len(queue)
                except Exception:
                    pass

        payload = {
            "ts": now.isoformat(),
            "uptime_sec": uptime_sec,
            "mode": mode,
            "open_positions": open_positions_count,
            "daily_pnl": daily_pnl,
            "queue_size": queue_size,
        }

        os.makedirs(os.path.dirname(heartbeat_file), exist_ok=True)
        with open(heartbeat_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        log.debug("Heartbeat v5 записан в %s: %s", heartbeat_file, payload)

    except Exception:
        # Важно: не даём исключению улететь наружу, только логируем
        log.exception("Ошибка при записи heartbeat в %s", heartbeat_file)
    log = logging.getLogger(__name__)

    try:
        # Аптайм
        uptime_sec = (now - start_ts).total_seconds()

        # Режим работы (DRY / LIVE и т.п.)
        mode = getattr(engine, "mode", None)

        # Открытые позиции
        open_positions = getattr(engine, "open_positions", None)
        if isinstance(open_positions, (list, tuple, set, dict)):
            open_positions_count = len(open_positions)
        elif isinstance(open_positions, (int, float)):
            open_positions_count = open_positions
        elif open_positions is None:
            open_positions_count = 0
        else:
            open_positions_count = None

        # Дневной PnL через RiskManager (если есть)
        risk_mgr = getattr(engine, "risk_manager", None)
        if risk_mgr is not None:
            daily_pnl = getattr(risk_mgr, "daily_pnl", None)
        else:
            daily_pnl = None

        # Очередь сигналов (если есть)
        queue = getattr(engine, "signal_queue", None)
        queue_size = None
        if queue is not None:
            # Пытаемся взять длину через методы
            for attr in ("size", "qsize", "pending_count"):
                func = getattr(queue, attr, None)
                if callable(func):
                    try:
                        queue_size = func()
                        break
                    except Exception:
                        pass
            # Если не получилось — через len(queue)
            if queue_size is None:
                try:
                    queue_size = len(queue)
                except Exception:
                    pass

        payload = {
            "ts": now.isoformat(),
            "uptime_sec": uptime_sec,
            "mode": mode,
            "open_positions": open_positions_count,
            "daily_pnl": daily_pnl,
            "queue_size": queue_size,
        }

        os.makedirs(os.path.dirname(heartbeat_file), exist_ok=True)
        with open(heartbeat_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        log.debug("Heartbeat v5 записан в %s: %s", heartbeat_file, payload)
    except Exception:
        log.exception("Ошибка при записи heartbeat в %s", heartbeat_file)

def write_health(heartbeat_file, engine, now, start_ts):
    """
    Безопасная запись heartbeat-файла v5.
    Не зависит от engine.status, не падает при отсутствии полей.
    """
    import os
    import json
    import logging

    log = logging.getLogger(__name__)

    try:
        # Аптайм
        uptime_sec = (now - start_ts).total_seconds()

        # Режим работы (DRY / LIVE и т.п.)
        mode = getattr(engine, "mode", None)

        # Открытые позиции
        open_positions = getattr(engine, "open_positions", None)
        if isinstance(open_positions, (list, tuple, set, dict)):
            open_positions_count = len(open_positions)
        elif isinstance(open_positions, (int, float)):
            open_positions_count = open_positions
        elif open_positions is None:
            open_positions_count = 0
        else:
            open_positions_count = None

        # Дневной PnL через RiskManager (если есть)
        risk_mgr = getattr(engine, "risk_manager", None)
        if risk_mgr is not None:
            daily_pnl = getattr(risk_mgr, "daily_pnl", None)
        else:
            daily_pnl = None

        # Очередь сигналов (если есть)
        queue = getattr(engine, "signal_queue", None)
        queue_size = None
        if queue is not None:
            # Пытаемся взять длину через методы
            for attr in ("size", "qsize", "pending_count"):
                func = getattr(queue, attr, None)
                if callable(func):
                    try:
                        queue_size = func()
                        break
                    except Exception:
                        pass
            # Если не получилось — через len(queue)
            if queue_size is None:
                try:
                    queue_size = len(queue)
                except Exception:
                    pass

        payload = {
            "ts": now.isoformat(),
            "uptime_sec": uptime_sec,
            "mode": mode,
            "open_positions": open_positions_count,
            "daily_pnl": daily_pnl,
            "queue_size": queue_size,
        }

        os.makedirs(os.path.dirname(heartbeat_file), exist_ok=True)
        with open(heartbeat_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        log.debug("Heartbeat v5 записан в %s: %s", heartbeat_file, payload)
    except Exception:
        log.exception("Ошибка при записи heartbeat в %s", heartbeat_file)

