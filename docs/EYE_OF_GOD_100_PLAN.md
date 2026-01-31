# 👁️ EYE OF GOD - ПЛАН $100 → $1,000+ ЗА МЕСЯЦ

**Дата**: 2026-01-31
**Капитал**: $100
**Цель**: Максимальный ROI через AI-скальпинг
**Стратегия**: Умный скальпинг + адаптивные наценки + полный AI контроль

---

## 📊 МАТЕМАТИКА $100 КАПИТАЛА

### Базовые расчёты:

| Параметр | Консервативно | Агрессивно | Ультра |
|----------|---------------|------------|--------|
| Позиция | $10 (10%) | $20 (20%) | $25 (25%) |
| Сделок/день | 10 | 20 | 30 |
| Win Rate | 55% | 65% | 70% |
| Avg Profit | 1.5% | 2.0% | 2.5% |
| Avg Loss | 0.5% | 0.7% | 1.0% |
| R:R | 3:1 | 3:1 | 2.5:1 |

### Прогноз месячного дохода:

```
КОНСЕРВАТИВНО ($10 позиция, 10 сделок/день, 55% WR):
  Wins: 5.5 × $0.15 = $0.825/день
  Losses: 4.5 × $0.05 = $0.225/день
  Net: $0.60/день × 30 = $18/месяц (18% ROI)

АГРЕССИВНО ($20 позиция, 20 сделок/день, 65% WR):
  Wins: 13 × $0.40 = $5.20/день
  Losses: 7 × $0.14 = $0.98/день
  Net: $4.22/день × 30 = $126/месяц (126% ROI)

УЛЬТРА ($25 позиция, 30 сделок/день, 70% WR):
  Wins: 21 × $0.625 = $13.12/день
  Losses: 9 × $0.25 = $2.25/день
  Net: $10.87/день × 30 = $326/месяц (326% ROI)

С COMPOUND (реинвестирование прибыли):
  Week 1: $100 → $130 (30% growth)
  Week 2: $130 → $195 (50% growth)
  Week 3: $195 → $340 (75% growth)
  Week 4: $340 → $680 (100% growth)
  
  ИТОГО: $100 → $680-1000+ при 70% WR и compound
```

---

## 🧠 AI-СКАЛЬПИНГ СТРАТЕГИЯ

### Архитектура принятия решений:

```
┌─────────────────────────────────────────────────────────────────┐
│                    AI SCALPING PIPELINE                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  STAGE 1: SIGNAL DETECTION (pump_detector)                      │
│  ├─ WebSocket: real-time trades                                 │
│  ├─ Volume spike: > 200% avg                                    │
│  ├─ Buy dominance: > 60%                                        │
│  └─ Delta: > 0.5% / minute                                      │
│           ↓                                                      │
│  STAGE 2: AI FILTERING (Eye of God V3)                          │
│  ├─ Alpha Chamber: opportunity score                            │
│  ├─ Risk Chamber: risk assessment                               │
│  ├─ ML Predictor: XGBoost confidence                            │
│  └─ Final: confidence >= 0.70                                   │
│           ↓                                                      │
│  STAGE 3: ADAPTIVE TARGETS (dynamic TP/SL)                      │
│  ├─ Volatility (ATR): adjust TP range                           │
│  ├─ Momentum (RSI): trend strength                              │
│  ├─ Volume profile: sustainability                              │
│  └─ BTC correlation: market regime                              │
│           ↓                                                      │
│  STAGE 4: POSITION SIZING (risk management)                     │
│  ├─ Base: $20 (20% of capital)                                  │
│  ├─ High confidence (>80%): $25                                 │
│  ├─ After 2 losses: reduce to $15                               │
│  └─ Max exposure: $50 (50% of capital)                          │
│           ↓                                                      │
│  STAGE 5: SMART EXIT (trailing + partial)                       │
│  ├─ Take 50% at +1.5%                                           │
│  ├─ Trailing stop: 0.5% below high                              │
│  ├─ Hard stop: -1.0%                                            │
│  └─ Timeout: 30 min max hold                                    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Адаптивная система наценок:

```python
def calculate_adaptive_targets(signal, market_data):
    """
    Динамический расчёт TP/SL на основе:
    - Волатильности (ATR)
    - Силы сигнала
    - Рыночного режима
    """
    
    base_tp = 0.015  # 1.5% базовый TP
    base_sl = 0.005  # 0.5% базовый SL
    
    # Корректировка по волатильности
    atr_multiplier = market_data['atr_pct'] / 1.5  # normalize to avg ATR
    
    # Корректировка по силе сигнала
    signal_strength = signal['confidence']
    
    # Корректировка по режиму рынка
    if market_data['btc_trend'] == 'BULLISH':
        regime_mult = 1.2  # больше TP в бычьем рынке
    elif market_data['btc_trend'] == 'BEARISH':
        regime_mult = 0.8  # меньше TP в медвежьем
    else:
        regime_mult = 1.0
    
    # Финальные targets
    tp = base_tp * atr_multiplier * regime_mult * (0.8 + signal_strength * 0.4)
    sl = base_sl * atr_multiplier
    
    # Ограничения
    tp = max(0.01, min(0.05, tp))  # 1% - 5%
    sl = max(0.003, min(0.015, sl))  # 0.3% - 1.5%
    
    # R:R check
    if tp / sl < 2.0:
        tp = sl * 2.5  # enforce minimum R:R
    
    return {'tp': tp, 'sl': sl, 'rr': tp/sl}
