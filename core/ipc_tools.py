"""
IPC Tools - Operator CLI for HOPE IPC system.

Commands:
    python -m core.ipc_tools send --to=claude --task_type=math --expression="2+2"
    python -m core.ipc_tools send --to=claude --task_type=ping
    python -m core.ipc_tools send --to=claude --task_type=status
    python -m core.ipc_tools send --to=gpt --task_type=echo --data="hello"

    python -m core.ipc_tools tail --role=gpt [--limit=10]
    python -m core.ipc_tools tail --role=claude [--limit=10]

    python -m core.ipc_tools stats --role=claude
    python -m core.ipc_tools stats --role=gpt

    python -m core.ipc_tools process --role=claude
    python -m core.ipc_tools process --role=gpt

    python -m core.ipc_tools scan [--top=10]

    python -m core.ipc_tools clean --deadletter [--days=7]
    python -m core.ipc_tools clean --outbox --role=claude [--days=1]
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.ipc_agent import (
    BASE_DIR,
    CLAUDE_INBOX,
    CLAUDE_OUTBOX,
    DEADLETTER,
    GPT_INBOX,
    GPT_OUTBOX,
    ClaudeAgent,
    GPTAgent,
    MessageType,
    Sender,
    _atomic_write,
    _generate_id_from_message_fields,
    init_ipc_folders,
    setup_ipc_logging,
)
def _parse_args(argv: List[str]) -> Dict[str, str]:
    """Parse command line arguments.

    Expects argv to be sys.argv-style (argv[0] is script name).
    """
    args: Dict[str, str] = {}
    positional_idx = 0

    for a in argv[1:]:
        if a.startswith("--"):
            if "=" in a:
                k, v = a[2:].split("=", 1)
                args[k.strip()] = v.strip()
            else:
                args[a[2:]] = "true"
        elif positional_idx == 0:
            args["command"] = a
            positional_idx += 1

    return args


def cmd_send(args: Dict[str, str]) -> int:
    """Send a task to specified agent."""
    to = args.get("to", "").lower()
    task_type = args.get("task_type", "ping")

    if to not in ("claude", "gpt"):
        print(f"ERROR: --to must be 'claude' or 'gpt', got '{to}'")
        return 1

    # Build payload
    payload: Dict[str, Any] = {"task_type": task_type}

    # Add optional fields based on task type
    if "expression" in args:
        payload["expression"] = args["expression"]
    if "data" in args:
        payload["data"] = args["data"]
    if "message" in args:
        payload["message"] = args["message"]
    if "path" in args:
        payload["path"] = args["path"]
    if "pattern" in args:
        payload["pattern"] = args["pattern"]
    if "checks" in args:
        payload["checks"] = args["checks"].split(",")

    # Determine sender and recipient
    if to == "claude":
        sender = Sender.GPT.value
        recipient = Sender.CLAUDE.value
        inbox = CLAUDE_INBOX
    else:
        sender = Sender.CLAUDE.value
        recipient = Sender.GPT.value
        inbox = GPT_INBOX

    # Build message
    ts = time.time()
    fields = {
        "from": sender,
        "to": recipient,
        "timestamp": ts,
        "type": MessageType.TASK.value,
        "payload": payload,
    }

    msg_id = _generate_id_from_message_fields(fields)
    msg = dict(fields, id=msg_id)

    # Write to inbox
    init_ipc_folders()
    fname = f"{msg_id[7:23]}_{msg_id[-8:]}.json"
    dst = inbox / fname
    _atomic_write(dst, json.dumps(msg, indent=2, ensure_ascii=False))

    print(f"SENT: {msg_id[:32]}...")
    print(f"  to: {recipient}")
    print(f"  task_type: {task_type}")
    print(f"  payload: {payload}")
    print(f"  file: {dst.name}")

    return 0


def cmd_tail(args: Dict[str, str]) -> int:
    """Tail responses for specified role."""
    role = args.get("role", "").lower()
    limit = int(args.get("limit", "10"))

    if role not in ("claude", "gpt"):
        print(f"ERROR: --role must be 'claude' or 'gpt', got '{role}'")
        return 1

    # Select inbox based on role (responses come to inbox)
    if role == "claude":
        inbox = CLAUDE_INBOX
        outbox = CLAUDE_OUTBOX
    else:
        inbox = GPT_INBOX
        outbox = GPT_OUTBOX

    print(f"=== INBOX for {role} (newest first) ===")

    inbox_files = sorted(inbox.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

    if not inbox_files:
        print("(empty)")
    else:
        for f in inbox_files[:limit]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                ts = datetime.fromtimestamp(data.get("timestamp", 0), tz=timezone.utc)
                msg_type = data.get("type", "?")
                sender = data.get("from", "?")

                print(f"\n[{ts.strftime('%H:%M:%S')}] {msg_type.upper()} from {sender}")
                print(f"  id: {data.get('id', '?')[:32]}...")

                if msg_type == "response":
                    print(f"  payload: {json.dumps(data.get('payload', {}), ensure_ascii=False)[:200]}")
                elif msg_type == "error":
                    print(f"  error: {data.get('payload', {}).get('error', '?')}")
                elif msg_type == "ack":
                    print(f"  acked: {data.get('payload', {}).get('acked', '?')[:32]}...")

            except Exception as e:
                print(f"  ERROR reading {f.name}: {e}")

    print(f"\n=== OUTBOX for {role} (newest first) ===")

    outbox_files = sorted(outbox.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

    if not outbox_files:
        print("(empty)")
    else:
        for f in outbox_files[:limit]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                ts = datetime.fromtimestamp(data.get("timestamp", 0), tz=timezone.utc)
                msg_type = data.get("type", "?")
                recipient = data.get("to", "?")

                print(f"\n[{ts.strftime('%H:%M:%S')}] {msg_type.upper()} to {recipient}")
                print(f"  id: {data.get('id', '?')[:32]}...")

            except Exception as e:
                print(f"  ERROR reading {f.name}: {e}")

    return 0


def cmd_stats(args: Dict[str, str]) -> int:
    """Show agent stats."""
    role = args.get("role", "").lower()

    if role not in ("claude", "gpt"):
        print(f"ERROR: --role must be 'claude' or 'gpt', got '{role}'")
        return 1

    init_ipc_folders()
    setup_ipc_logging()

    if role == "claude":
        agent = ClaudeAgent()
    else:
        agent = GPTAgent()

    stats = agent.get_stats()

    print(f"=== {role.upper()} AGENT STATS ===")
    print(json.dumps(stats, indent=2, ensure_ascii=False))

    return 0


def cmd_clean(args: Dict[str, str]) -> int:
    """Clean old files from deadletter or outbox."""
    days = int(args.get("days", "7"))
    cutoff = time.time() - (days * 24 * 3600)

    cleaned = 0

    if "deadletter" in args:
        print(f"Cleaning deadletter older than {days} days...")
        for f in DEADLETTER.glob("*"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    cleaned += 1
            except Exception as e:
                print(f"  ERROR: {f.name}: {e}")

    if "outbox" in args:
        role = args.get("role", "").lower()
        if role == "claude":
            outbox = CLAUDE_OUTBOX
        elif role == "gpt":
            outbox = GPT_OUTBOX
        else:
            print("ERROR: --role required for outbox cleanup")
            return 1

        print(f"Cleaning {role} outbox older than {days} days...")
        for f in outbox.glob("*.json"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    cleaned += 1
            except Exception as e:
                print(f"  ERROR: {f.name}: {e}")

    print(f"Cleaned {cleaned} files")
    return 0


def cmd_process(args: Dict[str, str]) -> int:
    """Run one processing cycle for an agent."""
    role = args.get("role", "").lower()

    if role not in ("claude", "gpt"):
        print(f"ERROR: --role must be 'claude' or 'gpt', got '{role}'")
        return 1

    init_ipc_folders()
    setup_ipc_logging()

    if role == "claude":
        agent = ClaudeAgent()
    else:
        agent = GPTAgent()

    result = agent.process_cycle()

    print(f"=== {role.upper()} CYCLE RESULT ===")
    print(json.dumps(result, indent=2, ensure_ascii=False))

    return 0


SCAN_TRIGGER_PHRASES = {"Чат друзей", "chat_friends"}


def cmd_scan(args: Dict[str, str]) -> int:
    """Scan market data and news feeds.

    Requires trigger phrase for safety: --trigger="Чат друзей" or --trigger="chat_friends"
    """
    import asyncio
    from core.intel_pipeline import IntelPipeline

    trigger = args.get("trigger", "")
    if trigger not in SCAN_TRIGGER_PHRASES:
        print("FAIL-CLOSED: scan requires trigger phrase")
        print('Usage: python -m core.ipc_tools scan --trigger="chat_friends"')
        print('   or: python -m core.ipc_tools scan --trigger="Чат друзей"')
        return 1

    top_n = int(args.get("top", "10"))

    print("=== MARKET INTEL SCAN ===")
    print(f"Trigger: {trigger} [OK]")
    print("Fetching data (this may take 10-30 seconds)...")

    pipeline = IntelPipeline(BASE_DIR)

    try:
        result = asyncio.run(pipeline.scan_all(top_n=top_n))
    except Exception as e:
        print(f"SCAN FAILED: {e}")
        return 1

    print(f"\nTimestamp: {datetime.fromtimestamp(result.timestamp, tz=timezone.utc).isoformat()}")
    print(f"Market snapshot: {result.market_snapshot_id[:32]}...")

    if result.top_gainers:
        print("\n--- TOP GAINERS ---")
        for m in result.top_gainers[:5]:
            print(f"  {m.symbol}: +{m.price_change_pct:.2f}% (vol: ${m.volume_usd:,.0f})")

    if result.top_losers:
        print("\n--- TOP LOSERS ---")
        for m in result.top_losers[:5]:
            print(f"  {m.symbol}: {m.price_change_pct:.2f}% (vol: ${m.volume_usd:,.0f})")

    if result.top_volume:
        print("\n--- TOP VOLUME ---")
        for m in result.top_volume[:5]:
            print(f"  {m.symbol}: ${m.volume_usd:,.0f} ({m.price_change_pct:+.2f}%)")

    if result.news_items:
        print(f"\n--- HIGH IMPACT NEWS ({len(result.news_items)}) ---")
        for n in result.news_items[:10]:
            print(f"  [{n.event_type}:{n.impact_score}] {n.title[:70]}...")
            print(f"    source: {n.source} | {n.link[:50]}...")

    if result.errors:
        print(f"\n--- ERRORS ({len(result.errors)}) ---")
        for e in result.errors:
            print(f"  - {e}")

    print(f"\nResult saved to: state/market_intel.json")
    return 0


def cmd_help() -> int:
    """Show help."""
    print(__doc__)
    print("\nExamples:")
    print("  # Send math task to Claude")
    print('  python -m core.ipc_tools send --to=claude --task_type=math --expression="2+2"')
    print()
    print("  # Send ping to Claude")
    print("  python -m core.ipc_tools send --to=claude --task_type=ping")
    print()
    print("  # View GPT inbox/outbox")
    print("  python -m core.ipc_tools tail --role=gpt")
    print()
    print("  # Get Claude agent stats")
    print("  python -m core.ipc_tools stats --role=claude")
    print()
    print("  # Run one Claude processing cycle")
    print("  python -m core.ipc_tools process --role=claude")
    print()
    print("  # Scan market data and news (requires trigger)")
    print('  python -m core.ipc_tools scan --trigger="chat_friends" --top=10')
    print()
    print("  # Health check - active mode (ACK roundtrip test, for dev)")
    print("  python -m core.ipc_tools health")
    print()
    print("  # Health check - passive mode (read-only stats, safe for prod)")
    print("  python -m core.ipc_tools health --passive")
    print()
    print("  # Clean old deadletter files")
    print("  python -m core.ipc_tools clean --deadletter --days=7")
    return 0


def cmd_health(args: Dict[str, str]) -> int:
    """Health check: verify IPC agents are running.

    Modes:
        --passive    Read-only stats, no message injection (safe for production)
        (default)    Active test with ACK roundtrip (for development)
    """
    init_ipc_folders()
    setup_ipc_logging()

    passive = "passive" in args

    if passive:
        print("=== IPC HEALTH CHECK [PASSIVE] ===\n")
    else:
        print("=== IPC HEALTH CHECK [ACTIVE] ===\n")

    # Get stats for both agents
    claude = ClaudeAgent()
    gpt = GPTAgent()

    claude_stats = claude.get_stats()
    gpt_stats = gpt.get_stats()

    # Check inbox/outbox activity
    claude_inbox_count = claude_stats["inbox_pending"]
    claude_outbox_count = claude_stats["outbox_pending"]
    gpt_inbox_count = gpt_stats["inbox_pending"]
    gpt_outbox_count = gpt_stats["outbox_pending"]

    print("CLAUDE AGENT:")
    print(f"  inbox_pending: {claude_inbox_count}")
    print(f"  outbox_pending: {claude_outbox_count}")
    print(f"  pending_acks: {claude_stats['pending_acks_count']}")
    print(f"  processed_total: {claude_stats['processed_count']}")
    print(f"  debug_enabled: {claude.is_debug_enabled()}")

    print("\nGPT AGENT:")
    print(f"  inbox_pending: {gpt_inbox_count}")
    print(f"  outbox_pending: {gpt_outbox_count}")
    print(f"  pending_acks: {gpt_stats['pending_acks_count']}")
    print(f"  processed_total: {gpt_stats['processed_count']}")
    print(f"  debug_enabled: {gpt.is_debug_enabled()}")

    print(f"\nDEADLETTER: {claude_stats['deadletter_count']} files")

    # In passive mode, only report stats (FAIL-CLOSED: strict thresholds)
    if passive:
        # FAIL-CLOSED: pending_acks must be 0, deadletter must be 0
        issues = []
        if claude_stats['pending_acks_count'] > 0:
            issues.append(f"Claude pending_acks={claude_stats['pending_acks_count']} (must be 0)")
        if gpt_stats['pending_acks_count'] > 0:
            issues.append(f"GPT pending_acks={gpt_stats['pending_acks_count']} (must be 0)")
        if claude_stats['deadletter_count'] > 0:
            issues.append(f"deadletter={claude_stats['deadletter_count']} (must be 0)")

        if issues:
            print("\n[FAIL] Issues detected (fail-closed):")
            for issue in issues:
                print(f"  - {issue}")
            return 1
        else:
            print("\n[PASS] ACK-контур замкнут (pending_acks=0, deadletter=0)")
            return 0

    # Active mode: ACK roundtrip test
    print("\n--- ACK ROUNDTRIP TEST ---")

    # Send ping from GPT to Claude
    test_id = gpt.send_task("ping", {})
    print(f"Sent ping: {test_id[:32]}...")

    # Process Claude side
    c1 = claude.process_cycle()
    print(f"Claude processed: {c1['processed']} messages")

    # Process GPT side (receive response, send ACK)
    g1 = gpt.process_cycle()
    print(f"GPT processed: {g1['processed']} messages")

    # Process Claude side (receive ACK)
    c2 = claude.process_cycle()
    print(f"Claude processed ACK: {c2['processed']} messages")

    # Final check
    final_pending = claude.get_stats()["pending_acks_count"]
    print(f"\nFinal pending_acks: {final_pending}")

    if final_pending == 0:
        print("\n[OK] ACK roundtrip successful")
        return 0
    else:
        print("\n[WARN] pending_acks > 0 - agents may not be running as services")
        return 1


def cmd_chat(args: Dict[str, str]) -> int:
    """Interactive chat mode for friend chat.

    Usage:
        python -m core.ipc_tools chat --to=claude --message="What's next?"
        python -m core.ipc_tools chat --to=gpt --message="Analyze this"
    """
    to = args.get("to", "").lower()
    message = args.get("message", "").strip()

    if to not in ("claude", "gpt"):
        print("ERROR: --to must be 'claude' or 'gpt'")
        return 1

    if not message:
        print("ERROR: --message is required")
        print('Usage: python -m core.ipc_tools chat --to=claude --message="your message"')
        return 1

    init_ipc_folders()
    setup_ipc_logging()

    # Create payload for chat task
    payload = {
        "task_type": "chat",
        "message": message,
        "context": "friend_chat",
    }

    # Determine sender and recipient
    if to == "claude":
        sender = Sender.GPT.value
        recipient = Sender.CLAUDE.value
        inbox = CLAUDE_INBOX
    else:
        sender = Sender.CLAUDE.value
        recipient = Sender.GPT.value
        inbox = GPT_INBOX

    ts = time.time()
    msg_data = {
        "from": sender,
        "to": recipient,
        "timestamp": ts,
        "type": MessageType.TASK.value,
        "payload": payload,
    }

    msg_id = _generate_id_from_message_fields(msg_data)
    msg_data["id"] = msg_id

    filename = f"{msg_id[7:23]}_{int(ts * 1000) % 100000000:08x}.json"
    filepath = inbox / filename

    content = json.dumps(msg_data, ensure_ascii=False, sort_keys=True, indent=2)
    _atomic_write(filepath, content)

    print(f"CHAT -> {to.upper()}")
    print(f"  id: {msg_id[:32]}...")
    print(f"  message: {message[:100]}{'...' if len(message) > 100 else ''}")
    print(f"  file: {filename}")

    return 0


def cmd_status(args: Dict[str, str]) -> int:
    """Quick status check for friend chat.

    Shows both agents' status in compact form.
    """
    init_ipc_folders()
    setup_ipc_logging()

    claude = ClaudeAgent()
    gpt = GPTAgent()

    cs = claude.get_stats()
    gs = gpt.get_stats()

    # Compact status
    print("=== FRIEND CHAT STATUS ===")
    print(f"Claude: inbox={cs['inbox_pending']} out={cs['outbox_pending']} ack={cs['pending_acks_count']} proc={cs['processed_count']}")
    print(f"GPT:    inbox={gs['inbox_pending']} out={gs['outbox_pending']} ack={gs['pending_acks_count']} proc={gs['processed_count']}")
    print(f"Dead:   {cs['deadletter_count']}")

    # Health indicator
    healthy = cs['pending_acks_count'] == 0 and gs['pending_acks_count'] == 0 and cs['deadletter_count'] == 0
    print(f"\nHealth: {'OK' if healthy else 'ISSUES'}")

    return 0 if healthy else 1


def cmd_ask(args: Dict[str, str]) -> int:
    """Send question to GPT and wait for answer.

    Usage:
        python -m core.ipc_tools ask --question="What is BTC doing today?"
    """
    question = args.get("question", "").strip()

    if not question:
        print("ERROR: --question is required")
        print('Usage: python -m core.ipc_tools ask --question="Your question"')
        return 1

    init_ipc_folders()
    setup_ipc_logging()

    # Send ask task to GPT
    payload = {
        "task_type": "ask",
        "question": question,
    }

    ts = time.time()
    msg_data = {
        "from": Sender.CLAUDE.value,
        "to": Sender.GPT.value,
        "timestamp": ts,
        "type": MessageType.TASK.value,
        "payload": payload,
    }

    msg_id = _generate_id_from_message_fields(msg_data)
    msg_data["id"] = msg_id

    filename = f"{msg_id[7:23]}_{int(ts * 1000) % 100000000:08x}.json"
    filepath = GPT_INBOX / filename

    content = json.dumps(msg_data, ensure_ascii=False, sort_keys=True, indent=2)
    _atomic_write(filepath, content)

    print(f"ASK -> GPT")
    print(f"  id: {msg_id[:32]}...")
    print(f"  question: {question[:100]}{'...' if len(question) > 100 else ''}")
    print(f"  file: {filename}")
    print()
    print("GPT response will appear in: state/gpt_responses.log")

    return 0


def main() -> int:
    """CLI entrypoint."""
    import sys

    args = _parse_args(sys.argv)

    command = args.get("command", "help")

    commands = {
        "send": cmd_send,
        "tail": cmd_tail,
        "stats": cmd_stats,
        "clean": cmd_clean,
        "process": cmd_process,
        "scan": cmd_scan,
        "health": cmd_health,
        "chat": cmd_chat,
        "status": cmd_status,
        "ask": cmd_ask,
        "help": cmd_help,
    }

    handler = commands.get(command)
    if handler is None:
        print(f"Unknown command: {command}")
        return cmd_help()

    return handler(args) if handler != cmd_help else handler()


if __name__ == "__main__":
    sys.exit(main())
