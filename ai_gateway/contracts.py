# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 03:32:00 UTC
# Purpose: Pydantic models for AI-Gateway artifacts with checksum validation
# === END SIGNATURE ===
"""
AI-Gateway Contracts: Data models for artifact exchange.

All artifacts written by AI-Gateway must conform to these contracts.
Core reads artifacts without importing AI libraries.
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, validator


class ModuleStatus(str, Enum):
    """Module health status indicators."""
    HEALTHY = "healthy"      # 🟢 Working normally
    WARNING = "warning"      # 🟡 Needs attention
    ERROR = "error"          # 🔴 Failed, needs repair
    DISABLED = "disabled"    # ⚪ Turned off by user


class SentimentLevel(str, Enum):
    """Market sentiment classification."""
    EXTREME_FEAR = "extreme_fear"
    FEAR = "fear"
    NEUTRAL = "neutral"
    GREED = "greed"
    EXTREME_GREED = "extreme_greed"


class MarketRegime(str, Enum):
    """Market regime classification."""
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"


class HealthStatus(str, Enum):
    """Strategy health status."""
    OPTIMAL = "optimal"
    ACCEPTABLE = "acceptable"
    DEGRADED = "degraded"
    CRITICAL = "critical"


class AnomalySeverity(str, Enum):
    """Anomaly severity levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# === Base Artifact Model ===

class BaseArtifact(BaseModel):
    """Base model for all AI artifacts with checksum and TTL."""

    artifact_id: str = Field(..., description="Unique artifact identifier")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    ttl_seconds: int = Field(default=300, description="Time-to-live in seconds")
    checksum: Optional[str] = Field(None, description="SHA256 of payload")
    version: str = Field(default="1.0.0")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() + "Z"
        }

    def compute_checksum(self) -> str:
        """Compute SHA256 checksum of artifact payload."""
        # Exclude checksum field from hash
        data = self.dict(exclude={"checksum"})
        payload = str(sorted(data.items())).encode("utf-8")
        return "sha256:" + hashlib.sha256(payload).hexdigest()[:16]

    def with_checksum(self) -> "BaseArtifact":
        """Return copy with computed checksum."""
        self.checksum = self.compute_checksum()
        return self

    def is_valid(self) -> bool:
        """Verify checksum matches payload."""
        if not self.checksum:
            return False
        return self.checksum == self.compute_checksum()

    def is_expired(self) -> bool:
        """Check if artifact has expired based on TTL."""
        age = (datetime.utcnow() - self.created_at).total_seconds()
        return age > self.ttl_seconds


# === Sentiment Artifacts ===