```

---

## ⚙️ КОНФИГУРАЦИЯ ДЛЯ $100

### Файл: config/scalping_100.json

```json
{
  "capital": 100,
  "strategy": "AI_SCALPING",
  "version": "2.0",
  
  "position_sizing": {
    "base_size_pct": 20,
    "base_size_usd": 20,
    "min_size_usd": 10,
    "max_size_usd": 25,
    "max_exposure_pct": 50,
    "max_exposure_usd": 50,
    "max_positions": 2
  },
  
  "risk_management": {
    "max_daily_loss_pct": 10,
    "max_daily_loss_usd": 10,
    "max_consecutive_losses": 3,
    "cooldown_after_losses_min": 15,
    "reduce_size_after_losses": true,
    "reduction_factor": 0.75
  },
  
  "ai_filters": {
    "min_confidence": 0.70,
    "min_alpha_score": 0.60,
    "max_risk_score": 0.40,
    "require_ml_confirm": true
  },
  
  "targets": {
    "mode": "ADAPTIVE",
    "base_tp_pct": 1.5,
    "base_sl_pct": 0.5,
    "min_rr": 2.5,
    "trailing_enabled": true,
    "trailing_activation_pct": 1.0,
    "trailing_distance_pct": 0.5,
    "partial_take_profit": true,
    "partial_tp_pct": 1.5,
    "partial_tp_size": 0.5
  },
  
  "timing": {
    "max_hold_minutes": 30,
    "min_hold_seconds": 30,
    "signal_ttl_seconds": 60,
    "cooldown_between_trades_sec": 30
  },
  
  "filters": {
    "min_liquidity_usd": 3000000,
    "min_volume_ratio": 1.5,
    "min_buy_dominance": 0.55,
    "min_delta_pct": 0.3,
    "blacklist": ["BTCUSDT", "ETHUSDT", "BNBUSDT"],
    "prefer_low_cap": true
  }
}
```

---

## 🔧 ИНТЕГРАЦИЯ AI КОМПОНЕНТОВ

### 1. Eye of God V3 Enhancement:

```python
# Добавить в eye_of_god_v3.py

