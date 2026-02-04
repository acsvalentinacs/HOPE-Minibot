#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 23:15:00 UTC
# Purpose: HOPE AI System Diagnostic Tool v2.0
# === END SIGNATURE ===
"""
HOPE AI - System Diagnostic Tool v2.0

Updated for HOPE v4.0 Trading Engine + Safety System

Changes from v1.0:
- Added core/signal_gate.py, core/adaptive_tp_engine.py, core/trading_engine.py
- Added core/live_trading_patch.py (CircuitBreaker, RateLimiter, HealthMonitor)
- Added execution/binance_live_client.py, execution/binance_oco_executor.py
- Added scripts/live_trader_v4.py, scripts/eye_of_god_adapter.py
- Added config/live_trade_policy.py
- Added learning/trade_outcome_logger.py
- Added tools/verify_live_ready.ps1
- New phase: "live_v4" for LIVE trading components

Usage:
    python hope_diagnostic_v2.py [--json] [--export FILE]
"""

import os
import sys
import json
import importlib
import importlib.util
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple
from hashlib import sha256
import subprocess


# ===============================================================================
# CONFIGURATION v2.0
# ===============================================================================

BASE_PATH = Path(".")

# Expected components - UPDATED for HOPE v4.0
EXPECTED_COMPONENTS = {
    # ---------------------------------------------------------------------------
    # AI GATEWAY - CORE
    # ---------------------------------------------------------------------------
    "ai_gateway/__init__.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "AI Gateway package init",
    },
    "ai_gateway/server.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "Main HTTP server",
        "test_import": "ai_gateway.server",
    },
    "ai_gateway/config.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "Configuration loader",
    },
    "ai_gateway/core/__init__.py": {
        "type": "module", "required": True, "phase": "base",
    },
    "ai_gateway/core/event_bus.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "Event Bus for signal routing",
        "test_import": "ai_gateway.core.event_bus",
    },
    "ai_gateway/core/decision_engine.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "Decision Engine (fail-closed)",
        "test_import": "ai_gateway.core.decision_engine",
    },
    "ai_gateway/core/mode_router.py": {
        "type": "module", "required": True, "phase": "3.1",
        "description": "Mode Router (SUPER_SCALP/SCALP/SWING)",
        "test_import": "ai_gateway.core.mode_router",
    },
    "ai_gateway/core/circuit_breaker.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "Circuit Breaker for loss protection",
    },

    # ---------------------------------------------------------------------------
    # CORE - TRADING ENGINE v4.0 (NEW)
    # ---------------------------------------------------------------------------
    "core/__init__.py": {
        "type": "module", "required": True, "phase": "live_v4",
        "description": "Core package init",
    },
    "core/signal_gate.py": {
        "type": "module", "required": True, "phase": "live_v4",
        "description": "Signal Gate - cannot bypass filter",
        "test_import": "core.signal_gate",
    },
    "core/adaptive_tp_engine.py": {
        "type": "module", "required": True, "phase": "live_v4",
        "description": "Adaptive TP/SL with R:R >= 2.5",
        "test_import": "core.adaptive_tp_engine",
    },
    "core/trading_engine.py": {
        "type": "module", "required": True, "phase": "live_v4",
        "description": "Full trading cycle orchestrator",
        "test_import": "core.trading_engine",
    },
    "core/live_trading_patch.py": {
        "type": "module", "required": True, "phase": "live_v4",
        "description": "Live Safety: CircuitBreaker, RateLimiter, HealthMonitor",
        "test_import": "core.live_trading_patch",
    },

    # ---------------------------------------------------------------------------
    # EXECUTION (NEW)
    # ---------------------------------------------------------------------------
    "execution/__init__.py": {
        "type": "module", "required": True, "phase": "live_v4",
        "description": "Execution package init",
    },
    "execution/binance_oco_executor.py": {
        "type": "module", "required": True, "phase": "live_v4",
        "description": "Binance OCO executor (MARKET + OCO)",
        "test_import": "execution.binance_oco_executor",
    },
    "execution/binance_live_client.py": {
        "type": "module", "required": True, "phase": "live_v4",
        "description": "Fail-closed Binance client + LIVE barrier",
        "test_import": "execution.binance_live_client",
    },

    # ---------------------------------------------------------------------------
    # CONFIG (NEW)
    # ---------------------------------------------------------------------------
    "config/__init__.py": {
        "type": "module", "required": True, "phase": "live_v4",
        "description": "Config package init",
    },
    "config/live_trade_policy.py": {
        "type": "module", "required": True, "phase": "live_v4",
        "description": "Blacklist, position limits",
        "test_import": "config.live_trade_policy",
    },
    "config/signal_filter_rules.json": {
        "type": "data", "required": True, "phase": "live_v4",
        "description": "Signal filter rules (delta, cooldown)",
        "validate": "json",
    },

    # ---------------------------------------------------------------------------
    # LEARNING (NEW)
    # ---------------------------------------------------------------------------
    "learning/__init__.py": {
        "type": "module", "required": True, "phase": "live_v4",
        "description": "Learning package init",
    },
    "learning/trade_outcome_logger.py": {
        "type": "module", "required": True, "phase": "live_v4",
        "description": "Trade outcome logger for ML",
        "test_import": "learning.trade_outcome_logger",
    },

    # ---------------------------------------------------------------------------
    # SCRIPTS - TRADING (NEW/UPDATED)
    # ---------------------------------------------------------------------------
    "scripts/pump_detector.py": {
        "type": "script", "required": True, "phase": "base",
        "description": "Pump detector with LIVE safety integration",
    },
    "scripts/autotrader.py": {
        "type": "script", "required": True, "phase": "base",
        "description": "AutoTrader with Eye of God V3",
    },
    "scripts/order_executor.py": {
        "type": "script", "required": True, "phase": "base",
        "description": "Order executor with trailing stop",
    },
    "scripts/live_trader_v4.py": {
        "type": "script", "required": True, "phase": "live_v4",
        "description": "LIVE trading entrypoint",
    },
    "scripts/eye_of_god_adapter.py": {
        "type": "script", "required": True, "phase": "live_v4",
        "description": "Eye of God adapter (.analyze() shim)",
    },
    "scripts/eye_of_god_v3.py": {
        "type": "script", "required": True, "phase": "live_v4",
        "description": "Eye of God V3 (Two-Chamber Architecture)",
    },
    "scripts/process_watchdog.py": {
        "type": "script", "required": True, "phase": "base",
        "description": "Process Watchdog with auto-restart",
    },

    # ---------------------------------------------------------------------------
    # TOOLS (NEW)
    # ---------------------------------------------------------------------------
    "tools/verify_live_ready.ps1": {
        "type": "script", "required": False, "phase": "live_v4",
        "description": "LIVE readiness verification",
    },

    # ---------------------------------------------------------------------------
    # AI GATEWAY - PATTERNS
    # ---------------------------------------------------------------------------
    "ai_gateway/patterns/__init__.py": {
        "type": "module", "required": True, "phase": "3.1",
    },
    "ai_gateway/patterns/pump_precursor_detector.py": {
        "type": "module", "required": True, "phase": "3.1",
        "description": "Pump Precursor Detector",
        "test_import": "ai_gateway.patterns.pump_precursor_detector",
    },

    # ---------------------------------------------------------------------------
    # AI GATEWAY - FEEDS
    # ---------------------------------------------------------------------------
    "ai_gateway/feeds/__init__.py": {
        "type": "module", "required": True, "phase": "base",
    },
    "ai_gateway/feeds/binance_ws.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "Binance WebSocket price feed",
    },
    "ai_gateway/feeds/binance_ws_enricher.py": {
        "type": "module", "required": False, "phase": "secret_ideas_p2",
        "description": "Binance WS Enricher (orderbook, trades)",
    },

    # ---------------------------------------------------------------------------
    # AI GATEWAY - INTEGRATIONS
    # ---------------------------------------------------------------------------
    "ai_gateway/integrations/__init__.py": {
        "type": "module", "required": True, "phase": "secret_ideas_p1",
    },
    "ai_gateway/integrations/moonbot_live.py": {
        "type": "module", "required": True, "phase": "secret_ideas_p1",
        "description": "MoonBot Live Signal Integration",
        "test_import": "ai_gateway.integrations.moonbot_live",
    },

    # ---------------------------------------------------------------------------
    # AI GATEWAY - MODULES
    # ---------------------------------------------------------------------------
    "ai_gateway/modules/__init__.py": {
        "type": "module", "required": True, "phase": "base",
    },
    "ai_gateway/modules/self_improver/__init__.py": {
        "type": "module", "required": True, "phase": "base",
    },
    "ai_gateway/modules/self_improver/outcome_tracker.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "Outcome Tracker (MFE/MAE)",
        "test_import": "ai_gateway.modules.self_improver.outcome_tracker",
    },
    "ai_gateway/modules/predictor/__init__.py": {
        "type": "module", "required": False, "phase": "3.1",
    },
    "ai_gateway/modules/predictor/signal_classifier.py": {
        "type": "module", "required": False, "phase": "3.1",
        "description": "Signal Classifier AI",
    },

    # ---------------------------------------------------------------------------
    # AI GATEWAY - TELEGRAM
    # ---------------------------------------------------------------------------
    "ai_gateway/telegram/__init__.py": {
        "type": "module", "required": False, "phase": "secret_ideas_p4",
    },
    "ai_gateway/telegram/commands.py": {
        "type": "module", "required": False, "phase": "secret_ideas_p4",
        "description": "Telegram /predict, /stats commands",
    },

    # ---------------------------------------------------------------------------
    # AI GATEWAY - UTILITIES
    # ---------------------------------------------------------------------------
    "ai_gateway/jsonl_writer.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "Atomic JSONL writer",
    },
    "ai_gateway/base_module.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "Base module class",
    },
    "ai_gateway/scheduler.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "Module scheduler",
    },
    "ai_gateway/status_manager.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "Status manager",
    },

    # ---------------------------------------------------------------------------
    # AI GATEWAY - MODELS
    # ---------------------------------------------------------------------------
    "ai_gateway/models/hope_model_v1.json": {
        "type": "data", "required": True, "phase": "3.1",
        "description": "Trained AI model v1",
        "validate": "json",
    },

    # ---------------------------------------------------------------------------
    # DATA FILES
    # ---------------------------------------------------------------------------
    "data/moonbot_signals/signals_20260129.jsonl": {
        "type": "data", "required": False, "phase": "3.1",
        "description": "Collected MoonBot signals",
        "validate": "jsonl",
    },
    "state/ai/decisions.jsonl": {
        "type": "data", "required": False, "phase": "secret_ideas_p1",
        "description": "AI decisions log",
        "validate": "jsonl",
    },
    "state/sources/sources.json": {
        "type": "data", "required": True, "phase": "base",
        "description": "Data sources registry",
        "validate": "json",
    },

    # ---------------------------------------------------------------------------
    # TESTS
    # ---------------------------------------------------------------------------
    "scripts/test_ai_gateway.py": {
        "type": "script", "required": True, "phase": "base",
        "description": "Integration tests",
    },
}

