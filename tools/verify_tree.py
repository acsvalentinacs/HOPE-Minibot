# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T20:00:00Z
# Modified by: Claude (opus-4)
# Modified at (UTC): 2026-01-25T21:00:00Z
# Purpose: Deterministic tree manifest generator v1.1 (fail-closed)
# === END SIGNATURE ===
"""
Verify Tree v1.1 - Deterministic Manifest Generator.

Produces a cryptographic proof pack of the minibot/** tree.
Used as evidence for release gates.

Output: state/health/tree_manifest.json

Schema: tree_manifest_v1
- schema_version: str
- ts_utc: ISO8601
- root: str (relative, "minibot")
- git_head: str (commit hash)
- cmdline_sha256: str (SSoT binding)
- run_id: str (deterministic)
- files[]: {path, size, sha256} (path normalized to POSIX /)
- metrics: {total_files, total_bytes, scan_time_ms}

Exit codes:
    0 = SUCCESS (manifest written)
    1 = FAIL (I/O error, hash error)
    2 = ERROR (config/setup issue)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Set, Optional

# SSoT paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Exclusion patterns (glob-style)
EXCLUDE_DIRS: Set[str] = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
}

EXCLUDE_PATHS: Set[str] = {
    "staging/backup",
    "staging/history",
}

# Binary artifacts (hash only, no content scan)
ARTIFACT_EXTENSIONS: Set[str] = {
    ".zip", ".7z", ".exe", ".dll", ".pyd", ".whl",
    ".bin", ".pdf", ".png", ".jpg", ".jpeg", ".webp",
    ".ico", ".gif", ".bmp", ".tiff", ".pyc", ".pyo",
}


def compute_file_sha256(filepath: Path) -> str:
    """
    Compute SHA256 of file contents.

    Args:
        filepath: Path to file

    Returns:
        Hex digest string

    Raises:
        OSError: On read failure (fail-closed)
    """
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def get_cmdline_sha256() -> str:
    """Get command line SHA256 from SSoT module."""
    try:
        from core.ssot.cmdline import get_cmdline_sha256 as ssot_cmdline
        return ssot_cmdline()
    except ImportError:
        # Fallback: hash sys.argv
        cmdline = " ".join(sys.argv)
        return hashlib.sha256(cmdline.encode("utf-8")).hexdigest()


def get_git_head(root: Path) -> Optional[str]:
    """Get current git HEAD commit hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def should_exclude(rel_path: Path) -> bool:
    """
    Check if path should be excluded from manifest.

    Args:
        rel_path: Path relative to project root

    Returns:
        True if should be excluded
    """
    parts = rel_path.parts

    # Check directory exclusions
    for part in parts[:-1]:  # All but filename
        if part in EXCLUDE_DIRS:
            return True

    # Check path prefix exclusions
    rel_str = str(rel_path).replace("\\", "/")
    for excl in EXCLUDE_PATHS:
        if rel_str.startswith(excl):
            return True

    return False


def is_artifact(filepath: Path) -> bool:
    """Check if file is a binary artifact."""
    return filepath.suffix.lower() in ARTIFACT_EXTENSIONS


def scan_tree(root: Path) -> List[Dict[str, Any]]:
    """
    Scan directory tree and collect file metadata.

    Args:
        root: Root directory to scan

    Returns:
        List of file entries sorted by rel_path

    Raises:
        OSError: On I/O failure (fail-closed)
    """
    files = []
    errors = []

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune excluded directories
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]

        for filename in filenames:
            filepath = Path(dirpath) / filename

            try:
                rel_path = filepath.relative_to(root)
            except ValueError:
                continue

            # Check exclusions
            if should_exclude(rel_path):
                continue

            try:
                stat = filepath.stat()

                # Compute SHA256
                file_hash = compute_file_sha256(filepath)

                # Normalize path (forward slashes)
                rel_str = str(rel_path).replace("\\", "/")

                # Get mtime as UTC ISO8601
                mtime_utc = datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat()

                files.append({
                    "rel_path": rel_str,
                    "size": stat.st_size,
                    "mtime_utc": mtime_utc,
                    "sha256": file_hash,
                })

            except OSError as e:
                errors.append(f"{rel_path}: {e}")

    if errors:
        # Fail-closed: any error = FAIL
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        raise OSError(f"Failed to process {len(errors)} files")

    # Sort by rel_path for determinism
    files.sort(key=lambda x: x["rel_path"])

    return files


