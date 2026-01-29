# HOPE AI - "Тайные Идеи" Technical Specification v1.0

**Дата:** 2026-01-29
**Автор:** Claude (opus-4) + Valentin
**Статус:** DRAFT → REVIEW
**SSoT:** Этот документ

---

## 1. EXECUTIVE SUMMARY

### 1.1 Цель
Система раннего обнаружения пампов криптовалют на основе паттернов MoonBot сигналов с автоматическим исполнением сделок в режиме SUPER_SCALP/SCALP.

### 1.2 Ключевая гипотеза
> **За 30-60 секунд ДО пампа появляются характерные паттерны:**
> - VolRaise > 50% (рост объёма)
> - Buys/sec > 3 (активные покупки)
> - dBTC5m > dBTC1m (ускорение)
> - Delta ↗↗↗ (последовательный рост)

### 1.3 Текущий статус

| Компонент | Статус | Результат |
|-----------|--------|-----------|
| PumpPrecursorDetector | ✅ Работает | SENT, WLD, XVS → BUY @ 90% |
| ModeRouter | ✅ Работает | SUPER_SCALP/SCALP/SWING |
| AI Model v1 | ✅ Обучена | 136 samples, rule-based |
| Signal Collection | ✅ Готово | 227 сигналов |
| **Live Integration** | ❌ Нет | Критично! |
| **Outcome Tracking** | ❌ Нет | Нужно для обучения |
| **Real-time Execution** | ❌ Нет | Нужно для прибыли |

---

## 2. ARCHITECTURE

### 2.1 System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        HOPE "SECRET IDEAS" PIPELINE                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  [MoonBot TG]  ──────────┐                                             │
│                          │                                             │
│  [Binance WS] ───────────┼──► [Signal Aggregator] ──► [Feature Extract]│
│                          │           │                      │          │
│  [News RSS] ─────────────┘           │                      ▼          │
│                                      │              ┌───────────────┐  │
│                                      │              │ Precursor     │  │
│                                      │              │ Detector      │  │
│                                      │              │ (паттерны)    │  │
│                                      │              └───────┬───────┘  │
│                                      │                      │          │
│                                      │                      ▼          │
│                                      │              ┌───────────────┐  │
│                                      │              │ Mode Router   │  │
│                                      │              │ SUPER/SCALP/  │  │
│                                      │              │ SWING/SKIP    │  │
│                                      │              └───────┬───────┘  │
│                                      │                      │          │
│                                      │                      ▼          │
│                                      │              ┌───────────────┐  │
│                                      └─────────────►│ Decision      │  │
│                                                     │ Engine        │  │
│                                                     │ (fail-closed) │  │
│                                                     └───────┬───────┘  │
│                                                             │          │
│                          ┌──────────────────────────────────┼──────┐   │
│                          │                                  │      │   │
│                          ▼                                  ▼      │   │
│                   ┌─────────────┐                    ┌───────────┐ │   │
│                   │ Telegram    │                    │ Binance   │ │   │
│                   │ Alerts      │                    │ Executor  │ │   │
│                   └─────────────┘                    └─────┬─────┘ │   │
│                                                            │       │   │
│                                                            ▼       │   │
│                                                     ┌───────────┐  │   │
│                                                     │ Outcome   │◄─┘   │
│                                                     │ Tracker   │      │
│                                                     │ (MFE/MAE) │      │
│                                                     └───────────┘      │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Data Flow

```
1. INGESTION (< 100ms)
   MoonBot TG Message → Parser → RawSignal

2. ENRICHMENT (< 200ms)
   RawSignal + BinanceWS(price, orderbook) → EnrichedSignal

3. DETECTION (< 50ms)
   EnrichedSignal → PrecursorDetector → PrecursorResult(BUY/WATCH/SKIP)

4. ROUTING (< 10ms)
   PrecursorResult → ModeRouter → RouteResult(mode, config)

5. DECISION (< 10ms)
   RouteResult → DecisionEngine → Decision(BUY/SKIP + reasons)

6. EXECUTION (< 100ms for SUPER_SCALP)
   Decision(BUY) → BinanceExecutor → Order

7. TRACKING (async)
   Order → OutcomeTracker → MFE/MAE/Result → FeedbackLoop
```

### 2.3 Latency Budget

