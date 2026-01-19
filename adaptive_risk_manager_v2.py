"""
AdaptiveRiskManagerV2 for HOPE v5.

Идея:
- не заменяет базовый RiskManager, а дополняет его;
- возвращает "мягкий" лимит позиции с учётом:
  * ATR (волатильность),
  * confidence (уверенность стратегии),
  * дневного PnL,
  * корреляции позиций,
  * количества уже открытых позиций (portfolio load).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional

from minibot.core.types import PositionInfo

logger = logging.getLogger(__name__)


@dataclass
class AdaptiveRiskConfig:
    max_position_size_usd: float = 100.0
    base_equity_usd: float = 1000.0
    avg_atr: float = 0.01
    max_daily_profit_usd: float = 300.0
    min_position_size_usd: float = 5.0
    # Карта групп корреляции: все символы из одной группы считаются сильно скоррелированными
    correlation_groups: Mapping[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.correlation_groups is None:
            # Простые дефолты под спот-мажоры, можно расширить в конфиге
            self.correlation_groups = {
                "BTCUSDT": "MAJORS",
                "ETHUSDT": "MAJORS",
                "BNBUSDT": "MAJORS",
                "SOLUSDT": "ALT_L1",
            }


class AdaptiveRiskManagerV2:
    """
    Использование:

        adaptive_cfg = AdaptiveRiskConfig(max_position_size_usd=risk_cfg.max_position_size_usd, ...)
        adaptive = AdaptiveRiskManagerV2(adaptive_cfg)

        size_usd = adaptive.calculate_position_size(
            symbol=tsig.symbol,
            atr=atr,
            signal_confidence=confidence,
            equity_usd=equity,
            open_positions=engine.positions,
            daily_pnl_usd=risk.daily_pnl_usd,
        )
    """

    def __init__(self, config: AdaptiveRiskConfig) -> None:
        self.config = config

    # ------------------------------------------------------------ helpers
    def _correlation_group(self, symbol: str) -> Optional[str]:
        symbol = symbol.upper()
        return self.config.correlation_groups.get(symbol)

    def _correlation_load(self, symbol: str, open_positions: Iterable[PositionInfo]) -> float:
        """
        Грубая оценка "нагрузки корреляции" — доля капитала в той же группе.
        0.0  → нет коррелированных позиций
        0.5+ → существенная нагрузка, надо резать размер
        """
        group = self._correlation_group(symbol)
        if not group:
            return 0.0

        total_usd = 0.0
        same_group_usd = 0.0

        for pos in open_positions:
            total_usd += pos.size_usd
            if self._correlation_group(pos.symbol) == group:
                same_group_usd += pos.size_usd

        if total_usd <= 0:
            return 0.0

        return same_group_usd / total_usd

    # ---------------------------------------------------------- main logic
    def calculate_position_size(
        self,
        symbol: str,
        atr: float,
        signal_confidence: float,
        equity_usd: float,
        open_positions: Iterable[PositionInfo],
        daily_pnl_usd: float,
    ) -> float:
        """
        Возвращает мягкий лимит размера позиции в USD.

        !!! Важно:
        - Это верхняя оценка. В движке её нужно пересечь с лимитом от RiskManager.
        - equity_usd должен уже включать дневной PnL (equity_0 + daily_pnl).
        """

        cfg = self.config

        base_size = cfg.max_position_size_usd

        # 1) ATR multiplier — чем выше волатильность, тем меньше размер.
        if atr <= 0:
            atr_mul = 1.0
        else:
            atr_norm = atr / max(cfg.avg_atr, 1e-8)
            atr_mul = 1.0 / (1.0 + max(0.0, atr_norm - 1.0))
            atr_mul = max(0.3, min(atr_mul, 1.0))

        # 2) Confidence multiplier — сигнал с уверенностью < 0.5 режем.
        conf = max(0.0, min(signal_confidence or 0.0, 1.0))
        conf_mul = max(0.5, conf)

        # 3) Daily PnL multiplier — после хорошей прибыли становимся консервативнее.
        pnl_ratio = 0.0
        if cfg.max_daily_profit_usd > 0:
            pnl_ratio = daily_pnl_usd / cfg.max_daily_profit_usd
        pnl_mul = 1.0 - max(0.0, pnl_ratio)
        pnl_mul = max(0.3, min(pnl_mul, 1.0))

        # 4) Correlation multiplier — если в той же группе уже много денег, режем.
        positions_list = list(open_positions)
        corr_load = self._correlation_load(symbol, positions_list)
        corr_mul = 1.0 / (1.0 + corr_load)
        corr_mul = max(0.5, min(corr_mul, 1.0))

        # 5) Portfolio multiplier — чем больше позиций, тем меньше риск на одну.
        open_count = len(positions_list)
        if open_count <= 0:
            port_mul = 1.0
        else:
            port_mul = 1.0 / (1.0 + 0.2 * open_count)
            port_mul = max(0.4, min(port_mul, 1.0))

        raw_size = base_size * atr_mul * conf_mul * pnl_mul * corr_mul * port_mul

        # Ограничиваем разумными пределами
        size = min(raw_size, cfg.max_position_size_usd, equity_usd)
        size = max(size, 0.0)

        if size > 0 and size < cfg.min_position_size_usd:
            logger.debug(
                "AdaptiveRiskManagerV2: рассчитанный размер %.2f USDT ниже min_position_size_usd %.2f, "
                "сигнал лучше отбросить.",
                size,
                cfg.min_position_size_usd,
            )
            return 0.0

        logger.debug(
            "AdaptiveRiskManagerV2: %s size=%.2f (base=%.2f, atr=%.3g mul=%.2f, conf_mul=%.2f, "
            "pnl_mul=%.2f, corr_mul=%.2f, port_mul=%.2f, equity=%.2f, pnl=%.2f)",
            symbol,
            size,
            base_size,
            atr,
            atr_mul,
            conf_mul,
            pnl_mul,
            corr_mul,
            port_mul,
            equity_usd,
            daily_pnl_usd,
        )
        return size


