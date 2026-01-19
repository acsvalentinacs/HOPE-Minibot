"""
HOPE/NORE News Spider v1.0

Fetches news from RSS feeds and Binance announcements.
All data persisted as atomic artifacts with sha256: prefix.

Design principles:
- Fail-closed: parse error = source blocked 30min, reason logged
- Atomic writes: raw -> sha256 -> persist -> normalize
- Dedup by title+link+pubDate hash
- Allowlist only: no arbitrary URLs

Allowed sources:
- https://www.coindesk.com/arc/outboundfeeds/rss/
- https://cointelegraph.com/rss
- https://decrypt.co/feed
- https://www.theblock.co/rss.xml
- https://bitcoinmagazine.com/feed
- https://www.binance.com/en/blog/rss

Usage:
    from core.news_spider import NewsSpider, get_news_spider

    spider = NewsSpider()
    items = spider.fetch_all()  # Returns list of NewsItem

    # Dry-run mode
    items = spider.fetch_all(dry_run=True)  # No persistence
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

logger = logging.getLogger(__name__)

STATE_DIR = Path(r"C:\Users\kirillDev\Desktop\TradingBot\minibot\state")
SNAPSHOTS_DIR = STATE_DIR / "snapshots"
NEWS_JSONL = STATE_DIR / "news_items.jsonl"
BLOCKED_SOURCES_FILE = STATE_DIR / "blocked_sources.json"

SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

RSS_SOURCES = {
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "cointelegraph": "https://cointelegraph.com/rss",
    "decrypt": "https://decrypt.co/feed",
    "theblock": "https://www.theblock.co/rss.xml",
    "bitcoinmagazine": "https://bitcoinmagazine.com/feed",
    "binance_blog": "https://www.binance.com/en/blog/rss",
}

REQUEST_TIMEOUT = 15
BLOCK_DURATION_SEC = 1800  # 30 minutes
MAX_ITEMS_PER_SOURCE = 15
NEWS_TTL_SEC = 900  # 15 minutes


def _sha256_hex(data: bytes | str) -> str:
    """Compute sha256 hex digest."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _sha256_short(data: bytes | str) -> str:
    """Short sha256 prefix (16 chars)."""
    return _sha256_hex(data)[:16]


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
        logger.error("Atomic write failed for %s: %s", path, e)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _append_jsonl(path: Path, obj: dict) -> None:
    """Append object to JSONL with sha256: prefix."""
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    hash_hex = _sha256_short(raw)
    line = f"sha256:{hash_hex}:{raw}\n"

    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


@dataclass
class NewsItem:
    """Normalized news item."""
    item_id: str  # sha256:xxx
    source: str
    title: str
    link: str
    pub_date: str
    pub_timestamp: float
    fetch_timestamp: float
    raw_hash: str

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "source": self.source,
            "title": self.title,
            "link": self.link,
            "pub_date": self.pub_date,
            "pub_timestamp": self.pub_timestamp,
            "fetch_timestamp": self.fetch_timestamp,
            "raw_hash": self.raw_hash,
        }


@dataclass
class FetchResult:
    """Result of fetch operation."""
    source: str
    success: bool
    items_count: int
    raw_hash: str
    error: Optional[str] = None
    blocked_until: Optional[float] = None