| Stage | Budget | Requirement |
|-------|--------|-------------|
| TG Message Parse | 50ms | P95 |
| Binance WS Enrich | 100ms | P95 |
| Precursor Detect | 30ms | P99 |
| Mode Route | 5ms | P99 |
| Decision | 5ms | P99 |
| Order Submit | 100ms | P95 |
| **Total SUPER_SCALP** | **<300ms** | **P95** |
| **Total SCALP** | **<500ms** | **P95** |

---

## 3. COMPONENTS SPECIFICATION

### 3.1 Signal Aggregator

**Файл:** `ai_gateway/ingestion/signal_aggregator.py`

**Интерфейс:**
```python
@dataclass
class RawSignal:
    source: str              # "moonbot" | "binance" | "news"
    symbol: str              # "BTCUSDT"
    timestamp: datetime
    data: Dict[str, Any]     # Source-specific data
    checksum: str            # sha256:...

class SignalAggregator:
    async def ingest(self, source: str, raw_data: bytes) -> RawSignal
    async def subscribe(self, callback: Callable[[RawSignal], None])
    def get_stats(self) -> Dict
```

**MoonBot Parser:**
```python
# Input: TG message text
# Output: RawSignal with:
{
    "symbol": "XVSUSDT",
    "strategy": "TopMarket",      # TopMarket | DropsDetection | PumpDetection
    "direction": "LONG",          # LONG | SHORT
    "delta_pct": 17.31,
    "delta_btc_1m": 0.5,
    "delta_btc_5m": 1.2,
    "vol_raise_pct": 150.0,
    "buys_per_sec": 33.0,
    "price": 3.54,
    "raw_text": "..."
}
```

### 3.2 Binance WebSocket Enricher

**Файл:** `ai_gateway/feeds/binance_ws_enricher.py`

**Интерфейс:**
```python
@dataclass
class EnrichedSignal:
    raw: RawSignal
    binance: BinanceData
    latency_ms: float
    enriched_at: datetime
    checksum: str

@dataclass
class BinanceData:
    price: float
    bid: float
    ask: float
    spread_pct: float
    volume_24h: float
    orderbook_imbalance: float  # (bids - asks) / total
    trades_1m: int
    avg_trade_size: float

class BinanceWSEnricher:
    async def enrich(self, signal: RawSignal) -> EnrichedSignal
    async def start(self)
    async def stop(self)
    def get_orderbook(self, symbol: str) -> OrderBook
    def get_recent_trades(self, symbol: str, n: int = 100) -> List[Trade]
```

**WebSocket Streams:**
```python
STREAMS = [
    "{symbol}@aggTrade",     # Aggregated trades
    "{symbol}@depth20@100ms", # Orderbook depth
    "{symbol}@ticker",       # 24h ticker
]
```

### 3.3 Pump Precursor Detector (ENHANCED)

**Файл:** `ai_gateway/patterns/pump_precursor_detector.py`

**Интерфейс:**
```python
@dataclass
class PrecursorResult:
    prediction: str           # "BUY" | "WATCH" | "SKIP"
    confidence: float         # 0.0 - 1.0
    signals_detected: List[str]
    pattern_scores: Dict[str, float]
    timestamp: datetime
    checksum: str

class PumpPrecursorDetector:
    # Thresholds (tunable)
    THRESHOLDS = {
        'vol_raise_min': 50,       # Volume growth %
        'buys_per_sec_min': 3,     # Buying activity
        'delta_acceleration': 0.5, # dBTC5m - dBTC1m
        'delta_sequence': [0.5, 1.0, 2.0],  # Growing deltas
        'orderbook_imbalance_min': 0.2,     # NEW: bid pressure
        'spread_max_pct': 0.5,     # NEW: max spread
    }
    
    def detect(self, signal: EnrichedSignal) -> PrecursorResult
    def detect_batch(self, signals: List[EnrichedSignal]) -> List[PrecursorResult]
    def update_thresholds(self, new_thresholds: Dict)  # For self-improvement
    def get_stats(self) -> Dict
```

