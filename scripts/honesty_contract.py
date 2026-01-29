# -*- coding: utf-8 -*-
# ══════════════════════════════════════════════════════════════════════════════
#
#                    КОНТРАКТ ЧЕСТНОСТИ CLAUDE ↔ ВАЛЕНТИН
#                              AI System v2.0
#
#                    ГЛОБАЛЬНЫЙ - ВСЕ ПРОЕКТЫ, ВСЕ ОТВЕТЫ
#
# ══════════════════════════════════════════════════════════════════════════════
# sha256: honesty_contract_global_v2.0
# ══════════════════════════════════════════════════════════════════════════════
"""
КОНТРАКТ ЧЕСТНОСТИ CLAUDE ↔ ВАЛЕНТИН | AI System v2.0

ГЛОБАЛЬНЫЙ МОДУЛЬ - применяется ко ВСЕМ проектам, ВСЕМ ответам.

АБСОЛЮТНЫЕ ЗАПРЕТЫ:
- Фейковые данные (random, hardcoded, симуляция без маркировки)
- Заглушки без явной маркировки [STUB]
- Искажение реальности (убыток ≠ "коррекция")
- Неопределённые формулировки ("примерно", "скоро", "должно работать")

АБСОЛЮТНЫЕ ОБЯЗАТЕЛЬСТВА:
- Реальные данные ИЛИ явное "НЕТ ДАННЫХ"
- Маркировка: [РЕАЛЬНОЕ], [СИМУЛЯЦИЯ], [ОЦЕНКА], [STUB]
- Признание: "не знаю", "нужно проверить", "могу ошибаться"
- Отчёт о ВСЕХ проблемах, рисках, ограничениях

ПРИНЦИП: РЕАЛЬНЫЙ УБЫТОК -10% лучше чем ФЕЙКОВАЯ ПРИБЫЛЬ +20%
"""

import functools
import logging
import math
from datetime import datetime, timezone
from typing import Any, Optional, Dict, List, Union
from dataclasses import dataclass, field
from enum import Enum

__version__ = "2.0"
__contract__ = "КОНТРАКТ ЧЕСТНОСТИ CLAUDE ↔ ВАЛЕНТИН | AI System"

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# EXCEPTIONS - НАРУШЕНИЯ КОНТРАКТА
# ══════════════════════════════════════════════════════════════════════════════

class HonestyViolation(Exception):
    """
    КРИТИЧЕСКОЕ нарушение контракта честности.
    
    Это исключение НЕЛЬЗЯ ловить и игнорировать!
    """
    pass


class DataUnavailable(Exception):
    """Реальные данные недоступны - это НЕ ошибка, это правильное поведение."""
    pass


class IntegrityError(Exception):
    """Нарушение целостности данных."""
    pass


# ══════════════════════════════════════════════════════════════════════════════
# STATUS MARKERS
# ══════════════════════════════════════════════════════════════════════════════

class Status(str, Enum):
    """Обязательные статусы для любых данных."""
    REAL = "РЕАЛЬНОЕ"
    SIMULATED = "СИМУЛЯЦИЯ"
    ESTIMATE = "ОЦЕНКА"
    STUB = "STUB"
    UNTESTED = "UNTESTED"
    BROKEN = "BROKEN"
    UNKNOWN = "НЕИЗВЕСТНО"
    
    def __str__(self) -> str:
        return f"[{self.value}]"


# ══════════════════════════════════════════════════════════════════════════════
# VERIFICATION FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def verify_numeric(
    value: Union[int, float],
    name: str,
    min_val: float = None,
    max_val: float = None,
    allow_zero: bool = True,
    allow_negative: bool = True,
    source: str = "unknown"
) -> float:
    """Верифицирует числовое значение. НИКОГДА не возвращает фейк!"""
    
    if value is None:
        raise DataUnavailable(f"{name} is None (source: {source})")
    
    if math.isnan(value) or math.isinf(value):
        raise HonestyViolation(f"{name} is NaN/Inf - invalid data!")
    
    if value == 0 and not allow_zero:
        raise DataUnavailable(f"{name} is 0 - no data (source: {source})")
    
    if value < 0 and not allow_negative:
        raise HonestyViolation(f"{name} is negative ({value}) - invalid!")
    
    if min_val is not None and value < min_val:
        raise IntegrityError(f"{name} = {value} < min {min_val}")
    if max_val is not None and value > max_val:
        raise IntegrityError(f"{name} = {value} > max {max_val}")
    
    # Suspicious hardcoded values
    suspicious = [1.0, 10.0, 100.0, 1000.0, 0.1, 0.01, 999, -999, 42]
    if value in suspicious:
        logger.warning(f"HONESTY: {name}={value} is suspicious. Verify it's real!")
    
    return float(value)


def verify_price(price: float, symbol: str, source: str = "unknown") -> float:
    """Верификация цены криптовалюты."""
    return verify_numeric(price, f"Price({symbol})", min_val=0, 
                         allow_zero=False, allow_negative=False, source=source)