# Environment variables
EXPECTED_ENV_VARS = {
    "BINANCE_API_KEY": {"required": False, "phase": "live"},
    "BINANCE_API_SECRET": {"required": False, "phase": "live"},
    "BINANCE_TESTNET": {"required": False, "phase": "testnet"},
    "TELEGRAM_BOT_TOKEN": {"required": False, "phase": "base"},
    "HOPE_MODE": {"required": False, "phase": "live_v4", "default": "DRY"},
    "HOPE_LIVE_ACK": {"required": False, "phase": "live_v4", "default": ""},
    "AI_GATEWAY_PORT": {"required": False, "phase": "base", "default": "8100"},
}

# Python packages
EXPECTED_PACKAGES = {
    "fastapi": {"required": True, "min_version": "0.100.0"},
    "uvicorn": {"required": True},
    "pydantic": {"required": True, "min_version": "2.0.0"},
    "orjson": {"required": True},
    "httpx": {"required": True},
    "websockets": {"required": True},
    "python-binance": {"required": False, "import_name": "binance"},
    "python-telegram-bot": {"required": False, "import_name": "telegram"},
    "numpy": {"required": False},
    "pandas": {"required": False},
}


# ===============================================================================
# DATA CLASSES
# ===============================================================================

@dataclass
class ComponentStatus:
    path: str
    exists: bool
    works: bool
    required: bool
    phase: str
    description: str
    error: Optional[str] = None
    details: Dict = field(default_factory=dict)

    @property
    def status(self) -> str:
        if self.exists and self.works:
            return "OK"
        elif self.exists and not self.works:
            return "BROKEN"
        elif not self.exists and self.required:
            return "MISSING"
        elif not self.exists and not self.required:
            return "NOT_IMPL"
        return "UNKNOWN"


