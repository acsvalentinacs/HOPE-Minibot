# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T14:45:00Z
# Purpose: HOPE Trading Bot - Super Diagnostic for Binance Readiness
# === END SIGNATURE ===
"""
HOPE SUPER DIAGNOSTIC v1.0

Полная диагностика готовности торгового бота к Binance.
Сравнивает текущее состояние с ТЗ v1.0.

Запуск:
    python tools/hope_super_diagnostic.py
"""

import sys
import os
import json
import importlib
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

# Ensure we're in the right directory
ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))


@dataclass
class ModuleStatus:
    """Status of a single module."""
    path: str
    exists: bool
    imports_ok: bool
    has_class: bool
    class_name: str
    completeness_pct: int
    issues: List[str]
    features: List[str]


@dataclass
class ComponentStatus:
    """Status of a component (group of modules)."""
    name: str
    required_modules: List[str]
    modules: List[ModuleStatus]
    readiness_pct: int
    status: str  # READY, PARTIAL, MISSING


class HopeDiagnostic:
    """HOPE Trading Bot Super Diagnostic."""

    # TZ v1.0 required modules
    TZ_REQUIREMENTS = {
        "Phase 1: AI Foundation": {
            "core/ai/technical_indicators.py": {
                "class": "TechnicalIndicators",
                "methods": ["rsi", "macd", "bollinger_bands", "atr", "ema", "sma", "volume_profile"],
            },
            "core/ai/signal_engine.py": {
                "class": "SignalEngine",
                "methods": ["generate_signal", "scan_market"],
            },
        },
        "Phase 2: Strategies": {
            "core/strategy/base.py": {
                "class": "BaseStrategy",
                "methods": ["should_enter", "should_exit", "can_trade"],
            },
            "core/strategy/momentum.py": {
                "class": "MomentumStrategy",
                "methods": ["should_enter", "should_exit"],
            },
            "core/strategy/mean_reversion.py": {
                "class": "MeanReversionStrategy",
                "methods": ["should_enter", "should_exit"],
            },
            "core/strategy/breakout.py": {
                "class": "BreakoutStrategy",
                "methods": ["should_enter", "should_exit"],
            },
            "core/strategy/orchestrator.py": {
                "class": "StrategyOrchestrator",
                "methods": ["detect_market_regime", "select_strategy", "run_cycle"],
            },
            "core/strategy/regime.py": {
                "class": "MarketRegimeDetector",
                "methods": ["detect"],
            },
        },
        "Phase 3: Backtesting": {
            "core/backtest/engine.py": {
                "class": "BacktestEngine",
                "methods": ["run", "load_data"],
            },
            "core/backtest/data_loader.py": {
                "class": "DataLoader",
                "methods": ["load", "generate_synthetic"],
            },
            "core/backtest/metrics.py": {
                "class": "BacktestMetrics",
                "methods": ["calculate"],
            },
        },
        "Phase 4: ML Integration": {
            "core/ai/features.py": {
                "class": "FeatureExtractor",
                "methods": ["extract"],
            },
            "core/ai/ml_predictor.py": {
                "class": "MLPredictor",
                "methods": ["predict"],
            },
        },
        "Phase 5: Auto-Optimization": {
            "core/analytics/performance.py": {
                "class": "PerformanceTracker",
                "methods": ["update", "get_metrics"],
            },
            "core/analytics/auto_tuner.py": {
                "class": "AutoTuner",
                "methods": ["run_check"],
            },
        },
    }

    # Existing infrastructure (from previous development)
    EXISTING_INFRASTRUCTURE = [
        ("core/order_router.py", "OrderRouter", "Order execution"),
        ("core/risk_engine.py", "RiskEngine", "Risk management"),
        ("core/trade/live_gate.py", "LiveGate", "MAINNET barrier"),
        ("core/trade/micro_trade_executor.py", "MicroTradeExecutor", "$10 trades"),
        ("core/exchange/binance_spot_client.py", "BinanceSpotClient", "Binance API"),
        ("core/telegram_signals.py", "TelegramSignals", "Signal publisher"),
        ("core/outcome_tracker.py", "OutcomeTracker", "MFE/MAE tracking"),
        ("core/market_intel.py", "MarketIntel", "Market data"),
        ("core/event_classifier.py", "EventClassifier", "News sentiment"),
    ]

    def __init__(self):
        self.results: Dict[str, ComponentStatus] = {}
        self.infrastructure_status: List[Dict] = []
        self.test_results: Dict[str, bool] = {}
        self.dependencies: Dict[str, bool] = {}

    def check_module(self, rel_path: str, expected_class: str, expected_methods: List[str]) -> ModuleStatus:
        """Check a single module's status."""
        full_path = ROOT / rel_path
        issues = []
        features = []

        # Check existence
        exists = full_path.exists()
        if not exists:
            return ModuleStatus(
                path=rel_path,
                exists=False,
                imports_ok=False,
                has_class=False,
                class_name=expected_class,
                completeness_pct=0,
                issues=["File does not exist"],
                features=[],
            )

        # Try import
        module_path = rel_path.replace("/", ".").replace("\\", ".").replace(".py", "")
        imports_ok = False
        has_class = False
        found_methods = []

        try:
            module = importlib.import_module(module_path)
            imports_ok = True

            # Check class
            if hasattr(module, expected_class):
                has_class = True
                cls = getattr(module, expected_class)

                # Check methods
                for method in expected_methods:
                    if hasattr(cls, method):
                        found_methods.append(method)
                        features.append(f"{method}()")
                    else:
                        issues.append(f"Missing method: {method}()")
            else:
                issues.append(f"Missing class: {expected_class}")

        except Exception as e:
            issues.append(f"Import error: {type(e).__name__}: {str(e)[:50]}")

        # Calculate completeness
        if not exists:
            completeness = 0
        elif not imports_ok:
            completeness = 10
        elif not has_class:
            completeness = 30
        else:
            method_pct = len(found_methods) / len(expected_methods) * 100 if expected_methods else 100
            completeness = int(50 + method_pct * 0.5)

        return ModuleStatus(
            path=rel_path,
            exists=exists,
            imports_ok=imports_ok,
            has_class=has_class,
            class_name=expected_class,
            completeness_pct=completeness,
            issues=issues,
            features=features,
        )

    def check_infrastructure(self) -> None:
        """Check existing infrastructure modules."""
        for rel_path, class_name, description in self.EXISTING_INFRASTRUCTURE:
            full_path = ROOT / rel_path
            exists = full_path.exists()
            imports_ok = False

            if exists:
                try:
                    module_path = rel_path.replace("/", ".").replace("\\", ".").replace(".py", "")
                    module = importlib.import_module(module_path)
                    imports_ok = hasattr(module, class_name)
                except Exception:
                    pass

            self.infrastructure_status.append({
                "path": rel_path,
                "class": class_name,
                "description": description,
                "exists": exists,
                "imports_ok": imports_ok,
                "status": "OK" if imports_ok else ("EXISTS" if exists else "MISSING"),
            })

    def check_dependencies(self) -> None:
        """Check required Python packages."""
        packages = {
            "numpy": "Numerical operations",
            "pandas": "Data manipulation",
            "aiohttp": "Async HTTP",
            "python-telegram-bot": "Telegram bot",
            "lightgbm": "ML predictor (optional)",
            "scikit-learn": "ML metrics",
            "optuna": "Optimization (optional)",
        }

        for package, desc in packages.items():
            try:
                # Handle package name differences
                import_name = package.replace("-", "_")
                if package == "python-telegram-bot":
                    import_name = "telegram"
                elif package == "scikit-learn":
                    import_name = "sklearn"

                importlib.import_module(import_name)
                self.dependencies[package] = True
            except ImportError:
                self.dependencies[package] = False

    def run_tests(self) -> None:
        """Run key tests and collect results."""
        test_files = [
            "tests/test_indicators.py",
            "tests/test_phase2_strategies.py",
            "tests/test_phase3_backtest.py",
            "tests/test_phase4_ml.py",
            "tests/test_phase5_autotune.py",
        ]

        for test_file in test_files:
            test_path = ROOT / test_file
            if test_path.exists():
                # Try to import and count test classes
                try:
                    module_path = test_file.replace("/", ".").replace("\\", ".").replace(".py", "")
                    module = importlib.import_module(module_path)
                    test_classes = [name for name in dir(module) if name.startswith("Test")]
                    self.test_results[test_file] = len(test_classes) > 0
                except Exception:
                    self.test_results[test_file] = False
            else:
                self.test_results[test_file] = False

    def check_phase(self, phase_name: str, modules: Dict) -> ComponentStatus:
        """Check a complete phase."""
        module_statuses = []
        for rel_path, spec in modules.items():
            status = self.check_module(
                rel_path,
                spec["class"],
                spec.get("methods", [])
            )
            module_statuses.append(status)

        # Calculate phase readiness
        if not module_statuses:
            readiness = 0
        else:
            readiness = sum(m.completeness_pct for m in module_statuses) // len(module_statuses)

        if readiness >= 90:
            status = "READY"
        elif readiness >= 50:
            status = "PARTIAL"
        else:
            status = "MISSING"

        return ComponentStatus(
            name=phase_name,
            required_modules=list(modules.keys()),
            modules=module_statuses,
            readiness_pct=readiness,
            status=status,
        )

    def run_full_diagnostic(self) -> Dict[str, Any]:
        """Run complete diagnostic."""
        print("=" * 60)
        print("HOPE TRADING BOT - SUPER DIAGNOSTIC v1.0")
        print("=" * 60)
        print(f"Timestamp: {datetime.now().isoformat()}")
        print(f"Root: {ROOT}")
        print()

        # Check phases
        print("Checking TZ v1.0 Phases...")
        for phase_name, modules in self.TZ_REQUIREMENTS.items():
            status = self.check_phase(phase_name, modules)
            self.results[phase_name] = status
            icon = "[OK]" if status.status == "READY" else ("[PART]" if status.status == "PARTIAL" else "[MISS]")
            print(f"  {icon} {phase_name}: {status.readiness_pct}% ({status.status})")

        print()

        # Check infrastructure
        print("Checking Existing Infrastructure...")
        self.check_infrastructure()
        ok_count = sum(1 for s in self.infrastructure_status if s["status"] == "OK")
        print(f"  [OK] {ok_count}/{len(self.infrastructure_status)} modules OK")

        print()

        # Check dependencies
        print("Checking Dependencies...")
        self.check_dependencies()
        for pkg, installed in self.dependencies.items():
            icon = "[OK]" if installed else "[X]"
            print(f"  {icon} {pkg}")

        print()

        # Check tests
        print("Checking Test Coverage...")
        self.run_tests()
        for test_file, has_tests in self.test_results.items():
            icon = "[OK]" if has_tests else "[X]"
            print(f"  {icon} {test_file}")

        return self.generate_report()

    def generate_report(self) -> Dict[str, Any]:
        """Generate comprehensive report."""
        # Calculate overall readiness
        phase_scores = [r.readiness_pct for r in self.results.values()]
        overall_readiness = sum(phase_scores) // len(phase_scores) if phase_scores else 0

        infra_ok = sum(1 for s in self.infrastructure_status if s["status"] == "OK")
        infra_total = len(self.infrastructure_status)

        deps_ok = sum(1 for v in self.dependencies.values() if v)
        deps_total = len(self.dependencies)

        tests_ok = sum(1 for v in self.test_results.values() if v)
        tests_total = len(self.test_results)

        # Binance readiness
        binance_ready = (
            overall_readiness >= 80 and
            infra_ok >= infra_total - 1 and
            deps_ok >= deps_total - 2
        )

        report = {
            "timestamp": datetime.now().isoformat(),
            "overall_readiness_pct": overall_readiness,
            "binance_ready": binance_ready,
            "phases": {
                name: {
                    "readiness_pct": status.readiness_pct,
                    "status": status.status,
                    "modules": [
                        {
                            "path": m.path,
                            "completeness_pct": m.completeness_pct,
                            "issues": m.issues,
                            "features": m.features,
                        }
                        for m in status.modules
                    ],
                }
                for name, status in self.results.items()
            },
            "infrastructure": {
                "ok_count": infra_ok,
                "total": infra_total,
                "details": self.infrastructure_status,
            },
            "dependencies": {
                "ok_count": deps_ok,
                "total": deps_total,
                "details": self.dependencies,
            },
            "tests": {
                "ok_count": tests_ok,
                "total": tests_total,
                "details": self.test_results,
            },
        }

        return report

    def print_summary(self, report: Dict) -> None:
        """Print final summary."""
        print()
        print("=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print()

        # Overall score
        score = report["overall_readiness_pct"]
        if score >= 90:
            grade = "A"
            icon = "[GREEN]"
        elif score >= 75:
            grade = "B"
            icon = "[YELLOW]"
        elif score >= 50:
            grade = "C"
            icon = "[ORANGE]"
        else:
            grade = "D"
            icon = "[RED]"

        print(f"{icon} OVERALL READINESS: {score}% (Grade: {grade})")
        print()

        # Phase breakdown
        print("PHASE BREAKDOWN:")
        for phase_name, phase_data in report["phases"].items():
            status_icon = "[OK]" if phase_data["status"] == "READY" else ("[!]" if phase_data["status"] == "PARTIAL" else "[X]")
            print(f"  {status_icon} {phase_name}: {phase_data['readiness_pct']}%")

        print()

        # What's ready
        print("[OK] READY FOR BINANCE:")
        for item in report["infrastructure"]["details"]:
            if item["status"] == "OK":
                print(f"   • {item['description']} ({item['class']})")

        print()

        # What's needed
        print("[X] STILL NEEDED:")
        for phase_name, phase_data in report["phases"].items():
            if phase_data["status"] != "READY":
                for mod in phase_data["modules"]:
                    if mod["completeness_pct"] < 90:
                        print(f"   • {mod['path']}: {mod['completeness_pct']}%")
                        for issue in mod["issues"][:2]:
                            print(f"     - {issue}")

        print()

        # Missing dependencies
        missing_deps = [pkg for pkg, ok in report["dependencies"]["details"].items() if not ok]
        if missing_deps:
            print("[PKG] MISSING DEPENDENCIES:")
            for pkg in missing_deps:
                print(f"   pip install {pkg}")

        print()

        # Binance readiness
        if report["binance_ready"]:
            print(">>> BINANCE STATUS: READY FOR TESTNET <<<")
        else:
            print("... BINANCE STATUS: NOT READY ...")
            print("   Complete the missing modules above before proceeding.")

        print()
        print("=" * 60)


def main():
    """Main entry point."""
    diag = HopeDiagnostic()
    report = diag.run_full_diagnostic()
    diag.print_summary(report)

    # Save report
    report_path = ROOT / "state" / "diagnostic_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"Report saved: {report_path}")


if __name__ == "__main__":
    main()
