# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 03:51:00 UTC
# Purpose: Strategy health diagnostics and recommendations
# === END SIGNATURE ===
"""
Strategy Doctor: AI-powered strategy health diagnostics.

Analyzes trading strategy performance and provides recommendations.
Uses optional Claude API for deep analysis, rule-based fallback.
Writes StrategyDoctorArtifact to state/ai/doctor.jsonl.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from ...contracts import (
    DiagnosticItem,
    HealthStatus,
    StrategyDoctorArtifact,
    create_artifact_id,
)
from ...jsonl_writer import write_artifact
from ...status_manager import get_status_manager

logger = logging.getLogger(__name__)


# Health score thresholds
HEALTH_OPTIMAL = 80
HEALTH_ACCEPTABLE = 60
HEALTH_DEGRADED = 40

# Performance thresholds
MIN_WIN_RATE = 0.4
MIN_PROFIT_FACTOR = 1.0
MIN_SHARPE = 0.5
MAX_DRAWDOWN = 0.20  # 20%


class StrategyDoctor:
    """
    Strategy health diagnostic system for HOPE AI-Gateway.

    Analyzes:
    - Performance metrics (win rate, profit factor, Sharpe)
    - Risk metrics (drawdown, exposure)
    - Market fit (regime compatibility)
    - Execution quality

    Provides actionable recommendations for improvement.
    """

    def __init__(
        self,
        anthropic_api_key: Optional[str] = None,
        use_ai: bool = True,
        ttl_seconds: int = 600,
    ):
        self._api_key = anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
        self._use_ai = use_ai and bool(self._api_key)
        self._ttl = ttl_seconds
        self._status = get_status_manager()

        self._client = None

    def _get_client(self):
        """Lazy init Anthropic client."""
        if self._client is None and self._use_ai:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self._api_key)
            except ImportError:
                logger.warning("anthropic package not installed")
                self._use_ai = False
            except Exception as e:
                logger.error(f"Failed to init Anthropic client: {e}")
                self._use_ai = False
        return self._client

    async def diagnose(
        self,
        strategy_id: str,
        trades: List[Dict[str, Any]],
        current_regime: Optional[str] = None,
        equity_curve: Optional[List[float]] = None,
    ) -> StrategyDoctorArtifact:
        """
        Run full diagnostic on trading strategy.

        Args:
            strategy_id: Strategy identifier
            trades: List of trade records with pnl, entry_time, exit_time, side
            current_regime: Current market regime (optional)
            equity_curve: Equity values over time (optional)

        Returns:
            StrategyDoctorArtifact with diagnosis
        """
        try:
            diagnostics: List[DiagnosticItem] = []

            # Calculate performance metrics
            metrics = self._calculate_metrics(trades, equity_curve)

            # Performance diagnostics
            perf_diags = self._diagnose_performance(metrics)
            diagnostics.extend(perf_diags)

            # Risk diagnostics
            risk_diags = self._diagnose_risk(metrics)
            diagnostics.extend(risk_diags)

            # Market fit diagnostics
            if current_regime:
                fit_diags = self._diagnose_market_fit(trades, current_regime)
                diagnostics.extend(fit_diags)

            # Calculate health score
            health_score = self._calculate_health_score(metrics, diagnostics)
            health_status = self._score_to_status(health_score)

            # Get top issues
            top_issues = self._extract_top_issues(diagnostics)

            # Generate recommendations
            if self._use_ai and len(trades) >= 10:
                suggestions = await self._get_ai_recommendations(
                    strategy_id, metrics, diagnostics
                )
            else:
                suggestions = self._get_rule_recommendations(metrics, diagnostics)

            # Calculate regime fit
            regime_fit = self._calculate_regime_fit(trades, current_regime)

            # Create artifact
            artifact = StrategyDoctorArtifact(
                artifact_id=create_artifact_id("doctor", strategy_id),
                ttl_seconds=self._ttl,
                strategy_id=strategy_id,
                health_status=health_status,
                health_score=health_score,
                win_rate=metrics.get("win_rate", 0),
                profit_factor=metrics.get("profit_factor", 0),
                sharpe_ratio=metrics.get("sharpe_ratio", 0),
                max_drawdown=metrics.get("max_drawdown", 0),
                diagnostics=diagnostics,
                top_issues=top_issues,
                suggested_actions=suggestions,
                current_regime_fit=regime_fit,
            )

            # Write to JSONL
            if write_artifact(artifact.with_checksum()):
                self._status.mark_healthy("doctor")
            else:
                self._status.mark_warning("doctor", "Write failed")

            return artifact

        except Exception as e:
            logger.error(f"Strategy diagnosis failed: {e}")
            self._status.mark_error("doctor", str(e))
            raise

    def _calculate_metrics(
        self,
        trades: List[Dict[str, Any]],
        equity_curve: Optional[List[float]],
    ) -> Dict[str, float]:
        """Calculate strategy performance metrics."""
        if not trades:
            return {
                "win_rate": 0,
                "profit_factor": 0,
                "sharpe_ratio": 0,
                "max_drawdown": 0,
                "total_trades": 0,
                "avg_win": 0,
                "avg_loss": 0,
            }

        pnls = [t.get("pnl", 0) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        win_rate = len(wins) / len(pnls) if pnls else 0
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0

        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        if profit_factor == float("inf"):
            profit_factor = 10.0  # Cap at 10

        avg_win = gross_profit / len(wins) if wins else 0
        avg_loss = gross_loss / len(losses) if losses else 0

        # Sharpe ratio (simplified)
        if len(pnls) > 1:
            import statistics
            mean_return = statistics.mean(pnls)
            std_return = statistics.stdev(pnls)
            sharpe_ratio = (mean_return / std_return) * (252 ** 0.5) if std_return > 0 else 0
        else:
            sharpe_ratio = 0

        # Max drawdown from equity curve or trades
        if equity_curve and len(equity_curve) > 1:
            max_drawdown = self._calculate_max_drawdown(equity_curve)
        else:
            # Estimate from cumulative PnL
            cumsum = []
            total = 0
            for p in pnls:
                total += p
                cumsum.append(total)
            max_drawdown = self._calculate_max_drawdown(cumsum) if cumsum else 0

        return {
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": max_drawdown,
            "total_trades": len(trades),
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
        }

    def _calculate_max_drawdown(self, equity: List[float]) -> float:
        """Calculate maximum drawdown from equity curve."""
        if not equity:
            return 0

        peak = equity[0]
        max_dd = 0

        for value in equity:
            if value > peak:
                peak = value
            dd = (peak - value) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        return max_dd

    def _diagnose_performance(self, metrics: Dict[str, float]) -> List[DiagnosticItem]:
        """Generate performance diagnostics."""
        diagnostics = []

        win_rate = metrics.get("win_rate", 0)
        if win_rate < MIN_WIN_RATE:
            diagnostics.append(DiagnosticItem(
                category="performance",
                finding=f"Низкий винрейт: {win_rate:.1%}",
                severity="warning",
                recommendation="Улучшить фильтрацию входов или тайминг",
            ))
        elif win_rate > 0.7:
            diagnostics.append(DiagnosticItem(
                category="performance",
                finding=f"Высокий винрейт: {win_rate:.1%}",
                severity="info",
                recommendation="Проверить соотношение риск/прибыль",
            ))

        profit_factor = metrics.get("profit_factor", 0)
        if profit_factor < MIN_PROFIT_FACTOR:
            diagnostics.append(DiagnosticItem(
                category="performance",
                finding=f"Профит-фактор ниже 1: {profit_factor:.2f}",
                severity="critical",
                recommendation="Стратегия убыточна - требуется пересмотр",
            ))

        sharpe = metrics.get("sharpe_ratio", 0)
        if sharpe < MIN_SHARPE:
            diagnostics.append(DiagnosticItem(
                category="performance",
                finding=f"Низкий коэф. Шарпа: {sharpe:.2f}",
                severity="warning",
                recommendation="Доходность не оправдывает риск",
            ))

        return diagnostics

    def _diagnose_risk(self, metrics: Dict[str, float]) -> List[DiagnosticItem]:
        """Generate risk diagnostics."""
        diagnostics = []

        max_dd = metrics.get("max_drawdown", 0)
        if max_dd > MAX_DRAWDOWN:
            severity = "critical" if max_dd > 0.3 else "warning"
            diagnostics.append(DiagnosticItem(
                category="risk",
                finding=f"Высокая просадка: {max_dd:.1%}",
                severity=severity,
                recommendation="Снизить размер позиций или добавить стоп-лоссы",
            ))

        avg_win = metrics.get("avg_win", 0)
        avg_loss = metrics.get("avg_loss", 0)
        if avg_loss > 0 and avg_win / avg_loss < 1.5:
            diagnostics.append(DiagnosticItem(
                category="risk",
                finding=f"Плохое соотношение win/loss: {avg_win:.2f}/{avg_loss:.2f}",
                severity="warning",
                recommendation="Увеличить тейк-профит или уменьшить стоп-лосс",
            ))

        return diagnostics

    def _diagnose_market_fit(
        self,
        trades: List[Dict[str, Any]],
        current_regime: str,
    ) -> List[DiagnosticItem]:
        """Diagnose strategy fit with current market regime."""
        diagnostics = []

        # Analyze recent trade performance
        recent_trades = trades[-20:] if len(trades) > 20 else trades
        recent_pnls = [t.get("pnl", 0) for t in recent_trades]
        recent_win_rate = len([p for p in recent_pnls if p > 0]) / len(recent_pnls) if recent_pnls else 0

        # Check regime compatibility
        regime_advice = {
            "trending_up": "Стратегия должна следовать тренду вверх",
            "trending_down": "Стратегия должна следовать тренду вниз",
            "ranging": "Использовать mean reversion или снизить активность",
            "high_volatility": "Снизить размер позиций, расширить стопы",
            "low_volatility": "Возможно затишье перед движением - осторожность",
        }

        advice = regime_advice.get(current_regime, "Режим не определен")

        if recent_win_rate < 0.4:
            diagnostics.append(DiagnosticItem(
                category="market_fit",
                finding=f"Стратегия плохо работает в режиме '{current_regime}'",
                severity="warning",
                recommendation=advice,
            ))
        else:
            diagnostics.append(DiagnosticItem(
                category="market_fit",
                finding=f"Стратегия адаптирована к режиму '{current_regime}'",
                severity="info",
                recommendation=advice,
            ))

        return diagnostics

    def _calculate_health_score(
        self,
        metrics: Dict[str, float],
        diagnostics: List[DiagnosticItem],
    ) -> float:
        """Calculate overall health score (0-100)."""
        score = 70  # Base score

        # Performance adjustments
        win_rate = metrics.get("win_rate", 0)
        score += (win_rate - 0.5) * 20  # ±10 points

        profit_factor = metrics.get("profit_factor", 0)
        if profit_factor >= 1.5:
            score += 10
        elif profit_factor < 1.0:
            score -= 20

        sharpe = metrics.get("sharpe_ratio", 0)
        if sharpe >= 1.0:
            score += 10
        elif sharpe < 0.5:
            score -= 10

        # Risk adjustments
        max_dd = metrics.get("max_drawdown", 0)
        if max_dd > 0.3:
            score -= 20
        elif max_dd > 0.2:
            score -= 10
        elif max_dd < 0.1:
            score += 5

        # Diagnostic penalties
        critical_count = sum(1 for d in diagnostics if d.severity == "critical")
        warning_count = sum(1 for d in diagnostics if d.severity == "warning")
        score -= critical_count * 15
        score -= warning_count * 5

        return max(0, min(100, score))

    def _score_to_status(self, score: float) -> HealthStatus:
        """Convert health score to status."""
        if score >= HEALTH_OPTIMAL:
            return HealthStatus.OPTIMAL
        elif score >= HEALTH_ACCEPTABLE:
            return HealthStatus.ACCEPTABLE
        elif score >= HEALTH_DEGRADED:
            return HealthStatus.DEGRADED
        else:
            return HealthStatus.CRITICAL

    def _extract_top_issues(self, diagnostics: List[DiagnosticItem]) -> List[str]:
        """Extract top issues from diagnostics."""
        # Sort by severity
        severity_order = {"critical": 0, "warning": 1, "info": 2}
        sorted_diags = sorted(
            diagnostics,
            key=lambda d: severity_order.get(d.severity, 3)
        )

        return [d.finding for d in sorted_diags[:3]]

    async def _get_ai_recommendations(
        self,
        strategy_id: str,
        metrics: Dict[str, float],
        diagnostics: List[DiagnosticItem],
    ) -> List[str]:
        """Get AI-powered recommendations using Claude."""
        client = self._get_client()
        if not client:
            return self._get_rule_recommendations(metrics, diagnostics)

        try:
            issues = [d.finding for d in diagnostics if d.severity in ("critical", "warning")]

            prompt = f"""Analyze this trading strategy and provide 3 specific actionable recommendations.

