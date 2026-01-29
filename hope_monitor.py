# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 13:27:00 UTC
# Purpose: HOPE AI Live Monitor - TESTNET monitoring without stopping server
# === END SIGNATURE ===
"""
HOPE AI - Live Monitor v1.1 (FIXED)

Monitors running TESTNET without stopping it.
Collects data for TZ decision-making.

Usage:
    python hope_monitor.py              # Single report
    python hope_monitor.py --loop       # Loop every 60 sec
    python hope_monitor.py --json       # JSON output
    python hope_monitor.py --export FILE # Save to file

FIXES from v1.0:
- Correct field names from actual API response
- Proper error counting from modules
- Atomic file writes
- SHA256 checksums
"""

import json
import sys
import os
import time
import hashlib
import argparse
from datetime import datetime, timezone
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Any
from pathlib import Path

# Use requests (stdlib-compatible) or httpx
try:
    import requests
    HTTP_CLIENT = "requests"
except ImportError:
    try:
        import httpx
        HTTP_CLIENT = "httpx"
    except ImportError:
        print("ERROR: Neither requests nor httpx installed")
        print("Run: pip install requests")
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

BASE_URL = "http://127.0.0.1:8100"
TIMEOUT = 10

# TZ Targets (from docs/HOPE_AI_TRADING_TZ_v3.md)
TZ_TARGETS = {
    "min_outcomes": 50,
    "target_outcomes": 100,
    "min_win_rate": 0.40,
    "target_win_rate": 0.50,
    "min_mfe": 1.0,
    "target_mfe": 2.0,
    "max_mae": -2.0,
    "max_errors": 5,
}


# ═══════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class SystemHealth:
    status: str
    gateway_status: str
    uptime_seconds: float
    modules_running: List[str]
    modules_disabled: List[str]
    total_errors: int


@dataclass
class PriceFeedStatus:
    connected: bool
    symbols_count: int
    symbols: List[str]
    updates_count: int
    last_price_age_sec: Optional[int]


@dataclass
class OutcomeStats:
    active_signals: int
    completed_signals: int
    win_rate: float
    avg_mfe: float
    avg_mae: float


@dataclass
class ActiveSignal:
    signal_id: str
    symbol: str
    entry_price: float
    direction: str
    mfe: float
    mae: float
    prices_collected: int
    duration_sec: float


@dataclass
class MonitorReport:
    timestamp: str
    checksum: str  # sha256 of report content
    health: SystemHealth
    price_feed: PriceFeedStatus
    outcomes: OutcomeStats
    active_signals: List[ActiveSignal]
    decisions_count: int
    tz_compliance: Dict[str, bool]
    recommendations: List[str]


# ═══════════════════════════════════════════════════════════════════════════
# HTTP CLIENT WRAPPER
# ═══════════════════════════════════════════════════════════════════════════

