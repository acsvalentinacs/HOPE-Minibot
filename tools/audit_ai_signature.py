# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-22 19:50:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 20:00:00 UTC
# === END SIGNATURE ===
"""
AI Signature Audit - Verify Python files have proper AI signatures.

Modes (in order of preference):
1. Git-diff mode: Check only files changed in git diff (fail-closed)
   python tools/audit_ai_signature.py --root . --git-diff
   python tools/audit_ai_signature.py --root . --git-diff --staged

2. Paths mode: Check specific files (fail-closed)
   python tools/audit_ai_signature.py --root . --paths core/foo.py scripts/bar.py

3. Scope-file mode: Check only files listed in scope file (fail-closed)
   python tools/audit_ai_signature.py --root . --scope-file tools/ai_signature_scope.txt

4. Directory mode: Check all files in core/scripts/tools
   python tools/audit_ai_signature.py --root . --fail-on-missing

Fail-closed: exits with code 1 if any audited file is missing signature.
Legacy files not touched = not checked (git-diff mode solves "Ошибка №1").
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional


AI_SIGNATURE_START = "# === AI SIGNATURE ==="
AI_SIGNATURE_END = "# === END SIGNATURE ==="

REQUIRED_PREFIXES = ("core", "scripts", "tools")
EXCLUDED_DIRS = {
    ".git", ".venv", "__pycache__", ".mypy_cache",
    ".ruff_cache", ".pytest_cache", ".tox", "node_modules"
}

UTC_PATTERN = re.compile(r"Created at:\s*\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC")
ISO_PATTERN = re.compile(r"Created at:\s*\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


class GitAuditError(RuntimeError):
    """Raised when git operations fail in audit mode (fail-closed)."""
    pass


class PathSecurityError(ValueError):
    """Raised when path validation fails (absolute path, traversal, outside root)."""
    pass


def _resolve_inside_root(root: Path, rel: str) -> Path:
    """
    Resolve a *relative* path inside root (FAIL-CLOSED helper).

    Forbids:
      - absolute paths
      - traversal outside root via .. or symlinks/junctions after resolve

    Raises:
        PathSecurityError: If path is invalid or outside root
    """
    rel = (rel or "").strip().replace("\\", "/")
    p = Path(rel)

    if not rel:
        raise PathSecurityError("empty_path")
    if p.is_absolute():
        raise PathSecurityError("absolute_path_not_allowed")

    root_resolved = root.resolve()
    resolved = (root_resolved / p).resolve()

    try:
        resolved.relative_to(root_resolved)
    except ValueError as e:
        raise PathSecurityError("path_outside_root") from e

    return resolved


@dataclass(frozen=True)
class Finding:
    """Audit finding for a single file."""
    path: Path
    ok: bool
    reason: str


def read_scope_file(scope_path: Path) -> List[str]:
    """Read scope file and return list of relative paths."""
    if not scope_path.exists():
        raise FileNotFoundError(f"Scope file not found: {scope_path}")

    lines = scope_path.read_text(encoding="utf-8").splitlines()
    paths = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        # Normalize path separators
        paths.append(s.replace("\\", "/"))
    return paths


def iter_scoped_files(root: Path, rel_paths: Iterable[str]) -> List[Path]:
    """
    Convert relative paths to absolute paths (FAIL-CLOSED: must be inside root).

    Raises:
        PathSecurityError: If any path is absolute or outside root
    """
    out: List[Path] = []
    for rel in rel_paths:
        out.append(_resolve_inside_root(root, rel))
    return out


def _assert_inside_git_repo(root: Path) -> None:
    """Verify we are inside a git repository. Raises GitAuditError if not."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=True,
        )
        if result.stdout.strip().lower() != "true":
            raise GitAuditError("not_inside_git_work_tree")
    except FileNotFoundError:
        raise GitAuditError("git_not_found_in_PATH")
    except subprocess.CalledProcessError as e:
        msg = (e.stderr or e.stdout or "").strip()
        raise GitAuditError(f"git_rev-parse_failed:{msg}")