class SentimentSignal(BaseModel):
    """Individual sentiment signal from a source."""
    source: str  # "news", "social", "market"
    score: float = Field(..., ge=-1.0, le=1.0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    sample_size: int = Field(default=0)


class SentimentArtifact(BaseArtifact):
    """Sentiment analysis result artifact."""

    module: str = Field(default="sentiment")
    symbol: str = Field(..., description="Trading symbol (e.g., BTCUSDT)")

    overall_sentiment: SentimentLevel
    overall_score: float = Field(..., ge=-1.0, le=1.0, description="-1=extreme fear, +1=extreme greed")
    confidence: float = Field(..., ge=0.0, le=1.0)

    signals: List[SentimentSignal] = Field(default_factory=list)

    # Action recommendation
    bias: str = Field(default="neutral", description="long/short/neutral")
    strength: float = Field(default=0.0, ge=0.0, le=1.0)

    # Source data reference
    news_count: int = Field(default=0)
    social_mentions: int = Field(default=0)

    reasoning: Optional[str] = Field(None, description="AI explanation (for logs)")


# === Regime Detection Artifacts ===

class RegimeIndicator(BaseModel):
    """Individual regime indicator."""
    name: str  # "trend_strength", "volatility", "volume_profile"
    value: float
    threshold_low: float
    threshold_high: float
    signal: str  # "bullish", "bearish", "neutral"


class RegimeArtifact(BaseArtifact):
    """Market regime detection result artifact."""

    module: str = Field(default="regime")
    symbol: str
    timeframe: str = Field(default="4h")

    current_regime: MarketRegime
    regime_confidence: float = Field(..., ge=0.0, le=1.0)
    regime_duration_hours: float = Field(default=0.0)

    # Regime characteristics
    trend_direction: str = Field(default="neutral")  # up/down/neutral
    trend_strength: float = Field(default=0.0, ge=0.0, le=1.0)
    volatility_percentile: float = Field(default=50.0, ge=0.0, le=100.0)

    indicators: List[RegimeIndicator] = Field(default_factory=list)

    # Recommendation
    recommended_strategy: str = Field(default="hold")  # trend_follow/mean_revert/hold
    position_size_modifier: float = Field(default=1.0, ge=0.0, le=2.0)


# === Strategy Doctor Artifacts ===

class DiagnosticItem(BaseModel):
    """Individual diagnostic finding."""
    category: str  # "performance", "risk", "execution", "market_fit"
    finding: str
    severity: str = Field(default="info")  # info/warning/critical
    recommendation: Optional[str] = None


class StrategyDoctorArtifact(BaseArtifact):
    """Strategy health diagnostic artifact."""

    module: str = Field(default="doctor")
    strategy_id: str

    health_status: HealthStatus
    health_score: float = Field(..., ge=0.0, le=100.0)

    # Performance metrics
    win_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    profit_factor: float = Field(default=0.0)
    sharpe_ratio: float = Field(default=0.0)
    max_drawdown: float = Field(default=0.0)

    # Diagnostics
    diagnostics: List[DiagnosticItem] = Field(default_factory=list)

    # AI recommendations
    top_issues: List[str] = Field(default_factory=list)
    suggested_actions: List[str] = Field(default_factory=list)

    # Market fit
    current_regime_fit: float = Field(default=0.5, ge=0.0, le=1.0)
    recommended_adjustments: Dict[str, Any] = Field(default_factory=dict)


# === Anomaly Scanner Artifacts ===

class AnomalyEvent(BaseModel):
    """Individual anomaly event."""
    anomaly_type: str  # "price_spike", "volume_surge", "correlation_break", "whale_move"
    severity: AnomalySeverity
    description: str
    detected_at: datetime
    affected_symbols: List[str] = Field(default_factory=list)
    metrics: Dict[str, float] = Field(default_factory=dict)


class AnomalyScannerArtifact(BaseArtifact):
    """Anomaly detection result artifact."""

    module: str = Field(default="anomaly")
    scan_scope: str = Field(default="market")  # market/portfolio/single

    anomalies_found: int = Field(default=0)
    critical_count: int = Field(default=0)
    high_count: int = Field(default=0)

    events: List[AnomalyEvent] = Field(default_factory=list)

    # Market stress indicators
    market_stress_level: float = Field(default=0.0, ge=0.0, le=1.0)
    correlation_stability: float = Field(default=1.0, ge=0.0, le=1.0)

    # Recommendations
    alert_level: str = Field(default="normal")  # normal/elevated/high/critical
    recommended_actions: List[str] = Field(default_factory=list)


# === Module Status Artifact ===

class ModuleStatusArtifact(BaseArtifact):
    """Module health status artifact for Telegram display."""

    module: str = Field(default="status")

    modules: Dict[str, ModuleStatus] = Field(default_factory=dict)
    last_run_times: Dict[str, datetime] = Field(default_factory=dict)
    error_counts: Dict[str, int] = Field(default_factory=dict)

    # Overall gateway health
    gateway_status: ModuleStatus = Field(default=ModuleStatus.HEALTHY)
    active_modules: int = Field(default=0)
    total_modules: int = Field(default=4)

    def get_status_emoji(self, module_name: str) -> str:
        """Get emoji for module status."""
        status = self.modules.get(module_name, ModuleStatus.DISABLED)
        return {
            ModuleStatus.HEALTHY: "🟢",
            ModuleStatus.WARNING: "🟡",
            ModuleStatus.ERROR: "🔴",
            ModuleStatus.DISABLED: "⚪",
        }.get(status, "⚪")


# === Artifact Factory ===

def create_artifact_id(module: str, symbol: str = "GLOBAL") -> str:
    """Generate unique artifact ID."""
    ts = int(time.time() * 1000)
    payload = f"{module}:{symbol}:{ts}"
    short_hash = hashlib.sha256(payload.encode()).hexdigest()[:8]
    return f"{module}_{symbol}_{short_hash}"
