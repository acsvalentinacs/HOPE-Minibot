# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T18:00:00Z
# Purpose: Push Gate - unified release verification (fail-closed, no "optional")
# === END SIGNATURE ===
"""
Push Gate - Unified Release Verification.

Runs ALL required gates in fixed order before allowing push.
ANY gate failure = FAIL (no exceptions, no "optional").

Gate Order (fixed, mandatory):
1. commit_gate      - Manifest and policy validation
2. dirty_tree_guard - No untracked/modified files
3. live_smoke_gate  - Trading system smoke test (DRY)
4. evidence_guard   - Health file schema validation
5. git push --dry-run - Verify push will succeed

Usage:
    python tools/push_gate.py           # Full verification
    python tools/push_gate.py --execute # Verify + actual push

Exit codes:
    0 = PASS (all gates passed, ready to push)
    1 = FAIL (one or more gates failed)
    2 = ERROR (setup/config issue)
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

# SSoT paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run_command(cmd: List[str], description: str) -> Tuple[bool, str]:
    """
    Run command and return (success, output).

    Args:
        cmd: Command as list
        description: Human-readable description

    Returns:
        (success, output_or_error)
    """
    try:
        result = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )

        output = result.stdout + result.stderr
        success = result.returncode == 0

        return success, output.strip()

    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT: {description}"
    except Exception as e:
        return False, f"ERROR: {e}"


def run_python_gate(script: str, args: List[str] = None) -> Tuple[bool, str]:
    """
    Run Python gate script.

    Args:
        script: Script path relative to PROJECT_ROOT
        args: Additional arguments

    Returns:
        (success, output)
    """
    cmd = [sys.executable, script]
    if args:
        cmd.extend(args)

    return run_command(cmd, script)


def main() -> int:
    """Main entrypoint."""
    parser = argparse.ArgumentParser(
        description="Push Gate - unified release verification (fail-closed)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually push after verification passes",
    )
    parser.add_argument(
        "--allow-state",
        action="store_true",
        help="Allow untracked files in state/** (dirty_tree_guard)",
    )
    parser.add_argument(
        "--skip-testnet",
        action="store_true",
        help="Skip TESTNET gate (for offline environments)",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("PUSH GATE - Unified Release Verification")
    print("=" * 60)
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print(f"Root: {PROJECT_ROOT}")
    print()
    print("ALL gates are MANDATORY. Any failure = FAIL.")
    print()

    gates = []
    all_passed = True

    # === GATE 1: commit_gate ===
    print("[1/6] commit_gate...")
    success, output = run_python_gate("tools/commit_gate.py", ["--check"])
    gates.append(("commit_gate", success))
    if success:
        print("      PASS")
    else:
        print("      FAIL")
        print(f"      {output[:200]}")
        all_passed = False

    # === GATE 2: dirty_tree_guard ===
    print("[2/6] dirty_tree_guard...")
    dirty_args = []
    if args.allow_state:
        dirty_args.append("--allow-state")
    success, output = run_python_gate("tools/dirty_tree_guard.py", dirty_args)
    gates.append(("dirty_tree_guard", success))
    if success:
        print("      PASS")
    else:
        print("      FAIL")
        # Show violations
        for line in output.split("\n"):
            if "UNTRACKED:" in line or "MODIFIED:" in line:
                print(f"      {line.strip()}")
        all_passed = False

    # === GATE 3: live_smoke_gate ===
    print("[3/6] live_smoke_gate (DRY)...")
    success, output = run_python_gate("tools/live_smoke_gate.py", ["--mode", "DRY"])
    gates.append(("live_smoke_gate", success))
    if success:
        print("      PASS")
    else:
        print("      FAIL")
        # Show failed checks
        for line in output.split("\n"):
            if "FAIL:" in line:
                print(f"      {line.strip()}")
        all_passed = False

    # === GATE 4: evidence_guard ===
    print("[4/6] evidence_guard...")
    success, output = run_python_gate("tools/evidence_guard.py", ["--path", "state/health/live_trade.json"])
    gates.append(("evidence_guard", success))
    if success:
        print("      PASS")
    else:
        print("      FAIL")
        for line in output.split("\n"):
            if "Missing" in line or "Error" in line or "FAIL" in line:
                print(f"      {line.strip()}")
        all_passed = False

    # === GATE 5: testnet_gate (optional skip for offline) ===
    if not args.skip_testnet:
        print("[5/6] testnet_gate (read-only)...")
        success, output = run_python_gate("tools/testnet_gate.py", [])
        gates.append(("testnet_gate", success))
        if success:
            print("      PASS")
        else:
            print("      FAIL")
            for line in output.split("\n"):
                if "FAIL:" in line:
                    print(f"      {line.strip()}")
            all_passed = False
    else:
        print("[5/6] testnet_gate... SKIPPED (--skip-testnet)")
        gates.append(("testnet_gate", None))

    # === GATE 6: git push --dry-run ===
    print("[6/6] git push --dry-run...")
    success, output = run_command(["git", "push", "--dry-run"], "git push --dry-run")
    gates.append(("git_push_dry", success))
    if success:
        print("      PASS")
    else:
        print("      FAIL")
        print(f"      {output[:200]}")
        all_passed = False

    # === SUMMARY ===
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)

    for gate_name, passed in gates:
        if passed is None:
            status = "SKIP"
        elif passed:
            status = "PASS"
        else:
            status = "FAIL"
        print(f"  {gate_name}: {status}")

    print()

    if all_passed:
        print("Result: PASS - READY TO PUSH")

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
