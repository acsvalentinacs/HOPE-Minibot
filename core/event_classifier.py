# === AI SIGNATURE ===
# Created by: Kirill Dev
# Created at: 2026-01-19 18:24:32 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 11:30:00 UTC
# === END SIGNATURE ===
"""
HOPE/NORE Event Classifier v1.0

Classifies market events by type and calculates impact score.

Event types (per Opinion1):
- market: price moves, volume spikes, liquidations
- regulation: legal/policy news (SEC, bans, approvals)
- listing: exchange listings, delistings
- exploit: hacks, vulnerabilities, rug pulls
- macro: fed rates, inflation, geopolitical
- institutional: ETF, corporate treasury, fund moves

Impact scoring (0.0 to 1.0):
- 0.0-0.3: Low impact (noise)
- 0.3-0.6: Medium impact (worth monitoring)
- 0.6-0.8: High impact (actionable)
- 0.8-1.0: Critical impact (immediate action)

Fail-closed: Unknown events = neutral/0.3 impact.

Usage:
    from core.event_classifier import EventClassifier, classify_news

    classifier = EventClassifier()
    classified = classifier.classify(news_item)
    high_impact = [e for e in events if e.impact_score >= 0.6]
"""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from typing import List, Optional, Dict, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ClassifiedEvent:
    """Event with classification and impact score."""
    title: str
    source: str
    link: str
    pub_date: str
    event_type: str  # market, regulation, listing, exploit, macro, institutional
    impact_score: float  # 0.0 to 1.0
    sentiment: str  # bullish, bearish, neutral
    affected_assets: List[str]  # ["BTC", "ETH", ...]
    keywords_matched: List[str]  # For debugging/transparency


# Classification patterns with base impact scores
EVENT_PATTERNS: Dict[str, List[Tuple[str, float]]] = {
    "regulation": [
        (r"\bSEC\b", 0.7),
        (r"\bCFTC\b", 0.6),
        (r"\bban(?:ned|s|ning)?\b", 0.8),
        (r"\bapprove[ds]?\b.*\bETF\b", 0.9),
        (r"\bETF\b.*\bapprove[ds]?\b", 0.9),
        (r"\breject(?:ed|s)?\b.*\bETF\b", 0.7),
        (r"\bregulat(?:ion|ory|ed|es|or)\b", 0.5),
        (r"\blegal\b", 0.4),
        (r"\blaw(?:suit|s)?\b", 0.6),
        (r"\bsubpoena\b", 0.6),
        (r"\bindictment\b", 0.7),
        (r"\bcompliance\b", 0.4),
        (r"\blicen(?:se|sing|sed)\b", 0.5),
        (r"\bMiCA\b", 0.5),
        (r"\bFATF\b", 0.5),
    ],
    "listing": [
        (r"\blist(?:ed|ing|s)?\b.*\b(?:Binance|Coinbase|Kraken)\b", 0.7),
        (r"\b(?:Binance|Coinbase|Kraken)\b.*\blist(?:ed|ing|s)?\b", 0.7),
        (r"\bdelist(?:ed|ing|s)?\b", 0.6),
        (r"\bnew\s+trading\s+pair\b", 0.4),
        (r"\bfutures?\s+launch\b", 0.5),
        (r"\bperp(?:etual)?\s+contract\b", 0.4),
    ],
    "exploit": [
        (r"\bhack(?:ed|er|s|ing)?\b", 0.9),
        (r"\bexploit(?:ed|s)?\b", 0.85),
        (r"\bvulnerabilit(?:y|ies)\b", 0.7),
        (r"\brug\s*pull\b", 0.9),
        (r"\bdrain(?:ed|s|ing)?\b.*\bwallet\b", 0.8),
        (r"\bstolen\b", 0.8),
        (r"\bbreach\b", 0.7),
        (r"\bcompromise[ds]?\b", 0.75),
        (r"\bmalware\b", 0.6),
        (r"\bphishing\b", 0.5),
        (r"\b51%\s*attack\b", 0.9),
        (r"\bdouble\s*spend\b", 0.85),
    ],
    "macro": [
        (r"\bFed(?:eral\s+Reserve)?\b", 0.6),
        (r"\binterest\s+rate\b", 0.65),
        (r"\brate\s+(?:hike|cut)\b", 0.7),
        (r"\binflation\b", 0.5),
        (r"\bCPI\b", 0.55),
        (r"\bGDP\b", 0.5),
        (r"\bunemployment\b", 0.45),
        (r"\brecession\b", 0.7),
        (r"\bTreasury\b.*\byield\b", 0.5),
        (r"\bdollar\s+(?:index|strength|weakness)\b", 0.5),
        (r"\bgeopolitic(?:al|s)?\b", 0.55),
        (r"\bwar\b", 0.7),
        (r"\bsanction(?:ed|s|ing)?\b", 0.65),
        (r"\btariff\b", 0.7),
        (r"\btrade\s+war\b", 0.75),
        (r"\bmarket\s+clos(?:ed|ure)\b", 0.5),
    ],
    "institutional": [
        (r"\bETF\b(?!\s+reject)", 0.7),
        (r"\bBlackRock\b", 0.75),
        (r"\bFidelity\b", 0.7),
        (r"\bGrayscale\b", 0.65),
        (r"\bMicroStrategy\b", 0.6),
        (r"\bTesla\b.*\b(?:BTC|Bitcoin)\b", 0.8),
        (r"\binstitutional\b", 0.5),
        (r"\bcorporate\s+treasury\b", 0.6),
        (r"\bhedge\s+fund\b", 0.5),
        (r"\bpension\s+fund\b", 0.6),
        (r"\bsovereign\s+wealth\b", 0.7),
        (r"\bAUM\b", 0.4),
        (r"\binflow(?:s)?\b", 0.5),
        (r"\boutflow(?:s)?\b", 0.5),
        (r"\bwhale\b", 0.4),
    ],
    "market": [
        (r"\$\d+\s*(?:m|b)illion.*\bliquidat", 0.85),  # $XXX million liquidated
        (r"\bliquidat(?:ion|ed|es|ing)\b", 0.6),
        (r"\b(?:long|short)\s+squeeze\b", 0.7),
        (r"\bshort\s+interest\b", 0.5),
        (r"\bopen\s+interest\b", 0.4),
        (r"\bfunding\s+rate\b", 0.4),
        (r"\bbreakout\b", 0.5),
        (r"\bbreakdown\b", 0.5),
        (r"\ball[- ]time\s+high\b", 0.7),
        (r"\bATH\b", 0.7),
        (r"\bcapitulation\b", 0.6),
        (r"\brally\b", 0.5),
        (r"\bcrash\b", 0.7),
        (r"\bdump\b", 0.55),
        (r"\bpump\b", 0.5),
        (r"\bvolatility\b", 0.4),
    ],
}

