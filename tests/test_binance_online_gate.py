# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-25 10:30:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-25 11:00:00 UTC
# === END SIGNATURE ===
"""
Binance Online Gate Test - Production-level standalone pytest test.

NO internal imports (core.*, nore.*) - works independently of broken modules.
Uses ONLY Python stdlib.

Features:
- Test A: Public endpoint (no keys required)
- Test B: Private endpoint (keys required, SKIP if absent)
- Explicit timeout (10s) with retry on network errors
- Sanitized errors (no signature leakage)
- Rate-limit awareness (429/418 detection)
- Network gate detection (DNS/TLS classification)
- Evidence pack with atomic writes

Evidence pack created on each run:
  state/audit/binance_online_gate/<UTC_YYYYMMDD_HHMMSS>/report.json
  state/audit/binance_online_gate/<UTC_YYYYMMDD_HHMMSS>/report.json.sha256
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

# ============================================================================
# CONSTANTS
# ============================================================================

# Default to mainnet, can override via env BINANCE_BASE_URL
DEFAULT_BASE_URL = "https://api.binance.com"
PUBLIC_ENDPOINT = "/api/v3/time"
PRIVATE_ENDPOINT = "/api/v3/account"

# Timeouts and retry
HTTP_TIMEOUT_SEC = 10
RECV_WINDOW_MS = 5000
MAX_RETRIES = 2  # 1 initial + 1 retry
RETRY_BACKOFF_MIN = 0.3
RETRY_BACKOFF_MAX = 0.7

# Retryable HTTP status codes
RETRYABLE_STATUS = frozenset({502, 503, 504})

# Rate limit status codes (special handling)
RATE_LIMIT_STATUS = frozenset({418, 429})

# Secrets file (READ-ONLY!)
SECRETS_PATH = Path(r"C:\secrets\hope\.env")

# Report output directory
PROJECT_ROOT = Path(__file__).parent.parent
AUDIT_DIR = PROJECT_ROOT / "state" / "audit" / "binance_online_gate"

# Key pairs to check (in priority order)
KEY_PAIRS = [
    ("BINANCE_MAINNET_API_KEY", "BINANCE_MAINNET_API_SECRET"),
    ("BINANCE_API_KEY", "BINANCE_API_SECRET"),
]

# Pattern to detect signature in error messages (MUST NOT leak)
SIGNATURE_PATTERN = re.compile(r"signature=[a-fA-F0-9]+", re.IGNORECASE)


# ============================================================================
# ERROR CLASSIFICATION
# ============================================================================

class ErrorClass:
    """Error classification for fail-closed reporting."""
    DNS = "DNS"
    TLS = "TLS"
    TIMEOUT = "TIMEOUT"
    CONNECTION = "CONNECTION"
    HTTP = "HTTP"
    JSON = "JSON"
    RATE_LIMIT = "RATE_LIMIT"
    AUTH = "AUTH"
    UNKNOWN = "UNKNOWN"


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class PublicTestResult:
    url: str = ""
    ok: bool = False
    status_code: Optional[int] = None
    latency_ms: Optional[int] = None
    serverTime_present: bool = False
    timeout_s: int = HTTP_TIMEOUT_SEC
    attempts: int = 0
    effective_host: Optional[str] = None
    error: Optional[str] = None
    error_class: Optional[str] = None


@dataclass
class PrivateTestResult:
    attempted: bool = False
    skipped_reason: Optional[str] = None
    ok: bool = False
    status_code: Optional[int] = None
    latency_ms: Optional[int] = None
    timeout_s: int = HTTP_TIMEOUT_SEC
    attempts: int = 0
    effective_host: Optional[str] = None
    key_present: bool = False
    secret_present: bool = False
    key_length: int = 0
    secret_length: int = 0
    top_level_fields: List[str] = field(default_factory=list)
    error: Optional[str] = None
    error_class: Optional[str] = None


@dataclass
class GateReport:
    utc: str = ""
    python_exe: str = ""
    base_url: str = ""
    public: PublicTestResult = field(default_factory=PublicTestResult)
    private: PrivateTestResult = field(default_factory=PrivateTestResult)
    verdict: str = "FAIL"


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_base_url() -> str:
    """Get Binance base URL (supports testnet override via env)."""
    return os.environ.get("BINANCE_BASE_URL", DEFAULT_BASE_URL)


def atomic_write(path: Path, content: str) -> None:
    """Atomic write: temp file -> fsync -> replace. No .tmp left behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        # Ensure no .tmp left behind
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def load_env_file(path: Path) -> Dict[str, str]:
    """Load .env file without modifying it. Returns key-value dict."""
    result = {}
    if not path.exists():
        return result

    try:
        content = path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    except Exception:
        pass

    return result


