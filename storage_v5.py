#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
storage_v5.py — безопасная работа с файлом exec_positions_v5.json.

Задача:
- читать/записывать state\\exec_positions_v5.json;
- писать файл атомарно (через временный файл → rename),
  чтобы при выдернутом свете он не портился.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path("state") / "exec_positions_v5.json"


def _normalize(data: Any) -> Dict[str, Any]:
    """
    Приводим данные к формату {"value": [...]}.

    Если сверху уже dict с ключом "value" — не трогаем.
    Если список — оборачиваем.
    Иначе возвращаем пустую структуру.
    """
    if isinstance(data, dict) and "value" in data:
        return data
    if isinstance(data, list):
        return {"value": data}
    return {"value": []}


def load_positions(path: str | Path = DEFAULT_PATH) -> Dict[str, Any]:
    """
    Прочитать exec_positions_v5.json.

    Никогда не кидает исключение наружу — при ошибке вернёт {"value": []}.
    """
    path = Path(path)
    if not path.exists():
        logger.info("storage_v5: файл %s ещё не создан, возвращаю пустой список", path)
        return {"value": []}

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        data = _normalize(data)
        count = len(data.get("value", []))
        logger.info("storage_v5: загружено %s позиций из %s", count, path)
        return data
    except Exception as exc:  # noqa: BLE001
        logger.error("storage_v5: ошибка чтения %s: %s", path, exc)
        # Если файл сломан — просто не даём упасть ядру
        return {"value": []}


def save_positions(data: Dict[str, Any], path: str | Path = DEFAULT_PATH) -> None:
    """
    Атомарно сохранить exec_positions_v5.json.

    Пишем во временный файл в той же директории и только потом
    заменяем основной файл os.replace.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    norm = _normalize(data)
    value = norm.get("value", [])
    tmp_name: str | None = None

    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(path.parent),
            delete=False,
        ) as tmp:
            json.dump(norm, tmp, ensure_ascii=False, indent=2)
            tmp_name = tmp.name

        os.replace(tmp_name, path)
        logger.info(
            "storage_v5: атомарное сохранение %s (позиций: %s)",
            path,
            len(value),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("storage_v5: ошибка сохранения %s: %s", path, exc)
        # На всякий случай пробуем удалить временный файл
        try:
            if tmp_name and os.path.exists(tmp_name):
                os.remove(tmp_name)
        except Exception:  # noqa: BLE001
            pass
