import os
import json
import logging
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)


def write_health(heartbeat_file: str, engine: Any, now: datetime, start_ts: datetime) -> None:
    """
    Записывает heartbeat-файл для v5.

    Ничего не знает про engine.status и не падает,
    даже если каких-то полей в engine нет.

    Пишем:
    - ts          — ISO-время
    - uptime_sec  — аптайм в секундах
    - mode        — режим (DRY / LIVE), если есть
    - open_positions — кол-во открытых позиций (если можем посчитать)
    - daily_pnl   — дневной PnL из RiskManager (если есть)
    - queue_size  — размер очереди сигналов (если есть)
    """
    try:
        # Время работы
        uptime_sec = (now - start_ts).total_seconds()

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
