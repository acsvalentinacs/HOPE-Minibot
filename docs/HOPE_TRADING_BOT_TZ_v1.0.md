# HOPE Trading Bot - Техническое Задание v1.0

**Дата:** 2026-01-27
**Автор:** Claude (Opus 4.5) + DDO Trinity
**Статус:** READY FOR IMPLEMENTATION

---

## EXECUTIVE SUMMARY

HOPE Trading Bot — автоматическая торговая система для Binance Spot с интеграцией AI/ML для генерации сигналов, управления рисками и непрерывной оптимизации. Цель: **стабильная доходность 15-25%/месяц** при контролируемом риске (max drawdown 10%).

### Текущее состояние (что УЖЕ есть):
- ✅ Order Router (market/limit orders)
- ✅ Risk Engine (fail-closed, kill switch)
- ✅ Market Intelligence Pipeline
- ✅ Telegram Signal Publisher
- ✅ Outcome Tracking (MFE/MAE)
- ✅ Live Gates (MAINNET barrier)
- ✅ Micro Trading ($10 trades)

### Что ОТСУТСТВУЕТ (scope этого ТЗ):
- ❌ AI/ML Signal Generation (RSI, MACD, ML модели)
- ❌ Technical Analysis Integration
- ❌ Strategy Orchestrator (выбор стратегии)
- ❌ Backtesting Framework
- ❌ Performance Analytics Dashboard
- ❌ Auto-Optimization Loop

---

## ЧАСТЬ 1: АРХИТЕКТУРА

### 1.1 Общая схема

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         HOPE TRADING BOT v2.0                           │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐              │
│  │   MARKET     │    │   SIGNAL     │    │   STRATEGY   │              │
│  │   DATA       │───▶│   ENGINE     │───▶│  ORCHESTRATOR│              │
│  │   PIPELINE   │    │   (AI/ML)    │    │              │              │
│  └──────────────┘    └──────────────┘    └──────┬───────┘              │
│         │                   │                    │                      │
│         ▼                   ▼                    ▼                      │
│  ┌──────────────────────────────────────────────────────┐              │
│  │                    RISK ENGINE                        │              │
│  │  [Daily Loss] [Drawdown] [Position Size] [Kill Switch]│              │
│  └──────────────────────────┬───────────────────────────┘              │
│                             │                                           │
│                             ▼                                           │
│  ┌──────────────────────────────────────────────────────┐              │
│  │                   ORDER ROUTER                        │              │
│  │      [Binance API] [Signature] [Audit Trail]          │              │
│  └──────────────────────────┬───────────────────────────┘              │
│                             │                                           │
│                             ▼                                           │
│  ┌──────────────────────────────────────────────────────┐              │
│  │               OUTCOME TRACKER + OPTIMIZER             │              │
│  │   [MFE/MAE] [Win Rate] [Sharpe] [Auto-Tune Params]   │              │
│  └──────────────────────────────────────────────────────┘              │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Новые модули (создать)

| Модуль | Путь | Назначение |
|--------|------|------------|
| `signal_engine.py` | `core/ai/signal_engine.py` | AI генерация сигналов |
| `technical_indicators.py` | `core/ai/technical_indicators.py` | RSI, MACD, BB, ATR |
| `ml_predictor.py` | `core/ai/ml_predictor.py` | ML модель предсказаний |
| `strategy_orchestrator.py` | `core/strategy/orchestrator.py` | Выбор и переключение стратегий |
| `strategy_base.py` | `core/strategy/base.py` | Абстрактная стратегия |
| `strategy_momentum.py` | `core/strategy/momentum.py` | Momentum стратегия |
| `strategy_mean_revert.py` | `core/strategy/mean_revert.py` | Mean Reversion |
| `strategy_breakout.py` | `core/strategy/breakout.py` | Breakout стратегия |
| `backtester.py` | `core/backtest/backtester.py` | Бэктестинг движок |
| `optimizer.py` | `core/backtest/optimizer.py` | Оптимизация параметров |
| `performance_tracker.py` | `core/analytics/performance.py` | Метрики производительности |
| `auto_tuner.py` | `core/analytics/auto_tuner.py` | Автоподстройка параметров |

---

## ЧАСТЬ 2: SIGNAL ENGINE (AI/ML)

