# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 13:10:00 UTC
# Purpose: Hot AllowList - instant pump detection + auto-add
# === END SIGNATURE ===

"""
Hot AllowList - Мгновенное добавление монет при детекции pump-а

Логика:
1. MoonBot детектит pump (buys/sec > 30, delta > 2%)
2. Fast Scanner проверяет сигнал
3. Eye of God даёт быструю оценку
4. Монета добавляется в HOT_LIST на 15 минут
5. После TTL - удаляется автоматически

НЕ СПРАШИВАЕТ пользователя - решает автономно!
"""

import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from threading import Lock

logger = logging.getLogger("HOT_ALLOWLIST")

# === CONFIGURATION ===

@dataclass
class HotListConfig:
    """Конфигурация Hot AllowList."""
    # Пороги для автодобавления
    min_buys_per_sec: float = 30.0      # Минимум buys/sec для pump
    min_delta_pct: float = 2.0          # Минимум delta %
    min_vol_raise_pct: float = 100.0    # Минимум рост объёма %

    # TTL (время жизни в hot list)
    hot_ttl_seconds: int = 900          # 15 минут

    # Лимиты
    max_hot_symbols: int = 10           # Максимум в hot list

    # Risk adjustments для hot symbols
    position_size_multiplier: float = 0.5   # 50% от обычного размера
    timeout_multiplier: float = 0.5         # 50% от обычного таймаута

    # Blacklist (никогда не добавлять)
    blacklist: Set[str] = None

    def __post_init__(self):
        if self.blacklist is None:
            self.blacklist = {
                # Стейблкоины
                "USDTUSDT", "USDCUSDT", "BUSDUSDT", "DAIUSDT",
                # Collapsed
                "LUNAUSDT", "USTCUSDT", "LEVERUSDT",
            }


@dataclass
class HotSymbol:
    """Запись о горячем символе."""
    symbol: str
    added_at: float          # Unix timestamp
    expires_at: float        # Unix timestamp
    pump_score: float        # 0-1
    buys_per_sec: float
    delta_pct: float
    vol_raise_pct: float
    daily_volume_usd: float
    trigger_reason: str      # "pump_detect", "volume_spike", etc.

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def ttl_seconds(self) -> int:
        return max(0, int(self.expires_at - time.time()))


# === STATE ===

STATE_DIR = Path("state/ai/hot_allowlist")
STATE_FILE = STATE_DIR / "hot_symbols.json"
HISTORY_FILE = STATE_DIR / "hot_history.jsonl"


