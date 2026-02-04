# === AI SIGNATURE ===
# Module: hope_core/alerts.py
# Created by: Claude (opus-4.5)
# Created at: 2026-02-04 11:20:00 UTC
# Purpose: Telegram and other alerting for HOPE Core
# === END SIGNATURE ===
"""
HOPE Core - Alerting System

Sends alerts via:
- Telegram
- Console (fallback)
- Log file

Alert types:
- CRITICAL: System failures, emergency stops
- WARNING: Degraded state, high losses
- INFO: Trades, position updates
"""

from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import threading
import asyncio
import json
import os

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False


# =============================================================================
# ALERT TYPES
# =============================================================================

class AlertLevel(Enum):
    """Alert severity levels."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AlertType(Enum):
    """Alert categories."""
    SYSTEM = "SYSTEM"           # System events
    TRADE = "TRADE"             # Trade events
    POSITION = "POSITION"       # Position events
    HEALTH = "HEALTH"           # Health alerts
    GUARDIAN = "GUARDIAN"       # Guardian events


# =============================================================================
# ALERT DATACLASS
# =============================================================================

@dataclass
class Alert:
    """Single alert."""
    level: AlertLevel
    type: AlertType
    title: str
    message: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    data: Dict[str, Any] = field(default_factory=dict)
    
    def to_telegram_message(self) -> str:
        """Format for Telegram."""
        emoji = {
            AlertLevel.DEBUG: "🔧",
            AlertLevel.INFO: "ℹ️",
            AlertLevel.WARNING: "⚠️",
            AlertLevel.CRITICAL: "🚨",
        }.get(self.level, "📢")
        
        lines = [
            f"{emoji} *{self.title}*",
            "",
            self.message,
        ]
        
        if self.data:
            lines.append("")
            lines.append("```")
            for k, v in self.data.items():
                lines.append(f"{k}: {v}")
            lines.append("```")
        
        lines.append(f"\n_{self.timestamp.strftime('%H:%M:%S UTC')}_")
        
        return "\n".join(lines)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "level": self.level.value,
            "type": self.type.value,
            "title": self.title,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "data": self.data,
        }


# =============================================================================
# TELEGRAM SENDER
# =============================================================================

class TelegramSender:
    """
    Sends messages to Telegram.
    """
    
    API_URL = "https://api.telegram.org/bot{token}/sendMessage"
    
    def __init__(self, bot_token: str, chat_id: str):
        """
        Initialize sender.
        
        Args:
            bot_token: Telegram bot token
            chat_id: Target chat ID
        """
        self._token = bot_token
        self._chat_id = chat_id
        self._enabled = bool(bot_token and chat_id)
        self._sent_count = 0
        self._error_count = 0
    
    @property
    def is_enabled(self) -> bool:
        """Check if Telegram is enabled."""
        return self._enabled
    
    async def send(self, text: str, parse_mode: str = "Markdown") -> bool:
        """
        Send message to Telegram.
        
        Args:
            text: Message text
            parse_mode: Parse mode (Markdown/HTML)
            
        Returns:
            True if sent successfully
        """
        if not self._enabled:
            print(f"[TELEGRAM] (disabled) {text[:100]}...")
            return False
        
        if not HAS_AIOHTTP:
            print(f"[TELEGRAM] aiohttp not available: {text[:100]}...")
            return False
        
        url = self.API_URL.format(token=self._token)
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        self._sent_count += 1
                        return True
                    else:
                        self._error_count += 1
                        error = await resp.text()
                        print(f"[TELEGRAM] Error {resp.status}: {error}")
                        return False
        except Exception as e:
            self._error_count += 1
            print(f"[TELEGRAM] Send error: {e}")
            return False
    
    def get_stats(self) -> Dict[str, int]:
        """Get send statistics."""
        return {
            "sent": self._sent_count,
            "errors": self._error_count,
            "enabled": self._enabled,
        }


# =============================================================================
# ALERT MANAGER
# =============================================================================

class AlertManager:
    """
    Manages all alerts for HOPE Core.
    
    Features:
    - Multiple channels (Telegram, console, file)
    - Rate limiting
    - Alert history
    - Filtering by level/type
    """
    
    def __init__(
        self,
        telegram_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        log_file: Optional[Path] = None,
        min_level: AlertLevel = AlertLevel.INFO,
    ):
        """
        Initialize alert manager.
        
        Args:
            telegram_token: Telegram bot token
            telegram_chat_id: Telegram chat ID
            log_file: Path to log file
            min_level: Minimum alert level to process
        """
        self._telegram = TelegramSender(telegram_token or "", telegram_chat_id or "")
        self._log_file = log_file
        self._min_level = min_level
        self._lock = threading.Lock()
        
        # History
        self._history: List[Alert] = []
        self._max_history = 1000
        
        # Rate limiting (per type)
        self._last_sent: Dict[str, datetime] = {}
        self._rate_limit_seconds = 60  # 1 alert per type per minute
        
        # Callbacks
        self._callbacks: List[Callable[[Alert], None]] = []
    
    def add_callback(self, callback: Callable[[Alert], None]):
        """Add callback for alerts."""
        self._callbacks.append(callback)
    
    def _should_send(self, alert: Alert) -> bool:
        """Check if alert should be sent (rate limiting)."""
        # Always send CRITICAL
        if alert.level == AlertLevel.CRITICAL:
            return True
        
        # Check level
        levels = list(AlertLevel)
        if levels.index(alert.level) < levels.index(self._min_level):
            return False
        
        # Check rate limit
        key = f"{alert.type.value}:{alert.level.value}"
        last = self._last_sent.get(key)
        
        if last:
            elapsed = (datetime.now(timezone.utc) - last).total_seconds()
            if elapsed < self._rate_limit_seconds:
                return False
        
        self._last_sent[key] = datetime.now(timezone.utc)
        return True
    
    async def send(self, alert: Alert) -> bool:
        """
        Send alert through all channels.
        
        Args:
            alert: Alert to send
            
        Returns:
            True if sent to at least one channel
        """
        # Store in history
        with self._lock:
            self._history.append(alert)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]
        
        # Check rate limiting
        if not self._should_send(alert):
            return False
        
        sent = False
        
        # Console
        level_colors = {
            AlertLevel.DEBUG: "\033[90m",    # Gray
            AlertLevel.INFO: "\033[94m",     # Blue
            AlertLevel.WARNING: "\033[93m",  # Yellow
            AlertLevel.CRITICAL: "\033[91m", # Red
        }
        reset = "\033[0m"
        color = level_colors.get(alert.level, "")
        print(f"{color}[ALERT:{alert.level.value}] {alert.title}: {alert.message}{reset}")
        sent = True
        
        # Telegram
        if self._telegram.is_enabled:
            tg_sent = await self._telegram.send(alert.to_telegram_message())
            sent = sent or tg_sent
        
        # Log file
        if self._log_file:
            try:
                with open(self._log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(alert.to_dict()) + "\n")
                sent = True
            except Exception as e:
                print(f"[ALERT] Log file error: {e}")
        
        # Callbacks
        for callback in self._callbacks:
            try:
                callback(alert)
            except Exception as e:
                print(f"[ALERT] Callback error: {e}")
        
        return sent
    
    # =========================================================================
    # CONVENIENCE METHODS
    # =========================================================================
    
    async def critical(self, title: str, message: str, **data):
        """Send CRITICAL alert."""
        await self.send(Alert(
            level=AlertLevel.CRITICAL,
            type=AlertType.SYSTEM,
            title=title,
            message=message,
            data=data,
        ))
    
    async def warning(self, title: str, message: str, **data):
        """Send WARNING alert."""
        await self.send(Alert(
            level=AlertLevel.WARNING,
            type=AlertType.SYSTEM,
            title=title,
            message=message,
            data=data,
        ))
    
    async def info(self, title: str, message: str, **data):
        """Send INFO alert."""
        await self.send(Alert(
            level=AlertLevel.INFO,
            type=AlertType.SYSTEM,
            title=title,
            message=message,
            data=data,
        ))
    
    async def trade_opened(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        **data,
    ):
        """Alert: Trade opened."""
        await self.send(Alert(
            level=AlertLevel.INFO,
            type=AlertType.TRADE,
            title=f"🟢 {side} {symbol}",
            message=f"Opened {side} position",
            data={
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "price": price,
                "value_usd": quantity * price,
                **data,
            },
        ))
    
    async def trade_closed(
        self,
        symbol: str,
        pnl: float,
        pnl_percent: float,
        reason: str,
        **data,
    ):
        """Alert: Trade closed."""
        emoji = "💰" if pnl >= 0 else "📉"
        level = AlertLevel.INFO if pnl >= 0 else AlertLevel.WARNING
        
        await self.send(Alert(
            level=level,
            type=AlertType.TRADE,
            title=f"{emoji} Closed {symbol}",
            message=f"PnL: ${pnl:.2f} ({pnl_percent:+.1f}%)",
            data={
                "symbol": symbol,
                "pnl_usd": pnl,
                "pnl_percent": pnl_percent,
                "reason": reason,
                **data,
            },
        ))
    
    async def emergency_stop(self, reason: str, **data):
        """Alert: Emergency stop."""
        await self.send(Alert(
            level=AlertLevel.CRITICAL,
            type=AlertType.GUARDIAN,
            title="🚨 EMERGENCY STOP",
            message=reason,
            data=data,
        ))
    
    async def system_restart(self, reason: str, **data):
        """Alert: System restart."""
        await self.send(Alert(
            level=AlertLevel.WARNING,
            type=AlertType.GUARDIAN,
            title="🔄 System Restarting",
            message=reason,
            data=data,
        ))
    
    async def health_degraded(self, checks: Dict[str, bool], **data):
        """Alert: Health degraded."""
        failed = [k for k, v in checks.items() if not v]
        await self.send(Alert(
            level=AlertLevel.WARNING,
            type=AlertType.HEALTH,
            title="⚠️ Health Degraded",
            message=f"Failed checks: {', '.join(failed)}",
            data={"checks": checks, **data},
        ))
    
    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get alert history."""
        with self._lock:
            return [a.to_dict() for a in self._history[-limit:]]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get alerting statistics."""
        return {
            "total_alerts": len(self._history),
            "telegram": self._telegram.get_stats(),
            "min_level": self._min_level.value,
            "rate_limit_seconds": self._rate_limit_seconds,
        }


# =============================================================================
# GLOBAL INSTANCE
# =============================================================================

_alert_manager: Optional[AlertManager] = None


def get_alert_manager() -> AlertManager:
    """Get global alert manager."""
    global _alert_manager
    if _alert_manager is None:
        # Try to load from environment
        _alert_manager = AlertManager(
            telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID"),
        )
    return _alert_manager


def configure_alerts(
    telegram_token: Optional[str] = None,
    telegram_chat_id: Optional[str] = None,
    log_file: Optional[Path] = None,
    min_level: AlertLevel = AlertLevel.INFO,
) -> AlertManager:
    """Configure global alert manager."""
    global _alert_manager
    _alert_manager = AlertManager(
        telegram_token=telegram_token,
        telegram_chat_id=telegram_chat_id,
        log_file=log_file,
        min_level=min_level,
    )
    return _alert_manager