### 2.1 Требования

**Вход:**
- Binance ticker data (24h: price, volume, change%)
- OHLCV данные (1m, 5m, 15m, 1h, 4h candles)
- Order book depth (bids/asks)
- News sentiment (из event_classifier.py)

**Выход:**
```python
@dataclass
class TradingSignal:
    signal_id: str              # sha256:xxx
    timestamp: datetime
    symbol: str                 # BTCUSDT
    direction: Literal["LONG", "SHORT", "NEUTRAL"]
    strength: float             # 0.0-1.0
    confidence: float           # 0.0-1.0
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward_ratio: float
    timeframe: str              # 5m, 15m, 1h, 4h
    strategy_name: str          # momentum, mean_revert, breakout
    indicators: dict            # RSI, MACD, BB values
    reasoning: str              # Human-readable explanation
    expires_at: datetime        # Signal validity window
```

### 2.2 Technical Indicators Module

**Файл:** `core/ai/technical_indicators.py`

```python
class TechnicalIndicators:
    """
    Расчёт технических индикаторов.
    Использует numpy для производительности.
    """

    @staticmethod
    def rsi(closes: np.ndarray, period: int = 14) -> float:
        """
        Relative Strength Index.

        Интерпретация:
        - RSI > 70: Перекупленность (сигнал на SHORT)
        - RSI < 30: Перепроданность (сигнал на LONG)
        - RSI 40-60: Нейтральная зона

        Returns:
            RSI value 0-100
        """

    @staticmethod
    def macd(closes: np.ndarray,
             fast: int = 12,
             slow: int = 26,
             signal: int = 9) -> tuple[float, float, float]:
        """
        Moving Average Convergence Divergence.

        Returns:
            (macd_line, signal_line, histogram)

        Сигналы:
        - MACD crosses above signal: LONG
        - MACD crosses below signal: SHORT
        - Histogram divergence: Trend strength
        """

    @staticmethod
    def bollinger_bands(closes: np.ndarray,
                        period: int = 20,
                        std_dev: float = 2.0) -> tuple[float, float, float]:
        """
        Bollinger Bands.

        Returns:
            (upper_band, middle_band, lower_band)

        Сигналы:
        - Price touches lower band: Potential LONG (mean reversion)
        - Price touches upper band: Potential SHORT
        - Band squeeze: Breakout incoming
        """

    @staticmethod
    def atr(highs: np.ndarray,
            lows: np.ndarray,
            closes: np.ndarray,
            period: int = 14) -> float:
        """
        Average True Range - для расчёта stop-loss.

        Returns:
            ATR value (абсолютное значение, не %)

        Использование:
        - Stop Loss = Entry - (ATR * multiplier)
        - Position Size = Risk$ / ATR
        """

    @staticmethod
    def ema(closes: np.ndarray, period: int) -> float:
        """Exponential Moving Average."""

    @staticmethod
    def sma(closes: np.ndarray, period: int) -> float:
        """Simple Moving Average."""

    @staticmethod
    def volume_profile(volumes: np.ndarray, period: int = 20) -> dict:
        """
        Volume analysis.

        Returns:
            {
                "avg_volume": float,
                "volume_trend": "increasing"|"decreasing"|"stable",
                "volume_spike": bool,  # > 2x average
            }
        """
```

### 2.3 ML Predictor Module

**Файл:** `core/ai/ml_predictor.py`

**Модель:** LightGBM (легковесный, быстрый, не требует GPU)

