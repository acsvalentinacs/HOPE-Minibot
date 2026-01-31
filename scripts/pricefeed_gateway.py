# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-01-31 13:25:00 UTC
# Purpose: HTTP Gateway for pricefeed - bridges file to HTTP API on port 8100
# === END SIGNATURE ===
"""
Pricefeed Gateway - HTTP server that serves prices from pricefeed.json file.

AutoTrader expects HTTP API on port 8100, but pricefeed_bridge writes to file.
This gateway bridges the two by serving the file contents via HTTP.
"""

import json
import logging
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
log = logging.getLogger("GATEWAY")

PROJECT_ROOT = Path(__file__).parent.parent
PRICEFEED_FILE = PROJECT_ROOT / "state" / "ai" / "pricefeed.json"


class PriceFeedHandler(BaseHTTPRequestHandler):
    """HTTP handler for pricefeed requests."""

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass

    def do_GET(self):
        """Handle GET requests."""
        if "/price" in self.path or self.path == "/":
            try:
                if not PRICEFEED_FILE.exists():
                    self.send_response(503)
                    self.end_headers()
                    self.wfile.write(b'{"error": "pricefeed not found"}')
                    return

                data = json.loads(PRICEFEED_FILE.read_text(encoding='utf-8'))
                response = {
                    "prices": data.get("prices", {}),
                    "count": len(data.get("prices", {})),
                    "timestamp": data.get("produced_unix", 0)
                }

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response).encode('utf-8'))

            except Exception as e:
                log.error(f"Error serving prices: {e}")
                self.send_response(503)
                self.end_headers()
                self.wfile.write(f'{{"error": "{str(e)}"}}'.encode('utf-8'))
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')

    def do_POST(self):
        """Handle POST requests (for subscribe endpoint)."""
        if "/subscribe" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "subscribed"}')
        else:
            self.send_response(404)
            self.end_headers()


def main():
    host = "127.0.0.1"
    port = 8100

    server = HTTPServer((host, port), PriceFeedHandler)
    log.info(f"Pricefeed Gateway started on http://{host}:{port}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Gateway stopped")
        server.shutdown()


if __name__ == "__main__":
    main()
