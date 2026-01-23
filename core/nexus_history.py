# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 13:15:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 14:10:00 UTC
# === END SIGNATURE ===
"""
NEXUS History - Persistent Message Storage ("Black Box").

ARCHITECTURE:
    All NEXUS messages (sent + received) are persisted to JSONL with:
    - sha256 prefix per line for integrity verification
    - Inter-process locking (via jsonl_sha)
    - fsync after each write (crash-safe)
    - Rotation to archive (NO DELETION)

PROTO LAYER INTEGRATION:
    - state_layout: Unified path management
    - schemas: Event validation (nexus_history.v1)
    - jsonl_sha: Atomic writes with integrity
    - audit: Rotation events logged

FILE FORMAT (sha256-prefixed JSONL, Canon B):
    sha256:<64-hex>:<canonical-json>\n
    sha256:<64-hex>:<canonical-json>\n
    ...

SCHEMA (nexus_history.v1):
    Required: schema, ts_unix, ts_utc, direction, text, peer
    Optional: bridge_id, reply_to, inbox, msg_type, meta

USAGE:
    from core.nexus_history import append_history, load_history

    # Append new entry (auto-locks, fsync, rotates)
    append_history({
        "direction": "out",
        "peer": "gpt",
        "text": "Hello GPT!",
    })

    # Load last 50 entries
    entries = load_history(limit=50)

THREAD SAFETY:
    - Uses jsonl_sha for inter-process safe writes
    - Single append is atomic (lock -> write -> fsync -> unlock)

FAIL-CLOSED:
    - If lock fails -> raise exception (no silent skip)
    - If write fails -> raise exception (no partial data)
    - If validation fails -> quarantine entry, don't crash
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Proto layer imports
from core.state_layout import get_layout, history_file
from core.jsonl_sha import append_sha256_line, read_and_verify
from core.schemas.registry import (
    validate,
    normalize,
    build_nexus_event,
    build_quarantine_event,
)


# === CONFIGURATION ===

DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB before rotation
DEFAULT_BACKUPS = 3  # Keep 3 rotated files (.1, .2, .3)


def _get_history_path() -> Path:
    """Get history file path from state_layout."""
    return history_file("nexus")


# === LEGACY COMPATIBILITY ===

def sha256_hex(data: str) -> str:
    """Compute SHA256 hex digest of UTF-8 string."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def canonical_json(obj: Any) -> str:
    """
    Canonical JSON (Canon B): sorted keys, minimal separators, UTF-8.

    This ensures the same object always produces the same string,
    which is required for consistent sha256 hashing.
    """
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def encode_jsonl_sha(obj: Dict[str, Any]) -> str:
    """
    Encode object to sha256-prefixed JSONL line.

    Format: sha256:<64-hex>:<canonical-json>\n
    """
    payload = canonical_json(obj)
    digest = sha256_hex(payload)
    return f"sha256:{digest}:{payload}\n"


def decode_jsonl_sha(line: str) -> Optional[Dict[str, Any]]:
    """
    Decode sha256-prefixed JSONL line.

    Returns None if:
        - Line is empty or whitespace
        - Format is invalid
        - SHA256 checksum fails
    """
    line = line.strip()
    if not line:
        return None

    if not line.startswith("sha256:"):
        return None

    parts = line.split(":", 2)
    if len(parts) != 3:
        return None

    prefix, expected_hash, payload = parts
    if len(expected_hash) != 64:
        return None

    actual_hash = sha256_hex(payload)
    if actual_hash != expected_hash:
        print(
            f"[WARN] nexus_history: SHA256 mismatch, line corrupted: "
            f"expected={expected_hash[:12]}..., actual={actual_hash[:12]}...",
            file=sys.stderr,
        )
        return None

    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


# === ROTATION (ARCHIVE, NO DELETE) ===