# Sentiment modifiers
BULLISH_KEYWORDS = [
    "surge", "soar", "rally", "bullish", "gain", "rise", "jump", "boost",
    "adoption", "growth", "record", "breakthrough", "milestone", "partnership",
    "approved", "acceptance", "accumulate", "buy", "long", "moon", "pump",
]

BEARISH_KEYWORDS = [
    "crash", "plunge", "dump", "bearish", "drop", "fall", "decline", "loss",
    "fear", "panic", "sell-off", "selloff", "rejection", "ban", "hack",
    "exploit", "vulnerability", "lawsuit", "indictment", "short", "rekt",
]

# Asset detection patterns
ASSET_PATTERNS = {
    "BTC": [r"\bBTC\b", r"\bBitcoin\b"],
    "ETH": [r"\bETH\b", r"\bEthereum\b"],
    "BNB": [r"\bBNB\b", r"\bBinance\s+Coin\b"],
    "XRP": [r"\bXRP\b", r"\bRipple\b"],
    "SOL": [r"\bSOL\b", r"\bSolana\b"],
    "ADA": [r"\bADA\b", r"\bCardano\b"],
    "DOGE": [r"\bDOGE\b", r"\bDogecoin\b"],
    "AVAX": [r"\bAVAX\b", r"\bAvalanche\b"],
    "LINK": [r"\bLINK\b", r"\bChainlink\b"],
    "DOT": [r"\bDOT\b", r"\bPolkadot\b"],
}

# Impact multipliers based on context
IMPACT_MULTIPLIERS = {
    "billion": 1.3,
    "million": 1.1,
    "massive": 1.2,
    "major": 1.15,
    "breaking": 1.25,
    "urgent": 1.2,
    "critical": 1.3,
    "historic": 1.2,
    "unprecedented": 1.25,
}


