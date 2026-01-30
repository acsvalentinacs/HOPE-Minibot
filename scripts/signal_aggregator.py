# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 14:55:00 UTC
# Modified by: Claude (opus-4.5)
# Modified at: 2026-01-30 18:10:00 UTC
# Purpose: Signal Aggregator for Telegram - ONLY delta >= 10% + ML training log
# === END SIGNATURE ===
"""
Signal Aggregator v1.0

Solves the problem of 200+ Telegram messages per hour by:
1. Filtering noise signals (delta < 0.3%)
2. Aggregating signals into 5-minute digests
3. Sending instant alerts only for HOT signals (delta > 3%)
4. Deduplicating repeated signals
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from datetime import datetime, timedelta
from collections import defaultdict
import hashlib
import json
from pathlib import Path

log = logging.getLogger("signal_aggregator")


@dataclass
class SignalEntry:
    """Single signal entry."""
    symbol: str
    delta_pct: float
    buys_per_sec: float
    price: float
    tier: str
    target_pct: float
    confidence: float
    timestamp: datetime = field(default_factory=datetime.utcnow)
    signal_id: str = ""

    def __post_init__(self):
        if not self.signal_id:
            # Generate unique ID
            data = f"{self.symbol}_{self.delta_pct}_{self.timestamp.isoformat()}"
            self.signal_id = hashlib.sha256(data.encode()).hexdigest()[:12]

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "delta_pct": self.delta_pct,
            "buys_per_sec": self.buys_per_sec,
            "price": self.price,
            "tier": self.tier,
            "target_pct": self.target_pct,
            "confidence": self.confidence,
            "timestamp": self.timestamp.isoformat(),
            "signal_id": self.signal_id,
        }


class SignalAggregator:
    """
    Aggregates signals to reduce Telegram spam.

    Features:
    - Deduplication (same symbol within 60s = 1 signal)
    - Noise filtering (delta < 0.3% = skip)
    - Digest mode (batch signals every 5 minutes)
    - Instant alerts for HOT signals (delta > 3%)
    """

    # === THRESHOLDS (UPDATED: ONLY HOT SIGNALS TO TELEGRAM) ===
    NOISE_THRESHOLD = 5.0      # Below 5% = noise, don't send to Telegram
    HOT_THRESHOLD = 10.0       # Only >= 10% goes to Telegram instantly
    MOONSHOT_THRESHOLD = 25.0  # 25%+ = MOONSHOT (extreme pump)
    DIGEST_INTERVAL = 3600     # 1 hour (was 5 min)
    DEDUP_WINDOW = 60          # Same symbol within 60s = duplicate

    def __init__(self, state_dir: Path = None):
        self.state_dir = state_dir or Path("state/ai/aggregator")
        self.state_dir.mkdir(parents=True, exist_ok=True)

        # Current digest buffer
        self.buffer: List[SignalEntry] = []
        self.buffer_start: datetime = datetime.utcnow()

        # Deduplication tracking
        self.recent_signals: Dict[str, datetime] = {}  # symbol -> last_seen

        # Statistics
        self.stats = {
            "total_received": 0,
            "filtered_noise": 0,
            "filtered_duplicate": 0,
            "sent_instant": 0,
            "sent_digest": 0,
        }

        # User preferences (UPDATED: delta >= 10% for Telegram)
        self.preferences = {
            "digest_enabled": True,
            "instant_alerts": True,
            "min_delta": 5.0,       # Ignore below 5%
            "hot_delta": 10.0,      # Only 10%+ goes to Telegram
            "muted": False,
        }

        # ML Training data logging
        self.ml_log_path = Path("state/ai/signals_training.jsonl")
        self.ml_log_path.parent.mkdir(parents=True, exist_ok=True)

        log.info("SignalAggregator initialized")

    def _is_duplicate(self, symbol: str) -> bool:
        """Check if signal is duplicate within dedup window."""
        last_seen = self.recent_signals.get(symbol)
        if last_seen is None:
            return False
        return (datetime.utcnow() - last_seen).total_seconds() < self.DEDUP_WINDOW

    def _update_dedup(self, symbol: str):
        """Update deduplication tracker."""
        self.recent_signals[symbol] = datetime.utcnow()

        # Cleanup old entries (older than 5 minutes)
        cutoff = datetime.utcnow() - timedelta(minutes=5)
        self.recent_signals = {
            k: v for k, v in self.recent_signals.items() if v > cutoff
        }

    def process_signal(self, signal: Dict) -> Dict:
        """
        Process incoming signal and decide what to do.

        Returns:
            {
                "action": "SKIP" | "BUFFER" | "INSTANT",
                "send_now": bool,
                "message": Optional[str],
                "reason": str,
            }
        """
        self.stats["total_received"] += 1

        symbol = signal.get("symbol", "UNKNOWN")
        delta_pct = signal.get("delta_pct", 0.0)
        buys_per_sec = signal.get("buys_per_sec", 0.0)
        price = signal.get("price", 0.0)
        tier = signal.get("tier", "UNKNOWN")
        target_pct = signal.get("target_pct", 1.0)
        confidence = signal.get("confidence", 0.5)

        # Check mute
        if self.preferences.get("muted"):
            return {
                "action": "SKIP",
                "send_now": False,
                "message": None,
                "reason": "Muted",
            }

        # Filter noise
        min_delta = self.preferences.get("min_delta", self.NOISE_THRESHOLD)
        if delta_pct < min_delta:
            self.stats["filtered_noise"] += 1
            # Log for ML training (NOT SENT - noise)
            self._log_for_ml_training(symbol, delta_pct, tier, sent=False,
                                      buys_per_sec=buys_per_sec, confidence=confidence)
            return {
                "action": "SKIP",
                "send_now": False,
                "message": None,
                "reason": f"Noise (delta={delta_pct:.2f}% < {min_delta}%)",
            }

        # Check duplicate
        if self._is_duplicate(symbol):
            self.stats["filtered_duplicate"] += 1
            return {
                "action": "SKIP",
                "send_now": False,
                "message": None,
                "reason": f"Duplicate ({symbol} seen recently)",
            }

        self._update_dedup(symbol)

        # Create entry
        entry = SignalEntry(
            symbol=symbol,
            delta_pct=delta_pct,
            buys_per_sec=buys_per_sec,
            price=price,
            tier=tier,
            target_pct=target_pct,
            confidence=confidence,
        )

        # HOT signal = instant alert
        hot_delta = self.preferences.get("hot_delta", self.HOT_THRESHOLD)
        if delta_pct >= hot_delta and self.preferences.get("instant_alerts", True):
            self.stats["sent_instant"] += 1
            message = self._format_instant_alert(entry)
            # Log for ML training (SENT)
            self._log_for_ml_training(symbol, delta_pct, tier, sent=True,
                                      buys_per_sec=buys_per_sec, confidence=confidence)
            return {
                "action": "INSTANT",
                "send_now": True,
                "message": message,
                "reason": f"HOT signal (delta={delta_pct:.2f}% >= {hot_delta}%)",
            }

        # Buffer for digest (5% <= delta < 10%)
        self.buffer.append(entry)
        # Log for ML training (NOT SENT - buffered)
        self._log_for_ml_training(symbol, delta_pct, tier, sent=False,
                                  buys_per_sec=buys_per_sec, confidence=confidence)

        # Check if digest ready
        if self._digest_ready():
            digest = self._create_digest()
            self.stats["sent_digest"] += 1
            return {
                "action": "DIGEST",
                "send_now": True,
                "message": digest,
                "reason": f"Digest ready ({len(self.buffer)} signals)",
            }

        return {
            "action": "BUFFER",
            "send_now": False,
            "message": None,
            "reason": f"Buffered for digest ({len(self.buffer)} signals)",
        }

    def _digest_ready(self) -> bool:
        """Check if it's time to send digest."""
        elapsed = (datetime.utcnow() - self.buffer_start).total_seconds()
        return elapsed >= self.DIGEST_INTERVAL and len(self.buffer) > 0

    def _format_instant_alert(self, entry: SignalEntry) -> str:
        """Format HOT signal instant alert."""
        emoji = self._get_tier_emoji(entry.tier)

        msg = f"""
{emoji} *HOT SIGNAL!*

*{entry.symbol}*
📈 Delta: +{entry.delta_pct:.2f}%
⚡ Buys: {entry.buys_per_sec:.0f}/sec
💰 Price: ${entry.price:.4f}
🎯 Target: +{entry.target_pct:.1f}%
📊 Confidence: {entry.confidence:.0%}

_Tier: {entry.tier}_
"""
        return msg.strip()

    def _create_digest(self) -> str:
        """Create digest message from buffer."""
        if not self.buffer:
            return None

        # Sort by delta (strongest first)
        sorted_signals = sorted(self.buffer, key=lambda x: x.delta_pct, reverse=True)

        # Categorize
        hot = [s for s in sorted_signals if s.delta_pct >= 3.0]
        active = [s for s in sorted_signals if 0.5 <= s.delta_pct < 3.0]
        weak = [s for s in sorted_signals if s.delta_pct < 0.5]

        # Build message
        lines = [
            "📊 *HOPE SIGNAL DIGEST*",
            f"_{datetime.utcnow().strftime('%H:%M UTC')}_",
            "",
        ]

        # Summary
        lines.append(f"🔥 Hot: {len(hot)} | 📈 Active: {len(active)} | 🔇 Weak: {len(weak)}")
        lines.append("")

        # Top signals
        if hot:
            lines.append("*🔥 HOT SIGNALS:*")
            for s in hot[:3]:
                lines.append(f"  {s.symbol}: +{s.delta_pct:.1f}% | {s.buys_per_sec:.0f}/s")
            lines.append("")

        if active:
            lines.append("*📈 ACTIVE:*")
            symbols = [s.symbol for s in active[:5]]
            lines.append(f"  {', '.join(symbols)}")
            lines.append("")

        # Best opportunity
        if sorted_signals:
            best = sorted_signals[0]
            lines.append("*🏆 BEST OPPORTUNITY:*")
            lines.append(f"  {best.symbol}")
            lines.append(f"  📈 +{best.delta_pct:.2f}% | ⚡ {best.buys_per_sec:.0f}/s")
            lines.append(f"  🎯 Target: +{best.target_pct:.1f}%")

        # Stats
        lines.append("")
        lines.append(f"_Total signals: {len(self.buffer)} | Interval: 5 min_")

        # Clear buffer
        self.buffer = []
        self.buffer_start = datetime.utcnow()

        return "\n".join(lines)

    def _get_tier_emoji(self, tier: str) -> str:
        """Get emoji for tier."""
        return {
            "NOISE": "🔇",
            "MICRO": "📊",
            "SCALP": "📈",
            "STRONG": "💪",
            "EXPLOSION": "🔥",
            "MOONSHOT": "🚀",
            "EXTREME": "💎",
        }.get(tier, "📊")

    def force_digest(self) -> Optional[str]:
        """Force send current digest (for /digest command)."""
        if not self.buffer:
            return "📊 No signals in buffer"
        return self._create_digest()

    def get_hot_only(self) -> str:
        """Get only hot signals from buffer (for /hot command)."""
        hot = [s for s in self.buffer if s.delta_pct >= 3.0]
        if not hot:
            return "🔇 No hot signals currently"

        lines = ["🔥 *HOT SIGNALS:*", ""]
        for s in sorted(hot, key=lambda x: x.delta_pct, reverse=True):
            lines.append(f"*{s.symbol}*: +{s.delta_pct:.1f}% | {s.buys_per_sec:.0f}/s | 🎯{s.target_pct:.1f}%")

        return "\n".join(lines)

    def set_preference(self, key: str, value) -> str:
        """Set user preference."""
        if key in self.preferences:
            self.preferences[key] = value
            return f"✅ {key} set to {value}"
        return f"❌ Unknown preference: {key}"

    def toggle_mute(self) -> str:
        """Toggle mute mode."""
        self.preferences["muted"] = not self.preferences["muted"]
        status = "🔇 Muted" if self.preferences["muted"] else "🔊 Unmuted"
        return status

    def get_stats(self) -> str:
        """Get statistics message."""
        s = self.stats
        return f"""
📊 *AGGREGATOR STATS*

Total received: {s['total_received']}
Filtered (noise): {s['filtered_noise']}
Filtered (duplicate): {s['filtered_duplicate']}
Sent (instant): {s['sent_instant']}
Sent (digest): {s['sent_digest']}

Buffer: {len(self.buffer)} signals
Muted: {'Yes' if self.preferences['muted'] else 'No'}
Min delta: {self.preferences['min_delta']}%
HOT threshold: {self.preferences['hot_delta']}%
""".strip()

    def _log_for_ml_training(self, symbol: str, delta_pct: float, tier: str,
                             sent: bool, buys_per_sec: float = 0, confidence: float = 0):
        """Log ALL signals for ML model training."""
        import time
        entry = {
            "ts": int(time.time()),
            "symbol": symbol,
            "delta": round(delta_pct, 4),
            "tier": tier,
            "buys_per_sec": round(buys_per_sec, 2),
            "confidence": round(confidence, 4),
            "sent": sent,
            "threshold": self.preferences['hot_delta'],
        }
        try:
            with open(self.ml_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            log.warning(f"ML log write error: {e}")


# === GLOBAL INSTANCE ===
_aggregator: Optional[SignalAggregator] = None


def get_aggregator() -> SignalAggregator:
    """Get or create global aggregator instance."""
    global _aggregator
    if _aggregator is None:
        _aggregator = SignalAggregator()
    return _aggregator


def process_signal_for_telegram(signal: Dict) -> Dict:
    """Convenience function to process signal."""
    return get_aggregator().process_signal(signal)


# === TELEGRAM BOT COMMANDS ===
TELEGRAM_COMMANDS = {
    "/digest": "Show current signal digest",
    "/hot": "Show only hot signals",
    "/mute": "Toggle mute mode",
    "/unmute": "Turn off mute mode",
    "/stats": "Show aggregator statistics",
    "/settings": "Show current settings",
}


async def handle_telegram_command(command: str, args: str = "") -> str:
    """Handle Telegram bot commands."""
    agg = get_aggregator()

    if command == "/digest":
        return agg.force_digest()

    elif command == "/hot":
        return agg.get_hot_only()

    elif command == "/mute":
        return agg.toggle_mute()

    elif command == "/unmute":
        agg.preferences["muted"] = False
        return "🔊 Unmuted - all alerts enabled"

    elif command == "/stats":
        return agg.get_stats()

    elif command == "/settings":
        p = agg.preferences
        return f"""
⚙️ *SETTINGS*

Digest enabled: {p['digest_enabled']}
Instant alerts: {p['instant_alerts']}
Min delta: {p['min_delta']}%
Hot threshold: {p['hot_delta']}%
Muted: {p['muted']}

Commands: {', '.join(TELEGRAM_COMMANDS.keys())}
""".strip()

    else:
        return f"❓ Unknown command: {command}\nAvailable: {', '.join(TELEGRAM_COMMANDS.keys())}"


# === TEST ===
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("=" * 70)
    print("SIGNAL AGGREGATOR v1.0 - TEST")
    print("=" * 70)

    agg = SignalAggregator()

    # Test signals
    test_signals = [
        {"symbol": "BTCUSDT", "delta_pct": 0.05, "buys_per_sec": 10, "price": 83500, "tier": "NOISE", "target_pct": 0, "confidence": 0.2},
        {"symbol": "ETHUSDT", "delta_pct": 0.15, "buys_per_sec": 15, "price": 2750, "tier": "NOISE", "target_pct": 0, "confidence": 0.2},
        {"symbol": "SOLUSDT", "delta_pct": 0.8, "buys_per_sec": 30, "price": 117, "tier": "MICRO", "target_pct": 0.3, "confidence": 0.5},
        {"symbol": "DOGEUSDT", "delta_pct": 2.0, "buys_per_sec": 50, "price": 0.12, "tier": "SCALP", "target_pct": 1.0, "confidence": 0.6},
        {"symbol": "PEPEUSDT", "delta_pct": 5.0, "buys_per_sec": 80, "price": 0.00001, "tier": "STRONG", "target_pct": 2.5, "confidence": 0.75},
        {"symbol": "PEPEUSDT", "delta_pct": 5.1, "buys_per_sec": 82, "price": 0.00001, "tier": "STRONG", "target_pct": 2.5, "confidence": 0.75},  # Duplicate
        {"symbol": "WIFUSDT", "delta_pct": 12.0, "buys_per_sec": 120, "price": 1.5, "tier": "EXPLOSION", "target_pct": 4.0, "confidence": 0.85},
    ]

    print("\nProcessing signals:")
    print("-" * 70)

    for sig in test_signals:
        result = agg.process_signal(sig)
        print(f"{sig['symbol']:<12} delta={sig['delta_pct']:>5.2f}% -> {result['action']:<8} | {result['reason']}")

    print("\n" + "=" * 70)
    print("STATS:")
    print(agg.get_stats())

    print("\n" + "=" * 70)
    print("FORCE DIGEST:")
    print(agg.force_digest())