**Detection Algorithm v2:**
```python
def detect(self, signal: EnrichedSignal) -> PrecursorResult:
    scores = {}
    
    # Pattern 1: Volume Raise
    if signal.data['vol_raise_pct'] >= self.THRESHOLDS['vol_raise_min']:
        scores['volume_raise'] = min(1.0, signal.data['vol_raise_pct'] / 100)
    
    # Pattern 2: Active Buys
    if signal.data['buys_per_sec'] >= self.THRESHOLDS['buys_per_sec_min']:
        scores['active_buys'] = min(1.0, signal.data['buys_per_sec'] / 30)
    
    # Pattern 3: Acceleration (dBTC5m > dBTC1m)
    acceleration = signal.data['delta_btc_5m'] - signal.data['delta_btc_1m']
    if acceleration >= self.THRESHOLDS['delta_acceleration']:
        scores['accelerating'] = min(1.0, acceleration / 2.0)
    
    # Pattern 4: Delta Sequence (growing)
    if self._check_delta_sequence(signal):
        scores['delta_growing'] = 0.8
    
    # Pattern 5: Orderbook Imbalance (NEW)
    if signal.binance.orderbook_imbalance >= self.THRESHOLDS['orderbook_imbalance_min']:
        scores['orderbook_pressure'] = min(1.0, signal.binance.orderbook_imbalance)
    
    # Pattern 6: Low Spread (NEW - good liquidity)
    if signal.binance.spread_pct <= self.THRESHOLDS['spread_max_pct']:
        scores['good_liquidity'] = 1.0 - (signal.binance.spread_pct / 1.0)
    
    # Decision
    signals_detected = list(scores.keys())
    confidence = sum(scores.values()) / 6  # Normalize to 6 patterns
    
    if len(signals_detected) >= 4 and confidence >= 0.6:
        prediction = "BUY"
    elif len(signals_detected) >= 2 and confidence >= 0.3:
        prediction = "WATCH"
    else:
        prediction = "SKIP"
    
    return PrecursorResult(
        prediction=prediction,
        confidence=confidence,
        signals_detected=signals_detected,
        pattern_scores=scores,
        ...
    )
```

### 3.4 Mode Router (EXISTS)

**Файл:** `ai_gateway/core/mode_router.py`

**Уже реализован.** См. Phase 3.1 delivery.

### 3.5 Decision Engine Integration

**Файл:** `ai_gateway/core/decision_engine.py` (UPDATE)

**Новый flow:**
```python
class DecisionEngine:
    def __init__(self):
        self.precursor_detector = PumpPrecursorDetector()
        self.mode_router = ModeRouter()
        # ... existing components
    
    def evaluate(self, signal: EnrichedSignal) -> Decision:
        # Step 1: Precursor Detection
        precursor = self.precursor_detector.detect(signal)
        if precursor.prediction == "SKIP":
            return Decision(action="SKIP", reason="precursor_skip")
        
        # Step 2: Mode Routing
        route = self.mode_router.route(signal.to_dict())
        if route.mode == TradingMode.SKIP:
            return Decision(action="SKIP", reason="mode_skip")
        
        # Step 3: Existing checks (circuit, cooldown, positions, etc.)
        checks = self._run_checks(signal, route)
        if not all(checks.values()):
            failed = [k for k, v in checks.items() if not v]
            return Decision(action="SKIP", reasons=failed)
        
        # Step 4: Final Decision
        return Decision(
            action="BUY",
            symbol=signal.symbol,
            mode=route.mode,
            config=route.config,
            precursor_confidence=precursor.confidence,
            route_confidence=route.confidence,
        )
```

### 3.6 Outcome Tracker (ENHANCED)

**Файл:** `ai_gateway/modules/self_improver/outcome_tracker.py`

**Интерфейс:**
```python
@dataclass
class TradeOutcome:
    signal_id: str
    symbol: str
    entry_price: float
    entry_time: datetime
    exit_price: Optional[float]
    exit_time: Optional[datetime]
    exit_reason: str          # "target" | "stop" | "timeout" | "manual"
    mfe_pct: float            # Maximum Favorable Excursion
    mae_pct: float            # Maximum Adverse Excursion
    pnl_pct: float
    duration_sec: float
    mode: str                 # SUPER_SCALP | SCALP | SWING
    precursor_signals: List[str]
    checksum: str

class OutcomeTracker:
    async def register_entry(self, decision: Decision, order: Order) -> str
    async def update_price(self, symbol: str, price: float)
    async def finalize(self, signal_id: str, exit_reason: str) -> TradeOutcome
    def get_stats(self) -> OutcomeStats
    def get_by_mode(self, mode: str) -> List[TradeOutcome]
    def export_for_training(self) -> List[Dict]  # For ML model
```

