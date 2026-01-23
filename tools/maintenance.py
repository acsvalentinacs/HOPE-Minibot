# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 14:00:00 UTC
# === END SIGNATURE ===
"""
tools/maintenance.py - State Maintenance CLI.

Unified maintenance operations for HOPE state files:
- archive: Move rotated files to archive (NO DELETE)
- compress: Compress archived files (NOT primary logs)
- verify: Verify JSONL integrity, quarantine corrupted lines
- report: Generate state summary report
- purge-archive: (optional) Delete old archives WITH manifest

PROTOCOL:
    1. NO DELETION by default - only archive/compress
    2. Every destructive action writes audit manifest FIRST
    3. Primary logs (state/*.jsonl) are NEVER compressed in-place
    4. Archive directory is the ONLY place for compression

USAGE:
    python tools/maintenance.py archive
    python tools/maintenance.py compress
    python tools/maintenance.py verify
    python tools/maintenance.py report
    python tools/maintenance.py purge-archive --older-than 180d --i-know-what-im-doing

REQUIREMENTS:
    pip install rich  # Optional, for pretty output
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add parent to path for imports
_THIS_FILE = Path(__file__).resolve()
_TOOLS_DIR = _THIS_FILE.parent
_MINIBOT_DIR = _TOOLS_DIR.parent
if str(_MINIBOT_DIR) not in sys.path:
    sys.path.insert(0, str(_MINIBOT_DIR))

from core.state_layout import get_layout, StateLayout
from core.audit import emit_maintenance_audit, emit_audit
from core.jsonl_sha import read_and_verify
from core.schemas.registry import build_quarantine_event

# Optional: rich for pretty output
try:
    from rich.console import Console
    from rich.table import Table
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


def _sha256_file(path: Path) -> str:
    """Compute SHA256 of file contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_age_days(path: Path) -> float:
    """Get file age in days."""
    mtime = path.stat().st_mtime
    age_sec = time.time() - mtime
    return age_sec / (24 * 3600)


def _find_rotated_files(layout: StateLayout) -> List[Path]:
    """Find all rotated JSONL files (*.jsonl.N) in state directory."""
    state_dir = layout.state_dir()
    rotated = []
    for f in state_dir.glob("*.jsonl.*"):
        # Check if suffix is a number
        suffix = f.suffix.lstrip(".")
        if suffix.isdigit():
            rotated.append(f)
    return rotated


def _find_archived_files(layout: StateLayout) -> List[Path]:
    """Find all files in archive directory."""
    archive_dir = layout.archive_dir()
    if not archive_dir.exists():
        return []
    return list(archive_dir.rglob("*.*"))


def _find_primary_logs(layout: StateLayout) -> List[Path]:
    """Find all primary JSONL logs (not rotated)."""
    state_dir = layout.state_dir()
    primary = []
    for f in state_dir.glob("*.jsonl"):
        # Skip rotated files (*.jsonl.N)
        if not f.suffix.lstrip(".").isdigit():
            primary.append(f)
    return primary


# === ARCHIVE COMMAND ===

def cmd_archive(layout: StateLayout, dry_run: bool = False) -> Dict[str, Any]:
    """
    Move rotated files to archive directory.

    NO DELETION - only moves files.
    """
    result = {
        "command": "archive",
        "files_moved": [],
        "errors": [],
        "dry_run": dry_run,
    }

    rotated = _find_rotated_files(layout)

    if not rotated:
        print("No rotated files to archive.")
        return result

    print(f"Found {len(rotated)} rotated file(s) to archive.")

    for src in rotated:
        # Determine component from filename
        component = src.stem.replace("_history", "").replace("_audit", "")
        if not component:
            component = "unknown"

        # Destination path
        dest = layout.archived_file(component, src.name)

        if dry_run:
            print(f"  [DRY-RUN] Would move: {src.name} -> {dest}")
            result["files_moved"].append({"src": str(src), "dest": str(dest), "dry_run": True})
            continue

        try:
            # Ensure dest directory exists
            dest.parent.mkdir(parents=True, exist_ok=True)

            # Move file
            shutil.move(str(src), str(dest))

            print(f"  Moved: {src.name} -> {dest}")
            result["files_moved"].append({"src": str(src), "dest": str(dest)})

        except Exception as e:
            print(f"  ERROR: Failed to move {src.name}: {e}")
            result["errors"].append({"file": str(src), "error": str(e)})

    # Emit audit event
    if result["files_moved"] and not dry_run:
        emit_maintenance_audit(
            "archive",
            [f["src"] for f in result["files_moved"]],
            details={"destinations": [f["dest"] for f in result["files_moved"]]},
        )

    return result


