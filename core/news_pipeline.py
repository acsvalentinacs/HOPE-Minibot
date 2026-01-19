"""
HOPE/NORE News Pipeline v1.0

Unified pipeline: Spider -> Scheduler -> Flash -> Publish.
Integrates all news/publication components.

Modes:
- dry_run=True: previews saved to state/previews/, no Telegram
- dry_run=False: actual publication to channels

CLI usage:
    python -m core.news_pipeline --dry-run
    python -m core.news_pipeline --publish

Components:
- NewsSpider: fetches RSS/announcements
- PublicationScheduler: UTC-based schedule
- FlashDetector: real-time alerts
- DailyDigestGenerator: end of day summary

Usage:
    from core.news_pipeline import NewsPipeline, get_news_pipeline

    pipeline = get_news_pipeline()
    result = pipeline.run_cycle(dry_run=True)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.news_spider import NewsSpider, get_news_spider, NewsItem
from core.publication_scheduler import (
    PublicationScheduler, get_scheduler,
    PublicationType, ScheduledPublication
)
from core.flash_detector import FlashDetector, get_flash_detector, FlashAlert
from core.daily_digest import DailyDigestGenerator, get_digest_generator, DailyDigest
from core.event_classifier import EventClassifier
from core.event_contract import Event, create_event, EventType, normalize_classified_event
from core.telegram_signals import SignalPublisher, get_signal_publisher, PublishResult

logger = logging.getLogger(__name__)

STATE_DIR = Path(r"C:\Users\kirillDev\Desktop\TradingBot\minibot\state")
PREVIEWS_DIR = STATE_DIR / "previews"
STOP_FLAG = Path(r"C:\Users\kirillDev\Desktop\TradingBot\flags\STOP.flag")

PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)


class PipelineStatus(str, Enum):
    """Pipeline execution status."""
    OK = "ok"
    DEGRADED = "degraded"
    ERROR = "error"
    STOPPED = "stopped"
    DRY_RUN = "dry_run"


@dataclass
class CycleResult:
    """Result of a pipeline cycle."""
    status: PipelineStatus
    news_fetched: int
    events_classified: int
    publications_due: int
    publications_sent: int
    flashes_detected: int
    flashes_sent: int
    errors: List[str]
    previews_saved: List[str]
    duration_sec: float
    timestamp: float


def _atomic_write(path: Path, content: str) -> None:
    """Atomic write: temp -> fsync -> replace."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as e:
        logger.error("Atomic write failed: %s", e)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


