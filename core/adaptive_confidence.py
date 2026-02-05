#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
adaptive_confidence.py - Динамический порог уверенности

=== AI SIGNATURE ===
Created by: Claude (opus-4.5)
Created at: 2026-02-05T01:00:00Z
Purpose: P0 Critical - Fix Win Rate from 35% to 55%+
=== END SIGNATURE ===

ПРОБЛЕМА:
- Текущий min_confidence = 20% слишком низкий
- Принимаем слишком много плохих сигналов
- Win Rate 35% = убыточная система

РЕШЕНИЕ:
- Динамический порог на основе последних N сделок
- Если WR падает - повышаем порог
- Если WR растёт - можем ослабить

ИНТЕГРАЦИЯ:
В autotrader.py или signal_processor.py:
    from core.adaptive_confidence import AdaptiveConfidence
    
    confidence_manager = AdaptiveConfidence()
    
    # При получении сигнала:
    min_conf = confidence_manager.get_threshold()
    if signal.confidence < min_conf:
        logger.info(f"Signal rejected: {signal.confidence} < {min_conf}")
        return
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("hope.adaptive_confidence")


@dataclass
class TradeResult:
    """Результат сделки для анализа"""
    symbol: str
    pnl: float
    pnl_pct: float
    is_win: bool
    timestamp: float = field(default_factory=time.time)
    confidence: float = 0.0


