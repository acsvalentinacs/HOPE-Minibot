# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-02 09:40:00 UTC
# Purpose: HOPE Protocol Checker - periodic verification + dynamic position sizing
# === END SIGNATURE ===
"""
HOPE PROTOCOL CHECKER v2.0

Периодическая проверка:
1. STARTUP PROTOCOL - все компоненты работают
2. CYCLIC TRADING PROTOCOL - размеры позиций корректируются при росте баланса

Запуск:
    python scripts/protocol_checker.py --daemon   # Фоновый режим (каждые 5 мин)
    python scripts/protocol_checker.py --once     # Одна проверка
    python scripts/protocol_checker.py --recalc   # Пересчитать позиции
"""

import os
import sys
import json
import time
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Tuple, Optional
from dataclasses import dataclass, asdict

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger("PROTOCOL")

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

CONFIG_FILE = PROJECT_ROOT / "config" / "scalping_100.json"
STATE_FILE = PROJECT_ROOT / "state" / "protocol_state.json"
SECRETS_PATH = "C:/secrets/hope.env"

# Minimum balance to operate
MIN_BALANCE_USD = 10.0

# Position sizing parameters
BASE_POSITION_PCT = 20  # 20% of balance
MIN_POSITION_USD = 10.0
MAX_POSITION_USD = 100.0  # Will scale with balance

# Check interval in daemon mode
CHECK_INTERVAL_SEC = 300  # 5 minutes


@dataclass
class ProtocolState:
    """Current protocol state."""
    balance_usd: float
    initial_balance: float
    growth_pct: float
    position_size_usd: float
    max_position_usd: float
    confidence_mult: float
    loss_adjustment: float
    compound_mult: float
    consecutive_losses: int
    last_check: str
    services_status: Dict[str, bool]

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class CheckResult:
    """Result of a protocol check."""
    passed: bool
    name: str
    message: str
    details: Optional[Dict] = None


# ═══════════════════════════════════════════════════════════════════════════════
# STARTUP PROTOCOL CHECKS
# ═══════════════════════════════════════════════════════════════════════════════

