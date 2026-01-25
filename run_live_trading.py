#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T16:35:00Z
# Modified by: Claude (opus-4)
# Modified at (UTC): 2026-01-25T17:30:00Z
# Purpose: LIVE Trading Entrypoint v2 - единственная точка запуска денег (MANDATORY gates)
# === END SIGNATURE ===
"""
LIVE Trading Entrypoint v2.

ЕДИНСТВЕННАЯ точка запуска торговли за реальные деньги.
Использует новый торговый контур: core/trade/*

Режимы:
- DRY: только расчёты, без ордеров (default)
- TESTNET: ордера на testnet.binance.vision
- MAINNET: ордера на api.binance.com (REAL MONEY!)

MAINNET требует:
- HOPE_LIVE_ENABLE=YES
- HOPE_LIVE_ACK=I_KNOW_WHAT_I_AM_DOING

Гейты (все ОБЯЗАТЕЛЬНЫ для MAINNET):
1. policy_preflight - SSoT evidence
2. verify_stack - cmdline SSoT
3. runtime_smoke - синтаксис core/*
4. live_gate - LIVE_ENABLE + LIVE_ACK + credentials

Usage:
    # DRY mode (safe default)
    python run_live_trading.py --mode DRY --symbol BTCUSDT --side BUY --size-usd 100

    # TESTNET
    python run_live_trading.py --mode TESTNET --symbol BTCUSDT --side BUY --size-usd 100

    # MAINNET (requires env vars!)
    set HOPE_LIVE_ENABLE=YES
    set HOPE_LIVE_ACK=I_KNOW_WHAT_I_AM_DOING
    python run_live_trading.py --mode MAINNET --symbol BTCUSDT --side BUY --size-usd 100 --once

Exit codes:
    0 = SUCCESS
    1 = FAIL (gate/risk/execution)
    2 = CONFIG ERROR
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# SSoT: project root
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("run_live_trading")

# SSoT paths
STATE_DIR = PROJECT_ROOT / "state"
HEALTH_DIR = STATE_DIR / "health"
LIVE_HEALTH_PATH = HEALTH_DIR / "live_trade.json"


def _atomic_write(path: Path, content: str) -> None:
    """Atomic write: temp -> fsync -> replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def run_policy_preflight() -> tuple[bool, str]:
    """Run policy_preflight gate."""
    try:
        from tools.commit_gate import check_policy_preflight
        result = check_policy_preflight()
        return result.passed, result.reason
    except Exception as e:
        return False, f"policy_preflight error: {e}"


def run_verify_stack() -> tuple[bool, str]:
    """Run verify_stack gate."""
    try:
        from tools.commit_gate import check_verify_stack
        result = check_verify_stack()
        return result.passed, result.reason
    except Exception as e:
        return False, f"verify_stack error: {e}"


def run_runtime_smoke() -> tuple[bool, str]:
    """Run runtime_smoke gate."""
    try:
        from tools.commit_gate import check_runtime_smoke
        result = check_runtime_smoke()
        return result.passed, result.reason
    except Exception as e:
        return False, f"runtime_smoke error: {e}"


def get_cmdline_sha256() -> str:
    """Get cmdline SHA256 (SSoT)."""
    try:
        from core.truth.cmdline_ssot import get_cmdline_sha256
        return get_cmdline_sha256()
    except ImportError:
        cmdline = " ".join(sys.argv)
        return hashlib.sha256(cmdline.encode()).hexdigest()


def save_live_evidence(
    gates: dict,
    mode: str,
    cmdline_sha256: str,
    run_id: str,
) -> None:
    """Save live trade evidence."""
    evidence = {
        "schema_version": "live_trade_v1",
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "cmdline_ssot": {
            "source": "GetCommandLineW",
            "sha256": cmdline_sha256,
        },
        "run_id": run_id,
        "gates": gates,
    }

    # Get allowlist SHA256
    allowlist_path = PROJECT_ROOT / "config" / "AllowList.spider.txt"
    if allowlist_path.exists():
        content = allowlist_path.read_bytes()
        evidence["allowlist_sha256"] = hashlib.sha256(content).hexdigest()

    _atomic_write(LIVE_HEALTH_PATH, json.dumps(evidence, indent=2))
    logger.info("Evidence saved: %s", LIVE_HEALTH_PATH)