# === COMPRESS COMMAND ===

def cmd_compress(layout: StateLayout, dry_run: bool = False) -> Dict[str, Any]:
    """
    Compress archived files (gzip).

    ONLY operates on archive directory - NEVER on primary logs.
    """
    result = {
        "command": "compress",
        "files_compressed": [],
        "files_skipped": [],
        "errors": [],
        "dry_run": dry_run,
    }

    archived = _find_archived_files(layout)

    if not archived:
        print("No archived files to compress.")
        return result

    # Filter: only compress uncompressed files
    to_compress = [f for f in archived if not f.suffix in (".gz", ".zip", ".bz2")]

    if not to_compress:
        print("All archived files are already compressed.")
        return result

    print(f"Found {len(to_compress)} file(s) to compress.")

    for src in to_compress:
        dest = src.with_suffix(src.suffix + ".gz")

        if dry_run:
            print(f"  [DRY-RUN] Would compress: {src.name} -> {dest.name}")
            result["files_compressed"].append({"src": str(src), "dest": str(dest), "dry_run": True})
            continue

        try:
            # Compress
            with open(src, "rb") as f_in:
                with gzip.open(dest, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)

            # Verify compressed file exists and is valid
            if dest.exists() and dest.stat().st_size > 0:
                # Remove original (safe because we're in archive, not primary)
                src.unlink()
                print(f"  Compressed: {src.name} -> {dest.name}")
                result["files_compressed"].append({"src": str(src), "dest": str(dest)})
            else:
                print(f"  ERROR: Compression failed for {src.name}")
                result["errors"].append({"file": str(src), "error": "Compression verification failed"})
                # Remove failed compressed file
                if dest.exists():
                    dest.unlink()

        except Exception as e:
            print(f"  ERROR: Failed to compress {src.name}: {e}")
            result["errors"].append({"file": str(src), "error": str(e)})

    # Emit audit event
    if result["files_compressed"] and not dry_run:
        emit_maintenance_audit(
            "compress",
            [f["src"] for f in result["files_compressed"]],
            details={"compressed_to": [f["dest"] for f in result["files_compressed"]]},
        )

    return result


# === VERIFY COMMAND ===

def cmd_verify(layout: StateLayout, quarantine_corrupted: bool = True) -> Dict[str, Any]:
    """
    Verify integrity of all JSONL files.

    Quarantines corrupted lines if found.
    """
    result = {
        "command": "verify",
        "files_checked": [],
        "total_valid": 0,
        "total_invalid": 0,
        "quarantined": [],
    }

    primary = _find_primary_logs(layout)

    if not primary:
        print("No JSONL files to verify.")
        return result

    print(f"Verifying {len(primary)} JSONL file(s)...")

    for log_file in primary:
        records, valid, invalid = read_and_verify(log_file)

        file_result = {
            "file": str(log_file),
            "name": log_file.name,
            "valid": valid,
            "invalid": invalid,
        }
        result["files_checked"].append(file_result)
        result["total_valid"] += valid
        result["total_invalid"] += invalid

        status = "OK" if invalid == 0 else "CORRUPTED"
        print(f"  {log_file.name}: {valid} valid, {invalid} invalid [{status}]")

        # Quarantine corrupted lines
        if invalid > 0 and quarantine_corrupted:
            # Re-read file to find corrupted lines
            corrupted_lines = _extract_corrupted_lines(log_file)
            for line_num, line_content in corrupted_lines:
                q_result = _quarantine_line(layout, log_file.name, line_num, line_content)
                if q_result:
                    result["quarantined"].append(q_result)
                    print(f"    Quarantined line {line_num}")

    # Emit audit event
    emit_audit(
        "maintenance",
        "verify",
        details={
            "files_count": len(result["files_checked"]),
            "total_valid": result["total_valid"],
            "total_invalid": result["total_invalid"],
            "quarantined_count": len(result["quarantined"]),
        },
    )

    return result


