#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HOPE AI - Mode Router v1.1
Классификация сигналов по торговым режимам: SUPER_SCALP / SCALP / SWING

Created: 2026-01-29
Author: Claude (opus-4) + Valentin
Version: 1.1 - Fixed PumpDetection edge case
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Any
import json
from hashlib import sha256


class TradingMode(Enum):
    """Торговые режимы"""
    SUPER_SCALP = "super_scalp"  # 5-30 sec, target +0.5%, stop -0.3%
    SCALP = "scalp"              # 30-120 sec, target +2%, stop -1%
    SWING = "swing"              # 5-15 min, target +5%, stop -2%
    SKIP = "skip"                # Не торговать


@dataclass
class ModeConfig:
    """Конфигурация режима"""
    name: str
    min_delta_pct: float
    min_buys_per_sec: float
    min_vol_raise_pct: float
    min_volume_24h: float
    target_pct: float
    stop_pct: float
    timeout_sec: int
    max_capital_pct: float
    daily_loss_limit_pct: float
    circuit_losses: int
    cooldown_sec: int
    latency_max_ms: int


@dataclass
class RouteResult:
    """Результат маршрутизации"""
    mode: TradingMode
    confidence: float
    reasons: List[str]
    config: Optional[ModeConfig]
    signal_data: Dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    checksum: str = field(default="")
    
    def __post_init__(self):
        data = f"{self.mode.value}:{self.confidence}:{self.timestamp}"
        self.checksum = f"sha256:{sha256(data.encode()).hexdigest()[:16]}"
    
    def to_dict(self) -> Dict:
        return {
            'mode': self.mode.value,
            'confidence': self.confidence,
            'reasons': self.reasons,
            'config': {
                'target_pct': self.config.target_pct,
                'stop_pct': self.config.stop_pct,
                'timeout_sec': self.config.timeout_sec,
            } if self.config else None,
            'timestamp': self.timestamp,
            'checksum': self.checksum,
        }