@dataclass
class DiagnosticReport:
    timestamp: str
    base_path: str
    version: str = "2.0"

    total_components: int = 0
    ok_count: int = 0
    broken_count: int = 0
    missing_count: int = 0
    not_impl_count: int = 0

    ok: List[ComponentStatus] = field(default_factory=list)
    broken: List[ComponentStatus] = field(default_factory=list)
    missing: List[ComponentStatus] = field(default_factory=list)
    not_implemented: List[ComponentStatus] = field(default_factory=list)

    env_ok: List[str] = field(default_factory=list)
    env_missing: List[str] = field(default_factory=list)
    env_warnings: List[str] = field(default_factory=list)

    packages_ok: List[str] = field(default_factory=list)
    packages_missing: List[str] = field(default_factory=list)
    packages_outdated: List[str] = field(default_factory=list)

    orphaned: List[str] = field(default_factory=list)
    phases: Dict[str, Dict] = field(default_factory=dict)

    # NEW: Safety system status
    safety_status: Dict[str, any] = field(default_factory=dict)

    checksum: str = ""

    def calculate_checksum(self):
        data = f"{self.timestamp}:{self.ok_count}:{self.broken_count}:{self.missing_count}"
        self.checksum = f"sha256:{sha256(data.encode()).hexdigest()[:16]}"


