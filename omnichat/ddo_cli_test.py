# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T16:00:00Z
# Purpose: CLI test for DDO without TUI
# === END SIGNATURE ===
"""
DDO CLI Test - Test DDO without full TUI.

Usage:
    cd minibot
    python omnichat/test_ddo_cli.py
"""
from __future__ import annotations

import asyncio
import sys
import io
from pathlib import Path

# Fix Windows encoding
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Setup paths
OMNICHAT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = OMNICHAT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(OMNICHAT_ROOT))


async def test_ddo():
    """Run DDO test."""
    print("=" * 50)
    print("DDO CLI TEST")
    print("=" * 50)

    # Import after path setup
    from src.connectors import create_all_agents
    from src.ddo import DDOOrchestrator, DiscussionMode

    print("\n[1/3] Creating agents...")
    agents = create_all_agents()

    for name, agent in agents.items():
        status = "✓ OK" if agent.is_connected else f"✗ {agent.error_message}"
        print(f"  {name}: {status}")

    print("\n[2/3] Creating orchestrator...")
    orchestrator = DDOOrchestrator(agents)

    print("\n[3/3] Running QUICK mode test...")
    print("-" * 50)

    topic = "Простой тест: скажи OK если работаешь"
    print(f"Тема: {topic}")
    print(f"Режим: QUICK")
    print("-" * 50)

    event_count = 0
    try:
        async for event in orchestrator.run_discussion(
            topic=topic,
            mode=DiscussionMode.QUICK,
            cost_limit=10.0,  # 10 cents
            time_limit=60,    # 1 minute
        ):
            event_count += 1
            event_type = event.event_type

            if event_type == "phase_start":
                print(f"\n📍 PHASE: {event.phase.display_name} ({event.agent})")
            elif event_type == "response":
                if event.response:
                    agent = event.response.agent.upper()
                    content = event.response.content[:200]
                    if len(event.response.content) > 200:
                        content += "..."
                    print(f"\n💬 {agent}:\n{content}")
            elif event_type == "progress":
                print(f"📊 Progress: {event.message} (${event.cost_cents/100:.4f})")
            elif event_type == "guard_fail":
                print(f"⚠️ GUARD FAIL: {event.guard_name} - {event.reason}")
            elif event_type == "completed":
                status = "✅ SUCCESS" if event.success else "❌ FAILED"
                print(f"\n{status}")
                if event.context:
                    print(f"   Cost: ${event.context.cost_usd:.4f}")
                    print(f"   Time: {event.context.elapsed_str}")
                    print(f"   Messages: {event.context.response_count}")
            elif event_type == "error":
                print(f"\n❌ ERROR: {event.error}")

    except Exception as e:
        print(f"\n❌ EXCEPTION: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 50)
    print(f"Total events: {event_count}")
    print("=" * 50)


if __name__ == "__main__":
    print("Starting DDO test...\n")
    asyncio.run(test_ddo())
    print("\nDone.")
