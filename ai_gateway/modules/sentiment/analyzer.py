# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 03:42:00 UTC
# Purpose: Sentiment analysis using Claude API and rule-based fallback
# === END SIGNATURE ===
"""
Sentiment Analyzer: Market sentiment detection from news and social data.

Uses Claude API for intelligent sentiment analysis with rule-based fallback.
Writes SentimentArtifact to state/ai/sentiment.jsonl.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...contracts import (
    SentimentArtifact,
    SentimentLevel,
    SentimentSignal,
    create_artifact_id,
)
from ...jsonl_writer import write_artifact
from ...status_manager import get_status_manager

logger = logging.getLogger(__name__)


# Sentiment keywords for rule-based fallback
BULLISH_KEYWORDS = [
    "bullish", "pump", "moon", "rally", "breakout", "surge", "soar",
    "accumulation", "buy", "long", "higher", "support", "bounce",
    "adoption", "partnership", "integration", "approval", "etf approved",
]

BEARISH_KEYWORDS = [
    "bearish", "dump", "crash", "selloff", "breakdown", "plunge", "tank",
    "distribution", "sell", "short", "lower", "resistance", "rejection",
    "hack", "exploit", "ban", "lawsuit", "sec", "regulation", "delisting",
]

# Fear & Greed thresholds
FG_EXTREME_FEAR = 20
FG_FEAR = 40
FG_GREED = 60
FG_EXTREME_GREED = 80


class SentimentAnalyzer:
    """
    Sentiment analysis module for HOPE AI-Gateway.

    Analyzes market sentiment from:
    - News headlines (RSS feeds)
    - Fear & Greed Index
    - Price action context
    - Optional: Claude API for deep analysis
    """

    def __init__(
        self,
        anthropic_api_key: Optional[str] = None,
        use_ai: bool = True,
        ttl_seconds: int = 300,
    ):
        self._api_key = anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
        self._use_ai = use_ai and bool(self._api_key)
        self._ttl = ttl_seconds
        self._status = get_status_manager()

        # Optional: anthropic client (lazy init)
        self._client = None

        if self._use_ai:
            logger.info("SentimentAnalyzer: Claude API mode enabled")
        else:
            logger.info("SentimentAnalyzer: Rule-based mode (no API key)")

    def _get_client(self):
        """Lazy init Anthropic client."""
        if self._client is None and self._use_ai:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self._api_key)
            except ImportError:
                logger.warning("anthropic package not installed, using rule-based mode")
                self._use_ai = False
            except Exception as e:
                logger.error(f"Failed to init Anthropic client: {e}")
                self._use_ai = False
        return self._client

    async def analyze(
        self,
        symbol: str,
        news_headlines: List[str],
        fear_greed_index: Optional[int] = None,
        price_change_24h: Optional[float] = None,
        volume_change_24h: Optional[float] = None,
    ) -> SentimentArtifact:
        """
        Analyze sentiment and return artifact.

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            news_headlines: Recent news headlines
            fear_greed_index: Fear & Greed Index (0-100)
            price_change_24h: 24h price change percentage
            volume_change_24h: 24h volume change percentage

        Returns:
            SentimentArtifact with analysis results
        """
        try:
            signals: List[SentimentSignal] = []

            # 1. News sentiment (rule-based or AI)
            if news_headlines:
                news_signal = await self._analyze_news(news_headlines)
                signals.append(news_signal)

            # 2. Fear & Greed sentiment
            if fear_greed_index is not None:
                fg_signal = self._analyze_fear_greed(fear_greed_index)
                signals.append(fg_signal)

            # 3. Market action sentiment
            if price_change_24h is not None:
                market_signal = self._analyze_market_action(
                    price_change_24h, volume_change_24h
                )
                signals.append(market_signal)

            # Aggregate signals
            overall_score, confidence = self._aggregate_signals(signals)
            overall_sentiment = self._score_to_level(overall_score)

            # Determine bias
            if overall_score > 0.2:
                bias = "long"
            elif overall_score < -0.2:
                bias = "short"
            else:
                bias = "neutral"

            strength = min(abs(overall_score), 1.0)

            # Create artifact
            artifact = SentimentArtifact(
                artifact_id=create_artifact_id("sentiment", symbol),
                ttl_seconds=self._ttl,
                symbol=symbol,
                overall_sentiment=overall_sentiment,
                overall_score=overall_score,
                confidence=confidence,
                signals=signals,
                bias=bias,
                strength=strength,
                news_count=len(news_headlines),
                reasoning=f"Based on {len(signals)} signal sources",
            )

            # Write to JSONL
            if write_artifact(artifact.with_checksum()):
                self._status.mark_healthy("sentiment")
            else:
                self._status.mark_warning("sentiment", "Write failed")

            return artifact

        except Exception as e:
            logger.error(f"Sentiment analysis failed: {e}")
            self._status.mark_error("sentiment", str(e))
            raise

    async def _analyze_news(self, headlines: List[str]) -> SentimentSignal:
        """Analyze news headlines for sentiment."""
        if self._use_ai and len(headlines) >= 3:
            return await self._analyze_news_ai(headlines)
        return self._analyze_news_rules(headlines)

    async def _analyze_news_ai(self, headlines: List[str]) -> SentimentSignal:
        """Use Claude API for news sentiment analysis."""
        client = self._get_client()
        if not client:
            return self._analyze_news_rules(headlines)

        try:
            prompt = f"""Analyze the sentiment of these crypto news headlines.
