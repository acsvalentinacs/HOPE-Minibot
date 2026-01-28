# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T12:30:00Z
# Modified by: Claude (opus-4)
# Modified at: 2026-01-28T12:30:00Z
# Purpose: Position storage v5 - atomic JSON read/write for exec_positions_v5.json
# P0 FIX: Changed import from core.types to core.type_defs (A2 fix)
# === END SIGNATURE ===
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable, List, Optional

from core.type_defs import PositionInfo

logger = logging.getLogger(__name__)


class PositionStorageV5:
    """
    Хранилище позиций v5 с аккуратной атомарной записью.

    Основные задачи:
    - безопасно читать/писать exec_positions_v5.json;
    - не ломать существующий формат файла;
    - по возможности поддерживать старый интерфейс PositionStorage.
    """

    def __init__(self, path_exec_positions: str, path_trades: Optional[str] = None) -> None:
        self.exec_path = Path(path_exec_positions)
        self.trades_path = Path(path_trades) if path_trades else None

        if self.exec_path.parent:
            self.exec_path.parent.mkdir(parents=True, exist_ok=True)
        if self.trades_path and self.trades_path.parent:
            self.trades_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ
    # ------------------------------------------------------------------
    def _atomic_write_json(self, path: Path, data: Any) -> None:
        """
        Атомарная запись JSON:
        1) пишем во временный файл *.tmp
        2) делаем .bak (если был старый файл)
        3) заменяем старый файл на новый через os.replace
        """
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        backup_path = path.with_suffix(path.suffix + ".bak")

        try:
            # 1. Запись во временный файл
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            # 2. Бэкап старого файла (по возможности)
            if path.exists():
                try:
                    os.replace(path, backup_path)
                except OSError as e:
                    logger.warning("PositionStorageV5: не удалось сделать бэкап %s: %s", path, e)

            # 3. Атомарная замена
            os.replace(tmp_path, path)
        except Exception as e:
            logger.error("PositionStorageV5: ошибка атомарной записи %s: %s", path, e)
            # На всякий случай чистим tmp
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # ПУБЛИЧНЫЙ ИНТЕРФЕЙС (СОВМЕСТИМЫЙ С СТАРЫМ PositionStorage)
    # ------------------------------------------------------------------
    def load_positions(self) -> List[PositionInfo]:
        """
        Загрузить список позиций из exec_positions_v5.json.

        Ожидаемый основной формат:
            [
              { ... поля PositionInfo ... },
              ...
            ]

        Также пытаемся быть толерантными к формату:
            { "positions": [ ... ] }
        """
        if not self.exec_path.exists():
            return []

        try:
            text = self.exec_path.read_text(encoding="utf-8").strip()
            if not text:
                return []

            raw = json.loads(text)

            # Поддержка формата {"positions": [...]} на всякий случай
            if isinstance(raw, dict):
                raw = raw.get("positions", [])

            if not isinstance(raw, list):
                logger.warning(
                    "PositionStorageV5: неожиданный формат exec_positions_v5.json (%s), ожидаю список",
                    type(raw),
                )
                return []

            positions: List[PositionInfo] = []
            for item in raw:
                if isinstance(item, dict):
                    try:
                        positions.append(PositionInfo(**item))
                    except TypeError as e:
                        logger.error("PositionStorageV5: не удалось распаковать PositionInfo из %r: %s", item, e)
                else:
                    logger.warning("PositionStorageV5: пропускаю непонятный элемент в списке позиций: %r", item)

            return positions

        except Exception as e:
            logger.error("PositionStorageV5: ошибка чтения exec_positions_v5.json: %s", e)
            return []

    def save_positions(self, positions: Iterable[PositionInfo]) -> None:
        """
        Сохранить список позиций в exec_positions_v5.json.

        Формат — просто массив JSON-объектов, совместимый с текущим v5.
        """
        serialised: List[dict] = []

        for p in positions:
            if is_dataclass(p):
                serialised.append(asdict(p))
            elif isinstance(p, dict):
                # На случай, если где-то уже лежат dict'ы
                serialised.append(p)
            else:
                logger.warning(
                    "PositionStorageV5: неожиданый тип позиции %r (%s) — пропускаю",
                    p,
                    type(p),
                )

        self._atomic_write_json(self.exec_path, serialised)

    # ------------------------------------------------------------------
    # ДОП. ЛОГ ТРЕЙДОВ (ОПЦИОНАЛЬНО)
    # ------------------------------------------------------------------
    def append_trade_record(self, trade_record: dict) -> None:
        """
        Простейший append-only лог трейдов.
        Не обязателен для работы ядра, но может использоваться ReconciliationEngine.
        """
        if not self.trades_path:
            return

        try:
            line = json.dumps(trade_record, ensure_ascii=False)
            with self.trades_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as e:
            logger.error("PositionStorageV5: ошибка записи трейда в %s: %s", self.trades_path, e)