def verify_pnl(entry: float, exit: float, qty: float, side: str = "LONG") -> Dict:
    """Вычисляет РЕАЛЬНЫЙ PnL. Убыток = убыток!"""
    verify_numeric(entry, "entry", min_val=0, allow_zero=False, allow_negative=False)
    verify_numeric(exit, "exit", min_val=0, allow_zero=False, allow_negative=False)
    verify_numeric(qty, "quantity", min_val=0, allow_zero=False, allow_negative=False)
    
    if side.upper() == "LONG":
        pnl_pct = (exit - entry) / entry * 100
    else:
        pnl_pct = (entry - exit) / entry * 100
    
    return {
        "pnl_pct": pnl_pct,  # МОЖЕТ БЫТЬ ОТРИЦАТЕЛЬНЫМ!
        "pnl_usdt": pnl_pct / 100 * (entry * qty),
        "entry": entry, "exit": exit, "qty": qty, "side": side
    }


# ══════════════════════════════════════════════════════════════════════════════
# DECORATORS
# ══════════════════════════════════════════════════════════════════════════════

def require_honesty(func):
    """Декоратор: функция должна возвращать честные данные."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        if isinstance(result, (int, float)) and result in [1.0, 10.0, 100.0, 0.1, 999, 42]:
            logger.warning(f"HONESTY: {func.__name__}()={result} - verify real!")
        return result
    return wrapper


def fail_closed(func):
    """Декоратор: fail-closed, НЕ подавляет исключения."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (HonestyViolation, DataUnavailable):
            raise
        except Exception as e:
            logger.error(f"FAIL-CLOSED: {func.__name__}() failed: {e}")
            raise
    return wrapper


# ══════════════════════════════════════════════════════════════════════════════
# REPORT
# ══════════════════════════════════════════════════════════════════════════════

def report_honest(title: str, data: Dict, problems: List[str] = None, 
                  mode: str = None) -> str:
    """Генерирует честный отчёт с обязательным указанием проблем."""
    lines = ["═" * 60, f"  {title}"]
    if mode:
        lines.append(f"  РЕЖИМ: {mode}")
    lines.extend([f"  ВРЕМЯ: {datetime.now(timezone.utc).isoformat()}", "═" * 60, ""])
    
    for key, val in data.items():
        mark = " 📉" if isinstance(val, (int, float)) and val < 0 else ""
        lines.append(f"  {key}: {val}{mark}")
    
    if problems:
        lines.extend(["", "⚠️ ПРОБЛЕМЫ:"])
        for p in problems:
            lines.append(f"  • {p}")
    
    lines.extend(["", "─" * 60, 
                  "КОНТРАКТ: Данные реальные. Убытки = убытки.", "═" * 60])
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# CODE CHECK
# ══════════════════════════════════════════════════════════════════════════════

VIOLATIONS = [
    ("random.random()", "Fake random data"),
    ("random.uniform(", "Fake random data"),
    ("# TODO", "Unfinished code"),
    ("# FIXME", "Known bug"),
    ("pass  #", "Empty implementation"),
    ("return 0.0  #", "Suspicious default"),
]

def check_code(filepath: str) -> Dict:
    """Проверяет код на нарушения контракта."""
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()
    
    violations = []
    for i, line in enumerate(lines, 1):
        for pattern, desc in VIOLATIONS:
            if pattern.lower() in line.lower():
                violations.append({"line": i, "pattern": pattern, "desc": desc})
    
    return {"filepath": filepath, "violations": violations, 
            "is_clean": len(violations) == 0}


# ══════════════════════════════════════════════════════════════════════════════
# CONTRACT
# ══════════════════════════════════════════════════════════════════════════════

CONTRACT = {
    "name": "HONESTY CONTRACT CLAUDE <-> VALENTIN | AI System",
    "version": "2.0",
    "scope": "GLOBAL - ALL projects, ALL responses",
    "status": "ACTIVE | PERMANENT | NON-NEGOTIABLE",
    "principles": [
        "NO fake data",
        "NO stubs without marking",
        "NO vague formulations",
        "ALWAYS real data or explicit exception",
        "ALWAYS honest metrics including losses",
        "REAL LOSS is better than FAKE PROFIT",
    ],
}

def print_contract():
    print("=" * 70)
    print(f"  {CONTRACT['name']}")
    print(f"  v{CONTRACT['version']} | {CONTRACT['scope']}")
    print("=" * 70)
    for p in CONTRACT['principles']:
        print(f"  [+] {p}")
    print("=" * 70)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        result = check_code(sys.argv[2])
        status = "CLEAN" if result['is_clean'] else "VIOLATIONS"
        print(f"\n{status}: {result['filepath']}")
        for v in result['violations']:
            print(f"  Line {v['line']}: {v['desc']}")
        sys.exit(0 if result['is_clean'] else 1)
    else:
        print_contract()