def get_binance_keys() -> Tuple[Optional[str], Optional[str], str, str]:
    """
    Get Binance API keys from environment or .env file.

    Returns: (api_key, api_secret, key_source, pair_name)
    Never prints/logs actual values!
    """
    # First try process environment
    for key_name, secret_name in KEY_PAIRS:
        api_key = os.environ.get(key_name)
        api_secret = os.environ.get(secret_name)
        if api_key and api_secret:
            return api_key, api_secret, "process_env", key_name

    # Then try .env file
    env_vars = load_env_file(SECRETS_PATH)
    for key_name, secret_name in KEY_PAIRS:
        api_key = env_vars.get(key_name)
        api_secret = env_vars.get(secret_name)
        if api_key and api_secret:
            return api_key, api_secret, "env_file", key_name

    return None, None, "not_found", ""


def sanitize_error(error_msg: str) -> str:
    """
    Sanitize error message to prevent signature/secret leakage.

    Removes:
    - signature=xxx from URLs
    - Full query strings
    - Any potential secret patterns
    """
    if not error_msg:
        return error_msg

    # Remove signature parameter
    sanitized = SIGNATURE_PATTERN.sub("signature=[REDACTED]", error_msg)

    # Remove full URLs with query strings (keep only host/path)
    url_pattern = re.compile(r"https?://[^\s]+\?[^\s]+")
    def replace_url(match):
        url = match.group(0)
        parsed = urllib.parse.urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?[PARAMS_REDACTED]"

    sanitized = url_pattern.sub(replace_url, sanitized)

    return sanitized


def classify_error(exc: Exception) -> Tuple[str, str]:
    """
    Classify exception into error_class and sanitized message.

    Returns: (error_class, sanitized_message)
    """
    error_msg = str(exc)

    # DNS errors
    if isinstance(exc, socket.gaierror):
        return ErrorClass.DNS, f"DNS resolution failed: {sanitize_error(error_msg)}"

    # Timeout errors
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return ErrorClass.TIMEOUT, f"Request timeout ({HTTP_TIMEOUT_SEC}s)"

    # SSL/TLS errors
    if isinstance(exc, ssl.SSLError):
        return ErrorClass.TLS, f"TLS/SSL error: {sanitize_error(error_msg)}"

    # Connection errors
    if isinstance(exc, (ConnectionError, OSError)) and not isinstance(exc, socket.gaierror):
        return ErrorClass.CONNECTION, f"Connection error: {sanitize_error(error_msg)}"

    # HTTP errors
    if isinstance(exc, urllib.error.HTTPError):
        status = exc.code
        if status in RATE_LIMIT_STATUS:
            return ErrorClass.RATE_LIMIT, f"Rate limit/block: HTTP {status}"
        if status in (401, 403):
            return ErrorClass.AUTH, f"Authentication error: HTTP {status}"
        return ErrorClass.HTTP, f"HTTP error: {status}"

    # URL errors (network issues)
    if isinstance(exc, urllib.error.URLError):
        reason = str(exc.reason) if exc.reason else error_msg
        if "getaddrinfo" in reason.lower() or "name or service" in reason.lower():
            return ErrorClass.DNS, f"DNS error: {sanitize_error(reason)}"
        if "ssl" in reason.lower() or "certificate" in reason.lower():
            return ErrorClass.TLS, f"TLS error: {sanitize_error(reason)}"
        if "timed out" in reason.lower():
            return ErrorClass.TIMEOUT, f"Timeout: {sanitize_error(reason)}"
        return ErrorClass.CONNECTION, f"URL error: {sanitize_error(reason)}"

    # JSON decode errors
    if isinstance(exc, json.JSONDecodeError):
        return ErrorClass.JSON, f"Invalid JSON response: {exc.msg}"

    return ErrorClass.UNKNOWN, sanitize_error(error_msg)


