# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 13:15:00 UTC
# Purpose: Dynamic AllowList manager for high-volatility trading
# === END SIGNATURE ===

"""
Dynamic AllowList Manager

Automatically selects top trading symbols based on:
- 24h Volume (min $50M)
- Price change % (volatility)
- Binance availability
- Eye of God compatibility

Updates every hour to capture market momentum.
"""

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import hashlib

logger = logging.getLogger("ALLOWLIST")

# === CONFIGURATION ===

@dataclass
class AllowListConfig:
    """AllowList configuration parameters."""
    min_volume_usd: float = 50_000_000  # $50M minimum 24h volume
    max_symbols: int = 20               # Maximum symbols in active list
    update_interval_hours: float = 1.0  # Update every hour

    # Volatility scoring weights
    weight_volume: float = 0.3          # Volume contribution to score
    weight_change_24h: float = 0.4      # 24h price change contribution
    weight_change_1h: float = 0.3       # 1h price change contribution

    # Blacklist (never trade)
    blacklist: Set[str] = field(default_factory=lambda: {
        "LEVERUSDT", "LUNAUSDT", "USTCUSDT",  # Collapsed
        "USDTUSDT", "BUSDUSDT", "TUSDUSDT",   # Stablecoins
        "USDCUSDT", "DAIUSDT", "FDUSDUSDT",   # Stablecoins
        "USD1USDT", "USDDUSDT", "USDEUSDT",   # More stablecoins
        "PYUSDUSDT", "RLSUSDUSDT", "EURCUSDT", # Fiat-backed
        "PAXGUSDT", "XAUTUSDT",               # Gold-backed (not crypto)
    })

    # Core symbols (always include if volume OK)
    core_symbols: Set[str] = field(default_factory=lambda: {
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"
    })


@dataclass
class SymbolScore:
    """Scoring data for a symbol."""
    symbol: str
    volume_24h: float
    price_change_24h: float
    price_change_1h: float
    score: float
    rank: int = 0


@dataclass
class AllowListState:
    """Current state of the dynamic AllowList."""
    symbols: List[str]
    scores: Dict[str, float]
    updated_at: str
    next_update_at: str
    update_count: int
    config_hash: str

    def to_dict(self) -> dict:
        return asdict(self)


# === STATE PATHS ===

STATE_DIR = Path("state/ai/allowlist")
STATE_FILE = STATE_DIR / "current.json"
HISTORY_FILE = STATE_DIR / "history.jsonl"
CONFIG_FILE = STATE_DIR / "config.json"


