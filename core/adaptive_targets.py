#!/usr/bin/env python3
"""
HOPE Adaptive Targets Module v2.0
=================================
Динамический расчёт TP/SL на основе:
- Волатильности (ATR)
- Силы сигнала (confidence)
- Рыночного режима (BTC trend)
- Volume profile

Интеграция: eye_of_god_v3.py, autotrader.py
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Paths
STATE_DIR = Path(__file__).parent.parent / "state" / "ai"
CONFIG_DIR = Path(__file__).parent.parent / "config"


@dataclass
class AdaptiveTargets:
    """Результат расчёта адаптивных целей"""
    tp_pct: float      # Take Profit в процентах
    sl_pct: float      # Stop Loss в процентах
    rr_ratio: float    # Risk/Reward ratio
    position_size: float  # Рекомендуемый размер позиции
    confidence: float  # Итоговая уверенность
    reasoning: str     # Объяснение расчёта


class AdaptiveTargetEngine:
    """
    Движок адаптивных целей для HOPE.
    
    Использование:
        engine = AdaptiveTargetEngine('config/scalping_100.json')
        targets = engine.calculate(signal, market_data)
    """
    
    def __init__(self, config_path: str = None):
        # Load config
        if config_path and Path(config_path).exists():
            self.config = json.loads(Path(config_path).read_text())
        else:
            # Default config
            self.config = self._default_config()
        
        # State
        self.daily_stats = {
            'trades': 0,
            'wins': 0,
            'losses': 0,
            'consecutive_losses': 0,
            'pnl': 0.0,
            'exposure': 0.0
        }
        
        self._load_daily_stats()
        logger.info(f"AdaptiveTargetEngine initialized | Config: {config_path}")
    
    def _default_config(self) -> dict:
        return {
            "capital": {"total_usd": 100},
            "position_sizing": {
                "base_size_usd": 20,
                "min_size_usd": 10,
                "max_size_usd": 25,
                "max_exposure_usd": 50
            },
            "targets": {
                "base_tp_pct": 1.5,
                "base_sl_pct": 0.5,
                "min_rr": 2.5,
                "max_tp_pct": 5.0,
                "max_sl_pct": 1.5
            },
            "ai_filters": {
                "min_confidence": 0.70
            },
            "risk_management": {
                "max_consecutive_losses": 3,
                "reduction_factor": 0.75
            }
        }
    
    def _load_daily_stats(self):
        """Загрузить статистику за день"""
        stats_file = STATE_DIR / "daily_stats.json"
        if stats_file.exists():
            try:
                data = json.loads(stats_file.read_text())
                if data.get('date') == datetime.now().strftime('%Y-%m-%d'):
                    self.daily_stats = data
            except:
                pass
    
    def _save_daily_stats(self):
        """Сохранить статистику"""
        self.daily_stats['date'] = datetime.now().strftime('%Y-%m-%d')
        stats_file = STATE_DIR / "daily_stats.json"
        stats_file.write_text(json.dumps(self.daily_stats, indent=2))
    
    def calculate(
        self,
        signal: Dict,
        market_data: Optional[Dict] = None
    ) -> AdaptiveTargets:
        """
        Рассчитать адаптивные цели для сигнала.
        
        Args:
            signal: {
                'symbol': str,
                'confidence': float (0-1),
                'delta_pct': float,
                'volume_ratio': float,
                'buy_dominance': float
            }
            market_data: {
                'atr_pct': float,
                'rsi': float,
                'btc_trend': str ('BULLISH', 'BEARISH', 'SIDEWAYS'),
                'btc_change_pct': float
            }
        
        Returns:
            AdaptiveTargets with calculated TP/SL/size
        """
        cfg_targets = self.config['targets']
        cfg_sizing = self.config['position_sizing']
        
        # Base values
        base_tp = cfg_targets['base_tp_pct']
        base_sl = cfg_targets['base_sl_pct']
        
        # Signal confidence
        confidence = signal.get('confidence', 0.7)
        
        # Market data defaults
        if market_data is None:
            market_data = {}
        
        atr_pct = market_data.get('atr_pct', 1.5)
        btc_trend = market_data.get('btc_trend', 'SIDEWAYS')
        rsi = market_data.get('rsi', 50)
        
        # === VOLATILITY ADJUSTMENT ===
        # Нормализуем ATR к среднему (1.5%)
        volatility_mult = min(2.0, max(0.5, atr_pct / 1.5))
        
        # === MOMENTUM ADJUSTMENT ===
        # RSI > 50 = bullish momentum
        if rsi > 65:
            momentum_mult = 1.3  # Strong momentum, larger TP
        elif rsi > 55:
            momentum_mult = 1.15
        elif rsi < 35:
            momentum_mult = 0.8  # Weak momentum, smaller TP
        else:
            momentum_mult = 1.0
        
        # === REGIME ADJUSTMENT ===
        regime_config = self.config.get('market_regime', {})
        if btc_trend == 'BULLISH':
            regime_mult = regime_config.get('bullish_multiplier', 1.2)
        elif btc_trend == 'BEARISH':
            regime_mult = regime_config.get('bearish_multiplier', 0.7)
        else:
            regime_mult = regime_config.get('sideways_multiplier', 1.0)
        
        # === SIGNAL STRENGTH ADJUSTMENT ===
        # High confidence = можно брать больше профита
        signal_mult = 0.8 + confidence * 0.4  # 0.8 - 1.2
        
        # === FINAL CALCULATION ===
        # TP корректируется всеми факторами
        final_tp = base_tp * volatility_mult * momentum_mult * regime_mult * signal_mult
        
        # SL корректируется только волатильностью
        final_sl = base_sl * volatility_mult
        
        # Ограничения
        final_tp = max(0.8, min(cfg_targets.get('max_tp_pct', 5.0), final_tp))
        final_sl = max(0.3, min(cfg_targets.get('max_sl_pct', 1.5), final_sl))
        
        # R:R Check - enforce minimum
        min_rr = cfg_targets.get('min_rr', 2.5)
        if final_tp / final_sl < min_rr:
            final_tp = final_sl * min_rr
        
        rr_ratio = final_tp / final_sl
        
        # === POSITION SIZING ===
        position_size = self._calculate_position_size(confidence)
        
        # === REASONING ===
        reasoning = (
            f"Vol:{volatility_mult:.2f} Mom:{momentum_mult:.2f} "
            f"Regime:{regime_mult:.2f} Signal:{signal_mult:.2f}"
        )
        
        targets = AdaptiveTargets(
            tp_pct=round(final_tp, 2),
            sl_pct=round(final_sl, 2),
            rr_ratio=round(rr_ratio, 1),
            position_size=position_size,
            confidence=confidence,
            reasoning=reasoning
        )
        
        logger.info(
            f"[ADAPTIVE] {signal.get('symbol', '?')}: "
            f"TP={targets.tp_pct}% SL={targets.sl_pct}% "
            f"R:R={targets.rr_ratio} Size=${targets.position_size}"
        )
        
        return targets
    
    def _calculate_position_size(self, confidence: float) -> float:
        """Рассчитать размер позиции по confidence"""
        cfg = self.config['position_sizing']
        risk_cfg = self.config.get('risk_management', {})
        
        # Size by confidence
        size_map = cfg.get('size_by_confidence', {})
        if size_map:
            for threshold, size in sorted(size_map.items(), reverse=True):
                if threshold != 'default' and confidence >= float(threshold):
                    base_size = size
                    break
            else:
                base_size = size_map.get('default', cfg['base_size_usd'])
        else:
            base_size = cfg['base_size_usd']
        
        # Reduce after consecutive losses
        if self.daily_stats['consecutive_losses'] >= 2:
            factor = risk_cfg.get('reduction_factor', 0.75)
            base_size *= factor
            logger.warning(f"[SIZE] Reduced due to {self.daily_stats['consecutive_losses']} losses")
        
        # Check exposure limit
        remaining = cfg['max_exposure_usd'] - self.daily_stats['exposure']
        base_size = min(base_size, remaining)
        
        # Ensure minimum
        base_size = max(cfg['min_size_usd'], base_size)
        
        return round(base_size, 2)
    
    def record_trade_result(self, pnl_pct: float, pnl_usd: float):
        """Записать результат сделки для статистики"""
        self.daily_stats['trades'] += 1
        self.daily_stats['pnl'] += pnl_usd
        
        if pnl_pct > 0:
            self.daily_stats['wins'] += 1
            self.daily_stats['consecutive_losses'] = 0
        else:
            self.daily_stats['losses'] += 1
            self.daily_stats['consecutive_losses'] += 1
        
        self._save_daily_stats()
        
        wr = self.daily_stats['wins'] / max(1, self.daily_stats['trades']) * 100
        logger.info(
            f"[STATS] Trades:{self.daily_stats['trades']} "
            f"WR:{wr:.0f}% PnL:${self.daily_stats['pnl']:.2f}"
        )
    
    def should_trade(self) -> Tuple[bool, str]:
        """Проверить можно ли торговать"""
        risk_cfg = self.config.get('risk_management', {})
        
        # Check consecutive losses
        max_losses = risk_cfg.get('max_consecutive_losses', 3)
        if self.daily_stats['consecutive_losses'] >= max_losses:
            return False, f"Consecutive losses: {self.daily_stats['consecutive_losses']}"
        
        # Check daily loss limit
        max_daily_loss = risk_cfg.get('max_daily_loss_usd', 10)
        if self.daily_stats['pnl'] <= -max_daily_loss:
            return False, f"Daily loss limit: ${-self.daily_stats['pnl']:.2f}"
        
        # Check stop trading limit
        stop_after = risk_cfg.get('stop_trading_after_losses', 5)
        if self.daily_stats['losses'] >= stop_after:
            return False, f"Max losses reached: {self.daily_stats['losses']}"
        
        return True, "OK"


class TrailingStopManager:
    """
    Управление trailing stop для позиций.
    
    Использование:
        manager = TrailingStopManager(config)
        result = manager.update('BTCUSDT', current_price, entry_price)
        if result['action'] == 'CLOSE':
            # Close position
    """
    
    def __init__(self, config: dict = None):
        if config is None:
            config = {
                'enabled': True,
                'activation_pct': 1.0,
                'distance_pct': 0.5
            }
        
        self.enabled = config.get('enabled', True)
        self.activation_pct = config.get('activation_pct', 1.0)
        self.distance_pct = config.get('distance_pct', 0.5)
        
        # Track highest prices
        self.highest_prices: Dict[str, float] = {}
        self.activated: Dict[str, bool] = {}
        
        logger.info(
            f"TrailingStop initialized | "
            f"Activation:{self.activation_pct}% Distance:{self.distance_pct}%"
        )
    
    def update(
        self,
        symbol: str,
        current_price: float,
        entry_price: float
    ) -> Dict:
        """
        Обновить trailing stop для позиции.
        
        Returns:
            {'action': 'CLOSE'|'HOLD', 'reason': str, 'pnl_pct': float, ...}
        """
        if not self.enabled:
            return {'action': 'HOLD', 'reason': 'Trailing disabled'}
        
        pnl_pct = (current_price / entry_price - 1) * 100
        
        # Initialize tracking
        if symbol not in self.highest_prices:
            self.highest_prices[symbol] = current_price
            self.activated[symbol] = False
        
        # Update highest
        if current_price > self.highest_prices[symbol]:
            self.highest_prices[symbol] = current_price
        
        highest = self.highest_prices[symbol]
        highest_pnl = (highest / entry_price - 1) * 100
        
        # Check activation
        if not self.activated[symbol] and highest_pnl >= self.activation_pct:
            self.activated[symbol] = True
            logger.info(f"[TRAILING] {symbol} activated at +{highest_pnl:.2f}%")
        
        # Check trailing stop hit
        if self.activated[symbol]:
            trailing_stop = highest * (1 - self.distance_pct / 100)
            
            if current_price <= trailing_stop:
                # Clean up
                del self.highest_prices[symbol]
                del self.activated[symbol]
                
                return {
                    'action': 'CLOSE',
                    'reason': 'TRAILING_STOP',
                    'pnl_pct': pnl_pct,
                    'highest_pnl': highest_pnl,
                    'trailing_stop': trailing_stop
                }
        
        return {
            'action': 'HOLD',
            'reason': 'In range',
            'pnl_pct': pnl_pct,
            'highest_pnl': highest_pnl,
            'trailing_activated': self.activated.get(symbol, False)
        }
    
    def remove(self, symbol: str):
        """Удалить позицию из трекинга"""
        self.highest_prices.pop(symbol, None)
        self.activated.pop(symbol, None)


class PartialProfitManager:
    """
    Управление частичным закрытием позиций.
    
    Использование:
        manager = PartialProfitManager(config)
        result = manager.check('pos_123', pnl_pct=2.0, qty=100)
        if result['action'] == 'PARTIAL_CLOSE':
            # Close result['qty'] units
    """
    
    def __init__(self, config: dict = None):
        if config is None:
            config = {
                'enabled': True,
                'levels': [
                    {'pnl_pct': 1.5, 'close_pct': 50}
                ]
            }
        
        self.enabled = config.get('enabled', True)
        self.levels = config.get('levels', [])
        
        # Track which levels have been taken
        self.taken_levels: Dict[str, set] = {}
        
        logger.info(f"PartialProfit initialized | Levels: {len(self.levels)}")
    
    def check(
        self,
        position_id: str,
        pnl_pct: float,
        qty: float
    ) -> Dict:
        """
        Проверить нужно ли взять частичную прибыль.
        
        Returns:
            {'action': 'PARTIAL_CLOSE'|'HOLD', 'qty': float, 'level': int}
        """
        if not self.enabled or not self.levels:
            return {'action': 'HOLD'}
        
        if position_id not in self.taken_levels:
            self.taken_levels[position_id] = set()
        
        taken = self.taken_levels[position_id]
        
        for i, level in enumerate(self.levels):
            if i in taken:
                continue
            
            if pnl_pct >= level['pnl_pct']:
                close_qty = qty * (level['close_pct'] / 100)
                taken.add(i)
                
                logger.info(
                    f"[PARTIAL] {position_id} taking {level['close_pct']}% "
                    f"at +{pnl_pct:.2f}%"
                )
                
                return {
                    'action': 'PARTIAL_CLOSE',
                    'qty': close_qty,
                    'level': i,
                    'reason': f"Partial TP level {i+1} at +{level['pnl_pct']}%"
                }
        
        return {'action': 'HOLD'}
    
    def remove(self, position_id: str):
        """Удалить позицию из трекинга"""
        self.taken_levels.pop(position_id, None)


# === INTEGRATION HELPER ===

def integrate_adaptive_targets(eye_of_god_instance):
    """
    Интегрировать адаптивные цели в существующий Eye of God.
    
    Usage:
        from core.adaptive_targets import integrate_adaptive_targets
        integrate_adaptive_targets(eye)
    """
    config_path = CONFIG_DIR / "scalping_100.json"
    
    if config_path.exists():
        engine = AdaptiveTargetEngine(str(config_path))
    else:
        engine = AdaptiveTargetEngine()
    
    # Attach to instance
    eye_of_god_instance.adaptive_engine = engine
    
    # Patch evaluate method
    original_evaluate = eye_of_god_instance.evaluate_signal
    
    def enhanced_evaluate(signal):
        # Get original decision
        decision = original_evaluate(signal)
        
        if decision.get('action') == 'BUY':
            # Calculate adaptive targets
            targets = engine.calculate(signal)
            
            # Update decision
            decision['tp_pct'] = targets.tp_pct
            decision['sl_pct'] = targets.sl_pct
            decision['rr_ratio'] = targets.rr_ratio
            decision['position_size'] = targets.position_size
            decision['adaptive_reasoning'] = targets.reasoning
        
        return decision
    
    eye_of_god_instance.evaluate_signal = enhanced_evaluate
    logger.info("Adaptive targets integrated into Eye of God")


if __name__ == "__main__":
    # Test
    logging.basicConfig(level=logging.INFO)
    
    engine = AdaptiveTargetEngine()
    
    # Test signal
    signal = {
        'symbol': 'TESTUSDT',
        'confidence': 0.75,
        'delta_pct': 1.5,
        'volume_ratio': 2.0,
        'buy_dominance': 0.65
    }
    
    market_data = {
        'atr_pct': 2.0,
        'rsi': 62,
        'btc_trend': 'BULLISH'
    }
    
    targets = engine.calculate(signal, market_data)
    
    print("\n" + "="*50)
    print("ADAPTIVE TARGETS TEST")
    print("="*50)
    print(f"TP: {targets.tp_pct}%")
    print(f"SL: {targets.sl_pct}%")
    print(f"R:R: {targets.rr_ratio}")
    print(f"Size: ${targets.position_size}")
    print(f"Reasoning: {targets.reasoning}")
    print("="*50)