**Stats Structure:**
```python
@dataclass
class OutcomeStats:
    total_trades: int
    win_rate: float           # % trades with PnL > 0
    avg_pnl_pct: float
    avg_mfe_pct: float
    avg_mae_pct: float
    avg_duration_sec: float
    by_mode: Dict[str, ModeStats]
    by_precursor: Dict[str, PrecursorStats]  # Which patterns work best?
    
@dataclass
class ModeStats:
    trades: int
    win_rate: float
    avg_pnl: float
    sharpe: float             # If enough data
```

### 3.7 Telegram Commands (NEW)

**Файл:** `ai_gateway/telegram/commands.py`

**Commands:**
```
/predict XVSUSDT      - Manual prediction check
/stats                - Current performance stats
/history [n]          - Last n trades
/mode                 - Current mode distribution
/thresholds           - Show/update thresholds
/enable_live          - Enable live trading (admin only)
/disable_live         - Disable live trading (admin only)
```

**Example Response:**
```
📊 PREDICTION: XVSUSDT

Precursor: BUY (87%)
├── ✓ volume_raise: 0.85
├── ✓ active_buys: 0.72
├── ✓ accelerating: 0.60
├── ✓ orderbook_pressure: 0.45
├── ✗ delta_growing: -
└── ✓ good_liquidity: 0.90

Mode: SUPER_SCALP
├── Target: +0.5%
├── Stop: -0.3%
└── Timeout: 30s

Decision: ✅ BUY
Confidence: 0.85
```

---

## 4. DATA CONTRACTS

### 4.1 Signal JSONL Schema

**Файл:** `state/ai/signals_*.jsonl`

```json
{
  "schema_version": "1.0",
  "signal_id": "sig:a1b2c3d4",
  "timestamp": "2026-01-29T12:00:00.123Z",
  "source": "moonbot",
  "symbol": "XVSUSDT",
  "data": {
    "strategy": "TopMarket",
    "direction": "LONG",
    "delta_pct": 17.31,
    "delta_btc_1m": 0.5,
    "delta_btc_5m": 1.2,
    "vol_raise_pct": 150.0,
    "buys_per_sec": 33.0,
    "price": 3.54
  },
  "enrichment": {
    "binance_price": 3.55,
    "spread_pct": 0.28,
    "volume_24h": 5400000,
    "orderbook_imbalance": 0.35
  },
  "precursor": {
    "prediction": "BUY",
    "confidence": 0.87,
    "signals": ["volume_raise", "active_buys", "accelerating", "orderbook_pressure"]
  },
  "route": {
    "mode": "super_scalp",
    "confidence": 0.95
  },
  "decision": {
    "action": "BUY",
    "checks_passed": true
  },
  "checksum": "sha256:abcd1234"
}
```

### 4.2 Outcome JSONL Schema

**Файл:** `state/ai/outcomes_*.jsonl`

```json
{
  "schema_version": "1.0",
  "outcome_id": "out:e5f6g7h8",
  "signal_id": "sig:a1b2c3d4",
  "symbol": "XVSUSDT",
  "mode": "super_scalp",
  "entry": {
    "price": 3.55,
    "time": "2026-01-29T12:00:00.500Z",
    "order_id": "12345678"
  },
  "tracking": {
    "mfe_pct": 0.62,
    "mfe_time": "2026-01-29T12:00:15.000Z",
    "mae_pct": 0.08,
    "mae_time": "2026-01-29T12:00:05.000Z"
  },
  "exit": {
    "price": 3.57,
    "time": "2026-01-29T12:00:18.000Z",
    "reason": "target",
    "pnl_pct": 0.56
  },
  "duration_sec": 17.5,
  "precursor_signals": ["volume_raise", "active_buys", "accelerating", "orderbook_pressure"],
  "checksum": "sha256:ijkl5678"
}
```

---

## 5. SAFETY & FAIL-CLOSED

### 5.1 Invariants

```python
# 1. NO TRADE без всех проверок
assert precursor.prediction != "SKIP"
assert route.mode != TradingMode.SKIP
assert all(checks.values()) == True
assert circuit_breaker.is_closed()

# 2. Latency limits
assert total_latency_ms < mode.config.latency_max_ms

# 3. Position limits
assert open_positions < MAX_POSITIONS  # 3 for SUPER_SCALP
assert position_size_pct <= mode.config.max_capital_pct

# 4. Daily loss limit
assert daily_loss_pct < mode.config.daily_loss_limit_pct
```

### 5.2 Circuit Breaker Rules