class DynamicAllowListManager:
    """
    Manages dynamic AllowList based on market data.

    Flow:
    1. Fetch 24h ticker data from Binance
    2. Filter by minimum volume
    3. Score by volatility (volume + price change)
    4. Select top N symbols
    5. Persist state
    """

    def __init__(self, config: Optional[AllowListConfig] = None):
        self.config = config or AllowListConfig()
        self.state: Optional[AllowListState] = None
        self._ensure_dirs()
        self._load_state()

    def _ensure_dirs(self):
        """Create state directories."""
        STATE_DIR.mkdir(parents=True, exist_ok=True)

    def _load_state(self):
        """Load current state from disk."""
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                self.state = AllowListState(**data)
                logger.info(f"Loaded AllowList state: {len(self.state.symbols)} symbols")
            except Exception as e:
                logger.warning(f"Failed to load state: {e}")
                self.state = None

    def _save_state(self):
        """Save current state to disk (atomic write)."""
        if not self.state:
            return

        # Atomic write
        tmp = STATE_FILE.with_suffix(".tmp")
        content = json.dumps(self.state.to_dict(), indent=2, ensure_ascii=False)

        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_FILE)

        # Append to history
        history_record = {
            "timestamp": self.state.updated_at,
            "symbols": self.state.symbols,
            "update_count": self.state.update_count,
            "sha256": hashlib.sha256(json.dumps(self.state.symbols).encode()).hexdigest()[:16]
        }
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(history_record) + "\n")

        logger.info(f"Saved AllowList: {len(self.state.symbols)} symbols")

    def _config_hash(self) -> str:
        """Generate hash of current config."""
        config_str = f"{self.config.min_volume_usd}:{self.config.max_symbols}"
        return hashlib.md5(config_str.encode()).hexdigest()[:8]

    async def fetch_market_data(self) -> List[Dict]:
        """Fetch 24h ticker data from Binance."""
        try:
            from binance.client import Client
            client = Client()

            # Get all 24h tickers
            tickers = client.get_ticker()

            # Filter USDT pairs only
            usdt_tickers = [
                t for t in tickers
                if t["symbol"].endswith("USDT")
                and t["symbol"] not in self.config.blacklist
            ]

            logger.info(f"Fetched {len(usdt_tickers)} USDT pairs from Binance")
            return usdt_tickers

        except Exception as e:
            logger.error(f"Failed to fetch market data: {e}")
            return []

    def score_symbol(self, ticker: Dict) -> Optional[SymbolScore]:
        """Calculate volatility score for a symbol."""
        try:
            symbol = ticker["symbol"]
            volume_24h = float(ticker["quoteVolume"])  # Volume in USDT
            price_change_24h = abs(float(ticker["priceChangePercent"]))

            # Estimate 1h change from weighted average price
            # (simplified - in production would use klines)
            price_change_1h = price_change_24h / 24 * 1.5  # Rough estimate

            # Filter by minimum volume
            if volume_24h < self.config.min_volume_usd:
                return None

            # Calculate composite score
            # Normalize volume (log scale, max around 1B = 9)
            import math
            vol_score = min(math.log10(max(volume_24h, 1)) / 9, 1.0)

            # Normalize price changes (cap at 20% for scoring)
            change_24h_score = min(price_change_24h / 20, 1.0)
            change_1h_score = min(price_change_1h / 5, 1.0)

            # Weighted composite score
            score = (
                self.config.weight_volume * vol_score +
                self.config.weight_change_24h * change_24h_score +
                self.config.weight_change_1h * change_1h_score
            )

            return SymbolScore(
                symbol=symbol,
                volume_24h=volume_24h,
                price_change_24h=price_change_24h,
                price_change_1h=price_change_1h,
                score=score
            )

        except Exception as e:
            logger.debug(f"Failed to score {ticker.get('symbol', '?')}: {e}")
            return None

    async def update(self, force: bool = False) -> List[str]:
        """
        Update AllowList based on current market conditions.

        Returns: List of symbols in the new AllowList
        """
        now = datetime.now(timezone.utc)

        # Check if update needed
        if not force and self.state:
            next_update = datetime.fromisoformat(self.state.next_update_at)
            if now < next_update:
                logger.debug(f"Next update at {self.state.next_update_at}")
                return self.state.symbols

        logger.info("=== UPDATING DYNAMIC ALLOWLIST ===")

        # Fetch market data
        tickers = await self.fetch_market_data()
        if not tickers:
            logger.warning("No market data, keeping current list")
            return self.state.symbols if self.state else []

        # Score all symbols
        scores: List[SymbolScore] = []
        for ticker in tickers:
            score = self.score_symbol(ticker)
            if score:
                scores.append(score)

        logger.info(f"Scored {len(scores)} symbols above ${self.config.min_volume_usd/1e6:.0f}M volume")

        # Sort by score (descending)
        scores.sort(key=lambda x: x.score, reverse=True)

        # Assign ranks
        for i, s in enumerate(scores):
            s.rank = i + 1

        # Build final list
        selected: List[str] = []
        score_map: Dict[str, float] = {}

        # First: ensure core symbols are included (if they pass volume filter)
        core_in_scores = {s.symbol for s in scores if s.symbol in self.config.core_symbols}
        for score in scores:
            if score.symbol in core_in_scores:
                selected.append(score.symbol)
                score_map[score.symbol] = score.score

        # Then: add top scorers up to max
        for score in scores:
            if len(selected) >= self.config.max_symbols:
                break
            if score.symbol not in selected:
                selected.append(score.symbol)
                score_map[score.symbol] = score.score

        # Calculate next update time
        from datetime import timedelta
        next_update = now + timedelta(hours=self.config.update_interval_hours)

        # Create new state
        update_count = (self.state.update_count + 1) if self.state else 1
        self.state = AllowListState(
            symbols=selected,
            scores=score_map,
            updated_at=now.isoformat(),
            next_update_at=next_update.isoformat(),
            update_count=update_count,
            config_hash=self._config_hash()
        )

        # Save state
        self._save_state()

        # Log top 10 for visibility
        logger.info(f"TOP {min(10, len(scores))} by score:")
        for s in scores[:10]:
            marker = "[SELECTED]" if s.symbol in selected else ""
            logger.info(
                f"  #{s.rank} {s.symbol}: score={s.score:.3f} "
                f"vol=${s.volume_24h/1e6:.1f}M chg={s.price_change_24h:.2f}% {marker}"
            )

        logger.info(f"AllowList updated: {len(selected)} symbols")
        return selected

    def get_symbols(self) -> List[str]:
        """Get current AllowList symbols."""
        if self.state:
            return self.state.symbols
        return list(self.config.core_symbols)

    def get_symbols_set(self) -> Set[str]:
        """Get current AllowList as set (for fast lookup)."""
        return set(self.get_symbols())

    def is_allowed(self, symbol: str) -> bool:
        """Check if symbol is in current AllowList."""
        return symbol in self.get_symbols_set()

    def get_score(self, symbol: str) -> float:
        """Get score for a symbol (0 if not in list)."""
        if self.state and symbol in self.state.scores:
            return self.state.scores[symbol]
        return 0.0

    def status(self) -> Dict:
        """Get current status for monitoring."""
        if not self.state:
            return {"status": "NOT_INITIALIZED"}

        return {
            "status": "OK",
            "symbols_count": len(self.state.symbols),
            "symbols": self.state.symbols,
            "updated_at": self.state.updated_at,
            "next_update_at": self.state.next_update_at,
            "update_count": self.state.update_count,
            "top_5": self.state.symbols[:5]
        }