```python
class MLPredictor:
    """
    Machine Learning модуль для предсказания направления цены.

    Features (входные признаки):
    - RSI (14)
    - MACD histogram
    - BB position (0-1, где 0 = lower, 1 = upper)
    - Volume ratio (current / avg)
    - Price change % (1h, 4h, 24h)
    - ATR normalized
    - Hour of day (cyclical encoding)
    - Day of week (cyclical encoding)

    Target:
    - 1 = Price up > 0.5% in next 4h
    - 0 = Price down > 0.5% in next 4h
    - Exclude: Price change < 0.5% (noise)

    Model: LightGBM Classifier
    - n_estimators: 100
    - max_depth: 6
    - learning_rate: 0.1
    - min_child_samples: 20
    """

    MODEL_PATH = Path("models/price_predictor.lgb")
    FEATURE_NAMES = [
        "rsi_14", "macd_hist", "bb_position", "volume_ratio",
        "price_change_1h", "price_change_4h", "price_change_24h",
        "atr_normalized", "hour_sin", "hour_cos", "dow_sin", "dow_cos"
    ]

    def __init__(self):
        self.model = None
        self._load_model()

    def predict(self, features: dict) -> tuple[float, float]:
        """
        Predict price direction.

        Returns:
            (probability_up, probability_down)
        """

    def retrain(self,
                X: pd.DataFrame,
                y: pd.Series,
                validation_split: float = 0.2) -> dict:
        """
        Retrain model on new data.

        Returns:
            {
                "accuracy": float,
                "precision": float,
                "recall": float,
                "f1": float,
                "auc_roc": float,
            }
        """
```

### 2.4 Signal Engine

**Файл:** `core/ai/signal_engine.py`

```python
class SignalEngine:
    """
    Главный движок генерации сигналов.

    Комбинирует:
    1. Technical Analysis (RSI, MACD, BB)
    2. ML Prediction (LightGBM)
    3. News Sentiment (из event_classifier)
    4. Volume Analysis

    Веса:
    - Technical: 40%
    - ML: 35%
    - Sentiment: 15%
    - Volume: 10%
    """

    SIGNAL_THRESHOLD = 0.65  # Минимальная сила для генерации сигнала
    CONFIDENCE_THRESHOLD = 0.55  # Минимальная уверенность

    def __init__(self,
                 binance_client: BinanceSpotClient,
                 event_classifier: EventClassifier):
        self.indicators = TechnicalIndicators()
        self.ml = MLPredictor()
        self.binance = binance_client
        self.classifier = event_classifier

    async def generate_signal(self, symbol: str) -> Optional[TradingSignal]:
        """
        Generate trading signal for a symbol.

        Steps:
        1. Fetch OHLCV data (multiple timeframes)
        2. Calculate technical indicators
        3. Get ML prediction
        4. Check news sentiment
        5. Analyze volume
        6. Combine scores with weights
        7. Generate signal if threshold met

        Returns:
            TradingSignal or None if no signal
        """

    async def scan_market(self,
                          symbols: list[str] = None) -> list[TradingSignal]:
        """
        Scan all USDT pairs and return signals.

        Default: Top 50 by volume

        Returns:
            List of signals sorted by strength (descending)
        """

    def _calculate_entry_exit(self,
                              symbol: str,
                              direction: str,
                              current_price: float,
                              atr: float) -> tuple[float, float, float]:
        """
        Calculate entry, stop-loss, take-profit.

        Logic:
        - Entry: current_price (market order)
        - Stop Loss: entry ± (ATR * 1.5)
        - Take Profit: entry ± (ATR * 3.0) = 2:1 R:R

        Returns:
            (entry, stop_loss, take_profit)
        """
```

---

## ЧАСТЬ 3: STRATEGY ORCHESTRATOR

### 3.1 Базовый класс стратегии

**Файл:** `core/strategy/base.py`

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

class StrategyState(Enum):
    IDLE = "idle"              # Ожидание сигнала
    ENTRY_PENDING = "entry"    # Ожидание входа
    IN_POSITION = "position"   # В позиции
    EXIT_PENDING = "exit"      # Ожидание выхода
    COOLDOWN = "cooldown"      # Пауза после убытка

@dataclass
class StrategyConfig:
    name: str
    enabled: bool = True
    max_positions: int = 3
    position_size_pct: float = 5.0  # % от equity
    max_daily_trades: int = 10
    cooldown_after_loss_sec: int = 3600  # 1 hour
    allowed_symbols: list[str] = None  # None = all
    timeframe: str = "15m"

