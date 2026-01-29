#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 17:30:00 UTC
# Purpose: HOPE AI System Diagnostic Tool v1.0
# === END SIGNATURE ===
"""
HOPE AI - System Diagnostic Tool v1.0

Полная диагностика системы:
- Что ЕСТЬ и работает
- Чего НЕТ (отсутствует)
- Что СЛОМАНО (есть но не работает)
- Что ПОТЕРЯНО (есть но путь неизвестен)

Usage:
    python hope_diagnostic.py [--fix] [--json]
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

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

# Base path (will be set dynamically)
BASE_PATH = Path(".")

# Expected components according to TZ v1.0 "Тайные Идеи"
EXPECTED_COMPONENTS = {
    # ─────────────────────────────────────────────────────────────────────
    # CORE MODULES
    # ─────────────────────────────────────────────────────────────────────
    "ai_gateway/__init__.py": {
        "type": "module",
        "required": True,
        "phase": "base",
        "description": "AI Gateway package init",
    },
    "ai_gateway/server.py": {
        "type": "module",
        "required": True,
        "phase": "base",
        "description": "Main HTTP server",
        "test_import": "ai_gateway.server",
    },
    "ai_gateway/config.py": {
        "type": "module",
        "required": True,
        "phase": "base",
        "description": "Configuration loader",
    },

    # ─────────────────────────────────────────────────────────────────────
    # CORE COMPONENTS
    # ─────────────────────────────────────────────────────────────────────
    "ai_gateway/core/__init__.py": {
        "type": "module",
        "required": True,
        "phase": "base",
    },
    "ai_gateway/core/event_bus.py": {
        "type": "module",
        "required": True,
        "phase": "base",
        "description": "Event Bus for signal routing",
        "test_import": "ai_gateway.core.event_bus",
    },
    "ai_gateway/core/decision_engine.py": {
        "type": "module",
        "required": True,
        "phase": "base",
        "description": "Decision Engine (fail-closed)",
        "test_import": "ai_gateway.core.decision_engine",
    },
    "ai_gateway/core/mode_router.py": {
        "type": "module",
        "required": True,
        "phase": "3.1",
        "description": "Mode Router (SUPER_SCALP/SCALP/SWING)",
        "test_import": "ai_gateway.core.mode_router",
    },
    "ai_gateway/core/signal_processor.py": {
        "type": "module",
        "required": True,
        "phase": "base",
        "description": "Signal Processor orchestrator",
        "test_import": "ai_gateway.core.signal_processor",
    },
    "ai_gateway/core/circuit_breaker.py": {
        "type": "module",
        "required": True,
        "phase": "base",
        "description": "Circuit Breaker for loss protection",
    },

    # ─────────────────────────────────────────────────────────────────────
    # PATTERNS (Phase 3.1)
    # ─────────────────────────────────────────────────────────────────────
    "ai_gateway/patterns/__init__.py": {
        "type": "module",
        "required": True,
        "phase": "3.1",
    },
    "ai_gateway/patterns/pump_precursor_detector.py": {
        "type": "module",
        "required": True,
        "phase": "3.1",
        "description": "Pump Precursor Detector (Предвестник пампа)",
        "test_import": "ai_gateway.patterns.pump_precursor_detector",
    },

    # ─────────────────────────────────────────────────────────────────────
    # MODELS
    # ─────────────────────────────────────────────────────────────────────
    "ai_gateway/models/__init__.py": {
        "type": "module",
        "required": False,
        "phase": "3.1",
    },
    "ai_gateway/models/hope_model_v1.json": {
        "type": "data",
        "required": True,
        "phase": "3.1",
        "description": "Trained AI model v1 (136 samples)",
        "validate": "json",
    },

    # ─────────────────────────────────────────────────────────────────────
    # FEEDS
    # ─────────────────────────────────────────────────────────────────────
    "ai_gateway/feeds/__init__.py": {
        "type": "module",
        "required": True,
        "phase": "base",
    },
    "ai_gateway/feeds/binance_ws.py": {
        "type": "module",
        "required": True,
        "phase": "base",
        "description": "Binance WebSocket price feed",
    },
    "ai_gateway/feeds/price_bridge.py": {
        "type": "module",
        "required": True,
        "phase": "secret_ideas_p2",
        "description": "Price Bridge - connects WS to OutcomeTracker",
        "test_import": "ai_gateway.feeds.price_bridge",
    },
    "ai_gateway/feeds/binance_ws_enricher.py": {
        "type": "module",
        "required": True,
        "phase": "secret_ideas_p2",
        "description": "Binance WS Enricher (orderbook, trades)",
        "test_import": "ai_gateway.feeds.binance_ws_enricher",
    },

    # ─────────────────────────────────────────────────────────────────────
    # INTEGRATIONS (Phase 1)
    # ─────────────────────────────────────────────────────────────────────
    "ai_gateway/integrations/__init__.py": {
        "type": "module",
        "required": True,
        "phase": "secret_ideas_p1",
    },
    "ai_gateway/integrations/moonbot_live.py": {
        "type": "module",
        "required": True,
        "phase": "secret_ideas_p1",
        "description": "MoonBot Live Signal Integration",
        "test_import": "ai_gateway.integrations.moonbot_live",
    },

    # ─────────────────────────────────────────────────────────────────────
    # INGESTION (Phase 1)
    # ─────────────────────────────────────────────────────────────────────
    "ai_gateway/ingestion/__init__.py": {
        "type": "module",
        "required": False,
        "phase": "secret_ideas_p1",
    },
    "ai_gateway/ingestion/signal_aggregator.py": {
        "type": "module",
        "required": False,
        "phase": "secret_ideas_p1",
        "description": "Signal Aggregator (multi-source)",
    },
    "ai_gateway/ingestion/moonbot_parser.py": {
        "type": "module",
        "required": False,
        "phase": "secret_ideas_p1",
        "description": "MoonBot TG message parser",
    },

    # ─────────────────────────────────────────────────────────────────────
    # SELF-IMPROVER
    # ─────────────────────────────────────────────────────────────────────
    "ai_gateway/modules/__init__.py": {
        "type": "module",
        "required": True,
        "phase": "base",
    },
    "ai_gateway/modules/self_improver/__init__.py": {
        "type": "module",
        "required": True,
        "phase": "base",
    },
    "ai_gateway/modules/self_improver/outcome_tracker.py": {
        "type": "module",
        "required": True,
        "phase": "base",
        "description": "Outcome Tracker (MFE/MAE)",
        "test_import": "ai_gateway.modules.self_improver.outcome_tracker",
    },
    "ai_gateway/modules/self_improver/threshold_tuner.py": {
        "type": "module",
        "required": False,
        "phase": "secret_ideas_p6",
        "description": "Automatic threshold tuning",
    },

    # ─────────────────────────────────────────────────────────────────────
    # TELEGRAM
    # ─────────────────────────────────────────────────────────────────────
    "ai_gateway/telegram/__init__.py": {
        "type": "module",
        "required": True,
        "phase": "secret_ideas_p4",
    },
    "ai_gateway/telegram/commands.py": {
        "type": "module",
        "required": True,
        "phase": "secret_ideas_p4",
        "description": "Telegram /predict, /stats commands",
        "test_import": "ai_gateway.telegram.commands",
    },

    # ─────────────────────────────────────────────────────────────────────
    # UTILITIES
    # ─────────────────────────────────────────────────────────────────────
    "ai_gateway/jsonl_writer.py": {
        "type": "module",
        "required": True,
        "phase": "base",
        "description": "Atomic JSONL writer (FIXED for pydantic v2)",
    },
    "ai_gateway/contracts.py": {
        "type": "module",
        "required": True,
        "phase": "base",
        "description": "Data contracts/schemas",
    },
    "ai_gateway/base_module.py": {
        "type": "module",
        "required": True,
        "phase": "base",
        "description": "Base module class",
    },
    "ai_gateway/scheduler.py": {
        "type": "module",
        "required": True,
        "phase": "base",
        "description": "Module scheduler",
    },
    "ai_gateway/status_manager.py": {
        "type": "module",
        "required": True,
        "phase": "base",
        "description": "Status manager",
    },

    # ─────────────────────────────────────────────────────────────────────
    # DATA FILES
    # ─────────────────────────────────────────────────────────────────────
    "data/moonbot_signals/signals_20260129.jsonl": {
        "type": "data",
        "required": True,
        "phase": "3.1",
        "description": "Collected MoonBot signals (227)",
        "validate": "jsonl",
    },
    "state/ai/decisions.jsonl": {
        "type": "data",
        "required": False,
        "phase": "secret_ideas_p1",
        "description": "AI decisions log",
        "validate": "jsonl",
    },
    "state/sources/sources.json": {
        "type": "data",
        "required": False,
        "phase": "base",
        "description": "Data sources registry",
        "validate": "json",
    },

    # ─────────────────────────────────────────────────────────────────────
    # SCRIPTS
    # ─────────────────────────────────────────────────────────────────────
    "scripts/moonbot_parser_v2.py": {
        "type": "script",
        "required": True,
        "phase": "3.1",
        "description": "MoonBot parser v2",
    },
    "scripts/hope_diagnostic.py": {
        "type": "script",
        "required": True,
        "phase": "base",
        "description": "System diagnostic tool",
    },

    # ─────────────────────────────────────────────────────────────────────
    # DOCUMENTATION
    # ─────────────────────────────────────────────────────────────────────
    "docs/HOPE_AI_TRADING_TZ_v3.md": {
        "type": "doc",
        "required": False,
        "phase": "base",
        "description": "Technical specification v3",
    },
}

# Environment variables to check
EXPECTED_ENV_VARS = {
    "BINANCE_API_KEY": {"required": False, "phase": "live"},
    "BINANCE_API_SECRET": {"required": False, "phase": "live"},
    "BINANCE_TESTNET": {"required": False, "phase": "testnet"},
    "TELEGRAM_BOT_TOKEN": {"required": False, "phase": "base"},
    "TELEGRAM_ADMIN_CHAT_ID": {"required": False, "phase": "base"},
    "ANTHROPIC_API_KEY": {"required": False, "phase": "ai"},
    "AI_GATEWAY_PORT": {"required": False, "phase": "base", "default": "8100"},
    "AI_GATEWAY_MODE": {"required": False, "phase": "base", "default": "DRY"},
}

# Python packages to check
EXPECTED_PACKAGES = {
    "fastapi": {"required": True, "min_version": "0.100.0"},
    "uvicorn": {"required": True},
    "pydantic": {"required": True, "min_version": "2.0.0"},
    "orjson": {"required": False},
    "httpx": {"required": False},
    "websockets": {"required": True},
    "aiohttp": {"required": True},
    "numpy": {"required": False},
    "pandas": {"required": False},
}


# ═══════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════

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

    # Component counts
    total_components: int = 0
    ok_count: int = 0
    broken_count: int = 0
    missing_count: int = 0
    not_impl_count: int = 0

    # Detailed lists
    ok: List[ComponentStatus] = field(default_factory=list)
    broken: List[ComponentStatus] = field(default_factory=list)
    missing: List[ComponentStatus] = field(default_factory=list)
    not_implemented: List[ComponentStatus] = field(default_factory=list)

    # Environment
    env_ok: List[str] = field(default_factory=list)
    env_missing: List[str] = field(default_factory=list)
    env_warnings: List[str] = field(default_factory=list)

    # Packages
    packages_ok: List[str] = field(default_factory=list)
    packages_missing: List[str] = field(default_factory=list)
    packages_outdated: List[str] = field(default_factory=list)

    # Orphaned files (exist but not in spec)
    orphaned: List[str] = field(default_factory=list)

    # Phase completion
    phases: Dict[str, Dict] = field(default_factory=dict)

    checksum: str = ""

    def calculate_checksum(self):
        data = f"{self.timestamp}:{self.ok_count}:{self.broken_count}:{self.missing_count}"
        self.checksum = f"sha256:{sha256(data.encode()).hexdigest()[:16]}"


# ═══════════════════════════════════════════════════════════════════════════
# DIAGNOSTIC FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def check_file_exists(path: Path) -> bool:
    """Check if file exists"""
    return path.exists()


def validate_json(path: Path) -> Tuple[bool, Optional[str]]:
    """Validate JSON file"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            json.load(f)
        return True, None
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {e}"
    except Exception as e:
        return False, str(e)


