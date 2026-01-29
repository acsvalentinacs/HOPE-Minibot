# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 02:45:00 UTC
# Purpose: Canonical SHA256 contract for all HOPE data structures
# Contract: Single source of truth for sha256 computation
# === END SIGNATURE ===
"""
SHA256 CONTRACT - единый стандарт хэширования для всех структур данных HOPE.

Правила каноникализации:
1. Удалить поле 'sha256' из объекта перед хэшированием
2. JSON: sort_keys=True, separators=(',', ':'), ensure_ascii=False
3. Кодировка: UTF-8
4. Префикс: 'sha256:'
5. Длина хэша: 16 hex chars (64 бита) для компактности

Использование:
    from core.sha256_contract import compute_sha256, add_sha256, verify_sha256

    obj = {"event": "SIGNAL", "data": {...}}
    obj_with_sha = add_sha256(obj)  # Добавляет sha256 поле

    is_valid, expected = verify_sha256(obj_with_sha)  # Проверяет
"""

import hashlib
import json
from typing import Any, Dict, Tuple, Union

# Стандартная длина хэша (hex chars)
SHA256_LENGTH = 16
SHA256_PREFIX = "sha256:"


def canonical_json(obj: Dict[str, Any]) -> bytes:
    """
    Канонический JSON для sha256 вычисления.

    Правила:
    - Удаляет поле 'sha256' (если есть)
    - sort_keys=True для детерминизма
    - Без пробелов: separators=(',', ':')
    - UTF-8 без BOM
    """
    obj_copy = dict(obj)
    obj_copy.pop("sha256", None)

    return json.dumps(
        obj_copy,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":")
    ).encode("utf-8")


def compute_sha256(obj: Dict[str, Any], length: int = SHA256_LENGTH) -> str:
    """
    Вычислить sha256 хэш объекта.

    Args:
        obj: Словарь для хэширования
        length: Длина hex-части (по умолчанию 16)

    Returns:
        Строка вида 'sha256:abc123...'
    """
    canonical = canonical_json(obj)
    full_hash = hashlib.sha256(canonical).hexdigest()
    return f"{SHA256_PREFIX}{full_hash[:length]}"


def add_sha256(obj: Dict[str, Any], length: int = SHA256_LENGTH) -> Dict[str, Any]:
    """
    Добавить sha256 поле к объекту.

    Args:
        obj: Исходный словарь
        length: Длина hex-части

    Returns:
        Новый словарь с добавленным полем sha256
    """
    result = dict(obj)
    result["sha256"] = compute_sha256(obj, length)
    return result


def verify_sha256(obj: Dict[str, Any], length: int = SHA256_LENGTH) -> Tuple[bool, str]:
    """
    Проверить sha256 поле объекта.

    Args:
        obj: Словарь с полем sha256
        length: Ожидаемая длина hex-части

    Returns:
        Tuple (is_valid, expected_sha256)
        - is_valid: True если sha256 совпадает
        - expected_sha256: Ожидаемое значение sha256
    """
    sha = obj.get("sha256")
    expected = compute_sha256(obj, length)

    if not sha:
        return False, expected

    if not str(sha).startswith(SHA256_PREFIX):
        return False, expected

    if sha != expected:
        return False, expected

    return True, expected


def extract_sha256(sha_string: str) -> str:
    """
    Извлечь hex-часть из sha256 строки.

    Args:
        sha_string: Строка вида 'sha256:abc123...'

    Returns:
        Hex-часть без префикса
    """
    if sha_string.startswith(SHA256_PREFIX):
        return sha_string[len(SHA256_PREFIX):]
    return sha_string


def is_valid_sha256_format(sha_string: str, length: int = SHA256_LENGTH) -> bool:
    """
    Проверить формат sha256 строки.

    Args:
        sha_string: Строка для проверки
        length: Ожидаемая длина hex-части

    Returns:
        True если формат валидный
    """
    if not sha_string or not isinstance(sha_string, str):
        return False

    if not sha_string.startswith(SHA256_PREFIX):
        return False

    hex_part = sha_string[len(SHA256_PREFIX):]

    if len(hex_part) != length:
        return False

    try:
        int(hex_part, 16)
        return True
    except ValueError:
        return False


# Алиасы для совместимости
compute_hash = compute_sha256
add_hash = add_sha256
verify_hash = verify_sha256
