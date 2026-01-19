#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Heartbeat/health writer for HOPE ExecutionEngine v5.
Writes a small JSON with the most important telemetry fields.
"""

import os
import json
import logging
from datetime import datetime

log = logging.getLogger(__name__)


def write_health(heartbeat_file, engine, now=None, start_ts=None):
    """
    Безопасная запись heartbeat-файла v5.

    Требования к engine минимальные:
      - engine.mode (str или Enum с .value) — режим работы (DRY/LIVE/TESTNET)
      - engine.open_positions (list/dict/int) — открытые позиции (по возможности)
      - engine.risk_manager.daily_pnl (float) — дневной PnL, если есть
      - engine.signal_queue — очередь сигналов, если есть:
            size()/qsize()/pending_count() или len(queue)

    Если каких-то полей нет — они просто будут None/0 в JSON,
    функция не должна падать.
    """
    try:
        if now is None:
            now = datetime.utcnow()

        # Аптайм
        uptime_sec = None
        if start_ts is not None:
            # Поддерживаем как datetime, так и float timestamp
            if hasattr(start_ts, "timestamp"):
                uptime_sec = (now - start_ts).total_seconds()
            else:
                try:
                    start_dt = datetime.fromtimestamp(float(start_ts))
                    uptime_sec = (now - start_dt).total_seconds()
                except Exception:
                    uptime_sec = None

        # Режим
        mode = getattr(engine, "mode", None)
        if hasattr(mode, "value"):
            mode = mode.value

        # Открытые позиции
        open_positions = getattr(engine, "open_positions", None)
        if open_positions is None and hasattr(engine, "positions"):
            open_positions = getattr(engine, "positions")

        if isinstance(open_positions, (list, tuple, set, dict)):
            open_positions_count = len(open_positions)
        elif isinstance(open_positions, (int, float)):
            open_positions_count = int(open_positions)
        elif open_positions is None:
            open_positions_count = 0
        else:
            open_positions_count = None

        # Дневной PnL
        daily_pnl = None
        risk_mgr = getattr(engine, "risk_manager", None)
        if risk_mgr is not None:
            daily_pnl = getattr(risk_mgr, "daily_pnl", None)
        if daily_pnl is None and hasattr(engine, "daily_pnl"):
            daily_pnl = getattr(engine, "daily_pnl")

        # Очередь сигналов
        queue = getattr(engine, "signal_queue", None)
        queue_size = None
        if queue is not None:
            for attr in ("size", "qsize", "pending_count"):
                func = getattr(queue, attr, None)
                if callable(func):
                    try:
                        queue_size = func()
                        break
                    except Exception:
                        pass
            if queue_size is None:
                try:
                    queue_size = len(queue)  # type: ignore[arg-type]
                except Exception:
                    queue_size = None

        payload = {
            "version": "v5.1.0",
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
        # Здесь не бросаем ошибку наружу, чтобы не уронить движок
        log.exception("Ошибка при записи heartbeat в %s", heartbeat_file)