def http_get_with_retry(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    max_retries: int = MAX_RETRIES
) -> Tuple[int, bytes, int, str, int]:
    """
    Perform HTTP GET with retry on network errors.

    Returns: (status_code, body, latency_ms, effective_host, attempts)
    Raises: Exception on final failure (after retries)
    """
    headers = headers or {}
    headers.setdefault("User-Agent", "HOPE-Binance-Gate/2.0")
    headers.setdefault("Accept", "application/json")

    # SSL context with cert verification
    ctx = ssl.create_default_context()

    last_exception = None
    attempts = 0

    for attempt in range(max_retries):
        attempts = attempt + 1

        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            start_ms = int(time.time() * 1000)

            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC, context=ctx) as resp:
                body = resp.read()
                status = resp.status
                effective_url = resp.geturl()
                effective_host = urllib.parse.urlparse(effective_url).netloc

            latency_ms = int(time.time() * 1000) - start_ms
            return status, body, latency_ms, effective_host, attempts

        except urllib.error.HTTPError as e:
            status = e.code
            body = e.read() if hasattr(e, "read") else b""
            latency_ms = int(time.time() * 1000) - start_ms
            effective_host = urllib.parse.urlparse(url).netloc

            # Retry on 502/503/504
            if status in RETRYABLE_STATUS and attempt < max_retries - 1:
                backoff = random.uniform(RETRY_BACKOFF_MIN, RETRY_BACKOFF_MAX)
                time.sleep(backoff)
                last_exception = e
                continue

            # Non-retryable HTTP error
            return status, body, latency_ms, effective_host, attempts

        except (socket.timeout, TimeoutError, socket.gaierror, ssl.SSLError,
                ConnectionError, urllib.error.URLError) as e:
            # Network errors - retry with backoff
            if attempt < max_retries - 1:
                backoff = random.uniform(RETRY_BACKOFF_MIN, RETRY_BACKOFF_MAX)
                time.sleep(backoff)
                last_exception = e
                continue
            raise e

    # Should not reach here, but just in case
    if last_exception:
        raise last_exception
    raise RuntimeError("Unexpected retry loop exit")