class ModeRouter:
    """
    Маршрутизатор торговых режимов
    
    Fail-closed: любое сомнение = SKIP
    Priority: SUPER_SCALP > SCALP > SWING > SKIP
    
    Особые случаи:
    - PumpDetection с buys/sec > 100: автоматически SUPER_SCALP
    - TopMarket + high delta: boost confidence
    """
    
    # Специальные пороги для PumpDetection
    PUMP_BUYS_THRESHOLD = 100  # buys/sec для автоматического SUPER_SCALP
    
    # Конфигурации режимов согласно TZ v4.0
    MODES: Dict[TradingMode, ModeConfig] = {
        TradingMode.SUPER_SCALP: ModeConfig(
            name="SUPER_SCALP",
            min_delta_pct=5.0,
            min_buys_per_sec=30.0,
            min_vol_raise_pct=100.0,
            min_volume_24h=1_000_000,
            target_pct=0.5,
            stop_pct=0.3,
            timeout_sec=30,
            max_capital_pct=5.0,
            daily_loss_limit_pct=3.0,
            circuit_losses=3,
            cooldown_sec=60,
            latency_max_ms=50,
        ),
        TradingMode.SCALP: ModeConfig(
            name="SCALP",
            min_delta_pct=2.0,
            min_buys_per_sec=5.0,
            min_vol_raise_pct=50.0,
            min_volume_24h=5_000_000,
            target_pct=2.0,
            stop_pct=1.0,
            timeout_sec=120,
            max_capital_pct=10.0,
            daily_loss_limit_pct=5.0,
            circuit_losses=5,
            cooldown_sec=180,
            latency_max_ms=200,
        ),
        TradingMode.SWING: ModeConfig(
            name="SWING",
            min_delta_pct=1.0,
            min_buys_per_sec=0.0,
            min_vol_raise_pct=0.0,
            min_volume_24h=5_000_000,
            target_pct=5.0,
            stop_pct=2.0,
            timeout_sec=900,
            max_capital_pct=20.0,
            daily_loss_limit_pct=10.0,
            circuit_losses=5,
            cooldown_sec=300,
            latency_max_ms=1000,
        ),
    }
    
    def __init__(self):
        self.stats = {
            'routed': 0,
            'by_mode': {m.value: 0 for m in TradingMode},
        }
    
    def route(self, signal: Dict[str, Any]) -> RouteResult:
        """
        Маршрутизировать сигнал к соответствующему режиму
        
        Приоритеты:
        1. PumpDetection с buys/sec > 100 → SUPER_SCALP
        2. Стандартные критерии SUPER_SCALP
        3. Стандартные критерии SCALP
        4. Стандартные критерии SWING
        5. SKIP
        """
        delta = signal.get('delta_pct', 0)
        buys = signal.get('buys_per_sec', 0)
        vol_raise = signal.get('vol_raise_pct', 0)
        volume = signal.get('volume_24h', 0)
        strategy = signal.get('strategy', '')
        
        super_cfg = self.MODES[TradingMode.SUPER_SCALP]
        scalp_cfg = self.MODES[TradingMode.SCALP]
        swing_cfg = self.MODES[TradingMode.SWING]
        
        # ═══════════════════════════════════════════════════════════
        # CASE 1: PumpDetection с экстремальным buys/sec → SUPER_SCALP
        # Это переопределяет все остальные проверки!
        # ═══════════════════════════════════════════════════════════
        if buys >= self.PUMP_BUYS_THRESHOLD and strategy == 'PumpDetection':
            reasons = [
                f"PUMP_OVERRIDE: buys/sec={buys:.0f} >= {self.PUMP_BUYS_THRESHOLD}",
                f"strategy={strategy}",
                f"delta={delta:.1f}%",
            ]
            confidence = min(0.95, 0.8 + min(buys / 2000, 0.15))
            
            self._record(TradingMode.SUPER_SCALP)
            return RouteResult(
                mode=TradingMode.SUPER_SCALP,
                confidence=round(confidence, 3),
                reasons=reasons,
                config=super_cfg,
                signal_data=signal,
            )
        
        # ═══════════════════════════════════════════════════════════
        # CASE 2: Стандартный SUPER_SCALP
        # ═══════════════════════════════════════════════════════════
        if (delta >= super_cfg.min_delta_pct and 
            buys >= super_cfg.min_buys_per_sec and
            vol_raise >= super_cfg.min_vol_raise_pct):
            
            reasons = [
                f"delta={delta:.1f}% >= {super_cfg.min_delta_pct}%",
                f"buys/sec={buys:.0f} >= {super_cfg.min_buys_per_sec}",
                f"vol_raise={vol_raise:.0f}% >= {super_cfg.min_vol_raise_pct}%",
            ]
            confidence = min(0.95, 0.7 + (delta / 20) + (buys / 100))
            
            self._record(TradingMode.SUPER_SCALP)
            return RouteResult(
                mode=TradingMode.SUPER_SCALP,
                confidence=round(confidence, 3),
                reasons=reasons,
                config=super_cfg,
                signal_data=signal,
            )
        
        # ═══════════════════════════════════════════════════════════
        # CASE 3: SCALP
        # ═══════════════════════════════════════════════════════════
        if (delta >= scalp_cfg.min_delta_pct and
            buys >= scalp_cfg.min_buys_per_sec and
            vol_raise >= scalp_cfg.min_vol_raise_pct and
            volume >= scalp_cfg.min_volume_24h):
            
            reasons = [
                f"delta={delta:.1f}% >= {scalp_cfg.min_delta_pct}%",
                f"buys/sec={buys:.0f} >= {scalp_cfg.min_buys_per_sec}",
                f"volume={volume/1e6:.1f}M >= {scalp_cfg.min_volume_24h/1e6:.0f}M",
            ]
            confidence = min(0.9, 0.6 + (delta / 15) + (buys / 50))
            
            self._record(TradingMode.SCALP)
            return RouteResult(
                mode=TradingMode.SCALP,
                confidence=round(confidence, 3),
                reasons=reasons,
                config=scalp_cfg,
                signal_data=signal,
            )
        
        # ═══════════════════════════════════════════════════════════
        # CASE 4: SWING
        # ═══════════════════════════════════════════════════════════
        if (delta >= swing_cfg.min_delta_pct and
            volume >= swing_cfg.min_volume_24h):
            
            reasons = [
                f"delta={delta:.1f}% >= {swing_cfg.min_delta_pct}%",
                f"volume={volume/1e6:.1f}M >= {swing_cfg.min_volume_24h/1e6:.0f}M",
            ]
            
            if strategy in ('PumpDetection', 'TopMarket'):
                confidence = min(0.85, 0.5 + (delta / 10))
                reasons.append(f"strategy={strategy} (boosted)")
            else:
                confidence = min(0.75, 0.4 + (delta / 15))
            
            self._record(TradingMode.SWING)
            return RouteResult(
                mode=TradingMode.SWING,
                confidence=round(confidence, 3),
                reasons=reasons,
                config=swing_cfg,
                signal_data=signal,
            )
        
        # ═══════════════════════════════════════════════════════════
        # CASE 5: SKIP (fail-closed default)
        # ═══════════════════════════════════════════════════════════
        reasons = []
        if delta < swing_cfg.min_delta_pct:
            reasons.append(f"delta={delta:.1f}% < {swing_cfg.min_delta_pct}% (min)")
        if volume < swing_cfg.min_volume_24h:
            reasons.append(f"volume={volume/1e6:.1f}M < {swing_cfg.min_volume_24h/1e6:.0f}M (min)")
        
        self._record(TradingMode.SKIP)
        return RouteResult(
            mode=TradingMode.SKIP,
            confidence=0.0,
            reasons=reasons or ["no_criteria_met"],
            config=None,
            signal_data=signal,
        )
    
    def _record(self, mode: TradingMode) -> None:
        """Записать статистику"""
        self.stats['routed'] += 1
        self.stats['by_mode'][mode.value] += 1
    
    def get_stats(self) -> Dict:
        """Получить статистику маршрутизации"""
        return self.stats.copy()
    
    def route_batch(self, signals: List[Dict]) -> List[RouteResult]:
        """Маршрутизировать пакет сигналов"""
        return [self.route(s) for s in signals]


