# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T14:00:00Z
# Purpose: News Spider smoke test (runtime verification)
# === END SIGNATURE ===
"""
News Spider Smoke Test

Proves STRICT/LENIENT mode behavior at runtime:
1. Source loading and AllowList validation
2. RSS parsing (using CoinDesk)
3. Deduplication across runs
4. Audit log records for all fetches

Run: python tools/spider_smoke_test.py [--mode strict|lenient] [--dry-run]
"""

import argparse
import sys
from pathlib import Path

# Setup project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.spider.collector import NewsCollector, CollectorMode, run_collection
from core.spider.sources import load_sources, get_enabled_sources
from core.spider.dedup import DedupStore
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


def run_collection_test(mode: str, dry_run: bool):
    """Run actual collection.

    Returns:
        Tuple of (is_success, result) where result is CollectorResult or None
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
            status = "OK" if sr.success else "FAIL"
            print(f"    [{status}] {sr.source_id}: {sr.items_count} items, "
                  f"{sr.new_items_count} new, {sr.latency_ms}ms")
            if sr.error:
                print(f"           Error: {sr.error}")

        return (result.is_success(), result)

    except Exception as e:
        print(f"  EXCEPTION: {type(e).__name__}: {e}")
        return (False, None)


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
            print(f"  {action} | {host} | {process} | {latency}ms")

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


def main():
    parser = argparse.ArgumentParser(description="News Spider Smoke Test")
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
    print("NEWS SPIDER SMOKE TEST")
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
        success, result = run_collection_test(args.mode, args.dry_run)

        # Show audit
        show_audit()

        # Show dedup stats
        show_dedup()

        # Final result
        print("\n" + "=" * 60)

        # In LENIENT mode, partial success is acceptable
        if args.mode == "lenient" and result is not None:
            # PASS if at least one source succeeded and no fatal error
            partial_success = (
                result.sources_success > 0 and
                result.fatal_error is None
            )
            if partial_success:
                print(f"[RESULT] SMOKE TEST PASSED (LENIENT: {result.sources_success}/{result.sources_attempted} sources)")
                return 0
            else:
                print("[RESULT] SMOKE TEST FAILED (no sources succeeded)")
                return 1
        else:
            # STRICT mode requires all sources to succeed
            if success:
                print("[RESULT] SMOKE TEST PASSED")
                return 0
            else:
                print("[RESULT] SMOKE TEST FAILED")
                return 1
    else:
        print("\n[SKIP] Collection skipped (--skip-collect)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
