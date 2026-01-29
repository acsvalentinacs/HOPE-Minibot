#!/usr/bin/env python3
# === AI SIGNATURE ===
# Created by: Claude
# Created at: 2026-01-29 12:00:00 UTC
# === END SIGNATURE ===

import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Optional
import json
from pathlib import Path


@dataclass
class FeedReport:
    url: str
    http_status: int
    status_reason: str
    content_type: Optional[str]
    is_valid_xml: bool
    is_valid_feed: bool
    item_count: int
    most_recent_date: Optional[str]
    error_message: Optional[str]
    result: str  # PASS or FAIL


def test_feed(url: str, timeout: int = 10) -> FeedReport:
    """Test a single RSS/Atom feed."""
    try:
        response = requests.get(url, timeout=timeout, allow_redirects=True)
        http_status = response.status_code
        status_reason = response.reason
        content_type = response.headers.get("content-type", "").split(";")[0]

        if http_status != 200:
            return FeedReport(
                url=url,
                http_status=http_status,
                status_reason=status_reason,
                content_type=content_type,
                is_valid_xml=False,
                is_valid_feed=False,
                item_count=0,
                most_recent_date=None,
                error_message=f"HTTP {http_status}: {status_reason}",
                result="FAIL",
            )

        # Check content type
        is_xml_type = "xml" in content_type.lower() or "rss" in content_type.lower() or "atom" in content_type.lower()

        # Try to parse XML
        try:
            root = ET.fromstring(response.content)
            is_valid_xml = True
        except ET.ParseError as e:
            return FeedReport(
                url=url,
                http_status=http_status,
                status_reason=status_reason,
                content_type=content_type,
                is_valid_xml=False,
                is_valid_feed=False,
                item_count=0,
                most_recent_date=None,
                error_message=f"XML parse error: {str(e)[:100]}",
                result="FAIL",
            )

        # Check if it's a valid RSS or Atom feed
        tag = root.tag.lower()
        is_rss = "rss" in tag
        is_atom = "feed" in tag and "atom" in tag

        if not (is_rss or is_atom):
            return FeedReport(
                url=url,
                http_status=http_status,
                status_reason=status_reason,
                content_type=content_type,
                is_valid_xml=True,
                is_valid_feed=False,
                item_count=0,
                most_recent_date=None,
                error_message=f"Not a valid RSS/Atom feed (root tag: {root.tag})",
                result="FAIL",
            )

        # Count items
        if is_rss:
            items = root.findall(".//item")
        else:  # Atom
            items = root.findall(".//{http://www.w3.org/2005/Atom}entry")

        item_count = len(items)

        # Get most recent date
        most_recent_date = None
        if items:
            if is_rss:
                # RSS: look for <pubDate> or <dc:date>
                for item in items:
                    pub_date = item.findtext("pubDate")
                    if pub_date:
                        most_recent_date = pub_date
                        break
            else:
                # Atom: look for <updated>
                for entry in items:
                    updated = entry.findtext("{http://www.w3.org/2005/Atom}updated")
                    if updated:
                        most_recent_date = updated
                        break

        result = "PASS" if (http_status == 200 and is_valid_xml) else "FAIL"

        return FeedReport(
            url=url,
            http_status=http_status,
            status_reason=status_reason,
            content_type=content_type,
            is_valid_xml=is_valid_xml,
            is_valid_feed=is_rss or is_atom,
            item_count=item_count,
            most_recent_date=most_recent_date,
            error_message=None,
            result=result,
        )

    except requests.Timeout:
        return FeedReport(
            url=url,
            http_status=0,
            status_reason="Timeout",
            content_type=None,
            is_valid_xml=False,
            is_valid_feed=False,
            item_count=0,
            most_recent_date=None,
            error_message=f"Request timeout after 10s",
            result="FAIL",
        )
    except requests.RequestException as e:
        return FeedReport(
            url=url,
            http_status=0,
            status_reason="Error",
            content_type=None,
            is_valid_xml=False,
            is_valid_feed=False,
            item_count=0,
            most_recent_date=None,
            error_message=f"Request error: {str(e)[:100]}",
            result="FAIL",
        )
    except Exception as e:
        return FeedReport(
            url=url,
            http_status=0,
            status_reason="Error",
            content_type=None,
            is_valid_xml=False,
            is_valid_feed=False,
            item_count=0,
            most_recent_date=None,
            error_message=f"Unexpected error: {str(e)[:100]}",
            result="FAIL",
        )


def main():
    feeds = [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
        "https://decrypt.co/feed",
        "https://www.theblock.co/rss.xml",
        "https://bitcoinmagazine.com/feed",
    ]

    print("=" * 100)
    print("RSS FEED AVAILABILITY TEST")
    print("=" * 100)
    print()

    reports = []
    for feed_url in feeds:
        print(f"Testing: {feed_url}")
        report = test_feed(feed_url)
        reports.append(report)

        print(f"  Status:        {report.http_status} {report.status_reason}")
        print(f"  Content-Type:  {report.content_type or 'N/A'}")
        print(f"  Valid XML:     {report.is_valid_xml}")
        print(f"  Valid Feed:    {report.is_valid_feed}")
        print(f"  Item Count:    {report.item_count}")
        print(f"  Most Recent:   {report.most_recent_date or 'N/A'}")
        if report.error_message:
            print(f"  Error:         {report.error_message}")
        print(f"  Result:        {report.result}")
        print()

    # Summary
    print("=" * 100)
    print("SUMMARY")
    print("=" * 100)
    passed = sum(1 for r in reports if r.result == "PASS")
    failed = sum(1 for r in reports if r.result == "FAIL")
    print(f"PASSED: {passed}/{len(reports)}")
    print(f"FAILED: {failed}/{len(reports)}")
    print()

    # Detailed results table
    print("=" * 100)
    print("DETAILED RESULTS")
    print("=" * 100)
    print(f"{'URL':<50} {'Status':<10} {'XML':<6} {'Feed':<6} {'Items':<8} {'Result':<6}")
    print("-" * 100)
    for r in reports:
        url_short = r.url[:48] + ".." if len(r.url) > 50 else r.url
        xml_mark = "YES" if r.is_valid_xml else "NO"
        feed_mark = "YES" if r.is_valid_feed else "NO"
        print(
            f"{url_short:<50} {str(r.http_status):<10} "
            f"{xml_mark:<6} "
            f"{feed_mark:<6} "
            f"{str(r.item_count):<8} "
            f"{r.result:<6}"
        )

    # Save to JSON
    output_file = Path(__file__).parent.parent / "data" / "rss_feed_test_results.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)

    report_dicts = [asdict(r) for r in reports]
    summary = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "total_feeds": len(reports),
        "passed": passed,
        "failed": failed,
        "feeds": report_dicts,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print()
    print(f"Results saved to: {output_file}")


if __name__ == "__main__":
    main()
