#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_autotrader_gates.py - Патч для интеграции SignalGate в autotrader

=== AI SIGNATURE ===
Created by: Claude (opus-4.5)
Created at: 2026-02-05T02:00:00Z
Purpose: Integrate Anti-Chase + Observation Mode into trading pipeline
=== END SIGNATURE ===

ИСПОЛЬЗОВАНИЕ:
    python3 patch_autotrader_gates.py /opt/hope/minibot/scripts/autotrader.py

Этот скрипт:
1. Находит место для вставки импортов
2. Добавляет инициализацию SignalGate
3. Добавляет проверку перед входом в позицию
4. Создаёт backup перед изменением
"""

import re
import sys
import shutil
from pathlib import Path
from datetime import datetime


def patch_autotrader(file_path: str) -> bool:
    """
    Патчит autotrader.py для интеграции SignalGate.
    """
    path = Path(file_path)
    
    if not path.exists():
        print(f"❌ File not found: {file_path}")
        return False
    
    # Backup
    backup_path = path.with_suffix(f".py.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    shutil.copy(path, backup_path)
    print(f"✅ Backup created: {backup_path}")
    
    content = path.read_text(encoding='utf-8')
    
    # 1. Добавить импорт после существующих импортов
    import_block = '''
# === SIGNAL GATE INTEGRATION (Auto-patched) ===
try:
    from core.ai.anti_chase_filter import SignalGate, AntiChaseFilter, ObservationMode
    SIGNAL_GATE_AVAILABLE = True
except ImportError:
    SIGNAL_GATE_AVAILABLE = False
    logger.warning("SignalGate not available, running without anti-chase filter")
# === END SIGNAL GATE INTEGRATION ===
'''
    
    # Найти место для импортов (после последнего import)
    import_pattern = r'(^import .*$|^from .* import .*$)'
    imports = list(re.finditer(import_pattern, content, re.MULTILINE))
    
    if imports:
        last_import_end = imports[-1].end()
        content = content[:last_import_end] + '\n' + import_block + content[last_import_end:]
        print("✅ Import block added")
    else:
        print("⚠️ Could not find import section, adding at top")
        content = import_block + '\n' + content
    
    # 2. Добавить инициализацию SignalGate
    init_block = '''
# === SIGNAL GATE INIT ===
_signal_gate = None
if SIGNAL_GATE_AVAILABLE:
    try:
        _signal_gate = SignalGate()
        logger.info("SignalGate initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize SignalGate: {e}")
# === END SIGNAL GATE INIT ===
'''
    
    # Найти место после определения logger
    logger_pattern = r'(logger\s*=\s*.*$)'
    logger_match = re.search(logger_pattern, content, re.MULTILINE)
    
    if logger_match:
        insert_pos = logger_match.end()
        content = content[:insert_pos] + '\n' + init_block + content[insert_pos:]
        print("✅ SignalGate initialization added")
    
    # 3. Добавить функцию проверки
    check_function = '''
# === SIGNAL GATE CHECK FUNCTION ===
def check_signal_gate(symbol: str, price: float, confidence: float, 
                      win_rate: float = 50.0, loss_streak: int = 0) -> tuple:
    """
    Проверить сигнал через SignalGate.
    
    Returns:
        (approved, adjusted_confidence, reason)
    """
    if not SIGNAL_GATE_AVAILABLE or _signal_gate is None:
        return True, confidence, "SignalGate not available"
    
    try:
        result = _signal_gate.check_signal(
            symbol=symbol,
            current_price=price,
            confidence=confidence,
            win_rate=win_rate,
            loss_streak=loss_streak,
        )
        return result.approved, result.adjusted_confidence, result.reason
    except Exception as e:
        logger.error(f"SignalGate error: {e}")
        return True, confidence, f"Gate error: {e}"
# === END SIGNAL GATE CHECK FUNCTION ===
'''
    
    # Добавить после init блока
    if '# === END SIGNAL GATE INIT ===' in content:
        insert_pos = content.find('# === END SIGNAL GATE INIT ===')
        insert_pos = content.find('\n', insert_pos) + 1
        content = content[:insert_pos] + check_function + content[insert_pos:]
        print("✅ Check function added")
    
    # 4. Сохранить
    path.write_text(content, encoding='utf-8')
    print(f"✅ File patched: {file_path}")
    
    return True


# Snippet для ручной интеграции в точку входа
MANUAL_INTEGRATION_SNIPPET = '''
# ============================================================
# MANUAL INTEGRATION SNIPPET
# Добавить ПЕРЕД execute_trade / open_position:
# ============================================================

# Получить текущие метрики (из adaptive_confidence или другого источника)
current_win_rate = confidence_manager.get_win_rate() if confidence_manager else 50.0
current_loss_streak = 0  # Получить из статистики

# Проверить через SignalGate
approved, adjusted_confidence, reason = check_signal_gate(
    symbol=signal.symbol,
    price=signal.price,
    confidence=signal.confidence,
    win_rate=current_win_rate,
    loss_streak=current_loss_streak,
)

if not approved:
    logger.info(f"Signal BLOCKED by SignalGate: {reason}")
    # Записать виртуальную сделку если в Observation Mode
    if _signal_gate and _signal_gate.observation.is_active:
        # Симулировать результат для обучения
        pass
    return  # Не торгуем

# Использовать adjusted_confidence вместо оригинального
signal.confidence = adjusted_confidence

# Продолжить с открытием позиции...
'''


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 patch_autotrader_gates.py <path_to_autotrader.py>")
        print("\nManual integration snippet:")
        print(MANUAL_INTEGRATION_SNIPPET)
        sys.exit(1)
    
    file_path = sys.argv[1]
    success = patch_autotrader(file_path)
    
    if success:
        print("\n✅ Patch applied successfully!")
        print("\n⚠️ IMPORTANT: You still need to manually add the check at the trade entry point.")
        print("See MANUAL_INTEGRATION_SNIPPET in this file for the code to add.")
    else:
        print("\n❌ Patch failed!")
        sys.exit(1)