class BaseStrategy(ABC):
    """
    Abstract base class for trading strategies.

    Все стратегии ОБЯЗАНЫ:
    1. Реализовать should_enter() и should_exit()
    2. Возвращать TradingSignal с обоснованием
    3. Логировать все решения
    4. Быть fail-closed (при ошибке = не торговать)
    """

    def __init__(self, config: StrategyConfig):
        self.config = config
        self.state = StrategyState.IDLE
        self.positions: list[Position] = []
        self.daily_trades = 0
        self.last_trade_time: Optional[datetime] = None

    @abstractmethod
    async def should_enter(self,
                           symbol: str,
                           market_data: MarketData) -> Optional[TradingSignal]:
        """
        Determine if we should enter a position.

        Returns:
            TradingSignal if entry conditions met, None otherwise
        """

    @abstractmethod
    async def should_exit(self,
                          position: Position,
                          market_data: MarketData) -> Optional[ExitSignal]:
        """
        Determine if we should exit a position.

        Returns:
            ExitSignal if exit conditions met, None otherwise
        """

    def can_trade(self) -> tuple[bool, str]:
        """
        Check if strategy can make a trade.

        Returns:
            (can_trade, reason)
        """
        if not self.config.enabled:
            return False, "Strategy disabled"
        if self.daily_trades >= self.config.max_daily_trades:
            return False, "Daily trade limit reached"
        if len(self.positions) >= self.config.max_positions:
            return False, "Max positions reached"
        if self.state == StrategyState.COOLDOWN:
            return False, "In cooldown period"
        return True, "OK"
```

### 3.2 Momentum Strategy

**Файл:** `core/strategy/momentum.py`

```python
class MomentumStrategy(BaseStrategy):
    """
    Momentum/Trend Following Strategy.

    Логика входа:
    1. RSI выходит из перепроданности (< 30 → > 35) = LONG
    2. RSI выходит из перекупленности (> 70 → < 65) = SHORT
    3. MACD пересечение подтверждает направление
    4. Volume выше среднего (подтверждение)

    Логика выхода:
    1. Take Profit: 2:1 R:R достигнут
    2. Stop Loss: ATR * 1.5 пробит
    3. RSI разворот (> 70 для LONG, < 30 для SHORT)
    4. Trailing stop при прибыли > 1%

    Лучшие условия:
    - Trending market (ADX > 25)
    - Не перед важными новостями
    - Высокий volume
    """

    # Параметры (оптимизируемые)
    RSI_OVERSOLD = 30
    RSI_OVERBOUGHT = 70
    RSI_EXIT_BUFFER = 5
    VOLUME_MULTIPLIER = 1.5
    ATR_STOP_MULTIPLIER = 1.5
    ATR_TP_MULTIPLIER = 3.0
    TRAILING_STOP_TRIGGER_PCT = 1.0
    TRAILING_STOP_DISTANCE_PCT = 0.5
```

### 3.3 Mean Reversion Strategy

**Файл:** `core/strategy/mean_revert.py`

```python
class MeanReversionStrategy(BaseStrategy):
    """
    Mean Reversion Strategy.

    Логика входа:
    1. Цена касается нижней Bollinger Band = LONG
    2. Цена касается верхней Bollinger Band = SHORT
    3. RSI подтверждает (< 30 для LONG, > 70 для SHORT)
    4. НЕТ сильного тренда (ADX < 20)

    Логика выхода:
    1. Цена возвращается к средней BB
    2. Stop Loss: за пределами BB + ATR
    3. Таймаут: 4 часа без движения к средней

    Лучшие условия:
    - Ranging/Sideways market
    - Низкая волатильность
    - Ночное время (меньше новостей)
    """

    BB_PERIOD = 20
    BB_STD_DEV = 2.0
    MAX_ADX_FOR_ENTRY = 20  # Не входить в тренд
    TIMEOUT_HOURS = 4
```

### 3.4 Breakout Strategy

**Файл:** `core/strategy/breakout.py`

```python
class BreakoutStrategy(BaseStrategy):
    """
    Breakout Strategy.

    Логика входа:
    1. Bollinger Bands сжимаются (squeeze)
    2. Цена пробивает верхнюю BB с volume spike = LONG
    3. Цена пробивает нижнюю BB с volume spike = SHORT
    4. Подтверждение: закрытие свечи за пределами BB

    Логика выхода:
    1. Take Profit: предыдущий swing high/low
    2. Stop Loss: внутри BB (ложный пробой)
    3. Trailing stop после подтверждения пробоя

    Лучшие условия:
    - После консолидации (низкий ATR)
    - High volume на пробое
    - Перед/после важных новостей
    """

    SQUEEZE_THRESHOLD = 0.02  # BB width < 2% = squeeze
    VOLUME_SPIKE_MULTIPLIER = 2.0
    CONFIRMATION_CANDLES = 2  # Ждём 2 свечи за BB