```python
CIRCUIT_BREAKER = {
    'super_scalp': {
        'consecutive_losses': 3,    # Open after 3 losses
        'cooldown_sec': 60,
        'daily_max_losses': 10,
    },
    'scalp': {
        'consecutive_losses': 5,
        'cooldown_sec': 180,
        'daily_max_losses': 15,
    },
    'swing': {
        'consecutive_losses': 5,
        'cooldown_sec': 300,
        'daily_max_losses': 10,
    },
}
```

### 5.3 Fail-Closed Defaults

| Condition | Action |
|-----------|--------|
| Binance WS disconnected | STOP all trading |
| MoonBot TG disconnected > 5 min | ALERT + reduce position size |
| Price feed stale > 10 sec | SKIP all new signals |
| Unknown error | SKIP + LOG + ALERT |
| Latency > budget | SKIP + LOG |

---

## 6. SELF-IMPROVEMENT LOOP

### 6.1 Feedback Pipeline

```
Outcomes → Aggregate by Precursor Pattern → Calculate Win Rate
    │
    ├── Pattern "volume_raise" → 72% win rate → KEEP
    ├── Pattern "active_buys" → 68% win rate → KEEP
    ├── Pattern "accelerating" → 45% win rate → REVIEW
    └── Pattern "delta_growing" → 38% win rate → LOWER WEIGHT
    
Weekly: Adjust thresholds based on outcomes
Monthly: Retrain ML model with new data
```

### 6.2 Threshold Tuning

```python
class ThresholdTuner:
    def analyze(self, outcomes: List[TradeOutcome]) -> Dict[str, float]:
        """
        Analyze outcomes and suggest threshold adjustments.
        
        Returns:
            Dict of pattern -> suggested_threshold_delta
        """
        pattern_stats = {}
        for outcome in outcomes:
            for pattern in outcome.precursor_signals:
                if pattern not in pattern_stats:
                    pattern_stats[pattern] = {'wins': 0, 'losses': 0}
                if outcome.pnl_pct > 0:
                    pattern_stats[pattern]['wins'] += 1
                else:
                    pattern_stats[pattern]['losses'] += 1
        
        suggestions = {}
        for pattern, stats in pattern_stats.items():
            win_rate = stats['wins'] / (stats['wins'] + stats['losses'])
            if win_rate < 0.5:
                suggestions[pattern] = +0.1  # Increase threshold (stricter)
            elif win_rate > 0.7:
                suggestions[pattern] = -0.05  # Decrease threshold (looser)
        
        return suggestions
```

---

## 7. IMPLEMENTATION PHASES

### Phase 1: Live Signal Integration (P0) - 2 days

**Tasks:**
1. [ ] `signal_aggregator.py` - MoonBot TG parser
2. [ ] Connect to existing `hunters_listener_v1.py`
3. [ ] Real-time signal flow to PrecursorDetector
4. [ ] Telegram alerts for BUY signals

**Deliverables:**
- MoonBot → PrecursorDetector → Telegram alert
- No actual trading yet (DRY mode)

**Test:**
```bash
# Start listener
python -m hunters_listener_v1

# Watch for alerts
# Expected: TG message within 5 sec of MoonBot signal
```

### Phase 2: Binance WebSocket Enrichment (P0) - 2 days

**Tasks:**
1. [ ] `binance_ws_enricher.py` - WebSocket client
2. [ ] Orderbook depth tracking
3. [ ] Trade aggregation
4. [ ] Enriched signal pipeline

**Deliverables:**
- Real-time price/orderbook data
- Enriched signals with Binance data

**Test:**
```bash
python -m scripts.test_binance_ws
# Expected: orderbook_imbalance, spread_pct in signal
```

### Phase 3: Outcome Tracking (P0) - 2 days

**Tasks:**
1. [ ] Enhanced `outcome_tracker.py`
2. [ ] MFE/MAE calculation
3. [ ] Stats aggregation
4. [ ] Export for training

**Deliverables:**
- Full trade lifecycle tracking
- Stats by mode and pattern

**Test:**
```bash
python -m scripts.test_outcome_tracker
# Expected: MFE, MAE, PnL for simulated trade
```

### Phase 4: Telegram Integration (P1) - 1 day

**Tasks:**
1. [ ] `/predict` command
2. [ ] `/stats` command
3. [ ] `/history` command
4. [ ] Admin controls

**Deliverables:**
- Interactive prediction checks
- Performance monitoring

### Phase 5: Live Trading (P1) - 3 days

**Tasks:**
1. [ ] TESTNET integration
2. [ ] Order execution
3. [ ] Position management
4. [ ] 24h TESTNET validation

