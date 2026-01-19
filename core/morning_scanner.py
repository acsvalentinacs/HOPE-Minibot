"""
HOPE/NORE Morning Scanner Module v2.0

Daily 10:00 AM routine:
1. Scan all .py files in minibot project
2. Analyze: syntax, imports, security, fail-closed compliance
3. Check LOGIC: trading patterns, risk management, order flow
4. Check AI: ML model usage, prediction logic, data pipelines
5. Check SECURITY: hardcoded secrets, injection risks, auth issues
6. Archive obsolete files (never delete)
7. Generate report

Usage:
    from core.morning_scanner import MorningScanner

    scanner = MorningScanner()
    report = scanner.run_full_scan()
    print(report.summary())
"""
from __future__ import annotations

import ast
import datetime
import hashlib
import json
import logging
import os
import py_compile
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Project paths
PROJECT_ROOT = Path(r'C:\Users\kirillDev\Desktop\TradingBot\minibot')
ARCHIVE_ROOT = Path(r'C:\Users\kirillDev\Desktop\TradingBot\Старые файлы от проекта НОРЕ 2025-11-23')

# Scan schedule
MORNING_SCAN_HOUR = 10
MORNING_SCAN_MINUTE = 0

# Security patterns to detect
SECRET_PATTERNS = [
    r'api[_-]?key\s*=\s*["\'][^"\']{10,}["\']',
    r'api[_-]?secret\s*=\s*["\'][^"\']{10,}["\']',
    r'password\s*=\s*["\'][^"\']+["\']',
    r'token\s*=\s*["\'][^"\']{20,}["\']',
    r'private[_-]?key\s*=\s*["\']',
    r'secret[_-]?key\s*=\s*["\']',
    r'["\'][a-zA-Z0-9]{32,}["\']',  # Long hex/base64 strings
]

# Trading logic patterns to check
TRADING_PATTERNS = {
    'risk_check': r'(risk|max_loss|stop_loss|position_size)',
    'order_validation': r'(validate|check).*(order|trade)',
    'balance_check': r'(balance|equity|margin).*check',
}

# AI/ML patterns to check
AI_PATTERNS = {
    'model_load': r'(load_model|joblib\.load|pickle\.load|torch\.load)',
    'prediction': r'(predict|inference|forward)',
    'training': r'(fit|train|backward)',
}


@dataclass
class FileAnalysis:
    """Analysis result for a single file."""
    path: Path
    status: str  # OK, WARNING, ERROR, OBSOLETE
    syntax_valid: bool
    imports_valid: bool
    has_silent_except: bool
    has_type_hints: bool
    # New fields for extended analysis
    security_issues: List[str] = field(default_factory=list)
    logic_issues: List[str] = field(default_factory=list)
    ai_issues: List[str] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    file_hash: str = ""
    last_modified: Optional[datetime.datetime] = None
    lines_count: int = 0