def get_git_diff_files(root: Path, staged: bool = False) -> List[str]:
    """
    Get list of changed files from git diff (FAIL-CLOSED).

    Args:
        root: Project root (must be in git repo)
        staged: If True, check staged changes (--cached), else working tree

    Returns:
        List of relative paths (normalized to forward slashes)

    Raises:
        GitAuditError: If git is not available or command fails
    """
    # First verify we're in a git repo
    _assert_inside_git_repo(root)

    try:
        cmd = ["git", "diff", "--name-only"]
        if staged:
            cmd.append("--cached")

        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            check=True,
        )

        paths = []
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if line:
                # Normalize to forward slashes
                paths.append(line.replace("\\", "/"))
        return paths

    except subprocess.CalledProcessError as e:
        msg = (e.stderr or e.stdout or "").strip()
        raise GitAuditError(f"git_diff_failed:{msg}")
    except FileNotFoundError:
        raise GitAuditError("git_not_found_in_PATH")


def filter_auditable_files(paths: List[str]) -> List[str]:
    """
    Filter paths to only those that require AI signature audit.

    Keeps: .py files in core/, scripts/, tools/
    Keeps: .ps1 files ONLY in tools/ (not core/ or scripts/)
    Skips: everything else
    """
    auditable = []
    for p in paths:
        # Normalize
        p = p.replace("\\", "/")

        # Check if in required scope
        parts = p.split("/")
        if not parts:
            continue

        top_dir = parts[0]

        # .py files: allowed in core/, scripts/, tools/
        if p.endswith(".py"):
            if top_dir in REQUIRED_PREFIXES:
                auditable.append(p)
        # .ps1 files: ONLY in tools/ (strict)
        elif p.endswith(".ps1"):
            if top_dir == "tools":
                auditable.append(p)

    return auditable


def audit_paths(root: Path, rel_paths: List[str]) -> tuple[int, List[Finding]]:
    """
    Audit specific files by path (fail-closed).

    All specified files MUST have valid signatures.
    Paths MUST be relative and inside root.
    """
    findings: List[Finding] = []

    for rel in rel_paths:
        # Path security check (FAIL-CLOSED on absolute/traversal)
        try:
            path = _resolve_inside_root(root, rel)
        except PathSecurityError as e:
            findings.append(Finding(Path(rel), False, f"invalid_path:{e}"))
            continue

        if not path.exists():
            findings.append(Finding(path, False, "file_not_found"))
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except Exception as e:
            findings.append(Finding(path, False, f"read_error:{type(e).__name__}"))
            continue

        if signature_ok(text):
            findings.append(Finding(path, True, "signed"))
        else:
            findings.append(Finding(path, False, "missing_or_malformed_signature"))

    # FAIL-CLOSED: if findings is empty but paths were provided, something is wrong
    exit_code = 0 if (findings and all(f.ok for f in findings)) else 1
    return exit_code, findings


def iter_py_files(root: Path) -> List[Path]:
    """Iterate all .py files, excluding cache directories."""
    files = []
    for p in root.rglob("*.py"):
        if any(part in EXCLUDED_DIRS for part in p.parts):
            continue
        files.append(p)
    return sorted(files)


