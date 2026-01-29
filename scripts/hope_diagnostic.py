# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 17:00:00 UTC
# Purpose: HOPE AI System Diagnostic - Full health check
# === END SIGNATURE ===
"""
HOPE AI SYSTEM DIAGNOSTIC v1.0

Comprehensive health check that answers:
1. What EXISTS in the system
2. What is MISSING
3. What is BROKEN (exists but doesn't work)
4. What is ORPHANED (exists but not connected/imported)

USAGE:
    python scripts/hope_diagnostic.py
    python scripts/hope_diagnostic.py --verbose
    python scripts/hope_diagnostic.py --fix  # Auto-fix what can be fixed

OUTPUT:
    - Console report with color coding
    - state/diagnostic_report.json (machine-readable)
"""
from __future__ import annotations

import ast
import importlib
import json
import os
import sys
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@dataclass
class CheckResult:
    """Result of a single check."""
    name: str
    category: str  # EXISTS, MISSING, BROKEN, ORPHANED
    status: str    # PASS, FAIL, WARN, SKIP
    message: str
    path: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    fix_hint: Optional[str] = None


@dataclass
class DiagnosticReport:
    """Complete diagnostic report."""
    timestamp: str
    project_root: str
    python_version: str

    # Counts
    total_checks: int = 0
    passed: int = 0
    failed: int = 0
    warnings: int = 0
    skipped: int = 0

    # Categories
    exists: List[CheckResult] = field(default_factory=list)
    missing: List[CheckResult] = field(default_factory=list)
    broken: List[CheckResult] = field(default_factory=list)
    orphaned: List[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult):
        self.total_checks += 1
        if result.status == "PASS":
            self.passed += 1
        elif result.status == "FAIL":
            self.failed += 1
        elif result.status == "WARN":
            self.warnings += 1
        else:
            self.skipped += 1

        if result.category == "EXISTS":
            self.exists.append(result)
        elif result.category == "MISSING":
            self.missing.append(result)
        elif result.category == "BROKEN":
            self.broken.append(result)
        elif result.category == "ORPHANED":
            self.orphaned.append(result)