@dataclass
class ScanReport:
    """Full scan report with extended analysis."""
    scan_time: datetime.datetime
    total_files: int
    ok_count: int
    warning_count: int
    error_count: int
    obsolete_count: int
    archived_files: List[Path]
    analyses: List[FileAnalysis]
    # Extended metrics
    security_issues_count: int = 0
    logic_issues_count: int = 0
    ai_issues_count: int = 0

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            "=" * 70,
            "HOPE/NORE MORNING SCAN REPORT v2.0",
            f"Time: {self.scan_time.strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 70,
            "",
            "=== FILE STATUS ===",
            f"Total files scanned: {self.total_files}",
            f"  OK:       {self.ok_count}",
            f"  WARNING:  {self.warning_count}",
            f"  ERROR:    {self.error_count}",
            f"  OBSOLETE: {self.obsolete_count}",
            "",
            "=== EXTENDED ANALYSIS ===",
            f"  SECURITY issues: {self.security_issues_count}",
            f"  LOGIC issues:    {self.logic_issues_count}",
            f"  AI/ML issues:    {self.ai_issues_count}",
            "",
        ]

        if self.archived_files:
            lines.append(f"Archived {len(self.archived_files)} obsolete files:")
            for f in self.archived_files:
                lines.append(f"  -> {f.name}")
            lines.append("")

        # Security issues (CRITICAL)
        security_files = [a for a in self.analyses if a.security_issues]
        if security_files:
            lines.append("!!! SECURITY ISSUES (CRITICAL) !!!")
            for a in security_files:
                try:
                    rel_path = a.path.relative_to(PROJECT_ROOT)
                except ValueError:
                    rel_path = a.path
                lines.append(f"  {rel_path}")
                for issue in a.security_issues:
                    lines.append(f"    [SEC] {issue}")
            lines.append("")

        # Logic issues
        logic_files = [a for a in self.analyses if a.logic_issues]
        if logic_files:
            lines.append("=== LOGIC ISSUES ===")
            for a in logic_files:
                try:
                    rel_path = a.path.relative_to(PROJECT_ROOT)
                except ValueError:
                    rel_path = a.path
                lines.append(f"  {rel_path}")
                for issue in a.logic_issues:
                    lines.append(f"    [LOGIC] {issue}")
            lines.append("")

        # AI issues
        ai_files = [a for a in self.analyses if a.ai_issues]
        if ai_files:
            lines.append("=== AI/ML ISSUES ===")
            for a in ai_files:
                try:
                    rel_path = a.path.relative_to(PROJECT_ROOT)
                except ValueError:
                    rel_path = a.path
                lines.append(f"  {rel_path}")
                for issue in a.ai_issues:
                    lines.append(f"    [AI] {issue}")
            lines.append("")

        # Show errors
        errors = [a for a in self.analyses if a.status == "ERROR"]
        if errors:
            lines.append("=== ERRORS (require immediate attention) ===")
            for a in errors:
                try:
                    rel_path = a.path.relative_to(PROJECT_ROOT)
                except ValueError:
                    rel_path = a.path
                lines.append(f"  {rel_path}")
                for issue in a.issues:
                    lines.append(f"    - {issue}")
            lines.append("")

        # Show warnings
        warnings = [a for a in self.analyses if a.status == "WARNING"]
        if warnings:
            lines.append("=== WARNINGS ===")
            for a in warnings:
                try:
                    rel_path = a.path.relative_to(PROJECT_ROOT)
                except ValueError:
                    rel_path = a.path
                lines.append(f"  {rel_path}")
                for issue in a.issues:
                    lines.append(f"    - {issue}")
            lines.append("")

        # Recommendations
        all_recommendations = []
        for a in self.analyses:
            for rec in a.recommendations:
                if rec not in all_recommendations:
                    all_recommendations.append(rec)

        if all_recommendations:
            lines.append("=== RECOMMENDATIONS ===")
            for rec in all_recommendations:
                lines.append(f"  * {rec}")

        lines.append("=" * 70)
        return "\n".join(lines)

    def to_json(self) -> str:
        """Export report as JSON."""
        data = {
            "scan_time": self.scan_time.isoformat(),
            "version": "2.0",
            "total_files": self.total_files,
            "ok_count": self.ok_count,
            "warning_count": self.warning_count,
            "error_count": self.error_count,
            "obsolete_count": self.obsolete_count,
            "security_issues_count": self.security_issues_count,
            "logic_issues_count": self.logic_issues_count,
            "ai_issues_count": self.ai_issues_count,
            "archived_files": [str(f) for f in self.archived_files],
            "analyses": [
                {
                    "path": str(a.path),
                    "status": a.status,
                    "syntax_valid": a.syntax_valid,
                    "imports_valid": a.imports_valid,
                    "has_silent_except": a.has_silent_except,
                    "has_type_hints": a.has_type_hints,
                    "security_issues": a.security_issues,
                    "logic_issues": a.logic_issues,
                    "ai_issues": a.ai_issues,
                    "issues": a.issues,
                    "recommendations": a.recommendations,
                    "file_hash": a.file_hash,
                    "lines_count": a.lines_count,
                }
                for a in self.analyses
            ],
        }
        return json.dumps(data, indent=2, ensure_ascii=False)


