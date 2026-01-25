# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T14:00:00Z
# Purpose: RSS/announcement parsing (stdlib-only, no feedparser)
# === END SIGNATURE ===
"""
News Parser Module

Parses RSS feeds and Binance announcements using stdlib only.
Uses xml.etree.ElementTree for XML parsing (no feedparser).
"""

import hashlib
import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse


@dataclass
class RSSItem:
    """
    Parsed news item from RSS feed or announcement API.

    Attributes:
        item_id: Unique identifier (sha256 of link or generated)
        source_id: Source configuration ID
        title: Item title (HTML-unescaped)
        link: Canonical URL to item
        published_utc: Publication timestamp (UTC)
        description: Item summary/description (HTML stripped)
        category: Item category if available
        author: Author name if available
        raw_published: Original published string from feed
    """
    item_id: str
    source_id: str
    title: str
    link: str
    published_utc: Optional[datetime]
    description: str = ""
    category: str = ""
    author: str = ""
    raw_published: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "item_id": self.item_id,
            "source_id": self.source_id,
            "title": self.title,
            "link": self.link,
            "published_utc": (
                self.published_utc.isoformat() if self.published_utc else None
            ),
            "description": self.description,
            "category": self.category,
            "author": self.author,
        }


class ParseError(Exception):
    """Error parsing feed content."""
    pass


def _strip_html(text: str) -> str:
    """Remove HTML tags and unescape entities."""
    if not text:
        return ""
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Unescape HTML entities
    text = html.unescape(text)
    # Normalize whitespace
    text = " ".join(text.split())
    return text.strip()


def _generate_item_id(link: str, title: str, source_id: str) -> str:
    """
    Generate unique item ID from link (preferred) or title+source.

    Returns first 16 chars of SHA256 hash.
    """
    if link:
        content = link
    else:
        content = f"{source_id}:{title}"

    h = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return h[:16]


def _parse_rfc822_date(date_str: str) -> Optional[datetime]:
    """
    Parse RFC 822 date string (common in RSS).

    Examples:
        "Mon, 25 Jan 2026 12:00:00 +0000"
        "25 Jan 2026 12:00:00 GMT"

    Returns:
        datetime in UTC or None if parsing fails
    """
    if not date_str:
        return None

    try:
        dt = parsedate_to_datetime(date_str)
        # Convert to UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception:
        pass

    # Try ISO 8601 format (some feeds use this)
    try:
        # Remove timezone suffix variations
        clean = re.sub(r"[Zz]$", "+00:00", date_str)
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception:
        pass

    return None


def _find_text(elem: ET.Element, tags: List[str], namespaces: Dict[str, str] = None) -> str:
    """
    Find text content from first matching tag.

    Args:
        elem: Parent element
        tags: List of tag names to try (in order)
        namespaces: XML namespace mappings

    Returns:
        Text content or empty string
    """
    ns = namespaces or {}
    for tag in tags:
        # Try with namespace prefix
        for prefix, uri in ns.items():
            child = elem.find(f"{{{uri}}}{tag}")
            if child is not None and child.text:
                return child.text.strip()
        # Try without namespace
        child = elem.find(tag)
        if child is not None and child.text:
            return child.text.strip()
    return ""


def parse_rss_xml(
    xml_content: bytes,
    source_id: str,
    max_items: int = 50,
) -> List[RSSItem]:
    """
    Parse RSS 2.0 or Atom feed XML.

    Args:
        xml_content: Raw XML bytes
        source_id: Source configuration ID for tracking
        max_items: Maximum items to return

    Returns:
        List of RSSItem objects

    Raises:
        ParseError: If XML is malformed or unrecognized format
    """
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        raise ParseError(f"Invalid XML: {e}")

    items = []

    # Detect feed type and parse accordingly
    root_tag = root.tag.lower()

    # Strip namespace from tag for comparison
    if "}" in root_tag:
        root_tag = root_tag.split("}")[-1]

    # Common namespaces
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "dc": "http://purl.org/dc/elements/1.1/",
        "content": "http://purl.org/rss/1.0/modules/content/",
    }

    if root_tag == "rss":
        # RSS 2.0 format
        channel = root.find("channel")
        if channel is None:
            raise ParseError("RSS feed missing <channel> element")

        for item_elem in channel.findall("item")[:max_items]:
            title = _find_text(item_elem, ["title"])
            link = _find_text(item_elem, ["link"])
            pub_date = _find_text(item_elem, ["pubDate", "date"])
            description = _find_text(
                item_elem, ["description", "content:encoded"], ns
            )
            category = _find_text(item_elem, ["category"])
            author = _find_text(item_elem, ["author", "dc:creator"], ns)

            # Generate ID
            item_id = _generate_item_id(link, title, source_id)

            # Parse date
            published_utc = _parse_rfc822_date(pub_date)

            items.append(RSSItem(
                item_id=item_id,
                source_id=source_id,
                title=_strip_html(title),
                link=link,
                published_utc=published_utc,
                description=_strip_html(description)[:500],  # Truncate
                category=category,
                author=author,
                raw_published=pub_date,
            ))

    elif root_tag == "feed":
        # Atom format
        for entry_elem in root.findall(f"{{{ns['atom']}}}entry")[:max_items]:
            # Also try without namespace (some feeds)
            pass

        # Try both with and without namespace
        entries = root.findall(f"{{{ns['atom']}}}entry")
        if not entries:
            entries = root.findall("entry")

        for entry_elem in entries[:max_items]:
            title = _find_text(entry_elem, ["title"], ns)

            # Atom link is in attribute
            link = ""
            for link_elem in entry_elem.findall(f"{{{ns['atom']}}}link"):
                rel = link_elem.get("rel", "alternate")
                if rel == "alternate":
                    link = link_elem.get("href", "")
                    break
            if not link:
                for link_elem in entry_elem.findall("link"):
                    link = link_elem.get("href", "")
                    if link:
                        break

            pub_date = _find_text(entry_elem, ["published", "updated"], ns)
            description = _find_text(entry_elem, ["summary", "content"], ns)
            category_elem = entry_elem.find(f"{{{ns['atom']}}}category")
            category = category_elem.get("term", "") if category_elem is not None else ""
            author_elem = entry_elem.find(f"{{{ns['atom']}}}author")
            author = ""
            if author_elem is not None:
                author = _find_text(author_elem, ["name"], ns)

            item_id = _generate_item_id(link, title, source_id)
            published_utc = _parse_rfc822_date(pub_date)

            items.append(RSSItem(
                item_id=item_id,
                source_id=source_id,
                title=_strip_html(title),
                link=link,
                published_utc=published_utc,
                description=_strip_html(description)[:500],
                category=category,
                author=author,
                raw_published=pub_date,
            ))

    else:
        raise ParseError(f"Unrecognized feed format: root element is '{root.tag}'")

    return items


