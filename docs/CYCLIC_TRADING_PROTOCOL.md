# 🔄 HOPE CYCLIC TRADING PROTOCOL v2.0

**Дата:** 2026-01-31
**Капитал:** $100.12 USDT
**Режим:** LIVE PRODUCTION
**Стратегия:** AI Scalping + Dynamic Position Sizing + Compound Growth

---

## ⚡ ГЛАВНЫЙ ПРИНЦИП

```
БОЛЬШЕ ДЕПОЗИТ = БОЛЬШЕ ОРДЕР

$100  → позиция $20 (20%)
$150  → позиция $30 (20%)
$200  → позиция $40 (20%)
$500  → позиция $100 (20%)
$1000 → позиция $200 (20%)
```

---

## 🚀 ПРОТОКОЛ ЗАПУСКА

### Быстрый старт (1 команда):

```powershell
cd C:\Users\kirillDev\Desktop\TradingBot\minibot
.\tools\start_hope_trading.ps1 -Restart
```

### Проверка без запуска:

```powershell
.\tools\start_hope_trading.ps1 -Check
```

---

## 📋 ПОРЯДОК ЗАПУСКА КОМПОНЕНТОВ

```
┌─────────────────────────────────────────────────────────────────────┐
│                    STARTUP SEQUENCE                                  │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  [1] PREFLIGHT CHECKS (обязательно!)                                │
│      ├─ Balance check (min $10)                                     │
│      ├─ API connection                                              │
│      ├─ Config files                                                │
│      ├─ Module syntax (py_compile)                                  │
│      └─ Calculate initial position size                             │
│                          ↓                                           │
│  [2] PRICEFEED BRIDGE (port 8100)                                   │
│      └─ Must start FIRST - all others depend on prices              │
│      └─ Wait 3 sec for initial data                                 │
│                          ↓                                           │
│  [3] POSITION WATCHDOG (--live)                                     │
│      └─ Protects existing positions                                 │
│      └─ Trailing stop, TP/SL enforcement                            │
│                          ↓                                           │
│  [4] EYE OF GOD V3 (--daemon)                                       │
│      └─ AI decision engine                                          │
│      └─ Adaptive targets calculation                                │
│      └─ Dynamic position sizing                                     │
│                          ↓                                           │
│  [5] SCALPING PIPELINE (--live)                                     │
│      └─ Pump detection                                              │
│      └─ Signal generation                                           │
│      └─ Order execution                                             │
│                          ↓                                           │
│  [6] DASHBOARD (port 8080)                                          │
│      └─ http://localhost:8080                                       │
│      └─ Real-time monitoring                                        │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 💰 ДИНАМИЧЕСКИЙ РАЗМЕР ПОЗИЦИИ

### Формула:

```python
position_size = balance × base_pct × confidence_mult × loss_adjust × compound_mult

