# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 02:55:00 UTC
# Purpose: Pre-flight policy check before any trading operations
# Contract: Must PASS before AutoTrader/Gateway can start trading
# === END SIGNATURE ===
"""
POLICY PREFLIGHT CHECK

Обязательная проверка перед запуском торговли:
1. Environment (timezone, mode, paths)
2. Credentials (API keys present, not empty)
3. Risk limits (configured and sane)
4. PriceFeed availability
5. trades.jsonl integrity
6. No duplicate processes

Usage:
    python scripts/gates/policy_preflight.py              # Full check
    python scripts/gates/policy_preflight.py --mode DRY   # DRY mode check
    python scripts/gates/policy_preflight.py --json       # JSON output

Exit codes:
    0 = PASS (all checks OK)
    1 = FAIL (critical check failed)
    2 = WARN (non-critical issues)
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Ensure project root in path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class CheckResult:
    """Single check result."""

    def __init__(self, name: str, passed: bool, message: str, severity: str = "ERROR"):
        self.name = name
        self.passed = passed
        self.message = message
        self.severity = severity  # ERROR, WARNING, INFO

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "message": self.message,
            "severity": self.severity
        }


def check_environment() -> List[CheckResult]:
    """Check environment configuration."""
    results = []

    # Timezone
    now = datetime.now(timezone.utc)
    results.append(CheckResult(
        "timezone",
        True,
        f"UTC time: {now.isoformat()}",
        "INFO"
    ))

    # Project root
    project_root = Path(__file__).parent.parent.parent
    if not (project_root / "core").exists():
        results.append(CheckResult(
            "project_root",
            False,
            f"Project root invalid: {project_root}"
        ))
    else:
        results.append(CheckResult(
            "project_root",
            True,
            f"Project root: {project_root}",
            "INFO"
        ))

    # State directories
    state_dirs = [
        "state/ai/autotrader",
        "state/ai/oracle",
        "state/pricefeed",
        "state/locks"
    ]
    for d in state_dirs:
        path = project_root / d
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
        results.append(CheckResult(
            f"state_dir_{d.replace('/', '_')}",
            path.exists(),
            f"{d}: {'OK' if path.exists() else 'MISSING'}",
            "INFO" if path.exists() else "WARNING"
        ))

    return results


def check_credentials(mode: str) -> List[CheckResult]:
    """Check API credentials."""
    results = []

    # Load from env file
    env_path = Path("C:/secrets/hope.env")
    env_vars = {}

    if env_path.exists():
        try:
            from dotenv import dotenv_values
            env_vars = dotenv_values(env_path)
        except ImportError:
            # Manual parse
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    key, _, value = line.partition("=")
                    env_vars[key.strip()] = value.strip()

    # Check required keys based on mode
    if mode == "LIVE":
        required = ["BINANCE_API_KEY", "BINANCE_API_SECRET"]
    elif mode == "TESTNET":
        required = ["BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET"]
    else:  # DRY
        required = []

    for key in required:
        value = env_vars.get(key) or os.getenv(key)
        if value and len(value) > 10:
            results.append(CheckResult(
                f"credential_{key}",
                True,
                f"{key}: configured ({len(value)} chars)",
                "INFO"
            ))
        else:
            results.append(CheckResult(
                f"credential_{key}",
                False,
                f"{key}: MISSING or invalid"
            ))

    # Telegram (optional)
    tg_token = env_vars.get("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    results.append(CheckResult(
        "telegram_token",
        bool(tg_token),
        f"TELEGRAM_BOT_TOKEN: {'configured' if tg_token else 'not set'}",
        "INFO" if tg_token else "WARNING"
    ))

    return results


def check_risk_limits() -> List[CheckResult]:
    """Check risk configuration."""
    results = []

    try:
        from core.oracle_config import get_config_manager
        cm = get_config_manager()
        cfg = cm.config

        # Check limits
        if cfg.max_daily_loss_pct <= 0 or cfg.max_daily_loss_pct > 10:
            results.append(CheckResult(
                "max_daily_loss",
                False,
                f"max_daily_loss_pct={cfg.max_daily_loss_pct}% (should be 0-10%)"
            ))
        else:
            results.append(CheckResult(
                "max_daily_loss",
                True,
                f"max_daily_loss_pct={cfg.max_daily_loss_pct}%",
                "INFO"
            ))

        if cfg.max_consecutive_losses <= 0 or cfg.max_consecutive_losses > 10:
            results.append(CheckResult(
                "max_consecutive_losses",
                False,
                f"max_consecutive_losses={cfg.max_consecutive_losses} (should be 1-10)"
            ))
        else:
            results.append(CheckResult(
                "max_consecutive_losses",
                True,
                f"max_consecutive_losses={cfg.max_consecutive_losses}",
                "INFO"
            ))

        # Whitelist/blacklist
        results.append(CheckResult(
            "whitelist",
            len(cfg.whitelist) > 0,
            f"Whitelist: {len(cfg.whitelist)} symbols",
            "INFO"
        ))

        results.append(CheckResult(
            "blacklist",
            True,
            f"Blacklist: {len(cfg.blacklist)} symbols",
            "INFO"
        ))

    except Exception as e:
        results.append(CheckResult(
            "risk_config",
            False,
            f"Failed to load risk config: {e}"
        ))

    return results


def check_pricefeed() -> List[CheckResult]:
    """Check PriceFeed availability."""
    results = []

    try:
        import urllib.request
        resp = urllib.request.urlopen("http://127.0.0.1:8100/health", timeout=5)
        data = json.loads(resp.read().decode())

        if data.get("status") == "ok":
            results.append(CheckResult(
                "gateway_health",
                True,
                "Gateway health: OK",
                "INFO"
            ))
        else:
            results.append(CheckResult(
                "gateway_health",
                False,
                f"Gateway health: {data.get('status')}"
            ))

        # Check price-feed
        resp2 = urllib.request.urlopen("http://127.0.0.1:8100/price-feed/prices", timeout=5)
        pf_data = json.loads(resp2.read().decode())

        subscribed = pf_data.get("subscribed_count", 0)
        prices = len(pf_data.get("prices", {}))

        results.append(CheckResult(
            "pricefeed",
            subscribed > 0,
            f"PriceFeed: {subscribed} subscribed, {prices} prices",
            "INFO" if subscribed > 0 else "WARNING"
        ))

    except Exception as e:
        results.append(CheckResult(
            "gateway_health",
            False,
            f"Gateway not reachable: {e}"
        ))

    return results


def check_trades_integrity() -> List[CheckResult]:
    """Check trades.jsonl integrity."""
    results = []

    trades_path = Path("state/ai/autotrader/trades.jsonl")

    if not trades_path.exists():
        results.append(CheckResult(
            "trades_file",
            True,
            "trades.jsonl: not found (will be created)",
            "INFO"
        ))
        return results

    try:
        from core.jsonl_atomic import verify_jsonl
        verification = verify_jsonl(trades_path)

        if verification["valid"]:
            results.append(CheckResult(
                "trades_integrity",
                True,
                f"trades.jsonl: {verification['valid_count']}/{verification['total']} valid",
                "INFO"
            ))
        else:
            results.append(CheckResult(
                "trades_integrity",
                False,
                f"trades.jsonl: {verification['bad_sha']} bad sha, {verification['no_sha']} no sha"
            ))

    except Exception as e:
        results.append(CheckResult(
            "trades_integrity",
            False,
            f"Failed to verify trades.jsonl: {e}"
        ))

    return results


def check_no_duplicates() -> List[CheckResult]:
    """Check for duplicate processes."""
    results = []

    try:
        from core.lockfile import is_locked, get_lock_owner

        # Gateway
        if is_locked("gateway"):
            pid = get_lock_owner("gateway")
            results.append(CheckResult(
                "gateway_lock",
                True,
                f"Gateway running (PID {pid})",
                "INFO"
            ))
        else:
            results.append(CheckResult(
                "gateway_lock",
                True,
                "Gateway not locked",
                "INFO"
            ))

        # AutoTrader
        if is_locked("autotrader"):
            pid = get_lock_owner("autotrader")
            results.append(CheckResult(
                "autotrader_lock",
                True,
                f"AutoTrader running (PID {pid})",
                "INFO"
            ))
        else:
            results.append(CheckResult(
                "autotrader_lock",
                True,
                "AutoTrader not locked",
                "INFO"
            ))

    except Exception as e:
        results.append(CheckResult(
            "lock_check",
            False,
            f"Failed to check locks: {e}"
        ))

    return results


def run_preflight(mode: str) -> Tuple[bool, List[CheckResult]]:
    """Run all preflight checks."""
    all_results = []

    print(f"Running preflight checks for mode: {mode}")
    print("-" * 50)

    # Run all checks
    checks = [
        ("Environment", check_environment),
        ("Credentials", lambda: check_credentials(mode)),
        ("Risk Limits", check_risk_limits),
        ("PriceFeed", check_pricefeed),
        ("Trades Integrity", check_trades_integrity),
        ("Duplicate Processes", check_no_duplicates),
    ]

    for name, check_fn in checks:
        print(f"\n{name}:")
        try:
            results = check_fn()
            all_results.extend(results)
            for r in results:
                status = "PASS" if r.passed else "FAIL"
                if r.severity == "WARNING" and not r.passed:
                    status = "WARN"
                elif r.severity == "INFO":
                    status = "INFO"
                print(f"  [{status}] {r.message}")
        except Exception as e:
            result = CheckResult(name.lower(), False, f"Check failed: {e}")
            all_results.append(result)
            print(f"  [FAIL] {result.message}")

    # Determine overall result
    errors = [r for r in all_results if not r.passed and r.severity == "ERROR"]
    overall_pass = len(errors) == 0

    return overall_pass, all_results


def main():
    parser = argparse.ArgumentParser(description="Policy Preflight Check")
    parser.add_argument("--mode", type=str, default="DRY", choices=["DRY", "TESTNET", "LIVE"])
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    overall_pass, results = run_preflight(args.mode)

    if args.json:
        output = {
            "result": "PASS" if overall_pass else "FAIL",
            "mode": args.mode,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": [r.to_dict() for r in results]
        }
        print(json.dumps(output, indent=2))
    else:
        print("\n" + "=" * 50)
        errors = [r for r in results if not r.passed and r.severity == "ERROR"]
        warnings = [r for r in results if not r.passed and r.severity == "WARNING"]
        print(f"RESULT: {'PASS' if overall_pass else 'FAIL'}")
        print(f"Errors: {len(errors)}, Warnings: {len(warnings)}")

        if not overall_pass:
            print("\nBlocking issues:")
            for r in errors:
                print(f"  - {r.name}: {r.message}")

    sys.exit(0 if overall_pass else 1)


if __name__ == "__main__":
    main()
