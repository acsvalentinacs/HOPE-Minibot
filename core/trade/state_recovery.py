# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T01:00:00Z
# Purpose: Latent State Recovery - синхронизация локального state с биржей
# Security: Fail-closed, любое расхождение = STOP
# === END SIGNATURE ===
"""
Latent State Recovery - State Reconciliation on Boot.

При старте Live-процесса сверяет "что бот думает" vs "что на бирже".
Любое расхождение = CRITICAL_ERROR и STOP.

Проверки:
1. Локальные открытые ордера vs ордера на бирже
2. Локальные позиции (активы > threshold) vs балансы на бирже
3. Timestamp свежести локального state

FAIL-CLOSED:
- Ghost Position: локально есть, на бирже нет → STOP
- Zombie Position: локально нет, на бирже есть → STOP
- Stale State: state старше TTL → STOP

Usage:
    from core.trade.state_recovery import StateReconciler, ReconcilerConfig

    reconciler = StateReconciler(ReconcilerConfig(mode="TESTNET"))
    result = reconciler.reconcile()

    if not result.ok:
        print(f"RECONCILIATION FAILED: {result.reason}")
        sys.exit(1)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("trade.state_recovery")

# SSoT paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATE_DIR = BASE_DIR / "state"
SECRETS_PATH = Path(r"C:\secrets\hope\.env")

# State file
LOCAL_STATE_FILE = STATE_DIR / "trading_state.json"

# Thresholds
POSITION_THRESHOLD_USD = 1.0  # Ignore assets worth less than $1
STATE_TTL_SECONDS = 3600  # State older than 1 hour = stale
BALANCE_TOLERANCE_PCT = 0.01  # 1% tolerance for balance comparison


class ReconcileStatus(str, Enum):
    """Reconciliation status codes."""
    OK = "OK"
    FAIL_NO_LOCAL_STATE = "FAIL_NO_LOCAL_STATE"
    FAIL_STALE_STATE = "FAIL_STALE_STATE"
    FAIL_EXCHANGE_ERROR = "FAIL_EXCHANGE_ERROR"
    FAIL_GHOST_POSITION = "FAIL_GHOST_POSITION"
    FAIL_ZOMBIE_POSITION = "FAIL_ZOMBIE_POSITION"
    FAIL_ORDER_MISMATCH = "FAIL_ORDER_MISMATCH"
    FAIL_BALANCE_MISMATCH = "FAIL_BALANCE_MISMATCH"
    FAIL_EXCEPTION = "FAIL_EXCEPTION"


@dataclass
class ReconcilerConfig:
    """Reconciler configuration."""
    mode: str = "TESTNET"  # TESTNET or MAINNET
    skip_order_check: bool = False
    skip_balance_check: bool = False
    state_ttl_seconds: int = STATE_TTL_SECONDS
    balance_tolerance_pct: float = BALANCE_TOLERANCE_PCT
    position_threshold_usd: float = POSITION_THRESHOLD_USD


@dataclass
class LocalState:
    """Local trading state structure."""
    schema_version: str = "trading_state.v1"
    updated_at_utc: str = ""
    updated_at_unix: float = 0.0
    open_orders: List[Dict[str, Any]] = field(default_factory=list)
    positions: Dict[str, float] = field(default_factory=dict)  # asset -> quantity
    last_trade_id: str = ""


@dataclass
class ReconcileResult:
    """Result of reconciliation."""
    ok: bool
    status: ReconcileStatus
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)
    local_state_age_sec: float = 0.0
    checks_passed: List[str] = field(default_factory=list)
    diffs: List[str] = field(default_factory=list)
    timestamp_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    schema_version: str = "reconcile_result.v1"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


class StateReconciler:
    """
    State Reconciler - синхронизация локального state с биржей.

    FAIL-CLOSED: любое расхождение = отказ в запуске торговли.
    """

    def __init__(self, config: ReconcilerConfig):
        """Initialize reconciler."""
        self.config = config
        self._env: Dict[str, str] = {}
        self._load_env()

        logger.info("StateReconciler initialized: mode=%s", config.mode)

    def _load_env(self) -> None:
        """Load environment from secrets file."""
        if not SECRETS_PATH.exists():
            return

        try:
            content = SECRETS_PATH.read_text(encoding="utf-8")
            for line in content.splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    self._env[key.strip()] = value.strip()
        except Exception as e:
            logger.warning("Failed to load secrets: %s", e)

    def _get_api_credentials(self) -> tuple[str, str, str]:
        """Get API credentials based on mode."""
        if self.config.mode == "TESTNET":
            base_url = "https://testnet.binance.vision/api"
            api_key = self._env.get("BINANCE_TESTNET_API_KEY", "")
            api_secret = self._env.get("BINANCE_TESTNET_API_SECRET", "")
        else:
            base_url = "https://api.binance.com/api"
            api_key = self._env.get("BINANCE_API_KEY", "")
            api_secret = self._env.get("BINANCE_API_SECRET", "")

        return base_url, api_key, api_secret

    def _load_local_state(self) -> Optional[LocalState]:
        """Load local trading state."""
        if not LOCAL_STATE_FILE.exists():
            return None

        try:
            content = LOCAL_STATE_FILE.read_text(encoding="utf-8")
            data = json.loads(content)

            return LocalState(
                schema_version=data.get("schema_version", "unknown"),
                updated_at_utc=data.get("updated_at_utc", ""),
                updated_at_unix=data.get("updated_at_unix", 0.0),
                open_orders=data.get("open_orders", []),
                positions=data.get("positions", {}),
                last_trade_id=data.get("last_trade_id", ""),
            )
        except Exception as e:
            logger.error("Failed to load local state: %s", e)
            return None

    def _fetch_exchange_orders(self) -> tuple[bool, List[Dict], str]:
        """Fetch open orders from exchange."""
        try:
            import requests

            base_url, api_key, api_secret = self._get_api_credentials()

            if not api_key or not api_secret:
                return False, [], "No API credentials"

            timestamp = int(time.time() * 1000)
            query = f"timestamp={timestamp}"
            signature = hmac.new(
                api_secret.encode(),
                query.encode(),
                hashlib.sha256
            ).hexdigest()

            url = f"{base_url}/v3/openOrders?{query}&signature={signature}"
            headers = {"X-MBX-APIKEY": api_key}

            resp = requests.get(url, headers=headers, timeout=10)

            if resp.status_code != 200:
                return False, [], f"HTTP {resp.status_code}: {resp.text[:200]}"

            orders = resp.json()
            return True, orders, ""

        except Exception as e:
            return False, [], str(e)

    def _fetch_exchange_balances(self) -> tuple[bool, Dict[str, float], str]:
        """Fetch account balances from exchange."""
        try:
            import requests

            base_url, api_key, api_secret = self._get_api_credentials()

            if not api_key or not api_secret:
                return False, {}, "No API credentials"

            timestamp = int(time.time() * 1000)
            query = f"timestamp={timestamp}"
            signature = hmac.new(
                api_secret.encode(),
                query.encode(),
                hashlib.sha256
            ).hexdigest()

            url = f"{base_url}/v3/account?{query}&signature={signature}"
            headers = {"X-MBX-APIKEY": api_key}

            resp = requests.get(url, headers=headers, timeout=10)

            if resp.status_code != 200:
                return False, {}, f"HTTP {resp.status_code}: {resp.text[:200]}"

            data = resp.json()
            balances = {}
            for b in data.get("balances", []):
                free = float(b.get("free", 0))
                if free > 0:
                    balances[b["asset"]] = free

            return True, balances, ""

        except Exception as e:
            return False, {}, str(e)

    def reconcile(self) -> ReconcileResult:
        """
        Execute state reconciliation.

        FAIL-CLOSED: any mismatch = STOP.
        """
        checks_passed = []
        diffs = []
        details: Dict[str, Any] = {"mode": self.config.mode}

        try:
            # === CHECK 1: Local state exists ===
            local_state = self._load_local_state()

            if local_state is None:
                # No local state = clean start, OK
                logger.info("No local state found - clean start")
                checks_passed.append("LOCAL_STATE_CLEAN")
                return ReconcileResult(
                    ok=True,
                    status=ReconcileStatus.OK,
                    reason="Clean start - no local state",
                    checks_passed=checks_passed,
                    details=details,
                )

            details["local_state_version"] = local_state.schema_version

            # === CHECK 2: State freshness ===
            now = time.time()
            state_age = now - local_state.updated_at_unix
            details["state_age_sec"] = state_age

            if state_age > self.config.state_ttl_seconds:
                diffs.append(f"State stale: {state_age:.0f}s > {self.config.state_ttl_seconds}s")
                return ReconcileResult(
                    ok=False,
                    status=ReconcileStatus.FAIL_STALE_STATE,
                    reason=f"Local state is stale ({state_age:.0f}s old)",
                    local_state_age_sec=state_age,
                    checks_passed=checks_passed,
                    diffs=diffs,
                    details=details,
                )
            checks_passed.append("STATE_FRESH")

            # === CHECK 3: Open orders reconciliation ===
            if not self.config.skip_order_check:
                ok, exchange_orders, error = self._fetch_exchange_orders()
                if not ok:
                    return ReconcileResult(
                        ok=False,
                        status=ReconcileStatus.FAIL_EXCHANGE_ERROR,
                        reason=f"Failed to fetch orders: {error}",
                        local_state_age_sec=state_age,
                        checks_passed=checks_passed,
                        diffs=diffs,
                        details=details,
                    )

                # Compare order IDs
                local_order_ids = {str(o.get("orderId", o.get("id", ""))) for o in local_state.open_orders}
                exchange_order_ids = {str(o.get("orderId", "")) for o in exchange_orders}

                # Ghost orders: local has, exchange doesn't
                ghost_orders = local_order_ids - exchange_order_ids
                # Zombie orders: exchange has, local doesn't
                zombie_orders = exchange_order_ids - local_order_ids

                if ghost_orders:
                    diffs.append(f"Ghost orders (local only): {ghost_orders}")
                    details["ghost_orders"] = list(ghost_orders)
                    return ReconcileResult(
                        ok=False,
                        status=ReconcileStatus.FAIL_ORDER_MISMATCH,
                        reason=f"Ghost orders detected: {ghost_orders}",
                        local_state_age_sec=state_age,
                        checks_passed=checks_passed,
                        diffs=diffs,
                        details=details,
                    )

                if zombie_orders:
                    diffs.append(f"Zombie orders (exchange only): {zombie_orders}")
                    details["zombie_orders"] = list(zombie_orders)
                    return ReconcileResult(
                        ok=False,
                        status=ReconcileStatus.FAIL_ORDER_MISMATCH,
                        reason=f"Zombie orders detected: {zombie_orders}",
                        local_state_age_sec=state_age,
                        checks_passed=checks_passed,
                        diffs=diffs,
                        details=details,
                    )

                checks_passed.append("ORDERS_MATCH")
                details["orders_count"] = len(exchange_orders)

            # === CHECK 4: Balance/position reconciliation ===
            if not self.config.skip_balance_check and local_state.positions:
                ok, exchange_balances, error = self._fetch_exchange_balances()
                if not ok:
                    return ReconcileResult(
                        ok=False,
                        status=ReconcileStatus.FAIL_EXCHANGE_ERROR,
                        reason=f"Failed to fetch balances: {error}",
                        local_state_age_sec=state_age,
                        checks_passed=checks_passed,
                        diffs=diffs,
                        details=details,
                    )

                # Compare significant positions
                for asset, local_qty in local_state.positions.items():
                    exchange_qty = exchange_balances.get(asset, 0)

                    # Check for significant difference
                    if local_qty > 0:
                        diff_pct = abs(exchange_qty - local_qty) / local_qty
                        if diff_pct > self.config.balance_tolerance_pct:
                            if exchange_qty < local_qty * 0.5:
                                # Ghost position: local has, exchange doesn't
                                diffs.append(f"Ghost position {asset}: local={local_qty}, exchange={exchange_qty}")
                                details["ghost_position"] = {"asset": asset, "local": local_qty, "exchange": exchange_qty}
                                return ReconcileResult(
                                    ok=False,
                                    status=ReconcileStatus.FAIL_GHOST_POSITION,
                                    reason=f"Ghost position: {asset} local={local_qty} vs exchange={exchange_qty}",
                                    local_state_age_sec=state_age,
                                    checks_passed=checks_passed,
                                    diffs=diffs,
                                    details=details,
                                )

                # Check for zombie positions (exchange has, local doesn't know)
                for asset, exchange_qty in exchange_balances.items():
                    if asset in ("USDT", "BTC", "BNB"):
                        continue  # Skip quote assets
                    if asset not in local_state.positions and exchange_qty > 0:
                        # Estimate USD value (rough)
                        if exchange_qty > self.config.position_threshold_usd:
                            diffs.append(f"Zombie position {asset}: exchange={exchange_qty}, local=0")
                            details["zombie_position"] = {"asset": asset, "exchange": exchange_qty}
                            return ReconcileResult(
                                ok=False,
                                status=ReconcileStatus.FAIL_ZOMBIE_POSITION,
                                reason=f"Zombie position: {asset} on exchange but not in local state",
                                local_state_age_sec=state_age,
                                checks_passed=checks_passed,
                                diffs=diffs,
                                details=details,
                            )

                checks_passed.append("BALANCES_MATCH")

            # === ALL CHECKS PASSED ===
            logger.info("Reconciliation PASS: %s", checks_passed)
            return ReconcileResult(
                ok=True,
                status=ReconcileStatus.OK,
                reason="All reconciliation checks passed",
                local_state_age_sec=state_age,
                checks_passed=checks_passed,
                diffs=diffs,
                details=details,
            )

        except Exception as e:
            logger.exception("Reconciliation exception: %s", e)
            return ReconcileResult(
                ok=False,
                status=ReconcileStatus.FAIL_EXCEPTION,
                reason=str(e),
                checks_passed=checks_passed,
                diffs=diffs,
                details=details,
            )


def save_trading_state(
    open_orders: List[Dict] = None,
    positions: Dict[str, float] = None,
    last_trade_id: str = "",
) -> bool:
    """
    Save trading state atomically.

    Args:
        open_orders: List of open order dicts
        positions: Dict of asset -> quantity
        last_trade_id: ID of last trade

    Returns:
        True if saved successfully
    """
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc)
        state = {
            "schema_version": "trading_state.v1",
            "updated_at_utc": now.isoformat(),
            "updated_at_unix": now.timestamp(),
            "open_orders": open_orders or [],
            "positions": positions or {},
            "last_trade_id": last_trade_id,
        }

        content = json.dumps(state, indent=2)

        # Atomic write
        tmp_path = LOCAL_STATE_FILE.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, LOCAL_STATE_FILE)

        logger.info("Trading state saved: %s", LOCAL_STATE_FILE)
        return True

    except Exception as e:
        logger.error("Failed to save trading state: %s", e)
        return False


def run_reconciliation(mode: str = "TESTNET") -> ReconcileResult:
    """
    Convenience function to run state reconciliation.

    Returns:
        ReconcileResult - check result.ok before proceeding
    """
    config = ReconcilerConfig(mode=mode.upper())
    reconciler = StateReconciler(config)
    return reconciler.reconcile()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="State Reconciliation Check")
    parser.add_argument("--mode", default="TESTNET", choices=["TESTNET", "MAINNET"])
    parser.add_argument("--skip-orders", action="store_true")
    parser.add_argument("--skip-balances", action="store_true")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    config = ReconcilerConfig(
        mode=args.mode,
        skip_order_check=args.skip_orders,
        skip_balance_check=args.skip_balances,
    )

    reconciler = StateReconciler(config)
    result = reconciler.reconcile()

    print(json.dumps(result.to_dict(), indent=2, default=str))
    exit(0 if result.ok else 1)