Return ONLY a JSON object with these fields:
- score: float from -1.0 (very bearish) to 1.0 (very bullish)
- confidence: float from 0.0 to 1.0
- reasoning: brief explanation (1 sentence)

Headlines:
{chr(10).join(f"- {h}" for h in headlines[:10])}

JSON response:"""

            message = client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}]
            )

            response_text = message.content[0].text.strip()

            # Parse JSON from response
            import json
            # Find JSON in response
            json_match = re.search(r'\{[^}]+\}', response_text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                return SentimentSignal(
                    source="news_ai",
                    score=max(-1.0, min(1.0, float(result.get("score", 0)))),
                    confidence=max(0.0, min(1.0, float(result.get("confidence", 0.5)))),
                    sample_size=len(headlines),
                )

        except Exception as e:
            logger.warning(f"AI news analysis failed, falling back to rules: {e}")

        return self._analyze_news_rules(headlines)

    def _analyze_news_rules(self, headlines: List[str]) -> SentimentSignal:
        """Rule-based news sentiment analysis."""
        bullish_count = 0
        bearish_count = 0

        text = " ".join(headlines).lower()

        for kw in BULLISH_KEYWORDS:
            if kw in text:
                bullish_count += 1

        for kw in BEARISH_KEYWORDS:
            if kw in text:
                bearish_count += 1

        total = bullish_count + bearish_count
        if total == 0:
            score = 0.0
            confidence = 0.3
        else:
            score = (bullish_count - bearish_count) / total
            confidence = min(0.8, 0.3 + (total * 0.05))

        return SentimentSignal(
            source="news_rules",
            score=score,
            confidence=confidence,
            sample_size=len(headlines),
        )

    def _analyze_fear_greed(self, index: int) -> SentimentSignal:
        """Analyze Fear & Greed Index."""
        # Convert 0-100 to -1 to 1
        score = (index - 50) / 50

        # Confidence is higher at extremes
        distance_from_neutral = abs(index - 50)
        confidence = 0.5 + (distance_from_neutral / 100)

        return SentimentSignal(
            source="fear_greed",
            score=score,
            confidence=confidence,
            sample_size=1,
        )

    def _analyze_market_action(
        self,
        price_change: float,
        volume_change: Optional[float],
    ) -> SentimentSignal:
        """Analyze price and volume action."""
        # Price change contributes to sentiment
        # Cap at ±10% for scoring
        price_score = max(-1.0, min(1.0, price_change / 10))

        # Volume confirms the move
        if volume_change is not None and volume_change > 0:
            confidence = min(0.9, 0.5 + (volume_change / 100))
        else:
            confidence = 0.5

        return SentimentSignal(
            source="market",
            score=price_score,
            confidence=confidence,
            sample_size=1,
        )

    def _aggregate_signals(
        self, signals: List[SentimentSignal]
    ) -> Tuple[float, float]:
        """Aggregate multiple signals into overall score."""
        if not signals:
            return 0.0, 0.0

        # Weighted average by confidence
        total_weight = sum(s.confidence for s in signals)
        if total_weight == 0:
            return 0.0, 0.0

        weighted_score = sum(s.score * s.confidence for s in signals) / total_weight

        # Overall confidence is average of individual confidences
        avg_confidence = total_weight / len(signals)

        return weighted_score, avg_confidence

    def _score_to_level(self, score: float) -> SentimentLevel:
        """Convert numeric score to sentiment level."""
        if score <= -0.6:
            return SentimentLevel.EXTREME_FEAR
        elif score <= -0.2:
            return SentimentLevel.FEAR
        elif score >= 0.6:
            return SentimentLevel.EXTREME_GREED
        elif score >= 0.2:
            return SentimentLevel.GREED
        else:
            return SentimentLevel.NEUTRAL


# === Convenience function ===

async def analyze_sentiment(
    symbol: str,
    news_headlines: List[str],
    fear_greed_index: Optional[int] = None,
    price_change_24h: Optional[float] = None,
    volume_change_24h: Optional[float] = None,
) -> SentimentArtifact:
    """Quick sentiment analysis using default analyzer."""
    analyzer = SentimentAnalyzer()
    return await analyzer.analyze(
        symbol=symbol,
        news_headlines=news_headlines,
        fear_greed_index=fear_greed_index,
        price_change_24h=price_change_24h,
        volume_change_24h=volume_change_24h,
    )