Где:
- balance         = текущий USDT баланс (real-time с Binance)
- base_pct        = 20% (базовый процент)
- confidence_mult = 0.75 - 1.25 (по AI confidence)
- loss_adjust     = 0.5 - 1.0 (после серии убытков)
- compound_mult   = 1.0 - 1.5 (бонус за рост депозита)
```

### Таблица размеров:

| Баланс | Base (20%) | Low Conf (65%) | High Conf (85%) | After Losses |
|--------|------------|----------------|-----------------|--------------|
| $100 | $20 | $15 | $25 | $10-15 |
| $150 | $30 | $22.5 | $37.5 | $15-22 |
| $200 | $40 | $30 | $50 | $20-30 |
| $300 | $60 | $45 | $75 | $30-45 |
| $500 | $100 | $75 | $125 | $50-75 |

### Confidence Scaling:

```
Confidence >= 85%  → размер × 1.25 (агрессивно)
Confidence >= 75%  → размер × 1.00 (нормально)
Confidence >= 65%  → размер × 0.75 (осторожно)
```

### Loss Adjustment:

```
0 losses        → размер × 1.00
2 losses подряд → размер × 0.75
3+ losses       → размер × 0.50 (минимум)
После 2 wins    → восстановление к × 1.00
```

### Compound Bonus:

```
Рост 0-10%   → размер × 1.00
Рост 10-20%  → размер × 1.05
Рост 20-30%  → размер × 1.10
Рост 30-40%  → размер × 1.15
...
Max bonus    → размер × 1.50 (+50%)
```

---

## 🔄 ТОРГОВЫЙ ЦИКЛ

```
┌─────────────────────────────────────────────────────────────────────┐
│                      TRADING CYCLE                                   │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────────┐                                               │
│  │ 1. SIGNAL DETECT │ ← Binance WebSocket (pump_detector)           │
│  │    Volume spike  │                                               │
│  │    Buy dominance │                                               │
│  │    Delta > 0.5%  │                                               │
│  └────────┬─────────┘                                               │
│           │                                                          │
│           ▼                                                          │
│  ┌──────────────────┐                                               │
│  │ 2. AI EVALUATION │ ← Eye of God V3                               │
│  │    Alpha chamber │                                               │
│  │    Risk chamber  │                                               │
│  │    ML prediction │                                               │
│  │    Conf >= 65%?  │                                               │
│  └────────┬─────────┘                                               │
│           │ YES                                                      │
│           ▼                                                          │
│  ┌──────────────────┐                                               │
│  │ 3. SIZE CALC     │ ← DynamicPositionSizer                        │
│  │    Get balance   │    (real-time from Binance)                   │
│  │    Apply formula │                                               │
│  │    Check limits  │                                               │
│  └────────┬─────────┘                                               │
│           │                                                          │
│           ▼                                                          │
│  ┌──────────────────┐                                               │
│  │ 4. ADAPTIVE TP   │ ← AdaptiveTargetEngine                        │
│  │    Volatility    │                                               │
│  │    Momentum      │                                               │
│  │    Regime        │                                               │
│  │    R:R >= 2.5:1  │                                               │
│  └────────┬─────────┘                                               │
│           │                                                          │
│           ▼                                                          │
│  ┌──────────────────┐                                               │
│  │ 5. EXECUTE ORDER │ → Binance MARKET BUY                          │
│  │    Position size │                                               │
│  │    Set TP/SL     │                                               │
│  └────────┬─────────┘                                               │
│           │                                                          │
│           ▼                                                          │
│  ┌──────────────────┐                                               │
│  │ 6. WATCHDOG      │ ← position_watchdog                           │
│  │    Monitor price │                                               │
│  │    Trailing stop │                                               │
│  │    Partial TP    │                                               │
│  └────────┬─────────┘                                               │
│           │                                                          │
│           ▼                                                          │
│  ┌──────────────────┐                                               │
│  │ 7. CLOSE         │ → Binance MARKET SELL                         │
│  │    TP_HIT        │                                               │
│  │    SL_HIT        │                                               │
│  │    TRAILING      │                                               │
│  │    TIMEOUT       │                                               │
│  └────────┬─────────┘                                               │
│           │                                                          │
│           ▼                                                          │
│  ┌──────────────────┐                                               │
│  │ 8. UPDATE STATE  │ ← DynamicPositionSizer                        │
│  │    Record result │                                               │
│  │    Refresh bal   │                                               │
│  │    Recalc size   │ → NEXT TRADE BIGGER IF PROFIT!               │
│  └──────────────────┘                                               │
│                                                                      │
│           │                                                          │
│           └──────────────────────────────────────────────┐          │
│                                                          │          │
│  ┌──────────────────────────────────────────────────────┐│          │
│  │              REPEAT CYCLE FOREVER                    ││          │
│  │                                                      ││          │
│  │   $100 → $120 → $150 → $200 → $300 → $500 → ...    │◀┘          │
│  │                                                      │           │
│  │   Позиции растут автоматически!                     │           │
│  └──────────────────────────────────────────────────────┘           │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## ⚙️ ПАРАМЕТРЫ КОНФИГУРАЦИИ

### Файл: `config/scalping_100.json`