class StartupProtocolChecker:
    """Checks all components of STARTUP PROTOCOL v2.0."""

    def __init__(self):
        self.checks: list[CheckResult] = []

    def check_all(self) -> Tuple[bool, list[CheckResult]]:
        """Run all startup protocol checks."""
        self.checks = []

        # 1. Check Binance connection and balance
        self._check_binance()

        # 2. Check config files
        self._check_config()

        # 3. Check services (ports)
        self._check_services()

        # 4. Check module syntax
        self._check_syntax()

        all_passed = all(c.passed for c in self.checks)
        return all_passed, self.checks

    def _check_binance(self):
        """Check Binance API connection and balance."""
        try:
            from binance.client import Client
            from dotenv import load_dotenv

            load_dotenv(SECRETS_PATH)
            client = Client(
                os.getenv('BINANCE_API_KEY'),
                os.getenv('BINANCE_API_SECRET')
            )

            account = client.get_account()
            usdt_balance = 0.0

            for b in account['balances']:
                if b['asset'] == 'USDT':
                    usdt_balance = float(b['free'])
                    break

            if usdt_balance >= MIN_BALANCE_USD:
                self.checks.append(CheckResult(
                    passed=True,
                    name="Binance Balance",
                    message=f"OK: ${usdt_balance:.2f} USDT",
                    details={"balance": usdt_balance}
                ))
            else:
                self.checks.append(CheckResult(
                    passed=False,
                    name="Binance Balance",
                    message=f"FAIL: ${usdt_balance:.2f} < ${MIN_BALANCE_USD} minimum",
                    details={"balance": usdt_balance}
                ))

        except Exception as e:
            self.checks.append(CheckResult(
                passed=False,
                name="Binance Connection",
                message=f"FAIL: {str(e)}"
            ))

    def _check_config(self):
        """Check configuration files exist and are valid."""
        if CONFIG_FILE.exists():
            try:
                config = json.loads(CONFIG_FILE.read_text())
                required_keys = ["position_sizing", "risk_management", "targets"]
                missing = [k for k in required_keys if k not in config]

                if not missing:
                    self.checks.append(CheckResult(
                        passed=True,
                        name="Config File",
                        message=f"OK: {CONFIG_FILE.name}",
                        details={"path": str(CONFIG_FILE)}
                    ))
                else:
                    self.checks.append(CheckResult(
                        passed=False,
                        name="Config File",
                        message=f"FAIL: Missing keys: {missing}"
                    ))
            except json.JSONDecodeError as e:
                self.checks.append(CheckResult(
                    passed=False,
                    name="Config File",
                    message=f"FAIL: Invalid JSON: {e}"
                ))
        else:
            self.checks.append(CheckResult(
                passed=False,
                name="Config File",
                message=f"FAIL: Not found: {CONFIG_FILE}"
            ))

    def _check_services(self):
        """Check required services are running."""
        import socket

        services = {
            "Pricefeed Gateway": ("127.0.0.1", 8100),
            "AutoTrader": ("127.0.0.1", 8200),
        }

        for name, (host, port) in services.items():
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((host, port))
            sock.close()

            if result == 0:
                self.checks.append(CheckResult(
                    passed=True,
                    name=name,
                    message=f"OK: :{port} listening",
                    details={"port": port}
                ))
            else:
                self.checks.append(CheckResult(
                    passed=False,
                    name=name,
                    message=f"FAIL: :{port} not responding",
                    details={"port": port}
                ))

    def _check_syntax(self):
        """Check critical modules compile."""
        import py_compile

        critical_modules = [
            "scripts/autotrader.py",
            "scripts/eye_of_god_v3.py",
            "scripts/momentum_trader.py",
            "core/unified_allowlist.py",
        ]

        all_ok = True
        errors = []

        for module in critical_modules:
            module_path = PROJECT_ROOT / module
            if module_path.exists():
                try:
                    py_compile.compile(str(module_path), doraise=True)
                except py_compile.PyCompileError as e:
                    all_ok = False
                    errors.append(f"{module}: {e}")

        if all_ok:
            self.checks.append(CheckResult(
                passed=True,
                name="Module Syntax",
                message=f"OK: {len(critical_modules)} modules compiled"
            ))
        else:
            self.checks.append(CheckResult(
                passed=False,
                name="Module Syntax",
                message=f"FAIL: {errors}"
            ))


# ═══════════════════════════════════════════════════════════════════════════════
# CYCLIC TRADING PROTOCOL - DYNAMIC POSITION SIZING
# ═══════════════════════════════════════════════════════════════════════════════

