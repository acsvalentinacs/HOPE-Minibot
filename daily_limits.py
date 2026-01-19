import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, Tuple

log = logging.getLogger(__name__)


@dataclass
class SymbolLimits:
    """
    Настройки лимитов по символу.

    mode:
        "both"       — разрешены и лонги, и шорты
        "only_long"  — только лонги
        "only_short" — только шорты
        "none"       — вообще не торговать символ
    """
    mode: str = "both"
    max_daily_loss_r: float = 3.0        # Макс. убыток за день в R
    max_trades_day: int = 10             # Макс. число сделок в день
    risk_per_trade: float = 0.01         # Риск на сделку (0.01 = 1% equity)

    # Дополнительные параметры под стратегию (ATR и т.п.)
    sl_atr: float = 2.0
    tp_atr: float = 6.0


@dataclass
class DailyState:
    """
    Состояние торговли за текущий день по конкретному символу.
    """
    current_date: date
    trades_count: int = 0
    accumulated_r: float = 0.0  # Накопленный результат в R


class DailyLimiter:
    """
    Лимитер по одному символу.
    Следит за:
      - сменой дня;
      - дневным убытком в R;
      - дневным числом сделок;
      - режимом (mode).
    """

    def __init__(self, symbol: str, limits: SymbolLimits) -> None:
        self.symbol = symbol
        self.limits = limits
        self.state = DailyState(current_date=date.today())

    def on_bar(self, current_ts_date: date) -> None:
        """
        Вызывать на каждом баре (или, минимум, при каждом проходе по символу).
        Если дата сменилась — сбрасываем дневное состояние.
        """
        if self.state.current_date != current_ts_date:
            # Новый торговый день — всё с нуля
            self.state = DailyState(current_date=current_ts_date)

    def can_open(self, side: str) -> Tuple[bool, str]:
        """
        Можно ли открывать НОВУЮ сделку?
        side: "long" или "short".
        Возвращает (ok, reason).
        """
        # 1) Дневной лимит по R
        if self.state.accumulated_r <= -self.limits.max_daily_loss_r:
            return (
                False,
                (
                    f"Daily Loss Limit hit: "
                    f"{self.state.accumulated_r:.2f}R <= "
                    f"-{self.limits.max_daily_loss_r:.2f}R"
                ),
            )

        # 2) Лимит по количеству сделок
        if self.state.trades_count >= self.limits.max_trades_day:
            return (
                False,
                (
                    "Max Trades Limit hit: "
                    f"{self.state.trades_count} >= {self.limits.max_trades_day}"
                ),
            )

        # 3) Режим (mode)
        mode = (self.limits.mode or "both").lower()

        if mode == "none":
            return False, "Mode is NONE → торги по символу отключены"

        if mode == "only_long" and side == "short":
            return False, "Mode is ONLY_LONG → шорты запрещены"

        if mode == "only_short" and side == "long":
            return False, "Mode is ONLY_SHORT → лонги запрещены"

        return True, "OK"

    def register_trade_result(self, r_multiple: float) -> None:
        """
        Регистрируем результат закрытой сделки в R.
        r_multiple:
            +1.0  — сделка дала +1R
            -1.0  — сделка дала -1R
            и т.д.
        """
        self.state.trades_count += 1
        self.state.accumulated_r += r_multiple


def _parse_symbol_limits_cfg(sym: str, cfg: dict) -> SymbolLimits:
    """
    Внутренняя функция: аккуратно превращает dict из JSON в SymbolLimits.
    Поддерживает несколько возможных названий полей.
    """
    return SymbolLimits(
        mode=str(cfg.get("mode", "both")),
        max_daily_loss_r=float(cfg.get("max_daily_loss_r", 3.0)),
        max_trades_day=int(
            cfg.get(
                "max_trades_per_day",
                cfg.get("max_trades_day", 10),
            )
        ),
        risk_per_trade=float(cfg.get("risk_per_trade", 0.01)),
        sl_atr=float(cfg.get("sl_atr", 2.0)),
        tp_atr=float(cfg.get("tp_atr", 6.0)),
    )


def load_symbol_limits_from_replay_config(path: Path) -> Dict[str, SymbolLimits]:
    """
    Читает replay_config.json и возвращает { "BTCUSDT": SymbolLimits, ... }.

    Поддерживает два варианта структуры:
      1) {"symbols": { "BTCUSDT": {...}, "ETHUSDT": {...} }}
      2) {"BTCUSDT": {...}, "ETHUSDT": {...}}
    """
    if not path.exists():
        log.warning("Replay config not found: %s", path)
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as e:
        log.error("Error parsing replay config %s: %s", path, e)
        return {}

    limits: Dict[str, SymbolLimits] = {}

    symbols_block = raw.get("symbols")
    if isinstance(symbols_block, dict):
        iterator = symbols_block.items()
    else:
        iterator = raw.items()

    for sym, cfg in iterator:
        if not isinstance(cfg, dict):
            continue
        try:
            limits[sym] = _parse_symbol_limits_cfg(sym, cfg)
        except Exception as e:
            log.error("Error parsing symbol config %s: %s", sym, e)

    return limits
