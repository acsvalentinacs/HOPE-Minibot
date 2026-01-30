# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 14:20:00 UTC
# Modified by: Claude (opus-4.5)
# Modified at: 2026-01-30 18:30:00 UTC
# Purpose: Telegram Sender-Only client + EGRESS GATE (delta >= 10%)
# === END SIGNATURE ===
"""
Telegram Sender-Only Client.

Решает проблему:
    telegram.error.Conflict: terminated by other getUpdates request

Этот модуль ТОЛЬКО ОТПРАВЛЯЕТ сообщения через Telegram Bot API.
Он НЕ использует polling/webhook, поэтому не конфликтует с ботом на сервере.

Usage:
    from core.telegram_sender import TelegramSender

    sender = TelegramSender()
    await sender.send("Trade executed: BUY BTCUSDT @ 85000")
    await sender.send_alert("Circuit breaker tripped!")
"""

import os
import asyncio
import logging
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

try:
    import httpx
except ImportError:
    httpx = None

log = logging.getLogger("TG-SENDER")

# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  TELEGRAM EGRESS GATE - BLOCKS ALL PUMP MESSAGES WITH delta < 10%        ║
# ║  This is the LAST LINE OF DEFENSE - nothing below 10% gets through!      ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
import re
import os

_TG_MIN_DELTA = float(os.environ.get("TG_MIN_DELTA", "10.0"))
_TG_BLOCK_TYPES = {"MICRO", "TEST_ACTIVITY", "SCALP", "VOLUME_SPIKE"}

_delta_re = re.compile(r"Delta:\s*([-+]?\d+(?:\.\d+)?)%")
_type_re = re.compile(r"Type:\s*([A-Z_]+)")


def _egress_gate_check(text: str) -> tuple:
    """
    HARD GATE for Telegram messages.

    Returns: (allowed: bool, reason: str)

    Rules:
    - If message contains "PUMP:" and "Delta:" - check delta >= 10%
    - Block MICRO, TEST_ACTIVITY, SCALP, VOLUME_SPIKE types
    - Allow all other messages (status, alerts, etc.)
    """
    if not text:
        return True, "empty"

    # Only filter PUMP messages - allow status/alert/other messages
    if "PUMP" not in text and "Delta:" not in text:
        return True, "non-pump"

    # Check blocked signal types
    m_type = _type_re.search(text)
    if m_type:
        signal_type = m_type.group(1).strip().upper()
        if signal_type in _TG_BLOCK_TYPES:
            return False, f"blocked_type:{signal_type}"

    # Check delta threshold
    m_delta = _delta_re.search(text)
    if m_delta:
        try:
            delta = float(m_delta.group(1))
        except ValueError:
            return False, "delta_parse_fail"

        if delta < _TG_MIN_DELTA:
            return False, f"delta:{delta:.2f}%<{_TG_MIN_DELTA}%"
        return True, f"delta_ok:{delta:.2f}%"

    # PUMP message without Delta = FAIL-CLOSED (block it)
    if "PUMP" in text:
        return False, "pump_without_delta"

    return True, "pass"
# ╚═══════════════════════════════════════════════════════════════════════════╝


@dataclass
class TelegramConfig:
    """Telegram sender configuration."""
    token: str
    chat_id: str
    api_base: str = "https://api.telegram.org"
    timeout_sec: float = 10.0
    parse_mode: str = "HTML"