# ===============================================================================
# DIAGNOSTIC FUNCTIONS
# ===============================================================================

def check_file_exists(path: Path) -> bool:
    return path.exists()


def validate_json(path: Path) -> Tuple[bool, Optional[str]]:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            json.load(f)
        return True, None
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {e}"
    except Exception as e:
        return False, str(e)


def validate_jsonl(path: Path) -> Tuple[bool, Optional[str], int]:
    try:
        count = 0
        with open(path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if line:
                    json.loads(line)
                    count += 1
        return True, None, count
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON at line {i}: {e}", 0
    except Exception as e:
        return False, str(e), 0


def check_import(module_name: str) -> Tuple[bool, Optional[str]]:
    try:
        importlib.import_module(module_name)
        return True, None
    except ImportError as e:
        return False, f"ImportError: {e}"
    except Exception as e:
        return False, f"Error: {e}"


def check_python_syntax(path: Path) -> Tuple[bool, Optional[str]]:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return True, None
        return False, result.stderr.strip()
    except Exception as e:
        return False, str(e)


def check_package(name: str, import_name: str = None, min_version: str = None) -> Tuple[bool, Optional[str], Optional[str]]:
    import_name = import_name or name
    try:
        module = importlib.import_module(import_name)
        version = getattr(module, '__version__', 'unknown')

        if min_version and version != 'unknown':
            try:
                from packaging import version as pkg_version
                if pkg_version.parse(version) < pkg_version.parse(min_version):
                    return True, f"outdated: {version} < {min_version}", version
            except ImportError:
                pass

        return True, None, version
    except ImportError:
        return False, "not installed", None
    except Exception as e:
        return False, str(e), None


def find_orphaned_files(base_path: Path, expected: Dict) -> List[str]:
    orphaned = []
    expected_paths = set(expected.keys())

    for folder in ["ai_gateway", "core", "execution", "config", "learning"]:
        folder_path = base_path / folder
        if folder_path.exists():
            for py_file in folder_path.rglob("*.py"):
                rel_path = str(py_file.relative_to(base_path)).replace("\\", "/")
                if rel_path not in expected_paths:
                    orphaned.append(rel_path)

    return orphaned


def check_safety_system(base_path: Path) -> Dict[str, any]:
    """Check LIVE safety system status."""
    status = {
        "circuit_breaker": False,
        "rate_limiter": False,
        "health_monitor": False,
        "live_barrier": False,
        "live_barrier_mode": "UNKNOWN",
    }

    try:
        sys.path.insert(0, str(base_path))
        from core.live_trading_patch import (
            get_circuit_breaker, get_rate_limiter,
            get_health_monitor, get_live_barrier
        )

        cb = get_circuit_breaker()
        status["circuit_breaker"] = True
        status["circuit_breaker_open"] = cb.is_open()

        rl = get_rate_limiter()
        status["rate_limiter"] = True

        hm = get_health_monitor()
        status["health_monitor"] = True
        status["health_status"] = hm.get_status()

        barrier = get_live_barrier()
        status["live_barrier"] = True
        status["live_barrier_mode"] = barrier.get_status()["effective_mode"]

    except Exception as e:
        status["error"] = str(e)

    return status


# ===============================================================================
# MAIN DIAGNOSTIC
# ===============================================================================

def run_diagnostic(base_path: Path = None) -> DiagnosticReport:
    if base_path is None:
        candidates = [
            Path("."),
            Path("C:/Users/kirillDev/Desktop/TradingBot/minibot"),
        ]
        for p in candidates:
            if (p / "ai_gateway").exists():
                base_path = p
                break
        else:
            base_path = Path(".")

    report = DiagnosticReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        base_path=str(base_path.absolute()),
    )

    sys.path.insert(0, str(base_path))
    phase_stats = {}

    # CHECK COMPONENTS
    for rel_path, spec in EXPECTED_COMPONENTS.items():
        full_path = base_path / rel_path

        status = ComponentStatus(
            path=rel_path,
            exists=check_file_exists(full_path),
            works=False,
            required=spec.get("required", False),
            phase=spec.get("phase", "unknown"),
            description=spec.get("description", ""),
        )

        phase = status.phase
        if phase not in phase_stats:
            phase_stats[phase] = {"total": 0, "ok": 0, "broken": 0, "missing": 0}
        phase_stats[phase]["total"] += 1

        if status.exists:
            comp_type = spec.get("type", "module")

            if comp_type == "module":
                syntax_ok, syntax_err = check_python_syntax(full_path)
                if not syntax_ok:
                    status.works = False
                    status.error = f"Syntax error: {syntax_err}"
                else:
                    if "test_import" in spec:
                        import_ok, import_err = check_import(spec["test_import"])
                        status.works = import_ok
                        if not import_ok:
                            status.error = import_err
                    else:
                        status.works = True

            elif comp_type == "data":
                validate = spec.get("validate")
                if validate == "json":
                    valid, err = validate_json(full_path)
                    status.works = valid
                    status.error = err
                elif validate == "jsonl":
                    valid, err, count = validate_jsonl(full_path)
                    status.works = valid
                    status.error = err
                    status.details["line_count"] = count
                else:
                    status.works = True

            elif comp_type == "script":
                if str(full_path).endswith(".py"):
                    syntax_ok, syntax_err = check_python_syntax(full_path)
                    status.works = syntax_ok
                    if not syntax_ok:
                        status.error = syntax_err
                else:
                    status.works = True

        report.total_components += 1

        if status.status == "OK":
            report.ok.append(status)
            report.ok_count += 1
            phase_stats[phase]["ok"] += 1
        elif status.status == "BROKEN":
            report.broken.append(status)
            report.broken_count += 1
            phase_stats[phase]["broken"] += 1
        elif status.status == "MISSING":
            report.missing.append(status)
            report.missing_count += 1
            phase_stats[phase]["missing"] += 1
        else:
            report.not_implemented.append(status)
            report.not_impl_count += 1

    # CHECK ENVIRONMENT VARIABLES
    for var_name, spec in EXPECTED_ENV_VARS.items():
        value = os.environ.get(var_name)
        if value:
            report.env_ok.append(var_name)
        elif spec.get("required"):
            report.env_missing.append(var_name)
        else:
            report.env_warnings.append(f"{var_name} (default: {spec.get('default', 'none')})")

    # CHECK PACKAGES
    for pkg_name, spec in EXPECTED_PACKAGES.items():
        import_name = spec.get("import_name", pkg_name)
        min_ver = spec.get("min_version")
        ok, err, version = check_package(pkg_name, import_name, min_ver)

        if ok and err is None:
            report.packages_ok.append(f"{pkg_name}=={version}")
        elif ok and "outdated" in str(err):
            report.packages_outdated.append(f"{pkg_name}: {err}")
        elif spec.get("required"):
            report.packages_missing.append(pkg_name)

    # FIND ORPHANED FILES
    report.orphaned = find_orphaned_files(base_path, EXPECTED_COMPONENTS)

    # CALCULATE PHASE COMPLETION
    for phase, stats in phase_stats.items():
        if stats["total"] > 0:
            completion = stats["ok"] / stats["total"] * 100
            report.phases[phase] = {
                **stats,
                "completion_pct": round(completion, 1),
            }

    # CHECK SAFETY SYSTEM
    report.safety_status = check_safety_system(base_path)

    report.calculate_checksum()
    return report