def test_router():
    """Тестирование роутера"""
    print("=" * 60)
    print("MODE ROUTER TEST v1.1")
    print("=" * 60)
    
    router = ModeRouter()
    
    test_signals = [
        # SUPER_SCALP: высокий delta, много покупок, рост объёма
        {
            'symbol': 'XVSUSDT',
            'delta_pct': 17.31,
            'buys_per_sec': 33,
            'vol_raise_pct': 150,
            'volume_24h': 5_400_000,
            'strategy': 'TopMarket',
        },
        # SCALP: средний delta, умеренная активность
        {
            'symbol': 'SENTUSDT',
            'delta_pct': 6.67,
            'buys_per_sec': 12,
            'vol_raise_pct': 70,
            'volume_24h': 56_000_000,
            'strategy': 'TopMarket',
        },
        # SWING: низкий delta, высокий объём
        {
            'symbol': 'WLDUSDT',
            'delta_pct': 3.1,
            'buys_per_sec': 2,
            'vol_raise_pct': 30,
            'volume_24h': 130_000_000,
            'strategy': 'DropsDetection',
        },
        # SKIP: слишком низкие показатели
        {
            'symbol': 'HOLOUSDT',
            'delta_pct': 0.5,
            'buys_per_sec': 0,
            'vol_raise_pct': 10,
            'volume_24h': 2_000_000,
            'strategy': 'DropsDetection',
        },
        # PumpDetection - высокий buys/sec ПЕРЕОПРЕДЕЛЯЕТ low delta!
        {
            'symbol': 'WLDUSDT',
            'delta_pct': 2.0,
            'buys_per_sec': 1004,
            'vol_raise_pct': 200,
            'volume_24h': 130_000_000,
            'strategy': 'PumpDetection',
        },
    ]
    
    print("\n--- Routing Signals ---\n")
    
    results = []
    for sig in test_signals:
        result = router.route(sig)
        results.append(result)
        
        print(f"{sig['symbol']}:")
        print(f"  Input: delta={sig['delta_pct']}%, buys/sec={sig['buys_per_sec']}, strategy={sig['strategy']}")
        print(f"  Mode: {result.mode.value.upper()}")
        print(f"  Confidence: {result.confidence:.1%}")
        print(f"  Reasons: {result.reasons}")
        if result.config:
            print(f"  Config: target={result.config.target_pct}%, stop={result.config.stop_pct}%, timeout={result.config.timeout_sec}s")
        print(f"  Checksum: {result.checksum}")
        print()
    
    stats = router.get_stats()
    print("--- Statistics ---")
    print(f"Total routed: {stats['routed']}")
    for mode, count in stats['by_mode'].items():
        if count > 0:
            print(f"  {mode}: {count}")
    
    print("\n--- Validation ---")
    expected = [
        ('XVSUSDT', TradingMode.SUPER_SCALP),
        ('SENTUSDT', TradingMode.SCALP),
        ('WLDUSDT', TradingMode.SWING),
        ('HOLOUSDT', TradingMode.SKIP),
        ('WLDUSDT', TradingMode.SUPER_SCALP),  # PumpDetection override!
    ]
    
    all_pass = True
    for (sym, exp_mode), result in zip(expected, results):
        status = "✓" if result.mode == exp_mode else "✗"
        if result.mode != exp_mode:
            all_pass = False
        print(f"  {status} {sym}: expected {exp_mode.value}, got {result.mode.value}")
    
    print(f"\nResult: {'PASS' if all_pass else 'FAIL'}")
    
    return all_pass


if __name__ == '__main__':
    success = test_router()
    exit(0 if success else 1)