Strategy: {strategy_id}
Metrics:
- Win Rate: {metrics.get('win_rate', 0):.1%}
- Profit Factor: {metrics.get('profit_factor', 0):.2f}
- Sharpe Ratio: {metrics.get('sharpe_ratio', 0):.2f}
- Max Drawdown: {metrics.get('max_drawdown', 0):.1%}
- Total Trades: {metrics.get('total_trades', 0)}

Issues found:
{chr(10).join(f"- {i}" for i in issues)}

Respond in Russian with exactly 3 numbered recommendations (1-2 sentences each).
Focus on practical, implementable changes."""

            message = client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}]
            )

            response = message.content[0].text.strip()

            # Parse numbered recommendations
            recommendations = []
            for line in response.split("\n"):
                line = line.strip()
                if line and (line[0].isdigit() or line.startswith("-")):
                    # Remove number prefix
                    clean = re.sub(r"^[\d\.\-\)\s]+", "", line).strip()
                    if clean:
                        recommendations.append(clean)

            return recommendations[:3] if recommendations else self._get_rule_recommendations(metrics, diagnostics)

        except Exception as e:
            logger.warning(f"AI recommendations failed: {e}")
            return self._get_rule_recommendations(metrics, diagnostics)

    def _get_rule_recommendations(
        self,
        metrics: Dict[str, float],
        diagnostics: List[DiagnosticItem],
    ) -> List[str]:
        """Generate rule-based recommendations."""
        recommendations = []

        win_rate = metrics.get("win_rate", 0)
        profit_factor = metrics.get("profit_factor", 0)
        max_dd = metrics.get("max_drawdown", 0)

        if profit_factor < 1.0:
            recommendations.append("Критично: пересмотреть логику входа/выхода - стратегия убыточна")

        if win_rate < 0.4:
            recommendations.append("Добавить фильтры для улучшения качества входов")

        if max_dd > 0.2:
            recommendations.append("Внедрить динамическое управление размером позиции")

        if not recommendations:
            recommendations.append("Стратегия работает стабильно - продолжать мониторинг")

        return recommendations[:3]

    def _calculate_regime_fit(
        self,
        trades: List[Dict[str, Any]],
        current_regime: Optional[str],
    ) -> float:
        """Calculate strategy fit with current regime (0-1)."""
        if not trades or not current_regime:
            return 0.5

        # Use recent trades
        recent = trades[-10:] if len(trades) > 10 else trades
        pnls = [t.get("pnl", 0) for t in recent]
        win_rate = len([p for p in pnls if p > 0]) / len(pnls) if pnls else 0

        # Higher win rate = better fit
        return min(1.0, win_rate * 1.5)


# === Convenience function ===

async def diagnose_strategy(
    strategy_id: str,
    trades: List[Dict[str, Any]],
    current_regime: Optional[str] = None,
) -> StrategyDoctorArtifact:
    """Quick strategy diagnosis."""
    doctor = StrategyDoctor()
    return await doctor.diagnose(strategy_id, trades, current_regime)
