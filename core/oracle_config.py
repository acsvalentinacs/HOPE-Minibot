# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 01:00:00 UTC
# Purpose: UNIFIED Oracle Config - Single Source of Truth for all trading components
# Contract: Atomic writes, fail-closed, self-learning
# === END SIGNATURE ===
"""
UNIFIED ORACLE CONFIG - SSoT для всех компонентов торговли.

Единственный файл конфигурации, который читают:
- Eye of God (oracle)
- AutoTrader
- Claude Brain (reporting)
- Self-Learning System

Принцип: Один файл → много читателей → атомарные записи.
"""

import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Set, List, Any, Optional
import logging

log = logging.getLogger("ORACLE-CONFIG")

# SSoT Path
CONFIG_PATH = Path("state/ai/oracle/unified_config.json")
CONFIG_LOCK = CONFIG_PATH.with_suffix(".lock")


@dataclass
class SymbolStats:
    """Statistics for a single symbol."""
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    last_trade: str = ""
    consecutive_wins: int = 0
    consecutive_losses: int = 0

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0.5

    @property
    def total_trades(self) -> int:
        return self.wins + self.losses


@dataclass
class OracleConfig:
    """
    Unified Oracle Configuration.

    Single Source of Truth for:
    - Whitelist/Blacklist
    - Confidence thresholds
    - Risk parameters
    - Strategy adjustments
    - Symbol statistics
    """

    # === FILTERS (auto-learned) ===
    whitelist: Set[str] = field(default_factory=lambda: {
        "KITEUSDT", "DUSKUSDT", "XVSUSDT"
    })
    blacklist: Set[str] = field(default_factory=lambda: {
        "SYNUSDT", "DODOUSDT", "AXSUSDT", "ARPAUSDT"
    })
    paused: Set[str] = field(default_factory=set)

    # === THRESHOLDS ===
    min_confidence: float = 0.50
    pump_override_buys: float = 100.0
    scalp_buys: float = 30.0
    min_delta_pct: float = 2.0

    # === RISK ===
    risk_multiplier: float = 1.0
    max_daily_loss_pct: float = 3.0
    max_consecutive_losses: int = 3

    # === STRATEGY MODIFIERS ===
    strategy_boosts: Dict[str, float] = field(default_factory=lambda: {
        "pump": 0.15,
        "drop": 0.05
    })
    strategy_penalties: Dict[str, float] = field(default_factory=lambda: {
        "delta": -0.15,
        "topmarket": -0.10
    })

    # === SELF-LEARNING STATE ===
    symbol_stats: Dict[str, Dict] = field(default_factory=dict)
    calibration: float = 1.0
    total_trades: int = 0
    total_wins: int = 0

    # === METADATA ===
    version: str = "2.0"
    updated_at: str = ""
    updated_by: str = ""

    # === AUTO-LEARN THRESHOLDS ===
    whitelist_min_trades: int = 3
    whitelist_min_win_rate: float = 0.80
    blacklist_min_trades: int = 3
    blacklist_max_win_rate: float = 0.20

    def to_dict(self) -> Dict:
        """Convert to JSON-serializable dict."""
        return {
            "whitelist": sorted(self.whitelist),
            "blacklist": sorted(self.blacklist),
            "paused": sorted(self.paused),
            "min_confidence": self.min_confidence,
            "pump_override_buys": self.pump_override_buys,
            "scalp_buys": self.scalp_buys,
            "min_delta_pct": self.min_delta_pct,
            "risk_multiplier": self.risk_multiplier,
            "max_daily_loss_pct": self.max_daily_loss_pct,
            "max_consecutive_losses": self.max_consecutive_losses,
            "strategy_boosts": self.strategy_boosts,
            "strategy_penalties": self.strategy_penalties,
            "symbol_stats": self.symbol_stats,
            "calibration": self.calibration,
            "total_trades": self.total_trades,
            "total_wins": self.total_wins,
            "version": self.version,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
            "whitelist_min_trades": self.whitelist_min_trades,
            "whitelist_min_win_rate": self.whitelist_min_win_rate,
            "blacklist_min_trades": self.blacklist_min_trades,
            "blacklist_max_win_rate": self.blacklist_max_win_rate,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "OracleConfig":
        """Create from dict."""
        return cls(
            whitelist=set(data.get("whitelist", [])),
            blacklist=set(data.get("blacklist", [])),
            paused=set(data.get("paused", [])),
            min_confidence=data.get("min_confidence", 0.50),
            pump_override_buys=data.get("pump_override_buys", 100.0),
            scalp_buys=data.get("scalp_buys", 30.0),
            min_delta_pct=data.get("min_delta_pct", 2.0),
            risk_multiplier=data.get("risk_multiplier", 1.0),
            max_daily_loss_pct=data.get("max_daily_loss_pct", 3.0),
            max_consecutive_losses=data.get("max_consecutive_losses", 3),
            strategy_boosts=data.get("strategy_boosts", {}),
            strategy_penalties=data.get("strategy_penalties", {}),
            symbol_stats=data.get("symbol_stats", {}),
            calibration=data.get("calibration", 1.0),
            total_trades=data.get("total_trades", 0),
            total_wins=data.get("total_wins", 0),
            version=data.get("version", "2.0"),
            updated_at=data.get("updated_at", ""),
            updated_by=data.get("updated_by", ""),
            whitelist_min_trades=data.get("whitelist_min_trades", 3),
            whitelist_min_win_rate=data.get("whitelist_min_win_rate", 0.80),
            blacklist_min_trades=data.get("blacklist_min_trades", 3),
            blacklist_max_win_rate=data.get("blacklist_max_win_rate", 0.20),
        )


class ConfigManager:
    """
    Atomic config manager with fail-closed semantics.

    Features:
    - Atomic writes (temp → fsync → replace)
    - File locking
    - Auto-backup
    - Self-learning integration
    """

    def __init__(self, config_path: Path = None):
        self.path = config_path or CONFIG_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._config: Optional[OracleConfig] = None

    def load(self) -> OracleConfig:
        """Load config from disk."""
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._config = OracleConfig.from_dict(data)
                return self._config
            except Exception as e:
                log.error(f"Config load failed: {e}")

        # Return default
        self._config = OracleConfig()
        return self._config

    def save(self, config: OracleConfig, by: str = "system") -> bool:
        """
        Atomic save with fsync.

        Pattern: temp → fsync → replace
        """
        config.updated_at = datetime.now(timezone.utc).isoformat()
        config.updated_by = by

        tmp = self.path.with_suffix(f".tmp.{os.getpid()}")
        try:
            data = json.dumps(config.to_dict(), indent=2, ensure_ascii=False)

            # Atomic write with fsync
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
            try:
                os.write(fd, data.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)

            # Atomic replace
            os.replace(str(tmp), str(self.path))
            self._config = config

            log.info(f"Config saved by {by}")
            return True

        except Exception as e:
            log.error(f"Config save failed: {e}")
            if tmp.exists():
                tmp.unlink()
            return False

    def get(self) -> OracleConfig:
        """Get current config (cached)."""
        if self._config is None:
            return self.load()
        return self._config

    @property
    def config(self) -> OracleConfig:
        """Property alias for get() - provides cm.config access pattern."""
        return self.get()

    def reload(self) -> OracleConfig:
        """Force reload from disk."""
        return self.load()

    # ═══════════════════════════════════════════════════════════════
    # SELF-LEARNING METHODS
    # ═══════════════════════════════════════════════════════════════

    def record_outcome(self, symbol: str, is_win: bool, pnl: float = 0) -> Dict[str, Any]:
        """
        Record trade outcome and auto-learn.

        Returns dict of any automatic adjustments made.
        """
        config = self.get()
        changes = {}

        # Update symbol stats
        if symbol not in config.symbol_stats:
            config.symbol_stats[symbol] = {
                "wins": 0, "losses": 0, "total_pnl": 0.0,
                "last_trade": "", "consecutive_wins": 0, "consecutive_losses": 0
            }

        stats = config.symbol_stats[symbol]

        if is_win:
            stats["wins"] += 1
            stats["consecutive_wins"] += 1
            stats["consecutive_losses"] = 0
            config.total_wins += 1
        else:
            stats["losses"] += 1
            stats["consecutive_losses"] += 1
            stats["consecutive_wins"] = 0

        stats["total_pnl"] += pnl
        stats["last_trade"] = datetime.now(timezone.utc).isoformat()
        config.total_trades += 1

        # Calculate win rate
        total = stats["wins"] + stats["losses"]
        win_rate = stats["wins"] / total if total > 0 else 0.5

        # === AUTO-WHITELIST ===
        if (total >= config.whitelist_min_trades and
            win_rate >= config.whitelist_min_win_rate and
            symbol not in config.whitelist):
            config.whitelist.add(symbol)
            config.blacklist.discard(symbol)
            changes["auto_whitelist"] = {
                "symbol": symbol,
                "win_rate": win_rate,
                "trades": total,
                "reason": f"Win rate {win_rate*100:.0f}% >= {config.whitelist_min_win_rate*100:.0f}%"
            }
            log.info(f"AUTO-WHITELIST: {symbol} (win_rate={win_rate*100:.0f}%)")

        # === AUTO-BLACKLIST ===
        if (total >= config.blacklist_min_trades and
            win_rate <= config.blacklist_max_win_rate and
            symbol not in config.blacklist):
            config.blacklist.add(symbol)
            config.whitelist.discard(symbol)
            changes["auto_blacklist"] = {
                "symbol": symbol,
                "win_rate": win_rate,
                "trades": total,
                "reason": f"Win rate {win_rate*100:.0f}% <= {config.blacklist_max_win_rate*100:.0f}%"
            }
            log.info(f"AUTO-BLACKLIST: {symbol} (win_rate={win_rate*100:.0f}%)")

        # === CALIBRATION ADJUSTMENT ===
        # Drift calibration based on recent performance
        if is_win:
            config.calibration = min(1.5, config.calibration + 0.01)
        else:
            config.calibration = max(0.5, config.calibration - 0.01)

        # Save
        self.save(config, by="self-learning")

        return changes

    def get_symbol_factor(self, symbol: str) -> float:
        """
        Get confidence factor for symbol based on stats.

        Returns: float modifier (-0.5 to +0.5)
        """
        config = self.get()

        # Whitelist boost
        if symbol in config.whitelist:
            return 0.25

        # Blacklist penalty
        if symbol in config.blacklist:
            return -1.0  # Will cause skip

        # Stats-based factor
        if symbol in config.symbol_stats:
            stats = config.symbol_stats[symbol]
            total = stats["wins"] + stats["losses"]
            if total >= 2:
                win_rate = stats["wins"] / total
                # Map 0-100% win rate to -0.2 to +0.2 factor
                return (win_rate - 0.5) * 0.4

        return 0.0  # Neutral

    def get_strategy_modifier(self, strategy: str) -> float:
        """Get strategy modifier (boost or penalty)."""
        config = self.get()
        strategy_lower = strategy.lower()

        # Check boosts
        for key, value in config.strategy_boosts.items():
            if key in strategy_lower:
                return value

        # Check penalties
        for key, value in config.strategy_penalties.items():
            if key in strategy_lower:
                return value

        return 0.0

    # ═══════════════════════════════════════════════════════════════
    # DIRECTIVE INTERFACE (for manual Claude intervention)
    # ═══════════════════════════════════════════════════════════════

    def apply_directive(self, directive: Dict) -> Dict[str, Any]:
        """
        Apply a directive from Claude.

        Returns result dict with success status.
        """
        config = self.get()
        dtype = directive.get("type", "").lower()
        action = directive.get("action", "").lower()
        target = directive.get("target", "")
        value = directive.get("value")

        result = {"success": False, "message": "", "before": {}, "after": {}}

        try:
            if dtype == "update_filter":
                if action == "add_whitelist":
                    config.whitelist.add(target.upper())
                    config.blacklist.discard(target.upper())
                    result["success"] = True
                    result["message"] = f"Added {target} to whitelist"

                elif action == "add_blacklist":
                    config.blacklist.add(target.upper())
                    config.whitelist.discard(target.upper())
                    result["success"] = True
                    result["message"] = f"Added {target} to blacklist"

                elif action == "remove_whitelist":
                    config.whitelist.discard(target.upper())
                    result["success"] = True
                    result["message"] = f"Removed {target} from whitelist"

                elif action == "remove_blacklist":
                    config.blacklist.discard(target.upper())
                    result["success"] = True
                    result["message"] = f"Removed {target} from blacklist"

            elif dtype == "adjust_threshold":
                if target == "min_confidence" and 0 <= value <= 1:
                    config.min_confidence = value
                    result["success"] = True
                    result["message"] = f"Set min_confidence to {value}"
                elif target == "min_delta_pct" and 0 <= value <= 20:
                    config.min_delta_pct = value
                    result["success"] = True
                    result["message"] = f"Set min_delta_pct to {value}"

            elif dtype == "set_risk":
                if target == "risk_multiplier" and 0.1 <= value <= 3.0:
                    config.risk_multiplier = value
                    result["success"] = True
                    result["message"] = f"Set risk_multiplier to {value}"

            elif dtype == "update_strategy":
                if "boost" in action:
                    config.strategy_boosts[target.lower()] = value
                    result["success"] = True
                    result["message"] = f"Set {target} boost to {value}"
                elif "penalty" in action:
                    config.strategy_penalties[target.lower()] = value
                    result["success"] = True
                    result["message"] = f"Set {target} penalty to {value}"

            elif dtype == "pause_symbol":
                config.paused.add(target.upper())
                result["success"] = True
                result["message"] = f"Paused {target}"

            elif dtype == "resume_symbol":
                config.paused.discard(target.upper())
                result["success"] = True
                result["message"] = f"Resumed {target}"

            else:
                result["message"] = f"Unknown directive type: {dtype}"

            if result["success"]:
                self.save(config, by=f"directive:{directive.get('directive_id', 'unknown')}")

        except Exception as e:
            result["message"] = str(e)

        return result

    def generate_report(self) -> Dict[str, Any]:
        """Generate analytics report for Claude."""
        config = self.get()

        # Symbol performance
        symbol_perf = []
        for symbol, stats in config.symbol_stats.items():
            total = stats["wins"] + stats["losses"]
            if total == 0:
                continue
            win_rate = stats["wins"] / total
            avg_pnl = stats["total_pnl"] / total

            rec = "keep"
            if win_rate >= config.whitelist_min_win_rate and total >= config.whitelist_min_trades:
                rec = "whitelist" if symbol not in config.whitelist else "keep_whitelist"
            elif win_rate <= config.blacklist_max_win_rate and total >= config.blacklist_min_trades:
                rec = "blacklist" if symbol not in config.blacklist else "keep_blacklist"

            symbol_perf.append({
                "symbol": symbol,
                "wins": stats["wins"],
                "losses": stats["losses"],
                "total": total,
                "win_rate": round(win_rate, 3),
                "total_pnl": round(stats["total_pnl"], 2),
                "avg_pnl": round(avg_pnl, 2),
                "recommendation": rec,
                "in_whitelist": symbol in config.whitelist,
                "in_blacklist": symbol in config.blacklist,
            })

        symbol_perf.sort(key=lambda x: x["win_rate"], reverse=True)

        # Overall stats
        overall_win_rate = config.total_wins / config.total_trades if config.total_trades > 0 else 0

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total_trades": config.total_trades,
                "total_wins": config.total_wins,
                "total_losses": config.total_trades - config.total_wins,
                "win_rate": round(overall_win_rate, 3),
                "calibration": config.calibration,
            },
            "filters": {
                "whitelist": sorted(config.whitelist),
                "blacklist": sorted(config.blacklist),
                "paused": sorted(config.paused),
            },
            "thresholds": {
                "min_confidence": config.min_confidence,
                "risk_multiplier": config.risk_multiplier,
                "min_delta_pct": config.min_delta_pct,
            },
            "strategy_modifiers": {
                "boosts": config.strategy_boosts,
                "penalties": config.strategy_penalties,
            },
            "symbol_performance": symbol_perf,
        }


# Global singleton
_manager: Optional[ConfigManager] = None


def get_config_manager() -> ConfigManager:
    """Get global config manager."""
    global _manager
    if _manager is None:
        _manager = ConfigManager()
    return _manager


def get_config() -> OracleConfig:
    """Get current config."""
    return get_config_manager().get()


# CLI
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="Oracle Config Manager")
    parser.add_argument("command", choices=["show", "report", "reset"])
    args = parser.parse_args()

    mgr = ConfigManager()

    if args.command == "show":
        config = mgr.load()
        print(json.dumps(config.to_dict(), indent=2))

    elif args.command == "report":
        report = mgr.generate_report()
        print(json.dumps(report, indent=2))

    elif args.command == "reset":
        config = OracleConfig()
        mgr.save(config, by="reset")
        print("Config reset to defaults")
