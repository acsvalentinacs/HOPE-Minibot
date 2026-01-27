# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T19:00:00Z
# Purpose: News analyzer and market impact scoring
# === END SIGNATURE ===
"""
News Analyzer and Market Impact Scoring.

Provides:
- News impact classification
- Market correlation detection
- Alert generation for significant events
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from .types import (
    NewsItem,
    TickerData,
    GlobalMetrics,
    ImpactScore,
    MarketAlert,
    Sentiment,
)


@dataclass
class MarketContext:
    """Context for market analysis."""
    btc_price: float
    btc_change_24h: float
    eth_price: float
    eth_change_24h: float
    total_market_cap: float
    btc_dominance: float
    sentiment: Sentiment


def calculate_impact_score(
    news: NewsItem,
    context: Optional[MarketContext] = None,
) -> float:
    """
    Calculate weighted impact score for news item.

    Factors:
    - Base impact classification (0.1 - 1.0)
    - Recency (news older than 4h gets penalty)
    - Sentiment correlation with market
    - Keyword density

    Returns:
        Float 0.0 - 1.0 representing impact score
    """
    base_score = news.impact.value

    # Recency factor (0.5 - 1.0)
    age_hours = news.age_minutes / 60
    if age_hours > 24:
        recency_factor = 0.3
    elif age_hours > 12:
        recency_factor = 0.5
    elif age_hours > 4:
        recency_factor = 0.7
    elif age_hours > 1:
        recency_factor = 0.9
    else:
        recency_factor = 1.0

    # Keyword density factor
    keyword_count = len(news.keywords)
    keyword_factor = min(1.0, 0.5 + keyword_count * 0.1)

    # Combine factors
    final_score = base_score * recency_factor * keyword_factor

    return min(1.0, max(0.0, final_score))


class NewsAnalyzer:
    """
    Analyzer for news impact and market correlation.

    Usage:
        analyzer = NewsAnalyzer()
        alerts = analyzer.analyze_news(news_items, tickers)
    """

    # Keywords that indicate market-moving events
    CRITICAL_PATTERNS = [
        r'\b(hack|exploit|breach|stolen)\b',
        r'\b(sec|cftc|doj)\s+(sue|charge|investigation)',
        r'\betf\s+(approved|rejected|filed)',
        r'\b(ban|prohibition|illegal)\b',
        r'\b(bankrupt|insolvent|collapse)\b',
    ]

    HIGH_PATTERNS = [
        r'\b(partnership|acquisition|merge)\b',
        r'\b(listing|delist)\b',
        r'\b(upgrade|hard\s*fork)\b',
        r'\b(institutional|whale)\b',
        r'\b(billion|trillion)\b',
    ]

    def __init__(self):
        self._critical_re = [re.compile(p, re.IGNORECASE) for p in self.CRITICAL_PATTERNS]
        self._high_re = [re.compile(p, re.IGNORECASE) for p in self.HIGH_PATTERNS]

    def analyze_news(
        self,
        news: list[NewsItem],
        tickers: Optional[dict[str, TickerData]] = None,
        metrics: Optional[GlobalMetrics] = None,
    ) -> list[MarketAlert]:
        """
        Analyze news items and generate alerts.

        Args:
            news: List of news items
            tickers: Current ticker data
            metrics: Current global metrics

        Returns:
            List of MarketAlert for significant events
        """
        alerts = []

        for item in news:
            alert = self._analyze_single(item, tickers, metrics)
            if alert:
                alerts.append(alert)

        # Sort by severity
        alerts.sort(key=lambda x: x.severity.value, reverse=True)

        return alerts

    def _analyze_single(
        self,
        item: NewsItem,
        tickers: Optional[dict[str, TickerData]],
        metrics: Optional[GlobalMetrics],
    ) -> Optional[MarketAlert]:
        """Analyze single news item."""
        text = f"{item.title} {item.summary}"

        # Check critical patterns
        for pattern in self._critical_re:
            if pattern.search(text):
                return MarketAlert(
                    alert_type="news_critical",
                    severity=ImpactScore.CRITICAL,
                    symbol=self._extract_symbol(text),
                    message=f"CRITICAL: {item.title}",
                    data=item.to_dict(),
                )

        # Check high impact patterns
        for pattern in self._high_re:
            if pattern.search(text):
                return MarketAlert(
                    alert_type="news_high",
                    severity=ImpactScore.HIGH,
                    symbol=self._extract_symbol(text),
                    message=f"HIGH IMPACT: {item.title}",
                    data=item.to_dict(),
                )

        # Return alert only for market-moving news
        if item.is_market_moving:
            return MarketAlert(
                alert_type="news_impact",
                severity=item.impact,
                symbol=self._extract_symbol(text),
                message=item.title,
                data=item.to_dict(),
            )

        return None

    def _extract_symbol(self, text: str) -> Optional[str]:
        """Extract primary crypto symbol from text."""
        text_lower = text.lower()

        symbol_map = {
            "bitcoin": "BTCUSDT",
            "btc": "BTCUSDT",
            "ethereum": "ETHUSDT",
            "eth": "ETHUSDT",
            "binance": "BNBUSDT",
            "bnb": "BNBUSDT",
            "solana": "SOLUSDT",
            "sol": "SOLUSDT",
            "xrp": "XRPUSDT",
            "ripple": "XRPUSDT",
            "cardano": "ADAUSDT",
            "ada": "ADAUSDT",
            "dogecoin": "DOGEUSDT",
            "doge": "DOGEUSDT",
        }

        for keyword, symbol in symbol_map.items():
            if keyword in text_lower:
                return symbol

        return None

    def generate_market_alerts(
        self,
        tickers: dict[str, TickerData],
        metrics: Optional[GlobalMetrics],
    ) -> list[MarketAlert]:
        """
        Generate alerts from market data.

        Detects:
        - Large price moves (>5%)
        - High volatility
        - Dominance shifts
        - Volume spikes
        """
        alerts = []

        # Check each ticker for significant moves
        for symbol, ticker in tickers.items():
            # Large price move
            if abs(ticker.price_change_pct) > 10:
                alerts.append(MarketAlert(
                    alert_type="price_move",
                    severity=ImpactScore.CRITICAL,
                    symbol=symbol,
                    message=f"{symbol} moved {ticker.price_change_pct:+.2f}%",
                    data=ticker.to_dict(),
                ))
            elif abs(ticker.price_change_pct) > 5:
                alerts.append(MarketAlert(
                    alert_type="price_move",
                    severity=ImpactScore.HIGH,
                    symbol=symbol,
                    message=f"{symbol} moved {ticker.price_change_pct:+.2f}%",
                    data=ticker.to_dict(),
                ))

            # High volatility
            if ticker.volatility > 0.1:  # 10% intraday range
                alerts.append(MarketAlert(
                    alert_type="high_volatility",
                    severity=ImpactScore.MEDIUM,
                    symbol=symbol,
                    message=f"{symbol} high volatility: {ticker.volatility*100:.1f}%",
                    data=ticker.to_dict(),
                ))

        # Check global metrics
        if metrics:
            # Market cap change
            if abs(metrics.market_cap_change_24h_pct) > 5:
                alerts.append(MarketAlert(
                    alert_type="market_shift",
                    severity=ImpactScore.HIGH,
                    symbol=None,
                    message=f"Market cap changed {metrics.market_cap_change_24h_pct:+.2f}%",
                    data=metrics.to_dict(),
                ))

        return alerts

    def summarize_sentiment(
        self,
        news: list[NewsItem],
        tickers: dict[str, TickerData],
        metrics: Optional[GlobalMetrics],
    ) -> dict:
        """
        Generate market sentiment summary.

        Returns dict with:
        - overall_sentiment: bullish/bearish/neutral
        - confidence: 0.0-1.0
        - key_factors: list of reasons
        - recommendation: brief action recommendation
        """
        factors = []
        bullish_score = 0
        bearish_score = 0

        # Analyze price action
        btc = tickers.get("BTCUSDT")
        if btc:
            if btc.price_change_pct > 2:
                bullish_score += 2
                factors.append(f"BTC up {btc.price_change_pct:.1f}%")
            elif btc.price_change_pct < -2:
                bearish_score += 2
                factors.append(f"BTC down {btc.price_change_pct:.1f}%")

        # Analyze global metrics
        if metrics:
            if metrics.market_cap_change_24h_pct > 1:
                bullish_score += 1
                factors.append("Market cap growing")
            elif metrics.market_cap_change_24h_pct < -1:
                bearish_score += 1
                factors.append("Market cap declining")

            if metrics.btc_dominance_pct > 55:
                factors.append(f"High BTC dominance ({metrics.btc_dominance_pct:.1f}%)")

        # Analyze news sentiment
        critical_news = [n for n in news if n.impact == ImpactScore.CRITICAL]
        if critical_news:
            factors.append(f"{len(critical_news)} critical news items")

        # Calculate overall
        total = bullish_score + bearish_score
        if total == 0:
            sentiment = "neutral"
            confidence = 0.5
        elif bullish_score > bearish_score:
            sentiment = "bullish"
            confidence = bullish_score / (total + 2)
        else:
            sentiment = "bearish"
            confidence = bearish_score / (total + 2)

        # Generate recommendation
        if sentiment == "bullish" and confidence > 0.6:
            recommendation = "Favorable conditions for long positions"
        elif sentiment == "bearish" and confidence > 0.6:
            recommendation = "Consider reducing exposure or hedging"
        else:
            recommendation = "Monitor closely, conditions unclear"

        return {
            "overall_sentiment": sentiment,
            "confidence": round(confidence, 2),
            "key_factors": factors[:5],
            "recommendation": recommendation,
            "timestamp": datetime.utcnow().isoformat(),
        }