```

### 3.5 Strategy Orchestrator

**Файл:** `core/strategy/orchestrator.py`

```python
class StrategyOrchestrator:
    """
    Оркестратор стратегий.

    Функции:
    1. Выбор активной стратегии на основе рыночных условий
    2. Распределение капитала между стратегиями
    3. Координация сигналов (избежание конфликтов)
    4. Отслеживание производительности каждой стратегии
    5. Динамическое включение/выключение стратегий

    Market Regime Detection:
    - TRENDING: ADX > 25, использовать Momentum
    - RANGING: ADX < 20, использовать Mean Reversion
    - VOLATILE: ATR spike, использовать Breakout
    - UNCERTAIN: снизить размеры позиций
    """

    def __init__(self, strategies: list[BaseStrategy]):
        self.strategies = {s.config.name: s for s in strategies}
        self.active_strategy: Optional[str] = None
        self.market_regime: str = "UNCERTAIN"

    async def detect_market_regime(self,
                                    market_data: MarketData) -> str:
        """
        Detect current market regime.

        Returns:
            "TRENDING" | "RANGING" | "VOLATILE" | "UNCERTAIN"
        """

    async def select_strategy(self) -> BaseStrategy:
        """
        Select best strategy for current market conditions.
        """

    async def run_cycle(self) -> list[TradingSignal]:
        """
        Run one trading cycle:
        1. Detect market regime
        2. Select strategy
        3. Scan for entry signals
        4. Check exits for open positions
        5. Return actionable signals
        """
```

---

## ЧАСТЬ 4: BACKTESTING FRAMEWORK

### 4.1 Backtester

**Файл:** `core/backtest/backtester.py`

```python
@dataclass
class BacktestConfig:
    start_date: datetime
    end_date: datetime
    initial_capital: float = 10000.0
    commission_pct: float = 0.1  # Binance 0.1%
    slippage_pct: float = 0.05   # Realistic slippage
    symbols: list[str] = None    # None = all USDT pairs
    timeframe: str = "15m"

@dataclass
class BacktestResult:
    total_return_pct: float
    annual_return_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    win_rate: float
    profit_factor: float
    total_trades: int
    avg_trade_duration: timedelta
    best_trade_pct: float
    worst_trade_pct: float
    equity_curve: pd.Series
    trades: pd.DataFrame

class Backtester:
    """
    Historical backtesting engine.

    Features:
    - Realistic execution (commission, slippage)
    - Multiple timeframes
    - Walk-forward optimization
    - Monte Carlo simulation
    - Out-of-sample testing
    """

    def __init__(self, config: BacktestConfig):
        self.config = config
        self.data_cache: dict[str, pd.DataFrame] = {}

    async def load_historical_data(self) -> None:
        """
        Load OHLCV data from Binance.
        Cache locally for speed.
        """

    def run(self, strategy: BaseStrategy) -> BacktestResult:
        """
        Run backtest.

        Steps:
        1. Initialize portfolio
        2. For each candle:
           a. Update market data
           b. Check exits for open positions
           c. Generate new signals
           d. Execute trades (with slippage)
           e. Update equity curve
        3. Calculate metrics
        4. Return results
        """

    def walk_forward(self,
                     strategy: BaseStrategy,
                     train_window: int = 60,  # days
                     test_window: int = 30,   # days
                     step: int = 30) -> list[BacktestResult]:
        """
        Walk-forward optimization.

        1. Train on train_window
        2. Test on test_window
        3. Step forward
        4. Repeat

        Returns:
            List of out-of-sample results
        """

    def monte_carlo(self,
                    trades: pd.DataFrame,
                    simulations: int = 1000) -> dict:
        """
        Monte Carlo simulation.

        Shuffle trade order to estimate:
        - Confidence intervals for returns
        - Probability of ruin
        - Expected drawdown distribution
        """
