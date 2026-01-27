# Quick test for Market Intel module
import asyncio
import sys
sys.path.insert(0, "omnichat")

async def test():
    from src.market_intel import MarketIntel, fetch_market_snapshot

    print("=== MARKET INTEL TEST ===")
    print()

    intel = MarketIntel()

    print("Fetching market snapshot...")
    try:
        snapshot = await intel.get_snapshot(force_refresh=True)
        print(f"[OK] Snapshot ID: {snapshot.snapshot_id}")
        print(f"[OK] BTC: ${snapshot.btc_price:,.2f}")
        print(f"[OK] ETH: ${snapshot.eth_price:,.2f}")
        print(f"[OK] Tickers: {len(snapshot.tickers)}")
        print(f"[OK] News: {len(snapshot.news)}")
        print(f"[OK] Duration: {snapshot.fetch_duration_ms}ms")

        if snapshot.global_metrics:
            m = snapshot.global_metrics
            print(f"[OK] Market Cap: ${m.total_market_cap_usd/1e12:.2f}T")
            print(f"[OK] BTC Dom: {m.btc_dominance_pct:.1f}%")

        # Get summary
        summary = intel.get_summary(snapshot)
        print(f"[OK] Sentiment: {summary['overall_sentiment']}")
        print(f"[OK] Confidence: {summary['confidence']*100:.0f}%")

        if snapshot.errors:
            print(f"[WARN] Errors: {snapshot.errors}")

        print()
        print("=== TEST PASSED ===")

    except Exception as e:
        print(f"[FAIL] Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(test()))
