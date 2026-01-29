#!/usr/bin/env python3
# === AI SIGNATURE ===
# Created by: Claude
# Created at: 2026-01-29 12:00:00 UTC
# === END SIGNATURE ===

"""
RSS Feed Health Monitor - Periodic check of feed availability.
Used for market intelligence collection in HOPE trading system.
"""

import requests
import json
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Dict, List


# Feeds approved for HOPE market intelligence
APPROVED_FEEDS = {
    "coindesk": {
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "priority": "high",
        "description": "CoinDesk - Flagship crypto news",
    },
    "cointelegraph": {
        "url": "https://cointelegraph.com/rss",
        "priority": "high",
        "description": "Cointelegraph - Major crypto news outlet",
    },
    "decrypt": {
        "url": "https://decrypt.co/feed",
        "priority": "medium",
        "description": "Decrypt - News and analysis",
    },
    "theblock": {
        "url": "https://www.theblock.co/rss.xml",
        "priority": "medium",
        "description": "The Block - Market research and data",
    },
}


def check_feed_health(url: str, timeout: int = 10) -> Dict:
    """Quick health check for a feed."""
    try:
        response = requests.get(url, timeout=timeout, allow_redirects=True)
        return {
            "status": "ok" if response.status_code == 200 else "error",
            "http_code": response.status_code,
            "xml_valid": is_valid_xml(response.content),
            "item_count": count_feed_items(response.content) if response.status_code == 200 else 0,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    except Exception as e:
        return {
            "status": "error",
            "http_code": 0,
            "xml_valid": False,
            "item_count": 0,
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }


def is_valid_xml(content: bytes) -> bool:
    """Check if content is valid XML."""
    try:
        ET.fromstring(content)
        return True
    except Exception:
        return False


def count_feed_items(content: bytes) -> int:
    """Count items in an RSS/Atom feed."""
    try:
        root = ET.fromstring(content)
        items = root.findall(".//item")
        if not items:  # Atom format
            items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
        return len(items)
    except Exception:
        return 0


def generate_health_report(results: Dict) -> str:
    """Generate a simple health report."""
    lines = [
        "=== RSS FEED HEALTH CHECK ===",
        f"Time: {datetime.utcnow().isoformat()}Z",
        "",
    ]

    all_ok = True
    for name, config in APPROVED_FEEDS.items():
        result = results.get(name, {})
        status = result.get("status", "unknown")
        http_code = result.get("http_code", "?")
        items = result.get("item_count", 0)

        status_mark = "OK" if status == "ok" else "ERROR"
        if status != "ok":
            all_ok = False

        lines.append(f"[{status_mark}] {name} ({config['priority']})")
        lines.append(f"       {config['description']}")
        lines.append(f"       HTTP {http_code}, {items} items")

    lines.append("")
    lines.append(f"Overall: {'ALL FEEDS HEALTHY' if all_ok else 'SOME FEEDS DOWN'}")
    lines.append("=" * 30)

    return "\n".join(lines)


def main():
    """Run health check on all approved feeds."""
    print("Checking RSS feed health...")
    results = {}

    for name, config in APPROVED_FEEDS.items():
        print(f"  {name}...", end=" ", flush=True)
        results[name] = check_feed_health(config["url"])
        status = results[name].get("status")
        print(status.upper())

    # Generate report
    report = generate_health_report(results)
    print("\n" + report)

    # Save results
    output_file = Path(__file__).parent.parent / "data" / "rss_health_latest.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "feeds": results,
                "all_healthy": all(r.get("status") == "ok" for r in results.values()),
            },
            f,
            indent=2,
        )

    print(f"Results saved to: {output_file}")

    # Exit with proper code
    return 0 if all(r.get("status") == "ok" for r in results.values()) else 1


if __name__ == "__main__":
    exit(main())