# ===============================================================================
# OUTPUT FORMATTERS
# ===============================================================================

def print_report(report: DiagnosticReport):
    print("=" * 70)
    print("HOPE AI - SYSTEM DIAGNOSTIC REPORT v2.0")
    print("=" * 70)
    print(f"Timestamp: {report.timestamp}")
    print(f"Base Path: {report.base_path}")
    print(f"Checksum:  {report.checksum}")
    print()

    # SUMMARY
    print("-" * 70)
    print("SUMMARY")
    print("-" * 70)
    total = report.total_components
    ok_pct = report.ok_count/total*100 if total > 0 else 0
    broken_pct = report.broken_count/total*100 if total > 0 else 0
    missing_pct = report.missing_count/total*100 if total > 0 else 0
    not_impl_pct = report.not_impl_count/total*100 if total > 0 else 0

    print(f"  Total Components: {total}")
    print(f"  [OK]      OK:           {report.ok_count} ({ok_pct:.0f}%)")
    print(f"  [BROKEN]  BROKEN:       {report.broken_count} ({broken_pct:.0f}%)")
    print(f"  [MISSING] MISSING:      {report.missing_count} ({missing_pct:.0f}%)")
    print(f"  [NOTIMPL] NOT IMPL:     {report.not_impl_count} ({not_impl_pct:.0f}%)")
    print()

    # PHASE COMPLETION
    print("-" * 70)
    print("PHASE COMPLETION")
    print("-" * 70)
    phase_order = ["base", "3.1", "live_v4", "secret_ideas_p1", "secret_ideas_p2",
                   "secret_ideas_p4", "secret_ideas_p6", "testnet", "live"]

    for phase in phase_order:
        if phase in report.phases:
            stats = report.phases[phase]
            bar_len = 20
            filled = int(stats["completion_pct"] / 100 * bar_len)
            bar = "#" * filled + "-" * (bar_len - filled)
            print(f"  {phase:20} [{bar}] {stats['completion_pct']:5.1f}% ({stats['ok']}/{stats['total']})")

    for phase, stats in report.phases.items():
        if phase not in phase_order:
            bar_len = 20
            filled = int(stats["completion_pct"] / 100 * bar_len)
            bar = "#" * filled + "-" * (bar_len - filled)
            print(f"  {phase:20} [{bar}] {stats['completion_pct']:5.1f}% ({stats['ok']}/{stats['total']})")
    print()

    # SAFETY SYSTEM STATUS (NEW)
    print("-" * 70)
    print("LIVE SAFETY SYSTEM")
    print("-" * 70)
    ss = report.safety_status
    print(f"  Circuit Breaker:  {'[OK]' if ss.get('circuit_breaker') else '[FAIL]'}")
    print(f"  Rate Limiter:     {'[OK]' if ss.get('rate_limiter') else '[FAIL]'}")
    print(f"  Health Monitor:   {'[OK]' if ss.get('health_monitor') else '[FAIL]'}")
    print(f"  Live Barrier:     {'[OK]' if ss.get('live_barrier') else '[FAIL]'}")
    print(f"  Effective Mode:   {ss.get('live_barrier_mode', 'N/A')}")
    if ss.get('circuit_breaker_open'):
        print(f"  [WARNING] Circuit Breaker is OPEN!")
    print()

    # BROKEN
    if report.broken:
        print("-" * 70)
        print("[BROKEN] BROKEN (exists but doesn't work)")
        print("-" * 70)
        for comp in report.broken:
            print(f"  * {comp.path}")
            print(f"    Error: {comp.error}")
        print()

    # MISSING
    if report.missing:
        print("-" * 70)
        print("[MISSING] MISSING (required but not found)")
        print("-" * 70)
        for comp in report.missing:
            print(f"  * {comp.path}")
            if comp.description:
                print(f"    Desc: {comp.description}")
            print(f"    Phase: {comp.phase}")
        print()

    # NOT IMPLEMENTED
    if report.not_implemented:
        print("-" * 70)
        print("[NOTIMPL] NOT IMPLEMENTED (optional, future phases)")
        print("-" * 70)
        for comp in report.not_implemented[:10]:
            print(f"  * {comp.path} [{comp.phase}]")
        if len(report.not_implemented) > 10:
            print(f"  ... and {len(report.not_implemented) - 10} more")
        print()

    # ORPHANED
    if report.orphaned:
        print("-" * 70)
        print("[ORPHAN] ORPHANED (exist but not in spec)")
        print("-" * 70)
        for path in report.orphaned[:15]:
            print(f"  * {path}")
        if len(report.orphaned) > 15:
            print(f"  ... and {len(report.orphaned) - 15} more")
        print()

    # ENVIRONMENT
    print("-" * 70)
    print("ENVIRONMENT VARIABLES")
    print("-" * 70)
    if report.env_ok:
        print(f"  [OK] Set: {', '.join(report.env_ok)}")
    if report.env_missing:
        print(f"  [MISSING] Required: {', '.join(report.env_missing)}")
    if report.env_warnings:
        print(f"  [WARN] Not set:")
        for w in report.env_warnings[:5]:
            print(f"      * {w}")
    print()

    # PACKAGES
    print("-" * 70)
    print("PYTHON PACKAGES")
    print("-" * 70)
    if report.packages_ok:
        print(f"  [OK] Installed: {len(report.packages_ok)}")
    if report.packages_missing:
        print(f"  [MISSING] Required: {', '.join(report.packages_missing)}")
    print()

    # OK COMPONENTS
    print("-" * 70)
    print("[OK] WORKING COMPONENTS")
    print("-" * 70)
    by_dir = {}
    for comp in report.ok:
        dir_name = str(Path(comp.path).parent)
        if dir_name not in by_dir:
            by_dir[dir_name] = []
        by_dir[dir_name].append(comp)

    for dir_name, comps in sorted(by_dir.items()):
        print(f"  {dir_name}/")
        for comp in comps[:5]:
            details = ""
            if comp.details:
                if "line_count" in comp.details:
                    details = f" ({comp.details['line_count']} records)"
            print(f"    + {Path(comp.path).name}{details}")
        if len(comps) > 5:
            print(f"    ... +{len(comps)-5} more")

    print()
    print("=" * 70)
    print("END OF DIAGNOSTIC REPORT")
    print("=" * 70)


