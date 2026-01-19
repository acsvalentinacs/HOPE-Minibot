#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notify_webhook.py — продюсер событий для Telegram-очереди (HOPE v3.3)

Пишет JSONL-события в logs/webhook_outbox.jsonl:

  kind:
    - trade_open
    - trade_close
    - daily_stop
    - system
    - system_debug   ← DEBUG-события

Фильтрация DEBUG:
  - Основной флаг — flags/DEBUG.flag
  - Дополнительно учитывает HOPE_NOTIFY_DEBUG из .env/окружения
"""

from __future__ import annotations

import json
import time
import os
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

__version__ = "3.3.0"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
FLAGS_DIR = PROJECT_ROOT / "flags"
OUTBOX = LOG_DIR / "webhook_outbox.jsonl"
ENV_PATH = Path(r"C:\secrets\hope\.env")
DEBUG_FLAG = FLAGS_DIR / "DEBUG.flag"

LOG_DIR.mkdir(parents=True, exist_ok=True)
FLAGS_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("HOPE.notify")


@dataclass
class WebhookEvent:
    ts: float
    ts_iso: str
    kind: str
    payload: Dict[str, Any]
    text: Optional[str]
    attempts: int = 0

    def to_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def _debug_from_env() -> bool:
    """Читает HOPE_NOTIFY_DEBUG из окружения или .env (fallback)."""
    val = os.environ.get("HOPE_NOTIFY_DEBUG")
    if val is not None:
        return val.lower() in ("1", "true", "yes", "on")

    if ENV_PATH.exists():
        try:
            with ENV_PATH.open(encoding="utf-8-sig") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    if line.startswith("HOPE_NOTIFY_DEBUG"):
                        _, v = line.split("=", 1)
                        v = v.strip().lower()
                        return v in ("1", "true", "yes", "on")
        except Exception:
            pass
    return False


def _is_debug_enabled() -> bool:
    """
    Основной флаг — DEBUG.flag.
    Если файла нет — смотрим HOPE_NOTIFY_DEBUG из окружения/.env.
    """
    if DEBUG_FLAG.exists():
        return True
    return _debug_from_env()


def emit_event(kind: str, payload: Dict[str, Any], text: Optional[str] = None) -> None:
    """
    Универсальная запись события в очередь.
    Для kind == "system_debug" уважает флаг DEBUG.
    """
    if kind == "system_debug" and not _is_debug_enabled():
        return

    ts = time.time()
    evt = WebhookEvent(
        ts=ts,
        ts_iso=datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
        kind=kind,
        payload=payload or {},
        text=text,
        attempts=0,
    )
    line = evt.to_line() + "\n"

    for attempt in range(3):
        try:
            with OUTBOX.open("a", encoding="utf-8") as f:
                f.write(line)
            return
        except Exception as e:
            if attempt < 2:
                time.sleep(0.05)
            else:
                log.error("emit_event FAILED for kind=%s: %s", kind, e)


# ==========================
# ХЕЛПЕРЫ ДЛЯ ТОРГОВЛИ
# ==========================

def notify_debug(message: str) -> None:
    """
    Debug-сообщение (kind == system_debug).
    Отправляется только если включён DEBUG.flag или HOPE_NOTIFY_DEBUG.
    """
    emit_event(
        "system_debug",
        {"msg": message},
        text=f"🐛 <b>Debug</b>\n{message}",
    )


def notify_trade_open(
    symbol: str,
    side: str,
    qty: float,
    entry: float,
    mode: str,
    risk: str,
    dry: bool,
    reason: str = "",
) -> None:
    base = symbol.replace("/", "")
    side_u = side.upper()
    emoji = "🟢" if side_u == "LONG" else "🔴"

    text = (
        f"{emoji} <b>OPEN {side_u} {base}</b>\n"
        f"Mode: <b>{mode}</b> | Risk: <b>{risk}</b> | <code>{'DRY' if dry else 'LIVE'}</code>\n"
        f"Qty: <code>{qty:.8f}</code>\n"
        f"Entry: <code>{entry:.4f}</code>"
    )
    if reason:
        text += f"\nReason: <i>{reason}</i>"

    emit_event(
        "trade_open",
        {
            "symbol": symbol,
            "side": side_u,
            "qty": qty,
            "entry": entry,
            "mode": mode,
            "risk": risk,
            "dry": dry,
            "reason": reason,
        },
        text=text,
    )


def notify_trade_close(
    symbol: str,
    side: str,
    qty: float,
    entry: float,
    exitp: float,
    pnl_abs: float,
    pnl_pct: float,
    mode: str,
    risk: str,
    dry: bool,
    reason: str,
) -> None:
    base = symbol.replace("/", "")
    side_u = side.upper()
    emoji = "✅" if pnl_abs >= 0 else "❌"

    text = (
        f"{emoji} <b>CLOSE {side_u} {base}</b>\n"
        f"PnL: <b>{pnl_abs:+.4f} USDT</b> ({pnl_pct:+.2f}%)\n"
        f"<code>{entry:.4f}</code> → <code>{exitp:.4f}</code>\n"
        f"Reason: <i>{reason}</i>\n"
        f"Mode: <b>{mode}</b> | Risk: <b>{risk}</b> | <code>{'DRY' if dry else 'LIVE'}</code>"
    )

    emit_event(
        "trade_close",
        {
            "symbol": symbol,
            "side": side_u,
            "qty": qty,
            "entry": entry,
            "exit": exitp,
            "pnl_abs": pnl_abs,
            "pnl_pct": pnl_pct,
            "mode": mode,
            "risk": risk,
            "dry": dry,
            "reason": reason,
        },
        text=text,
    )


def notify_daily_stop(day_pnl: float, limit: float, trades: int) -> None:
    text = (
        f"⛔ <b>DAILY STOP</b>\n"
        f"Day PnL: <b>{day_pnl:.4f} USDT</b>\n"
        f"Trades: <b>{trades}</b>\n"
        f"Limit: <b>{limit:.2f} USDT</b>"
    )
    emit_event(
        "daily_stop",
        {"day_pnl": day_pnl, "limit": limit, "trades": trades},
        text=text,
    )


def notify_system(message: str) -> None:
    emit_event(
        "system",
        {"msg": message},
        text=f"⚙️ <b>System</b>\n{message}",
    )