class CyclicTradingProtocol:
    """
    Implements CYCLIC TRADING PROTOCOL v2.0

    ГЛАВНЫЙ ПРИНЦИП: БОЛЬШЕ ДЕПОЗИТ = БОЛЬШЕ ОРДЕР
    """

    def __init__(self):
        self.state = self._load_state()
        self.binance_client = None
        self._init_binance()

    def _init_binance(self):
        """Initialize Binance client."""
        try:
            from binance.client import Client
            from dotenv import load_dotenv

            load_dotenv(SECRETS_PATH)
            self.binance_client = Client(
                os.getenv('BINANCE_API_KEY'),
                os.getenv('BINANCE_API_SECRET')
            )
        except Exception as e:
            log.error(f"Failed to init Binance: {e}")

    def _load_state(self) -> Dict:
        """Load protocol state."""
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text())
            except:
                pass

        # Default state
        return {
            "initial_balance": 100.0,
            "consecutive_losses": 0,
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
        }

    def _save_state(self, state: Dict):
        """Save protocol state atomically."""
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_FILE)

    def get_current_balance(self) -> float:
        """Get current USDT balance from Binance."""
        if not self.binance_client:
            log.warning("No Binance client - returning 0")
            return 0.0

        try:
            account = self.binance_client.get_account()
            for b in account['balances']:
                if b['asset'] == 'USDT':
                    return float(b['free'])
        except Exception as e:
            log.error(f"Failed to get balance: {e}")

        return 0.0

    def calculate_position_size(self, confidence: float = 0.75) -> Dict[str, Any]:
        """
        Calculate dynamic position size based on current balance.

        Formula:
        position_size = balance × base_pct × confidence_mult × loss_adjust × compound_mult
        """
        balance = self.get_current_balance()
        initial = self.state.get("initial_balance", 100.0)
        consecutive_losses = self.state.get("consecutive_losses", 0)

        if balance < MIN_BALANCE_USD:
            return {
                "error": f"Balance ${balance:.2f} < minimum ${MIN_BALANCE_USD}",
                "position_size": 0,
                "can_trade": False,
            }

        # 1. Base size (20% of balance)
        base_size = balance * (BASE_POSITION_PCT / 100)

        # 2. Confidence multiplier (0.75 - 1.25)
        if confidence >= 0.85:
            conf_mult = 1.25
        elif confidence >= 0.75:
            conf_mult = 1.0
        elif confidence >= 0.65:
            conf_mult = 0.75
        else:
            conf_mult = 0.5  # Low confidence = small position

        # 3. Loss adjustment (0.5 - 1.0)
        if consecutive_losses >= 3:
            loss_mult = 0.50
        elif consecutive_losses >= 2:
            loss_mult = 0.75
        else:
            loss_mult = 1.0

        # 4. Compound bonus (1.0 - 1.5)
        growth_pct = ((balance - initial) / initial) * 100 if initial > 0 else 0

        if growth_pct >= 50:
            compound_mult = 1.50
        elif growth_pct >= 40:
            compound_mult = 1.40
        elif growth_pct >= 30:
            compound_mult = 1.30
        elif growth_pct >= 20:
            compound_mult = 1.20
        elif growth_pct >= 10:
            compound_mult = 1.10
        else:
            compound_mult = 1.0

        # Final calculation
        position_size = base_size * conf_mult * loss_mult * compound_mult

        # Apply limits
        min_pos = MIN_POSITION_USD
        max_pos = min(MAX_POSITION_USD, balance * 0.5)  # Max 50% of balance
        position_size = max(min_pos, min(max_pos, position_size))

        return {
            "balance_usd": round(balance, 2),
            "initial_balance": round(initial, 2),
            "growth_pct": round(growth_pct, 1),
            "position_size_usd": round(position_size, 2),
            "base_size": round(base_size, 2),
            "confidence": confidence,
            "confidence_mult": conf_mult,
            "loss_adjustment": loss_mult,
            "compound_mult": compound_mult,
            "consecutive_losses": consecutive_losses,
            "min_position": min_pos,
            "max_position": round(max_pos, 2),
            "can_trade": True,
        }

    def record_trade_result(self, is_win: bool, pnl: float = 0.0):
        """Record trade result and update state."""
        self.state["total_trades"] = self.state.get("total_trades", 0) + 1

        if is_win:
            self.state["wins"] = self.state.get("wins", 0) + 1
            self.state["consecutive_losses"] = 0
        else:
            self.state["losses"] = self.state.get("losses", 0) + 1
            self.state["consecutive_losses"] = self.state.get("consecutive_losses", 0) + 1

        self._save_state(self.state)
        log.info(f"Trade recorded: {'WIN' if is_win else 'LOSS'}, consecutive_losses={self.state['consecutive_losses']}")

    def update_config_file(self, position_size: float, max_position: float):
        """Update scalping config with new position sizes."""
        if not CONFIG_FILE.exists():
            log.error(f"Config file not found: {CONFIG_FILE}")
            return False

        try:
            config = json.loads(CONFIG_FILE.read_text())

            old_base = config.get("position_sizing", {}).get("base_size_usd", 0)
            old_max = config.get("position_sizing", {}).get("max_size_usd", 0)

            # Update position sizing
            config["position_sizing"]["base_size_usd"] = position_size
            config["position_sizing"]["max_size_usd"] = max_position

            # Atomic write
            tmp = CONFIG_FILE.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, CONFIG_FILE)

            if position_size != old_base or max_position != old_max:
                log.info(f"CONFIG UPDATED: position ${old_base} → ${position_size}, max ${old_max} → ${max_position}")

            return True

        except Exception as e:
            log.error(f"Failed to update config: {e}")
            return False


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN CHECKER
# ═══════════════════════════════════════════════════════════════════════════════