def _rotate_to_archive(
    path: Path,
    max_bytes: int,
    max_rotations: int,
) -> Optional[str]:
    """
    Rotate file to archive if it exceeds max_bytes.

    PROTOCOL: NO DELETION
    - Files are moved to archive directory
    - maintenance.py handles compression/cleanup later

    Rotation scheme:
        history.jsonl -> history.jsonl.1
        history.jsonl.1 -> archive/nexus/YYYY-MM/history.jsonl.1

    Returns:
        Path to archived file if rotation occurred, None otherwise
    """
    if not path.exists():
        return None

    size = path.stat().st_size
    if size < max_bytes:
        return None

    layout = get_layout()

    # Move existing rotations to archive first
    for i in range(max_rotations, 0, -1):
        rotated = path.with_suffix(f".jsonl.{i}")
        if rotated.exists():
            # Archive instead of delete
            archive_dest = layout.archived_file("nexus", rotated.name)
            archive_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(rotated), str(archive_dest))

    # Rotate current to .1
    rotated_path = path.with_suffix(".jsonl.1")
    path.rename(rotated_path)

    # Emit audit event for rotation
    try:
        from core.audit import emit_audit
        emit_audit(
            "nexus",
            "rotate",
            details={
                "original_size_bytes": size,
                "rotated_to": str(rotated_path),
            },
        )
    except Exception:
        pass  # Don't fail rotation if audit fails

    return str(rotated_path)


# === QUARANTINE ===

def _quarantine_entry(
    entry: Dict[str, Any],
    reason: str,
    errors: List[str],
) -> None:
    """
    Quarantine invalid entry instead of losing it.

    Writes to quarantine directory with metadata.
    """
    try:
        layout = get_layout()

        content = canonical_json(entry)
        content_sha = sha256_hex(content)

        # Write blob
        blob_path = layout.quarantine_blob("nexus_history", content_sha)
        blob_path.write_text(content, encoding="utf-8")

        # Write metadata
        meta = build_quarantine_event(
            reason=reason,
            source="nexus_history.append",
            blob_sha256=content_sha,
            blob_path=str(blob_path),
            context={"validation_errors": errors},
        )

        meta_path = layout.quarantine_meta("nexus_history", content_sha)
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    except Exception as e:
        print(f"[WARN] nexus_history: Failed to quarantine: {e}", file=sys.stderr)


# === PUBLIC API ===

def append_history(
    entry: Dict[str, Any],
    *,
    path: Optional[Path] = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backups: int = DEFAULT_BACKUPS,
) -> str:
    """
    Append entry to NEXUS history file (thread-safe, fsync).

    Args:
        entry: Dictionary to persist (will be normalized to schema)
        path: History file path (default: from state_layout)
        max_bytes: Rotate when file exceeds this size
        backups: Number of rotated backups to keep

    Returns:
        SHA256 digest of the entry (for deduplication/reference)

    Raises:
        TimeoutError: If lock cannot be acquired
        OSError: If write fails

    Example:
        sha = append_history({
            "direction": "out",
            "peer": "gpt",
            "text": "Hello!",
        })
    """
    if path is None:
        path = _get_history_path()

    # Convert legacy fields to new schema
    normalized = _convert_to_schema(entry)

    # Validate
    errors = validate("nexus_history.v1", normalized)
    if errors:
        print(f"[WARN] nexus_history: Validation errors: {errors}", file=sys.stderr)
        # Quarantine but continue (fail-soft)
        _quarantine_entry(entry, "schema_mismatch", errors)
        # Normalize anyway to try writing
        normalized = normalize("nexus_history.v1", normalized)

    # Rotate if needed
    _rotate_to_archive(path, max_bytes, backups)

    # Write using jsonl_sha
    sha = append_sha256_line(path, normalized)

    return sha