def is_in_required_scope(root: Path, path: Path) -> bool:
    """Check if file is in a directory that requires AI signature."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    if not rel.parts:
        return False
    return rel.parts[0] in REQUIRED_PREFIXES


def signature_ok(text: str) -> bool:
    """
    Strict AI signature verification (FAIL-CLOSED).

    Rules (LAW 2):
      - Only consider the signature block between START and END
      - START must appear before END (within first 80 lines)
      - 'Created by:' must be INSIDE the block
      - 'Created at:' must be INSIDE the block (UTC or ISO formats)
    """
    head_lines = text.splitlines()[:80]
    head = "\n".join(head_lines)

    start_pos = head.find(AI_SIGNATURE_START)
    end_pos = head.find(AI_SIGNATURE_END)

    # Both markers must exist and END must come after START
    if start_pos == -1 or end_pos == -1 or end_pos <= start_pos:
        return False

    # Extract the block content between START and END
    block = head[start_pos:end_pos]

    # Check required fields INSIDE the block only
    if "Created by:" not in block:
        return False
    if not (UTC_PATTERN.search(block) or ISO_PATTERN.search(block)):
        return False

    return True


def audit_scoped(root: Path, scope_file: Path) -> tuple[int, List[Finding]]:
    """
    Audit files listed in scope file (fail-closed).

    All files in scope MUST have valid signatures.
    """
    rel_paths = read_scope_file(scope_file)
    targets = iter_scoped_files(root, rel_paths)
    findings = []

    for path in targets:
        if not path.exists():
            findings.append(Finding(path, False, "file_not_found"))
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except Exception as e:
            findings.append(Finding(path, False, f"read_error:{type(e).__name__}"))
            continue

        if signature_ok(text):
            findings.append(Finding(path, True, "signed"))
        else:
            findings.append(Finding(path, False, "missing_or_malformed_signature"))

    exit_code = 0 if all(f.ok for f in findings) else 1
    return exit_code, findings


def audit_directory(root: Path, fail_on_missing: bool) -> tuple[int, List[Finding]]:
    """
    Audit all Python files in project.

    If fail_on_missing, files in core/scripts/tools must have signatures.
    """
    findings = []

    for path in iter_py_files(root):
        scope_required = is_in_required_scope(root, path)

        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except Exception as e:
            if scope_required and fail_on_missing:
                findings.append(Finding(path, False, f"read_error:{type(e).__name__}"))
            else:
                findings.append(Finding(path, True, "read_error_ignored"))
            continue

        ok = signature_ok(text)

        if scope_required and fail_on_missing and not ok:
            findings.append(Finding(path, False, "missing_or_malformed_signature"))
        else:
            reason = "signed" if ok else "unsigned_allowed"
            findings.append(Finding(path, True, reason))

    exit_code = 0 if all(f.ok for f in findings) else 1
    return exit_code, findings


def main() -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description="Audit AI signatures in Python files")
    ap.add_argument("--root", type=Path, required=True, help="Project root directory")

    # Mode selection (mutually preferred in this order)
    ap.add_argument("--git-diff", action="store_true",
                    help="Check only files changed in git diff (fail-closed)")
    ap.add_argument("--staged", action="store_true",
                    help="With --git-diff: check staged changes instead of working tree")
    ap.add_argument("--require-nonempty", action="store_true",
                    help="With --git-diff: FAIL if selection is empty (use in Release mode)")
    ap.add_argument("--paths", nargs="+", default=None,
                    help="Check specific files (fail-closed)")
    ap.add_argument("--scope-file", type=Path, default=None,
                    help="File listing paths to audit (fail-closed mode)")
    ns = ap.parse_args()

    root = ns.root.resolve()
    if not root.exists():
        print(f"FAIL-CLOSED: root not found: {root}")
        return 1

    # Determine mode (priority order)
    mode = "unknown"
    findings: List[Finding] = []
    exit_code = 0

    if ns.git_diff:
        # Git-diff mode: check changed files only (FAIL-CLOSED on git errors)
        mode = "git-diff" + ("-staged" if ns.staged else "")
        try:
            changed = get_git_diff_files(root, staged=ns.staged)
        except GitAuditError as e:
            print(f"FAIL-CLOSED: git error in audit mode: {e}", file=sys.stderr)
            return 1

        auditable = filter_auditable_files(changed)

        # LAW 2: Handle empty selection based on --require-nonempty flag
        if not auditable:
            # If --require-nonempty is set (Release mode), empty selection = FAIL
            if ns.require_nonempty:
                print(f"FAIL-CLOSED: empty_git_selection mode={mode} --require-nonempty", file=sys.stderr)
                print(f"  Changed files: {len(changed)}, auditable: 0", file=sys.stderr)
                return 1
            # Dev mode: allow SKIP (exit 0 with informational message)
            if not changed:
                print(f"AI_SIGNATURE_AUDIT mode={mode} files=0 (git diff is empty, no changes)")
                print("\nSKIP: No changes in git diff")
                return 0
            print(f"AI_SIGNATURE_AUDIT mode={mode} files=0 (git diff has {len(changed)} files, none auditable)")
            print("\nSKIP: No auditable files in git diff")
            return 0

        exit_code, findings = audit_paths(root, auditable)

    elif ns.paths:
        # Paths mode: check specific files (FAIL-CLOSED if 0 auditable)
        mode = "paths"
        auditable = filter_auditable_files(ns.paths)

        if not auditable:
            # FAIL-CLOSED: user explicitly passed --paths but none are auditable
            # This prevents "PASS by empty" loophole
            print(f"FAIL-CLOSED: --paths provided ({len(ns.paths)} paths) but 0 auditable after filter", file=sys.stderr)
            print(f"  Paths provided: {ns.paths[:5]}{'...' if len(ns.paths) > 5 else ''}", file=sys.stderr)
            print(f"  Auditable extensions: .py (core/scripts/tools), .ps1 (tools only)", file=sys.stderr)
            return 1

        exit_code, findings = audit_paths(root, auditable)

    elif ns.scope_file:
        # Scope-file mode (FAIL-CLOSED on empty/invalid)
        mode = "scoped"
        scope_path = ns.scope_file if ns.scope_file.is_absolute() else (root / ns.scope_file).resolve()
        try:
            rel_paths = read_scope_file(scope_path)
            # LAW 2: NO EMPTY PASS - empty scope file = FAIL
            if not rel_paths:
                print("FAIL-CLOSED: empty_scope (scope-file has 0 valid entries)", file=sys.stderr)
                return 1
            targets = iter_scoped_files(root, rel_paths)
            # This shouldn't happen if rel_paths is non-empty, but check anyway
            if not targets:
                print("FAIL-CLOSED: empty_scope (0 resolved targets)", file=sys.stderr)
                return 1
            # Reuse audit_paths by converting back to relative strings
            rels = [str(t.relative_to(root)).replace("\\", "/") for t in targets]
            exit_code, findings = audit_paths(root, rels)
        except FileNotFoundError as e:
            print(f"FAIL-CLOSED: {e}", file=sys.stderr)
            return 1
        except PathSecurityError as e:
            print(f"FAIL-CLOSED: invalid_scope_entry:{e}", file=sys.stderr)
            return 1

    else:
        # No mode selected - FAIL-CLOSED (explicit mode required)
        print("FAIL-CLOSED: no_mode_selected", file=sys.stderr)
        print("  Use one of: --git-diff, --paths <files>, --scope-file <path>", file=sys.stderr)
        return 1

    # Summary
    total = len(findings)
    bad = [f for f in findings if not f.ok]
    signed = len([f for f in findings if f.reason == "signed"])

    print(f"AI_SIGNATURE_AUDIT mode={mode} files={total} signed={signed} bad={len(bad)}")

    # Show bad files
    if bad:
        print("\nFailed files:")
        for f in bad[:50]:
            try:
                rel = f.path.relative_to(root)
            except ValueError:
                rel = f.path
            print(f"  BAD: {rel} :: {f.reason}")
        if len(bad) > 50:
            print(f"  ... and {len(bad) - 50} more")

    if exit_code == 0:
        print("\nPASS: AI signature audit OK")
    else:
        print("\nFAIL-CLOSED: AI signature audit failed")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