class HotAllowList:
    """
    Горячий AllowList для мгновенного добавления pump-монет.

    Автономная система - НЕ спрашивает пользователя!
    """

    def __init__(self, config: HotListConfig = None):
        self.config = config or HotListConfig()
        self._symbols: Dict[str, HotSymbol] = {}
        self._lock = Lock()

        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._load_state()

    def _load_state(self):
        """Загрузить состояние."""
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                for sym_data in data.get("symbols", []):
                    sym = HotSymbol(**sym_data)
                    if not sym.is_expired():
                        self._symbols[sym.symbol] = sym
                logger.info(f"Loaded {len(self._symbols)} hot symbols")
            except Exception as e:
                logger.warning(f"Failed to load hot state: {e}")

    def _save_state(self):
        """Сохранить состояние (атомарно)."""
        data = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "symbols": [asdict(s) for s in self._symbols.values()]
        }

        tmp = STATE_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_FILE)

    def _log_history(self, symbol: HotSymbol, action: str):
        """Записать в историю."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "symbol": symbol.symbol,
            "pump_score": symbol.pump_score,
            "buys_per_sec": symbol.buys_per_sec,
            "delta_pct": symbol.delta_pct,
        }
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def calculate_pump_score(self, signal: dict) -> Tuple[float, List[str]]:
        """
        Рассчитать pump score для сигнала.

        Returns:
            (score 0-1, list of detected patterns)
        """
        patterns = []
        scores = []

        buys = signal.get("buys_per_sec", 0)
        delta = signal.get("delta_pct", 0)
        vol_raise = signal.get("vol_raise_pct", 0)

        # Pattern 1: High buys/sec
        if buys >= 100:
            scores.append(1.0)
            patterns.append("EXTREME_BUYS")
        elif buys >= 50:
            scores.append(0.8)
            patterns.append("HIGH_BUYS")
        elif buys >= 30:
            scores.append(0.6)
            patterns.append("ACTIVE_BUYS")

        # Pattern 2: Strong delta
        if delta >= 5:
            scores.append(1.0)
            patterns.append("STRONG_PUMP")
        elif delta >= 3:
            scores.append(0.7)
            patterns.append("MEDIUM_PUMP")
        elif delta >= 2:
            scores.append(0.5)
            patterns.append("LIGHT_PUMP")

        # Pattern 3: Volume spike
        if vol_raise >= 200:
            scores.append(0.9)
            patterns.append("VOLUME_EXPLOSION")
        elif vol_raise >= 100:
            scores.append(0.6)
            patterns.append("VOLUME_SPIKE")

        if not scores:
            return 0.0, []

        # Weighted average
        final_score = sum(scores) / len(scores)

        # Bonus for multiple patterns
        if len(patterns) >= 3:
            final_score = min(1.0, final_score * 1.2)

        return final_score, patterns

    def should_add(self, signal: dict) -> Tuple[bool, str, float]:
        """
        Проверить нужно ли добавить символ в hot list.

        Returns:
            (should_add, reason, pump_score)
        """
        symbol = signal.get("symbol", "")

        # Check blacklist
        if symbol in self.config.blacklist:
            return False, "blacklisted", 0.0

        # Check if already in hot list
        if symbol in self._symbols:
            existing = self._symbols[symbol]
            if not existing.is_expired():
                # Extend TTL if still pumping
                return False, "already_hot", existing.pump_score

        # Check max limit
        self._cleanup_expired()
        if len(self._symbols) >= self.config.max_hot_symbols:
            return False, "hot_list_full", 0.0

        # Calculate pump score
        pump_score, patterns = self.calculate_pump_score(signal)

        # Check thresholds
        buys = signal.get("buys_per_sec", 0)
        delta = signal.get("delta_pct", 0)
        vol_raise = signal.get("vol_raise_pct", 0)

        if buys < self.config.min_buys_per_sec:
            return False, f"low_buys ({buys:.1f} < {self.config.min_buys_per_sec})", pump_score

        if delta < self.config.min_delta_pct:
            return False, f"low_delta ({delta:.1f}% < {self.config.min_delta_pct}%)", pump_score

        if pump_score < 0.5:
            return False, f"low_pump_score ({pump_score:.2f})", pump_score

        # All checks passed!
        reason = f"PUMP_DETECTED: {', '.join(patterns)}"
        return True, reason, pump_score

    def add_symbol(self, signal: dict) -> Optional[HotSymbol]:
        """
        Добавить символ в hot list (автоматически).

        Returns:
            HotSymbol if added, None if rejected
        """
        should, reason, score = self.should_add(signal)

        symbol = signal.get("symbol", "UNKNOWN")

        if not should:
            logger.debug(f"HOT_LIST SKIP {symbol}: {reason}")
            return None

        with self._lock:
            now = time.time()
            hot_sym = HotSymbol(
                symbol=symbol,
                added_at=now,
                expires_at=now + self.config.hot_ttl_seconds,
                pump_score=score,
                buys_per_sec=signal.get("buys_per_sec", 0),
                delta_pct=signal.get("delta_pct", 0),
                vol_raise_pct=signal.get("vol_raise_pct", 0),
                daily_volume_usd=signal.get("daily_volume", 0),
                trigger_reason=reason,
            )

            self._symbols[symbol] = hot_sym
            self._save_state()
            self._log_history(hot_sym, "ADDED")

        logger.info(f"🔥 HOT_LIST ADD: {symbol} (score={score:.2f}, TTL={self.config.hot_ttl_seconds}s)")
        logger.info(f"   Reason: {reason}")

        return hot_sym

    def _cleanup_expired(self):
        """Удалить просроченные символы."""
        expired = [s for s, sym in self._symbols.items() if sym.is_expired()]
        for s in expired:
            sym = self._symbols.pop(s)
            self._log_history(sym, "EXPIRED")
            logger.info(f"HOT_LIST EXPIRED: {s}")

        if expired:
            self._save_state()

    def get_hot_symbols(self) -> Set[str]:
        """Получить текущий hot list."""
        self._cleanup_expired()
        return set(self._symbols.keys())

    def is_hot(self, symbol: str) -> bool:
        """Проверить в hot list ли символ."""
        if symbol not in self._symbols:
            return False
        sym = self._symbols[symbol]
        if sym.is_expired():
            self._cleanup_expired()
            return False
        return True

    def get_hot_info(self, symbol: str) -> Optional[HotSymbol]:
        """Получить информацию о hot символе."""
        if symbol in self._symbols:
            sym = self._symbols[symbol]
            if not sym.is_expired():
                return sym
        return None

    def get_risk_adjustments(self, symbol: str) -> dict:
        """
        Получить risk adjustments для hot символа.

        Hot symbols торгуются с уменьшенным размером и таймаутом.
        """
        if not self.is_hot(symbol):
            return {"position_multiplier": 1.0, "timeout_multiplier": 1.0}

        return {
            "position_multiplier": self.config.position_size_multiplier,
            "timeout_multiplier": self.config.timeout_multiplier,
        }

    def status(self) -> dict:
        """Получить статус hot list."""
        self._cleanup_expired()

        symbols_info = []
        for sym in self._symbols.values():
            symbols_info.append({
                "symbol": sym.symbol,
                "pump_score": sym.pump_score,
                "ttl_sec": sym.ttl_seconds(),
                "buys_per_sec": sym.buys_per_sec,
            })

        return {
            "count": len(self._symbols),
            "max": self.config.max_hot_symbols,
            "symbols": symbols_info,
            "config": {
                "min_buys_per_sec": self.config.min_buys_per_sec,
                "min_delta_pct": self.config.min_delta_pct,
                "hot_ttl_seconds": self.config.hot_ttl_seconds,
            }
        }


# === SINGLETON ===

_hot_list: Optional[HotAllowList] = None

def get_hot_allowlist() -> HotAllowList:
    """Get singleton hot allowlist."""
    global _hot_list
    if _hot_list is None:
        _hot_list = HotAllowList()
    return _hot_list


def process_signal_for_hot_list(signal: dict) -> Optional[HotSymbol]:
    """
    Обработать сигнал и автоматически добавить в hot list если нужно.

    Вызывается из MoonBot integration при каждом сигнале.
    """
    hot_list = get_hot_allowlist()
    return hot_list.add_symbol(signal)


# === CLI ===

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-5s | %(name)-15s | %(message)s",
        datefmt="%H:%M:%S"
    )

    parser = argparse.ArgumentParser(description="Hot AllowList Manager")
    parser.add_argument("--status", action="store_true", help="Show status")
    parser.add_argument("--test", type=str, help="Test signal (JSON)")
    args = parser.parse_args()

    hot = HotAllowList()

    if args.status:
        status = hot.status()
        print(json.dumps(status, indent=2))

    elif args.test:
        signal = json.loads(args.test)
        result = hot.add_symbol(signal)
        if result:
            print(f"ADDED: {result.symbol} (score={result.pump_score:.2f})")
        else:
            should, reason, score = hot.should_add(signal)
            print(f"REJECTED: {reason} (score={score:.2f})")

    else:
        parser.print_help()