class NewsPipeline:
    """
    Unified news pipeline with dry-run support.

    Flow:
    1. Fetch news (spider)
    2. Classify events
    3. Check scheduled publications
    4. Check flash conditions
    5. Publish or save previews
    """

    def __init__(
        self,
        spider: Optional[NewsSpider] = None,
        scheduler: Optional[PublicationScheduler] = None,
        flash_detector: Optional[FlashDetector] = None,
        digest_generator: Optional[DailyDigestGenerator] = None,
        publisher: Optional[SignalPublisher] = None,
    ):
        self._spider = spider or get_news_spider()
        self._scheduler = scheduler or get_scheduler()
        self._flash_detector = flash_detector or get_flash_detector()
        self._digest_generator = digest_generator or get_digest_generator()
        self._publisher = publisher or get_signal_publisher()
        self._classifier = EventClassifier()

        self._last_result: Optional[CycleResult] = None

    def _check_stop_flag(self) -> bool:
        """Check if STOP.flag is active."""
        return STOP_FLAG.exists()

    def _classify_news(self, news_items: List[NewsItem]) -> List[Event]:
        """Classify news items into events."""
        events: List[Event] = []

        for item in news_items:
            try:
                classified = self._classifier.classify(
                    title=item.title,
                    source=item.source,
                    link=item.link,
                    pub_date=item.pub_date,
                )
                event = normalize_classified_event(classified)
                events.append(event)
            except Exception as e:
                logger.warning("Failed to classify news: %s", e)

        return events

    def _format_scheduled_publication(
        self,
        pub: ScheduledPublication,
        events: List[Event],
        news_items: List[NewsItem],
    ) -> str:
        """Format publication content based on type."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        if pub.pub_type == PublicationType.RU_DAILY_DIGEST:
            digest = self._digest_generator.generate_digest()
            return digest.format_telegram_ru()

        high_impact = [e for e in events if e.impact_score >= 0.6][:5]

        if pub.language == "ru":
            lines = [
                f"📰 <b>HOPE NEWS</b> | {now}",
                "",
            ]
        else:
            lines = [
                f"📰 <b>HOPE NEWS</b> | {now}",
                "",
            ]

        type_emoji = {
            "regulation": "⚖️",
            "institutional": "🏦",
            "exploit": "🚨",
            "macro": "🌍",
            "market": "📊",
        }

        for event in high_impact:
            emoji = type_emoji.get(event.event_type, "📰")
            title = event.title[:70] + "..." if len(event.title) > 70 else event.title
            lines.append(f"{emoji} <b>{title}</b>")
            lines.append(f"  Impact: {event.impact_score:.0%} | {event.source}")
            if event.source_url:
                lines.append(f"  <a href=\"{event.source_url}\">Read more</a>")
            lines.append("")

        if not high_impact:
            recent_news = sorted(news_items, key=lambda x: x.fetch_timestamp, reverse=True)[:5]
            for item in recent_news:
                lines.append(f"📰 {item.title[:60]}...")
                lines.append(f"  └ {item.source}")
                lines.append("")

        lines.append("#HOPE #crypto #news")

        return "\n".join(lines)

    def _format_flash_alert(self, flash: FlashAlert) -> str:
        """Format flash alert for Telegram."""
        lines = [
            f"🔔 <b>FLASH ALERT</b>",
            "",
            f"<b>{flash.title}</b>",
            flash.details,
            "",
            f"Severity: {'█' * int(flash.severity * 5)}{'░' * (5 - int(flash.severity * 5))}",
            "",
            "#HOPE #flash #alert",
        ]
        return "\n".join(lines)

    def _save_preview(self, name: str, content: str) -> str:
        """Save preview to file. Returns file path."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{name}.txt"
        path = PREVIEWS_DIR / filename

        _atomic_write(path, content)
        logger.info("Saved preview: %s", path)

        return str(path)

    def run_cycle(self, dry_run: bool = True) -> CycleResult:
        """
        Run single pipeline cycle.

        Args:
            dry_run: If True, save previews instead of publishing

        Returns:
            CycleResult with metrics
        """
        start_time = time.time()
        errors: List[str] = []
        previews: List[str] = []

        news_fetched = 0
        events_classified = 0
        publications_due = 0
        publications_sent = 0
        flashes_detected = 0
        flashes_sent = 0

        if self._check_stop_flag():
            logger.info("Pipeline stopped by STOP.flag")
            return CycleResult(
                status=PipelineStatus.STOPPED,
                news_fetched=0,
                events_classified=0,
                publications_due=0,
                publications_sent=0,
                flashes_detected=0,
                flashes_sent=0,
                errors=[],
                previews_saved=[],
                duration_sec=time.time() - start_time,
                timestamp=time.time(),
            )

        fetched_items: List[NewsItem] = []

        try:
            logger.info("Fetching news...")
            # In non-dry-run mode, spider writes to JSONL and we read from it
            # In dry-run mode, we need to capture items directly from fetch
            fetch_results = self._spider.fetch_all(dry_run=dry_run)
            news_fetched = sum(r.items_count for r in fetch_results if r.success)

            for r in fetch_results:
                if not r.success and r.error:
                    errors.append(f"Fetch {r.source}: {r.error}")

            logger.info("Fetched %d news items", news_fetched)

        except Exception as e:
            logger.error("Spider failed: %s", e)
            errors.append(f"Spider error: {e}")

        try:
            # Get news items for classification
            if dry_run:
                # In dry-run, get recent from existing file + fetch fresh for display
                news_items = self._spider.get_recent_items(max_age_sec=3600)
                # Also get from market intel which fetches fresh
                from core.market_intel import get_market_intel
                intel = get_market_intel()
                snapshot = intel.get_snapshot(force_refresh=True)
                # Convert market intel news to NewsItem format for classification
                for n in getattr(snapshot, 'news', []):
                    news_items.append(NewsItem(
                        item_id=f"sha256:{hash(n.title) & 0xFFFFFFFF:08x}",
                        source=n.source,
                        title=n.title,
                        link=n.link,
                        pub_date=n.pub_date,
                        pub_timestamp=time.time(),
                        fetch_timestamp=time.time(),
                        raw_hash="",
                    ))
            else:
                news_items = self._spider.get_recent_items()

            events = self._classify_news(news_items)
            events_classified = len(events)
            logger.info("Classified %d events from %d news items", events_classified, len(news_items))

        except Exception as e:
            logger.error("Classification failed: %s", e)
            errors.append(f"Classification error: {e}")
            events = []
            news_items = []

        try:
            due_pubs = self._scheduler.get_due_publications()
            publications_due = len(due_pubs)
            logger.info("Publications due: %d", publications_due)

            for pub in due_pubs:
                content = self._format_scheduled_publication(pub, events, news_items)

                if dry_run:
                    preview_path = self._save_preview(
                        f"{pub.pub_type.value}_{pub.language}",
                        content
                    )
                    previews.append(preview_path)
                else:
                    result = self._publisher._send_message(content)
                    if result.success:
                        self._scheduler.mark_published(pub.pub_type)
                        publications_sent += 1
                    else:
                        errors.append(f"Publish {pub.pub_type.value}: {result.error}")

        except Exception as e:
            logger.error("Publication failed: %s", e)
            errors.append(f"Publication error: {e}")

        try:
            from core.market_intel import get_market_intel
            intel = get_market_intel()
            snapshot = intel.get_snapshot()

            market_flashes = self._flash_detector.check_market_flash(snapshot)
            news_flashes = self._flash_detector.check_news_flash(events)

            all_flashes = market_flashes + news_flashes
            flashes_detected = len(all_flashes)

            for flash in all_flashes:
                can_send, reason = self._scheduler.can_send_flash()
                if not can_send:
                    logger.info("Flash rate-limited: %s", reason)
                    continue

                content = self._format_flash_alert(flash)

                if dry_run:
                    preview_path = self._save_preview(
                        f"flash_{flash.flash_type.value}",
                        content
                    )
                    previews.append(preview_path)
                else:
                    result = self._publisher._send_message(content)
                    if result.success:
                        self._scheduler.record_flash()
                        flashes_sent += 1
                    else:
                        errors.append(f"Flash {flash.flash_type.value}: {result.error}")

        except Exception as e:
            logger.error("Flash detection failed: %s", e)
            errors.append(f"Flash error: {e}")

        status = PipelineStatus.DRY_RUN if dry_run else (
            PipelineStatus.OK if not errors else PipelineStatus.DEGRADED
        )

        result = CycleResult(
            status=status,
            news_fetched=news_fetched,
            events_classified=events_classified,
            publications_due=publications_due,
            publications_sent=publications_sent,
            flashes_detected=flashes_detected,
            flashes_sent=flashes_sent,
            errors=errors,
            previews_saved=previews,
            duration_sec=time.time() - start_time,
            timestamp=time.time(),
        )

        self._last_result = result
        return result

    def get_status(self) -> Dict[str, Any]:
        """Get pipeline status."""
        spider_stats = self._spider.get_stats()
        scheduler_status = self._scheduler.get_status()
        flash_stats = self._flash_detector.get_stats()

        return {
            "stop_flag_active": self._check_stop_flag(),
            "last_result": self._last_result.status.value if self._last_result else "never_run",
            "spider": spider_stats,
            "scheduler": scheduler_status,
            "flash_detector": flash_stats,
        }