class MorningScanner:
    """
    Daily morning scanner for HOPE/NORE project v2.0.

    Checks:
    - Syntax and imports
    - Silent except blocks (fail-closed violation)
    - SECURITY: hardcoded secrets, injection risks
    - LOGIC: trading patterns, risk management
    - AI: ML model usage, data pipelines

    Fail-closed: Any scan error = logged, not ignored.
    """

    def __init__(
        self,
        project_root: Optional[Path] = None,
        archive_root: Optional[Path] = None,
    ):
        self.project_root = project_root or PROJECT_ROOT
        self.archive_root = archive_root or ARCHIVE_ROOT
        self._last_scan: Optional[ScanReport] = None

    def run_full_scan(self) -> ScanReport:
        """Execute full morning scan with extended analysis."""
        scan_time = datetime.datetime.now()
        analyses: List[FileAnalysis] = []
        archived_files: List[Path] = []

        # Find all Python files
        py_files = list(self.project_root.rglob("*.py"))

        for file_path in py_files:
            # Skip __pycache__ and venv
            if "__pycache__" in str(file_path) or "venv" in str(file_path):
                continue

            analysis = self._analyze_file(file_path)
            analyses.append(analysis)

            # Archive obsolete files
            if analysis.status == "OBSOLETE":
                archived_path = self._archive_file(file_path)
                if archived_path:
                    archived_files.append(archived_path)

        # Count by status
        ok_count = sum(1 for a in analyses if a.status == "OK")
        warning_count = sum(1 for a in analyses if a.status == "WARNING")
        error_count = sum(1 for a in analyses if a.status == "ERROR")
        obsolete_count = sum(1 for a in analyses if a.status == "OBSOLETE")

        # Count extended issues
        security_issues_count = sum(len(a.security_issues) for a in analyses)
        logic_issues_count = sum(len(a.logic_issues) for a in analyses)
        ai_issues_count = sum(len(a.ai_issues) for a in analyses)

        report = ScanReport(
            scan_time=scan_time,
            total_files=len(analyses),
            ok_count=ok_count,
            warning_count=warning_count,
            error_count=error_count,
            obsolete_count=obsolete_count,
            archived_files=archived_files,
            analyses=analyses,
            security_issues_count=security_issues_count,
            logic_issues_count=logic_issues_count,
            ai_issues_count=ai_issues_count,
        )

        self._last_scan = report
        self._save_report(report)

        return report

    def _analyze_file(self, file_path: Path) -> FileAnalysis:
        """Analyze a single Python file with extended checks."""
        issues: List[str] = []
        recommendations: List[str] = []
        security_issues: List[str] = []
        logic_issues: List[str] = []
        ai_issues: List[str] = []

        # Read file content
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = file_path.read_text(encoding="cp1251")
            except Exception as e:
                return FileAnalysis(
                    path=file_path,
                    status="ERROR",
                    syntax_valid=False,
                    imports_valid=False,
                    has_silent_except=False,
                    has_type_hints=False,
                    issues=[f"Cannot read file: {e}"],
                )
        except Exception as e:
            return FileAnalysis(
                path=file_path,
                status="ERROR",
                syntax_valid=False,
                imports_valid=False,
                has_silent_except=False,
                has_type_hints=False,
                issues=[f"Cannot read file: {e}"],
            )

        # Calculate hash
        file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        lines_count = len(content.splitlines())

        # Get modification time
        try:
            mtime = datetime.datetime.fromtimestamp(file_path.stat().st_mtime)
        except OSError as e:
            logger.warning("Cannot get mtime for %s: %s", file_path, e)
            mtime = None

        # Check syntax
        syntax_valid = self._check_syntax(file_path)
        if not syntax_valid:
            issues.append("Syntax error - file cannot be compiled")

        # Parse AST for deeper analysis
        imports_valid = True
        has_silent_except = False
        has_type_hints = False

        try:
            tree = ast.parse(content)

            # Check for silent except blocks
            silent_excepts = self._find_silent_excepts(tree)
            if silent_excepts:
                has_silent_except = True
                issues.append(f"Silent except blocks at lines: {silent_excepts}")
                recommendations.append("Replace bare 'except:' with specific exceptions and logging")

            # Check for type hints
            has_type_hints = self._has_type_hints(tree)
            if not has_type_hints and lines_count > 50:
                recommendations.append("Consider adding type hints for better maintainability")

            # Check imports
            import_issues = self._check_imports(tree, file_path)
            if import_issues:
                imports_valid = False
                issues.extend(import_issues)

        except SyntaxError:
            # Already handled above
            pass

        # === SECURITY CHECKS ===
        security_issues = self._check_security(content, file_path)
        if security_issues:
            recommendations.append("CRITICAL: Remove hardcoded secrets, use environment variables")

        # === LOGIC CHECKS ===
        logic_issues = self._check_logic(content, file_path)

        # === AI CHECKS ===
        ai_issues = self._check_ai(content, file_path)

        # Determine status
        if not syntax_valid:
            status = "ERROR"
        elif security_issues:
            status = "ERROR"  # Security issues are critical
        elif has_silent_except or not imports_valid or logic_issues:
            status = "WARNING"
        elif self._is_obsolete(file_path, content):
            status = "OBSOLETE"
        else:
            status = "OK"

        return FileAnalysis(
            path=file_path,
            status=status,
            syntax_valid=syntax_valid,
            imports_valid=imports_valid,
            has_silent_except=has_silent_except,
            has_type_hints=has_type_hints,
            security_issues=security_issues,
            logic_issues=logic_issues,
            ai_issues=ai_issues,
            issues=issues,
            recommendations=recommendations,
            file_hash=file_hash,
            last_modified=mtime,
            lines_count=lines_count,
        )

    def _check_security(self, content: str, file_path: Path) -> List[str]:
        """Check for security issues: hardcoded secrets, etc."""
        issues = []

        # Skip test files and templates
        if "test" in str(file_path).lower() or "template" in str(file_path).lower():
            return []

        for pattern in SECRET_PATTERNS:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                # Filter out false positives (env vars, config references)
                for match in matches:
                    if "os.getenv" not in match and "environ" not in match:
                        if "your_" not in match.lower() and "placeholder" not in match.lower():
                            issues.append(f"Potential hardcoded secret: {match[:50]}...")
                            break  # One per pattern is enough

        # Check for SQL injection patterns
        if re.search(r'execute\s*\(\s*[f"\'].*\{', content):
            issues.append("Potential SQL injection: f-string in execute()")

        # Check for command injection
        if re.search(r'subprocess\.(call|run|Popen)\s*\([^)]*shell\s*=\s*True', content):
            if re.search(r'subprocess\.(call|run|Popen)\s*\(\s*f["\']', content):
                issues.append("Potential command injection: f-string with shell=True")

        return issues

    def _check_logic(self, content: str, file_path: Path) -> List[str]:
        """Check for trading logic issues."""
        issues = []

        # Only check trading-related files
        trading_keywords = ['trade', 'order', 'position', 'exchange', 'binance', 'strategy']
        is_trading_file = any(kw in str(file_path).lower() for kw in trading_keywords)

        if not is_trading_file:
            return []

        # Check for missing risk checks
        has_order_code = re.search(r'(place_order|create_order|submit_order)', content)
        has_risk_check = re.search(TRADING_PATTERNS['risk_check'], content, re.IGNORECASE)

        if has_order_code and not has_risk_check:
            issues.append("Order placement without visible risk check")

        # Check for missing balance validation
        if has_order_code and not re.search(r'balance|equity|margin', content, re.IGNORECASE):
            issues.append("Order placement without balance validation")

        # Check for hardcoded position sizes
        if re.search(r'(quantity|size|amount)\s*=\s*\d+(\.\d+)?[^*/%]', content):
            issues.append("Hardcoded position size detected")

        # Check for missing error handling in API calls
        if re.search(r'client\.(get|post|place|create)', content):
            if not re.search(r'try:.*client\.(get|post|place|create)', content, re.DOTALL):
                issues.append("API call without try/except wrapper")

        return issues

    def _check_ai(self, content: str, file_path: Path) -> List[str]:
        """Check for AI/ML related issues."""
        issues = []

        # Check for model usage
        has_model = re.search(AI_PATTERNS['model_load'], content)
        has_prediction = re.search(AI_PATTERNS['prediction'], content)

        if has_model or has_prediction:
            # Check for missing input validation
            if not re.search(r'(validate|check|assert).*input', content, re.IGNORECASE):
                issues.append("ML model usage without input validation")

            # Check for missing error handling on prediction
            if has_prediction and not re.search(r'try:.*predict', content, re.DOTALL):
                issues.append("Prediction call without error handling")

            # Check for missing model version tracking
            if has_model and not re.search(r'(version|model_id|checkpoint)', content, re.IGNORECASE):
                issues.append("Model loading without version tracking")

        return issues

    def _check_syntax(self, file_path: Path) -> bool:
        """Check if file has valid Python syntax."""
        try:
            py_compile.compile(str(file_path), doraise=True)
            return True
        except py_compile.PyCompileError:
            return False

    def _find_silent_excepts(self, tree: ast.AST) -> List[int]:
        """Find bare except blocks (fail-closed violation)."""
        silent_lines = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                # Bare except: or except Exception with just pass/...
                if node.type is None:
                    silent_lines.append(node.lineno)
                elif len(node.body) == 1:
                    body = node.body[0]
                    if isinstance(body, ast.Pass):
                        silent_lines.append(node.lineno)
                    elif isinstance(body, ast.Expr) and isinstance(body.value, ast.Constant):
                        if body.value.value is ...:
                            silent_lines.append(node.lineno)

        return silent_lines

    def _has_type_hints(self, tree: ast.AST) -> bool:
        """Check if file uses type hints."""
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                if node.returns is not None:
                    return True
                for arg in node.args.args:
                    if arg.annotation is not None:
                        return True
            if isinstance(node, ast.AnnAssign):
                return True
        return False

    def _check_imports(self, tree: ast.AST, file_path: Path) -> List[str]:
        """Check for problematic imports."""
        issues = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("_"):
                        issues.append(f"Private module import: {alias.name}")

            if isinstance(node, ast.ImportFrom):
                if node.module and "test" in node.module.lower():
                    # Test imports in non-test files
                    if "test" not in str(file_path).lower():
                        issues.append(f"Test module imported in non-test file: {node.module}")

        return issues

    def _is_obsolete(self, file_path: Path, content: str) -> bool:
        """Check if file is obsolete."""
        content_lower = content.lower()

        # Explicit markers
        if "# deprecated" in content_lower or "# obsolete" in content_lower:
            return True

        # Old file with pending work
        try:
            mtime = datetime.datetime.fromtimestamp(file_path.stat().st_mtime)
            age_days = (datetime.datetime.now() - mtime).days
            if age_days > 90 and ("todo" in content_lower or "fixme" in content_lower):
                return True
        except OSError as e:
            logger.warning("Cannot check obsolete status for %s: %s", file_path, e)

        return False

    def _archive_file(self, file_path: Path) -> Optional[Path]:
        """Move obsolete file to archive. NEVER deletes - only moves."""
        try:
            self.archive_root.mkdir(parents=True, exist_ok=True)

            rel_path = file_path.relative_to(self.project_root)
            archive_path = self.archive_root / rel_path
            archive_path.parent.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            final_path = archive_path.with_name(
                f"{archive_path.stem}_{timestamp}{archive_path.suffix}"
            )

            shutil.move(str(file_path), str(final_path))
            logger.info("Archived %s -> %s", file_path, final_path)

            return final_path

        except Exception as e:
            logger.error("Could not archive %s: %s", file_path, e)
            return None

    def _save_report(self, report: ScanReport) -> None:
        """Save report to file."""
        reports_dir = self.project_root / "reports"
        reports_dir.mkdir(exist_ok=True)

        timestamp = report.scan_time.strftime("%Y%m%d_%H%M%S")

        json_path = reports_dir / f"morning_scan_{timestamp}.json"
        json_path.write_text(report.to_json(), encoding="utf-8")

        txt_path = reports_dir / f"morning_scan_{timestamp}.txt"
        txt_path.write_text(report.summary(), encoding="utf-8")

    def trigger_morning_scan(self) -> ScanReport:
        """Trigger scan immediately (for button press)."""
        print(f"[{datetime.datetime.now()}] Morning scan v2.0 triggered...")
        report = self.run_full_scan()
        print(report.summary())
        return report

    def should_run_scheduled(self) -> bool:
        """Check if scheduled scan should run now."""
        now = datetime.datetime.now()
        return now.hour == MORNING_SCAN_HOUR and now.minute == MORNING_SCAN_MINUTE

    def get_last_report(self) -> Optional[ScanReport]:
        """Get last scan report."""
        return self._last_scan


# Singleton instance for panel button
_scanner_instance: Optional[MorningScanner] = None


def get_scanner() -> MorningScanner:
    """Get or create scanner singleton."""
    global _scanner_instance
    if _scanner_instance is None:
        _scanner_instance = MorningScanner()
    return _scanner_instance


def morning_button_handler() -> str:
    """Handler for Morning button on panel."""
    scanner = get_scanner()
    report = scanner.trigger_morning_scan()
    return report.summary()


if __name__ == "__main__":
    scanner = MorningScanner()
    report = scanner.run_full_scan()
    print(report.summary())