class ProtocolChecker:
    """Main protocol checker combining all checks."""

    def __init__(self):
        self.startup_checker = StartupProtocolChecker()
        self.cyclic_protocol = CyclicTradingProtocol()

    def run_full_check(self) -> Dict[str, Any]:
        """Run all protocol checks and recalculate positions."""
        results = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "startup_protocol": {},
            "cyclic_protocol": {},
            "overall_status": "UNKNOWN",
        }

        # 1. Startup Protocol Checks
        log.info("=" * 60)
        log.info("STARTUP PROTOCOL v2.0 CHECK")
        log.info("=" * 60)

        passed, checks = self.startup_checker.check_all()

        for check in checks:
            status = "✅" if check.passed else "❌"
            log.info(f"  {status} {check.name}: {check.message}")

        results["startup_protocol"] = {
            "passed": passed,
            "checks": [{"name": c.name, "passed": c.passed, "message": c.message} for c in checks]
        }

        # 2. Cyclic Trading Protocol
        log.info("")
        log.info("=" * 60)
        log.info("CYCLIC TRADING PROTOCOL v2.0")
        log.info("=" * 60)

        sizing = self.cyclic_protocol.calculate_position_size(confidence=0.75)

        if sizing.get("can_trade"):
            log.info(f"  Balance:         ${sizing['balance_usd']:.2f}")
            log.info(f"  Initial:         ${sizing['initial_balance']:.2f}")
            log.info(f"  Growth:          {sizing['growth_pct']:+.1f}%")
            log.info(f"  Position Size:   ${sizing['position_size_usd']:.2f}")
            log.info(f"  Compound Mult:   {sizing['compound_mult']:.2f}x")
            log.info(f"  Loss Adjust:     {sizing['loss_adjustment']:.2f}x")

            # Update config file with new sizes
            self.cyclic_protocol.update_config_file(
                sizing['position_size_usd'],
                sizing['max_position']
            )

            results["cyclic_protocol"] = sizing
        else:
            log.error(f"  ❌ {sizing.get('error', 'Unknown error')}")
            results["cyclic_protocol"] = sizing

        # Overall status
        results["overall_status"] = "PASS" if passed and sizing.get("can_trade") else "FAIL"

        log.info("")
        log.info("=" * 60)
        log.info(f"OVERALL STATUS: {results['overall_status']}")
        log.info("=" * 60)

        return results

    def run_daemon(self, interval_sec: int = CHECK_INTERVAL_SEC):
        """Run continuous checks in daemon mode."""
        log.info(f"Protocol Checker daemon started (interval: {interval_sec}s)")

        while True:
            try:
                self.run_full_check()
            except Exception as e:
                log.error(f"Check error: {e}")

            log.info(f"Next check in {interval_sec} seconds...")
            time.sleep(interval_sec)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="HOPE Protocol Checker v2.0")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--daemon", action="store_true", help="Run as daemon")
    parser.add_argument("--recalc", action="store_true", help="Recalculate position sizes")
    parser.add_argument("--interval", type=int, default=CHECK_INTERVAL_SEC, help="Check interval (seconds)")
    parser.add_argument("--confidence", type=float, default=0.75, help="Confidence for sizing")

    args = parser.parse_args()

    checker = ProtocolChecker()

    if args.daemon:
        checker.run_daemon(args.interval)
    elif args.recalc:
        sizing = checker.cyclic_protocol.calculate_position_size(args.confidence)
        print(json.dumps(sizing, indent=2))
        if sizing.get("can_trade"):
            checker.cyclic_protocol.update_config_file(
                sizing['position_size_usd'],
                sizing['max_position']
            )
    else:
        # Default: run once
        results = checker.run_full_check()
        if not args.once:
            print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
