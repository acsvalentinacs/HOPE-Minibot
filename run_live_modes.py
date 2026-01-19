#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
minibot/run_live_modes.py
Единая точка входа HOPE по режимам: CORE / TURBO / HYBRID.

Задача:
- Не трогая существующий minibot/run_live.py (Core),
  добавить сверху диспетчер режимов:

  HOPE_MODE=CORE   -> запускаем только Core (minibot/run_live.py)
  HOPE_MODE=TURBO  -> запускаем только Turbo (minibot/run_live_turbo.py)
  HOPE_MODE=HYBRID -> запускаем оба, следим за их жизнью

CLI:
  python -m minibot.run_live_modes --mode CORE
  python -m minibot.run_live_modes --mode TURBO
  python -m minibot.run_live_modes --mode HYBRID

Если --mode не указан, берём из переменной окружения HOPE_MODE (по умолчанию CORE).
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [HOPE_MODES] {msg}")


def build_cmd(rel_path: str, extra_args: Optional[List[str]] = None) -> List[str]:
    """
    Собирает команду запуска конкретного скрипта внутри текущего venv.
    rel_path задаётся относительно PROJECT_ROOT, например:
      'minibot/run_live.py'
      'minibot/run_live_turbo.py'
    """
    script_path = PROJECT_ROOT / rel_path
    cmd = [sys.executable, str(script_path)]
    if extra_args:
        cmd.extend(extra_args)
    return cmd


def run_blocking(cmd: List[str], name: str) -> int:
    log(f"Запуск {name}: {' '.join(cmd)}")
    return subprocess.call(cmd)


def run_hybrid(core_cmd: List[str], turbo_cmd: List[str]) -> int:
    """
    HYBRID-режим: параллельный запуск Core + Turbo.

    Поведение v1:
      - стартуем оба процесса;
      - ждём завершения любого;
      - по Ctrl+C гасим обоих;
      - по завершении одного — пишем лог и выходим,
        а "как рестартить" будем решать уже на уровне watchdog/сервиса.
    """
    log("Запуск HYBRID-режима (Core + Turbo)...")

    core_proc = subprocess.Popen(core_cmd)
    log(f"Core PID = {core_proc.pid}")

    turbo_proc = subprocess.Popen(turbo_cmd)
    log(f"Turbo PID = {turbo_proc.pid}")

    try:
        # ждём, пока один из процессов не завершится
        while True:
            core_ret = core_proc.poll()
            turbo_ret = turbo_proc.poll()

            if core_ret is not None:
                log(f"Core завершился с кодом {core_ret}, останавливаем HYBRID.")
                break

            if turbo_ret is not None:
                log(f"Turbo завершился с кодом {turbo_ret}, останавливаем HYBRID.")
                break

            time.sleep(2.0)

    except KeyboardInterrupt:
        log("Получен KeyboardInterrupt, останавливаем оба процесса...")

    finally:
        # Пытаемся корректно завершить детей
        for proc, label in ((core_proc, "Core"), (turbo_proc, "Turbo")):
            if proc.poll() is None:
                log(f"Пытаюсь завершить {label} (PID={proc.pid})...")
                proc.terminate()
        time.sleep(3.0)
        for proc, label in ((core_proc, "Core"), (turbo_proc, "Turbo")):
            if proc.poll() is None:
                log(f"{label} не завершился, принудительный kill (PID={proc.pid})")
                proc.kill()

    log("HYBRID-режим завершён.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(description="HOPE live modes launcher (CORE/TURBO/HYBRID)")
    parser.add_argument(
        "--mode",
        type=str,
        help="Режим работы: CORE / TURBO / HYBRID (если не указано — берётся из HOPE_MODE, по умолчанию CORE)",
    )
    args = parser.parse_args(argv)

    env_mode = os.getenv("HOPE_MODE", "CORE").upper()
    mode = (args.mode or env_mode).upper()

    if mode not in ("CORE", "TURBO", "HYBRID"):
        log(f"Неизвестный режим HOPE_MODE={mode}, допустимо: CORE / TURBO / HYBRID")
        return 1

    log(f"Режим работы: {mode}")

    # Путь к существующему Core-скрипту
    core_rel = "minibot/run_live.py"
    turbo_rel = "minibot/run_live_turbo.py"

    core_cmd = build_cmd(core_rel)
    turbo_cmd = build_cmd(turbo_rel)

    if mode == "CORE":
        return run_blocking(core_cmd, "Core")

    if mode == "TURBO":
        return run_blocking(turbo_cmd, "Turbo")

    # HYBRID
    return run_hybrid(core_cmd, turbo_cmd)


if __name__ == "__main__":
    raise SystemExit(main())