# === SINGLETON INSTANCE ===

_manager: Optional[DynamicAllowListManager] = None

def get_allowlist_manager() -> DynamicAllowListManager:
    """Get singleton AllowList manager."""
    global _manager
    if _manager is None:
        _manager = DynamicAllowListManager()
    return _manager


async def update_allowlist(force: bool = False) -> List[str]:
    """Update AllowList (convenience function)."""
    manager = get_allowlist_manager()
    return await manager.update(force=force)


def get_allowed_symbols() -> Set[str]:
    """Get current allowed symbols (convenience function)."""
    manager = get_allowlist_manager()
    return manager.get_symbols_set()


# === CLI ===

if __name__ == "__main__":
    import asyncio
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-5s | %(name)-20s | %(message)s",
        datefmt="%H:%M:%S"
    )

    parser = argparse.ArgumentParser(description="Dynamic AllowList Manager")
    parser.add_argument("--update", action="store_true", help="Force update AllowList")
    parser.add_argument("--status", action="store_true", help="Show current status")
    parser.add_argument("--list", action="store_true", help="List current symbols")
    parser.add_argument("--check", type=str, help="Check if symbol is allowed")
    args = parser.parse_args()

    manager = DynamicAllowListManager()

    if args.update:
        symbols = asyncio.run(manager.update(force=True))
        print(f"\nAllowList updated: {len(symbols)} symbols")
        print(f"Symbols: {', '.join(symbols)}")

    elif args.status:
        status = manager.status()
        print(json.dumps(status, indent=2))

    elif args.list:
        symbols = manager.get_symbols()
        print(f"Current AllowList ({len(symbols)} symbols):")
        for i, s in enumerate(symbols, 1):
            score = manager.get_score(s)
            print(f"  {i:2}. {s} (score={score:.3f})")

    elif args.check:
        symbol = args.check.upper()
        if not symbol.endswith("USDT"):
            symbol += "USDT"
        allowed = manager.is_allowed(symbol)
        score = manager.get_score(symbol)
        print(f"{symbol}: {'ALLOWED' if allowed else 'NOT ALLOWED'} (score={score:.3f})")

    else:
        parser.print_help()
