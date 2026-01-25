# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T16:00:00Z
# Modified by: Claude (opus-4)
# Modified at (UTC): 2026-01-25T19:00:00Z
# Purpose: Spider v1.2 -> Telegram Bridge (sha256 JSONL format)
# === END SIGNATURE ===
"""
Spider to Telegram Bridge v1.1

Connects News Spider v1.2 output to Telegram publishing.
Reads from state/news_items.jsonl (sha256 JSONL format), classifies events.

SHA256 JSONL format: sha256:<hex16> <json>
Backward compatible with legacy plain JSON lines.

Modes:
- dry_run=True: Format and log, don't send
- dry_run=False: Actually publish to @hope_vip_signals

Fail-closed:
- No items = skip (not error)
- Parse error = skip item, log (corrupted data)
- SHA256 mismatch = skip item, log (integrity violation)
- Telegram error = retry queue

Usage:
    from core.spider.telegram_bridge import SpiderTelegramBridge

    bridge = SpiderTelegramBridge()
    result = bridge.publish_recent_news(dry_run=True)

CLI:
    python -m core.spider.telegram_bridge --dry-run
    python -m core.spider.telegram_bridge --publish
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any

# Add project root for imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.io.atomic import parse_sha256_jsonl_line, atomic_write_json
from core.spider.parser import RSSItem
from core.spider.collector import run_collection, CollectorResult
from core.spider.health import HealthTracker, categorize_error, ErrorCategory

logger = logging.getLogger(__name__)

# Paths (SSoT from project root)
STATE_DIR = PROJECT_ROOT / "state"
NEWS_ITEMS_PATH = STATE_DIR / "news_items.jsonl"
BRIDGE_STATE_PATH = STATE_DIR / "spider_telegram_bridge.json"

# Thresholds
HIGH_IMPACT_THRESHOLD = 0.6
MAX_ITEMS_PER_PUBLISH = 5
MAX_AGE_SECONDS = 3600  # 1 hour - don't publish older items


@dataclass
class ClassifiedNewsItem:
    """News item with classification."""
    item_id: str
    source_id: str
    title: str
    link: str
    published_utc: Optional[str]
    event_type: str
    impact_score: float
    sentiment: str
    affected_assets: List[str]


@dataclass
class PublishResult:
    """Result of publish operation."""
    success: bool
    items_processed: int
    items_published: int
    items_skipped: int
    items_already_published: int
    error: Optional[str] = None
    message_id: Optional[int] = None
    dry_run: bool = False


class SpiderTelegramBridge:
    """
    Bridge between Spider v1.1 and Telegram publishing.

    Flow:
    1. Read new items from news_items.jsonl
    2. Classify each item (event type, impact, sentiment)
    3. Filter by impact >= 0.6
    4. Publish to Telegram via SignalPublisher
    5. Track published item_ids to prevent duplicates
    """

    def __init__(
        self,
        news_path: Optional[Path] = None,
        state_path: Optional[Path] = None,
    ):
        self._news_path = news_path or NEWS_ITEMS_PATH
        self._state_path = state_path or BRIDGE_STATE_PATH
        self._published_ids: set = set()
        self._last_publish_time: float = 0
        self._classifier = None  # Lazy load
        self._publisher = None   # Lazy load
        self._load_state()

    def _load_state(self) -> None:
        """Load bridge state (published IDs)."""
        if not self._state_path.exists():
            return

        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._published_ids = set(data.get("published_ids", []))
            self._last_publish_time = data.get("last_publish_time", 0)
            logger.info("Loaded bridge state: %d published items", len(self._published_ids))
        except Exception as e:
            logger.warning("Failed to load bridge state: %s", e)

    def _save_state(self) -> None:
        """Save bridge state atomically using core.io.atomic."""
        published_list = list(self._published_ids)
        data = {
            "published_ids": published_list[-1000] if len(published_list) > 1000 else published_list,
            "last_publish_time": self._last_publish_time,
            "last_update_utc": datetime.now(timezone.utc).isoformat(),
        }

        try:
            atomic_write_json(self._state_path, data)
        except Exception as e:
            logger.error("Failed to save bridge state: %s", e)

    def _get_classifier(self):
        """Lazy load event classifier."""
        if self._classifier is None:
            from core.event_classifier import EventClassifier
            self._classifier = EventClassifier()
        return self._classifier

    def _get_publisher(self):
        """Lazy load signal publisher."""
        if self._publisher is None:
            from core.telegram_signals import SignalPublisher
            self._publisher = SignalPublisher()
        return self._publisher

    def _read_recent_items(self, max_age_sec: int = MAX_AGE_SECONDS) -> List[Dict[str, Any]]:
        """
        Read recent news items from sha256 JSONL file.

        Format: sha256:<hex16> <json>
        Backward compatible with legacy plain JSON lines.

        Args:
            max_age_sec: Maximum age in seconds

        Returns:
            List of parsed items (only recent ones)
        """
        if not self._news_path.exists():
            logger.warning("News items file not found: %s", self._news_path)
            return []

        items = []
        cutoff = time.time() - max_age_sec
        integrity_errors = 0

        try:
            with open(self._news_path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        # Try sha256 JSONL format first
                        if line.startswith("sha256:"):
                            item = parse_sha256_jsonl_line(line)
                        else:
                            # Legacy plain JSON (backward compatible)
                            item = json.loads(line)

                        # Parse timestamp
                        ts_utc = item.get("ts_utc", "")
                        if ts_utc:
                            try:
                                dt = datetime.fromisoformat(ts_utc.replace("Z", "+00:00"))
                                ts = dt.timestamp()
                            except Exception:
                                ts = 0
                        else:
                            ts = 0

                        # Filter by age
                        if ts >= cutoff:
                            item["_ts"] = ts
                            items.append(item)

                    except ValueError as e:
                        # SHA256 mismatch = integrity violation
                        if "SHA256 mismatch" in str(e):
                            integrity_errors += 1
                            logger.warning(
                                "Line %d: integrity violation (sha256 mismatch), skipping",
                                line_num
                            )
                        else:
                            logger.warning("Line %d: parse error: %s", line_num, e)
                        continue

                    except json.JSONDecodeError:
                        continue

            if integrity_errors > 0:
                logger.warning(
                    "Read %d items with %d integrity errors",
                    len(items), integrity_errors
                )
            else:
                logger.info("Read %d recent items (age < %ds)", len(items), max_age_sec)

            return items

        except Exception as e:
            logger.error("Failed to read news items: %s", e)
            return []

    def _classify_item(self, item: Dict[str, Any]) -> Optional[ClassifiedNewsItem]:
        """Classify a news item."""
        try:
            classifier = self._get_classifier()

            title = item.get("title", "")
            source = item.get("source_id", "unknown")
            link = item.get("link", "")
            pub_date = item.get("published_utc", "")

            classified = classifier.classify(
                title=title,
                source=source,
                link=link,
                pub_date=pub_date,
            )

            return ClassifiedNewsItem(
                item_id=item.get("item_id", ""),
                source_id=source,
                title=title,
                link=link,
                published_utc=pub_date,
                event_type=classified.event_type,
                impact_score=classified.impact_score,
                sentiment=classified.sentiment,
                affected_assets=classified.affected_assets,
            )

        except Exception as e:
            logger.warning("Failed to classify item: %s", e)
            return None

    def _format_news_message(self, items: List[ClassifiedNewsItem]) -> str:
        """Format news items for Telegram message."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        lines = [
            f"[SPIDER NEWS] {now}",
            "",
        ]

        type_emoji = {
            "regulation": "[LAW]",
            "listing": "[LIST]",
            "exploit": "[ALERT]",
            "macro": "[MACRO]",
            "institutional": "[INST]",
            "market": "[MKT]",
        }

        sentiment_marker = {
            "bullish": "+",
            "bearish": "-",
            "neutral": "~",
        }

        for item in items[:MAX_ITEMS_PER_PUBLISH]:
            e_type = type_emoji.get(item.event_type, "[NEWS]")
            sent = sentiment_marker.get(item.sentiment, "~")

            # Impact bar
            impact_filled = int(item.impact_score * 5)
            impact_bar = "#" * impact_filled + "." * (5 - impact_filled)

            title = item.title[:70] + "..." if len(item.title) > 70 else item.title

            lines.append(f"{e_type} <b>{title}</b>")
            lines.append(f"  [{sent}] Impact: [{impact_bar}] {item.impact_score:.0%}")

            if item.affected_assets:
                lines.append(f"  Assets: {', '.join(item.affected_assets[:5])}")

            lines.append(f"  Source: {item.source_id}")
            lines.append("")

        lines.append("#HOPE #spider #news")

        return "\n".join(lines)

    def publish_recent_news(
        self,
        dry_run: bool = True,
        max_age_sec: int = MAX_AGE_SECONDS,
        impact_threshold: float = HIGH_IMPACT_THRESHOLD,
    ) -> PublishResult:
        """
        Publish recent high-impact news to Telegram.

        Args:
            dry_run: If True, only format and log
            max_age_sec: Max age of items to consider
            impact_threshold: Minimum impact score to publish

        Returns:
            PublishResult with statistics
        """
        # Read recent items
        raw_items = self._read_recent_items(max_age_sec)

        if not raw_items:
            return PublishResult(
                success=True,
                items_processed=0,
                items_published=0,
                items_skipped=0,
                items_already_published=0,
                dry_run=dry_run,
            )

        # Classify items
        classified: List[ClassifiedNewsItem] = []
        for item in raw_items:
            c = self._classify_item(item)
            if c:
                classified.append(c)

        # Filter by impact
        high_impact = [c for c in classified if c.impact_score >= impact_threshold]

        # Filter already published
        to_publish = []
        already_published = 0

        for item in high_impact:
            if item.item_id in self._published_ids:
                already_published += 1
            else:
                to_publish.append(item)

        if not to_publish:
            return PublishResult(
                success=True,
                items_processed=len(raw_items),
                items_published=0,
                items_skipped=len(classified) - len(high_impact),
                items_already_published=already_published,
                dry_run=dry_run,
            )

        # Format message
        message = self._format_news_message(to_publish)

        if dry_run:
            print("\n=== DRY-RUN MESSAGE ===")
            print(message)
            print("=== END MESSAGE ===\n")

            return PublishResult(
                success=True,
                items_processed=len(raw_items),
                items_published=len(to_publish),
                items_skipped=len(classified) - len(high_impact),
                items_already_published=already_published,
                dry_run=True,
            )

        # Actually publish
        try:
            publisher = self._get_publisher()
            result = publisher._send_message(message)

            if result.success:
                # Mark as published
                for item in to_publish:
                    self._published_ids.add(item.item_id)

                self._last_publish_time = time.time()
                self._save_state()

                return PublishResult(
                    success=True,
                    items_processed=len(raw_items),
                    items_published=len(to_publish),
                    items_skipped=len(classified) - len(high_impact),
                    items_already_published=already_published,
                    message_id=result.message_id,
                    dry_run=False,
                )
            else:
                return PublishResult(
                    success=False,
                    items_processed=len(raw_items),
                    items_published=0,
                    items_skipped=len(classified) - len(high_impact),
                    items_already_published=already_published,
                    error=result.error,
                    dry_run=False,
                )

        except Exception as e:
            logger.error("Publish failed: %s", e)
            return PublishResult(
                success=False,
                items_processed=len(raw_items),
                items_published=0,
                items_skipped=len(classified) - len(high_impact),
                items_already_published=already_published,
                error=str(e),
                dry_run=False,
            )

    def run_full_cycle(
        self,
        collect_mode: str = "lenient",
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        """
        Run full cycle: collect news + publish high-impact.

        Args:
            collect_mode: "strict" or "lenient" for collector
            dry_run: If True, collect but don't persist or publish

        Returns:
            Dict with collector and publisher results
        """
        result = {
            "collect": None,
            "publish": None,
            "status": "ok",
            "errors": [],
        }

        # Step 1: Collect news
        try:
            collect_result = run_collection(mode=collect_mode, dry_run=dry_run)
            result["collect"] = collect_result.to_dict()

            if collect_result.fatal_error:
                result["errors"].append(f"Collector: {collect_result.fatal_error}")
                result["status"] = "degraded"

        except Exception as e:
            result["errors"].append(f"Collector error: {e}")
            result["status"] = "error"
            return result

        # Step 2: Publish high-impact news
        try:
            publish_result = self.publish_recent_news(dry_run=dry_run)
            result["publish"] = {
                "success": publish_result.success,
                "items_processed": publish_result.items_processed,
                "items_published": publish_result.items_published,
                "items_skipped": publish_result.items_skipped,
                "items_already_published": publish_result.items_already_published,
                "error": publish_result.error,
                "dry_run": publish_result.dry_run,
            }

            if not publish_result.success:
                result["errors"].append(f"Publisher: {publish_result.error}")
                result["status"] = "degraded"

        except Exception as e:
            result["errors"].append(f"Publisher error: {e}")
            result["status"] = "degraded"

        return result


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Spider Telegram Bridge v1.0")
    parser.add_argument(
        "--dry-run", "-d",
        action="store_true",
        help="Dry-run mode (format only, no publish)"
    )
    parser.add_argument(
        "--publish", "-p",
        action="store_true",
        help="Actually publish to Telegram"
    )
    parser.add_argument(
        "--collect", "-c",
        action="store_true",
        help="Run full cycle (collect + publish)"
    )
    parser.add_argument(
        "--mode", "-m",
        choices=["strict", "lenient"],
        default="lenient",
        help="Collector mode (default: lenient)"
    )
    parser.add_argument(
        "--threshold", "-t",
        type=float,
        default=HIGH_IMPACT_THRESHOLD,
        help=f"Impact threshold (default: {HIGH_IMPACT_THRESHOLD})"
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    dry_run = not args.publish

    bridge = SpiderTelegramBridge()

    if args.collect:
        print(f"\n=== SPIDER TELEGRAM BRIDGE (FULL CYCLE) ===")
        print(f"Mode: {args.mode.upper()}")
        print(f"Dry-run: {dry_run}")
        print(f"Impact threshold: {args.threshold}")
        print()

        result = bridge.run_full_cycle(
            collect_mode=args.mode,
            dry_run=dry_run,
        )

        print("\n=== RESULT ===")
        print(json.dumps(result, indent=2, default=str))

    else:
        print(f"\n=== SPIDER TELEGRAM BRIDGE (PUBLISH ONLY) ===")
        print(f"Dry-run: {dry_run}")
        print(f"Impact threshold: {args.threshold}")
        print()

        result = bridge.publish_recent_news(
            dry_run=dry_run,
            impact_threshold=args.threshold,
        )

        print("\n=== RESULT ===")
        print(f"Success: {result.success}")
        print(f"Items processed: {result.items_processed}")
        print(f"Items published: {result.items_published}")
        print(f"Items skipped (low impact): {result.items_skipped}")
        print(f"Items already published: {result.items_already_published}")
        if result.error:
            print(f"Error: {result.error}")
        if result.message_id:
            print(f"Message ID: {result.message_id}")


if __name__ == "__main__":
    main()
