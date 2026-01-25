# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T14:00:00Z
# Modified by: Claude (opus-4)
# Modified at (UTC): 2026-01-25T15:00:00Z
# Purpose: News Spider smoke test v1.1 (with proper error categorization)
# === END SIGNATURE ===
"""
News Spider Smoke Test v1.1

HOTFIX: Proper error categorization - HTTP 4xx = CLIENT BUG (FAIL)

Proves STRICT/LENIENT mode behavior at runtime:
1. Source loading and AllowList validation
2. RSS parsing (using CoinDesk)
3. Deduplication across runs
4. Audit log records for all fetches
5. Health tracking and error categorization

Run: python tools/spider_smoke_test.py [--mode strict|lenient] [--dry-run]
"""

import argparse
import sys
from pathlib import Path
from typing import Tuple, List, Optional

# Setup project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.spider.collector import NewsCollector, CollectorMode, CollectorResult, run_collection, SOURCES_MUST_HAVE_ITEMS
from core.spider.sources import load_sources, get_enabled_sources
from core.spider.dedup import DedupStore
from core.spider.health import HealthTracker, categorize_error, ErrorCategory
from core.net.audit_log import read_audit_log, get_audit_stats


def show_sources():
    """Display loaded sources and AllowList status."""
    print("\n[SOURCES] Loading from config/sources_registry.json...")
    try:
        sources = load_sources()
        print(f"  Total defined: {len(sources)}")

        enabled_count = sum(1 for s in sources if s.enabled)
        print(f"  Enabled: {enabled_count}")

        print("\n  Source list:")
        for s in sources:
            status = "ON" if s.enabled else "OFF"
            print(f"    [{status}] {s.id} -> {s.host} ({s.source_type.value})")

        return sources
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


def check_allowlist(sources):
    """Check which sources are allowed by egress policy."""
    print("\n[ALLOWLIST] Checking hosts against AllowList.txt...")
    try:
        # LENIENT mode to see which are denied
        enabled = get_enabled_sources(sources, strict_mode=False)
        denied = [s for s in sources if s.enabled and s not in enabled]

        print(f"  Allowed: {len(enabled)}")
        print(f"  Denied: {len(denied)}")

        if denied:
            print("\n  Denied sources (host not in AllowList):")
            for s in denied:
                print(f"    - {s.id} ({s.host})")

        return enabled
    except Exception as e:
        print(f"  ERROR: {e}")
        return []


def categorize_source_result(source_id: str, success: bool, error: Optional[str], items_count: int) -> Tuple[str, str]:
    """
    Categorize source result into status and category.

    Returns:
        Tuple of (status_marker, category_description)
    """
    if success:
        if items_count == 0 and source_id in SOURCES_MUST_HAVE_ITEMS:
            return "[WARN]", "SILENT_FAIL (0 items from source that should have items)"
        return "[OK]", "OK"

    category = categorize_error(error)

    if category == ErrorCategory.CLIENT_BUG:
        return "[CRIT]", f"CLIENT_BUG ({error}) - THIS IS OUR BUG, MUST FIX"
    elif category == ErrorCategory.SERVER_ERROR:
        return "[WARN]", f"SERVER_ERROR ({error}) - transient, acceptable"
    elif category == ErrorCategory.PARSE_FAIL:
        return "[CRIT]", f"PARSE_FAIL ({error}) - parser bug"
    elif category == ErrorCategory.POLICY_DENY:
        return "[CRIT]", f"POLICY_DENY ({error}) - config issue"
    elif category == ErrorCategory.NETWORK:
        return "[WARN]", f"NETWORK ({error}) - transient"
    else:
        return "[WARN]", f"UNKNOWN ({error})"


def evaluate_result(result: CollectorResult, mode: str) -> Tuple[bool, str, List[str]]:
    """
    Evaluate collection result with proper error categorization.

    Returns:
        Tuple of (is_pass, summary, critical_issues)
    """
    critical_issues = []

    for sr in result.source_results:
        category = categorize_error(sr.error)

        # CLIENT_BUG (HTTP 4xx) = always critical, even in LENIENT
        if category == ErrorCategory.CLIENT_BUG:
            critical_issues.append(f"{sr.source_id}: CLIENT_BUG - {sr.error}")

        # PARSE_FAIL = critical
        elif category == ErrorCategory.PARSE_FAIL:
            critical_issues.append(f"{sr.source_id}: PARSE_FAIL - {sr.error}")

        # POLICY_DENY = config issue
        elif category == ErrorCategory.POLICY_DENY:
            critical_issues.append(f"{sr.source_id}: POLICY_DENY - {sr.error}")

        # 0 items from source that must have items = suspicious
        elif sr.success and sr.items_count == 0 and sr.source_id in SOURCES_MUST_HAVE_ITEMS:
            critical_issues.append(f"{sr.source_id}: SILENT_FAILURE - 0 items")

    # Determine pass/fail
    if critical_issues:
        return False, f"CRITICAL ISSUES: {len(critical_issues)}", critical_issues

    if mode == "strict":
        if result.sources_failed > 0:
            return False, f"STRICT: {result.sources_failed} sources failed", []
        return True, "All sources OK", []

    # LENIENT mode
    if result.sources_success == 0:
        return False, "No sources succeeded", []

    return True, f"{result.sources_success}/{result.sources_attempted} sources OK", []


