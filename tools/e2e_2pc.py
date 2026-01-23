# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-20 12:00:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 14:00:00 UTC
# === END SIGNATURE ===
"""
E2E 2-Phase Commit Test v1.0

Tests full 2PC flow: draft -> validate -> commit
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_MINIBOT_DIR = Path(__file__).resolve().parent.parent
if str(_MINIBOT_DIR) not in sys.path:
    sys.path.insert(0, str(_MINIBOT_DIR))

from core.ipc_compat import make_msg, msg_reply_to
from core.ipc_fs import IPCFileSystem
from core.two_phase_commit import save_draft, save_validate, save_commit, load_phase


def test_valid_draft():
    """Test: valid draft should be committed."""
    print("\n[TEST 1] Valid draft (1+1=2)")
    print("-" * 40)

    ipc = IPCFileSystem()
    user_id = f"test_user_{int(time.time())}"

    # Phase 1: Create draft
    draft = {"kind": "compute", "expression": "1+1", "answer": "2"}
    save_draft(user_id, draft)
    print(f"[1] Draft saved: {draft}")

    # Send validate task
    validate_task = make_msg(
        from_="gpt",
        to="claude",
        type_="task",
        payload={
            "task_type": "validate",
            "draft": draft,
            "origin_user_id": user_id,
        },
    )
    ipc.write_message("claude_validator_inbox", validate_task)
    print(f"[2] Validate task sent: {validate_task['id'][:30]}...")

    # Run validator
    from core.claude_validator_runner import run_once as validator_run
    validator_run(ipc)
    print("[3] Validator processed")

    # Check response
    responses = ipc.read_inbox("gpt_inbox", limit=100)
    matching = [r for r in responses if msg_reply_to(r) == validate_task["id"]]

    if not matching:
        print("[FAIL] No validation response")
        return False

    response = matching[0]
    ok = response["payload"]["ok"]
    print(f"[4] Validation result: ok={ok}")

    # Phase 2: Save validation
    save_validate(user_id, ok, response["payload"].get("issues", []))

    if ok:
        # Commit
        save_commit(user_id, "final_123")
        print("[5] Committed!")

        # Verify 2PC files
        d = load_phase(user_id, "draft")
        v = load_phase(user_id, "validate")
        c = load_phase(user_id, "commit")

        if d and v and c:
            print("[PASS] All 2PC phases complete")
            return True
        else:
            print(f"[FAIL] Missing phases: draft={bool(d)}, validate={bool(v)}, commit={bool(c)}")
            return False
    else:
        print("[FAIL] Validation failed (unexpected)")
        return False


def test_invalid_draft():
    """Test: invalid draft should NOT be committed."""
    print("\n[TEST 2] Invalid draft (1+1=3)")
    print("-" * 40)

    ipc = IPCFileSystem()
    user_id = f"test_invalid_{int(time.time())}"

    # Phase 1: Create BAD draft
    draft = {"kind": "compute", "expression": "1+1", "answer": "3"}  # WRONG!
    save_draft(user_id, draft)
    print(f"[1] Draft saved: {draft}")

    # Send validate task
    validate_task = make_msg(
        from_="gpt",
        to="claude",
        type_="task",
        payload={
            "task_type": "validate",
            "draft": draft,
            "origin_user_id": user_id,
        },
    )
    ipc.write_message("claude_validator_inbox", validate_task)
    print(f"[2] Validate task sent: {validate_task['id'][:30]}...")

    # Run validator
    from core.claude_validator_runner import run_once as validator_run
    validator_run(ipc)
    print("[3] Validator processed")

    # Check response
    responses = ipc.read_inbox("gpt_inbox", limit=100)
    matching = [r for r in responses if msg_reply_to(r) == validate_task["id"]]

    if not matching:
        print("[FAIL] No validation response")
        return False

    response = matching[0]
    ok = response["payload"]["ok"]
    issues = response["payload"].get("issues", [])
    print(f"[4] Validation result: ok={ok}, issues={issues}")

    # Save validation (should be failed)
    save_validate(user_id, ok, issues)

    if not ok:
        # Do NOT commit - fail-closed!
        c = load_phase(user_id, "commit")
        if c is None:
            print("[PASS] Correctly blocked - no commit for invalid draft")
            return True
        else:
            print("[FAIL] Commit exists but should not!")
            return False
    else:
        print("[FAIL] Validation passed but should have failed")
        return False


def main() -> int:
    print("=" * 60)
    print("E2E 2-Phase Commit Test v1.0")
    print("=" * 60)

    results = []

    results.append(("Valid draft", test_valid_draft()))
    results.append(("Invalid draft", test_invalid_draft()))

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    all_pass = True
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {name}: {status}")
        if not passed:
            all_pass = False

    print("=" * 60)
    print(f"Result: {'ALL PASS' if all_pass else 'SOME FAILED'}")
    print("=" * 60)

    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
