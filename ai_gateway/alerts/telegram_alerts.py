# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 13:35:00 UTC
# Purpose: Telegram alerts for MFE/MAE thresholds
# === END SIGNATURE ===
"""
Telegram Alerts for HOPE AI Trading System.

Sends alerts when:
- MFE > threshold (opportunity)
- MAE < threshold (risk warning)
- Circuit breaker triggered
- System errors

Usage:
    from ai_gateway.alerts.telegram_alerts import AlertManager, get_alert_manager

    alert_mgr = get_alert_manager()
    await alert_mgr.send_mfe_alert(symbol="XVSUSDT", mfe=3.5, entry_price=3.55)
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from pathlib import Path

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class AlertConfig:
    """Alert configuration thresholds."""
    mfe_alert_pct: float = 2.0       # Alert when MFE > 2%
    mae_warning_pct: float = -1.0    # Warning when MAE < -1%
    mae_critical_pct: float = -2.0   # Critical when MAE < -2%
    circuit_breaker_pct: float = -3.0  # Stop trading when avg MAE < -3%
    cooldown_seconds: int = 300      # 5 min between same alerts
    max_alerts_per_hour: int = 20    # Rate limit


# ═══════════════════════════════════════════════════════════════════════════
# ALERT TYPES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Alert:
    """Single alert record."""
    alert_type: str  # mfe_high, mae_warning, mae_critical, circuit_breaker, error
    symbol: str
    message: str
    timestamp: float
    data: Dict[str, Any] = field(default_factory=dict)
    sent: bool = False


# ═══════════════════════════════════════════════════════════════════════════
# TELEGRAM CLIENT
# ═══════════════════════════════════════════════════════════════════════════

class TelegramClient:
    """Simple Telegram bot client."""

    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_ALERT_CHAT_IDS")
        self._enabled = bool(self.token and self.chat_id)

        if not self._enabled:
            logger.warning("Telegram alerts disabled: missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send message to Telegram chat."""
        if not self._enabled:
            logger.debug(f"[DRY] Would send: {text[:100]}...")
            return False

        try:
            import aiohttp

            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=10) as resp:
                    if resp.status == 200:
                        logger.info(f"Telegram alert sent: {text[:50]}...")
                        return True
                    else:
                        body = await resp.text()
                        logger.error(f"Telegram error {resp.status}: {body}")
                        return False

        except ImportError:
            logger.warning("aiohttp not installed, using sync requests")
            return self._send_sync(text, parse_mode)
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    def _send_sync(self, text: str, parse_mode: str = "HTML") -> bool:
        """Sync fallback for sending."""
        try:
            import requests

            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
            }

            resp = requests.post(url, json=payload, timeout=10)
            return resp.status_code == 200

        except Exception as e:
            logger.error(f"Telegram sync send failed: {e}")
            return False


# ═══════════════════════════════════════════════════════════════════════════
# ALERT MANAGER
# ═══════════════════════════════════════════════════════════════════════════