def run_collection_test(mode: str, dry_run: bool):
    """Run actual collection.

    Returns:
        Tuple of (is_pass, result, critical_issues) where result is CollectorResult or None
    """
    print(f"\n[COLLECT] Running in {mode.upper()} mode (dry_run={dry_run})...")

    try:
        result = run_collection(mode=mode, dry_run=dry_run)

        print(f"\n  Started: {result.started_utc}")
        print(f"  Finished: {result.finished_utc}")
        print(f"  Sources attempted: {result.sources_attempted}")
        print(f"  Sources success: {result.sources_success}")
        print(f"  Sources failed: {result.sources_failed}")
        print(f"  Total items: {result.total_items}")
        print(f"  New items: {result.new_items}")
        print(f"  Duplicates: {result.duplicate_items}")

        if result.fatal_error:
            print(f"\n  FATAL ERROR: {result.fatal_error}")

        print("\n  Per-source results:")
        for sr in result.source_results:
            emoji, category = categorize_source_result(
                sr.source_id, sr.success, sr.error, sr.items_count
            )
            print(f"    {emoji} {sr.source_id}: {sr.items_count} items, "
                  f"{sr.new_items_count} new, {sr.latency_ms}ms")
            if not sr.success or (sr.success and sr.items_count == 0):
                print(f"           -> {category}")

        # Evaluate with proper categorization
        is_pass, summary, critical_issues = evaluate_result(result, mode)

        return (is_pass, result, critical_issues)

    except Exception as e:
        print(f"  EXCEPTION: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return (False, None, [f"Exception: {e}"])


def show_audit():
    """Show recent audit log entries."""
    print("\n[AUDIT] Last 10 egress audit entries:")
    try:
        records = read_audit_log(last_n=10)
        if not records:
            print("  (no records)")
            return

        for r in records:
            action = r.get("action", "?")
            host = r.get("host", "?")
            process = r.get("process", "?")
            latency = r.get("latency_ms", "?")
            notes = r.get("notes", "")
            line = f"  {action} | {host} | {process} | {latency}ms"
            if notes:
                line += f" | {notes}"
            print(line)

        stats = get_audit_stats()
        print(f"\n  Stats: {stats['allow_count']} ALLOW, {stats['deny_count']} DENY")
    except Exception as e:
        print(f"  Error reading audit: {e}")


def show_dedup():
    """Show dedup store stats."""
    print("\n[DEDUP] Deduplication store:")
    try:
        store = DedupStore()
        count = store.count()
        print(f"  Entries: {count}")
    except Exception as e:
        print(f"  Error: {e}")


def show_health():
    """Show source health status."""
    print("\n[HEALTH] Source health status:")
    try:
        tracker = HealthTracker()
        summary = tracker.get_summary()
        print(f"  Healthy: {summary['healthy']}")
        print(f"  Degraded: {summary['degraded']}")
        print(f"  Dead: {summary['dead']}")

        critical = tracker.get_critical_issues()
        if critical:
            print("\n  Critical issues:")
            for issue in critical:
                print(f"    [CRIT] {issue['source_id']}: {issue['category']} - {issue['error']}")
    except Exception as e:
        print(f"  Error: {e}")


def main():
    parser = argparse.ArgumentParser(description="News Spider Smoke Test v1.1")
    parser.add_argument(
        "--mode", choices=["strict", "lenient"], default="lenient",
        help="Collector mode (default: lenient)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Don't persist collected items"
    )
    parser.add_argument(
        "--skip-collect", action="store_true",
        help="Skip actual collection (only show sources/config)"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("NEWS SPIDER SMOKE TEST v1.1")
    print("=" * 60)

    # Load and display sources
    sources = show_sources()
    if not sources:
        print("\n[RESULT] FAIL - Cannot load sources")
        return 1

    # Check AllowList
    allowed = check_allowlist(sources)

    if not args.skip_collect:
        # Run collection
        is_pass, result, critical_issues = run_collection_test(args.mode, args.dry_run)

        # Show audit
        show_audit()

        # Show dedup stats
        show_dedup()

        # Show health status
        show_health()

        # Final result
        print("\n" + "=" * 60)

        if critical_issues:
            print("[CRITICAL ISSUES FOUND]")
            for issue in critical_issues:
                print(f"  [CRIT] {issue}")
            print()

        if is_pass:
            if result:
                print(f"[RESULT] SMOKE TEST PASSED ({result.sources_success}/{result.sources_attempted} sources)")
            else:
                print("[RESULT] SMOKE TEST PASSED")
            return 0
        else:
            if result:
                print(f"[RESULT] SMOKE TEST FAILED ({result.sources_failed} failed, {len(critical_issues)} critical)")
            else:
                print("[RESULT] SMOKE TEST FAILED")
            return 1
    else:
        print("\n[SKIP] Collection skipped (--skip-collect)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
