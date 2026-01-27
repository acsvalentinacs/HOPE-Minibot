"""
HOPE/NORE Signals Pipeline v1.0

Unified pipeline: fetch → classify → journal → publish.
Integrates MarketIntel, EventClassifier, EventJournal, SignalPublisher.

Fail-closed design:
- Stale data = DEGRADED mode (no signals, only status)
- API error = log + skip cycle
- Publish error = deadletter + retry next cycle

Usage:
    from core.signals_pipeline import SignalsPipeline

    pipeline = SignalsPipeline()
    result = pipeline.run_cycle()

    # Or integrate with runner
    pipeline.run_loop(interval_sec=300)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional, Dict, Any

from core.event_contract import Event, create_event, EventType, normalize_classified_event, filter_high_impact
from core.event_journal import EventJournal, get_event_journal
from core.event_classifier import EventClassifier
from core.market_intel import MarketIntel, get_market_intel, TradingSignal
from core.telegram_signals import SignalPublisher, get_signal_publisher, PublishResult
from core.signal_outcomes import OutcomeTracker, TrackedSignal, get_outcome_tracker
from core.strategy_integration import StrategyIntegration, get_strategy_integration, IntegrationConfig

logger = logging.getLogger(__name__)

# Pipeline configuration
STATE_DIR = Path(r"C:\Users\kirillDev\Desktop\TradingBot\minibot\state")
STOP_FLAG = Path(r"C:\Users\kirillDev\Desktop\TradingBot\flags\STOP.flag")

# Thresholds
HIGH_IMPACT_THRESHOLD = 0.6
STALE_DATA_THRESHOLD_SEC = 600  # 10 minutes = stale
MIN_TICKERS_FOR_VALID = 10  # Minimum tickers to consider data valid


class PipelineStatus(str, Enum):
    """Pipeline execution status."""
    OK = "ok"                    # All systems operational
    DEGRADED = "degraded"       # Partial data, limited signals
    ERROR = "error"             # Critical failure
    STOPPED = "stopped"         # STOP.flag active


@dataclass
class CycleResult:
    """Result of a single pipeline cycle."""
    status: PipelineStatus
    events_collected: int
    events_published: int
    signals_generated: int
    high_impact_news: int
    errors: List[str]
    duration_sec: float
    timestamp: float


class SignalsPipeline:
    """
    End-to-end signals pipeline with fail-closed design.

    Components:
    - MarketIntel: data fetching
    - EventClassifier: news classification
    - EventJournal: atomic storage
    - SignalPublisher: Telegram delivery
    - OutcomeTracker: signal performance tracking (v1.1)
    """

    def __init__(
        self,
        market_intel: Optional[MarketIntel] = None,
        journal: Optional[EventJournal] = None,
        publisher: Optional[SignalPublisher] = None,
        outcome_tracker: Optional[OutcomeTracker] = None,
        strategy_integration: Optional[StrategyIntegration] = None,
        use_strategy_orchestrator: bool = True,  # Phase 2.5: use new orchestrator
    ):
        self._intel = market_intel or get_market_intel()
        self._journal = journal or get_event_journal()
        self._publisher = publisher or get_signal_publisher()
        self._outcome_tracker = outcome_tracker or get_outcome_tracker()
        self._classifier = EventClassifier()

        # Phase 2.5: Strategy orchestrator integration (Spot-only)
        self._use_orchestrator = use_strategy_orchestrator
        if self._use_orchestrator:
            self._strategy = strategy_integration or get_strategy_integration()
        else:
            self._strategy = None

        self._last_cycle_result: Optional[CycleResult] = None
        self._consecutive_errors = 0

    def _check_stop_flag(self) -> bool:
        """Check if STOP.flag is active."""
        return STOP_FLAG.exists()

    def _is_data_valid(self, snapshot: Any) -> bool:
        """
        Validate market snapshot data.

        Fail-closed: any doubt = invalid.
        """
        if snapshot is None:
            return False

        if getattr(snapshot, 'is_stale', True):
            logger.warning("Market data is stale")
            return False

        tickers = getattr(snapshot, 'tickers', {})
        if len(tickers) < MIN_TICKERS_FOR_VALID:
            logger.warning("Insufficient tickers: %d < %d", len(tickers), MIN_TICKERS_FOR_VALID)
            return False

        # Check for critical tickers
        if "BTCUSDT" not in tickers:
            logger.warning("Missing critical ticker: BTCUSDT")
            return False

        return True

    def _collect_events(self, snapshot: Any) -> List[Event]:
        """
        Collect and classify events from market snapshot.

        Returns list of Event objects in unified contract format.
        """
        events: List[Event] = []

        # Classify news
        for news in getattr(snapshot, 'news', []):
            try:
                classified = self._classifier.classify(
                    title=getattr(news, 'title', ''),
                    source=getattr(news, 'source', 'unknown'),
                    link=getattr(news, 'link', ''),
                    pub_date=getattr(news, 'pub_date', ''),
                )
                event = normalize_classified_event(classified)
                events.append(event)
            except Exception as e:
                logger.warning("Failed to classify news: %s", e)

        return events

    def _create_signal_events(self, signals: List[TradingSignal]) -> List[Event]:
        """Convert trading signals to Event format."""
        events: List[Event] = []

        for sig in signals:
            try:
                event = create_event(
                    event_type=EventType.SIGNAL,
                    title=f"{sig.symbol} {sig.direction.upper()} ({sig.signal_type})",
                    source="hope_signals",
                    impact_score=getattr(sig, 'strength', 0.5),
                    sentiment="bullish" if sig.direction == "long" else "bearish",
                    assets=[sig.symbol.replace("USDT", "")],
                    keywords=[sig.signal_type],
                )
                events.append(event)
            except Exception as e:
                logger.warning("Failed to create signal event: %s", e)

        return events

    def _record_signals_for_tracking(self, signals: List[TradingSignal], signal_events: List[Event]) -> int:
        """
        Record signals for outcome tracking.

        Matches TradingSignal with Event to get sha256 event_id.
        Returns count of signals recorded.
        """
        recorded = 0

        for sig, event in zip(signals, signal_events):
            if sig.entry_price <= 0:
                logger.warning("Signal %s has no entry_price, skipping tracking", sig.symbol)
                continue

            tracked = TrackedSignal(
                signal_id=event.event_id,
                symbol=sig.symbol,
                direction=sig.direction,
                entry_price=sig.entry_price,
                entry_ts=sig.timestamp,
                invalidation_price=sig.invalidation_price,
            )

            try:
                self._outcome_tracker.record_signal(tracked)
                recorded += 1
            except Exception as e:
                logger.warning("Failed to record signal for tracking: %s", e)

        return recorded

    def _record_price_samples(self, snapshot: Any) -> bool:
        """
        Record price samples from snapshot for outcome tracking.

        Called every cycle to build price history.
        """
        tickers = getattr(snapshot, 'tickers', {})
        if not tickers:
            return False

        prices = {
            symbol: ticker.price
            for symbol, ticker in tickers.items()
            if hasattr(ticker, 'price') and ticker.price > 0
        }

        if not prices:
            return False

        try:
            self._outcome_tracker.record_price_samples(time.time(), prices)
            logger.debug("Recorded %d price samples", len(prices))
            return True
        except Exception as e:
            logger.warning("Failed to record price samples: %s", e)
            return False

    def _update_signal_outcomes(self) -> int:
        """
        Update signal outcomes for completed horizons.

        Returns count of outcomes computed.
        """
        try:
            outcomes = self._outcome_tracker.update_outcomes(time.time())
            if outcomes:
                logger.info("Computed %d signal outcomes", len(outcomes))
            return len(outcomes)
        except Exception as e:
            logger.warning("Failed to update outcomes: %s", e)
            return 0

    def _publish_to_telegram(
        self,
        snapshot: Any,
        signals: List[Any],
        high_impact_events: List[Event],
    ) -> List[PublishResult]:
        """
        Publish to Telegram channel.

        Returns list of publish results.
        """
        results: List[PublishResult] = []

        try:
            # Combined update (snapshot + signals + news)
            tickers = getattr(snapshot, 'tickers', {})
            fear_greed = getattr(snapshot, 'fear_greed_index', 50)

            # Convert Event objects back to format publisher expects
            # (publisher uses ClassifiedEvent-like objects)
            publish_results = self._publisher.publish_combined_update(
                tickers=tickers,
                fear_greed=fear_greed,
                signals=signals,
                events=high_impact_events,
            )
            results.extend(publish_results)

        except Exception as e:
            logger.error("Telegram publish failed: %s", e)
            results.append(PublishResult(success=False, error=str(e)))

        return results

    def run_cycle(self) -> CycleResult:
        """
        Run single pipeline cycle.

        Returns CycleResult with status and metrics.
        """
        start_time = time.time()
        errors: List[str] = []
        events_collected = 0
        events_published = 0
        signals_count = 0
        high_impact_count = 0

        # Check STOP flag
        if self._check_stop_flag():
            logger.info("Pipeline stopped by STOP.flag")
            return CycleResult(
                status=PipelineStatus.STOPPED,
                events_collected=0,
                events_published=0,
                signals_generated=0,
                high_impact_news=0,
                errors=[],
                duration_sec=time.time() - start_time,
                timestamp=time.time(),
            )

        try:
            # 1. Fetch market data
            logger.info("Fetching market data...")
            snapshot = self._intel.get_snapshot(force_refresh=True)

            # 2. Validate data (fail-closed)
            if not self._is_data_valid(snapshot):
                errors.append("Invalid or stale market data")
                self._consecutive_errors += 1

                return CycleResult(
                    status=PipelineStatus.DEGRADED,
                    events_collected=0,
                    events_published=0,
                    signals_generated=0,
                    high_impact_news=0,
                    errors=errors,
                    duration_sec=time.time() - start_time,
                    timestamp=time.time(),
                )

            # 3. Generate trading signals
            logger.info("Generating signals...")
            if self._use_orchestrator and self._strategy:
                # Phase 2.5: Use StrategyOrchestrator (Spot-only enforced)
                signals = self._strategy.generate_signals(snapshot)
                logger.info("Generated %d signals via StrategyOrchestrator", len(signals))
            else:
                # Legacy: Use MarketIntel signals
                signals = self._intel.get_trading_signals()
            signals_count = len(signals)
            logger.info("Generated %d signals total", signals_count)

            # 4. Collect and classify events
            logger.info("Classifying events...")
            all_events = self._collect_events(snapshot)
            signal_events = self._create_signal_events(signals)
            all_events.extend(signal_events)
            events_collected = len(all_events)

            # 5. Store in journal
            logger.info("Storing %d events in journal...", events_collected)
            stored = self._journal.append_batch(all_events)
            logger.info("Stored %d new events (skipped %d duplicates)", stored, events_collected - stored)

            # 5.1 Record price samples for outcome tracking (every cycle)
            self._record_price_samples(snapshot)

            # 5.2 Record signals for outcome tracking
            if signals and signal_events:
                tracked = self._record_signals_for_tracking(signals, signal_events)
                logger.info("Recorded %d signals for outcome tracking", tracked)

            # 5.3 Update outcomes for completed horizons
            self._update_signal_outcomes()

            # 6. Filter high-impact for publishing
            high_impact_events = filter_high_impact(all_events, HIGH_IMPACT_THRESHOLD)
            high_impact_count = len(high_impact_events)
            logger.info("High-impact events: %d", high_impact_count)

            # 7. Publish to Telegram
            logger.info("Publishing to Telegram...")
            publish_results = self._publish_to_telegram(snapshot, signals, high_impact_events)

            for result in publish_results:
                if result.success:
                    events_published += 1
                else:
                    error_msg = result.error or "Unknown publish error"
                    errors.append(f"Publish failed: {error_msg}")

                    # Send failed events to deadletter
                    for event in high_impact_events:
                        self._journal.send_to_deadletter(event, error_msg)

            # Reset error counter on success
            if not errors:
                self._consecutive_errors = 0

            status = PipelineStatus.OK if not errors else PipelineStatus.DEGRADED

        except Exception as e:
            logger.error("Pipeline cycle failed: %s", e, exc_info=True)
            errors.append(str(e))
            self._consecutive_errors += 1
            status = PipelineStatus.ERROR

        result = CycleResult(
            status=status,
            events_collected=events_collected,
            events_published=events_published,
            signals_generated=signals_count,
            high_impact_news=high_impact_count,
            errors=errors,
            duration_sec=time.time() - start_time,
            timestamp=time.time(),
        )

        self._last_cycle_result = result
        return result

    def run_loop(
        self,
        interval_sec: int = 300,
        max_cycles: Optional[int] = None,
    ) -> None:
        """
        Run pipeline in continuous loop.

        Args:
            interval_sec: Seconds between cycles
            max_cycles: Maximum cycles (None = infinite)
        """
        logger.info("Starting signals pipeline loop (interval=%ds)", interval_sec)
        cycle_count = 0

        while True:
            if max_cycles and cycle_count >= max_cycles:
                logger.info("Reached max cycles (%d), stopping", max_cycles)
                break

            result = self.run_cycle()
            cycle_count += 1

            logger.info(
                "Cycle %d: status=%s, events=%d, published=%d, signals=%d, duration=%.1fs",
                cycle_count,
                result.status.value,
                result.events_collected,
                result.events_published,
                result.signals_generated,
                result.duration_sec,
            )

            if result.errors:
                for error in result.errors:
                    logger.warning("  Error: %s", error)

            if result.status == PipelineStatus.STOPPED:
                logger.info("Pipeline stopped, exiting loop")
                break

            # Backoff on consecutive errors
            if self._consecutive_errors > 3:
                backoff = min(interval_sec * 2, 1800)  # Max 30 min
                logger.warning("Consecutive errors: %d, backing off %ds", self._consecutive_errors, backoff)
                time.sleep(backoff)
            else:
                time.sleep(interval_sec)

    def get_last_result(self) -> Optional[CycleResult]:
        """Get last cycle result."""
        return self._last_cycle_result

    def get_status(self) -> Dict[str, Any]:
        """Get pipeline status summary."""
        journal_stats = self._journal.get_stats()

        return {
            "stop_flag_active": self._check_stop_flag(),
            "consecutive_errors": self._consecutive_errors,
            "last_cycle": self._last_cycle_result.status.value if self._last_cycle_result else "never_run",
            "journal": {
                "total_events": journal_stats.total_events,
                "deadletter_count": journal_stats.deadletter_count,
                "pending_by_consumer": journal_stats.pending_by_consumer,
            },
        }


def get_signals_pipeline() -> SignalsPipeline:
    """Get singleton pipeline instance."""
    global _pipeline_instance
    if "_pipeline_instance" not in globals():
        _pipeline_instance = SignalsPipeline()
    return _pipeline_instance


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== SIGNALS PIPELINE TEST ===\n")

    pipeline = SignalsPipeline()

    # Run single cycle
    result = pipeline.run_cycle()

    print(f"\nCycle Result:")
    print(f"  Status: {result.status.value}")
    print(f"  Events collected: {result.events_collected}")
    print(f"  Events published: {result.events_published}")
    print(f"  Signals: {result.signals_generated}")
    print(f"  High-impact news: {result.high_impact_news}")
    print(f"  Duration: {result.duration_sec:.2f}s")
    if result.errors:
        print(f"  Errors: {result.errors}")

    print(f"\nPipeline Status:")
    status = pipeline.get_status()
    for key, value in status.items():
        print(f"  {key}: {value}")
