# HOPE AI TRADING SYSTEM — TZ v4.0 (FINAL)

<!-- AI SIGNATURE: Created by Claude (opus-4) at 2026-01-29 12:00:00 UTC -->

## METADATA
| Field | Value |
|-------|-------|
| Version | 4.0 |
| Date | 2026-01-29 |
| Author | Claude (opus-4) + Valentin |
| SSoT | docs/HOPE_AI_TRADING_TZ_v4.md |
| Status | ACTIVE |

---

## PART 0: CURRENT STATE

### COMPLETED (Phase 1-2.5):
| File | Lines | Status |
|------|-------|--------|
| ai_gateway/core/event_bus.py | ~350 | DONE |
| ai_gateway/core/decision_engine.py | ~450 | DONE |
| ai_gateway/core/signal_processor.py | ~280 | DONE |
| ai_gateway/feeds/binance_ws.py | ~420 | DONE |
| ai_gateway/modules/self_improver/* | ~1200 | DONE |
| scripts/sources_manager.py | ~500 | DONE |
| scripts/update_market_intel.py | ~200 | DONE |
| scripts/test_ai_gateway.py | ~300 | DONE |

### Current Metrics:
- **SOURCES:** 20 endpoints, 19 active
- **SIGNALS:** 78/100 (need 22 more)
- **MODEL:** Not trained (waiting for 100+ samples)
- **COMMIT:** 8fe8de4 (rollback point)

---

## PART 1: SYSTEM ARCHITECTURE

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    HOPE AI TRADING SYSTEM v4.0                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  INGESTION LAYER                                                        │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐                   │
│  │ MoonBot  │ │ Binance  │ │CoinGecko │ │   RSS    │                   │
│  │  Parser  │ │    WS    │ │   API    │ │  Feeds   │                   │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘                   │
│       └────────────┴────────────┴────────────┘                         │
│                           │                                             │
│                           ▼                                             │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                      EVENT BUS                                   │   │
│  │  Channels: signals | prices | news | predictions | outcomes     │   │
│  └──────────────────────────────┬──────────────────────────────────┘   │
│                                 │                                       │
│                                 ▼                                       │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                      MODE ROUTER (NEW)                          │   │
│  │  ┌───────────┐  ┌───────────┐  ┌───────────┐                   │   │
│  │  │SUPER_SCALP│  │   SCALP   │  │   SWING   │                   │   │
│  │  │  5-30 sec │  │ 30-120 sec│  │  5-15 min │                   │   │
│  │  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘                   │   │
│  └────────┼──────────────┼──────────────┼──────────────────────────┘   │
│           └──────────────┼──────────────┘                              │
│                          ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                      AI MODULES                                  │   │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────────────┐│   │
│  │  │ Regime │ │Anomaly │ │Sentim. │ │Predict │ │ Self-Improver  ││   │
│  │  └────────┘ └────────┘ └────────┘ └────────┘ └────────────────┘│   │
│  └──────────────────────────────┬──────────────────────────────────┘   │
│                                 ▼                                       │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                   DECISION ENGINE                                │   │
│  │  if all(checks) → BUY else → SKIP with reasons                  │   │
│  └──────────────────────────────┬──────────────────────────────────┘   │
│                                 ▼                                       │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                   EXECUTION LAYER                                │   │
│  │  Risk Manager → Circuit Breaker → Binance API                   │   │
│  └──────────────────────────────┬──────────────────────────────────┘   │
│                                 ▼                                       │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                   OUTCOME TRACKER                                │   │
│  │  MFE/MAE → Labels → Training Data → Auto-Retrain               │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## PART 2: TRADING MODES

| Parameter | SUPER_SCALP | SCALP | SWING |
|-----------|-------------|-------|-------|
| Hold Time | 5-30 sec | 30-120 sec | 5-15 min |
| Target | +0.3-0.5% | +1-2% | +3-5% |
| Stop Loss | -0.3% | -1% | -2% |
| Max Position | 5% capital | 10% capital | 20% capital |
| Circuit Break | 3 losses | 5 losses | 5 losses |
| Min Volume | 1M | 3M | 5M |
| Latency Req | <50ms | <200ms | <1s |
| Model | model_ss.joblib | model_s.joblib | model_sw.joblib |

### Signal Classification:

```python
def classify_signal(signal: MoonBotSignal) -> TradingMode:

    if (signal.delta_pct > 5
        and signal.buys_per_sec > 30
        and signal.vol_raise_pct > 100):
        return TradingMode.SUPER_SCALP

    if (signal.delta_pct > 2
        and signal.buys_per_sec > 5
        and signal.vol_raise_pct > 50):
        return TradingMode.SCALP

    if (signal.delta_pct > 1
        and signal.daily_volume > 5_000_000):
        return TradingMode.SWING

    return TradingMode.SKIP
```

---

## PART 3: DATA SOURCES

**SSoT:** `state/sources/sources.json`
**Manager:** `scripts/sources_manager.py`

### Active (19/20):
```
├── BINANCE (8): REST + WebSocket + Testnet
├── MARKET DATA (3): CoinGecko ping/global/price
├── NEWS RSS (4): CoinDesk, Cointelegraph, Decrypt, TheBlock
├── SENTIMENT (1): Fear & Greed Index
└── INFRASTRUCTURE (4): GitHub, PyPI, CheckIP
```

### Commands:
```bash
python -m scripts.sources_manager check    # Check all
python -m scripts.sources_manager report   # Status report
python -m scripts.sources_manager daemon   # Background mode (6h)
```

---

## PART 4: FILE STRUCTURE

```
minibot/
├── ai_gateway/
│   ├── config.py                    ✅ EXISTS
│   ├── contracts.py                 ✅ EXISTS
│   ├── server.py                    ✅ EXISTS
│   ├── telegram_panel.py            ✅ EXISTS
│   │
│   ├── core/
│   │   ├── event_bus.py             ✅ DONE
│   │   ├── decision_engine.py       ✅ DONE
│   │   ├── signal_processor.py      ✅ DONE
│   │   ├── circuit_breaker.py       ✅ EXISTS
│   │   └── mode_router.py           🔴 TODO (Phase 3.1)
│   │
│   ├── feeds/
│   │   ├── binance_ws.py            ✅ DONE
│   │   ├── orderbook_ws.py          🔴 TODO (Phase 3.1)
│   │   └── news_aggregator.py       🔴 TODO (Phase 4)
│   │
│   ├── modules/
│   │   ├── regime/                  ✅ EXISTS
│   │   ├── anomaly/                 ✅ EXISTS
│   │   ├── sentiment/               ✅ EXISTS
│   │   ├── predictor/               ✅ EXISTS
│   │   ├── self_improver/           ✅ DONE
│   │   │
│   │   ├── scalp/                   🔴 TODO (Phase 3.1)
│   │   │   ├── detector.py
│   │   │   ├── executor.py
│   │   │   └── model.py
│   │   │
│   │   ├── super_scalp/             🔴 TODO (Phase 3.2)
│   │   │   ├── detector.py
│   │   │   ├── executor.py
│   │   │   └── model.py
│   │   │
│   │   └── thoughts/                🔴 TODO (Phase 3.3)
│   │       ├── generator.py
│   │       ├── validator.py
│   │       └── integrator.py
│   │
│   └── telegram/                    🔴 TODO (Phase 4)
│       ├── bot.py
│       ├── handlers/
│       └── channel.py
│
├── dashboard/                       🔴 TODO (Phase 4)
│   ├── app.py
│   ├── static/
│   └── templates/
│
├── scripts/
│   ├── sources_manager.py           ✅ DONE
│   ├── update_market_intel.py       ✅ DONE
│   ├── test_ai_gateway.py           ✅ DONE
│   └── parse_moonbot_log.py         ✅ EXISTS
│
├── state/
│   ├── sources/sources.json         ✅ DONE
│   ├── market_intel.json            ✅ DONE
│   └── ai/
│       ├── models/registry.json
│       └── thoughts/                🔴 TODO
│
├── data/moonbot_signals/            ✅ EXISTS
├── docs/HOPE_AI_TRADING_TZ_v4.md    📄 THIS FILE
└── CLAUDE.md                        ✅ EXISTS
```

---

## PART 5: IMPLEMENTATION PHASES

### PHASE 3.1: SCALPING CORE
```
□ ai_gateway/core/mode_router.py
□ ai_gateway/modules/scalp/detector.py
□ ai_gateway/modules/scalp/executor.py
□ ai_gateway/feeds/orderbook_ws.py
□ tests/test_scalp.py

EXIT CRITERIA:
├── Signal classified into SWING/SCALP/SUPER_SCALP
├── Order book depth available via WebSocket
├── Execution latency < 200ms for SCALP mode
└── py_compile + ruff pass
```

### PHASE 3.2: SUPER SCALP
```
□ ai_gateway/modules/super_scalp/detector.py
□ ai_gateway/modules/super_scalp/executor.py
□ Latency optimization (async, connection pooling)
□ tests/test_super_scalp.py

EXIT CRITERIA:
├── SUPER_SCALP detection < 50ms
├── Execution < 100ms total
├── Circuit breaker per mode (3 losses for SS)
└── py_compile + ruff pass
```

### PHASE 3.3: AI THOUGHTS
```
□ ai_gateway/modules/thoughts/generator.py
□ ai_gateway/modules/thoughts/validator.py
□ ai_gateway/modules/thoughts/integrator.py
□ state/ai/thoughts/*.jsonl

EXIT CRITERIA:
├── Hypotheses generated from patterns
├── Validation cycle: pending → validated/rejected
├── Telegram command /thoughts works
└── py_compile + ruff pass
```

### PHASE 4: TELEGRAM + DASHBOARD
```
□ ai_gateway/telegram/bot.py
□ ai_gateway/telegram/handlers/*
□ ai_gateway/feeds/news_aggregator.py
□ dashboard/app.py (FastAPI + WebSocket)
□ dashboard/static + templates

EXIT CRITERIA:
├── Bot responds to all commands
├── Dashboard shows live data
├── WebSocket updates < 100ms
├── Alerts arrive in Telegram < 1 sec
└── py_compile + ruff pass
```

### PHASE 5: PRODUCTION
```
□ Integration with HOPE ENGINE (run_live_v5.py)
□ TESTNET run 24h (all modes)
□ Full audit + documentation
□ Human approval for LIVE

EXIT CRITERIA:
├── 24h without errors on TESTNET
├── Win Rate > 60% on test data
├── All safety invariants PASS
└── Manual sign-off for LIVE deployment
```

---

## PART 6: SAFETY INVARIANTS (MANDATORY)

```python
# INVARIANT 1: No Trade Without All Checks
def execute_trade(decision: Decision) -> bool:
    if decision.action != Action.BUY:
        return False
    if not all(decision.checks_passed.values()):
        log.error(f"BLOCKED: {decision.signal_id}")
        return False
    return execute_order(decision)

# INVARIANT 2: Atomic File Operations
def atomic_write(path: Path, data: bytes) -> None:
    temp = path.with_suffix('.tmp')
    with open(temp, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)

# INVARIANT 3: Checksum Validation
def load_model(version: int, registry: ModelRegistry) -> Model:
    entry = registry.get(version)
    actual = compute_sha256(MODELS_DIR / entry.file)
    if actual != entry.checksum:
        raise ChecksumError(f"Model {version} corrupted")
    return joblib.load(MODELS_DIR / entry.file)

# INVARIANT 4: Fail-Closed
def check_regime(regime: Optional[Regime]) -> bool:
    if regime is None:
        return False  # Unknown = FAIL
    return regime in ALLOWED_REGIMES

# INVARIANT 5: Circuit Breaker Auto-Rollback
def record_outcome(self, outcome: Outcome) -> None:
    if outcome.label == Label.LOSS:
        self.consecutive_losses += 1
        if self.consecutive_losses >= self.mode_config.circuit_threshold:
            self.circuit_breaker.open()
            self.maybe_rollback()
    else:
        self.consecutive_losses = 0
```

---

## PART 7: TELEGRAM COMMANDS

### Control:
| Command | Description |
|---------|-------------|
| `/start` | Main menu |
| `/stop` | Emergency stop |
| `/start_trading` | Resume trading |
| `/mode [SS\|S\|SW\|ALL]` | Select mode |

### Monitoring:
| Command | Description |
|---------|-------------|
| `/status` | Current state |
| `/ai` | AI Dashboard |
| `/pnl` | P&L for period |
| `/circuit` | Circuit breaker status |
| `/model` | Model version and metrics |

### Analysis:
| Command | Description |
|---------|-------------|
| `/signal [SYMBOL]` | Recent signals |
| `/predict SYMBOL` | Request prediction |
| `/news` | Recent news |
| `/thoughts` | AI hypotheses |

### Admin:
| Command | Description |
|---------|-------------|
| `/retrain` | Force retraining |
| `/rollback` | Rollback model |
| `/settings` | Settings |

---

## PART 8: IMMEDIATE ACTIONS

### PRIORITY 1 (TODAY):
```
├── Collect 22+ MoonBot signals (78 → 100)
├── First model training
└── Start WebSocket feed for real-time prices
```

### PRIORITY 2 (TOMORROW):
```
├── mode_router.py — signal classification
├── orderbook_ws.py — order book for scalp
└── scalp/detector.py — scalp pattern detector
```

### PRIORITY 3 (THIS WEEK):
```
├── super_scalp/* — ultra-fast trading
├── thoughts/* — AI hypotheses
└── Telegram bot MVP
```

---

## PART 9: VERIFICATION

```bash
# Syntax check
python -m py_compile ai_gateway/core/*.py ai_gateway/feeds/*.py

# Integration tests
python -m scripts.test_ai_gateway

# Sources check
python -m scripts.sources_manager check

# Market intel update
python -m scripts.update_market_intel

# Signal count
python -c "import json; d=json.load(open('data/moonbot_signals/signals_20260129.jsonl')); print(len(d))"
```

---

## PART 10: CLAUDE CODE PROMPT

```markdown
# HOPE AI TASK

## CONTEXT
Project: HOPE AI Trading System v4.0
SSoT: docs/HOPE_AI_TRADING_TZ_v4.md
Sources: state/sources/sources.json

## PRINCIPLES
- Fail-closed: doubt = FAIL
- Atomic: temp → fsync → replace
- Contracts: sha256: prefix
- Execute NOW, no offers

## STATE
python -m scripts.sources_manager report
python -m scripts.test_ai_gateway

## TASK
[Specific task here]

## DELIVERABLES
1. Audit inputs (find hidden errors)
2. Working code (not "can do" but DONE)
3. Verification: py_compile + test
4. TASK COMPLETION summary
```

---

## CHECKSUM

```
Document: HOPE_AI_TRADING_TZ_v4.md
Version: 4.0
SHA256: [auto-computed on save]
```