def sign_request(params: Dict[str, Any], secret: str) -> str:
    """Generate HMAC-SHA256 signature for Binance API."""
    query_string = urllib.parse.urlencode(params)
    signature = hmac.new(
        secret.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return signature


def compute_sha256(content: str) -> str:
    """Compute SHA256 hex digest of string content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def get_utc_timestamp() -> str:
    """Get stable UTC timestamp in YYYYMMDD_HHMMSS format (no colons)."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def save_report(report: GateReport) -> Tuple[Path, Path]:
    """
    Save report.json and report.json.sha256 atomically.

    Returns: (report_path, sha256_path)
    """
    # Create timestamped directory (no colons in name)
    ts = get_utc_timestamp()
    report_dir = AUDIT_DIR / ts
    report_dir.mkdir(parents=True, exist_ok=True)

    report_path = report_dir / "report.json"
    sha256_path = report_dir / "report.json.sha256"

    # Convert to JSON
    report_dict = {
        "utc": report.utc,
        "python_exe": report.python_exe,
        "base_url": report.base_url,
        "public": asdict(report.public),
        "private": asdict(report.private),
        "verdict": report.verdict,
    }
    report_json = json.dumps(report_dict, indent=2, ensure_ascii=False)

    # Write atomically
    atomic_write(report_path, report_json)
    atomic_write(sha256_path, compute_sha256(report_json))

    return report_path, sha256_path


# ============================================================================
# PYTEST MARKERS
# ============================================================================

# Mark all tests as network-dependent
pytestmark = pytest.mark.network


# ============================================================================
# TEST IMPLEMENTATION
# ============================================================================

# Global report for this test session
_session_report: Optional[GateReport] = None


def get_or_create_report() -> GateReport:
    """Get or create session report."""
    global _session_report
    if _session_report is None:
        _session_report = GateReport(
            utc=datetime.now(timezone.utc).isoformat(),
            python_exe=sys.executable,
            base_url=get_base_url(),
        )
    return _session_report


class TestBinanceOnlineGate:
    """Binance Online Connectivity Tests (Production-level)."""

    def test_a_public_time(self) -> None:
        """
        Test A: Public endpoint - GET /api/v3/time

        PASS: HTTP 200 AND serverTime is int > 0
        FAIL: Any other result (network error, bad JSON, wrong data)

        Markers: @pytest.mark.network
        """
        report = get_or_create_report()
        result = report.public

        base_url = get_base_url()
        url = f"{base_url}{PUBLIC_ENDPOINT}"
        result.url = url
        result.timeout_s = HTTP_TIMEOUT_SEC

        try:
            status, body, latency, effective_host, attempts = http_get_with_retry(url)

            result.status_code = status
            result.latency_ms = latency
            result.effective_host = effective_host
            result.attempts = attempts

            if status == 200:
                # Validate JSON response
                try:
                    data = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError as je:
                    result.ok = False
                    result.error_class = ErrorClass.JSON
                    result.error = f"Invalid JSON: {je.msg}"
                    raise AssertionError(result.error)

                server_time = data.get("serverTime")
                result.serverTime_present = isinstance(server_time, int) and server_time > 0
                result.ok = result.serverTime_present

                if not result.ok:
                    result.error_class = ErrorClass.JSON
                    result.error = "serverTime missing or invalid"
            elif status in RATE_LIMIT_STATUS:
                result.ok = False
                result.error_class = ErrorClass.RATE_LIMIT
                result.error = f"Rate limit/block: HTTP {status}"
            else:
                result.ok = False
                result.error_class = ErrorClass.HTTP
                result.error = f"HTTP {status}"

        except Exception as e:
            if result.error_class is None:  # Not already classified
                result.error_class, result.error = classify_error(e)
            result.ok = False

        # Print status (no secrets possible here)
        print(f"\n[TEST A] Public endpoint: {url}")
        print(f"  Attempts: {result.attempts}")
        print(f"  Status: {result.status_code}")
        print(f"  Latency: {result.latency_ms}ms")
        print(f"  Effective host: {result.effective_host}")
        print(f"  serverTime present: {result.serverTime_present}")
        print(f"  Result: {'PASS' if result.ok else 'FAIL'}")
        if result.error:
            print(f"  Error: [{result.error_class}] {result.error}")

        assert result.ok, f"Public endpoint failed: [{result.error_class}] {result.error}"

    @pytest.mark.private
    def test_b_private_account(self) -> None:
        """
        Test B: Private endpoint - GET /api/v3/account (signed)

        SKIP: No API keys found
        PASS: HTTP 200 AND (accountType OR balances in response)
        FAIL: HTTP error or missing required fields

        Markers: @pytest.mark.network, @pytest.mark.private
        """
        report = get_or_create_report()
        result = report.private
        result.timeout_s = HTTP_TIMEOUT_SEC

        # Get keys (never print values!)
        api_key, api_secret, source, pair_name = get_binance_keys()

        result.key_present = api_key is not None
        result.secret_present = api_secret is not None
        result.key_length = len(api_key) if api_key else 0
        result.secret_length = len(api_secret) if api_secret else 0

        # Print key status (lengths only, NEVER values)
        print(f"\n[TEST B] Private endpoint check")
        print(f"  Key present: {result.key_present} (length={result.key_length})")
        print(f"  Secret present: {result.secret_present} (length={result.secret_length})")

        if not api_key or not api_secret:
            result.attempted = False
            result.skipped_reason = "no_keys"
            print(f"  SKIP: No API keys found in env or {SECRETS_PATH}")
            pytest.skip("No Binance API keys available")
            return

        result.attempted = True
        print(f"  Key source: {source} ({pair_name})")

        # Build signed request
        base_url = get_base_url()
        timestamp = int(time.time() * 1000)
        params = {
            "timestamp": timestamp,
            "recvWindow": RECV_WINDOW_MS,
        }
        signature = sign_request(params, api_secret)
        params["signature"] = signature

        url = f"{base_url}{PRIVATE_ENDPOINT}?{urllib.parse.urlencode(params)}"
        headers = {"X-MBX-APIKEY": api_key}

        try:
            status, body, latency, effective_host, attempts = http_get_with_retry(
                url, headers, max_retries=MAX_RETRIES
            )

            result.status_code = status
            result.latency_ms = latency
            result.effective_host = effective_host
            result.attempts = attempts

            if status == 200:
                # Validate JSON response
                try:
                    data = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError as je:
                    result.ok = False
                    result.error_class = ErrorClass.JSON
                    result.error = f"Invalid JSON: {je.msg}"
                    raise AssertionError(result.error)

                result.top_level_fields = list(data.keys())[:10]  # First 10 fields only

                # Check for expected fields
                has_account_type = "accountType" in data
                has_balances = "balances" in data
                result.ok = has_account_type or has_balances

                if not result.ok:
                    result.error_class = ErrorClass.JSON
                    result.error = "Missing accountType and balances"

            elif status in RATE_LIMIT_STATUS:
                result.ok = False
                result.error_class = ErrorClass.RATE_LIMIT
                result.error = f"Rate limit/block: HTTP {status}"
            elif status in (401, 403):
                result.ok = False
                result.error_class = ErrorClass.AUTH
                result.error = f"Authentication failed: HTTP {status}"
            else:
                result.ok = False
                result.error_class = ErrorClass.HTTP
                try:
                    err_data = json.loads(body.decode("utf-8"))
                    # Sanitize error message (no signature leak)
                    msg = err_data.get("msg", "unknown")
                    result.error = f"HTTP {status}: {sanitize_error(msg)}"
                except Exception:
                    result.error = f"HTTP {status}"

        except Exception as e:
            if result.error_class is None:
                result.error_class, result.error = classify_error(e)
            result.ok = False

        # Print result (no secrets!)
        print(f"  Attempts: {result.attempts}")
        print(f"  Status: {result.status_code}")
        print(f"  Latency: {result.latency_ms}ms")
        print(f"  Effective host: {result.effective_host}")
        print(f"  Top-level fields: {result.top_level_fields}")
        print(f"  Result: {'PASS' if result.ok else 'FAIL'}")
        if result.error:
            print(f"  Error: [{result.error_class}] {result.error}")

        assert result.ok, f"Private endpoint failed: [{result.error_class}] {result.error}"


@pytest.fixture(scope="session", autouse=True)
def finalize_report(request):
    """Save report after all tests complete."""
    yield

    report = get_or_create_report()

    # Compute verdict
    public_ok = report.public.ok
    private_ok = report.private.ok
    private_skipped = not report.private.attempted

    if public_ok and (private_ok or private_skipped):
        report.verdict = "PASS"
    else:
        report.verdict = "FAIL"

    # Save report
    try:
        report_path, sha256_path = save_report(report)
        print(f"\n{'='*60}")
        print(f"VERDICT: {report.verdict}")
        print(f"Report: {report_path}")
        print(f"SHA256: {sha256_path}")
        print(f"{'='*60}")
    except Exception as e:
        print(f"\nWARNING: Failed to save report: {e}")


# ============================================================================
# CLI ENTRYPOINT (for direct execution)
# ============================================================================

if __name__ == "__main__":
    # Allow running directly: python tests/test_binance_online_gate.py
    pytest.main([__file__, "-v"])
