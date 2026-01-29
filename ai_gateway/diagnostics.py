# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 16:10:00 UTC
# Purpose: Health checks and diagnostics for AI-Gateway
# === END SIGNATURE ===
"""
AI-Gateway Diagnostics: Health checks for all components.

Provides:
- Component connectivity checks
- API key validation
- Model persistence verification
- Full gateway health report
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """Result of a single health check."""
    name: str
    passed: bool
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0


@dataclass
class HealthReport:
    """Full health report for the gateway."""
    timestamp: str
    overall_status: str  # healthy, degraded, unhealthy
    checks: List[CheckResult]
    warnings: List[str]
    errors: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "overall_status": self.overall_status,
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "message": c.message,
                    "details": c.details,
                    "duration_ms": c.duration_ms,
                }
                for c in self.checks
            ],
            "warnings": self.warnings,
            "errors": self.errors,
        }


class GatewayDiagnostics:
    """Health checker for AI-Gateway components."""

    def __init__(self, state_dir: Optional[Path] = None):
        self._state_dir = state_dir or Path("state/ai")

    async def check_filesystem(self) -> CheckResult:
        """Check state directory exists and is writable."""
        import time
        start = time.perf_counter()

        try:
            # Check dir exists
            if not self._state_dir.exists():
                self._state_dir.mkdir(parents=True, exist_ok=True)

            # Test write
            test_file = self._state_dir / ".health_check"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink()

            duration = (time.perf_counter() - start) * 1000
            return CheckResult(
                name="filesystem",
                passed=True,
                message="State directory writable",
                details={"path": str(self._state_dir)},
                duration_ms=duration,
            )

        except Exception as e:
            duration = (time.perf_counter() - start) * 1000
            return CheckResult(
                name="filesystem",
                passed=False,
                message=f"Filesystem error: {e}",
                details={"path": str(self._state_dir)},
                duration_ms=duration,
            )

    async def check_anthropic_key(self) -> CheckResult:
        """Check if Anthropic API key is set."""
        import time
        start = time.perf_counter()

        key = os.environ.get("ANTHROPIC_API_KEY", "")
        has_key = bool(key and key.startswith("sk-ant-"))

        duration = (time.perf_counter() - start) * 1000

        if has_key:
            return CheckResult(
                name="anthropic_api_key",
                passed=True,
                message="API key configured",
                details={"prefix": key[:10] + "..."},
                duration_ms=duration,
            )
        else:
            return CheckResult(
                name="anthropic_api_key",
                passed=False,
                message="ANTHROPIC_API_KEY not set or invalid",
                details={},
                duration_ms=duration,
            )

    async def check_binance_connectivity(self) -> CheckResult:
        """Check Binance API connectivity."""
        import time
        start = time.perf_counter()

        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get("https://api.binance.com/api/v3/ping")
                resp.raise_for_status()

            duration = (time.perf_counter() - start) * 1000
            return CheckResult(
                name="binance_api",
                passed=True,
                message="Binance API reachable",
                details={"latency_ms": duration},
                duration_ms=duration,
            )

        except Exception as e:
            duration = (time.perf_counter() - start) * 1000
            return CheckResult(
                name="binance_api",
                passed=False,
                message=f"Binance API unreachable: {e}",
                details={},
                duration_ms=duration,
            )

    async def check_anthropic_connectivity(self) -> CheckResult:
        """Check Anthropic API connectivity (DNS only, no actual call)."""
        import time
        start = time.perf_counter()

        try:
            import socket
            socket.setdefaulttimeout(5.0)
            socket.gethostbyname("api.anthropic.com")

            duration = (time.perf_counter() - start) * 1000
            return CheckResult(
                name="anthropic_dns",
                passed=True,
                message="api.anthropic.com resolves",
                details={"latency_ms": duration},
                duration_ms=duration,
            )

        except Exception as e:
            duration = (time.perf_counter() - start) * 1000
            return CheckResult(
                name="anthropic_dns",
                passed=False,
                message=f"api.anthropic.com unreachable: {e}",
                details={},
                duration_ms=duration,
            )

    async def check_anomaly_model(self) -> CheckResult:
        """Check if anomaly model is persisted."""
        import time
        start = time.perf_counter()

        model_path = self._state_dir / "anomaly_model.joblib"
        exists = model_path.exists()

        duration = (time.perf_counter() - start) * 1000

        if exists:
            size = model_path.stat().st_size
            return CheckResult(
                name="anomaly_model",
                passed=True,
                message="Model file exists",
                details={"path": str(model_path), "size_bytes": size},
                duration_ms=duration,
            )
        else:
            return CheckResult(
                name="anomaly_model",
                passed=False,  # Warning, not critical
                message="Model not trained yet",
                details={"path": str(model_path)},
                duration_ms=duration,
            )

    async def check_modules_status(self) -> CheckResult:
        """Check status manager modules."""
        import time
        start = time.perf_counter()

        try:
            from .status_manager import get_status_manager

            sm = get_status_manager()
            modules = {}
            enabled_count = 0
            error_count = 0

            for module_id in ["sentiment", "regime", "doctor", "anomaly"]:
                status = sm.get_status(module_id)
                enabled = sm.is_enabled(module_id)
                modules[module_id] = {
                    "status": status.value,
                    "enabled": enabled,
                    "emoji": sm.get_emoji(module_id),
                }
                if enabled:
                    enabled_count += 1
                if status.value == "error":
                    error_count += 1

            duration = (time.perf_counter() - start) * 1000

            passed = error_count == 0
            message = f"{enabled_count}/4 enabled, {error_count} errors"

            return CheckResult(
                name="modules_status",
                passed=passed,
                message=message,
                details={"modules": modules},
                duration_ms=duration,
            )

        except Exception as e:
            duration = (time.perf_counter() - start) * 1000
            return CheckResult(
                name="modules_status",
                passed=False,
                message=f"Status check failed: {e}",
                details={},
                duration_ms=duration,
            )

    async def run_all_checks(self) -> HealthReport:
        """Run all health checks and produce report."""
        checks = await asyncio.gather(
            self.check_filesystem(),
            self.check_anthropic_key(),
            self.check_binance_connectivity(),
            self.check_anthropic_connectivity(),
            self.check_anomaly_model(),
            self.check_modules_status(),
        )

        warnings = []
        errors = []

        for check in checks:
            if not check.passed:
                if check.name in ("anomaly_model",):
                    warnings.append(f"{check.name}: {check.message}")
                else:
                    errors.append(f"{check.name}: {check.message}")

        # Determine overall status
        if errors:
            overall = "unhealthy"
        elif warnings:
            overall = "degraded"
        else:
            overall = "healthy"

        return HealthReport(
            timestamp=datetime.utcnow().isoformat() + "Z",
            overall_status=overall,
            checks=list(checks),
            warnings=warnings,
            errors=errors,
        )


# === Convenience ===

async def run_health_check() -> Dict[str, Any]:
    """Run health check and return dict."""
    diag = GatewayDiagnostics()
    report = await diag.run_all_checks()
    return report.to_dict()


def format_health_report_telegram(report: HealthReport) -> str:
    """Format health report for Telegram display."""
    emoji_map = {
        "healthy": "🟢",
        "degraded": "🟡",
        "unhealthy": "🔴",
    }

    lines = [
        "╔══════════════════════════════╗",
        "║   🏥 AI-GATEWAY ДИАГНОСТИКА  ║",
        "╠══════════════════════════════╣",
        f"║ Статус: {emoji_map.get(report.overall_status, '⚪')} {report.overall_status.upper()}",
        "╟──────────────────────────────╢",
    ]

    for check in report.checks:
        emoji = "✅" if check.passed else "❌"
        name = check.name[:15].ljust(15)
        lines.append(f"║ {emoji} {name} {check.duration_ms:.0f}ms")

    if report.warnings:
        lines.append("╟──────────────────────────────╢")
        lines.append("║ ⚠️ ПРЕДУПРЕЖДЕНИЯ:")
        for w in report.warnings[:3]:
            lines.append(f"║   • {w[:25]}...")

    if report.errors:
        lines.append("╟──────────────────────────────╢")
        lines.append("║ 🔴 ОШИБКИ:")
        for e in report.errors[:3]:
            lines.append(f"║   • {e[:25]}...")

    lines.append("╚══════════════════════════════╝")

    return "\n".join(lines)