```

### 4.2 Optimizer

**Файл:** `core/backtest/optimizer.py`

```python
class ParameterOptimizer:
    """
    Оптимизация параметров стратегии.

    Методы:
    1. Grid Search (exhaustive)
    2. Random Search (faster)
    3. Bayesian Optimization (smart)

    Objective: Maximize Sharpe Ratio
    Constraints: Max Drawdown < 15%
    """

    def __init__(self,
                 backtester: Backtester,
                 strategy_class: type[BaseStrategy]):
        self.backtester = backtester
        self.strategy_class = strategy_class

    def optimize(self,
                 param_space: dict,
                 method: str = "bayesian",
                 max_iterations: int = 100) -> dict:
        """
        Find optimal parameters.

        Args:
            param_space: {
                "RSI_OVERSOLD": (20, 40),
                "RSI_OVERBOUGHT": (60, 80),
                "ATR_MULTIPLIER": (1.0, 3.0),
            }

        Returns:
            {
                "best_params": {...},
                "best_sharpe": float,
                "best_result": BacktestResult,
                "all_trials": pd.DataFrame,
            }
        """
```

---

## ЧАСТЬ 5: AUTO-OPTIMIZATION LOOP

### 5.1 Performance Tracker

**Файл:** `core/analytics/performance.py`

```python
class PerformanceTracker:
    """
    Отслеживание производительности в реальном времени.

    Метрики (rolling windows):
    - Return: 1h, 24h, 7d, 30d
    - Sharpe Ratio: 7d, 30d
    - Win Rate: последние 20 сделок
    - Avg R:R: последние 20 сделок
    - Max Drawdown: текущий и исторический
    """

    STATE_FILE = Path("state/performance_metrics.json")

    def update(self, trade: CompletedTrade) -> None:
        """Update metrics after trade completion."""

    def get_dashboard(self) -> dict:
        """
        Get performance dashboard.

        Returns:
            {
                "equity": float,
                "equity_peak": float,
                "current_drawdown_pct": float,
                "returns": {
                    "1h": float,
                    "24h": float,
                    "7d": float,
                    "30d": float,
                },
                "sharpe_7d": float,
                "sharpe_30d": float,
                "win_rate_20": float,
                "avg_rr_20": float,
                "total_trades": int,
                "strategies": {
                    "momentum": {"trades": int, "pnl": float, "win_rate": float},
                    ...
                }
            }
        """

    def should_reduce_risk(self) -> tuple[bool, str]:
        """
        Determine if we should reduce risk.

        Triggers:
        - Drawdown > 5%: Reduce position size by 50%
        - Drawdown > 8%: Reduce to minimum sizes
        - Drawdown > 10%: Kill switch
        - Win rate < 40% last 20: Reduce size
        - Sharpe < 0.5 last 7d: Review strategy
        """
```

### 5.2 Auto Tuner

**Файл:** `core/analytics/auto_tuner.py`

```python
class AutoTuner:
    """
    Автоматическая подстройка параметров.

    Цикл:
    1. Каждые 24h: анализ производительности
    2. Если Sharpe < target: запуск оптимизации
    3. Бэктест новых параметров на последних 30 днях
    4. Если улучшение > 10%: применить параметры
    5. Логирование всех изменений

    Защита:
    - Не менять параметры чаще 1 раза в 24h
    - Минимальный период наблюдения: 20 сделок
    - Rollback если новые параметры хуже
    """

    MIN_TRADES_FOR_EVAL = 20
    MIN_HOURS_BETWEEN_UPDATES = 24
    IMPROVEMENT_THRESHOLD = 0.10  # 10%

    async def run_daily_check(self) -> Optional[dict]:
        """
        Daily optimization check.

        Returns:
            New parameters if update needed, None otherwise
        """

    async def apply_new_params(self,
                                strategy: BaseStrategy,
                                new_params: dict) -> bool:
        """
        Apply new parameters with rollback capability.
        """