class AlertManager:
    """
    Manages trading alerts with rate limiting and cooldowns.

    Usage:
        mgr = AlertManager()
        await mgr.check_and_alert(symbol="XVSUSDT", mfe=3.5, mae=-0.5, entry_price=3.55)
    """

    def __init__(self, config: Optional[AlertConfig] = None):
        self.config = config or AlertConfig()
        self.telegram = TelegramClient()

        # Alert history for cooldowns and rate limiting
        self._alert_history: List[Alert] = []
        self._last_alert_time: Dict[str, float] = {}  # key: "{type}:{symbol}"

        logger.info(f"AlertManager initialized (telegram_enabled={self.telegram.enabled})")

    async def check_and_alert(
        self,
        symbol: str,
        mfe: float,
        mae: float,
        entry_price: float,
        current_price: Optional[float] = None,
    ) -> List[Alert]:
        """
        Check thresholds and send alerts if needed.

        Args:
            symbol: Trading pair
            mfe: Maximum Favorable Excursion %
            mae: Maximum Adverse Excursion %
            entry_price: Entry price
            current_price: Current price (optional)

        Returns:
            List of alerts sent
        """
        alerts = []

        # MFE alert (opportunity)
        if mfe >= self.config.mfe_alert_pct:
            alert = await self._send_mfe_alert(symbol, mfe, entry_price, current_price)
            if alert:
                alerts.append(alert)

        # MAE warning
        if mae <= self.config.mae_warning_pct:
            if mae <= self.config.mae_critical_pct:
                alert = await self._send_mae_critical(symbol, mae, entry_price, current_price)
            else:
                alert = await self._send_mae_warning(symbol, mae, entry_price, current_price)
            if alert:
                alerts.append(alert)

        return alerts

    async def check_circuit_breaker(self, avg_mae: float, active_signals: int) -> Optional[Alert]:
        """Check if circuit breaker should be triggered."""
        if avg_mae <= self.config.circuit_breaker_pct:
            return await self._send_circuit_breaker_alert(avg_mae, active_signals)
        return None

    async def send_system_error(self, error: str, module: str = "unknown") -> Optional[Alert]:
        """Send system error alert."""
        key = f"error:{module}"
        if not self._check_cooldown(key):
            return None

        alert = Alert(
            alert_type="error",
            symbol="SYSTEM",
            message=f"System error in {module}: {error}",
            timestamp=time.time(),
            data={"module": module, "error": error},
        )

        text = f"""🔴 <b>SYSTEM ERROR</b>

Module: <code>{module}</code>
Error: {error[:200]}

Time: {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"""

        alert.sent = await self.telegram.send_message(text)
        self._record_alert(alert, key)
        return alert

    async def _send_mfe_alert(
        self,
        symbol: str,
        mfe: float,
        entry_price: float,
        current_price: Optional[float],
    ) -> Optional[Alert]:
        """Send MFE opportunity alert."""
        key = f"mfe_high:{symbol}"
        if not self._check_cooldown(key):
            return None

        alert = Alert(
            alert_type="mfe_high",
            symbol=symbol,
            message=f"High MFE {mfe:.2f}% on {symbol}",
            timestamp=time.time(),
            data={"mfe": mfe, "entry_price": entry_price, "current_price": current_price},
        )

        price_info = f"Current: {current_price:.6f}" if current_price else ""

        text = f"""📈 <b>HIGH MFE ALERT</b>

Symbol: <b>{symbol}</b>
MFE: <b>+{mfe:.2f}%</b>
Entry: {entry_price:.6f}
{price_info}

Consider taking profit!"""

        alert.sent = await self.telegram.send_message(text)
        self._record_alert(alert, key)
        return alert

    async def _send_mae_warning(
        self,
        symbol: str,
        mae: float,
        entry_price: float,
        current_price: Optional[float],
    ) -> Optional[Alert]:
        """Send MAE warning alert."""
        key = f"mae_warning:{symbol}"
        if not self._check_cooldown(key):
            return None

        alert = Alert(
            alert_type="mae_warning",
            symbol=symbol,
            message=f"MAE warning {mae:.2f}% on {symbol}",
            timestamp=time.time(),
            data={"mae": mae, "entry_price": entry_price, "current_price": current_price},
        )

        price_info = f"Current: {current_price:.6f}" if current_price else ""

        text = f"""⚠️ <b>MAE WARNING</b>

Symbol: <b>{symbol}</b>
MAE: <b>{mae:.2f}%</b>
Entry: {entry_price:.6f}
{price_info}

Monitor closely!"""

        alert.sent = await self.telegram.send_message(text)
        self._record_alert(alert, key)
        return alert

    async def _send_mae_critical(
        self,
        symbol: str,
        mae: float,
        entry_price: float,
        current_price: Optional[float],
    ) -> Optional[Alert]:
        """Send MAE critical alert."""
        key = f"mae_critical:{symbol}"
        if not self._check_cooldown(key):
            return None

        alert = Alert(
            alert_type="mae_critical",
            symbol=symbol,
            message=f"CRITICAL MAE {mae:.2f}% on {symbol}",
            timestamp=time.time(),
            data={"mae": mae, "entry_price": entry_price, "current_price": current_price},
        )

        price_info = f"Current: {current_price:.6f}" if current_price else ""

        text = f"""🔴 <b>CRITICAL MAE</b>

Symbol: <b>{symbol}</b>
MAE: <b>{mae:.2f}%</b>
Entry: {entry_price:.6f}
{price_info}

Consider exit or stop-loss!"""

        alert.sent = await self.telegram.send_message(text)
        self._record_alert(alert, key)
        return alert

    async def _send_circuit_breaker_alert(
        self,
        avg_mae: float,
        active_signals: int,
    ) -> Optional[Alert]:
        """Send circuit breaker triggered alert."""
        key = "circuit_breaker:SYSTEM"
        if not self._check_cooldown(key, cooldown=600):  # 10 min cooldown
            return None

        alert = Alert(
            alert_type="circuit_breaker",
            symbol="SYSTEM",
            message=f"Circuit breaker triggered: avg MAE {avg_mae:.2f}%",
            timestamp=time.time(),
            data={"avg_mae": avg_mae, "active_signals": active_signals},
        )

        text = f"""🛑 <b>CIRCUIT BREAKER TRIGGERED</b>

Avg MAE: <b>{avg_mae:.2f}%</b>
Active Signals: {active_signals}
Threshold: {self.config.circuit_breaker_pct}%

Trading PAUSED until conditions improve."""

        alert.sent = await self.telegram.send_message(text)
        self._record_alert(alert, key)
        return alert

    def _check_cooldown(self, key: str, cooldown: Optional[int] = None) -> bool:
        """Check if alert is within cooldown period."""
        cooldown = cooldown or self.config.cooldown_seconds
        last_time = self._last_alert_time.get(key, 0)
        return (time.time() - last_time) >= cooldown

    def _check_rate_limit(self) -> bool:
        """Check if within hourly rate limit."""
        cutoff = time.time() - 3600
        recent = [a for a in self._alert_history if a.timestamp > cutoff]
        return len(recent) < self.config.max_alerts_per_hour

    def _record_alert(self, alert: Alert, key: str) -> None:
        """Record alert for cooldown and history."""
        self._last_alert_time[key] = time.time()
        self._alert_history.append(alert)

        # Prune old history (keep last 100)
        if len(self._alert_history) > 100:
            self._alert_history = self._alert_history[-100:]

    def get_stats(self) -> Dict[str, Any]:
        """Get alert statistics."""
        cutoff = time.time() - 3600
        recent = [a for a in self._alert_history if a.timestamp > cutoff]

        return {
            "total_alerts": len(self._alert_history),
            "alerts_last_hour": len(recent),
            "telegram_enabled": self.telegram.enabled,
            "by_type": {
                "mfe_high": len([a for a in recent if a.alert_type == "mfe_high"]),
                "mae_warning": len([a for a in recent if a.alert_type == "mae_warning"]),
                "mae_critical": len([a for a in recent if a.alert_type == "mae_critical"]),
                "circuit_breaker": len([a for a in recent if a.alert_type == "circuit_breaker"]),
                "error": len([a for a in recent if a.alert_type == "error"]),
            },
        }


# ═══════════════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

_alert_manager: Optional[AlertManager] = None


def get_alert_manager() -> AlertManager:
    """Get or create singleton AlertManager."""
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = AlertManager()
    return _alert_manager
