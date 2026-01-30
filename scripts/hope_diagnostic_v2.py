# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-01-30T08:05:00Z
# Purpose: HOPE AI System Diagnostic Tool v2.0 - Full System Check
# Includes: Eye of God V3, Position Watchdog, Signal Schema, Production Engine
# === END SIGNATURE ===
"""
HOPE AI - System Diagnostic Tool v2.0

Полная диагностика системы включая:
- Eye of God V3 (Two-Chamber Architecture)
- Position Watchdog
- Signal Schema Validation
- Production Engine
- All AI Modules
- Testnet Readiness

Usage:
    python scripts/hope_diagnostic_v2.py
    python scripts/hope_diagnostic_v2.py --json
    python scripts/hope_diagnostic_v2.py --testnet-check
"""

import os
import sys
import json
import importlib
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple, Any
from hashlib import sha256

# Ensure project root
sys.path.insert(0, str(Path(__file__).parent.parent))

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION v2.0 - COMPLETE SYSTEM SPEC
# ═══════════════════════════════════════════════════════════════════════════════

EXPECTED_COMPONENTS = {
    # ═══════════════════════════════════════════════════════════════════════════
    # CORE AI GATEWAY
    # ═══════════════════════════════════════════════════════════════════════════
    "ai_gateway/__init__.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "AI Gateway package init",
    },
    "ai_gateway/server.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "FastAPI HTTP server",
        "test_import": "ai_gateway.server",
    },
    "ai_gateway/config.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "Configuration with ALLOWED_DOMAINS",
    },
    "ai_gateway/contracts.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "Data contracts (EventContract, etc.)",
    },
    "ai_gateway/jsonl_writer.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "Atomic JSONL writer",
    },
    "ai_gateway/base_module.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "Base module class for AI modules",
    },
    "ai_gateway/scheduler.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "Module scheduler with SelfImprovingLoop",
    },
    "ai_gateway/status_manager.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "Health status manager",
    },
    "ai_gateway/diagnostics.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "Gateway diagnostics",
    },
    "ai_gateway/telegram_panel.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "Telegram control panel",
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # CORE COMPONENTS
    # ═══════════════════════════════════════════════════════════════════════════
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
    "ai_gateway/core/signal_processor.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "Signal Processor orchestrator",
    },
    "ai_gateway/core/mode_router.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "Mode Router (SUPER_SCALP/SCALP/SWING)",
        "test_import": "ai_gateway.core.mode_router",
    },
    "ai_gateway/core/circuit_breaker.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "Circuit Breaker for loss protection",
    },
    "ai_gateway/core/drop_filter.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "DROP signal filter",
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # EYE OF GOD V3 - HARDENED TRADING ORACLE
    # ═══════════════════════════════════════════════════════════════════════════
    "scripts/eye_of_god_v3.py": {
        "type": "module", "required": True, "phase": "eye_of_god",
        "description": "Eye of God V3 - Two-Chamber Architecture",
        "test_import": "scripts.eye_of_god_v3",
        "critical": True,
    },
    "scripts/position_watchdog.py": {
        "type": "module", "required": True, "phase": "eye_of_god",
        "description": "Independent position closing (timeout/stop/panic)",
        "test_import": "scripts.position_watchdog",
        "critical": True,
    },
    "scripts/signal_schema.py": {
        "type": "module", "required": True, "phase": "eye_of_god",
        "description": "Strict signal validation (TTL, types, ranges)",
        "test_import": "scripts.signal_schema",
        "critical": True,
    },
    "scripts/engine_watchdog.py": {
        "type": "module", "required": True, "phase": "eye_of_god",
        "description": "Production engine monitoring",
    },
    "scripts/hope_supervisor.py": {
        "type": "module", "required": True, "phase": "eye_of_god",
        "description": "Process supervisor with restart logic",
    },
    "docs/EYE_OF_GOD_HARDENING_v1.md": {
        "type": "doc", "required": True, "phase": "eye_of_god",
        "description": "Eye of God hardening documentation",
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # PRODUCTION ENGINE
    # ═══════════════════════════════════════════════════════════════════════════
    "scripts/hope_production_engine.py": {
        "type": "module", "required": True, "phase": "production",
        "description": "Full trading cycle engine (1764 lines)",
        "test_import": "scripts.hope_production_engine",
        "critical": True,
    },
    "scripts/start_hope_production.py": {
        "type": "module", "required": True, "phase": "production",
        "description": "Production engine launcher",
    },
    "scripts/eye_controller.py": {
        "type": "module", "required": True, "phase": "production",
        "description": "Eye of God session controller",
    },
    "scripts/autotrader.py": {
        "type": "module", "required": True, "phase": "production",
        "description": "Autotrader with Binance execution",
    },
    "scripts/order_executor.py": {
        "type": "module", "required": True, "phase": "production",
        "description": "Order executor (DRY/TESTNET/LIVE)",
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # FEEDS - PRICE DATA
    # ═══════════════════════════════════════════════════════════════════════════
    "ai_gateway/feeds/__init__.py": {
        "type": "module", "required": True, "phase": "base",
    },
    "ai_gateway/feeds/binance_ws.py": {
        "type": "module", "required": True, "phase": "base",
        "description": "Binance WebSocket price feed",
    },
    "ai_gateway/feeds/binance_realtime.py": {
        "type": "module", "required": True, "phase": "feeds",
        "description": "Binance real-time price feed",
    },
    "ai_gateway/feeds/binance_ws_enricher.py": {
        "type": "module", "required": True, "phase": "feeds",
        "description": "Binance WS Enricher (orderbook, trades)",
    },
    "ai_gateway/feeds/price_bridge.py": {
        "type": "module", "required": True, "phase": "feeds",
        "description": "Price Bridge to OutcomeTracker",
    },
    "ai_gateway/feeds/trade_aggregator.py": {
        "type": "module", "required": True, "phase": "feeds",
        "description": "Trade data aggregation",
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # AI MODULES - PREDICTION & ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════════
    "ai_gateway/modules/__init__.py": {
        "type": "module", "required": True, "phase": "base",
    },
    "ai_gateway/modules/predictor/__init__.py": {
        "type": "module", "required": True, "phase": "ai_modules",
    },
    "ai_gateway/modules/predictor/signal_classifier.py": {
        "type": "module", "required": True, "phase": "ai_modules",
        "description": "ML Signal Classifier with empirical filters",
        "test_import": "ai_gateway.modules.predictor.signal_classifier",
        "critical": True,
    },
    "ai_gateway/modules/anomaly/__init__.py": {
        "type": "module", "required": True, "phase": "ai_modules",
    },
    "ai_gateway/modules/anomaly/scanner.py": {
        "type": "module", "required": True, "phase": "ai_modules",
        "description": "Anomaly Scanner for market detection",
    },
    "ai_gateway/modules/regime/__init__.py": {
        "type": "module", "required": True, "phase": "ai_modules",
    },
    "ai_gateway/modules/regime/detector.py": {
        "type": "module", "required": True, "phase": "ai_modules",
        "description": "Market Regime Detector (BULL/BEAR/SIDEWAYS/PANIC)",
    },
    "ai_gateway/modules/sentiment/__init__.py": {
        "type": "module", "required": True, "phase": "ai_modules",
    },
    "ai_gateway/modules/sentiment/analyzer.py": {
        "type": "module", "required": True, "phase": "ai_modules",
        "description": "News Sentiment Analyzer",
    },
    "ai_gateway/modules/doctor/__init__.py": {
        "type": "module", "required": True, "phase": "ai_modules",
    },
    "ai_gateway/modules/doctor/diagnostics.py": {
        "type": "module", "required": True, "phase": "ai_modules",
        "description": "System health diagnostics",
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # SELF-IMPROVER - ML LEARNING LOOP
    # ═══════════════════════════════════════════════════════════════════════════
    "ai_gateway/modules/self_improver/__init__.py": {
        "type": "module", "required": True, "phase": "self_improver",
    },
    "ai_gateway/modules/self_improver/loop.py": {
        "type": "module", "required": True, "phase": "self_improver",
        "description": "Self-Improving Loop (A/B testing, retraining)",
        "test_import": "ai_gateway.modules.self_improver.loop",
    },
    "ai_gateway/modules/self_improver/outcome_tracker.py": {
        "type": "module", "required": True, "phase": "self_improver",
        "description": "Outcome Tracker (MFE/MAE)",
        "test_import": "ai_gateway.modules.self_improver.outcome_tracker",
    },
    "ai_gateway/modules/self_improver/ab_tester.py": {
        "type": "module", "required": True, "phase": "self_improver",
        "description": "A/B Tester for model comparison",
    },
    "ai_gateway/modules/self_improver/model_registry.py": {
        "type": "module", "required": True, "phase": "self_improver",
        "description": "Model Registry with versioning",
    },
    "ai_gateway/modules/self_improver/threshold_tuner.py": {
        "type": "module", "required": False, "phase": "future_p6",
        "description": "Automatic threshold tuning (TODO)",
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # PATTERNS - PUMP/DROP DETECTION
    # ═══════════════════════════════════════════════════════════════════════════
    "ai_gateway/patterns/__init__.py": {
        "type": "module", "required": True, "phase": "patterns",
    },
    "ai_gateway/patterns/pump_precursor_detector.py": {
        "type": "module", "required": True, "phase": "patterns",
        "description": "Pump Precursor Detector",
        "test_import": "ai_gateway.patterns.pump_precursor_detector",
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # INTEGRATIONS - MOONBOT
    # ═══════════════════════════════════════════════════════════════════════════
    "ai_gateway/integrations/__init__.py": {
        "type": "module", "required": True, "phase": "integrations",
    },
    "ai_gateway/integrations/moonbot_live.py": {
        "type": "module", "required": True, "phase": "integrations",
        "description": "MoonBot Live Signal Integration",
        "test_import": "ai_gateway.integrations.moonbot_live",
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # ALERTS - TELEGRAM
    # ═══════════════════════════════════════════════════════════════════════════
    "ai_gateway/alerts/__init__.py": {
        "type": "module", "required": True, "phase": "alerts",
    },
    "ai_gateway/alerts/telegram_alerts.py": {
        "type": "module", "required": True, "phase": "alerts",
        "description": "Telegram alert system",
    },
    "ai_gateway/telegram/__init__.py": {
        "type": "module", "required": True, "phase": "alerts",
    },
    "ai_gateway/telegram/commands.py": {
        "type": "module", "required": True, "phase": "alerts",
        "description": "Telegram /predict, /stats commands",
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # CORE LIBRARY
    # ═══════════════════════════════════════════════════════════════════════════
    "core/io_atomic.py": {
        "type": "module", "required": True, "phase": "core",
        "description": "Atomic file I/O operations",
    },
    "core/pricefeed_ssot.py": {
        "type": "module", "required": True, "phase": "core",
        "description": "Price Feed Single Source of Truth",
    },
    "core/lockfile.py": {
        "type": "module", "required": True, "phase": "core",
        "description": "Process lockfile manager",
    },
    "core/oracle_config.py": {
        "type": "module", "required": True, "phase": "core",
        "description": "Oracle configuration",
    },
    "core/jsonl_atomic.py": {
        "type": "module", "required": True, "phase": "core",
        "description": "Atomic JSONL operations",
    },
    "core/sha256_contract.py": {
        "type": "module", "required": True, "phase": "core",
        "description": "SHA256 data contracts",
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # GATES - POLICY & PREFLIGHT
    # ═══════════════════════════════════════════════════════════════════════════
    "scripts/gates/__init__.py": {
        "type": "module", "required": True, "phase": "gates",
    },
    "scripts/gates/policy_preflight.py": {
        "type": "module", "required": True, "phase": "gates",
        "description": "Policy preflight gates",
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # UTILITIES
    # ═══════════════════════════════════════════════════════════════════════════
    "scripts/update_market_intel.py": {
        "type": "module", "required": True, "phase": "utilities",
        "description": "Market intelligence updater",
    },
    "scripts/sources_manager.py": {
        "type": "module", "required": True, "phase": "utilities",
        "description": "Data sources manager",
    },
    "scripts/decision_to_autotrader.py": {
        "type": "module", "required": True, "phase": "utilities",
        "description": "Decision to autotrader bridge",
    },
    "scripts/live_ai_test.py": {
        "type": "module", "required": True, "phase": "utilities",
        "description": "Live AI testing",
    },
    "scripts/moonbot_log_parser.py": {
        "type": "module", "required": True, "phase": "utilities",
        "description": "MoonBot log parser",
    },
    "scripts/honesty_contract.py": {
        "type": "module", "required": True, "phase": "contracts",
        "description": "Honesty Contract enforcement",
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # DATA FILES
    # ═══════════════════════════════════════════════════════════════════════════
    "ai_gateway/models/hope_model_v1.json": {
        "type": "data", "required": True, "phase": "data",
        "description": "Trained AI model v1",
        "validate": "json",
    },
    "state/market_intel.json": {
        "type": "data", "required": True, "phase": "data",
        "description": "Current market intelligence",
        "validate": "json",
    },
    "state/sources/sources.json": {
        "type": "data", "required": True, "phase": "data",
        "description": "Data sources registry",
        "validate": "json",
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # DOCUMENTATION
    # ═══════════════════════════════════════════════════════════════════════════
    "docs/HONESTY_CONTRACT_GLOBAL.md": {
        "type": "doc", "required": True, "phase": "docs",
        "description": "Global Honesty Contract",
    },
    "docs/PRINCIPLES.md": {
        "type": "doc", "required": True, "phase": "docs",
        "description": "HOPE Design Principles",
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # FUTURE / NOT IMPLEMENTED
    # ═══════════════════════════════════════════════════════════════════════════
    "ai_gateway/ingestion/__init__.py": {
        "type": "module", "required": False, "phase": "future",
        "description": "Signal ingestion (TODO)",
    },
    "ai_gateway/ingestion/signal_aggregator.py": {
        "type": "module", "required": False, "phase": "future",
        "description": "Multi-source signal aggregator (TODO)",
    },
}

# Environment variables
EXPECTED_ENV_VARS = {
    "BINANCE_API_KEY": {"required": False, "phase": "testnet"},
    "BINANCE_API_SECRET": {"required": False, "phase": "testnet"},
    "BINANCE_TESTNET": {"required": False, "phase": "testnet", "default": "false"},
    "TELEGRAM_BOT_TOKEN": {"required": False, "phase": "base"},
    "ANTHROPIC_API_KEY": {"required": False, "phase": "ai"},
}

# Required packages
EXPECTED_PACKAGES = {
    "fastapi": {"required": True},
    "uvicorn": {"required": True},
    "pydantic": {"required": True},
    "orjson": {"required": True},
    "httpx": {"required": True},
    "websockets": {"required": True},
    "python-binance": {"required": True, "import_name": "binance"},
}

# ═══════════════════════════════════════════════════════════════════════════════
# EYE OF GOD TODO LIST - WHAT'S NOT DONE
# ═══════════════════════════════════════════════════════════════════════════════

EYE_OF_GOD_TODO = {
    "completed": [
        "Two-Chamber Architecture (Alpha + Risk Committee)",
        "Signal Schema V1 validation",
        "Signal TTL enforcement (60s max)",
        "Position Watchdog (timeout, stop, panic)",
        "Price staleness check (30s)",
        "Daily loss circuit breaker",
        "Max positions limit",
        "Blacklist/Whitelist support",
        "Session-based risk multipliers",
        "Heartbeat monitoring",
        "STOP.flag graceful shutdown",
    ],
    "in_progress": [
        "Integration with live Binance execution",
        "MoonBot signal consumer in production engine",
        "Real-time price feed connection",
    ],
    "todo_p0": [
        "TESTNET integration test (end-to-end)",
        "Real trade execution verification",
        "Position close verification on Binance",
        "Panic close all positions test",
    ],
    "todo_p1": [
        "Multi-symbol position tracking",
        "Portfolio-level risk management",
        "Correlation-based position limits",
        "Dynamic stop-loss adjustment",
    ],
    "todo_p2": [
        "ML-based confidence calibration",
        "Regime-aware risk adjustment",
        "Volatility-scaled position sizing",
        "News event pause mechanism",
    ],
    "secret_ideas": [
        "Orderbook imbalance detector (before pump)",
        "Large order flow tracker (whale detection)",
        "Cross-exchange arbitrage signals",
        "Funding rate momentum",
        "Open interest divergence",
        "Liquidation cascade predictor",
        "Social sentiment spike detector",
        "On-chain whale movement tracker",
    ],
}


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ComponentStatus:
    path: str
    exists: bool
    works: bool
    required: bool
    phase: str
    description: str
    critical: bool = False
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
        else:
            return "NOT_IMPL"


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
    critical_broken: int = 0

    ok: List[ComponentStatus] = field(default_factory=list)
    broken: List[ComponentStatus] = field(default_factory=list)
    missing: List[ComponentStatus] = field(default_factory=list)
    not_implemented: List[ComponentStatus] = field(default_factory=list)

    env_ok: List[str] = field(default_factory=list)
    env_missing: List[str] = field(default_factory=list)

    packages_ok: List[str] = field(default_factory=list)
    packages_missing: List[str] = field(default_factory=list)

    phases: Dict[str, Dict] = field(default_factory=dict)

    testnet_ready: bool = False
    live_ready: bool = False

    checksum: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# CHECK FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def check_syntax(path: Path) -> Tuple[bool, Optional[str]]:
    """Check Python file syntax"""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0, result.stderr.strip() if result.returncode != 0 else None
    except Exception as e:
        return False, str(e)


def check_import(module_name: str) -> Tuple[bool, Optional[str]]:
    """Try to import module"""
    try:
        importlib.import_module(module_name)
        return True, None
    except Exception as e:
        return False, str(e)


def validate_json(path: Path) -> Tuple[bool, Optional[str]]:
    """Validate JSON file"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            json.load(f)
        return True, None
    except Exception as e:
        return False, str(e)


def check_package(name: str, import_name: str = None) -> Tuple[bool, Optional[str]]:
    """Check if package installed"""
    try:
        importlib.import_module(import_name or name)
        return True, None
    except ImportError:
        return False, "not installed"


# ═══════════════════════════════════════════════════════════════════════════════
# TESTNET CHECK
# ═══════════════════════════════════════════════════════════════════════════════

def check_testnet_readiness(base_path: Path) -> Dict[str, Any]:
    """Check if system ready for testnet trading"""
    results = {
        "ready": False,
        "checks": {},
        "blockers": [],
        "warnings": [],
    }

    # 1. Check Binance credentials
    api_key = os.environ.get("BINANCE_API_KEY") or os.environ.get("BINANCE_TESTNET_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET") or os.environ.get("BINANCE_TESTNET_API_SECRET")

    results["checks"]["binance_credentials"] = bool(api_key and api_secret)
    if not results["checks"]["binance_credentials"]:
        results["blockers"].append("BINANCE_API_KEY/SECRET not set")

    # 2. Check critical modules
    critical_modules = [
        "scripts/eye_of_god_v3.py",
        "scripts/position_watchdog.py",
        "scripts/signal_schema.py",
        "scripts/hope_production_engine.py",
        "scripts/order_executor.py",
    ]

    all_critical_ok = True
    for mod in critical_modules:
        path = base_path / mod
        if path.exists():
            ok, _ = check_syntax(path)
            results["checks"][mod] = ok
            if not ok:
                all_critical_ok = False
                results["blockers"].append(f"{mod} has syntax errors")
        else:
            results["checks"][mod] = False
            all_critical_ok = False
            results["blockers"].append(f"{mod} missing")

    results["checks"]["critical_modules"] = all_critical_ok

    # 3. Check price feed
    market_intel = base_path / "state/market_intel.json"
    if market_intel.exists():
        try:
            with open(market_intel) as f:
                data = json.load(f)
            ts = data.get("timestamp_unix", 0)
            age_sec = datetime.now(timezone.utc).timestamp() - ts
            results["checks"]["market_intel_fresh"] = age_sec < 600  # 10 min
            if age_sec > 600:
                results["warnings"].append(f"market_intel.json is {age_sec/60:.0f} min old")
        except:
            results["checks"]["market_intel_fresh"] = False
    else:
        results["checks"]["market_intel_fresh"] = False
        results["warnings"].append("market_intel.json missing")

    # 4. Check binance package
    try:
        from binance.client import Client
        results["checks"]["binance_package"] = True
    except ImportError:
        results["checks"]["binance_package"] = False
        results["blockers"].append("python-binance not installed")

    # 5. Overall readiness
    results["ready"] = len(results["blockers"]) == 0

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN DIAGNOSTIC
# ═══════════════════════════════════════════════════════════════════════════════

def run_diagnostic(base_path: Path = None) -> DiagnosticReport:
    """Run full system diagnostic"""

    if base_path is None:
        base_path = Path(__file__).parent.parent

    report = DiagnosticReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        base_path=str(base_path.absolute()),
    )

    sys.path.insert(0, str(base_path))

    phase_stats = {}

    # Check components
    for rel_path, spec in EXPECTED_COMPONENTS.items():
        full_path = base_path / rel_path

        status = ComponentStatus(
            path=rel_path,
            exists=full_path.exists(),
            works=False,
            required=spec.get("required", False),
            phase=spec.get("phase", "unknown"),
            description=spec.get("description", ""),
            critical=spec.get("critical", False),
        )

        phase = status.phase
        if phase not in phase_stats:
            phase_stats[phase] = {"total": 0, "ok": 0}
        phase_stats[phase]["total"] += 1

        if status.exists:
            comp_type = spec.get("type", "module")

            if comp_type == "module":
                ok, err = check_syntax(full_path)
                if ok:
                    if "test_import" in spec:
                        ok, err = check_import(spec["test_import"])
                    status.works = ok
                    status.error = err
                else:
                    status.error = err
            elif comp_type == "data":
                if spec.get("validate") == "json":
                    ok, err = validate_json(full_path)
                    status.works = ok
                    status.error = err
                else:
                    status.works = True
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
            if status.critical:
                report.critical_broken += 1
        elif status.status == "MISSING":
            report.missing.append(status)
            report.missing_count += 1
        else:
            report.not_implemented.append(status)
            report.not_impl_count += 1

    # Phase completion
    for phase, stats in phase_stats.items():
        if stats["total"] > 0:
            report.phases[phase] = {
                **stats,
                "pct": round(stats["ok"] / stats["total"] * 100, 1),
            }

    # Environment
    for var, spec in EXPECTED_ENV_VARS.items():
        if os.environ.get(var):
            report.env_ok.append(var)
        elif spec.get("required"):
            report.env_missing.append(var)

    # Packages
    for pkg, spec in EXPECTED_PACKAGES.items():
        ok, _ = check_package(pkg, spec.get("import_name"))
        if ok:
            report.packages_ok.append(pkg)
        elif spec.get("required"):
            report.packages_missing.append(pkg)

    # Testnet/Live readiness
    testnet = check_testnet_readiness(base_path)
    report.testnet_ready = testnet["ready"]
    report.live_ready = report.testnet_ready and report.critical_broken == 0

    report.checksum = f"sha256:{sha256(report.timestamp.encode()).hexdigest()[:16]}"

    return report


# ═══════════════════════════════════════════════════════════════════════════════
# OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════

def print_report(report: DiagnosticReport):
    """Print human-readable report"""

    print("=" * 75)
    print("HOPE AI - SYSTEM DIAGNOSTIC v2.0")
    print("=" * 75)
    print(f"Timestamp: {report.timestamp}")
    print(f"Base Path: {report.base_path}")
    print()

    # Summary
    print("-" * 75)
    print("SUMMARY")
    print("-" * 75)
    total = report.total_components
    print(f"  Total Components: {total}")
    print(f"  [OK] OK:          {report.ok_count} ({report.ok_count/total*100:.0f}%)")
    print(f"  [X]  BROKEN:      {report.broken_count} ({report.broken_count/total*100:.0f}%)" +
          (f" ({report.critical_broken} CRITICAL!)" if report.critical_broken else ""))
    print(f"  [!]  MISSING:     {report.missing_count} ({report.missing_count/total*100:.0f}%)")
    print(f"  [~]  NOT IMPL:    {report.not_impl_count} ({report.not_impl_count/total*100:.0f}%)")
    print()

    # Readiness
    print("-" * 75)
    print("SYSTEM READINESS")
    print("-" * 75)
    print(f"  TESTNET: {'[OK] READY' if report.testnet_ready else '[X] NOT READY'}")
    print(f"  LIVE:    {'[OK] READY' if report.live_ready else '[X] NOT READY'}")
    print()

    # Phase completion
    print("-" * 75)
    print("PHASE COMPLETION")
    print("-" * 75)

    phase_order = ["base", "core", "eye_of_god", "production", "feeds",
                   "ai_modules", "self_improver", "patterns", "integrations",
                   "alerts", "gates", "utilities", "contracts", "data", "docs", "future"]

    for phase in phase_order:
        if phase in report.phases:
            s = report.phases[phase]
            bar_len = 20
            filled = int(s["pct"] / 100 * bar_len)
            bar = "#" * filled + "-" * (bar_len - filled)
            status = "[OK]" if s["pct"] == 100 else "[..]"
            print(f"  {phase:18} [{bar}] {s['pct']:5.1f}% ({s['ok']}/{s['total']}) {status}")
    print()

    # Broken
    if report.broken:
        print("-" * 75)
        print("[X] BROKEN COMPONENTS")
        print("-" * 75)
        for c in report.broken:
            crit = " [CRITICAL!]" if c.critical else ""
            print(f"  * {c.path}{crit}")
            print(f"    Error: {c.error}")
        print()

    # Missing
    if report.missing:
        print("-" * 75)
        print("[!] MISSING COMPONENTS")
        print("-" * 75)
        for c in report.missing:
            print(f"  * {c.path}")
            if c.description:
                print(f"    -> {c.description}")
        print()

    # Eye of God TODO
    print("-" * 75)
    print("EYE OF GOD V3 - STATUS")
    print("-" * 75)
    print("  [DONE]")
    for item in EYE_OF_GOD_TODO["completed"][:5]:
        print(f"    + {item}")
    print(f"    ... and {len(EYE_OF_GOD_TODO['completed'])-5} more")
    print()
    print("  [IN PROGRESS]")
    for item in EYE_OF_GOD_TODO["in_progress"]:
        print(f"    ~ {item}")
    print()
    print("  [TODO P0 - CRITICAL]")
    for item in EYE_OF_GOD_TODO["todo_p0"]:
        print(f"    ! {item}")
    print()
    print("  [SECRET IDEAS - FUTURE]")
    for item in EYE_OF_GOD_TODO["secret_ideas"][:4]:
        print(f"    ? {item}")
    print(f"    ... and {len(EYE_OF_GOD_TODO['secret_ideas'])-4} more secret ideas")
    print()

    # Environment
    print("-" * 75)
    print("ENVIRONMENT")
    print("-" * 75)
    if report.env_ok:
        print(f"  [OK] Set: {', '.join(report.env_ok)}")
    if report.env_missing:
        print(f"  [X]  Missing: {', '.join(report.env_missing)}")
    print()

    # Packages
    print("-" * 75)
    print("PACKAGES")
    print("-" * 75)
    print(f"  [OK] Installed: {len(report.packages_ok)}")
    if report.packages_missing:
        print(f"  [X]  Missing: {', '.join(report.packages_missing)}")
    print()

    print("=" * 75)
    print(f"Checksum: {report.checksum}")
    print("=" * 75)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="HOPE AI Diagnostic v2.0")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--testnet-check", action="store_true")
    parser.add_argument("--path", type=str)
    args = parser.parse_args()

    base_path = Path(args.path) if args.path else None

    if args.testnet_check:
        base = base_path or Path(__file__).parent.parent
        result = check_testnet_readiness(base)
        print("=" * 60)
        print("TESTNET READINESS CHECK")
        print("=" * 60)
        print(f"Ready: {'YES' if result['ready'] else 'NO'}")
        print()
        print("Checks:")
        for k, v in result["checks"].items():
            status = "[OK]" if v else "[X]"
            print(f"  {status} {k}")
        if result["blockers"]:
            print()
            print("Blockers:")
            for b in result["blockers"]:
                print(f"  ! {b}")
        if result["warnings"]:
            print()
            print("Warnings:")
            for w in result["warnings"]:
                print(f"  ~ {w}")
        sys.exit(0 if result["ready"] else 1)

    report = run_diagnostic(base_path)

    if args.json:
        print(json.dumps(asdict(report), indent=2, default=str))
    else:
        print_report(report)

    if report.critical_broken > 0:
        sys.exit(2)
    elif report.missing_count > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