def main() -> int:
    """Main entrypoint."""
    parser = argparse.ArgumentParser(
        description="LIVE Trading Entrypoint v2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--mode",
        choices=["DRY", "TESTNET", "MAINNET"],
        default="DRY",
        help="Trading mode (default: DRY)",
    )
    parser.add_argument(
        "--symbol",
        default="BTCUSDT",
        help="Trading pair (default: BTCUSDT)",
    )
    parser.add_argument(
        "--side",
        choices=["BUY", "SELL"],
        default="BUY",
        help="Order side (default: BUY)",
    )
    parser.add_argument(
        "--size-usd",
        type=float,
        default=100.0,
        help="Order size in USD (default: 100)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Execute once and exit",
    )
    # REMOVED: --skip-gates flag is DANGEROUS and has been removed
    # All gates are MANDATORY - no bypass allowed
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    args = parser.parse_args()
    mode = args.mode.upper()

    # All gates are MANDATORY - no bypass allowed
    logger.info("=== LIVE TRADING ENTRYPOINT v2 ===")
    logger.info("Mode: %s", mode)
    logger.info("Symbol: %s", args.symbol)
    logger.info("Side: %s", args.side)
    logger.info("Size: %.2f USD", args.size_usd)

    # === RUN GATES (MANDATORY - no bypass) ===
    gates = {}
    cmdline_sha256 = get_cmdline_sha256()

    # Gate 1: policy_preflight
    logger.info("Running policy_preflight...")
    passed, reason = run_policy_preflight()
    gates["policy_preflight"] = {"passed": passed, "reason": reason}
    if not passed and mode == "MAINNET":
        logger.error("GATE FAIL: policy_preflight - %s", reason)
        return 1
    elif not passed:
        logger.warning("GATE WARN: policy_preflight - %s", reason)

    # Gate 2: verify_stack
    logger.info("Running verify_stack...")
    passed, reason = run_verify_stack()
    gates["verify_stack"] = {"passed": passed, "reason": reason}
    if not passed and mode == "MAINNET":
        logger.error("GATE FAIL: verify_stack - %s", reason)
        return 1
    elif not passed:
        logger.warning("GATE WARN: verify_stack - %s", reason)

    # Gate 3: runtime_smoke
    logger.info("Running runtime_smoke...")
    passed, reason = run_runtime_smoke()
    gates["runtime_smoke"] = {"passed": passed, "reason": reason}
    if not passed and mode == "MAINNET":
        logger.error("GATE FAIL: runtime_smoke - %s", reason)
        return 1
    elif not passed:
        logger.warning("GATE WARN: runtime_smoke - %s", reason)

    # === IMPORT TRADING MODULES ===
    try:
        from core.trade.live_gate import LiveGate
        from core.trade.risk_engine import TradingRiskEngine
        from core.trade.order_router import TradingOrderRouter
        from core.trade.position_tracker import PositionTracker
    except ImportError as e:
        logger.error("Failed to import trading modules: %s", e)
        return 2

    # === LIVE GATE CHECK ===
    logger.info("Running live_gate...")
    live_gate = LiveGate()
    gate_result = live_gate.check(mode=mode, target_host="api.binance.com")
    gates["live_gate"] = {
        "passed": gate_result.allowed,
        "reason": gate_result.reason,
        "decision": gate_result.decision.value,
    }

    if not gate_result.allowed and mode == "MAINNET":
        logger.error("GATE FAIL: live_gate - %s", gate_result.reason)
        return 1
    elif not gate_result.allowed:
        logger.warning("GATE WARN: live_gate - %s (proceeding in %s mode)", gate_result.reason, mode)

    # === GENERATE RUN ID ===
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pid = os.getpid()
    nonce = hashlib.sha256(f"{ts}{pid}".encode()).hexdigest()[:32]
    run_id = f"live_v1__ts={ts}__pid={pid}__nonce={nonce}__cmd={cmdline_sha256[:8]}"

    # === SAVE EVIDENCE ===
    save_live_evidence(gates, mode, cmdline_sha256, run_id)

    # === GET PORTFOLIO SNAPSHOT ===
    logger.info("Getting portfolio snapshot...")
    tracker = PositionTracker(mode=mode)
    portfolio = tracker.get_snapshot()

    if portfolio is None:
        logger.error("FAIL-CLOSED: Could not get portfolio snapshot")
        return 1

    logger.info("Equity: %.2f USD", portfolio.equity_usd)
    logger.info("Daily PnL: %.2f USD", portfolio.daily_pnl_usd)

    # === EXECUTE ORDER ===
    logger.info("Initializing order router...")
    router = TradingOrderRouter(
        mode=mode,
        dry_run=(mode == "DRY"),
    )

    logger.info("Executing order...")
    result = router.execute_order(
        symbol=args.symbol,
        side=args.side,
        size_usd=args.size_usd,
        portfolio=portfolio,
    )

    # === OUTPUT ===
    if args.json:
        output = {
            "run_id": run_id,
            "mode": mode,
            "gates": gates,
            "portfolio": portfolio.to_dict(),
            "order_result": result.to_dict(),
        }
        print(json.dumps(output, indent=2))
    else:
        print()
        print("=== ORDER RESULT ===")
        print(f"Success: {result.success}")
        print(f"Status: {result.status.value}")
        print(f"Reason: {result.reason}")
        if result.order_id:
            print(f"Order ID: {result.order_id}")
        print(f"Requested: {result.requested_size_usd:.2f} USD")
        print(f"Allowed: {result.allowed_size_usd:.2f} USD")
        if result.executed_qty > 0:
            print(f"Executed: {result.executed_qty:.8f} @ {result.avg_price:.2f}")
            print(f"Total: {result.executed_usd:.2f} USD")
        print()

    if result.success:
        logger.info("Order completed successfully")
        return 0
    else:
        logger.error("Order failed: %s", result.reason)
        return 1


if __name__ == "__main__":
    sys.exit(main())
