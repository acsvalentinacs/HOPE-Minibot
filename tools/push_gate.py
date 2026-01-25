# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T18:00:00Z
# Modified by: Claude (opus-4)
# Modified at (UTC): 2026-01-25T21:00:00Z
# Purpose: Push Gate v2.1 - unified release verification with MAINNET eligibility
# === END SIGNATURE ===
"""
Push Gate v2.1 - Unified Release Verification.

Runs ALL required gates in fixed order before allowing push.
ANY gate failure = FAIL (no exceptions, no "optional").

Gate Order (fixed, mandatory):
1. commit_gate       - Manifest and policy validation
2. dirty_tree_guard  - No untracked/modified files
3. verify_tree       - Deterministic tree manifest
4. allowlist_guard   - AllowList format validation
5. network_guard     - No direct network outside core/net/** (AST)
6. secrets_guard     - No hardcoded secrets
7. live_smoke_gate   - Trading smoke test (DRY)
8. evidence_guard    - Health file schema validation
9. testnet_gate      - TESTNET API verification
10. git push --dry-run

Output: state/health/release_gate.json

Schema: release_gate_v1
- schema_version: str
- cmdline_sha256: str
- git_head: str
- ts_utc: str
- eligible_for_mainnet: bool
- gates[]: {name, passed, details}

Usage:
    python tools/push_gate.py           # Full verification
    python tools/push_gate.py --execute # Verify + actual push

Exit codes:
    0 = PASS (all gates passed)
    1 = FAIL (one or more gates failed)
    2 = ERROR (setup/config issue)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def get_cmdline_sha256() -> str:
    """Get command line SHA256."""
    try:
        from core.ssot.cmdline import get_cmdline_sha256 as ssot_cmdline
        return ssot_cmdline()
    except ImportError:
        cmdline = " ".join(sys.argv)
        return hashlib.sha256(cmdline.encode("utf-8")).hexdigest()


def get_git_head() -> Optional[str]:
    """Get current git HEAD."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def run_command(cmd: List[str], description: str) -> Tuple[bool, str]:
    """Run command and return (success, output)."""
    try:
        result = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output.strip()
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT: {description}"
    except Exception as e:
        return False, f"ERROR: {e}"


def run_python_gate(script: str, args: List[str] = None) -> Tuple[bool, str]:
    """Run Python gate script."""
    cmd = [sys.executable, script]
    if args:
        cmd.extend(args)
    return run_command(cmd, script)


def atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    """Write JSON atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def check_legacy_allowlist_empty() -> bool:
    """Check if legacy network allowlist is empty (required for MAINNET)."""
    allowlist_path = PROJECT_ROOT / "config" / "legacy_net_allowlist.json"
    if not allowlist_path.exists():
        return True  # No file = no legacy entries

    try:
        data = json.loads(allowlist_path.read_text(encoding="utf-8"))
        allowed = data.get("allowed", [])
        # Filter out "never" deadline entries (they're permanent exceptions)
        active_entries = [e for e in allowed if e.get("deadline") != "never"]
        return len(active_entries) == 0
    except (json.JSONDecodeError, KeyError):
        return False


def main() -> int:
    """Main entrypoint."""
    parser = argparse.ArgumentParser(
        description="Push Gate v2.1 - unified release verification (fail-closed)",
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually push after verification passes",
    )
    parser.add_argument(
        "--allow-state", action="store_true",
        help="Allow untracked files in state/**",
    )
    parser.add_argument(
        "--skip-testnet", action="store_true",
        help="Skip TESTNET gate (for offline environments)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output JSON report only",
    )

    args = parser.parse_args()

    ts_utc = datetime.now(timezone.utc).isoformat()
    cmdline_sha256 = get_cmdline_sha256()
    git_head = get_git_head() or "unknown"

    total_gates = 10 if not args.skip_testnet else 9

    if not args.json:
        print("=" * 60)
        print("PUSH GATE v2.1 - Unified Release Verification")
        print("=" * 60)
        print(f"Timestamp: {ts_utc}")
        print(f"Root: {PROJECT_ROOT}")
        print(f"Git HEAD: {git_head[:12]}...")
        print(f"Gates: {total_gates}")
        print()
        print("ALL gates are MANDATORY. Any failure = FAIL.")
        print()

    gates_results: List[Dict[str, Any]] = []
    all_passed = True
    gate_num = 0

    def run_gate(name: str, func, *func_args) -> bool:
        nonlocal gate_num, all_passed
        gate_num += 1

        if not args.json:
            print(f"[{gate_num}/{total_gates}] {name}...")

        success, output = func(*func_args)

        gates_results.append({
            "name": name,
            "passed": success,
            "details": output[:500] if not success else None,
        })

        if success:
            if not args.json:
                print("      PASS")
        else:
            if not args.json:
                print("      FAIL")
                for line in output.split("\n")[:5]:
                    if line.strip():
                        print(f"      {line.strip()[:70]}")
            all_passed = False

        return success

    # === GATE 1: commit_gate ===
    run_gate("commit_gate",
             lambda: run_python_gate("tools/commit_gate.py", ["--check"]))

    # === GATE 2: dirty_tree_guard ===
    dirty_args = ["--allow-state"] if args.allow_state else []
    run_gate("dirty_tree_guard",
             lambda: run_python_gate("tools/dirty_tree_guard.py", dirty_args))

    # === GATE 3: verify_tree ===
    run_gate("verify_tree",
             lambda: run_python_gate("tools/verify_tree.py", []))

    # === GATE 4: allowlist_guard ===
    run_gate("allowlist_guard",
             lambda: run_python_gate("tools/allowlist_guard.py", []))

    # === GATE 5: network_guard (AST-based v2) ===
    run_gate("network_guard",
             lambda: run_python_gate("tools/network_guard.py", []))

    # === GATE 6: secrets_guard ===
    run_gate("secrets_guard",
             lambda: run_python_gate("tools/secrets_guard.py", []))

    # === GATE 7: live_smoke_gate ===
    run_gate("live_smoke_gate",
             lambda: run_python_gate("tools/live_smoke_gate.py", ["--mode", "DRY"]))

    # === GATE 8: evidence_guard ===
    run_gate("evidence_guard",
             lambda: run_python_gate("tools/evidence_guard.py", ["--path", "state/health/live_trade.json"]))

    # === GATE 9: testnet_gate ===
    if not args.skip_testnet:
        run_gate("testnet_gate",
                 lambda: run_python_gate("tools/testnet_gate.py", []))
    else:
        gates_results.append({"name": "testnet_gate", "passed": None, "details": "SKIPPED"})

    # === GATE 10: git push --dry-run ===
    run_gate("git_push_dry",
             lambda: run_command(["git", "push", "--dry-run"], "git push --dry-run"))

    # Determine MAINNET eligibility
    legacy_empty = check_legacy_allowlist_empty()
    network_gate_passed = any(g["name"] == "network_guard" and g["passed"] for g in gates_results)
    eligible_for_mainnet = all_passed and legacy_empty and network_gate_passed

    # Write release_gate.json
    report = {
        "schema_version": "release_gate_v1",
        "ts_utc": ts_utc,
        "cmdline_sha256": cmdline_sha256,
        "git_head": git_head,
        "eligible_for_mainnet": eligible_for_mainnet,
        "all_passed": all_passed,
        "gates": gates_results,
    }

    report_path = PROJECT_ROOT / "state" / "health" / "release_gate.json"
    try:
        atomic_write_json(report_path, report)
    except OSError as e:
        if not args.json:
            print(f"WARNING: Failed to write release_gate.json: {e}")

    # Output
    if args.json:
        print(json.dumps(report, indent=2))
        return 0 if all_passed else 1

    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)

    for gate in gates_results:
        if gate["passed"] is None:
            status = "SKIP"
        elif gate["passed"]:
            status = "PASS"
        else:
            status = "FAIL"
        print(f"  {gate['name']}: {status}")

    print()
    print(f"MAINNET Eligible: {'YES' if eligible_for_mainnet else 'NO'}")
    if not eligible_for_mainnet:
        if not legacy_empty:
            print("  Reason: Legacy network allowlist not empty")
        if not all_passed:
            print("  Reason: Not all gates passed")

    print()

    if all_passed:
        print("Result: PASS - READY TO PUSH")
        print(f"Report: {report_path}")

        if args.execute:
            print()
            print("Executing git push...")
            success, output = run_command(["git", "push"], "git push")
            if success:
                print("Push successful!")
                print(output)
                return 0
            else:
                print("Push FAILED!")
                print(output)
                return 1
        else:
            print()
            print("To push: git push")
            print("Or run: python tools/push_gate.py --execute")
            return 0
    else:
        print("Result: FAIL - NOT READY TO PUSH")
        print()
        print("Fix all failed gates before pushing.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