```json
{
  "position_sizing": {
    "base_pct": 20,           // 20% от баланса
    "min_size_usd": 10,       // Минимум $10
    "max_size_usd": 50,       // Максимум $50 (на старте)
    "max_exposure_pct": 50    // Max 50% в позициях
  },
  "risk_management": {
    "max_daily_loss_usd": 15, // Стоп на день: -$15
    "max_consecutive_losses": 3,
    "loss_reduction_factor": 0.75
  },
  "targets": {
    "base_tp_pct": 1.5,       // Take Profit 1.5%
    "base_sl_pct": 0.5,       // Stop Loss 0.5%
    "min_rr": 2.5             // Min R:R 2.5:1
  },
  "compound": {
    "enabled": true,
    "increase_step_pct": 10   // Увеличивать каждые 10% роста
  }
}
```

---

## 📊 ПРОГНОЗ РОСТА (Compound Effect)

```
СЦЕНАРИЙ: 65% Win Rate, 15 сделок/день, TP=1.5%, SL=0.5%

День 1:  $100.00 → $103.50 (+3.5%)
День 5:  $103.50 → $120.00 (+16%)
День 10: $120.00 → $155.00 (+29%)
День 15: $155.00 → $210.00 (+35%)
День 20: $210.00 → $290.00 (+38%)
День 25: $290.00 → $410.00 (+41%)
День 30: $410.00 → $580.00 (+41%)

ИТОГО: $100 → $500-600 за месяц (500%+ ROI)
```

### С более высоким Win Rate (70%):

```
День 1:  $100.00 → $105.50
День 10: $105.50 → $180.00
День 20: $180.00 → $380.00
День 30: $380.00 → $800.00+

ИТОГО: $100 → $700-1000 за месяц (700%+ ROI)
```

---

## 🛡️ ЗАЩИТНЫЕ МЕХАНИЗМЫ

### 1. Daily Loss Limit

```python
if daily_pnl <= -$15:
    STOP_TRADING_TODAY()
    ALERT("Daily loss limit reached")
```

### 2. Consecutive Loss Protection

```python
if consecutive_losses >= 3:
    position_size *= 0.50  # Минимальные позиции
    cooldown(30_minutes)
```

### 3. Exposure Limit

```python
if current_exposure >= balance * 0.50:
    BLOCK_NEW_POSITIONS()
```

### 4. Trailing Stop

```python
if pnl >= 1.0%:
    ACTIVATE_TRAILING(distance=0.5%)
```

### 5. Partial Profit

```python
if pnl >= 1.5%:
    SELL_50%()  # Lock in profits
```

---

## 🔴 КОМАНДЫ УПРАВЛЕНИЯ

### Запуск:

```powershell
# Полный перезапуск
.\tools\start_hope_trading.ps1 -Restart

# Только проверки
.\tools\start_hope_trading.ps1 -Check

# Принудительный запуск
.\tools\start_hope_trading.ps1 -Force
```

### Мониторинг:

```powershell
# Баланс и статус
python core/dynamic_position_sizer.py --status

# Расчёт позиции
python core/dynamic_position_sizer.py --calculate 0.75

# Логи
Get-Content logs/eye_of_god.log -Tail 50 -Wait
```

### Остановка:

```powershell
# Мягкая (дождаться закрытия позиций)
# Через Dashboard → STOP

# Жёсткая
Get-Process python* | Stop-Process -Force
```

---

## ✅ CHECKLIST ПЕРЕД СТАРТОМ

```
□ Баланс >= $100 USDT
□ API ключи актуальны
□ config/scalping_100.json существует
□ Все py_compile PASS
□ Pricefeed обновляется
□ Dashboard доступен на :8080
□ Watchdog запущен с --live
□ Eye of God запущен с --daemon
□ Нет открытых позиций (или учтены)
```

---

## 📈 KPIs ДЛЯ ОТСЛЕЖИВАНИЯ

| Метрика | Target | Critical |
|---------|--------|----------|
| Win Rate | >= 60% | < 50% → STOP |
| R:R | >= 2.5:1 | < 2:1 → Review |
| Daily PnL | > $0 | < -$15 → STOP |
| Uptime | > 95% | < 80% → Fix |
| Position Size | Growing | Static → Check |

---

**Этот протокол обеспечивает автоматический рост позиций вместе с ростом депозита.**
**Больше прибыль = больше следующая позиция = ещё больше прибыль!**

🔄 **COMPOUND GROWTH ACTIVATED**