class EnhancedEyeOfGod:
    """
    Улучшенный Eye of God с:
    - Adaptive targets
    - ML confirmation
    - Market regime awareness
    - Position sizing по confidence
    """
    
    def __init__(self, config_path='config/scalping_100.json'):
        self.config = json.load(open(config_path))
        self.ml_predictor = MLPredictor()
        self.regime_detector = RegimeDetector()
        self.daily_stats = DailyStats()
    
    def evaluate_signal(self, signal: dict) -> dict:
        """Полная оценка сигнала"""
        
        # 1. Alpha Chamber (opportunity)
        alpha = self._alpha_chamber(signal)
        
        # 2. Risk Chamber (risk assessment)
        risk = self._risk_chamber(signal)
        
        # 3. ML Prediction
        ml_conf = self.ml_predictor.predict(signal)
        
        # 4. Market Regime
        regime = self.regime_detector.current_regime()
        
        # 5. Combined Score
        final_confidence = (alpha * 0.35 + (1-risk) * 0.25 + ml_conf * 0.40)
        
        # 6. Adaptive Targets
        targets = self._calculate_adaptive_targets(signal, regime)
        
        # 7. Position Size
        size = self._calculate_position_size(final_confidence)
        
        # 8. Decision
        decision = {
            'action': 'BUY' if final_confidence >= 0.70 else 'SKIP',
            'confidence': final_confidence,
            'alpha_score': alpha,
            'risk_score': risk,
            'ml_confidence': ml_conf,
            'regime': regime,
            'tp_pct': targets['tp'],
            'sl_pct': targets['sl'],
            'rr_ratio': targets['rr'],
            'position_size': size,
            'reason': self._generate_reason(final_confidence, alpha, risk, ml_conf)
        }
        
        return decision
    
    def _calculate_position_size(self, confidence: float) -> float:
        """Размер позиции по confidence"""
        cfg = self.config['position_sizing']
        
        # Base size
        size = cfg['base_size_usd']
        
        # Adjust by confidence
        if confidence >= 0.85:
            size = cfg['max_size_usd']  # $25
        elif confidence >= 0.75:
            size = cfg['base_size_usd']  # $20
        else:
            size = cfg['min_size_usd']  # $10
        
        # Reduce after losses
        if self.daily_stats.consecutive_losses >= 2:
            size *= cfg.get('reduction_factor', 0.75)
        
        # Check daily limit
        remaining = cfg['max_exposure_usd'] - self.daily_stats.current_exposure
        size = min(size, remaining)
        
        return max(cfg['min_size_usd'], size)
```

### 2. Trailing Stop Implementation:

```python
# Добавить в position_watchdog.py

class TrailingStopManager:
    """Управление trailing stop"""
    
    def __init__(self, config):
        self.activation_pct = config['trailing_activation_pct']
        self.distance_pct = config['trailing_distance_pct']
        self.highest_prices = {}  # symbol -> highest price since entry
    
    def update(self, symbol: str, current_price: float, entry_price: float) -> dict:
        """Обновить trailing stop"""
        
        pnl_pct = (current_price / entry_price - 1) * 100
        
        # Track highest price
        if symbol not in self.highest_prices:
            self.highest_prices[symbol] = current_price
        else:
            self.highest_prices[symbol] = max(self.highest_prices[symbol], current_price)
        
        highest = self.highest_prices[symbol]
        highest_pnl = (highest / entry_price - 1) * 100
        
        # Check if trailing activated
        if highest_pnl >= self.activation_pct:
            # Trailing stop = highest - distance
            trailing_stop = highest * (1 - self.distance_pct / 100)
            
            if current_price <= trailing_stop:
                return {
                    'action': 'CLOSE',
                    'reason': 'TRAILING_STOP',
                    'pnl_pct': pnl_pct,
                    'highest_pnl': highest_pnl
                }
        
        return {'action': 'HOLD', 'pnl_pct': pnl_pct, 'highest_pnl': highest_pnl}
```

### 3. Partial Profit Taking:

```python
# Добавить в autotrader.py