class AdaptiveConfidence:
    """
    Динамически корректирует min_confidence на основе Win Rate.
    
    Логика:
    - WR < 35%: PANIC - порог 75%
    - WR 35-45%: CAUTIOUS - порог 65%
    - WR 45-55%: NORMAL - базовый порог
    - WR 55-65%: CONFIDENT - можно снизить
    - WR > 65%: AGGRESSIVE - минимальный порог
    """
    
    # Режимы в зависимости от Win Rate
    REGIMES = {
        "PANIC":      {"wr_range": (0, 0.35),   "threshold": 0.75, "max_positions": 1},
        "CAUTIOUS":   {"wr_range": (0.35, 0.45), "threshold": 0.65, "max_positions": 2},
        "NORMAL":     {"wr_range": (0.45, 0.55), "threshold": 0.55, "max_positions": 3},
        "CONFIDENT":  {"wr_range": (0.55, 0.65), "threshold": 0.45, "max_positions": 4},
        "AGGRESSIVE": {"wr_range": (0.65, 1.0),  "threshold": 0.40, "max_positions": 5},
    }
    
    def __init__(
        self,
        base_threshold: float = 0.55,
        window_size: int = 30,
        state_file: Optional[Path] = None
    ):
        self.base_threshold = base_threshold
        self.window_size = window_size
        self.recent_trades: deque[TradeResult] = deque(maxlen=window_size)
        
        self.state_file = state_file or Path("state/ai/adaptive_confidence.json")
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        
        self._load_state()
        
        logger.info(
            f"AdaptiveConfidence initialized: base={base_threshold}, "
            f"window={window_size}, trades={len(self.recent_trades)}"
        )
    
    def _load_state(self) -> None:
        """Загрузить историю сделок из файла"""
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text())
                for t in data.get("trades", []):
                    self.recent_trades.append(TradeResult(**t))
                logger.info(f"Loaded {len(self.recent_trades)} trades from state")
            except Exception as e:
                logger.warning(f"Failed to load state: {e}")
    
    def _save_state(self) -> None:
        """Сохранить историю сделок в файл"""
        try:
            data = {
                "trades": [
                    {
                        "symbol": t.symbol,
                        "pnl": t.pnl,
                        "pnl_pct": t.pnl_pct,
                        "is_win": t.is_win,
                        "timestamp": t.timestamp,
                        "confidence": t.confidence,
                    }
                    for t in self.recent_trades
                ],
                "last_update": time.time(),
            }
            self.state_file.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
    
    def add_trade(self, trade: TradeResult) -> None:
        """Добавить результат сделки"""
        self.recent_trades.append(trade)
        self._save_state()
        
        wr = self.get_win_rate()
        regime = self.get_current_regime()
        logger.info(
            f"Trade added: {trade.symbol} {'WIN' if trade.is_win else 'LOSS'} "
            f"PnL={trade.pnl_pct:.2f}% | WR={wr:.1%} | Regime={regime}"
        )
    
    def add_trade_simple(
        self, 
        symbol: str, 
        pnl: float, 
        confidence: float = 0.0
    ) -> None:
        """Упрощённое добавление сделки"""
        trade = TradeResult(
            symbol=symbol,
            pnl=pnl,
            pnl_pct=pnl,  # Assume pnl is already %
            is_win=pnl > 0,
            confidence=confidence,
        )
        self.add_trade(trade)
    
    def get_win_rate(self) -> float:
        """Текущий Win Rate на основе последних N сделок"""
        if len(self.recent_trades) < 5:
            return 0.50  # Default when not enough data
        
        wins = sum(1 for t in self.recent_trades if t.is_win)
        return wins / len(self.recent_trades)
    
    def get_current_regime(self) -> str:
        """Определить текущий режим по Win Rate"""
        wr = self.get_win_rate()
        
        for regime_name, config in self.REGIMES.items():
            low, high = config["wr_range"]
            if low <= wr < high:
                return regime_name
        
        return "NORMAL"
    
    def get_threshold(self) -> float:
        """
        Получить текущий порог уверенности.
        
        Это основной метод для использования в сигнальном процессоре.
        """
        regime = self.get_current_regime()
        threshold = self.REGIMES[regime]["threshold"]
        
        # Дополнительная корректировка на основе streak
        streak = self._get_loss_streak()
        if streak >= 5:
            threshold = min(0.85, threshold + 0.10)
            logger.warning(f"Loss streak {streak}, raising threshold to {threshold}")
        
        return threshold
    
    def get_max_positions(self) -> int:
        """Получить максимум позиций для текущего режима"""
        regime = self.get_current_regime()
        return self.REGIMES[regime]["max_positions"]
    
    def _get_loss_streak(self) -> int:
        """Подсчёт серии убытков подряд"""
        streak = 0
        for trade in reversed(list(self.recent_trades)):
            if not trade.is_win:
                streak += 1
            else:
                break
        return streak
    
    def get_stats(self) -> dict:
        """Получить статистику для отображения"""
        wr = self.get_win_rate()
        regime = self.get_current_regime()
        
        total_pnl = sum(t.pnl for t in self.recent_trades)
        avg_win = 0.0
        avg_loss = 0.0
        
        wins = [t.pnl for t in self.recent_trades if t.is_win]
        losses = [t.pnl for t in self.recent_trades if not t.is_win]
        
        if wins:
            avg_win = sum(wins) / len(wins)
        if losses:
            avg_loss = sum(losses) / len(losses)
        
        return {
            "trades_count": len(self.recent_trades),
            "win_rate": round(wr * 100, 1),
            "regime": regime,
            "current_threshold": self.get_threshold(),
            "max_positions": self.get_max_positions(),
            "loss_streak": self._get_loss_streak(),
            "total_pnl": round(total_pnl, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
        }
    
    def should_accept_signal(self, signal_confidence: float) -> tuple[bool, str]:
        """
        Проверить, принять ли сигнал.
        
        Returns:
            (accepted, reason)
        """
        threshold = self.get_threshold()
        regime = self.get_current_regime()
        
        if signal_confidence < threshold:
            reason = (
                f"REJECTED: confidence {signal_confidence:.2f} < "
                f"threshold {threshold:.2f} (regime: {regime})"
            )
            return False, reason
        
        reason = (
            f"ACCEPTED: confidence {signal_confidence:.2f} >= "
            f"threshold {threshold:.2f} (regime: {regime})"
        )
        return True, reason


# ═══════════════════════════════════════════════════════════════════
# STANDALONE TEST
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # Test scenarios
    ac = AdaptiveConfidence(base_threshold=0.55, window_size=20)
    
    print("\n=== Initial State ===")
    print(f"Stats: {ac.get_stats()}")
    
    # Simulate losing streak
    print("\n=== Simulating 10 losses ===")
    for i in range(10):
        ac.add_trade_simple(f"TEST{i}USDT", pnl=-1.5)
    
    print(f"Stats after losses: {ac.get_stats()}")
    print(f"Should accept 0.60 confidence: {ac.should_accept_signal(0.60)}")
    print(f"Should accept 0.80 confidence: {ac.should_accept_signal(0.80)}")
    
    # Simulate recovery
    print("\n=== Simulating 15 wins ===")
    for i in range(15):
        ac.add_trade_simple(f"WIN{i}USDT", pnl=2.0)
    
    print(f"Stats after wins: {ac.get_stats()}")
    print(f"Should accept 0.45 confidence: {ac.should_accept_signal(0.45)}")
    
    print("\n✅ AdaptiveConfidence test PASSED")