def get_news_pipeline() -> NewsPipeline:
    """Get singleton pipeline instance."""
    global _pipeline_instance
    if "_pipeline_instance" not in globals():
        _pipeline_instance = NewsPipeline()
    return _pipeline_instance


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="HOPE News Pipeline")
    parser.add_argument(
        "--dry-run", "-d",
        action="store_true",
        help="Save previews instead of publishing (default)"
    )
    parser.add_argument(
        "--publish", "-p",
        action="store_true",
        help="Actually publish to Telegram"
    )
    parser.add_argument(
        "--status", "-s",
        action="store_true",
        help="Show pipeline status"
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    pipeline = get_news_pipeline()

    if args.status:
        status = pipeline.get_status()
        print("\n=== NEWS PIPELINE STATUS ===\n")
        print(json.dumps(status, indent=2, default=str))
        return

    dry_run = not args.publish

    print(f"\n=== NEWS PIPELINE {'DRY-RUN' if dry_run else 'PUBLISH'} ===\n")

    result = pipeline.run_cycle(dry_run=dry_run)

    print(f"\nCycle Result:")
    print(f"  Status: {result.status.value}")
    print(f"  News fetched: {result.news_fetched}")
    print(f"  Events classified: {result.events_classified}")
    print(f"  Publications due: {result.publications_due}")
    print(f"  Publications sent: {result.publications_sent}")
    print(f"  Flashes detected: {result.flashes_detected}")
    print(f"  Flashes sent: {result.flashes_sent}")
    print(f"  Duration: {result.duration_sec:.2f}s")

    if result.errors:
        print(f"\nErrors ({len(result.errors)}):")
        for err in result.errors:
            print(f"  - {err}")

    if result.previews_saved:
        print(f"\nPreviews saved ({len(result.previews_saved)}):")
        for path in result.previews_saved:
            print(f"  - {path}")


if __name__ == "__main__":
    main()