class TelegramSender:
    """
    Sender-only Telegram client.

    Does NOT use polling - only sends messages via Bot API.
    Safe to run alongside another bot instance.
    """

    def __init__(self, config: Optional[TelegramConfig] = None):
        """
        Initialize sender.

        Args:
            config: TelegramConfig or None to load from environment
        """
        if config:
            self.config = config
        else:
            self.config = self._load_from_env()

        self._client: Optional[httpx.AsyncClient] = None
        self._sync_client: Optional[httpx.Client] = None

    def _load_from_env(self) -> TelegramConfig:
        """Load config from environment variables."""
        # Try multiple env var names
        token = (
            os.getenv("TELEGRAM_BOT_TOKEN") or
            os.getenv("TG_BOT_TOKEN") or
            os.getenv("HOPE_TG_TOKEN")
        )

        chat_id = (
            os.getenv("TELEGRAM_CHAT_ID") or
            os.getenv("TG_CHAT_ID") or
            os.getenv("HOPE_ADMIN_ID") or
            os.getenv("TG_ADMIN_ID")
        )

        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN not found in environment")
        if not chat_id:
            raise ValueError("TELEGRAM_CHAT_ID not found in environment")

        return TelegramConfig(token=token, chat_id=chat_id)

    @property
    def _api_url(self) -> str:
        return f"{self.config.api_base}/bot{self.config.token}"

    async def _get_client(self) -> "httpx.AsyncClient":
        """Get or create async HTTP client."""
        if httpx is None:
            raise ImportError("httpx not installed. Run: pip install httpx")

        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.config.timeout_sec)
        return self._client

    def _get_sync_client(self) -> "httpx.Client":
        """Get or create sync HTTP client."""
        if httpx is None:
            raise ImportError("httpx not installed. Run: pip install httpx")

        if self._sync_client is None or self._sync_client.is_closed:
            self._sync_client = httpx.Client(timeout=self.config.timeout_sec)
        return self._sync_client

    async def send(
        self,
        text: str,
        chat_id: Optional[str] = None,
        parse_mode: Optional[str] = None,
        disable_notification: bool = False,
    ) -> bool:
        """
        Send message asynchronously.

        Args:
            text: Message text
            chat_id: Override chat ID
            parse_mode: Override parse mode (HTML/Markdown)
            disable_notification: Send silently

        Returns:
            True if sent successfully
        """
        # === EGRESS GATE: Block spam BEFORE sending ===
        allowed, reason = _egress_gate_check(text)
        if not allowed:
            log.info(f"[EGRESS-BLOCK] {reason}")
            return False  # Don't send, but don't raise error
        # ================================================

        try:
            client = await self._get_client()

            payload = {
                "chat_id": chat_id or self.config.chat_id,
                "text": text,
                "parse_mode": parse_mode or self.config.parse_mode,
                "disable_notification": disable_notification,
            }

            response = await client.post(
                f"{self._api_url}/sendMessage",
                json=payload,
            )

            if response.status_code == 200:
                return True
            else:
                log.error(f"Telegram send failed: {response.status_code} {response.text}")
                return False

        except Exception as e:
            log.error(f"Telegram send error: {e}")
            return False

    def send_sync(
        self,
        text: str,
        chat_id: Optional[str] = None,
        parse_mode: Optional[str] = None,
    ) -> bool:
        """
        Send message synchronously (blocking).

        Use this in non-async contexts.
        """
        # === EGRESS GATE: Block spam BEFORE sending ===
        allowed, reason = _egress_gate_check(text)
        if not allowed:
            log.info(f"[EGRESS-BLOCK] {reason}")
            return False  # Don't send, but don't raise error
        # ================================================

        try:
            client = self._get_sync_client()

            payload = {
                "chat_id": chat_id or self.config.chat_id,
                "text": text,
                "parse_mode": parse_mode or self.config.parse_mode,
            }

            response = client.post(
                f"{self._api_url}/sendMessage",
                json=payload,
            )

            return response.status_code == 200

        except Exception as e:
            log.error(f"Telegram sync send error: {e}")
            return False

    async def send_alert(self, text: str, prefix: str = "⚠️ ALERT") -> bool:
        """Send alert with notification."""
        return await self.send(f"{prefix}\n\n{text}", disable_notification=False)

    async def send_trade(
        self,
        action: str,
        symbol: str,
        price: float,
        amount: float,
        pnl: Optional[float] = None,
    ) -> bool:
        """Send trade notification."""
        emoji = "🟢" if action == "BUY" else "🔴"

        lines = [
            f"{emoji} <b>{action} {symbol}</b>",
            f"Price: ${price:,.2f}",
            f"Amount: ${amount:.2f}",
        ]

        if pnl is not None:
            pnl_emoji = "✅" if pnl >= 0 else "❌"
            lines.append(f"P&L: {pnl_emoji} ${pnl:+.2f}")

        return await self.send("\n".join(lines))

    async def send_status(self, status: dict) -> bool:
        """Send system status update."""
        lines = [
            "<b>HOPE STATUS</b>",
            "",
            f"Mode: {status.get('mode', 'UNKNOWN')}",
            f"Positions: {status.get('positions', 0)}/{status.get('max_positions', 3)}",
            f"Daily P&L: ${status.get('daily_pnl', 0):+.2f}",
            f"Signals: {status.get('signals_today', 0)}",
        ]

        return await self.send("\n".join(lines), disable_notification=True)

    async def close(self):
        """Close HTTP clients."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        if self._sync_client and not self._sync_client.is_closed:
            self._sync_client.close()


# Singleton instance
_sender: Optional[TelegramSender] = None


def get_sender() -> TelegramSender:
    """Get singleton TelegramSender instance."""
    global _sender
    if _sender is None:
        _sender = TelegramSender()
    return _sender


async def send_telegram(text: str) -> bool:
    """Convenience function to send message."""
    return await get_sender().send(text)


def send_telegram_sync(text: str) -> bool:
    """Convenience function to send message synchronously."""
    return get_sender().send_sync(text)


# === CLI ===

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        message = " ".join(sys.argv[1:])

        async def main():
            sender = TelegramSender()
            success = await sender.send(message)
            print(f"Sent: {success}")
            await sender.close()

        asyncio.run(main())
    else:
        print("Usage: python telegram_sender.py <message>")
        print("\nThis is a sender-only client that does NOT conflict with polling bots.")