def validate_jsonl(path: Path) -> Tuple[bool, Optional[str], int]:
    """Validate JSONL file, return line count"""
    try:
        count = 0
        line_num = 0
        with open(path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if line:
                    json.loads(line)
                    count += 1
        return True, None, count
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON at line {line_num}: {e}", 0
    except Exception as e:
        return False, str(e), 0


def check_import(module_name: str) -> Tuple[bool, Optional[str]]:
    """Try to import a module"""
    try:
        importlib.import_module(module_name)
        return True, None
    except ImportError as e:
        return False, f"ImportError: {e}"
    except Exception as e:
        return False, f"Error: {e}"


def check_python_syntax(path: Path) -> Tuple[bool, Optional[str]]:
    """Check Python file syntax using py_compile"""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return True, None
        return False, result.stderr.strip()
    except Exception as e:
        return False, str(e)


def check_package(name: str, import_name: str = None, min_version: str = None) -> Tuple[bool, Optional[str], Optional[str]]:
    """Check if package is installed and optionally check version"""
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
                pass  # packaging not installed, skip version check

        return True, None, version
    except ImportError:
        return False, "not installed", None
    except Exception as e:
        return False, str(e), None


def find_orphaned_files(base_path: Path, expected: Dict) -> List[str]:
    """Find Python files that exist but aren't in the expected list"""
    orphaned = []
    ai_gateway = base_path / "ai_gateway"

    if not ai_gateway.exists():
        return orphaned

    expected_paths = set(expected.keys())

    for py_file in ai_gateway.rglob("*.py"):
        if "__pycache__" in str(py_file):
            continue
        rel_path = str(py_file.relative_to(base_path)).replace("\\", "/")
        if rel_path not in expected_paths:
            orphaned.append(rel_path)

    return orphaned


# ═══════════════════════════════════════════════════════════════════════════
# MAIN DIAGNOSTIC
# ═══════════════════════════════════════════════════════════════════════════

def run_diagnostic(base_path: Path = None) -> DiagnosticReport:
    """Run full system diagnostic"""

    if base_path is None:
        # Try to detect base path
        candidates = [
            Path("."),
            Path("C:/Users/kirillDev/Desktop/TradingBot/minibot"),
            Path.home() / "TradingBot" / "minibot",
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

    # Add base_path to Python path for imports
    sys.path.insert(0, str(base_path))

    # Track phases
    phase_stats = {}

    # ─────────────────────────────────────────────────────────────────────
    # CHECK COMPONENTS
    # ─────────────────────────────────────────────────────────────────────

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

        # Track phase
        phase = status.phase
        if phase not in phase_stats:
            phase_stats[phase] = {"total": 0, "ok": 0, "broken": 0, "missing": 0}
        phase_stats[phase]["total"] += 1

        if status.exists:
            comp_type = spec.get("type", "module")

            if comp_type == "module":
                # Check syntax
                syntax_ok, syntax_err = check_python_syntax(full_path)
                if not syntax_ok:
                    status.works = False
                    status.error = f"Syntax error: {syntax_err}"
                else:
                    # Try import if specified
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

            elif comp_type in ("script", "doc"):
                status.works = True

        # Categorize
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
        else:  # NOT_IMPL
            report.not_implemented.append(status)
            report.not_impl_count += 1

    # ─────────────────────────────────────────────────────────────────────
    # CHECK ENVIRONMENT VARIABLES
    # ─────────────────────────────────────────────────────────────────────

    # Also check secrets file
    env_from_file = {}
    secrets_path = Path("C:/secrets/hope.env")
    if secrets_path.exists():
        try:
            for line in secrets_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    env_from_file[key.strip()] = value.strip()
        except Exception:
            pass

    for var_name, spec in EXPECTED_ENV_VARS.items():
        value = os.environ.get(var_name) or env_from_file.get(var_name)
        if value:
            report.env_ok.append(var_name)
        elif spec.get("required"):
            report.env_missing.append(var_name)
        else:
            report.env_warnings.append(f"{var_name} (optional, default: {spec.get('default', 'none')})")

    # ─────────────────────────────────────────────────────────────────────
    # CHECK PACKAGES
    # ─────────────────────────────────────────────────────────────────────

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

    # ─────────────────────────────────────────────────────────────────────
    # FIND ORPHANED FILES
    # ─────────────────────────────────────────────────────────────────────

    report.orphaned = find_orphaned_files(base_path, EXPECTED_COMPONENTS)

    # ─────────────────────────────────────────────────────────────────────
    # CALCULATE PHASE COMPLETION
    # ─────────────────────────────────────────────────────────────────────

    for phase, stats in phase_stats.items():
        if stats["total"] > 0:
            completion = stats["ok"] / stats["total"] * 100
            report.phases[phase] = {
                **stats,
                "completion_pct": round(completion, 1),
            }

    report.calculate_checksum()
    return report


# ═══════════════════════════════════════════════════════════════════════════
# OUTPUT FORMATTERS
# ═══════════════════════════════════════════════════════════════════════════

def print_report(report: DiagnosticReport):
    """Print human-readable report"""

    print("=" * 70)
    print("HOPE AI - SYSTEM DIAGNOSTIC REPORT")
    print("=" * 70)
    print(f"Timestamp: {report.timestamp}")
    print(f"Base Path: {report.base_path}")
    print(f"Checksum:  {report.checksum}")
    print()

    # ─────────────────────────────────────────────────────────────────────
    # SUMMARY
    # ─────────────────────────────────────────────────────────────────────

    print("-" * 70)
    print("SUMMARY")
    print("-" * 70)

    total = report.total_components
    print(f"  Total Components: {total}")
    print(f"  [OK] OK:           {report.ok_count} ({report.ok_count/total*100:.0f}%)")
    print(f"  [X]  BROKEN:       {report.broken_count} ({report.broken_count/total*100:.0f}%)")
    print(f"  [!]  MISSING:      {report.missing_count} ({report.missing_count/total*100:.0f}%)")
    print(f"  [~]  NOT IMPL:     {report.not_impl_count} ({report.not_impl_count/total*100:.0f}%)")
    print()

    # ─────────────────────────────────────────────────────────────────────
    # PHASE COMPLETION
    # ─────────────────────────────────────────────────────────────────────

    print("-" * 70)
    print("PHASE COMPLETION")
    print("-" * 70)

    phase_order = ["base", "3.1", "secret_ideas_p1", "secret_ideas_p2",
                   "secret_ideas_p3", "secret_ideas_p4", "secret_ideas_p5",
                   "secret_ideas_p6", "testnet", "live"]

    for phase in phase_order:
        if phase in report.phases:
            stats = report.phases[phase]
            bar_len = 20
            filled = int(stats["completion_pct"] / 100 * bar_len)
            bar = "#" * filled + "-" * (bar_len - filled)
            print(f"  {phase:20} [{bar}] {stats['completion_pct']:5.1f}% ({stats['ok']}/{stats['total']})")

    # Print other phases
    for phase, stats in report.phases.items():
        if phase not in phase_order:
            bar_len = 20
            filled = int(stats["completion_pct"] / 100 * bar_len)
            bar = "#" * filled + "-" * (bar_len - filled)
            print(f"  {phase:20} [{bar}] {stats['completion_pct']:5.1f}% ({stats['ok']}/{stats['total']})")

    print()

    # ─────────────────────────────────────────────────────────────────────
    # BROKEN COMPONENTS
    # ─────────────────────────────────────────────────────────────────────

    if report.broken:
        print("-" * 70)
        print("[X] BROKEN (exists but doesn't work)")
        print("-" * 70)
        for comp in report.broken:
            print(f"  * {comp.path}")
            print(f"    Error: {comp.error}")
        print()

    # ─────────────────────────────────────────────────────────────────────
    # MISSING COMPONENTS
    # ─────────────────────────────────────────────────────────────────────

    if report.missing:
        print("-" * 70)
        print("[!] MISSING (required but not found)")
        print("-" * 70)
        for comp in report.missing:
            print(f"  * {comp.path}")
            if comp.description:
                print(f"    Desc: {comp.description}")
            print(f"    Phase: {comp.phase}")
        print()

    # ─────────────────────────────────────────────────────────────────────
    # NOT IMPLEMENTED
    # ─────────────────────────────────────────────────────────────────────

    if report.not_implemented:
        print("-" * 70)
        print("[~] NOT IMPLEMENTED (optional, future phases)")
        print("-" * 70)
        for comp in report.not_implemented:
            print(f"  * {comp.path} [{comp.phase}]")
            if comp.description:
                print(f"    -> {comp.description}")
        print()

    # ─────────────────────────────────────────────────────────────────────
    # ORPHANED FILES
    # ─────────────────────────────────────────────────────────────────────

    if report.orphaned:
        print("-" * 70)
        print("[?] ORPHANED (exist but not in spec)")
        print("-" * 70)
        for path in report.orphaned[:20]:  # Limit to 20
            print(f"  * {path}")
        if len(report.orphaned) > 20:
            print(f"  ... and {len(report.orphaned) - 20} more")
        print()

    # ─────────────────────────────────────────────────────────────────────
    # ENVIRONMENT
    # ─────────────────────────────────────────────────────────────────────

    print("-" * 70)
    print("ENVIRONMENT VARIABLES")
    print("-" * 70)

    if report.env_ok:
        print(f"  [OK] Set: {', '.join(report.env_ok)}")
    if report.env_missing:
        print(f"  [X] Missing (required): {', '.join(report.env_missing)}")
    if report.env_warnings:
        print(f"  [~] Not set (optional):")
        for w in report.env_warnings:
            print(f"      * {w}")
    print()

    # ─────────────────────────────────────────────────────────────────────
    # PACKAGES
    # ─────────────────────────────────────────────────────────────────────

    print("-" * 70)
    print("PYTHON PACKAGES")
    print("-" * 70)

    if report.packages_ok:
        print(f"  [OK] Installed: {len(report.packages_ok)}")
    if report.packages_missing:
        print(f"  [X] Missing: {', '.join(report.packages_missing)}")
    if report.packages_outdated:
        print(f"  [~] Outdated:")
        for p in report.packages_outdated:
            print(f"      * {p}")
    print()

    # ─────────────────────────────────────────────────────────────────────
    # OK COMPONENTS (condensed)
    # ─────────────────────────────────────────────────────────────────────

    print("-" * 70)
    print("[OK] WORKING COMPONENTS")
    print("-" * 70)

    # Group by directory
    by_dir = {}
    for comp in report.ok:
        dir_name = str(Path(comp.path).parent)
        if dir_name not in by_dir:
            by_dir[dir_name] = []
        by_dir[dir_name].append(comp)

    for dir_name, comps in sorted(by_dir.items()):
        print(f"  {dir_name}/")
        for comp in comps:
            details = ""
            if comp.details:
                if "line_count" in comp.details:
                    details = f" ({comp.details['line_count']} records)"
            print(f"    + {Path(comp.path).name}{details}")

    print()
    print("=" * 70)
    print("END OF DIAGNOSTIC REPORT")
    print("=" * 70)


def export_json(report: DiagnosticReport, path: Path = None):
    """Export report as JSON"""

    # Convert to dict
    data = {
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
        "broken": [asdict(c) for c in report.broken],
        "missing": [asdict(c) for c in report.missing],
        "not_implemented": [asdict(c) for c in report.not_implemented],
        "orphaned": report.orphaned,
        "environment": {
            "ok": report.env_ok,
            "missing": report.env_missing,
            "warnings": report.env_warnings,
        },
        "packages": {
            "ok": report.packages_ok,
            "missing": report.packages_missing,
            "outdated": report.packages_outdated,
        },
    }

    if path:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Report exported to: {path}")
    else:
        print(json.dumps(data, indent=2, ensure_ascii=False))


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    import argparse

    parser = argparse.ArgumentParser(description="HOPE AI System Diagnostic")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--export", type=str, help="Export JSON to file")
    parser.add_argument("--path", type=str, help="Base path to check")
    parser.add_argument("--fix", action="store_true", help="Attempt to fix issues (creates missing __init__.py)")

    args = parser.parse_args()

    base_path = Path(args.path) if args.path else None

    report = run_diagnostic(base_path)

    if args.json:
        export_json(report)
    elif args.export:
        export_json(report, Path(args.export))
    else:
        print_report(report)

    # Return exit code based on status
    if report.broken_count > 0:
        sys.exit(2)  # Broken components
    elif report.missing_count > 0:
        sys.exit(1)  # Missing components
    else:
        sys.exit(0)  # All OK


if __name__ == "__main__":
    main()
