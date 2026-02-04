# === AI SIGNATURE ===
# Module: hope_core/alerts/telegram.py
# Created by: Claude (opus-4.5)
# Created at: 2026-02-04 11:30:00 UTC
# Purpose: Telegram alert system for HOPE Core
# === END SIGNATURE ===
"""
Telegram Alert System

Sends alerts to Telegram for:
- Trade execution (BUY/SELL)
- Position closed (TP/SL)
- Emergency stops
- Daily summaries
- System errors
"""

from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import asyncio
import aiohttp
import os


class AlertLevel(Enum):
    """Alert severity levels."""
    INFO = "ℹ️"
    SUCCESS = "✅"
    WARNING = "⚠️"
    ERROR = "❌"
    CRITICAL = "🚨"
    TRADE = "📈"
    MONEY = "💰"


@dataclass
class TelegramConfig:
    """Telegram configuration."""
    bot_token: str = ""
    chat_id: str = ""
    enabled: bool = True
    rate_limit_per_minute: int = 20
    
    @classmethod
    def from_env(cls) -> "TelegramConfig":
        """Load from environment variables."""
        return cls(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            enabled=os.getenv("TELEGRAM_ENABLED", "true").lower() == "true",
        )


class TelegramAlertManager:
    """
    Telegram Alert Manager.
    
    Handles sending alerts with rate limiting and formatting.
    """
    
    def __init__(self, config: Optional[TelegramConfig] = None):
        """
        Initialize alert manager.
        
        Args:
            config: Telegram configuration
        """
        self.config = config or TelegramConfig.from_env()
        self._message_times: List[datetime] = []
        self._session: Optional[aiohttp.ClientSession] = None
    
    @property
    def is_configured(self) -> bool:
        """Check if Telegram is configured."""
        return bool(self.config.bot_token and self.config.chat_id)
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
    
    async def close(self):
        """Close session."""
        if self._session and not self._session.closed:
            await self._session.close()
    
    def _check_rate_limit(self) -> bool:
        """Check if we can send a message."""
        now = datetime.now(timezone.utc)
        
        # Remove old timestamps
        cutoff = now.timestamp() - 60
        self._message_times = [
            t for t in self._message_times 
            if t.timestamp() > cutoff
        ]
        
        # Check limit
        if len(self._message_times) >= self.config.rate_limit_per_minute:
            return False
        
        self._message_times.append(now)
        return True
    
    async def send_message(
        self,
        text: str,
        parse_mode: str = "HTML",
        disable_notification: bool = False,
    ) -> bool:
        """
        Send message to Telegram.
        
        Args:
            text: Message text (HTML or Markdown)
            parse_mode: Parse mode (HTML/Markdown)
            disable_notification: Send silently
            
        Returns:
            True if sent successfully
        """
        if not self.config.enabled or not self.is_configured:
            print(f"[TELEGRAM] Not configured, message: {text[:50]}...")
            return False
        
        if not self._check_rate_limit():
            print(f"[TELEGRAM] Rate limited, skipping: {text[:50]}...")
            return False
        
        try:
            session = await self._get_session()
            url = f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage"
            
            payload = {
                "chat_id": self.config.chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_notification": disable_notification,
            }
            
            async with session.post(url, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    return True
                else:
                    error = await resp.text()
                    print(f"[TELEGRAM] Error {resp.status}: {error}")
                    return False
                    
        except Exception as e:
            print(f"[TELEGRAM] Send failed: {e}")
            return False
    
    async def send_alert(
        self,
        title: str,
        message: str,
        level: AlertLevel = AlertLevel.INFO,
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Send formatted alert.
        
        Args:
            title: Alert title
            message: Alert message
            level: Alert level
            details: Additional details
            
        Returns:
            True if sent
        """
        # Format message
        text = f"{level.value} <b>{title}</b>\n\n{message}"
        
        if details:
            text += "\n\n<code>"
            for key, value in details.items():
                text += f"{key}: {value}\n"
            text += "</code>"
        
        text += f"\n\n<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
        
        return await self.send_message(text)
    
    # =========================================================================
    # TRADE ALERTS
    # =========================================================================
    
    async def alert_trade_opened(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        confidence: float,
        position_id: str,
    ) -> bool:
        """Alert: Trade opened."""
        emoji = "🟢" if side == "BUY" else "🔴"
        
        text = f"""
{emoji} <b>TRADE OPENED</b>

Symbol: <code>{symbol}</code>
Side: <code>{side}</code>
Quantity: <code>{quantity:.6f}</code>
Price: <code>${price:.4f}</code>
Confidence: <code>{confidence:.0%}</code>

Position ID: <code>{position_id}</code>
"""
        return await self.send_message(text.strip())
    
    async def alert_trade_closed(
        self,
        symbol: str,
        side: str,
        quantity: float,
        entry_price: float,
        exit_price: float,
        pnl: float,
        pnl_percent: float,
        reason: str,
        position_id: str,
    ) -> bool:
        """Alert: Trade closed."""
        emoji = "💰" if pnl > 0 else "💸"
        pnl_emoji = "🟢" if pnl > 0 else "🔴"
        
        text = f"""
{emoji} <b>TRADE CLOSED</b>

Symbol: <code>{symbol}</code>
Reason: <code>{reason}</code>

Entry: <code>${entry_price:.4f}</code>
Exit: <code>${exit_price:.4f}</code>
Quantity: <code>{quantity:.6f}</code>

{pnl_emoji} PnL: <code>${pnl:+.4f}</code> (<code>{pnl_percent:+.2f}%</code>)

Position ID: <code>{position_id}</code>
"""
        return await self.send_message(text.strip())
    
    async def alert_daily_summary(
        self,
        trades: int,
        wins: int,
        losses: int,
        total_pnl: float,
        win_rate: float,
        best_trade: Optional[Dict[str, Any]] = None,
        worst_trade: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Alert: Daily summary."""
        emoji = "📊"
        pnl_emoji = "🟢" if total_pnl > 0 else "🔴" if total_pnl < 0 else "⚪"
        
        text = f"""
{emoji} <b>DAILY SUMMARY</b>

Trades: <code>{trades}</code>
Wins: <code>{wins}</code> | Losses: <code>{losses}</code>
Win Rate: <code>{win_rate:.1%}</code>

{pnl_emoji} Total PnL: <code>${total_pnl:+.2f}</code>
"""
        
        if best_trade:
            text += f"\n🏆 Best: <code>{best_trade['symbol']}</code> ${best_trade['pnl']:+.2f}"
        
        if worst_trade:
            text += f"\n📉 Worst: <code>{worst_trade['symbol']}</code> ${worst_trade['pnl']:+.2f}"
        
        return await self.send_message(text.strip())
    
    # =========================================================================
    # SYSTEM ALERTS
    # =========================================================================
    
    async def alert_emergency_stop(
        self,
        reason: str,
        positions_closed: int,
    ) -> bool:
        """Alert: Emergency stop triggered."""
        text = f"""
🚨 <b>EMERGENCY STOP</b>

Reason: <code>{reason}</code>
Positions Closed: <code>{positions_closed}</code>

⚠️ Trading has been halted. Manual intervention required.
"""
        return await self.send_message(text.strip(), disable_notification=False)
    
    async def alert_circuit_breaker(
        self,
        state: str,
        failures: int,
        reason: str,
    ) -> bool:
        """Alert: Circuit breaker state change."""
        emoji = "🔴" if state == "OPEN" else "🟢"
        
        text = f"""
{emoji} <b>CIRCUIT BREAKER: {state}</b>

Failures: <code>{failures}</code>
Reason: <code>{reason}</code>
"""
        return await self.send_message(text.strip())
    
    async def alert_system_start(
        self,
        mode: str,
        version: str = "2.0",
    ) -> bool:
        """Alert: System started."""
        text = f"""
🚀 <b>HOPE CORE STARTED</b>

Version: <code>{version}</code>
Mode: <code>{mode}</code>
Time: <code>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</code>

Trading system is now active.
"""
        return await self.send_message(text.strip())
    
    async def alert_system_stop(
        self,
        reason: str,
        uptime_hours: float,
    ) -> bool:
        """Alert: System stopped."""
        text = f"""
🛑 <b>HOPE CORE STOPPED</b>

Reason: <code>{reason}</code>
Uptime: <code>{uptime_hours:.1f} hours</code>
Time: <code>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</code>
"""
        return await self.send_message(text.strip())
    
    async def alert_error(
        self,
        error_type: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Alert: Error occurred."""
        return await self.send_alert(
            title=f"ERROR: {error_type}",
            message=message,
            level=AlertLevel.ERROR,
            details=details,
        )
    
    async def alert_guardian_restart(
        self,
        restart_count: int,
        reason: str,
    ) -> bool:
        """Alert: Guardian restarted core."""
        text = f"""
🔄 <b>CORE RESTARTED BY GUARDIAN</b>

Restart Count: <code>{restart_count}</code>/hour
Reason: <code>{reason}</code>

System has been automatically recovered.
"""
        return await self.send_message(text.strip())


# =============================================================================
# SINGLETON INSTANCE
# =============================================================================

_alert_manager: Optional[TelegramAlertManager] = None


def get_alert_manager() -> TelegramAlertManager:
    """Get singleton alert manager."""
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = TelegramAlertManager()
    return _alert_manager


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

async def send_alert(title: str, message: str, level: AlertLevel = AlertLevel.INFO):
    """Send alert using singleton manager."""
    manager = get_alert_manager()
    await manager.send_alert(title, message, level)


async def send_trade_alert(
    symbol: str,
    action: str,
    price: float,
    pnl: Optional[float] = None,
):
    """Quick trade alert."""
    manager = get_alert_manager()
    if action in ("BUY", "OPEN"):
        await manager.alert_trade_opened(
            symbol=symbol,
            side="BUY",
            quantity=0,
            price=price,
            confidence=0,
            position_id="",
        )
    else:
        await manager.alert_trade_closed(
            symbol=symbol,
            side="SELL",
            quantity=0,
            entry_price=0,
            exit_price=price,
            pnl=pnl or 0,
            pnl_percent=0,
            reason=action,
            position_id="",
        )


# =============================================================================
# TESTING
# =============================================================================

if __name__ == "__main__":
    print("=== Telegram Alert Tests ===\n")
    
    # Test without actual sending
    config = TelegramConfig(enabled=False)
    manager = TelegramAlertManager(config)
    
    print("Configuration:")
    print(f"  Enabled: {manager.config.enabled}")
    print(f"  Configured: {manager.is_configured}")
    print()
    
    # Test rate limiting
    print("Rate Limit Test:")
    for i in range(25):
        can_send = manager._check_rate_limit()
        if i < 20:
            assert can_send, f"Should allow message {i}"
        else:
            assert not can_send, f"Should block message {i}"
    print("  ✅ Rate limiting works")
    print()
    
    print("=== Tests Completed ===")
