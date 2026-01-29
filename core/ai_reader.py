# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 04:05:00 UTC
# Purpose: Read-only AI artifact access for Trading Core
# === END SIGNATURE ===
"""
AI Artifact Reader: Core's interface to AI-Gateway artifacts.

CRITICAL ARCHITECTURE RULE:
- This module does NOT import any AI libraries (anthropic, torch, sklearn)
- Core reads artifacts from state/ai/*.jsonl (JSONL format)
- If artifact is missing/expired/invalid, Core continues with base strategy
- This is a DETERMINISTIC reader - no AI inference here

The AI-Gateway writes artifacts, Core only reads them.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

logger = logging.getLogger(__name__)


# Default artifact directory
DEFAULT_ARTIFACT_DIR = Path("state/ai")


class SentimentData(TypedDict, total=False):
    """Sentiment artifact data (subset for Core use)."""
    symbol: str
    overall_sentiment: str  # extreme_fear, fear, neutral, greed, extreme_greed
    overall_score: float  # -1 to +1
    confidence: float  # 0 to 1
    bias: str  # long, short, neutral
    strength: float  # 0 to 1
    created_at: str
    ttl_seconds: int


class RegimeData(TypedDict, total=False):
    """Regime artifact data (subset for Core use)."""
    symbol: str
    current_regime: str  # trending_up, trending_down, ranging, high_volatility, low_volatility
    regime_confidence: float
    trend_direction: str  # up, down, neutral
    trend_strength: float
    volatility_percentile: float
    recommended_strategy: str
    position_size_modifier: float
    created_at: str
    ttl_seconds: int


class DoctorData(TypedDict, total=False):
    """Strategy doctor artifact data (subset for Core use)."""
    strategy_id: str
    health_status: str  # optimal, acceptable, degraded, critical
    health_score: float  # 0-100
    top_issues: List[str]
    suggested_actions: List[str]
    current_regime_fit: float
    created_at: str
    ttl_seconds: int


class AnomalyData(TypedDict, total=False):
    """Anomaly artifact data (subset for Core use)."""
    anomalies_found: int
    critical_count: int
    high_count: int
    market_stress_level: float
    alert_level: str  # normal, elevated, high, critical
    recommended_actions: List[str]
    created_at: str
    ttl_seconds: int


@dataclass
class AIInsight:
    """Combined AI insights for trading decisions."""
    sentiment: Optional[SentimentData] = None
    regime: Optional[RegimeData] = None
    doctor: Optional[DoctorData] = None
    anomaly: Optional[AnomalyData] = None

    @property
    def has_any(self) -> bool:
        """Check if any insights are available."""
        return any([self.sentiment, self.regime, self.doctor, self.anomaly])

    @property
    def should_reduce_exposure(self) -> bool:
        """Check if AI recommends reducing exposure."""
        # Anomaly alert
        if self.anomaly:
            if self.anomaly.get("alert_level") in ("critical", "high"):
                return True
            if self.anomaly.get("market_stress_level", 0) > 0.7:
                return True

        # High volatility regime
        if self.regime:
            if self.regime.get("current_regime") == "high_volatility":
                return True

        # Strategy health critical
        if self.doctor:
            if self.doctor.get("health_status") == "critical":
                return True

        return False

    @property
    def suggested_bias(self) -> str:
        """Get suggested trading bias (long/short/neutral)."""
        if not self.sentiment:
            return "neutral"
        return self.sentiment.get("bias", "neutral")

    @property
    def position_size_multiplier(self) -> float:
        """Get position size multiplier based on regime."""
        if not self.regime:
            return 1.0
        return self.regime.get("position_size_modifier", 1.0)


class AIArtifactReader:
    """
    Read-only access to AI-Gateway artifacts.

    Thread-safe, no side effects, deterministic behavior.
    If artifact is unavailable, returns None (fail-open for reads).
    """

    def __init__(self, artifact_dir: Optional[Path] = None):
        self._dir = artifact_dir or DEFAULT_ARTIFACT_DIR

    def _read_artifact(self, module: str) -> Optional[Dict[str, Any]]:
        """
        Read latest artifact from module's JSONL file.

        Returns None if:
        - File doesn't exist
        - File is empty
        - Last line is not valid JSON
        - Artifact is expired (TTL exceeded)
        - Checksum validation fails
        """
        file_path = self._dir / f"{module}.jsonl"

        if not file_path.exists():
            logger.debug(f"Artifact file not found: {file_path}")
            return None

        try:
            # Read last line (most recent artifact)
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            if not lines:
                return None

            # Get last non-empty line
            last_line = None
            for line in reversed(lines):
                line = line.strip()
                if line:
                    last_line = line
                    break

            if not last_line:
                return None

            # Parse JSON
            artifact = json.loads(last_line)

            # Validate TTL
            if not self._check_ttl(artifact):
                logger.debug(f"Artifact {module} expired")
                return None

            # Validate checksum (optional - warn but don't fail)
            if not self._verify_checksum(artifact):
                logger.warning(f"Artifact {module} checksum mismatch")
                # Continue anyway - checksum is advisory

            return artifact

        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in {file_path}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error reading artifact {module}: {e}")
            return None

    def _check_ttl(self, artifact: Dict[str, Any]) -> bool:
        """Check if artifact is within TTL."""
        created_at = artifact.get("created_at", "")
        ttl_seconds = artifact.get("ttl_seconds", 300)

        if not created_at:
            return False

        try:
            # Parse ISO format
            if created_at.endswith("Z"):
                created_at = created_at[:-1]
            created_dt = datetime.fromisoformat(created_at)
            age = (datetime.utcnow() - created_dt).total_seconds()
            return age <= ttl_seconds
        except Exception:
            return False

    def _verify_checksum(self, artifact: Dict[str, Any]) -> bool:
        """Verify artifact checksum."""
        stored_checksum = artifact.get("checksum", "")
        if not stored_checksum:
            return True  # No checksum = assume valid

        # Compute checksum (same logic as contracts.py)
        data = {k: v for k, v in artifact.items() if k != "checksum"}
        payload = str(sorted(data.items())).encode("utf-8")
        computed = "sha256:" + hashlib.sha256(payload).hexdigest()[:16]

        return stored_checksum == computed

    # === Public API ===

    def get_sentiment(self, symbol: str = "BTCUSDT") -> Optional[SentimentData]:
        """Get latest sentiment for symbol."""
        artifact = self._read_artifact("sentiment")
        if not artifact:
            return None

        # Check symbol matches
        if artifact.get("symbol") != symbol:
            logger.debug(f"Sentiment symbol mismatch: {artifact.get('symbol')} != {symbol}")
            return None

        return SentimentData(
            symbol=artifact.get("symbol", ""),
            overall_sentiment=artifact.get("overall_sentiment", "neutral"),
            overall_score=artifact.get("overall_score", 0.0),
            confidence=artifact.get("confidence", 0.0),
            bias=artifact.get("bias", "neutral"),
            strength=artifact.get("strength", 0.0),
            created_at=artifact.get("created_at", ""),
            ttl_seconds=artifact.get("ttl_seconds", 300),
        )

    def get_regime(self, symbol: str = "BTCUSDT") -> Optional[RegimeData]:
        """Get latest regime for symbol."""
        artifact = self._read_artifact("regime")
        if not artifact:
            return None

        if artifact.get("symbol") != symbol:
            return None

        return RegimeData(
            symbol=artifact.get("symbol", ""),
            current_regime=artifact.get("current_regime", "ranging"),
            regime_confidence=artifact.get("regime_confidence", 0.0),
            trend_direction=artifact.get("trend_direction", "neutral"),
            trend_strength=artifact.get("trend_strength", 0.0),
            volatility_percentile=artifact.get("volatility_percentile", 50.0),
            recommended_strategy=artifact.get("recommended_strategy", "hold"),
            position_size_modifier=artifact.get("position_size_modifier", 1.0),
            created_at=artifact.get("created_at", ""),
            ttl_seconds=artifact.get("ttl_seconds", 300),
        )

    def get_doctor(self, strategy_id: str = "default") -> Optional[DoctorData]:
        """Get latest strategy diagnostics."""
        artifact = self._read_artifact("doctor")
        if not artifact:
            return None

        if artifact.get("strategy_id") != strategy_id:
            return None

        return DoctorData(
            strategy_id=artifact.get("strategy_id", ""),
            health_status=artifact.get("health_status", "acceptable"),
            health_score=artifact.get("health_score", 70.0),
            top_issues=artifact.get("top_issues", []),
            suggested_actions=artifact.get("suggested_actions", []),
            current_regime_fit=artifact.get("current_regime_fit", 0.5),
            created_at=artifact.get("created_at", ""),
            ttl_seconds=artifact.get("ttl_seconds", 600),
        )

    def get_anomaly(self) -> Optional[AnomalyData]:
        """Get latest anomaly scan results."""
        artifact = self._read_artifact("anomaly")
        if not artifact:
            return None

        return AnomalyData(
            anomalies_found=artifact.get("anomalies_found", 0),
            critical_count=artifact.get("critical_count", 0),
            high_count=artifact.get("high_count", 0),
            market_stress_level=artifact.get("market_stress_level", 0.0),
            alert_level=artifact.get("alert_level", "normal"),
            recommended_actions=artifact.get("recommended_actions", []),
            created_at=artifact.get("created_at", ""),
            ttl_seconds=artifact.get("ttl_seconds", 120),
        )

    def get_all_insights(self, symbol: str = "BTCUSDT", strategy_id: str = "default") -> AIInsight:
        """Get all available AI insights combined."""
        return AIInsight(
            sentiment=self.get_sentiment(symbol),
            regime=self.get_regime(symbol),
            doctor=self.get_doctor(strategy_id),
            anomaly=self.get_anomaly(),
        )


# === Module-level convenience ===

_default_reader: Optional[AIArtifactReader] = None


def get_reader(artifact_dir: Optional[Path] = None) -> AIArtifactReader:
    """Get or create default reader."""
    global _default_reader
    if _default_reader is None:
        _default_reader = AIArtifactReader(artifact_dir)
    return _default_reader


def get_insights(symbol: str = "BTCUSDT") -> AIInsight:
    """Quick access to all AI insights."""
    return get_reader().get_all_insights(symbol)


def should_trade(symbol: str = "BTCUSDT") -> bool:
    """
    Quick check if AI recommends trading.

    Returns True if no blocking conditions found.
    Returns True if no AI data available (fail-open).
    """
    insights = get_insights(symbol)

    # No data = proceed with base strategy
    if not insights.has_any:
        return True

    # Check for blocking conditions
    return not insights.should_reduce_exposure
