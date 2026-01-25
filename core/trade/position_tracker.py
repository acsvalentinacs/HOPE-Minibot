# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T16:30:00Z
# Purpose: Position Tracker - снимок портфеля для risk engine
# === END SIGNATURE ===
"""
Position Tracker - Portfolio Snapshot.

Минимальный модуль для получения снимка позиций/эквити.
Ошибки получения данных = FAIL-CLOSED.

Usage:
    from core.trade.position_tracker import PositionTracker

    tracker = PositionTracker(mode="TESTNET")

    snapshot = tracker.get_snapshot()
    if snapshot is None:
        log.error("Failed to get portfolio snapshot")
        return  # STOP

    # Use snapshot for risk validation
    result = risk_engine.validate_order(intent, snapshot)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("trade.position_tracker")

# SSoT paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATE_DIR = BASE_DIR / "state"


@dataclass
class PositionInfo:
    """Information about a single position."""
    symbol: str
    side: str  # LONG or SHORT
    qty: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    size_usd: float

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict."""
        return {
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "unrealized_pnl": self.unrealized_pnl,
            "size_usd": self.size_usd,
        }


@dataclass
class PortfolioSnapshot:
    """
    Complete portfolio snapshot for risk calculations.

    Used by risk_engine.validate_order().
    """
    equity_usd: float
    open_positions: int
    daily_pnl_usd: float
    start_of_day_equity: float
    timestamp_utc: str
    source: str  # Where data came from

    # Optional details
    positions: List[PositionInfo] = field(default_factory=list)
    balances: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict."""
        return {
            "equity_usd": self.equity_usd,
            "open_positions": self.open_positions,
            "daily_pnl_usd": self.daily_pnl_usd,
            "start_of_day_equity": self.start_of_day_equity,
            "timestamp_utc": self.timestamp_utc,
            "source": self.source,
            "positions": [p.to_dict() for p in self.positions],
            "balances": self.balances,
        }


class PositionTracker:
    """
    Position Tracker - fail-closed portfolio snapshot.

    Ошибки получения данных = return None (caller must handle).
    """

    def __init__(
        self,
        mode: str = "DRY",
    ):
        """
        Initialize Position Tracker.

        Args:
            mode: Trading mode (DRY, TESTNET, MAINNET)
        """
        self.mode = mode.upper()
        self._exchange_client = None
        self._start_of_day_equity: Optional[float] = None
        self._start_of_day_date: Optional[str] = None

        logger.debug("PositionTracker initialized: mode=%s", self.mode)

    def _get_exchange_client(self):
        """Get or create exchange client."""
        if self._exchange_client is not None:
            return self._exchange_client

        if self.mode == "TESTNET":
            from core.spot_testnet_client import SpotTestnetClient
            self._exchange_client = SpotTestnetClient()
        elif self.mode == "MAINNET":
            from core.spot_testnet_client import SpotTestnetClient

            # Load mainnet credentials
            secrets_path = Path(r"C:\secrets\hope\.env")
            env = {}
            if secrets_path.exists():
                for line in secrets_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        env[k.strip()] = v.strip()

            self._exchange_client = SpotTestnetClient(
                api_key=env.get("BINANCE_MAINNET_API_KEY", ""),
                api_secret=env.get("BINANCE_MAINNET_API_SECRET", ""),
            )
            self._exchange_client.base_url = "https://api.binance.com/api"

        return self._exchange_client

    def _calculate_equity(self, balances: List, prices: Dict[str, float]) -> float:
        """Calculate total equity in USD."""
        total = 0.0

        for b in balances:
            asset = b.asset if hasattr(b, "asset") else b.get("asset", "")
            free = float(b.free if hasattr(b, "free") else b.get("free", 0))
            locked = float(b.locked if hasattr(b, "locked") else b.get("locked", 0))
            total_amount = free + locked

            if total_amount <= 0:
                continue

            # USDT/USDC/BUSD = 1:1
            if asset in ("USDT", "USDC", "BUSD", "USD"):
                total += total_amount
            else:
                # Try to find price
                symbol = f"{asset}USDT"
                price = prices.get(symbol, 0)
                if price > 0:
                    total += total_amount * price

        return total

    def get_snapshot(self) -> Optional[PortfolioSnapshot]:
        """
        Get current portfolio snapshot.

        FAIL-CLOSED: Returns None on any error.
        """
        now_utc = datetime.now(timezone.utc)
        today = now_utc.strftime("%Y-%m-%d")

        if self.mode == "DRY":
            # DRY mode: return mock snapshot
            equity = 10000.0

            # Check for start of day reset
            if self._start_of_day_date != today:
                self._start_of_day_equity = equity
                self._start_of_day_date = today

            return PortfolioSnapshot(
                equity_usd=equity,
                open_positions=0,
                daily_pnl_usd=0.0,
                start_of_day_equity=self._start_of_day_equity or equity,
                timestamp_utc=now_utc.isoformat(),
                source="dry_mode",
            )

        try:
            client = self._get_exchange_client()
            if client is None:
                logger.error("Exchange client not available")
                return None

            # Get balances
            balances = client.get_balances()
            if not balances:
                logger.warning("No balances returned")

            # Get prices for non-USDT assets
            prices = {}
            try:
                all_tickers = client.get_ticker_price()
                for t in all_tickers:
                    prices[t["symbol"]] = float(t["price"])
            except Exception as e:
                logger.warning("Failed to get prices: %s", e)

            # Calculate equity
            equity = self._calculate_equity(balances, prices)

            # Check for start of day reset
            if self._start_of_day_date != today:
                self._start_of_day_equity = equity
                self._start_of_day_date = today

            # Calculate daily PnL
            start_equity = self._start_of_day_equity or equity
            daily_pnl = equity - start_equity

            # Build balances dict
            balances_dict = {}
            for b in balances:
                asset = b.asset if hasattr(b, "asset") else b.get("asset", "")
                free = float(b.free if hasattr(b, "free") else b.get("free", 0))
                locked = float(b.locked if hasattr(b, "locked") else b.get("locked", 0))
                if free + locked > 0:
                    balances_dict[asset] = free + locked

            return PortfolioSnapshot(
                equity_usd=equity,
                open_positions=0,  # SPOT has no "positions" in futures sense
                daily_pnl_usd=daily_pnl,
                start_of_day_equity=start_equity,
                timestamp_utc=now_utc.isoformat(),
                source=f"{self.mode.lower()}_exchange",
                balances=balances_dict,
            )

        except Exception as e:
            logger.error("Failed to get portfolio snapshot: %s", e)
            return None

    def refresh_start_of_day(self, equity: float) -> None:
        """
        Manually set start of day equity.

        Call at beginning of trading day.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._start_of_day_equity = equity
        self._start_of_day_date = today
        logger.info("Start of day equity set: %.2f USD", equity)


# === CLI Interface ===
def main() -> int:
    """CLI entrypoint."""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if len(sys.argv) < 2:
        print("Usage: python -m core.trade.position_tracker <command>")
        print("Commands:")
        print("  snapshot <mode>  - Get portfolio snapshot")
        return 1

    command = sys.argv[1]

    if command == "snapshot":
        mode = sys.argv[2] if len(sys.argv) > 2 else "DRY"
        tracker = PositionTracker(mode=mode)
        snapshot = tracker.get_snapshot()

        if snapshot is None:
            print("FAIL: Could not get snapshot")
            return 1

        print(json.dumps(snapshot.to_dict(), indent=2))
        return 0

    else:
        print(f"Unknown command: {command}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