```

---

## ЧАСТЬ 6: ИНТЕГРАЦИЯ С OMNI-CHAT DDO

### 6.1 DDO Trading Discussion Templates

Добавить в `omnichat/src/ddo/templates.py`:

```python
TRADING_ANALYSIS_TEMPLATE = DiscussionTemplate(
    mode=DiscussionMode.TRADING_ANALYSIS,
    name="Trading Analysis",
    description=(
        "Analyze trading opportunity. "
        "Gemini: risk assessment, GPT: technical analysis, "
        "Claude: entry/exit calculation."
    ),
    phases=[
        PhaseConfig(
            phase=DiscussionPhase.ANALYZE,
            agent="gpt",
            prompt_key="trading_technical",
            required=True,
        ),
        PhaseConfig(
            phase=DiscussionPhase.SECURITY_REVIEW,
            agent="gemini",
            prompt_key="trading_risk",
            required=True,
        ),
        PhaseConfig(
            phase=DiscussionPhase.IMPLEMENT,
            agent="claude",
            prompt_key="trading_execution",
            required=True,
        ),
    ],
    synthesizer_agent="gpt",
    require_consensus=True,
)
```

### 6.2 Trading Prompts

Добавить в `omnichat/src/ddo/roles.py`:

```python
PROMPTS[DiscussionPhase.TRADING_TECHNICAL] = """
## 📊 TRADING TECHNICAL ANALYSIS

### Данные
**Символ:** {symbol}
**Текущая цена:** {current_price}
**24h изменение:** {change_24h}%
**Volume:** {volume_24h}

### Индикаторы
{indicators_json}

### Задача
Проанализируй технические индикаторы и определи:
1. Текущий тренд (UP/DOWN/SIDEWAYS)
2. Ключевые уровни поддержки/сопротивления
3. Потенциальные точки входа
4. Риски

### Формат ответа
```json
{
  "trend": "UP|DOWN|SIDEWAYS",
  "support_levels": [price1, price2],
  "resistance_levels": [price1, price2],
  "entry_zones": [{"price": x, "reason": "..."}],
  "risks": ["risk1", "risk2"],
  "recommendation": "BUY|SELL|WAIT",
  "confidence": 0.0-1.0
}
```
"""
```

---

## ЧАСТЬ 7: ПЛАН РЕАЛИЗАЦИИ

### Фаза 1: Foundation (3-5 дней)

| # | Задача | Файлы | Приоритет |
|---|--------|-------|-----------|
| 1.1 | Technical Indicators | `core/ai/technical_indicators.py` | CRITICAL |
| 1.2 | Signal Engine Base | `core/ai/signal_engine.py` | CRITICAL |
| 1.3 | Strategy Base Class | `core/strategy/base.py` | CRITICAL |
| 1.4 | Momentum Strategy | `core/strategy/momentum.py` | HIGH |
| 1.5 | Unit Tests | `tests/test_indicators.py` | HIGH |

### Фаза 2: Strategies (3-5 дней)

| # | Задача | Файлы | Приоритет |
|---|--------|-------|-----------|
| 2.1 | Mean Reversion Strategy | `core/strategy/mean_revert.py` | HIGH |
| 2.2 | Breakout Strategy | `core/strategy/breakout.py` | HIGH |
| 2.3 | Strategy Orchestrator | `core/strategy/orchestrator.py` | CRITICAL |
| 2.4 | Market Regime Detection | `core/strategy/regime.py` | HIGH |
| 2.5 | Integration Tests | `tests/test_strategies.py` | HIGH |

### Фаза 3: Backtesting (3-5 дней)

| # | Задача | Файлы | Приоритет |
|---|--------|-------|-----------|
| 3.1 | Backtester Core | `core/backtest/backtester.py` | CRITICAL |
| 3.2 | Data Loader | `core/backtest/data_loader.py` | HIGH |
| 3.3 | Parameter Optimizer | `core/backtest/optimizer.py` | HIGH |
| 3.4 | Walk-Forward | `core/backtest/walk_forward.py` | MEDIUM |
| 3.5 | Monte Carlo | `core/backtest/monte_carlo.py` | MEDIUM |

### Фаза 4: ML Integration (5-7 дней)

| # | Задача | Файлы | Приоритет |
|---|--------|-------|-----------|
| 4.1 | Feature Engineering | `core/ai/features.py` | CRITICAL |
| 4.2 | ML Predictor | `core/ai/ml_predictor.py` | CRITICAL |
| 4.3 | Training Pipeline | `core/ai/training.py` | HIGH |
| 4.4 | Model Evaluation | `core/ai/evaluation.py` | HIGH |
| 4.5 | Integration with SignalEngine | Update `signal_engine.py` | CRITICAL |

### Фаза 5: Auto-Optimization (3-5 дней)

| # | Задача | Файлы | Приоритет |
|---|--------|-------|-----------|
| 5.1 | Performance Tracker | `core/analytics/performance.py` | CRITICAL |
| 5.2 | Auto Tuner | `core/analytics/auto_tuner.py` | HIGH |
| 5.3 | Risk Adjuster | `core/analytics/risk_adjuster.py` | HIGH |
| 5.4 | Dashboard Export | `core/analytics/dashboard.py` | MEDIUM |

### Фаза 6: Production (5-7 дней)

| # | Задача | Файлы | Приоритет |
|---|--------|-------|-----------|
| 6.1 | Live Trading Loop | `run_auto_trading.py` | CRITICAL |
| 6.2 | DDO Integration | Update DDO templates | HIGH |
| 6.3 | Telegram Alerts | Update `telegram_signals.py` | HIGH |
| 6.4 | Monitoring Dashboard | `tools/dashboard.py` | MEDIUM |
| 6.5 | Documentation | `docs/TRADING_GUIDE.md` | HIGH |

---

## ЧАСТЬ 8: РИСКИ И МИТИГАЦИЯ

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| Overfitting ML модели | HIGH | CRITICAL | Walk-forward validation, regularization |
| API Rate Limits | MEDIUM | HIGH | Request throttling, caching |
| Slippage на волатильности | HIGH | MEDIUM | Limit orders, wider stops |
| Ложные сигналы | HIGH | MEDIUM | Multiple confirmations, filters |
| Drawdown > 10% | MEDIUM | CRITICAL | Kill switch, position sizing |
| Exchange downtime | LOW | HIGH | Graceful degradation, alerts |

---

## ЧАСТЬ 9: МЕТРИКИ УСПЕХА

### KPI (Key Performance Indicators)

| Метрика | Target | Minimum | Action if Below |
|---------|--------|---------|-----------------|
| Monthly Return | 25% | 10% | Review strategy |
| Sharpe Ratio | 2.0 | 1.0 | Optimize params |
| Max Drawdown | 10% | 15% | Kill switch |
| Win Rate | 55% | 45% | Review entry criteria |
| Profit Factor | 2.0 | 1.3 | Review R:R |
| Avg Trade Duration | 4h | 24h | Review timeframe |

---

## ЧАСТЬ 10: ЗАВИСИМОСТИ

### Новые пакеты (добавить в requirements.txt):

```
lightgbm>=4.0.0          # ML predictor
scikit-learn>=1.3.0      # Metrics, preprocessing
optuna>=3.4.0            # Bayesian optimization
ta-lib>=0.4.28           # Technical analysis (optional, numpy fallback)
pandas>=2.0.0            # Data manipulation
numpy>=1.24.0            # Numerical operations
```

---

## ВОПРОСЫ ДЛЯ УТОЧНЕНИЯ

> **ВНИМАНИЕ:** Эти вопросы НЕ блокируют реализацию.
> Если ответа нет — используются значения по умолчанию.

1. **Начальный капитал:** $1000? $5000? $10000?
   - *Default: $1000*

2. **Максимальный риск на сделку:** 1%? 2%? 5%?
   - *Default: 2%*

3. **Предпочтительные пары:** Только BTC/ETH? Топ-20? Все USDT?
   - *Default: Топ-20 по объёму*

4. **Timeframe:** 5m (агрессивный)? 15m (balanced)? 1h (консервативный)?
   - *Default: 15m*

5. **Режим запуска:** 24/7? Только определённые часы?
   - *Default: 24/7*

---

## ЗАКЛЮЧЕНИЕ

Данное ТЗ описывает полную систему автоматической торговли с:

1. **AI Signal Generation** — технический анализ + ML предсказания
2. **Multi-Strategy Orchestration** — адаптация к рыночным условиям
3. **Robust Backtesting** — валидация перед продакшеном
4. **Auto-Optimization** — непрерывное улучшение
5. **Fail-Closed Safety** — защита капитала превыше всего

**Ожидаемые результаты:**
- Время разработки: 20-30 рабочих дней
- Тестирование на TESTNET: 7 дней
- Запуск на MAINNET: после успешного TESTNET периода

---

*Документ подготовлен: Claude (Opus 4.5) + DDO Trinity*
*Дата: 2026-01-27*
*Версия: 1.0*