def http_get(url: str, timeout: int = TIMEOUT) -> Optional[Dict]:
    """Universal HTTP GET with error handling."""
    try:
        if HTTP_CLIENT == "requests":
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
        else:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    return resp.json()
        return None
    except Exception as e:
        return {"_error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
# MONITOR CLASS
# ═══════════════════════════════════════════════════════════════════════════

class HopeMonitor:
    """Live monitor for HOPE AI TESTNET"""

    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url

    def _get(self, endpoint: str) -> Optional[Dict]:
        """Safe GET request."""
        return http_get(f"{self.base_url}{endpoint}")

    def get_health(self) -> Optional[Dict]:
        return self._get("/health")

    def get_status(self) -> Optional[Dict]:
        return self._get("/status")

    def get_price_feed(self) -> Optional[Dict]:
        return self._get("/price-feed/status")

    def get_outcomes_stats(self) -> Optional[Dict]:
        return self._get("/outcomes/stats")

    def get_outcomes_pending(self) -> Optional[Dict]:
        return self._get("/outcomes/pending")

    def get_outcomes_completed(self, limit: int = 20) -> Optional[Dict]:
        return self._get(f"/outcomes/completed?limit={limit}")

    def collect_report(self) -> MonitorReport:
        """Collect full monitoring report."""

        # Health check
        health_data = self.get_health() or {}
        status_data = self.get_status() or {}
        pf_data = self.get_price_feed() or {}

        # Parse modules - FIXED: use "module" not "name"
        modules_running = []
        modules_disabled = []
        total_errors = 0

        for m in status_data.get("modules", []):
            module_name = m.get("module", "unknown")
            if m.get("status") == "healthy":
                modules_running.append(module_name)
            elif m.get("status") == "disabled":
                modules_disabled.append(module_name)
            total_errors += m.get("error_count", 0)

        # Uptime from price feed stats
        uptime = pf_data.get("stats", {}).get("uptime_seconds", 0)

        health = SystemHealth(
            status=health_data.get("status", "unknown"),
            gateway_status=status_data.get("gateway_status", "unknown"),
            uptime_seconds=uptime,
            modules_running=modules_running,
            modules_disabled=modules_disabled,
            total_errors=total_errors,
        )

        # Price Feed - FIXED: correct nested structure
        stats = pf_data.get("stats", {})
        price_feed = PriceFeedStatus(
            connected=pf_data.get("connected", False),
            symbols_count=stats.get("symbols_tracked", 0),
            symbols=[],  # Not directly available
            updates_count=stats.get("price_updates", 0),
            last_price_age_sec=stats.get("last_price_age_sec"),
        )

        # Outcomes - FIXED: correct nested structure
        outcomes_data = self.get_outcomes_stats() or {}
        ostats = outcomes_data.get("stats", {})

        outcomes = OutcomeStats(
            active_signals=ostats.get("active_signals", 0),
            completed_signals=ostats.get("completed_signals", 0),
            win_rate=ostats.get("win_rate_5m", 0),
            avg_mfe=ostats.get("avg_mfe", 0),
            avg_mae=ostats.get("avg_mae", 0),
        )

        # Active symbols from outcomes
        active_symbols = outcomes_data.get("active_symbols", [])
        price_feed.symbols = active_symbols
        if active_symbols:
            price_feed.symbols_count = max(price_feed.symbols_count, len(active_symbols))

        # Active signals
        pending_data = self.get_outcomes_pending() or {}
        active_signals = []

        for sig in pending_data.get("signals", []):
            entry_time = sig.get("entry_time", "")
            duration = 0
            if entry_time:
                try:
                    entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                    duration = (datetime.now(timezone.utc) - entry_dt).total_seconds()
                except:
                    pass

            active_signals.append(ActiveSignal(
                signal_id=sig.get("signal_id", "")[:20],
                symbol=sig.get("symbol", ""),
                entry_price=sig.get("entry_price", 0),
                direction=sig.get("direction", "Long"),
                mfe=sig.get("mfe", 0),
                mae=sig.get("mae", 0),
                prices_collected=sig.get("prices_collected", 0),
                duration_sec=round(duration, 1),
            ))

        # Decisions count (estimate from outcomes)
        decisions_count = outcomes.completed_signals + outcomes.active_signals

        # TZ Compliance check
        tz_compliance = self._check_tz_compliance(outcomes, health)

        # Recommendations
        recommendations = self._generate_recommendations(
            health, price_feed, outcomes, active_signals, tz_compliance
        )

        # Create report
        report = MonitorReport(
            timestamp=datetime.now(timezone.utc).isoformat() + "Z",
            checksum="",  # Will be set below
            health=health,
            price_feed=price_feed,
            outcomes=outcomes,
            active_signals=active_signals,
            decisions_count=decisions_count,
            tz_compliance=tz_compliance,
            recommendations=recommendations,
        )

        # Calculate checksum
        report.checksum = self._calculate_checksum(report)

        return report

    def _calculate_checksum(self, report: MonitorReport) -> str:
        """Calculate SHA256 checksum of report content."""
        # Temporarily remove checksum for hashing
        data = asdict(report)
        data["checksum"] = ""
        content = json.dumps(data, sort_keys=True, default=str)
        return "sha256:" + hashlib.sha256(content.encode()).hexdigest()[:16]

    def _check_tz_compliance(self, outcomes: OutcomeStats, health: SystemHealth) -> Dict[str, bool]:
        """Check compliance with TZ targets."""
        return {
            "outcomes_min": outcomes.completed_signals >= TZ_TARGETS["min_outcomes"],
            "outcomes_target": outcomes.completed_signals >= TZ_TARGETS["target_outcomes"],
            "win_rate_min": outcomes.win_rate >= TZ_TARGETS["min_win_rate"],
            "win_rate_target": outcomes.win_rate >= TZ_TARGETS["target_win_rate"],
            "mfe_min": outcomes.avg_mfe >= TZ_TARGETS["min_mfe"],
            "mfe_target": outcomes.avg_mfe >= TZ_TARGETS["target_mfe"],
            "mae_ok": outcomes.avg_mae >= TZ_TARGETS["max_mae"],
            "errors_ok": health.total_errors <= TZ_TARGETS["max_errors"],
            "system_healthy": health.status == "ok",
        }

    def _generate_recommendations(
        self,
        health: SystemHealth,
        price_feed: PriceFeedStatus,
        outcomes: OutcomeStats,
        active_signals: List[ActiveSignal],
        tz_compliance: Dict[str, bool],
    ) -> List[str]:
        """Generate actionable recommendations."""
        recs = []

        # Critical issues
        if health.status != "ok":
            recs.append("[CRITICAL] System unhealthy - check logs")

        if not price_feed.connected:
            recs.append("[CRITICAL] Price feed disconnected - restart server")

        if health.total_errors > 0:
            recs.append(f"[WARNING] {health.total_errors} errors detected")

        # TZ compliance
        if not tz_compliance["outcomes_min"]:
            needed = TZ_TARGETS["min_outcomes"] - outcomes.completed_signals
            recs.append(f"[TZ] Need {needed} more outcomes for minimum target")

        if outcomes.win_rate < TZ_TARGETS["min_win_rate"] and outcomes.completed_signals >= 20:
            recs.append(f"[TZ] Win rate {outcomes.win_rate:.1%} below minimum {TZ_TARGETS['min_win_rate']:.0%}")

        if outcomes.avg_mfe < TZ_TARGETS["min_mfe"] and outcomes.completed_signals >= 20:
            recs.append(f"[TZ] Avg MFE {outcomes.avg_mfe:.2f}% below minimum {TZ_TARGETS['min_mfe']}%")

        # Positive indicators
        if tz_compliance["outcomes_target"]:
            recs.append("[OK] Outcome target reached!")

        if tz_compliance["win_rate_target"] and outcomes.completed_signals >= 20:
            recs.append("[OK] Win rate target achieved!")

        # Active signals analysis
        high_mfe = [s for s in active_signals if s.mfe > 2.0]
        if high_mfe:
            symbols = ", ".join(s.symbol for s in high_mfe)
            recs.append(f"[SIGNAL] High MFE: {symbols}")

        # Ready for next phase?
        if all([
            tz_compliance["outcomes_min"],
            tz_compliance["win_rate_min"],
            tz_compliance["errors_ok"],
            tz_compliance["system_healthy"],
        ]):
            recs.append("[READY] System meets minimum TZ requirements")

        if not recs:
            recs.append("[OK] System operating normally")

        return recs

    def print_report(self, report: MonitorReport):
        """Print formatted report to console (ASCII-safe)."""

        print()
        print("=" * 70)
        print("  HOPE AI TESTNET MONITOR v1.1")
        print(f"  {report.timestamp}")
        print(f"  Checksum: {report.checksum}")
        print("=" * 70)

        # Health
        health_icon = "[OK]" if report.health.status == "ok" else "[FAIL]"
        print(f"\n  SYSTEM HEALTH: {health_icon} {report.health.gateway_status.upper()}")
        print(f"  +-- Uptime: {report.health.uptime_seconds:.0f}s")
        print(f"  +-- Running: {', '.join(report.health.modules_running) or 'none'}")
        print(f"  +-- Disabled: {', '.join(report.health.modules_disabled) or 'none'}")
        print(f"  +-- Errors: {report.health.total_errors}")

        # Price Feed
        pf_icon = "[OK]" if report.price_feed.connected else "[FAIL]"
        print(f"\n  PRICE FEED: {pf_icon} {'CONNECTED' if report.price_feed.connected else 'DISCONNECTED'}")
        print(f"  +-- Symbols: {report.price_feed.symbols_count}")
        print(f"  +-- Updates: {report.price_feed.updates_count}")
        if report.price_feed.last_price_age_sec is not None:
            print(f"  +-- Last Price Age: {report.price_feed.last_price_age_sec}s")

        # Outcomes
        print(f"\n  OUTCOMES:")
        print(f"  +-- Active:    {report.outcomes.active_signals}")
        print(f"  +-- Completed: {report.outcomes.completed_signals}")
        print(f"  +-- Win Rate:  {report.outcomes.win_rate:.1%}")
        print(f"  +-- Avg MFE:   {report.outcomes.avg_mfe:+.2f}%")
        print(f"  +-- Avg MAE:   {report.outcomes.avg_mae:+.2f}%")

        # Active Signals
        if report.active_signals:
            print(f"\n  ACTIVE SIGNALS ({len(report.active_signals)}):")
            for sig in report.active_signals[:10]:  # Limit to 10
                mfe_icon = "[UP]" if sig.mfe > 1 else "[--]"
                print(f"  | {mfe_icon} {sig.symbol:10} @ {sig.entry_price:<10.4f} | MFE: {sig.mfe:+6.2f}% | MAE: {sig.mae:+6.2f}% | {sig.prices_collected} prices | {sig.duration_sec:.0f}s")

        # TZ Compliance
        print(f"\n  TZ COMPLIANCE:")
        for key, passed in report.tz_compliance.items():
            icon = "[OK]" if passed else "[X]"
            print(f"  | {icon} {key}")

        # Recommendations
        print(f"\n  RECOMMENDATIONS:")
        for rec in report.recommendations:
            print(f"  | {rec}")

        print()
        print("=" * 70)
        print("  Press Ctrl+C to stop | --loop for continuous monitoring")
        print("=" * 70)
        print()


def atomic_write(path: Path, content: str) -> None:
    """Atomic write with fsync."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="HOPE AI TESTNET Monitor v1.1")
    parser.add_argument("--loop", action="store_true", help="Continuous monitoring")
    parser.add_argument("--interval", type=int, default=60, help="Loop interval seconds")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--export", type=str, help="Export to file (atomic write)")
    parser.add_argument("--url", type=str, default=BASE_URL, help="Server URL")

    args = parser.parse_args()

    monitor = HopeMonitor(base_url=args.url)

    try:
        iteration = 0
        while True:
            iteration += 1
            report = monitor.collect_report()

            if args.json:
                print(json.dumps(asdict(report), indent=2, default=str))
            else:
                monitor.print_report(report)

            if args.export:
                export_path = Path(args.export)
                content = json.dumps(asdict(report), indent=2, default=str, ensure_ascii=False)
                atomic_write(export_path, content)
                print(f"Exported to {export_path} (iteration {iteration})")

            if not args.loop:
                break

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\nMonitor stopped.")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