def _extract_corrupted_lines(path: Path) -> List[Tuple[int, str]]:
    """Extract line numbers and content of corrupted lines."""
    corrupted = []

    with open(path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.rstrip("\n\r")
            if not line:
                continue

            if not line.startswith("sha256:"):
                corrupted.append((line_num, line))
                continue

            parts = line.split(":", 2)
            if len(parts) != 3:
                corrupted.append((line_num, line))
                continue

            claimed_sha = parts[1]
            payload = parts[2]

            computed_sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            if computed_sha != claimed_sha:
                corrupted.append((line_num, line))

    return corrupted


def _quarantine_line(
    layout: StateLayout,
    source_file: str,
    line_num: int,
    content: str,
) -> Optional[Dict[str, Any]]:
    """Quarantine a single corrupted line."""
    try:
        content_bytes = content.encode("utf-8")
        content_sha = hashlib.sha256(content_bytes).hexdigest()

        # Write blob
        blob_path = layout.quarantine_blob(source_file.replace(".", "_"), content_sha)
        blob_path.write_bytes(content_bytes)

        # Write metadata
        meta_path = layout.quarantine_meta(source_file.replace(".", "_"), content_sha)
        meta = build_quarantine_event(
            reason="sha256_mismatch",
            source=f"maintenance.verify:{source_file}",
            blob_sha256=content_sha,
            blob_path=str(blob_path),
            context={"line_num": line_num, "source_file": source_file},
        )
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return {
            "source_file": source_file,
            "line_num": line_num,
            "blob_sha256": content_sha,
            "blob_path": str(blob_path),
        }

    except Exception as e:
        print(f"    ERROR: Failed to quarantine line {line_num}: {e}")
        return None


# === REPORT COMMAND ===

def cmd_report(layout: StateLayout) -> Dict[str, Any]:
    """Generate state summary report."""
    result = {
        "command": "report",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "state_dir": str(layout.state_dir()),
        "primary_logs": [],
        "archived_files": [],
        "quarantine_files": [],
        "total_size_bytes": 0,
    }

    # Primary logs
    for f in _find_primary_logs(layout):
        size = f.stat().st_size
        result["primary_logs"].append({
            "name": f.name,
            "size_bytes": size,
            "size_mb": round(size / (1024 * 1024), 2),
            "age_days": round(_file_age_days(f), 1),
        })
        result["total_size_bytes"] += size

    # Archived files
    for f in _find_archived_files(layout):
        size = f.stat().st_size
        result["archived_files"].append({
            "name": str(f.relative_to(layout.archive_dir())),
            "size_bytes": size,
            "compressed": f.suffix in (".gz", ".zip", ".bz2"),
        })
        result["total_size_bytes"] += size

    # Quarantine files
    q_dir = layout.quarantine_dir()
    if q_dir.exists():
        for f in q_dir.glob("*.blob"):
            size = f.stat().st_size
            result["quarantine_files"].append({
                "name": f.name,
                "size_bytes": size,
            })
            result["total_size_bytes"] += size

    result["total_size_mb"] = round(result["total_size_bytes"] / (1024 * 1024), 2)

    # Save report
    report_path = layout.report_file("maintenance")
    report_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Report saved: {report_path}")

    # Print summary
    _print_report(result)

    return result


def _print_report(result: Dict[str, Any]) -> None:
    """Print report summary."""
    if RICH_AVAILABLE:
        _print_report_rich(result)
    else:
        _print_report_plain(result)


def _print_report_plain(result: Dict[str, Any]) -> None:
    """Print report without rich."""
    print("\n=== STATE MAINTENANCE REPORT ===")
    print(f"Timestamp: {result['timestamp']}")
    print(f"State dir: {result['state_dir']}")
    print(f"Total size: {result['total_size_mb']} MB")

    print("\nPrimary logs:")
    for f in result["primary_logs"]:
        print(f"  {f['name']}: {f['size_mb']} MB, {f['age_days']} days old")

    print(f"\nArchived files: {len(result['archived_files'])}")
    print(f"Quarantine files: {len(result['quarantine_files'])}")


def _print_report_rich(result: Dict[str, Any]) -> None:
    """Print report with rich formatting."""
    console = Console()

    console.print("\n[bold cyan]=== STATE MAINTENANCE REPORT ===[/bold cyan]")
    console.print(f"Timestamp: {result['timestamp']}")
    console.print(f"State dir: {result['state_dir']}")
    console.print(f"Total size: [bold]{result['total_size_mb']} MB[/bold]")

    if result["primary_logs"]:
        table = Table(title="Primary Logs")
        table.add_column("Name", style="cyan")
        table.add_column("Size (MB)", justify="right")
        table.add_column("Age (days)", justify="right")

        for f in result["primary_logs"]:
            table.add_row(f["name"], str(f["size_mb"]), str(f["age_days"]))

        console.print(table)

    console.print(f"\nArchived files: {len(result['archived_files'])}")
    console.print(f"Quarantine files: {len(result['quarantine_files'])}")


# === PURGE-ARCHIVE COMMAND ===

def cmd_purge_archive(
    layout: StateLayout,
    older_than_days: int,
    confirmed: bool,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Delete old archived files.

    REQUIRES:
    - --older-than flag
    - --i-know-what-im-doing confirmation
    - Writes manifest to audit BEFORE deletion

    Args:
        layout: StateLayout instance
        older_than_days: Only delete files older than this
        confirmed: User confirmed with --i-know-what-im-doing
        dry_run: Only show what would be deleted
    """
    result = {
        "command": "purge-archive",
        "older_than_days": older_than_days,
        "files_deleted": [],
        "files_skipped": [],
        "errors": [],
        "dry_run": dry_run,
        "confirmed": confirmed,
    }

    if not confirmed:
        print("ERROR: purge-archive requires --i-know-what-im-doing flag")
        print("This command PERMANENTLY DELETES files.")
        result["errors"].append("Not confirmed")
        return result

    archived = _find_archived_files(layout)

    if not archived:
        print("No archived files to purge.")
        return result

    # Filter by age
    to_delete = []
    for f in archived:
        age = _file_age_days(f)
        if age >= older_than_days:
            to_delete.append((f, age))
        else:
            result["files_skipped"].append({
                "file": str(f),
                "age_days": round(age, 1),
                "reason": "too_recent",
            })

    if not to_delete:
        print(f"No files older than {older_than_days} days to purge.")
        return result

    print(f"Found {len(to_delete)} file(s) to purge (older than {older_than_days} days).")

    # Build manifest BEFORE deleting
    manifest = []
    for f, age in to_delete:
        manifest.append({
            "file": str(f),
            "sha256": _sha256_file(f) if not dry_run else "DRY_RUN",
            "size_bytes": f.stat().st_size,
            "age_days": round(age, 1),
        })

    # Write manifest to audit FIRST
    if not dry_run:
        emit_maintenance_audit(
            "purge",
            [m["file"] for m in manifest],
            details={
                "older_than_days": older_than_days,
                "manifest": manifest,
            },
        )
        print("Manifest written to audit log.")

    # Now delete
    for f, age in to_delete:
        if dry_run:
            print(f"  [DRY-RUN] Would delete: {f.name} ({round(age, 1)} days old)")
            result["files_deleted"].append({"file": str(f), "dry_run": True})
            continue

        try:
            f.unlink()
            print(f"  Deleted: {f.name}")
            result["files_deleted"].append({"file": str(f)})
        except Exception as e:
            print(f"  ERROR: Failed to delete {f.name}: {e}")
            result["errors"].append({"file": str(f), "error": str(e)})

    return result


# === MAIN ===

def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="HOPE State Maintenance CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "command",
        choices=["archive", "compress", "verify", "report", "purge-archive"],
        help="Maintenance command to run",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--older-than",
        type=str,
        help="For purge-archive: only delete files older than this (e.g., 180d)",
    )
    parser.add_argument(
        "--i-know-what-im-doing",
        action="store_true",
        help="Confirm destructive operations",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        help="Override state directory",
    )

    args = parser.parse_args()

    # Setup layout
    if args.state_dir:
        from core.state_layout import reset_layout
        layout = reset_layout(args.state_dir)
    else:
        layout = get_layout()

    # Execute command
    if args.command == "archive":
        result = cmd_archive(layout, dry_run=args.dry_run)

    elif args.command == "compress":
        result = cmd_compress(layout, dry_run=args.dry_run)

    elif args.command == "verify":
        result = cmd_verify(layout)

    elif args.command == "report":
        result = cmd_report(layout)

    elif args.command == "purge-archive":
        if not args.older_than:
            print("ERROR: purge-archive requires --older-than flag")
            return 1

        # Parse older_than (e.g., "180d" -> 180)
        older_str = args.older_than.lower().rstrip("d")
        try:
            older_days = int(older_str)
        except ValueError:
            print(f"ERROR: Invalid --older-than value: {args.older_than}")
            return 1

        result = cmd_purge_archive(
            layout,
            older_than_days=older_days,
            confirmed=args.i_know_what_im_doing,
            dry_run=args.dry_run,
        )

    else:
        print(f"Unknown command: {args.command}")
        return 1

    # Print result summary
    if result.get("errors"):
        print(f"\nCompleted with {len(result['errors'])} error(s)")
        return 1

    print("\nCompleted successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
