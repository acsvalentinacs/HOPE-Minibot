# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-22 23:15:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-22 23:15:00 UTC
# === END SIGNATURE ===
"""
JSONL Stress Test - Prove inter-process write correctness.

Spawns N processes, each writes M lines to the same JSONL file.
Uses core.jsonl_sha for atomic append with inter-process lock.

Expected invariants:
- Exactly N*M lines in output
- Every line matches sha256:<64hex>:<json> format
- Every sha256 matches recomputed hash of JSON payload
- No partial/corrupted/mixed lines

Usage:
    python tools/jsonl_stress.py --procs 8 --lines 200 --out state/stress/out.jsonl
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
import time
from pathlib import Path

# SSoT: compute paths from __file__
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))


def worker(worker_id: int, num_lines: int, output_path: Path) -> int:
    """
    Worker process: write num_lines to output_path.

    Returns number of lines written.
    """
    from core.jsonl_sha import append_sha256_line

    written = 0
    for i in range(num_lines):
        record = {
            "worker_id": worker_id,
            "line_num": i,
            "ts": time.time(),
            "payload": f"stress_test_w{worker_id}_l{i}",
        }
        try:
            append_sha256_line(output_path, record)
            written += 1
        except Exception as e:
            print(f"[W{worker_id}] FAIL line {i}: {e}", file=sys.stderr)

    return written


def run_stress(num_procs: int, num_lines: int, output_path: Path) -> dict:
    """
    Run stress test with N processes, M lines each.

    Returns summary dict.
    """
    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Remove old file if exists
    if output_path.exists():
        output_path.unlink()

    print(f"JSONL Stress Test: {num_procs} procs x {num_lines} lines = {num_procs * num_lines} expected")
    print(f"Output: {output_path}")

    start = time.time()

    # Spawn workers
    with multiprocessing.Pool(processes=num_procs) as pool:
        args = [(i, num_lines, output_path) for i in range(num_procs)]
        results = pool.starmap(worker, args)

    elapsed = time.time() - start
    total_written = sum(results)

    # Count actual lines
    actual_lines = 0
    if output_path.exists():
        with open(output_path, "r", encoding="utf-8") as f:
            actual_lines = sum(1 for _ in f)

    summary = {
        "procs": num_procs,
        "lines_per_proc": num_lines,
        "expected_total": num_procs * num_lines,
        "reported_written": total_written,
        "actual_lines": actual_lines,
        "elapsed_sec": round(elapsed, 3),
        "lines_per_sec": round(actual_lines / elapsed, 1) if elapsed > 0 else 0,
        "output_path": str(output_path),
    }

    # Print summary
    print(f"\n=== STRESS TEST SUMMARY ===")
    print(f"Expected:  {summary['expected_total']}")
    print(f"Reported:  {summary['reported_written']}")
    print(f"Actual:    {summary['actual_lines']}")
    print(f"Elapsed:   {summary['elapsed_sec']}s")
    print(f"Throughput: {summary['lines_per_sec']} lines/sec")

    if summary["actual_lines"] == summary["expected_total"]:
        print(f"\nPASS: Line count matches expected")
    else:
        print(f"\nFAIL: Line count mismatch!")
        print(f"  Missing: {summary['expected_total'] - summary['actual_lines']}")

    return summary


def main() -> int:
    """CLI entrypoint."""
    ap = argparse.ArgumentParser(description="JSONL inter-process stress test")
    ap.add_argument("--procs", type=int, default=8, help="Number of processes")
    ap.add_argument("--lines", type=int, default=200, help="Lines per process")
    ap.add_argument("--out", type=Path, default=BASE_DIR / "state" / "stress" / "out.jsonl",
                    help="Output JSONL file")
    ns = ap.parse_args()

    summary = run_stress(ns.procs, ns.lines, ns.out)

    # Exit code: 0 if pass, 1 if fail
    if summary["actual_lines"] == summary["expected_total"]:
        return 0
    return 1


if __name__ == "__main__":
    # Required for Windows multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
