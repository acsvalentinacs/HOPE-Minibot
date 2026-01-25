# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T18:00:00Z
# Modified by: Claude (opus-4)
# Modified at (UTC): 2026-01-25T22:40:00Z
# Purpose: Push Gate v2.2 - unified release verification with strict MAINNET eligibility
# === END SIGNATURE ===
"""
Push Gate v2.2 - Unified Release Verification.

Runs ALL required gates in fixed order before allowing push.
ANY gate failure = FAIL (no exceptions, no "optional").

v2.2 Changes:
- ADDED: legacy_allowlist_guard (validates "never" policy)
- ADDED: manifest_sha256 in release_gate.json (chain-of-custody)
- FIXED: --skip-testnet now sets eligible_for_mainnet=false (mathematically strict)
- FIXED: Eligibility formula is AND of ALL requirements

Gate Order (fixed, mandatory):
1. commit_gate       - Manifest and policy validation
2. dirty_tree_guard  - No untracked/modified files
3. verify_tree       - Deterministic tree manifest
4. allowlist_guard   - AllowList format validation
5. network_guard     - No direct network outside core/net/** (AST)
6. secrets_guard     - No hardcoded secrets (v2.2 JSON-aware)
7. legacy_allowlist_guard - Legacy network allowlist policy
8. live_smoke_gate   - Trading smoke test (DRY)
9. evidence_guard    - Health file schema validation
10. testnet_gate     - TESTNET API verification
11. git push --dry-run

Output: state/health/release_gate.json

Schema: release_gate_v2
- schema_version: str
- cmdline_sha256: str
- git_head: str
- ts_utc: str
- manifest_sha256: str (from tree_manifest.json - chain-of-custody)
- eligible_for_mainnet: bool
- eligibility_reasons: [str] (why not eligible)
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


def get_manifest_sha256() -> Optional[str]:
    """
    Get SHA256 from tree_manifest.json for chain-of-custody.

    Returns:
        SHA256 of manifest file content, or None if not available
    """
    manifest_path = PROJECT_ROOT / "state" / "health" / "tree_manifest.json"
    if not manifest_path.exists():
        return None

    try:
        content = manifest_path.read_bytes()
        return hashlib.sha256(content).hexdigest()
    except OSError:
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


def check_legacy_allowlist_empty() -> Tuple[bool, int]:
    """
    Check if legacy network allowlist has no non-infra "never" entries.

    Returns:
        (is_empty, count_of_active_entries)
    """
    allowlist_path = PROJECT_ROOT / "config" / "legacy_net_allowlist.json"
    if not allowlist_path.exists():
        return True, 0  # No file = no legacy entries

    try:
        data = json.loads(allowlist_path.read_text(encoding="utf-8"))
        allowed = data.get("allowed", [])

        # Filter: only entries that have deadline (not "never") need migration
        # "never" entries for infra are OK, but "never" for non-infra are violations
        # Active entries = entries with actual deadlines (need migration)
        active_entries = [e for e in allowed if e.get("deadline") != "never"]

        # Also count invalid "never" entries (non-infra)
        infra_paths = {"tools/network_guard.py"}
        invalid_never = [
            e for e in allowed
            if e.get("deadline") == "never"
            and e.get("path", "").replace("\\", "/") not in infra_paths
        ]

        return len(active_entries) == 0 and len(invalid_never) == 0, len(active_entries) + len(invalid_never)
    except (json.JSONDecodeError, KeyError):
        return False, -1


def main() -> int:
    """Main entrypoint."""
    parser = argparse.ArgumentParser(
        description="Push Gate v2.2 - unified release verification (fail-closed)",
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
        help="Skip TESTNET gate (for offline environments) - DISABLES MAINNET ELIGIBILITY",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output JSON report only",
    )

    args = parser.parse_args()

    ts_utc = datetime.now(timezone.utc).isoformat()
    cmdline_sha256 = get_cmdline_sha256()
    git_head = get_git_head() or "unknown"

    total_gates = 11 if not args.skip_testnet else 10

    if not args.json:
        print("=" * 60)
        print("PUSH GATE v2.2 - Unified Release Verification")
        print("=" * 60)
        print(f"Timestamp: {ts_utc}")
        print(f"Root: {PROJECT_ROOT}")
        print(f"Git HEAD: {git_head[:12]}...")
        print(f"Gates: {total_gates}")
        if args.skip_testnet:
            print(f"WARNING: --skip-testnet DISABLES MAINNET eligibility")
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

    # === GATE 6: secrets_guard (v2.2 JSON-aware) ===
    run_gate("secrets_guard",
             lambda: run_python_gate("tools/secrets_guard.py", []))

    # === GATE 7: legacy_allowlist_guard (NEW in v2.2) ===
    run_gate("legacy_allowlist_guard",
             lambda: run_python_gate("tools/legacy_allowlist_guard.py", []))

    # === GATE 8: live_smoke_gate ===
    run_gate("live_smoke_gate",
             lambda: run_python_gate("tools/live_smoke_gate.py", ["--mode", "DRY"]))

    # === GATE 9: evidence_guard ===
    run_gate("evidence_guard",
             lambda: run_python_gate("tools/evidence_guard.py", ["--path", "state/health/live_trade.json"]))

    # === GATE 10: testnet_gate ===
    testnet_passed = False
    if not args.skip_testnet:
        testnet_passed = run_gate("testnet_gate",
                                   lambda: run_python_gate("tools/testnet_gate.py", []))
    else:
        gates_results.append({"name": "testnet_gate", "passed": None, "details": "SKIPPED (--skip-testnet)"})
        if not args.json:
            gate_num += 1
            print(f"[{gate_num}/{total_gates}] testnet_gate...")
            print("      SKIP (--skip-testnet)")

    # === GATE 11: git push --dry-run ===
    run_gate("git_push_dry",
             lambda: run_command(["git", "push", "--dry-run"], "git push --dry-run"))

    # Get manifest SHA256 for chain-of-custody
    manifest_sha256 = get_manifest_sha256()

    # Determine MAINNET eligibility (STRICT formula v2.2)
    eligibility_reasons: List[str] = []

    # Rule 1: All gates must pass
    if not all_passed:
        eligibility_reasons.append("Not all gates passed")

    # Rule 2: Legacy allowlist must be empty (no pending migrations)
    legacy_empty, legacy_count = check_legacy_allowlist_empty()
    if not legacy_empty:
        eligibility_reasons.append(f"Legacy network allowlist not empty ({legacy_count} entries)")

    # Rule 3: Network guard must pass
    network_gate_passed = any(g["name"] == "network_guard" and g["passed"] for g in gates_results)
    if not network_gate_passed:
        eligibility_reasons.append("Network guard failed")

    # Rule 4: Secrets guard must pass
    secrets_gate_passed = any(g["name"] == "secrets_guard" and g["passed"] for g in gates_results)
    if not secrets_gate_passed:
        eligibility_reasons.append("Secrets guard failed")

    # Rule 5: Legacy allowlist guard must pass
    legacy_guard_passed = any(g["name"] == "legacy_allowlist_guard" and g["passed"] for g in gates_results)
    if not legacy_guard_passed:
        eligibility_reasons.append("Legacy allowlist guard failed")

    # Rule 6: Testnet must be verified (not skipped) - STRICT in v2.2
    if args.skip_testnet:
        eligibility_reasons.append("Testnet gate was skipped (--skip-testnet)")
    elif not testnet_passed:
        eligibility_reasons.append("Testnet gate failed")

    # Rule 7: Manifest must exist for chain-of-custody
    if not manifest_sha256:
        eligibility_reasons.append("Tree manifest not found (chain-of-custody broken)")

    # Final eligibility: ONLY if no reasons
    eligible_for_mainnet = len(eligibility_reasons) == 0

    # Write release_gate.json
    report = {
        "schema_version": "release_gate_v2",
        "ts_utc": ts_utc,
        "cmdline_sha256": cmdline_sha256,
        "git_head": git_head,
        "manifest_sha256": manifest_sha256 or "missing",
        "eligible_for_mainnet": eligible_for_mainnet,
        "eligibility_reasons": eligibility_reasons if not eligible_for_mainnet else [],
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
        print("  Reasons:")
        for reason in eligibility_reasons:
            print(f"    - {reason}")

    if manifest_sha256:
        print(f"  Manifest SHA256: {manifest_sha256[:16]}...")

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