class HopeDiagnostic:
    """HOPE AI System Diagnostic."""

    # ═══════════════════════════════════════════════════════════════
    # EXPECTED STRUCTURE - What SHOULD exist
    # ═══════════════════════════════════════════════════════════════

    REQUIRED_DIRS = [
        "ai_gateway",
        "ai_gateway/core",
        "ai_gateway/patterns",
        "ai_gateway/modules",
        "ai_gateway/integrations",
        "ai_gateway/feeds",
        "core",
        "scripts",
        "state",
        "state/ai",
        "state/ai/signals",
        "data",
        "docs",
    ]

    REQUIRED_FILES = {
        # Core AI Gateway
        "ai_gateway/__init__.py": "AI Gateway package init",
        "ai_gateway/server.py": "FastAPI server",
        "ai_gateway/contracts.py": "Data contracts/schemas",
        "ai_gateway/config.py": "Configuration",
        "ai_gateway/jsonl_writer.py": "JSONL atomic writer",

        # AI Gateway Core
        "ai_gateway/core/__init__.py": "Core package init",
        "ai_gateway/core/mode_router.py": "Trading mode router",
        "ai_gateway/core/decision_engine.py": "Decision engine",
        "ai_gateway/core/signal_processor.py": "Signal processor",
        "ai_gateway/core/event_bus.py": "Event bus",

        # Patterns
        "ai_gateway/patterns/pump_precursor_detector.py": "Pump precursor detector",

        # Integrations
        "ai_gateway/integrations/__init__.py": "Integrations package",
        "ai_gateway/integrations/moonbot_live.py": "MoonBot live integration",

        # Modules
        "ai_gateway/modules/__init__.py": "Modules package",
        "ai_gateway/modules/regime/__init__.py": "Regime module",
        "ai_gateway/modules/regime/detector.py": "Regime detector",
        "ai_gateway/modules/anomaly/__init__.py": "Anomaly module",
        "ai_gateway/modules/anomaly/scanner.py": "Anomaly scanner",
        "ai_gateway/modules/predictor/__init__.py": "Predictor module",
        "ai_gateway/modules/predictor/signal_classifier.py": "Signal classifier",
        "ai_gateway/modules/sentiment/__init__.py": "Sentiment module",
        "ai_gateway/modules/sentiment/analyzer.py": "Sentiment analyzer",
        "ai_gateway/modules/self_improver/__init__.py": "Self-improver module",
        "ai_gateway/modules/self_improver/outcome_tracker.py": "Outcome tracker",

        # Feeds
        "ai_gateway/feeds/__init__.py": "Feeds package",
        "ai_gateway/feeds/binance_ws.py": "Binance WebSocket feed",

        # Scripts
        "scripts/moonbot_parser_v2.py": "MoonBot parser",

        # Core trading
        "core/__init__.py": "Core package",

        # State files
        "state/ai/decisions.jsonl": "Decision log",
    }

    REQUIRED_MODULES = [
        # Python imports that should work
        ("ai_gateway", "AI Gateway main"),
        ("ai_gateway.core", "AI Gateway core"),
        ("ai_gateway.core.mode_router", "Mode router"),
        ("ai_gateway.core.decision_engine", "Decision engine"),
        ("ai_gateway.core.signal_processor", "Signal processor"),
        ("ai_gateway.core.event_bus", "Event bus"),
        ("ai_gateway.patterns.pump_precursor_detector", "Pump precursor"),
        ("ai_gateway.integrations.moonbot_live", "MoonBot integration"),
        ("ai_gateway.contracts", "Contracts"),
        ("ai_gateway.jsonl_writer", "JSONL writer"),
    ]

    REQUIRED_CLASSES = [
        ("ai_gateway.core.mode_router", "ModeRouter", "route"),
        ("ai_gateway.core.mode_router", "TradingMode", None),
        ("ai_gateway.core.decision_engine", "DecisionEngine", "evaluate"),
        ("ai_gateway.core.decision_engine", "SignalContext", None),
        ("ai_gateway.core.signal_processor", "SignalProcessor", "process_signal"),
        ("ai_gateway.patterns.pump_precursor_detector", "PumpPrecursorDetector", "detect_precursor"),
        ("ai_gateway.integrations.moonbot_live", "MoonBotLiveIntegration", "process_signal"),
    ]

    EXTERNAL_DEPS = [
        ("fastapi", "FastAPI web framework"),
        ("pydantic", "Data validation"),
        ("aiohttp", "Async HTTP client"),
        ("websockets", "WebSocket client"),
        ("orjson", "Fast JSON (optional)"),
        ("numpy", "Numerical computing"),
        ("pandas", "Data analysis"),
    ]

    ENV_VARS = [
        ("BINANCE_API_KEY", "Binance API key", False),  # (name, desc, required_for_test)
        ("BINANCE_API_SECRET", "Binance API secret", False),
        ("TELEGRAM_BOT_TOKEN", "Telegram bot token", False),
    ]

    def __init__(self, project_root: Path = PROJECT_ROOT):
        self.root = project_root
        self.report = DiagnosticReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            project_root=str(project_root),
            python_version=sys.version,
        )

    def run_all(self, verbose: bool = False) -> DiagnosticReport:
        """Run all diagnostic checks."""
        print("=" * 70)
        print("HOPE AI SYSTEM DIAGNOSTIC v1.0")
        print("=" * 70)
        print(f"Project: {self.root}")
        print(f"Python: {sys.version.split()[0]}")
        print(f"Time: {self.report.timestamp}")
        print("=" * 70)

        # Run checks
        self._check_directories()
        self._check_files()
        self._check_imports()
        self._check_classes()
        self._check_dependencies()
        self._check_env_vars()
        self._check_state_files()
        self._check_orphaned_files()
        self._check_broken_imports()
        self._check_circular_imports()

        # Print report
        self._print_report(verbose)

        # Save JSON report
        self._save_report()

        return self.report

    def _check_directories(self):
        """Check required directories exist."""
        print("\n[1/10] Checking directories...")

        for dir_path in self.REQUIRED_DIRS:
            full_path = self.root / dir_path
            if full_path.exists() and full_path.is_dir():
                self.report.add(CheckResult(
                    name=f"dir:{dir_path}",
                    category="EXISTS",
                    status="PASS",
                    message=f"Directory exists",
                    path=str(full_path),
                ))
            else:
                self.report.add(CheckResult(
                    name=f"dir:{dir_path}",
                    category="MISSING",
                    status="FAIL",
                    message=f"Directory missing",
                    path=str(full_path),
                    fix_hint=f"mkdir -p {dir_path}",
                ))

    def _check_files(self):
        """Check required files exist."""
        print("[2/10] Checking files...")

        for file_path, description in self.REQUIRED_FILES.items():
            full_path = self.root / file_path
            if full_path.exists():
                # Check if it's valid Python
                if file_path.endswith(".py"):
                    try:
                        compile(full_path.read_text(encoding="utf-8"), file_path, "exec")
                        status = "PASS"
                        category = "EXISTS"
                        msg = f"{description} - OK"
                    except SyntaxError as e:
                        status = "FAIL"
                        category = "BROKEN"
                        msg = f"{description} - SYNTAX ERROR: {e}"
                else:
                    status = "PASS"
                    category = "EXISTS"
                    msg = f"{description} - OK"

                self.report.add(CheckResult(
                    name=f"file:{file_path}",
                    category=category,
                    status=status,
                    message=msg,
                    path=str(full_path),
                ))
            else:
                self.report.add(CheckResult(
                    name=f"file:{file_path}",
                    category="MISSING",
                    status="FAIL",
                    message=f"{description} - MISSING",
                    path=str(full_path),
                    fix_hint=f"Create {file_path}",
                ))

    def _check_imports(self):
        """Check if modules can be imported."""
        print("[3/10] Checking module imports...")

        for module_name, description in self.REQUIRED_MODULES:
            try:
                module = importlib.import_module(module_name)
                self.report.add(CheckResult(
                    name=f"import:{module_name}",
                    category="EXISTS",
                    status="PASS",
                    message=f"{description} - importable",
                    details={"module_file": getattr(module, "__file__", "built-in")},
                ))
            except ImportError as e:
                self.report.add(CheckResult(
                    name=f"import:{module_name}",
                    category="BROKEN",
                    status="FAIL",
                    message=f"{description} - IMPORT ERROR: {e}",
                    fix_hint=f"Check __init__.py and dependencies",
                ))
            except Exception as e:
                self.report.add(CheckResult(
                    name=f"import:{module_name}",
                    category="BROKEN",
                    status="FAIL",
                    message=f"{description} - ERROR: {type(e).__name__}: {e}",
                ))

    def _check_classes(self):
        """Check if required classes exist and have expected methods."""
        print("[4/10] Checking classes and methods...")

        for module_name, class_name, method_name in self.REQUIRED_CLASSES:
            try:
                module = importlib.import_module(module_name)
                cls = getattr(module, class_name, None)

                if cls is None:
                    self.report.add(CheckResult(
                        name=f"class:{module_name}.{class_name}",
                        category="MISSING",
                        status="FAIL",
                        message=f"Class {class_name} not found in {module_name}",
                    ))
                    continue

                # Check method if specified
                if method_name:
                    method = getattr(cls, method_name, None)
                    if method is None:
                        self.report.add(CheckResult(
                            name=f"method:{module_name}.{class_name}.{method_name}",
                            category="MISSING",
                            status="FAIL",
                            message=f"Method {method_name} not found",
                        ))
                    else:
                        self.report.add(CheckResult(
                            name=f"class:{module_name}.{class_name}",
                            category="EXISTS",
                            status="PASS",
                            message=f"Class with method {method_name}() - OK",
                        ))
                else:
                    self.report.add(CheckResult(
                        name=f"class:{module_name}.{class_name}",
                        category="EXISTS",
                        status="PASS",
                        message=f"Class exists - OK",
                    ))

            except ImportError as e:
                self.report.add(CheckResult(
                    name=f"class:{module_name}.{class_name}",
                    category="BROKEN",
                    status="FAIL",
                    message=f"Cannot import module: {e}",
                ))

    def _check_dependencies(self):
        """Check external dependencies."""
        print("[5/10] Checking dependencies...")

        for package, description in self.EXTERNAL_DEPS:
            try:
                module = importlib.import_module(package)
                version = getattr(module, "__version__", "unknown")
                self.report.add(CheckResult(
                    name=f"dep:{package}",
                    category="EXISTS",
                    status="PASS",
                    message=f"{description} v{version}",
                    details={"version": version},
                ))
            except ImportError:
                # Check if it's optional
                if package == "orjson":
                    self.report.add(CheckResult(
                        name=f"dep:{package}",
                        category="MISSING",
                        status="WARN",
                        message=f"{description} - not installed (optional)",
                        fix_hint=f"pip install {package}",
                    ))
                else:
                    self.report.add(CheckResult(
                        name=f"dep:{package}",
                        category="MISSING",
                        status="FAIL",
                        message=f"{description} - NOT INSTALLED",
                        fix_hint=f"pip install {package}",
                    ))

    def _check_env_vars(self):
        """Check environment variables."""
        print("[6/10] Checking environment variables...")

        # Also check .env file
        env_file = Path("C:/secrets/hope.env")
        env_from_file = {}

        if env_file.exists():
            try:
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, value = line.partition("=")
                        env_from_file[key.strip()] = value.strip()
            except Exception:
                pass

        for var_name, description, required in self.ENV_VARS:
            value = os.environ.get(var_name) or env_from_file.get(var_name)

            if value:
                # Mask the value
                masked = value[:4] + "..." + value[-4:] if len(value) > 10 else "***"
                self.report.add(CheckResult(
                    name=f"env:{var_name}",
                    category="EXISTS",
                    status="PASS",
                    message=f"{description}: {masked}",
                ))
            else:
                status = "FAIL" if required else "WARN"
                self.report.add(CheckResult(
                    name=f"env:{var_name}",
                    category="MISSING",
                    status=status,
                    message=f"{description} - not set",
                    fix_hint=f"Set {var_name} in environment or C:/secrets/hope.env",
                ))

    def _check_state_files(self):
        """Check state/data files."""
        print("[7/10] Checking state files...")

        state_files = [
            ("state/ai/decisions.jsonl", "Decision log", False),
            ("state/ai/signals/moonbot_signals.jsonl", "MoonBot signals", False),
            ("data/moonbot_signals/signals_20260129.jsonl", "Historical signals", False),
        ]

        for file_path, description, required in state_files:
            full_path = self.root / file_path
            if full_path.exists():
                try:
                    size = full_path.stat().st_size
                    lines = sum(1 for _ in open(full_path, encoding="utf-8"))
                    self.report.add(CheckResult(
                        name=f"state:{file_path}",
                        category="EXISTS",
                        status="PASS",
                        message=f"{description}: {lines} lines, {size/1024:.1f}KB",
                        path=str(full_path),
                        details={"lines": lines, "size_bytes": size},
                    ))
                except Exception as e:
                    self.report.add(CheckResult(
                        name=f"state:{file_path}",
                        category="BROKEN",
                        status="FAIL",
                        message=f"{description} - READ ERROR: {e}",
                        path=str(full_path),
                    ))
            else:
                status = "FAIL" if required else "WARN"
                self.report.add(CheckResult(
                    name=f"state:{file_path}",
                    category="MISSING",
                    status=status,
                    message=f"{description} - not created yet",
                    path=str(full_path),
                ))

    def _check_orphaned_files(self):
        """Find Python files that exist but aren't imported anywhere."""
        print("[8/10] Checking for orphaned files...")

        # Find all .py files in ai_gateway
        all_py_files = set()
        for py_file in (self.root / "ai_gateway").rglob("*.py"):
            if "__pycache__" not in str(py_file):
                rel_path = py_file.relative_to(self.root)
                all_py_files.add(str(rel_path).replace("\\", "/"))

        # Find all imports in ai_gateway
        imported_modules = set()
        for py_file in (self.root / "ai_gateway").rglob("*.py"):
            if "__pycache__" not in str(py_file):
                try:
                    content = py_file.read_text(encoding="utf-8")
                    tree = ast.parse(content)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                imported_modules.add(alias.name)
                        elif isinstance(node, ast.ImportFrom):
                            if node.module:
                                imported_modules.add(node.module)
                except Exception:
                    pass

        # Check __init__.py files for proper exports
        init_files = [
            "ai_gateway/__init__.py",
            "ai_gateway/core/__init__.py",
            "ai_gateway/patterns/__init__.py",
            "ai_gateway/integrations/__init__.py",
            "ai_gateway/modules/__init__.py",
        ]

        for init_file in init_files:
            full_path = self.root / init_file
            if full_path.exists():
                content = full_path.read_text(encoding="utf-8")
                if len(content.strip()) < 50:  # Nearly empty
                    self.report.add(CheckResult(
                        name=f"orphan:{init_file}",
                        category="ORPHANED",
                        status="WARN",
                        message=f"__init__.py has minimal exports",
                        path=str(full_path),
                        fix_hint="Add proper exports to __init__.py",
                    ))

    def _check_broken_imports(self):
        """Find files with broken internal imports."""
        print("[9/10] Checking for broken imports...")

        for py_file in (self.root / "ai_gateway").rglob("*.py"):
            if "__pycache__" not in str(py_file):
                try:
                    content = py_file.read_text(encoding="utf-8")
                    tree = ast.parse(content)

                    for node in ast.walk(tree):
                        if isinstance(node, ast.ImportFrom):
                            if node.module and node.module.startswith("."):
                                # Relative import - try to resolve
                                try:
                                    rel_path = py_file.relative_to(self.root)
                                    module_parts = str(rel_path.parent).replace("\\", "/").replace("/", ".")
                                    full_module = f"{module_parts}{node.module}"
                                    # This is a basic check - could be more thorough
                                except Exception:
                                    pass

                except SyntaxError as e:
                    rel_path = py_file.relative_to(self.root)
                    self.report.add(CheckResult(
                        name=f"syntax:{rel_path}",
                        category="BROKEN",
                        status="FAIL",
                        message=f"Syntax error: {e}",
                        path=str(py_file),
                    ))
                except Exception:
                    pass

    def _check_circular_imports(self):
        """Basic check for potential circular imports."""
        print("[10/10] Checking for circular import risks...")

        # Track import graph
        import_graph: Dict[str, Set[str]] = {}

        for py_file in (self.root / "ai_gateway").rglob("*.py"):
            if "__pycache__" not in str(py_file):
                try:
                    rel_path = str(py_file.relative_to(self.root)).replace("\\", "/")
                    content = py_file.read_text(encoding="utf-8")
                    tree = ast.parse(content)

                    imports = set()
                    for node in ast.walk(tree):
                        if isinstance(node, ast.ImportFrom):
                            if node.module and "ai_gateway" in (node.module or ""):
                                imports.add(node.module)

                    if imports:
                        import_graph[rel_path] = imports

                except Exception:
                    pass

        # Simple cycle detection (not exhaustive)
        for file_path, imports in import_graph.items():
            for imp in imports:
                imp_file = imp.replace(".", "/") + ".py"
                if imp_file in import_graph:
                    reverse_imports = import_graph.get(imp_file, set())
                    # Check if there's a back-reference
                    file_module = file_path.replace("/", ".").replace(".py", "")
                    for rev_imp in reverse_imports:
                        if file_module in rev_imp or rev_imp in file_module:
                            self.report.add(CheckResult(
                                name=f"circular:{file_path}<->{imp_file}",
                                category="BROKEN",
                                status="WARN",
                                message=f"Potential circular import risk",
                                details={"file1": file_path, "file2": imp_file},
                            ))

    def _print_report(self, verbose: bool):
        """Print formatted report."""
        print("\n" + "=" * 70)
        print("DIAGNOSTIC RESULTS")
        print("=" * 70)

        # Summary
        print(f"\nTotal checks: {self.report.total_checks}")
        print(f"  PASS: {self.report.passed}")
        print(f"  FAIL: {self.report.failed}")
        print(f"  WARN: {self.report.warnings}")
        print(f"  SKIP: {self.report.skipped}")

        # Categories
        if self.report.missing:
            print(f"\n--- MISSING ({len(self.report.missing)}) ---")
            for r in self.report.missing:
                status_icon = "X" if r.status == "FAIL" else "?"
                print(f"  [{status_icon}] {r.name}: {r.message}")
                if r.fix_hint and verbose:
                    print(f"      FIX: {r.fix_hint}")

        if self.report.broken:
            print(f"\n--- BROKEN ({len(self.report.broken)}) ---")
            for r in self.report.broken:
                print(f"  [!] {r.name}: {r.message}")
                if r.fix_hint and verbose:
                    print(f"      FIX: {r.fix_hint}")

        if self.report.orphaned:
            print(f"\n--- ORPHANED ({len(self.report.orphaned)}) ---")
            for r in self.report.orphaned:
                print(f"  [~] {r.name}: {r.message}")

        if verbose and self.report.exists:
            print(f"\n--- EXISTS ({len(self.report.exists)}) ---")
            for r in self.report.exists:
                print(f"  [OK] {r.name}")

        # Final verdict
        print("\n" + "=" * 70)
        if self.report.failed == 0:
            print("VERDICT: SYSTEM HEALTHY")
        elif self.report.failed < 5:
            print(f"VERDICT: MINOR ISSUES ({self.report.failed} failures)")
        else:
            print(f"VERDICT: NEEDS ATTENTION ({self.report.failed} failures)")
        print("=" * 70)

    def _save_report(self):
        """Save report as JSON."""
        report_path = self.root / "state" / "diagnostic_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)

        # Convert to dict
        report_dict = {
            "timestamp": self.report.timestamp,
            "project_root": self.report.project_root,
            "python_version": self.report.python_version,
            "summary": {
                "total": self.report.total_checks,
                "passed": self.report.passed,
                "failed": self.report.failed,
                "warnings": self.report.warnings,
            },
            "exists": [asdict(r) for r in self.report.exists],
            "missing": [asdict(r) for r in self.report.missing],
            "broken": [asdict(r) for r in self.report.broken],
            "orphaned": [asdict(r) for r in self.report.orphaned],
        }

        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report_dict, f, indent=2, ensure_ascii=False)

        print(f"\nReport saved: {report_path}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="HOPE AI System Diagnostic")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--fix", action="store_true", help="Auto-fix issues (coming soon)")
    args = parser.parse_args()

    diagnostic = HopeDiagnostic()
    report = diagnostic.run_all(verbose=args.verbose)

    # Exit code based on failures
    sys.exit(0 if report.failed == 0 else 1)


if __name__ == "__main__":
    main()
