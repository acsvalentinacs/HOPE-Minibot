# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T18:00:00Z
# Modified by: Claude (opus-4)
# Modified at (UTC): 2026-01-25T19:45:00Z
# Purpose: Spider CLI v1.3 - SSoT evidence before network I/O (fail-closed)
# === END SIGNATURE ===
"""
Spider CLI Entrypoint v1.3

CRITICAL: Evidence is generated and written to spider_health.json
BEFORE any network I/O. This is the SSoT for PASS/FAIL claims.

Evidence channel: state/health/spider_health.json

Fail-closed rules:
- Missing allowlist -> FAIL, exit 1
- Evidence write failure -> FAIL, exit 1
- PASS claim without evidence -> FAIL, exit 1

Usage:
    python -m core.spider collect --mode lenient --dry-run
    python -m core.spider publish --dry-run
    python -m core.spider full --mode lenient --dry-run
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# Setup path before imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
        ]
    )


def cmd_collect(args):
    """
    Run news collection with SSoT evidence.

    Evidence flow:
    1. Generate run_id
    2. Compute allowlist sha256
    3. Write spider_health.json (BEFORE network I/O)
    4. Print POLICY_EGRESS line
    5. Run collection
    6. Update spider_health.json with sources_result
    7. Print SOURCES_RESULT line
    """
    from core.spider.collector import run_collection
    from core.spider.policy import (
        PolicyMode, PolicyConfig, evaluate_policy,
        categorize_error, format_verdict_report, load_policy_config
    )
    from core.spider.policy import SourceResult as PolicySourceResult
    from core.spider.evidence import (
        create_initial_evidence, write_evidence, update_evidence_result,
        EvidenceError, SourcesResult, format_sources_result_line,
    )
    from core.spider.reason_codes import (
        ReasonCode, Stage, SourceFailure, map_exception_to_reason,
    )
    from core.io.atomic import atomic_write_json

    logger = logging.getLogger("spider.collect")

    # === STEP 1: Resolve allowlist path ===
    if args.allowlist:
        allowlist_path = Path(args.allowlist)
    else:
        allowlist_path = PROJECT_ROOT / "config" / "AllowList.spider.txt"

    if not allowlist_path.exists():
        print(f"FAIL: Allowlist not found: {allowlist_path}", file=sys.stderr)
        print("POLICY_EGRESS allowlist_path=MISSING allowlist_sha256=NONE ts_utc=N/A run_id=N/A")
        return 1

    # === STEP 2: Determine policy mode ===
    policy_mode = "enforced" if args.mode == "strict" else "lenient"

    # === STEP 3: Create initial evidence (BEFORE network I/O) ===
    try:
        evidence = create_initial_evidence(
            allowlist_path=allowlist_path,
            policy_mode=policy_mode,
        )
    except EvidenceError as e:
        print(f"FAIL: Cannot create evidence: {e}", file=sys.stderr)
        return 1

    # === STEP 4: Write evidence to disk (BEFORE network I/O) ===
    try:
        health_path = write_evidence(evidence, PROJECT_ROOT)
        logger.info("Evidence written: %s", health_path)
    except EvidenceError as e:
        print(f"FAIL: Cannot write evidence: {e}", file=sys.stderr)
        return 1

    # === STEP 5: Print POLICY_EGRESS line (KPI requirement) ===
    print()
    print("=== POLICY EVIDENCE (SSoT) ===")
    print(evidence.evidence_line)
    print(f"  health_file: {health_path}")
    print("=== END EVIDENCE ===")
    print()

    # === STEP 6: Load policy config ===
    policy = load_policy_config()
    policy.mode = PolicyMode(args.mode)

    logger.info("Starting collection: mode=%s, dry_run=%s, run_id=%s",
                args.mode, args.dry_run, evidence.run_id)

    # === STEP 7: Run collection (network I/O happens here) ===
    try:
        result = run_collection(mode=args.mode, dry_run=args.dry_run)

        # Convert collector results to policy evaluation format
        policy_results = []
        failures = []

        for sr in result.source_results:
            cat = categorize_error(sr.error)
            policy_results.append(PolicySourceResult(
                source_id=sr.source_id,
                success=sr.success,
                items_count=sr.items_count,
                error=sr.error,
                error_category=cat,
                latency_ms=sr.latency_ms,
            ))

            # Track failures with reason codes
            if not sr.success:
                # Map error to reason code
                reason = _map_collector_error_to_reason(sr.error)
                failures.append(SourceFailure(
                    source_id=sr.source_id,
                    reason_code=reason,
                    stage=_get_stage_from_error(sr.error),
                    retryable=_is_retryable(reason),
                    detail=sr.error[:200] if sr.error else None,
                    http_status=_extract_http_status(sr.error),
                ))

        # === STEP 8: Create sources result ===
        sources_result = SourcesResult(
            ok=result.sources_success,
            total=result.sources_attempted,
            failed=failures,
        )

        # === STEP 9: Evaluate policy ===
        verdict = evaluate_policy(policy_results, policy)

        # Determine final result
        final_result = "PASS" if verdict.passed else "FAIL"

        # === STEP 10: Update evidence with sources result ===
        try:
            update_evidence_result(sources_result, final_result, PROJECT_ROOT)
        except EvidenceError as e:
            logger.error("Failed to update evidence: %s", e)
            # This is a FAIL even if collection succeeded
            final_result = "FAIL"

        # === STEP 11: Print SOURCES_RESULT line (KPI requirement) ===
        print()
        sources_line = format_sources_result_line(sources_result)
        print(sources_line)
        print()

        # === STEP 12: Print verdict report ===
        print(format_verdict_report(verdict))

        # === STEP 13: Save manifest ===
        manifest = {
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "run_id": evidence.run_id,
            "policy_egress": evidence.policy_egress.to_dict(),
            "mode": args.mode,
            "dry_run": args.dry_run,
            "sources_result": sources_result.to_dict(),
            "result": result.to_dict(),
            "verdict": {
                "passed": verdict.passed,
                "reason": verdict.reason,
                "exit_code": verdict.exit_code,
            }
        }
        manifest_path = PROJECT_ROOT / "state" / "spider_manifest.json"
        atomic_write_json(manifest_path, manifest)
        logger.info("Manifest saved: %s", manifest_path)

        return verdict.exit_code

    except Exception as e:
        logger.error("Collection failed: %s", e)
        import traceback
        traceback.print_exc()

        # Update evidence with FAIL
        try:
            reason = map_exception_to_reason(e, str(e))
            sources_result = SourcesResult(ok=0, total=0, failed=[
                SourceFailure(
                    source_id="collector",
                    reason_code=reason,
                    stage=Stage.FETCH,
                    retryable=False,
                    detail=str(e)[:200],
                )
            ])
            update_evidence_result(sources_result, "FAIL", PROJECT_ROOT)
        except Exception:
            pass

        return 1


def _map_collector_error_to_reason(error: str):
    """Map collector error string to ReasonCode."""
    from core.spider.reason_codes import ReasonCode

    if not error:
        return ReasonCode.UNKNOWN_ERROR

    error_lower = error.lower()

    # HTTP status codes
    if "http 5" in error_lower:
        return ReasonCode.HTTP_STATUS_5XX
    if "http 4" in error_lower:
        return ReasonCode.HTTP_STATUS_4XX
    if "http 429" in error_lower:
        return ReasonCode.RATE_LIMITED

    # Timeout
    if "timeout" in error_lower:
        return ReasonCode.HTTP_TIMEOUT

    # Policy
    if "egress denied" in error_lower or "not_in_allowlist" in error_lower:
        return ReasonCode.POLICY_URL_NOT_ALLOWED
    if "redirect" in error_lower:
        return ReasonCode.POLICY_REDIRECT_NOT_ALLOWED

    # Parse
    if "parse" in error_lower:
        if "xml" in error_lower:
            return ReasonCode.PARSE_INVALID_XML
        if "json" in error_lower:
            return ReasonCode.PARSE_INVALID_JSON
        return ReasonCode.PARSE_MISSING_REQUIRED_FIELDS

    # Network
    if "connection" in error_lower or "connect" in error_lower:
        return ReasonCode.CONNECT_TIMEOUT
    if "dns" in error_lower:
        return ReasonCode.DNS_FAIL
    if "ssl" in error_lower or "tls" in error_lower:
        return ReasonCode.TLS_HANDSHAKE_FAIL

    return ReasonCode.UNKNOWN_ERROR


def _get_stage_from_error(error: str):
    """Determine stage from error string."""
    from core.spider.reason_codes import Stage

    if not error:
        return Stage.FETCH

    error_lower = error.lower()

    if "egress" in error_lower or "allowlist" in error_lower or "policy" in error_lower:
        return Stage.POLICY
    if "parse" in error_lower or "xml" in error_lower or "json" in error_lower:
        return Stage.PARSE
    if "write" in error_lower or "storage" in error_lower:
        return Stage.WRITE

    return Stage.FETCH


def _is_retryable(reason) -> bool:
    """Check if error is retryable."""
    from core.spider.reason_codes import REASON_CODE_REGISTRY
    info = REASON_CODE_REGISTRY.get(reason)
    return info.retryable if info else False


def _extract_http_status(error: str) -> int | None:
    """Extract HTTP status code from error string."""
    import re
    if not error:
        return None

    match = re.search(r'HTTP (\d{3})', error, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def cmd_publish(args):
    """Run Telegram publish for recent news."""
    from core.spider.telegram_bridge import SpiderTelegramBridge
    from core.spider.evidence import load_and_validate_evidence, EvidenceError

    logger = logging.getLogger("spider.publish")

    # === Validate evidence exists before publish ===
    try:
        evidence = load_and_validate_evidence(PROJECT_ROOT, enforced=False)
        print()
        print("=== LOADED EVIDENCE ===")
        print(f"run_id: {evidence.get('run_id', 'N/A')}")
        print(f"evidence_line: {evidence.get('evidence_line', 'N/A')}")
        print("=== END EVIDENCE ===")
        print()
    except EvidenceError as e:
        logger.warning("No valid evidence found: %s", e)
        # Continue anyway for publish-only mode

    logger.info("Starting publish: dry_run=%s, threshold=%s", args.dry_run, args.threshold)

    try:
        bridge = SpiderTelegramBridge()
        result = bridge.publish_recent_news(
            dry_run=args.dry_run,
            impact_threshold=args.threshold,
        )

        print(f"\n=== PUBLISH RESULT ===")
        print(f"Success: {result.success}")
        print(f"Items processed: {result.items_processed}")
        print(f"Items published: {result.items_published}")
        print(f"Items skipped: {result.items_skipped}")
        print(f"Items already published: {result.items_already_published}")
        if result.error:
            print(f"Error: {result.error}")

        return 0 if result.success else 1

    except Exception as e:
        logger.error("Publish failed: %s", e)
        import traceback
        traceback.print_exc()
        return 1


def cmd_full(args):
    """Run full cycle: collect + publish."""
    from core.spider.telegram_bridge import SpiderTelegramBridge
    from core.spider.evidence import (
        create_initial_evidence, write_evidence, update_evidence_result,
        EvidenceError, SourcesResult,
    )
    from core.io.atomic import AtomicFileLock

    logger = logging.getLogger("spider.full")

    # Acquire lock
    lock_path = PROJECT_ROOT / "state" / "spider.lock"
    lock = AtomicFileLock(lock_path)

    if not lock.acquire(timeout_sec=10):
        logger.error("FAIL: Could not acquire lock (another instance running?)")
        return 42

    try:
        # Resolve allowlist
        if args.allowlist:
            allowlist_path = Path(args.allowlist)
        else:
            allowlist_path = PROJECT_ROOT / "config" / "AllowList.spider.txt"

        if not allowlist_path.exists():
            print(f"FAIL: Allowlist not found: {allowlist_path}", file=sys.stderr)
            return 1

        policy_mode = "enforced" if args.mode == "strict" else "lenient"

        # Create and write evidence BEFORE network I/O
        try:
            evidence = create_initial_evidence(allowlist_path, policy_mode)
            health_path = write_evidence(evidence, PROJECT_ROOT)
        except EvidenceError as e:
            print(f"FAIL: Evidence error: {e}", file=sys.stderr)
            return 1

        print()
        print("=== POLICY EVIDENCE (SSoT) ===")
        print(evidence.evidence_line)
        print("=== END EVIDENCE ===")
        print()

        logger.info("Starting full cycle: mode=%s, dry_run=%s, run_id=%s",
                    args.mode, args.dry_run, evidence.run_id)

        bridge = SpiderTelegramBridge()
        result = bridge.run_full_cycle(
            collect_mode=args.mode,
            dry_run=args.dry_run,
        )

        print(f"\n=== FULL CYCLE RESULT ===")
        print(json.dumps(result, indent=2, default=str))

        # Determine exit code
        if result.get("status") == "error":
            return 1
        if result.get("errors"):
            return 1
        return 0

    except Exception as e:
        logger.error("Full cycle failed: %s", e)
        import traceback
        traceback.print_exc()
        return 1

    finally:
        lock.release()


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="HOPE News Spider v1.3 (SSoT Evidence)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # collect command
    p_collect = subparsers.add_parser("collect", help="Collect news from sources")
    p_collect.add_argument(
        "--mode", "-m", choices=["strict", "lenient"], default="lenient",
        help="Policy mode (default: lenient)"
    )
    p_collect.add_argument(
        "--dry-run", "-d", action="store_true",
        help="Don't persist items"
    )
    p_collect.add_argument(
        "--allowlist", "-a", type=str,
        help="Path to allowlist file (default: config/AllowList.spider.txt)"
    )

    # publish command
    p_publish = subparsers.add_parser("publish", help="Publish news to Telegram")
    p_publish.add_argument(
        "--dry-run", "-d", action="store_true",
        help="Format only, don't send"
    )
    p_publish.add_argument(
        "--threshold", "-t", type=float, default=0.6,
        help="Impact threshold (default: 0.6)"
    )

    # full command
    p_full = subparsers.add_parser("full", help="Full cycle: collect + publish")
    p_full.add_argument(
        "--mode", "-m", choices=["strict", "lenient"], default="lenient",
        help="Policy mode (default: lenient)"
    )
    p_full.add_argument(
        "--dry-run", "-d", action="store_true",
        help="Don't persist or publish"
    )
    p_full.add_argument(
        "--allowlist", "-a", type=str,
        help="Path to allowlist file"
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.command == "collect":
        return cmd_collect(args)
    elif args.command == "publish":
        return cmd_publish(args)
    elif args.command == "full":
        return cmd_full(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