class PartialProfitManager:
    """Управление частичным закрытием"""
    
    def __init__(self, config):
        self.partial_tp_pct = config['partial_tp_pct']
        self.partial_size = config['partial_tp_size']
        self.partial_taken = set()  # positions with partial taken
    
    def check_partial(self, position_id: str, pnl_pct: float, qty: float) -> dict:
        """Проверить нужно ли взять частичную прибыль"""
        
        if position_id in self.partial_taken:
            return {'action': 'HOLD'}
        
        if pnl_pct >= self.partial_tp_pct:
            partial_qty = qty * self.partial_size
            self.partial_taken.add(position_id)
            
            return {
                'action': 'PARTIAL_CLOSE',
                'qty': partial_qty,
                'reason': f'Partial TP at +{pnl_pct:.2f}%'
            }
        
        return {'action': 'HOLD'}
```

---

## 📈 ПЛАН РАЗВИТИЯ ПО НЕДЕЛЯМ

### НЕДЕЛЯ 1 (1-7 февраля): FOUNDATION
```
✅ Задачи:
1. Интегрировать config/scalping_100.json
2. Включить adaptive targets в Eye of God
3. Добавить trailing stop в watchdog
4. Увеличить min_confidence до 0.70
5. Тестировать 10 сделок/день

📊 Цель: Win Rate 60%+, капитал $100 → $120
```

### НЕДЕЛЯ 2 (8-14 февраля): OPTIMIZATION
```
✅ Задачи:
1. Собрать 200+ сигналов для ML
2. Retrain XGBoost с новыми данными
3. Добавить partial profit taking
4. Оптимизировать timing (entry/exit)
5. Увеличить до 15-20 сделок/день

📊 Цель: Win Rate 65%+, капитал $120 → $180
```

### НЕДЕЛЯ 3 (15-21 февраля): SCALING
```
✅ Задачи:
1. Добавить market regime filter
2. BTC correlation для входов
3. Увеличить позиции до $25-30
4. Multi-position management
5. 20-25 сделок/день

📊 Цель: Win Rate 68%+, капитал $180 → $350
```

### НЕДЕЛЯ 4 (22-28 февраля): AGGRESSIVE
```
✅ Задачи:
1. Fine-tune всех параметров
2. Compound (реинвестирование)
3. Агрессивный режим при 70%+ WR
4. 25-30 сделок/день
5. Анализ и корректировка

📊 Цель: Win Rate 70%+, капитал $350 → $700-1000
```

---

## 🚀 НЕМЕДЛЕННЫЕ ДЕЙСТВИЯ

### Шаг 1: Создать конфигурацию
```bash
mkdir -p config
# Создать config/scalping_100.json
```

### Шаг 2: Обновить Eye of God
```bash
# Добавить adaptive targets
# Добавить ML confirmation
# Добавить position sizing
```

### Шаг 3: Обновить Watchdog
```bash
# Добавить trailing stop
# Добавить partial profit
```

### Шаг 4: Запустить в production
```bash
# Тестовый режим с маленькими позициями
# Мониторинг 24/7
```

---

## 📊 KPIs ДЛЯ ОТСЛЕЖИВАНИЯ

| Метрика | Неделя 1 | Неделя 2 | Неделя 3 | Неделя 4 |
|---------|----------|----------|----------|----------|
| Win Rate | 60% | 65% | 68% | 70% |
| Trades/Day | 10 | 15 | 20 | 25 |
| Avg Profit | 1.5% | 1.8% | 2.0% | 2.2% |
| Daily PnL | $2-3 | $4-6 | $8-12 | $15-25 |
| Capital | $120 | $180 | $350 | $700+ |

---

## ⚠️ РИСК-МЕНЕДЖМЕНТ

```
ЖЕЛЕЗНЫЕ ПРАВИЛА:

1. Max 10% daily loss ($10 при $100 капитале)
2. После 3 losses подряд → пауза 30 мин
3. После 5 losses → STOP на день
4. Max 2 позиции одновременно
5. Max 50% капитала в рынке
6. Каждую пятницу → review и корректировка
```

---

**Этот план РЕАЛИСТИЧЕН для $100 капитала при правильном исполнении AI и discipline.**
