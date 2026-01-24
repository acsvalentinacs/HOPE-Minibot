"""
minibot/notifier.py

HOPE Trading Bot - Telegram Notifier
-----------------------------------

Лёгкий синхронный модуль для отправки уведомлений в Telegram
из live-логики (minibot, live_manager, watchdog и т.п.).

Особенности:
- НЕ трогает C:/secrets/hope/.env, только читает переменные окружения.
- По умолчанию использует TELEGRAM_TOKEN_MINI / TELEGRAM_TOKEN
  и TELEGRAM_ALLOWED / TELEGRAM_ALERT_CHAT_IDS.
- Минимальные зависимости: только requests.
- Форматирует сообщения в HTML (parse_mode="HTML").
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any

import requests

log = logging.getLogger("notifier")


# ===== Вспомогательные структуры данных =====


@dataclass
class TradeEvent:
    """Событие открытия/закрытия сделки."""

    symbol: str
    side: str           # "long" / "short"
    entry: float
    sl: float
    tp: float
    risk_usd: float
    exit_price: Optional[float] = None
    pnl_usd: Optional[float] = None
    pnl_r: Optional[float] = None
    reason: str = ""
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    def ensure_times(self) -> None:
        """Гарантирует, что timestamps заполнены."""
        now = datetime.utcnow()
        if self.opened_at is None:
            self.opened_at = now
        if self.closed_at is None and self.exit_price is not None:
            self.closed_at = now


@dataclass
class LimitHitEvent:
    """Событие срабатывания лимита по символу или глобально."""

    symbol: str
    limit_type: str         # "daily_loss_r", "max_trades", "global_daily_stop" и т.п.
    current_value: float
    max_value: float
    accumulated_r: float
    trades_count: int
    timestamp: Optional[datetime] = None

    def ensure_time(self) -> None:
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()


# ===== Основной класс Notifier =====


class TelegramNotifier:
    """
    Синхронный отправитель уведомлений в Telegram.

    Использование (по умолчанию берём настройки из окружения):

        notifier = TelegramNotifier()  # env-based
        notifier.send_text("Бот запущен 🚀")

        notifier.notify_limit_hit(...)
        notifier.notify_trade_open(...)
        notifier.notify_trade_close(...)
        notifier.notify_status(...)

    Переменные окружения (уже есть в проекте HOPE):
        TELEGRAM_TOKEN_MINI   - токен бота (tg_bot_simple)
        TELEGRAM_TOKEN        - fallback, если MINI нет
        TELEGRAM_ALLOWED      - ID чата (или список через запятую)
        TELEGRAM_ALERT_CHAT_IDS - альтернативный список чатов
    """

    def __init__(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
        enabled: bool = True,
        timeout: float = 5.0,
    ) -> None:
        # 1. Определяем токен и чат из явных параметров или окружения
        env_token = (
            token
            or os.getenv("TELEGRAM_TOKEN_MINI")
            or os.getenv("TELEGRAM_TOKEN")
        )

        env_chat = (
            chat_id
            or os.getenv("TELEGRAM_ALLOWED")
            or os.getenv("TELEGRAM_ALERT_CHAT_IDS")
        )

        if env_chat:
            # Если в TELEGRAM_ALLOWED несколько ID через запятую — берём первый
            env_chat = str(env_chat).split(",")[0].strip()

        self.token: Optional[str] = env_token
        self.chat_id: Optional[str] = env_chat
        self.timeout: float = timeout

        # enabled = True И есть токен/чат → реально включён
        self.enabled: bool = bool(enabled and self.token and self.chat_id)

        if not self.enabled:
            log.warning(
                "TelegramNotifier: disabled (нет токена или chat_id). "
                "Проверь TELEGRAM_TOKEN_MINI / TELEGRAM_ALLOWED."
            )

    # ----- Базовый низкоуровневый метод -----

    def _send_raw(
        self,
        text: str,
        silent: bool = False,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Низкоуровневая отправка сообщения в Telegram."""
        if not self.enabled:
            return

        if not self.token or not self.chat_id:
            # На случай, если состояние поменялось после __init__
            log.warning("TelegramNotifier: no token/chat_id at send_raw()")
            return

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"

        payload: Dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": bool(silent),
        }

        if extra:
            payload.update(extra)

        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            if resp.status_code != 200:
                # Не падаем, просто логируем
                log.warning(
                    "TelegramNotifier: sendMessage status %s, body: %s",
                    resp.status_code,
                    resp.text[:500],
                )
        except Exception as e:
            log.error("TelegramNotifier: exception while sending message: %s", e, exc_info=True)

    # ----- Удобные публичные методы -----

    def send_text(self, text: str, silent: bool = False) -> None:
        """Простое текстовое сообщение (debug / info)."""
        self._send_raw(text, silent=silent)

    def notify_info(self, title: str, message: str, silent: bool = False) -> None:
        """Информационное сообщение."""
        text = f"ℹ️ <b>{self._escape(title)}</b>\n\n{self._escape(message)}"
        self._send_raw(text, silent=silent)

    def notify_error(self, context: str, error_msg: str) -> None:
        """Критическая ошибка (без трейсбэка, он будет в логах)."""
        text = (
            "❌ <b>CRITICAL ERROR</b>\n\n"
            f"<b>Context:</b> {self._escape(context)}\n"
            f"<b>Message:</b> {self._escape(error_msg)}\n\n"
            "<i>Подробнее смотри в логах HOPE.</i>"
        )
        self._send_raw(text, silent=False)

    # --- События по сделкам ---

    def notify_trade_open(self, event: TradeEvent, is_paper: bool = False) -> None:
        """Уведомление об открытии сделки."""
        event.ensure_times()

        side = event.side.lower()
        side_text = "LONG" if side in ("long", "buy") else "SHORT"
        side_emoji = "📈" if side_text == "LONG" else "📉"
        mode_emoji = "⚠️ [PAPER]" if is_paper else "🔥 [REAL]"

        text = (
            f"🟢 <b>TRADE OPEN</b> {mode_emoji}\n\n"
            f"<b>Symbol:</b> {self._escape(event.symbol)}\n"
            f"<b>Side:</b> {side_text} {side_emoji}\n"
            f"<b>Entry:</b> ${event.entry:,.2f}\n"
            f"<b>SL:</b> ${event.sl:,.2f}\n"
            f"<b>TP:</b> ${event.tp:,.2f}\n"
            f"<b>Risk:</b> ${event.risk_usd:,.2f}\n\n"
            f"<i>{event.opened_at.strftime('%Y-%m-%d %H:%M:%S UTC')}</i>"
        )

        self._send_raw(text, silent=is_paper)

    def notify_trade_close(self, event: TradeEvent, is_paper: bool = False) -> None:
        """Уведомление о закрытии сделки."""
        event.ensure_times()

        side = event.side.lower()
        side_text = "LONG" if side in ("long", "buy") else "SHORT"

        pnl_usd = event.pnl_usd if event.pnl_usd is not None else 0.0
        pnl_r = event.pnl_r if event.pnl_r is not None else 0.0

        pnl_emoji = "💰" if pnl_usd >= 0 else "💸"
        mode_emoji = "⚠️ [PAPER]" if is_paper else "🔥 [REAL]"

        duration_str = ""
        if event.opened_at and event.closed_at:
            minutes = int((event.closed_at - event.opened_at).total_seconds() // 60)
            duration_str = f"\n<b>Duration:</b> {minutes} min"

        text = (
            f"🔴 <b>TRADE CLOSE</b> {mode_emoji}\n\n"
            f"<b>Symbol:</b> {self._escape(event.symbol)}\n"
            f"<b>Side:</b> {side_text}\n"
            f"<b>Entry:</b> ${event.entry:,.2f}\n"
            f"<b>Exit:</b> ${event.exit_price:,.2f}\n"
            f"<b>Reason:</b> {self._escape(event.reason or 'N/A')}"
            f"{duration_str}\n\n"
            f"<b>PnL:</b> {pnl_emoji} ${pnl_usd:+.2f} ({pnl_r:+.2f}R)\n"
            f"<i>{event.closed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}</i>"
        )

        self._send_raw(text, silent=is_paper)

    # --- Лимиты и статус ---

    def notify_limit_hit(self, event: LimitHitEvent) -> None:
        """Срабатывание лимита по символу / глобального лимита."""
        event.ensure_time()

        pretty_name = {
            "daily_loss_r": "Daily Loss Limit (R)",
            "max_trades": "Max Trades Per Day",
            "global_daily_stop": "Global Daily Stop (USD)",
        }.get(event.limit_type, event.limit_type)

        text = (
            "🚨 <b>TRADING STOPPED</b>\n\n"
            f"<b>Symbol:</b> {self._escape(event.symbol)}\n"
            f"<b>Limit:</b> {self._escape(pretty_name)}\n"
            f"<b>Current:</b> {event.current_value:.2f}\n"
            f"<b>Max:</b> {event.max_value:.2f}\n\n"
            f"<b>Today:</b>\n"
            f"  • Accumulated R: {event.accumulated_r:+.2f}\n"
            f"  • Trades: {event.trades_count}\n\n"
            "<i>Торговля по этому символу остановлена до смены суток.</i>\n"
            f"<i>{event.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}</i>"
        )

        self._send_raw(text, silent=False)

    def notify_status(self, status: Dict[str, Any]) -> None:
        """
        Уведомление о статусе бота.
        Ожидается словарь формата, который мы позже сделаем в live_manager.get_status().
        """
        env = status.get("environment", "unknown")
        version = status.get("bot_version", "N/A")
        uptime_sec = int(status.get("uptime_seconds", 0))
        uptime_min = uptime_sec // 60

        balance = status.get("global_balance", 0.0)
        pnl_today = status.get("global_pnl_today", 0.0)
        last_update = status.get("last_update", "N/A")

        symbols = status.get("symbols", {})

        lines = []
        for symbol, info in symbols.items():
            mode = info.get("mode", "?")
            day_r = info.get("day_r", 0.0)
            trades = info.get("trades_today", 0)
            enabled = info.get("trading_enabled", True)
            flag = "✅" if enabled else "❌"
            lines.append(
                f"{flag} <b>{self._escape(symbol)}</b> "
                f"({self._escape(mode)}): {day_r:+.2f}R, {trades} trades"
            )

        symbols_block = "\n".join(lines) if lines else "нет активных символов"

        text = (
            "📊 <b>HOPE BOT STATUS</b>\n\n"
            f"<b>Version:</b> {self._escape(version)}\n"
            f"<b>Env:</b> {self._escape(env)}\n"
            f"<b>Uptime:</b> {uptime_min} min\n\n"
            f"<b>Global balance:</b> ${balance:,.2f}\n"
            f"<b>PnL today:</b> {pnl_today:+.2f}$\n\n"
            f"<b>Symbols:</b>\n{symbols_block}\n\n"
            f"<i>Last update:</i> {self._escape(last_update)}"
        )

        self._send_raw(text, silent=True)

    # ===== Вспомогательные методы =====

    @staticmethod
    def _escape(text: Any) -> str:
        """Экранируем спецсимволы для HTML-parse_mode."""
        s = str(text)
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    n = TelegramNotifier()
    n.send_text("Test from notifier.py 🚀")