**Gates:**
- [ ] 100+ signals processed without error
- [ ] Win rate > 50% on simulated trades
- [ ] No circuit breaker issues

### Phase 6: ML Model v2 (P2) - 5 days

**Tasks:**
1. [ ] Feature engineering
2. [ ] XGBoost/LightGBM training
3. [ ] A/B testing framework
4. [ ] Model versioning

**Deliverables:**
- ML model with >60% accuracy
- Automated retraining pipeline

---

## 8. FILES STRUCTURE

```
ai_gateway/
├── ingestion/
│   ├── __init__.py
│   ├── signal_aggregator.py      # NEW
│   └── moonbot_parser.py         # NEW
├── feeds/
│   ├── __init__.py
│   ├── binance_ws.py             # EXISTS
│   └── binance_ws_enricher.py    # NEW
├── patterns/
│   ├── __init__.py
│   └── pump_precursor_detector.py # UPDATE
├── core/
│   ├── __init__.py
│   ├── mode_router.py            # EXISTS
│   ├── decision_engine.py        # UPDATE
│   └── circuit_breaker.py        # EXISTS
├── modules/
│   └── self_improver/
│       ├── outcome_tracker.py    # UPDATE
│       └── threshold_tuner.py    # NEW
├── telegram/
│   ├── __init__.py
│   └── commands.py               # NEW
└── execution/
    ├── __init__.py
    └── binance_executor.py       # EXISTS
```

---

## 9. ACCEPTANCE CRITERIA

### 9.1 Phase 1 Gate (DRY)

- [ ] MoonBot signal → Telegram alert < 5 sec
- [ ] 100% signal parsing success rate
- [ ] Precursor detection matches manual analysis

### 9.2 Phase 2 Gate (TESTNET)

- [ ] 24h uptime without crash
- [ ] < 500ms total latency P95
- [ ] Win rate > 50% on paper trades

### 9.3 Phase 3 Gate (LIVE)

- [ ] 7 days TESTNET without circuit breaker
- [ ] Sharpe ratio > 1.0
- [ ] Max drawdown < 5%
- [ ] Human approval

---

## 10. MONITORING & ALERTS

### 10.1 Metrics

```python
METRICS = {
    'signals_processed': Counter,
    'precursor_predictions': Counter(labels=['prediction']),
    'mode_routes': Counter(labels=['mode']),
    'decisions': Counter(labels=['action']),
    'latency_ms': Histogram(buckets=[50, 100, 200, 500, 1000]),
    'win_rate': Gauge,
    'daily_pnl': Gauge,
    'open_positions': Gauge,
}
```

### 10.2 Alerts

| Alert | Condition | Action |
|-------|-----------|--------|
| HIGH_LATENCY | P95 > 500ms | Telegram + Log |
| CIRCUIT_OPEN | Any mode | Telegram + STOP |
| CONNECTION_LOST | WS disconnect > 30s | Telegram + STOP |
| DAILY_LOSS | > 3% | Telegram + STOP |
| WIN_RATE_LOW | < 40% over 20 trades | Telegram + Review |

---

## 11. APPENDIX

### A. Environment Variables

```bash
# Binance
BINANCE_API_KEY=...
BINANCE_SECRET_KEY=...
BINANCE_TESTNET=true

# Telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ADMIN_CHAT_ID=...
MOONBOT_CHANNEL_ID=...

# AI Gateway
AI_GATEWAY_PORT=8100
AI_GATEWAY_MODE=DRY  # DRY | TESTNET | LIVE

# Anthropic (for AI modules)
ANTHROPIC_API_KEY=...
```

### B. Commands Reference

```bash
# Development
python -m scripts.test_ai_gateway
python -m scripts.test_precursor_detector
python -m scripts.test_mode_router
python -m scripts.test_outcome_tracker

# Operations
python -m ai_gateway.server
python -m hunters_listener_v1

# Monitoring
python -m scripts.sources_manager report
python -m scripts.stats_report
```

### C. Checksum Verification

```python
def verify_signal(signal: dict) -> bool:
    """Verify signal checksum"""
    data = json.dumps(signal, sort_keys=True, default=str)
    expected = f"sha256:{sha256(data.encode()).hexdigest()[:16]}"
    return signal.get('checksum') == expected
```

---

**END OF SPECIFICATION**

**Version:** 1.0
**Checksum:** sha256:{to_be_computed}
**Status:** DRAFT → Валентин review required
