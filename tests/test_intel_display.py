# Test Market Intel display output
import asyncio
import sys
import io

# Fix Windows encoding
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, "omnichat")

async def test():
    from src.market_intel import MarketIntel
    intel = MarketIntel()
    print("Fetching data...")
    snapshot = await intel.get_snapshot()
    print()
    print(intel.format_snapshot(snapshot))

asyncio.run(test())
