# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 03:56:00 UTC
# Purpose: Market anomaly detection and alerting
# === END SIGNATURE ===
"""
Anomaly Scanner: Detect unusual market conditions.

Identifies:
- Price spikes/crashes
- Volume surges
- Whale movements
- Correlation breaks
- Liquidity anomalies

Writes AnomalyScannerArtifact to state/ai/anomaly.jsonl.
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional

from ...contracts import (
    AnomalyEvent,
    AnomalyScannerArtifact,
    AnomalySeverity,
    create_artifact_id,
)
from ...jsonl_writer import write_artifact
from ...status_manager import get_status_manager

logger = logging.getLogger(__name__)


# Anomaly detection thresholds
PRICE_SPIKE_THRESHOLD = 0.05  # 5% in short period
VOLUME_SURGE_THRESHOLD = 3.0  # 3x average volume
WHALE_TRADE_USD = 1_000_000  # $1M single trade
CORRELATION_BREAK_THRESHOLD = 0.5  # Deviation from normal correlation


class AnomalyScanner:
    """
    Market anomaly detection for HOPE AI-Gateway.

    Scans for unusual market conditions that may indicate:
    - Manipulation (pump/dump)
    - Large player activity (whales)
    - Market stress
    - Technical failures
    """

    def __init__(self, ttl_seconds: int = 120):
        self._ttl = ttl_seconds
        self._status = get_status_manager()

    def scan(
        self,
        tickers: List[Dict[str, Any]],
        recent_trades: Optional[List[Dict[str, Any]]] = None,
        historical_volumes: Optional[Dict[str, List[float]]] = None,
        correlation_matrix: Optional[Dict[str, float]] = None,
    ) -> AnomalyScannerArtifact:
        """
        Scan for market anomalies.

        Args:
            tickers: Current ticker data with price, volume, change
            recent_trades: Recent large trades (optional)
            historical_volumes: Historical volume data per symbol (optional)
            correlation_matrix: Normal correlation values (optional)

        Returns:
            AnomalyScannerArtifact with detected anomalies
        """
        try:
            events: List[AnomalyEvent] = []

            # 1. Price anomalies
            price_events = self._scan_price_anomalies(tickers)
            events.extend(price_events)

            # 2. Volume anomalies
            volume_events = self._scan_volume_anomalies(tickers, historical_volumes)
            events.extend(volume_events)

            # 3. Whale movements
            if recent_trades:
                whale_events = self._scan_whale_activity(recent_trades)
                events.extend(whale_events)

            # 4. Correlation breaks
            if correlation_matrix:
                corr_events = self._scan_correlation_breaks(tickers, correlation_matrix)
                events.extend(corr_events)

            # Calculate stress level
            market_stress = self._calculate_market_stress(events, tickers)

            # Determine alert level
            critical_count = sum(1 for e in events if e.severity == AnomalySeverity.CRITICAL)
            high_count = sum(1 for e in events if e.severity == AnomalySeverity.HIGH)

            if critical_count > 0:
                alert_level = "critical"
            elif high_count >= 2:
                alert_level = "high"
            elif len(events) >= 3:
                alert_level = "elevated"
            else:
                alert_level = "normal"

            # Generate recommendations
            recommendations = self._generate_recommendations(events, alert_level)

            # Create artifact
            artifact = AnomalyScannerArtifact(
                artifact_id=create_artifact_id("anomaly"),
                ttl_seconds=self._ttl,
                anomalies_found=len(events),
                critical_count=critical_count,
                high_count=high_count,
                events=events,
                market_stress_level=market_stress,
                correlation_stability=1.0 - len([e for e in events if "correlation" in e.anomaly_type.lower()]) * 0.1,
                alert_level=alert_level,
                recommended_actions=recommendations,
            )

            # Write to JSONL
            if write_artifact(artifact.with_checksum()):
                self._status.mark_healthy("anomaly")
            else:
                self._status.mark_warning("anomaly", "Write failed")

            return artifact

        except Exception as e:
            logger.error(f"Anomaly scan failed: {e}")
            self._status.mark_error("anomaly", str(e))
            raise

    def _scan_price_anomalies(self, tickers: List[Dict[str, Any]]) -> List[AnomalyEvent]:
        """Detect price spikes and crashes."""
        events = []

        for ticker in tickers:
            symbol = ticker.get("symbol", "UNKNOWN")
            change = ticker.get("priceChangePercent", 0)

            if isinstance(change, str):
                try:
                    change = float(change)
                except ValueError:
                    continue

            change_pct = change / 100

            # Significant price movement
            if abs(change_pct) > PRICE_SPIKE_THRESHOLD:
                if change_pct > 0:
                    anomaly_type = "price_spike"
                    description = f"{symbol}: +{change_pct:.1%} за 24ч - резкий рост"
                else:
                    anomaly_type = "price_crash"
                    description = f"{symbol}: {change_pct:.1%} за 24ч - резкое падение"

                severity = self._classify_price_severity(abs(change_pct))

                events.append(AnomalyEvent(
                    anomaly_type=anomaly_type,
                    severity=severity,
                    description=description,
                    detected_at=datetime.utcnow(),
                    affected_symbols=[symbol],
                    metrics={"change_pct": change_pct},
                ))

        return events

    def _scan_volume_anomalies(
        self,
        tickers: List[Dict[str, Any]],
        historical: Optional[Dict[str, List[float]]],
    ) -> List[AnomalyEvent]:
        """Detect volume surges."""
        events = []

        for ticker in tickers:
            symbol = ticker.get("symbol", "UNKNOWN")
            volume = ticker.get("quoteVolume", 0)

            if isinstance(volume, str):
                try:
                    volume = float(volume)
                except ValueError:
                    continue

            # Compare to historical if available
            if historical and symbol in historical:
                hist_volumes = historical[symbol]
                if hist_volumes:
                    avg_volume = statistics.mean(hist_volumes)
                    if avg_volume > 0:
                        ratio = volume / avg_volume
                        if ratio > VOLUME_SURGE_THRESHOLD:
                            severity = AnomalySeverity.HIGH if ratio > 5 else AnomalySeverity.MEDIUM

                            events.append(AnomalyEvent(
                                anomaly_type="volume_surge",
                                severity=severity,
                                description=f"{symbol}: объем {ratio:.1f}x от среднего - аномальная активность",
                                detected_at=datetime.utcnow(),
                                affected_symbols=[symbol],
                                metrics={"volume_ratio": ratio, "current_volume": volume},
                            ))

        return events

    def _scan_whale_activity(self, trades: List[Dict[str, Any]]) -> List[AnomalyEvent]:
        """Detect whale (large) trades."""
        events = []

        for trade in trades:
            usd_value = trade.get("usd_value", 0)
            symbol = trade.get("symbol", "UNKNOWN")
            side = trade.get("side", "unknown")

            if usd_value >= WHALE_TRADE_USD:
                severity = AnomalySeverity.CRITICAL if usd_value >= 10_000_000 else AnomalySeverity.HIGH

                events.append(AnomalyEvent(
                    anomaly_type="whale_move",
                    severity=severity,
                    description=f"Крупная сделка {symbol}: ${usd_value:,.0f} ({side})",
                    detected_at=datetime.utcnow(),
                    affected_symbols=[symbol],
                    metrics={"usd_value": usd_value, "side": side},
                ))

        return events

    def _scan_correlation_breaks(
        self,
        tickers: List[Dict[str, Any]],
        normal_correlations: Dict[str, float],
    ) -> List[AnomalyEvent]:
        """Detect correlation breakdowns between assets."""
        events = []

        # Build current changes map
        changes = {}
        for ticker in tickers:
            symbol = ticker.get("symbol", "")
            change = ticker.get("priceChangePercent", 0)
            if isinstance(change, str):
                try:
                    change = float(change)
                except ValueError:
                    continue
            changes[symbol] = change

        # Check BTC correlation for alts
        btc_change = changes.get("BTCUSDT", 0)

        for symbol, change in changes.items():
            if symbol == "BTCUSDT":
                continue

            # Expected correlation with BTC
            expected_corr = normal_correlations.get(f"{symbol}_BTC", 0.7)

            # Actual correlation (simplified: same direction)
            if btc_change != 0:
                direction_match = (change > 0) == (btc_change > 0)
                magnitude_ratio = abs(change / btc_change) if btc_change != 0 else 1

                # Correlation break: opposite direction or very different magnitude
                if not direction_match and abs(change) > 3:
                    events.append(AnomalyEvent(
                        anomaly_type="correlation_break",
                        severity=AnomalySeverity.MEDIUM,
                        description=f"{symbol} движется против BTC: {change:+.1f}% vs BTC {btc_change:+.1f}%",
                        detected_at=datetime.utcnow(),
                        affected_symbols=[symbol, "BTCUSDT"],
                        metrics={
                            "symbol_change": change,
                            "btc_change": btc_change,
                            "expected_correlation": expected_corr,
                        },
                    ))

        return events

    def _classify_price_severity(self, abs_change: float) -> AnomalySeverity:
        """Classify price change severity."""
        if abs_change > 0.20:  # >20%
            return AnomalySeverity.CRITICAL
        elif abs_change > 0.10:  # >10%
            return AnomalySeverity.HIGH
        elif abs_change > 0.05:  # >5%
            return AnomalySeverity.MEDIUM
        else:
            return AnomalySeverity.LOW

    def _calculate_market_stress(
        self,
        events: List[AnomalyEvent],
        tickers: List[Dict[str, Any]],
    ) -> float:
        """Calculate overall market stress level (0-1)."""
        stress = 0.0

        # Event-based stress
        for event in events:
            if event.severity == AnomalySeverity.CRITICAL:
                stress += 0.3
            elif event.severity == AnomalySeverity.HIGH:
                stress += 0.15
            elif event.severity == AnomalySeverity.MEDIUM:
                stress += 0.05

        # Market-wide movement stress
        changes = []
        for ticker in tickers:
            change = ticker.get("priceChangePercent", 0)
            if isinstance(change, str):
                try:
                    change = float(change)
                except ValueError:
                    continue
            changes.append(abs(change))

        if changes:
            avg_change = statistics.mean(changes)
            if avg_change > 5:  # >5% average move
                stress += 0.2
            elif avg_change > 3:
                stress += 0.1

        return min(1.0, stress)

    def _generate_recommendations(
        self,
        events: List[AnomalyEvent],
        alert_level: str,
    ) -> List[str]:
        """Generate actionable recommendations based on anomalies."""
        recommendations = []

        if alert_level == "critical":
            recommendations.append("⚠️ КРИТИЧНО: Приостановить автоматическую торговлю")
            recommendations.append("Проверить позиции и стоп-лоссы")

        if alert_level in ("critical", "high"):
            recommendations.append("Снизить размер новых позиций на 50%")

        # Specific recommendations based on event types
        event_types = {e.anomaly_type for e in events}

        if "whale_move" in event_types:
            recommendations.append("Крупный игрок активен - ожидать волатильность")

        if "correlation_break" in event_types:
            recommendations.append("Корреляции нарушены - проверить хеджи")

        if "volume_surge" in event_types:
            recommendations.append("Аномальный объем - возможен прорыв")

        if not recommendations:
            recommendations.append("Рынок в норме - продолжать работу")

        return recommendations[:4]


# === Convenience function ===

def scan_anomalies(
    tickers: List[Dict[str, Any]],
    recent_trades: Optional[List[Dict[str, Any]]] = None,
) -> AnomalyScannerArtifact:
    """Quick anomaly scan."""
    scanner = AnomalyScanner()
    return scanner.scan(tickers, recent_trades)
