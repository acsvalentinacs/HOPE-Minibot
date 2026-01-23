# === AI SIGNATURE ===
# Created by: Claude
# Created at: 2026-01-22 13:00:00 UTC
# === END SIGNATURE ===
r"""
tools/jsonl_repair_sha256_v2.py

Fail-closed JSONL repair/normalizer:
- Accepts either:
  1) sha256:<hex>:<json>
  2) raw JSON line (object/array/number/string) -> will be rewritten to sha256 format
- If a line is neither valid sha256-line nor valid JSON -> FAIL (no partial rewrite)

Atomic replace:
- write .fixed, fsync, rename original to .bak, replace

Usage (PowerShell):
  .\.venv\Scripts\python.exe tools\jsonl_repair_sha256_v2.py path\to\file.jsonl
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable, Tuple

RX = re.compile(r"^sha256:([0-9a-f]{64}):(.*)$")


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _fsync_dir(path: Path) -> None:
    # Best-effort fsync directory on Windows: open directory handle may fail; ignore silently
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except Exception:
        return
    try:
        os.fsync(fd)
    except Exception:
        pass
    finally:
        try:
            os.close(fd)
        except Exception:
            pass


def _atomic_replace(src: Path, dst: Path) -> None:
    os.replace(str(src), str(dst))
    _fsync_dir(dst.parent)


def _parse_or_rewrite_line(line: str, line_no: int) -> Tuple[str, bool]:
    """
    Returns: (normalized_sha256_line, changed?)
    FAIL-CLOSED on any invalid format.
    """
    m = RX.match(line)
    if m:
        h = m.group(1)
        payload = m.group(2)
        hh = _sha256_hex(payload)
        if hh != h:
            raise SystemExit(f"[FAIL] sha256 mismatch at line {line_no}: expected={h} actual={hh}")
        return line, False

    # Not sha256-line: must be valid JSON
    try:
        obj = json.loads(line)
    except Exception as e:
        raise SystemExit(f"[FAIL] invalid line at {line_no}: not sha256-line and not JSON: {e}")

    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    h = _sha256_hex(payload)
    return f"sha256:{h}:{payload}", True


def repair_file(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"[FAIL] not found: {path}")

    raw = path.read_text("utf-8", errors="replace").splitlines()
    if not raw:
        raise SystemExit(f"[FAIL] empty file: {path}")

    changed = 0
    out_lines = []
    for i, line in enumerate(raw, 1):
        if not line.strip():
            # empty lines: skip silently (lenient mode for logs)
            continue
        norm, ch = _parse_or_rewrite_line(line, i)
        out_lines.append(norm)
        if ch:
            changed += 1

    fixed = path.with_suffix(path.suffix + ".fixed")
    bak = path.with_suffix(path.suffix + ".bak")

    data = ("\n".join(out_lines) + "\n").encode("utf-8")
    with open(fixed, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())

    # Backup original then replace atomically
    if bak.exists():
        # Remove old backup
        bak.unlink()

    _atomic_replace(path, bak)
    _atomic_replace(fixed, path)

    print(f"[OK] repaired: {path} (lines={len(out_lines)} changed={changed})")
    print(f"[OK] backup:  {bak}")


def repair_all_in_dir(dir_path: Path) -> int:
    """Repair all .jsonl files in directory."""
    count = 0
    for f in sorted(dir_path.glob("*.jsonl")):
        try:
            repair_file(f)
            count += 1
        except SystemExit as e:
            print(str(e))
    return count


def main(argv: Iterable[str]) -> int:
    args = list(argv)
    if len(args) < 2:
        print("Usage: python tools/jsonl_repair_sha256_v2.py <file.jsonl|--dir=path>")
        return 2

    if args[1].startswith("--dir="):
        dir_path = Path(args[1].replace("--dir=", ""))
        count = repair_all_in_dir(dir_path)
        print(f"\n[DONE] Repaired {count} files in {dir_path}")
        return 0

    repair_file(Path(args[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
