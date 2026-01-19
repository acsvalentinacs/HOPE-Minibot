"""
HOPE/NORE Publication Scheduler v1.0

UTC-based schedule for RU/EN publications.

Schedule (all times UTC):
RU (3x/day):
  - 07:30 UTC - Morning (Europe wakes, Asia closes)
  - 12:30 UTC - Midday (Europe/US crossover)
  - 19:30 UTC - Evening (US peak)

EN (2x/day):
  - 13:00 UTC - US crossover digest
  - 21:00 UTC - US close digest

Daily RU Digest:
  - 21:30 UTC - "Итоги дня"

Flash alerts: triggered by FlashDetector, not scheduled.

Usage:
    from core.publication_scheduler import PublicationScheduler, get_scheduler

    scheduler = get_scheduler()
    due = scheduler.get_due_publications()
    for pub in due:
        print(f"Due: {pub.pub_type} at {pub.scheduled_utc}")
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

STATE_DIR = Path(r"C:\Users\kirillDev\Desktop\TradingBot\minibot\state")
SCHEDULER_STATE_FILE = STATE_DIR / "scheduler_state.json"


class PublicationType(str, Enum):
    """Publication types."""
    RU_MORNING = "ru_morning"       # 07:30 UTC
    RU_MIDDAY = "ru_midday"         # 12:30 UTC
    RU_EVENING = "ru_evening"       # 19:30 UTC
    EN_CROSSOVER = "en_crossover"   # 13:00 UTC
    EN_CLOSE = "en_close"           # 21:00 UTC
    RU_DAILY_DIGEST = "ru_daily"    # 21:30 UTC
    FLASH_RU = "flash_ru"           # instant
    FLASH_EN = "flash_en"           # instant


SCHEDULE_UTC = {
    PublicationType.RU_MORNING: (7, 30),
    PublicationType.RU_MIDDAY: (12, 30),
    PublicationType.RU_EVENING: (19, 30),
    PublicationType.EN_CROSSOVER: (13, 0),
    PublicationType.EN_CLOSE: (21, 0),
    PublicationType.RU_DAILY_DIGEST: (21, 30),
}

FLASH_MIN_INTERVAL_SEC = 90
FLASH_MAX_PER_HOUR = 6


@dataclass
class ScheduledPublication:
    """A scheduled publication."""
    pub_type: PublicationType
    scheduled_utc: datetime
    language: str  # "ru" | "en"
    is_flash: bool = False

    def to_dict(self) -> dict:
        return {
            "pub_type": self.pub_type.value,
            "scheduled_utc": self.scheduled_utc.isoformat(),
            "language": self.language,
            "is_flash": self.is_flash,
        }


@dataclass
class SchedulerState:
    """Scheduler persistent state."""
    last_published: Dict[str, float]  # pub_type -> unix timestamp
    flash_timestamps: List[float]     # recent flash timestamps for rate limiting
    last_update: float

    def to_dict(self) -> dict:
        return {
            "last_published": self.last_published,
            "flash_timestamps": self.flash_timestamps,
            "last_update": self.last_update,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SchedulerState":
        return cls(
            last_published=data.get("last_published", {}),
            flash_timestamps=data.get("flash_timestamps", []),
            last_update=data.get("last_update", 0),
        )


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


class PublicationScheduler:
    """
    UTC-based publication scheduler.

    Tracks last published times, determines what's due.
    """

    def __init__(self, state_path: Path = SCHEDULER_STATE_FILE):
        self._state_path = state_path
        self._state = self._load_state()

    def _load_state(self) -> SchedulerState:
        """Load state from file."""
        if not self._state_path.exists():
            return SchedulerState(
                last_published={},
                flash_timestamps=[],
                last_update=time.time(),
            )

        try:
            content = self._state_path.read_text(encoding="utf-8")
            data = json.loads(content)
            return SchedulerState.from_dict(data)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to load scheduler state: %s", e)
            return SchedulerState(
                last_published={},
                flash_timestamps=[],
                last_update=time.time(),
            )

    def _save_state(self) -> None:
        """Save state to file."""
        self._state.last_update = time.time()
        content = json.dumps(self._state.to_dict(), indent=2)
        try:
            _atomic_write(self._state_path, content)
        except OSError as e:
            logger.error("Failed to save scheduler state: %s", e)

    def _utc_now(self) -> datetime:
        """Get current UTC datetime."""
        return datetime.now(timezone.utc)

    def _get_scheduled_time_today(self, pub_type: PublicationType) -> datetime:
        """Get scheduled UTC time for today."""
        if pub_type not in SCHEDULE_UTC:
            raise ValueError(f"No schedule for {pub_type}")

        hour, minute = SCHEDULE_UTC[pub_type]
        now = self._utc_now()
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def _was_published_today(self, pub_type: PublicationType) -> bool:
        """Check if publication was already done today."""
        last_ts = self._state.last_published.get(pub_type.value, 0)
        if last_ts == 0:
            return False

        last_dt = datetime.fromtimestamp(last_ts, tz=timezone.utc)
        now = self._utc_now()

        return last_dt.date() == now.date()

    def get_due_publications(self) -> List[ScheduledPublication]:
        """
        Get list of publications that are due now.

        Returns publications where:
        - Current time >= scheduled time
        - Not already published today
        """
        now = self._utc_now()
        due: List[ScheduledPublication] = []

        for pub_type, (hour, minute) in SCHEDULE_UTC.items():
            scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

            if now < scheduled:
                continue

            if self._was_published_today(pub_type):
                continue

            lang = "ru" if "ru" in pub_type.value or "daily" in pub_type.value else "en"

            due.append(ScheduledPublication(
                pub_type=pub_type,
                scheduled_utc=scheduled,
                language=lang,
                is_flash=False,
            ))

        return due

    def mark_published(self, pub_type: PublicationType) -> None:
        """Mark publication as done."""
        self._state.last_published[pub_type.value] = time.time()
        self._save_state()
        logger.info("Marked %s as published at %s UTC", pub_type.value, self._utc_now().strftime("%H:%M"))

    def can_send_flash(self) -> Tuple[bool, Optional[str]]:
        """
        Check if flash alert can be sent.

        Returns (can_send, reason_if_not).
        Rate limits:
        - Min 90s between flashes
        - Max 6 flashes per hour
        """
        now = time.time()
        one_hour_ago = now - 3600

        self._state.flash_timestamps = [
            ts for ts in self._state.flash_timestamps
            if ts > one_hour_ago
        ]

        if len(self._state.flash_timestamps) >= FLASH_MAX_PER_HOUR:
            return False, f"Rate limit: {FLASH_MAX_PER_HOUR} flashes/hour"

        if self._state.flash_timestamps:
            last_flash = max(self._state.flash_timestamps)
            if now - last_flash < FLASH_MIN_INTERVAL_SEC:
                wait = int(FLASH_MIN_INTERVAL_SEC - (now - last_flash))
                return False, f"Cooldown: wait {wait}s"

        return True, None

    def record_flash(self) -> None:
        """Record flash alert timestamp."""
        self._state.flash_timestamps.append(time.time())
        self._save_state()

    def get_next_scheduled(self) -> List[Tuple[PublicationType, datetime, int]]:
        """
        Get upcoming scheduled publications.

        Returns list of (pub_type, scheduled_utc, seconds_until).
        """
        now = self._utc_now()
        upcoming: List[Tuple[PublicationType, datetime, int]] = []

        for pub_type, (hour, minute) in SCHEDULE_UTC.items():
            scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

            if scheduled <= now:
                scheduled += timedelta(days=1)

            seconds_until = int((scheduled - now).total_seconds())

            upcoming.append((pub_type, scheduled, seconds_until))

        upcoming.sort(key=lambda x: x[2])
        return upcoming

    def get_status(self) -> Dict[str, any]:
        """Get scheduler status."""
        now = self._utc_now()
        due = self.get_due_publications()
        upcoming = self.get_next_scheduled()

        can_flash, flash_reason = self.can_send_flash()

        return {
            "utc_now": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "due_count": len(due),
            "due_types": [p.pub_type.value for p in due],
            "next_scheduled": upcoming[0][0].value if upcoming else None,
            "next_in_seconds": upcoming[0][2] if upcoming else None,
            "can_send_flash": can_flash,
            "flash_reason": flash_reason,
            "flashes_last_hour": len(self._state.flash_timestamps),
        }


def get_scheduler() -> PublicationScheduler:
    """Get singleton scheduler instance."""
    global _scheduler_instance
    if "_scheduler_instance" not in globals():
        _scheduler_instance = PublicationScheduler()
    return _scheduler_instance


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("=== PUBLICATION SCHEDULER TEST ===\n")

    scheduler = PublicationScheduler()
    status = scheduler.get_status()

    print("Current Status:")
    for k, v in status.items():
        print(f"  {k}: {v}")

    print("\nUpcoming Publications:")
    for pub_type, scheduled, secs in scheduler.get_next_scheduled():
        hours = secs // 3600
        mins = (secs % 3600) // 60
        print(f"  {pub_type.value}: {scheduled.strftime('%H:%M')} UTC (in {hours}h {mins}m)")
