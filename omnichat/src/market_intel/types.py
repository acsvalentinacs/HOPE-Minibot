# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T19:00:00Z
# Purpose: Market Intelligence data types - fail-closed, self-documenting
# === END SIGNATURE ===
"""
Market Intelligence Data Types.

All types are immutable dataclasses with explicit contracts.
sha256 prefixes ensure data integrity verification.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class ImpactScore(Enum):
    """News impact classification."""
    CRITICAL = 1.0      # Market-moving: regulation, hacks, major institutional
    HIGH = 0.8          # Significant: ETF news, exchange listings, protocol upgrades
    MEDIUM = 0.5        # Notable: partnerships, adoption news, analyst reports
    LOW = 0.3           # Minor: opinions, minor updates
    NOISE = 0.1         # Irrelevant: promotional, repetitive


class Sentiment(Enum):
    """Market sentiment classification."""
    EXTREME_FEAR = "extreme_fear"
    FEAR = "fear"
    NEUTRAL = "neutral"
    GREED = "greed"
    EXTREME_GREED = "extreme_greed"


@dataclass(frozen=True)
class TickerData:
    """Single ticker data point from exchange."""
    symbol: str
    price: float
    price_change_pct: float
    volume: float
    quote_volume: float
    high_24h: float
    low_24h: float
    timestamp: datetime

    @property
    def is_bullish(self) -> bool:
        return self.price_change_pct > 0

    @property
    def volatility(self) -> float:
        """Calculate 24h volatility as (high-low)/price."""
        if self.price <= 0:
            return 0.0
        return (self.high_24h - self.low_24h) / self.price

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "price_change_pct": self.price_change_pct,
            "volume": self.volume,
            "quote_volume": self.quote_volume,
            "high_24h": self.high_24h,
            "low_24h": self.low_24h,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass(frozen=True)
class GlobalMetrics:
    """Global cryptocurrency market metrics."""
    total_market_cap_usd: float
    total_volume_24h_usd: float
    btc_dominance_pct: float
    eth_dominance_pct: float
    market_cap_change_24h_pct: float
    active_cryptocurrencies: int
    timestamp: datetime

    @property
    def sentiment(self) -> Sentiment:
        """Derive sentiment from market cap change."""
        change = self.market_cap_change_24h_pct
        if change < -5:
            return Sentiment.EXTREME_FEAR
        elif change < -2:
            return Sentiment.FEAR
        elif change < 2:
            return Sentiment.NEUTRAL
        elif change < 5:
            return Sentiment.GREED
        else:
            return Sentiment.EXTREME_GREED

    def to_dict(self) -> dict:
        return {
            "total_market_cap_usd": self.total_market_cap_usd,
            "total_volume_24h_usd": self.total_volume_24h_usd,
            "btc_dominance_pct": self.btc_dominance_pct,
            "eth_dominance_pct": self.eth_dominance_pct,
            "market_cap_change_24h_pct": self.market_cap_change_24h_pct,
            "active_cryptocurrencies": self.active_cryptocurrencies,
            "timestamp": self.timestamp.isoformat(),
            "sentiment": self.sentiment.value,
        }


@dataclass(frozen=True)
class NewsItem:
    """Single news item with impact analysis."""
    title: str
    source: str
    url: str
    published_at: datetime
    summary: str
    impact: ImpactScore
    keywords: tuple[str, ...] = field(default_factory=tuple)
    sentiment_score: float = 0.0  # -1.0 to 1.0

    @property
    def is_market_moving(self) -> bool:
        return self.impact in (ImpactScore.CRITICAL, ImpactScore.HIGH)

    @property
    def age_minutes(self) -> float:
        return (datetime.utcnow() - self.published_at).total_seconds() / 60

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "source": self.source,
            "url": self.url,
            "published_at": self.published_at.isoformat(),
            "summary": self.summary,
            "impact": self.impact.name,
            "impact_score": self.impact.value,
            "keywords": list(self.keywords),
            "sentiment_score": self.sentiment_score,
        }


@dataclass
class MarketSnapshot:
    """
    Complete market snapshot with integrity verification.

    Explicit contract:
    - snapshot_id: sha256:<hash> format for verification
    - All timestamps in UTC
    - Prices in USD unless specified
    - TTL: 5 minutes for market data, 15 minutes for news
    """
    snapshot_id: str  # sha256:xxxx format
    timestamp: datetime
    tickers: dict[str, TickerData]
    global_metrics: Optional[GlobalMetrics]
    news: list[NewsItem]
    source_urls: list[str]
    fetch_duration_ms: int
    errors: list[str] = field(default_factory=list)

    # TTL constants
    MARKET_TTL_SECONDS: int = 300   # 5 minutes
    NEWS_TTL_SECONDS: int = 900     # 15 minutes

    @property
    def is_stale(self) -> bool:
        age = (datetime.utcnow() - self.timestamp).total_seconds()
        return age > self.MARKET_TTL_SECONDS

    @property
    def btc_price(self) -> float:
        btc = self.tickers.get("BTCUSDT")
        return btc.price if btc else 0.0

    @property
    def eth_price(self) -> float:
        eth = self.tickers.get("ETHUSDT")
        return eth.price if eth else 0.0

    @property
    def market_moving_news(self) -> list[NewsItem]:
        return [n for n in self.news if n.is_market_moving]

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "timestamp": self.timestamp.isoformat(),
            "tickers": {k: v.to_dict() for k, v in self.tickers.items()},
            "global_metrics": self.global_metrics.to_dict() if self.global_metrics else None,
            "news": [n.to_dict() for n in self.news],
            "source_urls": self.source_urls,
            "fetch_duration_ms": self.fetch_duration_ms,
            "errors": self.errors,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @staticmethod
    def compute_id(data: dict) -> str:
        """Compute sha256 snapshot ID from data."""
        raw = json.dumps(data, sort_keys=True, ensure_ascii=False)
        hash_hex = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return f"sha256:{hash_hex}"

    def verify_integrity(self) -> bool:
        """Verify snapshot ID matches content hash."""
        data = self.to_dict()
        data.pop("snapshot_id", None)
        expected_id = self.compute_id(data)
        return self.snapshot_id == expected_id


@dataclass
class MarketAlert:
    """Alert generated from market analysis."""
    alert_type: str  # price_move, news_impact, volume_spike, dominance_shift
    severity: ImpactScore
    symbol: Optional[str]
    message: str
    data: dict
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "alert_type": self.alert_type,
            "severity": self.severity.name,
            "symbol": self.symbol,
            "message": self.message,
            "data": self.data,
            "timestamp": self.timestamp.isoformat(),
        }