def _convert_to_schema(entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert legacy entry format to nexus_history.v1 schema.

    Legacy fields:
        from, to, direction, text, timestamp, time, id, type, inbox

    New schema fields:
        schema, ts_unix, ts_utc, direction, text, peer, bridge_id, reply_to, inbox, msg_type, meta
    """
    result: Dict[str, Any] = {}

    # Schema identifier
    result["schema"] = "nexus_history.v1"

    # Timestamps
    now = datetime.now(timezone.utc)
    if "ts_unix" not in entry:
        result["ts_unix"] = time.time()
    else:
        result["ts_unix"] = entry["ts_unix"]

    if "ts_utc" not in entry:
        # Try to use legacy timestamp
        if "timestamp" in entry:
            result["ts_utc"] = entry["timestamp"]
        else:
            result["ts_utc"] = now.isoformat()
    else:
        result["ts_utc"] = entry["ts_utc"]

    # Direction
    result["direction"] = entry.get("direction", "status")

    # Text
    result["text"] = entry.get("text", entry.get("message", ""))

    # Peer (unified from/to)
    direction = result["direction"]
    if direction == "in":
        result["peer"] = entry.get("from", entry.get("peer", "unknown"))
    elif direction == "out":
        result["peer"] = entry.get("to", entry.get("peer", "unknown"))
    else:
        result["peer"] = entry.get("peer", entry.get("from", entry.get("to", "unknown")))

    # Optional fields
    result["bridge_id"] = entry.get("id", entry.get("bridge_id"))
    result["reply_to"] = entry.get("reply_to")
    result["inbox"] = entry.get("inbox", "nexus")
    result["msg_type"] = entry.get("type", entry.get("msg_type"))
    result["meta"] = entry.get("meta")

    # Preserve any legacy fields in meta
    legacy_fields = {"time", "from", "to", "timestamp"}
    legacy_data = {k: v for k, v in entry.items() if k in legacy_fields}
    if legacy_data:
        if result["meta"] is None:
            result["meta"] = {}
        result["meta"]["_legacy"] = legacy_data

    return result


def load_history(
    limit: int = 50,
    *,
    path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    Load last N entries from NEXUS history.

    Args:
        limit: Maximum number of entries to return
        path: History file path (default: from state_layout)

    Returns:
        List of entries (oldest to newest), up to `limit` items.
        Corrupted lines are skipped.
    """
    if path is None:
        path = _get_history_path()

    if not path.exists():
        return []

    records, valid, invalid = read_and_verify(path)

    if invalid > 0:
        print(
            f"[WARN] nexus_history: {invalid} corrupted line(s) skipped",
            file=sys.stderr,
        )

    # Return last `limit` entries
    return records[-limit:] if limit else records


def count_history(
    *,
    path: Optional[Path] = None,
) -> int:
    """
    Count valid entries in history file.

    Useful for status display without loading all data.
    """
    if path is None:
        path = _get_history_path()

    if not path.exists():
        return 0

    records, valid, invalid = read_and_verify(path)
    return valid


def verify_history(
    *,
    path: Optional[Path] = None,
) -> Tuple[int, int]:
    """
    Verify history file integrity.

    Returns:
        Tuple of (valid_count, corrupted_count)
    """
    if path is None:
        path = _get_history_path()

    if not path.exists():
        return (0, 0)

    records, valid, invalid = read_and_verify(path)
    return (valid, invalid)


# === CLI ===

def main() -> int:
    """CLI for history inspection."""
    import argparse

    parser = argparse.ArgumentParser(description="NEXUS History Inspector")
    parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help="History file path (default: from state_layout)",
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=10,
        help="Show last N entries (default: 10)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify file integrity",
    )
    parser.add_argument(
        "--count",
        action="store_true",
        help="Count entries only",
    )

    args = parser.parse_args()

    # Use provided path or default
    path = args.path

    if args.verify:
        valid, corrupted = verify_history(path=path)
        print(f"Valid entries: {valid}")
        print(f"Corrupted entries: {corrupted}")
        return 0 if corrupted == 0 else 1

    if args.count:
        print(count_history(path=path))
        return 0

    # Show tail
    entries = load_history(limit=args.tail, path=path)

    if not entries:
        print("No history entries found.")
        return 0

    for e in entries:
        direction = e.get("direction", "?")
        ts = e.get("ts_utc", e.get("timestamp", "?"))[:19]
        peer = e.get("peer", "?")
        text = e.get("text", "")[:60]

        if direction == "out":
            print(f"[{ts}] YOU -> {peer.upper()}: {text}...")
        else:
            print(f"[{ts}] {peer.upper()} -> YOU: {text}...")

    return 0


if __name__ == "__main__":
    sys.exit(main())
