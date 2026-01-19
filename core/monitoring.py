from __future__ import annotations
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Импортируем обновленный тип
from .models import EngineStatus, EngineMode

log = logging.getLogger(__name__)

class HealthMonitor:
    def __init__(self, path_health: str, engine_version: str = "v5.0.0") -> None:
        self.path = Path(path_health)
        if self.path.parent:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine_version = engine_version
        self._last_error: Optional[str] = None

    def update(
        self,
        status: EngineStatus,
        uptime_sec: float,
        queue_size: Optional[int],
        now_ts: float,
    ) -> None:
        try:
            # Превращаем EngineStatus в dict
            payload = asdict(status)
            
            # Дописываем мета-данные
            payload["ts"] = datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat()
            payload["engine_version"] = self.engine_version
            # Добавляем uptime и queue
            payload["uptime_sec"] = uptime_sec
            payload["queue_size"] = queue_size
            payload["engine_ok"] = True  # Если дошли сюда — ядро работает
            # Enum в строку
            if isinstance(payload.get("mode"), EngineMode):
                payload["mode"] = payload["mode"].value
            
            # Пишем файл
            tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            tmp_path.replace(self.path)
        except Exception as e:
            log.error(f"Failed to update health file: {e}")

    def record_error(self, message: str) -> None:
        self._last_error = str(message)