def parse_binance_announcements(
    json_content: bytes,
    source_id: str,
    max_items: int = 50,
    debug: bool = False,
) -> List[RSSItem]:
    """
    Parse Binance announcement API JSON response.

    Supports multiple response formats:
    1. {"data": {"articles": [...]}}
    2. {"data": {"catalogs": [{"articles": [...]}]}}
    3. {"data": [...]} (direct array)
    4. {"articles": [...]}

    Args:
        json_content: Raw JSON bytes
        source_id: Source configuration ID
        max_items: Maximum items to return
        debug: If True, print structure info for debugging

    Returns:
        List of RSSItem objects

    Raises:
        ParseError: If JSON is malformed
    """
    import json
    import sys

    try:
        data = json.loads(json_content)
    except json.JSONDecodeError as e:
        raise ParseError(f"Invalid JSON: {e}")

    items = []

    # Debug: show response structure
    if debug:
        print(f"[DEBUG] Binance response type: {type(data).__name__}", file=sys.stderr)
        if isinstance(data, dict):
            print(f"[DEBUG] Top-level keys: {list(data.keys())}", file=sys.stderr)
            if "data" in data:
                d = data["data"]
                print(f"[DEBUG] data type: {type(d).__name__}", file=sys.stderr)
                if isinstance(d, dict):
                    print(f"[DEBUG] data keys: {list(d.keys())}", file=sys.stderr)
                    if "catalogs" in d:
                        print(f"[DEBUG] catalogs count: {len(d.get('catalogs', []))}", file=sys.stderr)

    # Navigate to articles array - try multiple structures
    articles = []

    if isinstance(data, dict):
        inner = data.get("data")

        if isinstance(inner, dict):
            # Format 1: {"data": {"articles": [...]}}
            if "articles" in inner:
                articles = inner["articles"]

            # Format 2: {"data": {"catalogs": [{"articles": [...]}]}}
            elif "catalogs" in inner and isinstance(inner["catalogs"], list):
                for catalog in inner["catalogs"]:
                    if isinstance(catalog, dict) and "articles" in catalog:
                        articles.extend(catalog["articles"])

        # Format 3: {"data": [...]}
        elif isinstance(inner, list):
            articles = inner

        # Format 4: {"articles": [...]}
        elif "articles" in data:
            articles = data["articles"]

    if debug:
        print(f"[DEBUG] Found {len(articles)} articles", file=sys.stderr)

    if not isinstance(articles, list):
        raise ParseError(f"Cannot find articles array in Binance response (got {type(articles).__name__})")

    for article in articles[:max_items]:
        if not isinstance(article, dict):
            continue

        title = article.get("title", "")
        code = article.get("code", "")
        article_id = article.get("id", "")

        # Build link
        if code:
            link = f"https://www.binance.com/en/support/announcement/{code}"
        else:
            link = ""

        # Parse release date (Unix milliseconds)
        release_date = article.get("releaseDate")
        published_utc = None
        raw_published = ""
        if release_date:
            try:
                if isinstance(release_date, (int, float)):
                    # Unix milliseconds
                    published_utc = datetime.fromtimestamp(
                        release_date / 1000, tz=timezone.utc
                    )
                    raw_published = str(release_date)
            except Exception:
                pass

        # Generate item ID
        item_id = _generate_item_id(
            link or str(article_id),
            title,
            source_id
        )

        items.append(RSSItem(
            item_id=item_id,
            source_id=source_id,
            title=_strip_html(title),
            link=link,
            published_utc=published_utc,
            description="",  # Binance API doesn't include description
            category="announcement",
            author="Binance",
            raw_published=raw_published,
        ))

    return items