def generate_manifest(root: Path) -> Dict[str, Any]:
    """
    Generate tree manifest.

    Args:
        root: Root directory to scan

    Returns:
        Manifest dict

    Raises:
        OSError: On failure (fail-closed)
    """
    ts_start = time.perf_counter()
    ts_utc = datetime.now(timezone.utc).isoformat()
    cmdline_sha256 = get_cmdline_sha256()
    git_head = get_git_head(root)

    # Scan tree
    files = scan_tree(root)

    # Convert to schema format (path instead of rel_path, no mtime)
    file_entries = [
        {"path": f["rel_path"], "size": f["size"], "sha256": f["sha256"]}
        for f in files
    ]

    # Compute totals
    total_files = len(files)
    total_bytes = sum(f["size"] for f in files)

    ts_end = time.perf_counter()
    scan_time_ms = int((ts_end - ts_start) * 1000)

    # Generate deterministic run_id
    run_id = f"tree_v1__ts={ts_utc[:19].replace('-', '').replace(':', '').replace('T', 'T')}Z__files={total_files}__cmd={cmdline_sha256[:8]}"

    manifest = {
        "schema_version": "tree_manifest_v1",
        "ts_utc": ts_utc,
        "root": root.name,  # Just "minibot", not full path
        "git_head": git_head or "unknown",
        "cmdline_sha256": cmdline_sha256,
        "run_id": run_id,
        "files": file_entries,
        "metrics": {
            "total_files": total_files,
            "total_bytes": total_bytes,
            "scan_time_ms": scan_time_ms,
        },
    }

    return manifest


def atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    """
    Write JSON atomically (temp -> fsync -> replace).

    Args:
        path: Target path
        data: Data to write

    Raises:
        OSError: On write failure
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = path.with_suffix(path.suffix + ".tmp")

    try:
        content = json.dumps(data, indent=2, ensure_ascii=False)

        with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())

        # Atomic replace
        os.replace(tmp_path, path)

    except Exception:
        # Cleanup on failure
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def main() -> int:
    """Main entrypoint."""
    parser = argparse.ArgumentParser(
        description="Verify Tree - Deterministic manifest generator (fail-closed)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=PROJECT_ROOT,
        help=f"Root directory to scan (default: {PROJECT_ROOT})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "state" / "health" / "tree_manifest.json",
        help="Output path for manifest",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON to stdout instead of file",
    )

    args = parser.parse_args()

    root = args.root.resolve()

    if not root.exists():
        print(f"ERROR: Root directory not found: {root}", file=sys.stderr)
        return 2

    if not root.is_dir():
        print(f"ERROR: Root is not a directory: {root}", file=sys.stderr)
        return 2

    print(f"Scanning: {root}")

    try:
        manifest = generate_manifest(root)
    except OSError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1

    print(f"Files: {manifest['metrics']['total_files']}")
    print(f"Bytes: {manifest['metrics']['total_bytes']:,}")
    print(f"Scan time: {manifest['metrics']['scan_time_ms']}ms")
    print(f"Git HEAD: {manifest['git_head'][:12]}...")

    if args.json:
        print(json.dumps(manifest, indent=2))
        return 0

    # Write manifest
    out_path = args.out

    try:
        atomic_write_json(out_path, manifest)
        print(f"Manifest: {out_path}")
        print("PASS")
        return 0
    except OSError as e:
        print(f"FAIL: Write error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