class EventClassifier:
    """
    Classifies news events and calculates impact score.

    Fail-closed: Unknown events get neutral sentiment and 0.3 impact.
    """

    def __init__(self):
        # Pre-compile all patterns for performance
        self._compiled_event_patterns: Dict[str, List[Tuple[re.Pattern, float]]] = {}
        for event_type, patterns in EVENT_PATTERNS.items():
            self._compiled_event_patterns[event_type] = [
                (re.compile(pattern, re.IGNORECASE), score)
                for pattern, score in patterns
            ]

        self._compiled_asset_patterns: Dict[str, List[re.Pattern]] = {}
        for asset, patterns in ASSET_PATTERNS.items():
            self._compiled_asset_patterns[asset] = [
                re.compile(p, re.IGNORECASE) for p in patterns
            ]

        self._compiled_multipliers: List[Tuple[re.Pattern, float]] = [
            (re.compile(rf"\b{kw}\b", re.IGNORECASE), mult)
            for kw, mult in IMPACT_MULTIPLIERS.items()
        ]

    def classify(
        self,
        title: str,
        source: str = "",
        link: str = "",
        pub_date: str = "",
    ) -> ClassifiedEvent:
        """
        Classify a single news item.

        Args:
            title: News headline
            source: News source
            link: URL to article
            pub_date: Publication date string

        Returns:
            ClassifiedEvent with type, impact, sentiment, and affected assets
        """
        # Detect event type and gather matches
        event_type = "market"  # Default
        max_score = 0.3  # Default impact for unknown
        keywords_matched: List[str] = []

        for etype, patterns in self._compiled_event_patterns.items():
            for pattern, base_score in patterns:
                match = pattern.search(title)
                if match:
                    keywords_matched.append(match.group(0))
                    if base_score > max_score:
                        max_score = base_score
                        event_type = etype

        # Apply multipliers
        impact_score = max_score
        for pattern, multiplier in self._compiled_multipliers:
            if pattern.search(title):
                impact_score *= multiplier

        # Cap at 1.0
        impact_score = min(1.0, impact_score)

        # Detect sentiment
        sentiment = self._detect_sentiment(title)

        # Detect affected assets
        affected_assets = self._detect_assets(title)

        return ClassifiedEvent(
            title=title,
            source=source,
            link=link,
            pub_date=pub_date,
            event_type=event_type,
            impact_score=round(impact_score, 2),
            sentiment=sentiment,
            affected_assets=affected_assets,
            keywords_matched=keywords_matched,
        )

    def classify_batch(
        self,
        news_items: List[Dict],
    ) -> List[ClassifiedEvent]:
        """
        Classify multiple news items.

        Args:
            news_items: List of dicts with title, source, link, pub_date

        Returns:
            List of ClassifiedEvent objects
        """
        results = []
        for item in news_items:
            try:
                classified = self.classify(
                    title=item.get("title", ""),
                    source=item.get("source", ""),
                    link=item.get("link", ""),
                    pub_date=item.get("pub_date", ""),
                )
                results.append(classified)
            except Exception as e:
                logger.warning("Failed to classify item: %s", e)
                # Fail-closed: skip bad items
                continue

        return results

    def filter_high_impact(
        self,
        events: List[ClassifiedEvent],
        threshold: float = 0.6,
    ) -> List[ClassifiedEvent]:
        """
        Filter events by impact threshold.

        Args:
            events: List of classified events
            threshold: Minimum impact score (default 0.6)

        Returns:
            Filtered list sorted by impact descending
        """
        filtered = [e for e in events if e.impact_score >= threshold]
        filtered.sort(key=lambda e: e.impact_score, reverse=True)
        return filtered

    def _detect_sentiment(self, text: str) -> str:
        """Detect bullish/bearish/neutral sentiment."""
        text_lower = text.lower()

        bullish_count = sum(1 for kw in BULLISH_KEYWORDS if kw in text_lower)
        bearish_count = sum(1 for kw in BEARISH_KEYWORDS if kw in text_lower)

        if bullish_count > bearish_count:
            return "bullish"
        elif bearish_count > bullish_count:
            return "bearish"
        return "neutral"

    def _detect_assets(self, text: str) -> List[str]:
        """Detect mentioned crypto assets."""
        assets = []
        for asset, patterns in self._compiled_asset_patterns.items():
            for pattern in patterns:
                if pattern.search(text):
                    assets.append(asset)
                    break
        return assets


def classify_news(news_items: List[Dict]) -> List[ClassifiedEvent]:
    """
    Convenience function to classify news items.

    Args:
        news_items: List of dicts with title, source, link, pub_date

    Returns:
        List of ClassifiedEvent objects
    """
    classifier = EventClassifier()
    return classifier.classify_batch(news_items)


def get_high_impact_news(
    news_items: List[Dict],
    threshold: float = 0.6,
) -> List[ClassifiedEvent]:
    """
    Get only high-impact news items.

    Args:
        news_items: Raw news items
        threshold: Minimum impact score

    Returns:
        Filtered and classified events
    """
    classifier = EventClassifier()
    classified = classifier.classify_batch(news_items)
    return classifier.filter_high_impact(classified, threshold)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test classification
    test_headlines = [
        "SEC Approves First Spot Bitcoin ETF in Historic Decision",
        "Ethereum Price Drops 5% Amid Market Volatility",
        "Binance Lists New Token PEPE2 - Trading Starts Today",
        "DeFi Protocol Hacked for $50 Million in Smart Contract Exploit",
        "Federal Reserve Signals Rate Cut in Q2 2026",
        "BlackRock Bitcoin ETF Sees $1 Billion Inflow in Single Day",
        "Minor Update to Coinbase Mobile App UI",
        "Bitcoin Mining Difficulty Reaches New All-Time High",
    ]

    classifier = EventClassifier()

    print("=== EVENT CLASSIFICATION TEST ===\n")
    for headline in test_headlines:
        event = classifier.classify(headline)
        print(f"📰 {headline[:60]}...")
        print(f"   Type: {event.event_type}")
        print(f"   Impact: {event.impact_score:.2f}")
        print(f"   Sentiment: {event.sentiment}")
        print(f"   Assets: {event.affected_assets}")
        print(f"   Keywords: {event.keywords_matched}")
        print()