class NewsSpider:
    """
    News spider with fail-closed design.

    Fetches RSS feeds, saves raw artifacts, normalizes to NewsItem.
    """

    def __init__(
        self,
        snapshots_dir: Path = SNAPSHOTS_DIR,
        news_path: Path = NEWS_JSONL,
    ):
        self._snapshots_dir = snapshots_dir
        self._news_path = news_path
        self._seen_ids: Set[str] = set()
        self._blocked_sources: Dict[str, float] = {}

        self._load_seen_ids()
        self._load_blocked_sources()

    def _load_seen_ids(self) -> None:
        """Load already seen item IDs for dedup."""
        if not self._news_path.exists():
            return

        try:
            with open(self._news_path, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split(":", 2)
                    if len(parts) == 3:
                        obj = json.loads(parts[2])
                        if "item_id" in obj:
                            self._seen_ids.add(obj["item_id"])
            logger.info("Loaded %d seen news IDs", len(self._seen_ids))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to load seen IDs: %s", e)

    def _load_blocked_sources(self) -> None:
        """Load blocked sources list."""
        if not BLOCKED_SOURCES_FILE.exists():
            return

        try:
            content = BLOCKED_SOURCES_FILE.read_text(encoding="utf-8")
            self._blocked_sources = json.loads(content)

            now = time.time()
            expired = [k for k, v in self._blocked_sources.items() if v < now]
            for k in expired:
                del self._blocked_sources[k]

            if expired:
                self._save_blocked_sources()

        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to load blocked sources: %s", e)

    def _save_blocked_sources(self) -> None:
        """Save blocked sources list."""
        try:
            content = json.dumps(self._blocked_sources, indent=2)
            _atomic_write(BLOCKED_SOURCES_FILE, content)
        except OSError as e:
            logger.error("Failed to save blocked sources: %s", e)

    def _is_source_blocked(self, source: str) -> bool:
        """Check if source is blocked."""
        if source not in self._blocked_sources:
            return False
        return self._blocked_sources[source] > time.time()

    def _block_source(self, source: str, reason: str) -> None:
        """Block source for BLOCK_DURATION_SEC."""
        until = time.time() + BLOCK_DURATION_SEC
        self._blocked_sources[source] = until
        self._save_blocked_sources()
        logger.warning("Blocked source %s until %s: %s", source, datetime.fromtimestamp(until), reason)

    def _save_raw_snapshot(self, source: str, raw_bytes: bytes) -> str:
        """Save raw snapshot as artifact. Returns sha256 hash."""
        hash_full = _sha256_hex(raw_bytes)
        hash_short = hash_full[:16]

        today = datetime.utcnow().strftime("%Y-%m-%d")
        source_dir = self._snapshots_dir / source / today
        source_dir.mkdir(parents=True, exist_ok=True)

        raw_path = source_dir / f"sha256_{hash_short}.raw"
        meta_path = source_dir / f"sha256_{hash_short}.json"

        if not raw_path.exists():
            raw_path.write_bytes(raw_bytes)

            meta = {
                "source": source,
                "timestamp_utc": datetime.utcnow().isoformat(),
                "timestamp_unix": time.time(),
                "content_sha256": hash_full,
                "size_bytes": len(raw_bytes),
            }
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            logger.debug("Saved snapshot: %s", raw_path)

        return hash_short

    def _fetch_rss(self, source: str, url: str) -> Tuple[Optional[bytes], Optional[str]]:
        """Fetch RSS feed. Returns (raw_bytes, error)."""
        try:
            req = Request(url, headers={"User-Agent": "HOPE-NewsSpider/1.0"})
            with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                raw = resp.read()
            return raw, None
        except (URLError, HTTPError) as e:
            return None, str(e)
        except Exception as e:
            return None, f"Unexpected: {e}"

    def _parse_rss(self, source: str, raw_bytes: bytes) -> List[NewsItem]:
        """Parse RSS XML into NewsItem list. Fail-closed on error."""
        items: List[NewsItem] = []
        fetch_ts = time.time()

        try:
            content = raw_bytes.decode("utf-8", errors="replace")
            root = ET.fromstring(content)
        except ET.ParseError as e:
            raise ValueError(f"XML parse error: {e}")

        channel = root.find("channel")
        if channel is None:
            channel = root.find(".//{http://www.w3.org/2005/Atom}feed")
            if channel is None:
                raise ValueError("No channel/feed found in RSS")

        item_tags = channel.findall("item")
        if not item_tags:
            item_tags = channel.findall("{http://www.w3.org/2005/Atom}entry")

        for item_elem in item_tags[:MAX_ITEMS_PER_SOURCE]:
            title_elem = item_elem.find("title")
            if title_elem is None:
                title_elem = item_elem.find("{http://www.w3.org/2005/Atom}title")

            link_elem = item_elem.find("link")
            if link_elem is None:
                link_elem = item_elem.find("{http://www.w3.org/2005/Atom}link")

            pub_elem = item_elem.find("pubDate")
            if pub_elem is None:
                pub_elem = item_elem.find("{http://www.w3.org/2005/Atom}published")
            if pub_elem is None:
                pub_elem = item_elem.find("{http://www.w3.org/2005/Atom}updated")

            title = (title_elem.text or "").strip() if title_elem is not None else ""

            if link_elem is not None:
                link = link_elem.text or link_elem.get("href", "") or ""
            else:
                link = ""
            link = link.strip()

            pub_date = (pub_elem.text or "").strip() if pub_elem is not None else ""

            if not title:
                continue

            dedup_key = f"{source}:{title}:{link}:{pub_date}"
            item_id = f"sha256:{_sha256_short(dedup_key)}"

            pub_ts = self._parse_date(pub_date)

            raw_hash = _sha256_short(f"{title}{link}{pub_date}")

            items.append(NewsItem(
                item_id=item_id,
                source=source,
                title=title,
                link=link,
                pub_date=pub_date,
                pub_timestamp=pub_ts,
                fetch_timestamp=fetch_ts,
                raw_hash=raw_hash,
            ))

        return items

    def _parse_date(self, date_str: str) -> float:
        """Parse RSS date string to unix timestamp."""
        if not date_str:
            return time.time()

        formats = [
            "%a, %d %b %Y %H:%M:%S %z",
            "%a, %d %b %Y %H:%M:%S GMT",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d %H:%M:%S",
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.timestamp()
            except ValueError:
                continue

        return time.time()

    def fetch_source(self, source: str, url: str, dry_run: bool = False) -> FetchResult:
        """Fetch single source. Returns FetchResult."""
        if self._is_source_blocked(source):
            blocked_until = self._blocked_sources.get(source, 0)
            return FetchResult(
                source=source,
                success=False,
                items_count=0,
                raw_hash="",
                error="Source blocked",
                blocked_until=blocked_until,
            )

        raw_bytes, fetch_error = self._fetch_rss(source, url)

        if fetch_error:
            self._block_source(source, fetch_error)
            return FetchResult(
                source=source,
                success=False,
                items_count=0,
                raw_hash="",
                error=fetch_error,
            )

        try:
            raw_hash = self._save_raw_snapshot(source, raw_bytes) if not dry_run else _sha256_short(raw_bytes)
            items = self._parse_rss(source, raw_bytes)
        except ValueError as e:
            self._block_source(source, str(e))
            return FetchResult(
                source=source,
                success=False,
                items_count=0,
                raw_hash="",
                error=str(e),
            )

        new_count = 0
        for item in items:
            if item.item_id in self._seen_ids:
                continue

            if not dry_run:
                _append_jsonl(self._news_path, item.to_dict())
                self._seen_ids.add(item.item_id)

            new_count += 1

        logger.info("Fetched %s: %d items (%d new)", source, len(items), new_count)

        return FetchResult(
            source=source,
            success=True,
            items_count=new_count,
            raw_hash=raw_hash,
        )

    def fetch_all(self, dry_run: bool = False) -> List[FetchResult]:
        """Fetch all RSS sources. Returns list of FetchResult."""
        results: List[FetchResult] = []

        for source, url in RSS_SOURCES.items():
            result = self.fetch_source(source, url, dry_run=dry_run)
            results.append(result)

        return results

    def get_recent_items(self, max_age_sec: float = NEWS_TTL_SEC) -> List[NewsItem]:
        """Get recent news items within TTL."""
        if not self._news_path.exists():
            return []

        cutoff = time.time() - max_age_sec
        items: List[NewsItem] = []

        try:
            with open(self._news_path, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split(":", 2)
                    if len(parts) != 3:
                        continue

                    obj = json.loads(parts[2])
                    if obj.get("fetch_timestamp", 0) >= cutoff:
                        items.append(NewsItem(
                            item_id=obj["item_id"],
                            source=obj["source"],
                            title=obj["title"],
                            link=obj["link"],
                            pub_date=obj["pub_date"],
                            pub_timestamp=obj.get("pub_timestamp", 0),
                            fetch_timestamp=obj["fetch_timestamp"],
                            raw_hash=obj.get("raw_hash", ""),
                        ))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to load recent items: %s", e)

        items.sort(key=lambda x: x.fetch_timestamp, reverse=True)
        return items

    def get_stats(self) -> Dict[str, int]:
        """Get spider statistics."""
        return {
            "seen_items": len(self._seen_ids),
            "blocked_sources": len([k for k, v in self._blocked_sources.items() if v > time.time()]),
            "total_sources": len(RSS_SOURCES),
        }


def get_news_spider() -> NewsSpider:
    """Get singleton spider instance."""
    global _spider_instance
    if "_spider_instance" not in globals():
        _spider_instance = NewsSpider()
    return _spider_instance


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    print("=== NEWS SPIDER TEST (DRY-RUN) ===\n")

    spider = NewsSpider()
    results = spider.fetch_all(dry_run=True)

    print("\nFetch Results:")
    for r in results:
        status = "OK" if r.success else f"FAIL: {r.error}"
        print(f"  {r.source}: {status} ({r.items_count} items)")

    print("\nStats:", spider.get_stats())