def export_json(report: DiagnosticReport, path: Path = None):
    data = {
        "version": report.version,
        "timestamp": report.timestamp,
        "base_path": report.base_path,
        "checksum": report.checksum,
        "summary": {
            "total": report.total_components,
            "ok": report.ok_count,
            "broken": report.broken_count,
            "missing": report.missing_count,
            "not_implemented": report.not_impl_count,
        },
        "phases": report.phases,
        "safety_status": report.safety_status,
        "broken": [asdict(c) for c in report.broken],
        "missing": [asdict(c) for c in report.missing],
        "orphaned": report.orphaned[:20],
        "environment": {
            "ok": report.env_ok,
            "missing": report.env_missing,
        },
        "packages": {
            "ok": report.packages_ok,
            "missing": report.packages_missing,
        },
    }

    if path:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Report exported to: {path}")
    else:
        print(json.dumps(data, indent=2, ensure_ascii=False))


# ===============================================================================
# MAIN
# ===============================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="HOPE AI System Diagnostic v2.0")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--export", type=str, help="Export JSON to file")
    parser.add_argument("--path", type=str, help="Base path to check")

    args = parser.parse_args()
    base_path = Path(args.path) if args.path else None

    report = run_diagnostic(base_path)

    if args.json:
        export_json(report)
    elif args.export:
        export_json(report, Path(args.export))
    else:
        print_report(report)

    if report.broken_count > 0:
        sys.exit(2)
    elif report.missing_count > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
