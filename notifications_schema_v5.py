from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional

# Корень проекта: ...\TradingBot\minibot\notifications_schema_v5.py → ROOT_DIR = ...\TradingBot
ROOT_DIR = Path(__file__).resolve().parents[1]
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

NOTIFICATIONS_FILE = LOGS_DIR / "notifications_v5.jsonl"
_WRITE_LOCK = Lock()


class NotificationType(str, Enum):
    INFO = "INFO"
    TRADE_OPEN = "TRADE_OPEN"
    TRADE_CLOSE = "TRADE_CLOSE"
    RISK_BLOCK = "RISK_BLOCK"


@dataclass
class NotificationV5:
    ts: float
    type: str
    text: str

    symbol: Optional[str] = None
    side: Optional[str] = None
    price: Optional[float] = None
    qty: Optional[float] = None
    pnl: Optional[float] = None
    mode: Optional[str] = None

    reason_code: Optional[str] = None
    source: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None


def _side_name(side: Any) -> Optional[str]:
    if side is None:
        return None
    if hasattr(side, "name"):
        return side.name
    return str(side)


def _mode_name(mode: Any) -> Optional[str]:
    if mode is None:
        return None
    if hasattr(mode, "name"):
        return mode.name
    return str(mode)


def append_notification(notification: NotificationV5 | Dict[str, Any]) -> None:
    """
    Универсальная запись нотификации в logs/notifications_v5.jsonl (JSONL-формат).
    """
    if isinstance(notification, NotificationV5):
        payload = asdict(notification)
    else:
        payload = dict(notification)

    # Если ts не задан — ставим сейчас
    payload.setdefault("ts", time.time())

    with _WRITE_LOCK:
        with NOTIFICATIONS_FILE.open("a", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.write("\n")


def build_info_notification(
    *,
    text: Optional[str] = None,
    message: Optional[str] = None,   # ← поддерживаем старые вызовы message=
    level: str = "INFO",
    tag: Optional[str] = None,
    source: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> NotificationV5:
    """
    Информационное сообщение (STOP.flag, блокировка торговли, технические события).
    Совместимо как с text=, так и с message=.
    """
    if not text:
        text = message or ""

    full_extra: Dict[str, Any] = dict(extra or {})
    full_extra.setdefault("level", level)
    if tag:
        full_extra.setdefault("tag", tag)

    # Прокидываем прочие параметры в extra, чтобы ничего не терять
    for k, v in kwargs.items():
        if k not in {"text", "message"}:
            full_extra[k] = v

    return NotificationV5(
        ts=time.time(),
        type=NotificationType.INFO.value,
        text=text,
        source=source,
        extra=full_extra,
    )


def build_open_notification(
    *,
    symbol: str,
    side,
    price: float,
    qty: float,
    mode,
    reason: Optional[str] = None,
    source: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> NotificationV5:
    """
    Открытие позиции.
    Совместима с вызовами вида:
    build_open_notification(symbol=..., side="LONG" или TradeSide.LONG, price=..., qty=..., mode=self.mode.name, reason=...)
    """
    # P0.1: Strict validation (keyword-only API)
    if not symbol or str(symbol).strip() == '':
        raise ValueError('build_open_notification:  symbol is required')
    if side is None:
        raise ValueError('build_open_notification:  side is required')
    if mode is None:
        raise ValueError('build_open_notification: mode is required')
    if price is None:
        raise ValueError('build_open_notification: price is required')
    if qty is None:
        raise ValueError('build_open_notification:  qty is required')

    s_name = _side_name(side)
    m_name = _mode_name(mode)

    base = f"🚀 OPEN {symbol} {s_name} @ {price:.4f} x {qty:.4f} • {m_name}"
    if reason:
        base += f" • {reason}"

    full_extra: Dict[str, Any] = dict(extra or {})
    for k, v in kwargs.items():
        if k not in {"symbol", "side", "price", "qty", "mode", "reason", "source", "extra"}:
            full_extra[k] = v

    return NotificationV5(
        ts=time.time(),
        type=NotificationType.TRADE_OPEN.value,
        text=base,
        symbol=symbol,
        side=s_name,
        price=price,
        qty=qty,
        mode=m_name,
        reason_code=reason,
        source=source,
        extra=full_extra,
    )


def build_close_notification(
    *,
    symbol: str,
    side: Any,
    price: float,
    qty: float,
    pnl: float,
    mode: Any,
    reason: Optional[str] = None,
    source: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> NotificationV5:
    """
    Закрытие позиции.
    Исправляет баг 'str' object has no attribute 'name' (корректно работает и со строкой, и с enum).
    """
    s_name = _side_name(side)
    m_name = _mode_name(mode)

    direction = "✅ PROFIT" if pnl >= 0 else "❌ LOSS"
    base = f"💰 CLOSE {symbol} {s_name} @ {price:.4f} x {qty:.4f} • PnL: {pnl:.2f} • {direction} • {m_name}"
    if reason:
        base += f" • {reason}"

    full_extra: Dict[str, Any] = dict(extra or {})
    for k, v in kwargs.items():
        if k not in {"symbol", "side", "price", "qty", "pnl", "mode", "reason", "source", "extra"}:
            full_extra[k] = v

    return NotificationV5(
        ts=time.time(),
        type=NotificationType.TRADE_CLOSE.value,
        text=base,
        symbol=symbol,
        side=s_name,
        price=price,
        qty=qty,
        pnl=pnl,
        mode=m_name,
        reason_code=reason,
        source=source,
        extra=full_extra,
    )


def build_risk_block_notification(
    *,
    symbol: Optional[str] = None,
    reason: str,
    source: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> NotificationV5:
    """
    Отдельный тип для блокировок RiskManager (если будешь использовать).
    """
    text = f"⛔ Торговля заблокирована: {reason}"
    if symbol:
        text += f" • {symbol}"

    return NotificationV5(
        ts=time.time(),
        type=NotificationType.RISK_BLOCK.value,
        text=text,
        symbol=symbol,
        source=source,
        extra=extra,
    )
# HOPE_HOTFIX_FINAL: build_open_notification accepts *args, **kwargs