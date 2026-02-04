# === AI SIGNATURE ===
# Module: scripts/signal_forwarder.py
# Created by: Claude (opus-4.5)
# Created at: 2026-02-04 22:40:00 UTC
# Purpose: Forward MoonBot signals to HOPE Core v2.0 API
# === END SIGNATURE ===
"""
Signal Forwarder - Bridge between MoonBot and HOPE Core v2.0

Routes signals from MoonBot watcher to HOPE Core API for processing
through Command Bus + State Machine architecture.

Usage:
    # Import and use in moonbot_watcher.py
    from signal_forwarder import SignalForwarder

    forwarder = SignalForwarder()
    result = forwarder.forward(signal_data)
"""

import json
import time
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from pathlib import Path

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    import urllib.request
    import urllib.error

log = logging.getLogger(__name__)


class SignalForwarder:
    """
    Forwards MoonBot signals to HOPE Core v2.0 API.

    Features:
    - Automatic retry on failure
    - Health check before forwarding
    - Fallback to existing autotrader if hope-core unavailable
    """

    def __init__(
        self,
        hope_core_url: str = "http://127.0.0.1:8201",
        autotrader_url: str = "http://127.0.0.1:8200",
        timeout: float = 5.0,
        max_retries: int = 2,
        prefer_hope_core: bool = True,
    ):
        """
        Initialize Signal Forwarder.

        Args:
            hope_core_url: URL of HOPE Core v2.0 API
            autotrader_url: URL of existing autotrader API (fallback)
            timeout: Request timeout in seconds
            max_retries: Max retry attempts
            prefer_hope_core: If True, try hope-core first, fallback to autotrader
        """
        self.hope_core_url = hope_core_url
        self.autotrader_url = autotrader_url
        self.timeout = timeout
        self.max_retries = max_retries
        self.prefer_hope_core = prefer_hope_core

        # Stats
        self.stats = {
            "forwarded": 0,
            "to_hope_core": 0,
            "to_autotrader": 0,
            "failed": 0,
            "last_forward": None,
        }

        # HTTP client
        if HTTPX_AVAILABLE:
            self._client = httpx.Client(timeout=timeout)
        else:
            self._client = None

        log.info(f"SignalForwarder initialized: hope_core={hope_core_url}, autotrader={autotrader_url}")

    def _request(self, method: str, url: str, json_data: Optional[Dict] = None) -> Dict:
        """Make HTTP request."""
        if HTTPX_AVAILABLE:
            try:
                if method == "GET":
                    resp = self._client.get(url)
                else:
                    resp = self._client.post(url, json=json_data)
                return {"status": resp.status_code, "data": resp.json() if resp.text else {}}
            except Exception as e:
                return {"status": 0, "error": str(e)}
        else:
            # Fallback to urllib
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(json_data).encode() if json_data else None,
                    headers={"Content-Type": "application/json"},
                    method=method,
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode())
                    return {"status": resp.status, "data": data}
            except Exception as e:
                return {"status": 0, "error": str(e)}

    def check_hope_core_health(self) -> bool:
        """Check if HOPE Core is healthy."""
        try:
            result = self._request("GET", f"{self.hope_core_url}/api/health")
            if result.get("status") == 200:
                data = result.get("data", {})
                return data.get("status") == "healthy"
        except Exception:
            pass
        return False

    def check_autotrader_health(self) -> bool:
        """Check if autotrader is healthy."""
        try:
            result = self._request("GET", f"{self.autotrader_url}/status")
            return result.get("status") == 200
        except Exception:
            pass
        return False

    def forward_to_hope_core(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """
        Forward signal to HOPE Core v2.0.

        Args:
            signal: Signal data with symbol, score, source, etc.

        Returns:
            Result dict with success status
        """
        # Extract/normalize signal fields
        symbol = signal.get("symbol", "")
        score = signal.get("score") or signal.get("confidence", 0.5)
        source = signal.get("source", "moonbot")

        # Additional data for HOPE Core
        payload = {
            "symbol": symbol,
            "score": float(score),
            "source": source,
            "raw_data": signal,  # Include full signal for Eye of God
        }

        # Send to hope-core /signal/external endpoint
        result = self._request("POST", f"{self.hope_core_url}/signal/external", payload)

        if result.get("status") == 200:
            self.stats["to_hope_core"] += 1
            return {"success": True, "target": "hope_core", "result": result.get("data")}
        else:
            return {"success": False, "error": result.get("error", f"HTTP {result.get('status')}")}

    def forward_to_autotrader(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """
        Forward signal to existing autotrader.

        Args:
            signal: Signal data

        Returns:
            Result dict with success status
        """
        result = self._request("POST", f"{self.autotrader_url}/signal", signal)

        if result.get("status") == 200:
            self.stats["to_autotrader"] += 1
            return {"success": True, "target": "autotrader", "result": result.get("data")}
        else:
            return {"success": False, "error": result.get("error", f"HTTP {result.get('status')}")}

    def forward(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """
        Forward signal to best available target.

        Tries HOPE Core first if prefer_hope_core=True, falls back to autotrader.

        Args:
            signal: Signal data from MoonBot

        Returns:
            Result dict with success, target, and result
        """
        symbol = signal.get("symbol", "UNKNOWN")

        # Try preferred target first
        if self.prefer_hope_core:
            targets = [
                ("hope_core", self.forward_to_hope_core, self.check_hope_core_health),
                ("autotrader", self.forward_to_autotrader, self.check_autotrader_health),
            ]
        else:
            targets = [
                ("autotrader", self.forward_to_autotrader, self.check_autotrader_health),
                ("hope_core", self.forward_to_hope_core, self.check_hope_core_health),
            ]

        last_error = None

        for target_name, forward_fn, health_fn in targets:
            # Check health first
            if not health_fn():
                log.debug(f"[FORWARDER] {target_name} not healthy, skipping")
                continue

            # Try to forward with retries
            for attempt in range(self.max_retries):
                result = forward_fn(signal)

                if result.get("success"):
                    self.stats["forwarded"] += 1
                    self.stats["last_forward"] = datetime.now(timezone.utc).isoformat()
                    log.info(f"[FORWARDER] {symbol} -> {target_name} (attempt {attempt + 1})")
                    return result

                last_error = result.get("error")
                time.sleep(0.1 * (attempt + 1))  # Brief backoff

        # All targets failed
        self.stats["failed"] += 1
        log.error(f"[FORWARDER] FAILED to forward {symbol}: {last_error}")
        return {"success": False, "error": last_error or "All targets unavailable"}

    def get_stats(self) -> Dict[str, Any]:
        """Get forwarding statistics."""
        return self.stats.copy()


# Global instance for easy import
_forwarder: Optional[SignalForwarder] = None


def get_forwarder() -> SignalForwarder:
    """Get or create global SignalForwarder instance."""
    global _forwarder
    if _forwarder is None:
        _forwarder = SignalForwarder()
    return _forwarder


def forward_signal(signal: Dict[str, Any]) -> Dict[str, Any]:
    """Convenience function to forward a signal."""
    return get_forwarder().forward(signal)


# =============================================================================
# SELF TEST
# =============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("=" * 50)
    print("  Signal Forwarder Test")
    print("=" * 50)

    forwarder = SignalForwarder()

    # Check health
    print("\n[1] Health Checks:")
    print(f"    HOPE Core: {'OK' if forwarder.check_hope_core_health() else 'UNAVAILABLE'}")
    print(f"    Autotrader: {'OK' if forwarder.check_autotrader_health() else 'UNAVAILABLE'}")

    # Test signal
    print("\n[2] Test Forward:")
    test_signal = {
        "symbol": "BTCUSDT",
        "score": 0.75,
        "source": "test",
        "buys_per_sec": 50,
        "delta_pct": 2.5,
    }

    result = forwarder.forward(test_signal)
    print(f"    Result: {result}")

    # Stats
    print("\n[3] Stats:")
    stats = forwarder.get_stats()
    for k, v in stats.items():
        print(f"    {k}: {v}")

    print("\n" + "=" * 50)
    print("  Test Complete")
    print("=" * 50)
