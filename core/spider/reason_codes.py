# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T19:30:00Z
# Purpose: Spider reason_code closed dictionary (fail-closed)
# === END SIGNATURE ===
"""
Spider Reason Codes Module

Closed dictionary of all valid reason_codes for source failures.
UNKNOWN_ERROR is forbidden in enforced mode.

Categories:
- POLICY: before network I/O (allowlist, URL policy)
- FETCH: network/HTTP errors
- PARSE: content parsing errors
- NORMALIZE/DEDUP/WRITE: processing errors
- CLASSIFY/PUBLISH: downstream errors

Each reason_code has:
- stage: where error occurred
- retryable: whether retry makes sense
- severity: error/warning/info
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional, Dict, Any


class Stage(str, Enum):
    """Error stage enum."""
    POLICY = "POLICY"
    FETCH = "FETCH"
    PARSE = "PARSE"
    NORMALIZE = "NORMALIZE"
    DEDUP = "DEDUP"
    WRITE = "WRITE"
    CLASSIFY = "CLASSIFY"
    PUBLISH = "PUBLISH"


class ReasonCode(str, Enum):
    """
    Closed dictionary of reason codes.

    Format: CATEGORY_SPECIFIC_ERROR
    """
    # === POLICY (before network I/O) ===
    POLICY_ALLOWLIST_MISSING = "POLICY_ALLOWLIST_MISSING"
    POLICY_ALLOWLIST_UNREADABLE = "POLICY_ALLOWLIST_UNREADABLE"
    POLICY_ALLOWLIST_HASH_FAIL = "POLICY_ALLOWLIST_HASH_FAIL"
    POLICY_URL_NOT_ALLOWED = "POLICY_URL_NOT_ALLOWED"
    POLICY_SCHEME_NOT_ALLOWED = "POLICY_SCHEME_NOT_ALLOWED"
    POLICY_REDIRECT_NOT_ALLOWED = "POLICY_REDIRECT_NOT_ALLOWED"
    POLICY_EVIDENCE_WRITE_FAIL = "POLICY_EVIDENCE_WRITE_FAIL"

    # === FETCH (network/HTTP) ===
    DNS_FAIL = "DNS_FAIL"
    CONNECT_TIMEOUT = "CONNECT_TIMEOUT"
    TLS_HANDSHAKE_FAIL = "TLS_HANDSHAKE_FAIL"
    HTTP_TIMEOUT = "HTTP_TIMEOUT"
    HTTP_STATUS_4XX = "HTTP_STATUS_4XX"
    HTTP_STATUS_5XX = "HTTP_STATUS_5XX"
    HTTP_TOO_MANY_REDIRECTS = "HTTP_TOO_MANY_REDIRECTS"
    HTTP_REDIRECT_LOOP = "HTTP_REDIRECT_LOOP"
    HTTP_INVALID_RESPONSE = "HTTP_INVALID_RESPONSE"
    CONTENT_TOO_LARGE = "CONTENT_TOO_LARGE"
    RATE_LIMITED = "RATE_LIMITED"
    REMOTE_CLOSED = "REMOTE_CLOSED"

    # === PARSE (content parsing) ===
    PARSE_INVALID_XML = "PARSE_INVALID_XML"
    PARSE_INVALID_JSON = "PARSE_INVALID_JSON"
    PARSE_INVALID_ENCODING = "PARSE_INVALID_ENCODING"
    PARSE_MISSING_REQUIRED_FIELDS = "PARSE_MISSING_REQUIRED_FIELDS"
    PARSE_UNSUPPORTED_FORMAT = "PARSE_UNSUPPORTED_FORMAT"
    PARSE_HTML_EXTRACT_FAIL = "PARSE_HTML_EXTRACT_FAIL"

    # === NORMALIZE / DEDUP / WRITE ===
    NORMALIZE_FAIL = "NORMALIZE_FAIL"
    DEDUP_STORE_UNAVAILABLE = "DEDUP_STORE_UNAVAILABLE"
    DEDUP_WRITE_FAIL = "DEDUP_WRITE_FAIL"
    OUTPUT_WRITE_FAIL = "OUTPUT_WRITE_FAIL"
    HEALTH_WRITE_FAIL = "HEALTH_WRITE_FAIL"
    STATE_CORRUPTED = "STATE_CORRUPTED"

    # === CLASSIFY / PUBLISH ===
    CLASSIFIER_FAIL = "CLASSIFIER_FAIL"
    PUBLISH_FAIL = "PUBLISH_FAIL"
    QUEUE_UNAVAILABLE = "QUEUE_UNAVAILABLE"
    DOWNSTREAM_TIMEOUT = "DOWNSTREAM_TIMEOUT"

    # === UNKNOWN (forbidden in enforced mode) ===
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


@dataclass
class ReasonCodeInfo:
    """Metadata for a reason code."""
    code: ReasonCode
    stage: Stage
    retryable: bool
    severity: str  # "error", "warning", "info"
    description: str


# Reason code registry with metadata
REASON_CODE_REGISTRY: Dict[ReasonCode, ReasonCodeInfo] = {
    # POLICY
    ReasonCode.POLICY_ALLOWLIST_MISSING: ReasonCodeInfo(
        code=ReasonCode.POLICY_ALLOWLIST_MISSING,
        stage=Stage.POLICY, retryable=False, severity="error",
        description="Allowlist file not found"
    ),
    ReasonCode.POLICY_ALLOWLIST_UNREADABLE: ReasonCodeInfo(
        code=ReasonCode.POLICY_ALLOWLIST_UNREADABLE,
        stage=Stage.POLICY, retryable=False, severity="error",
        description="Cannot read allowlist (permissions/corruption)"
    ),
    ReasonCode.POLICY_ALLOWLIST_HASH_FAIL: ReasonCodeInfo(
        code=ReasonCode.POLICY_ALLOWLIST_HASH_FAIL,
        stage=Stage.POLICY, retryable=False, severity="error",
        description="Failed to compute allowlist SHA256"
    ),
    ReasonCode.POLICY_URL_NOT_ALLOWED: ReasonCodeInfo(
        code=ReasonCode.POLICY_URL_NOT_ALLOWED,
        stage=Stage.POLICY, retryable=False, severity="error",
        description="URL/host not in allowlist"
    ),
    ReasonCode.POLICY_SCHEME_NOT_ALLOWED: ReasonCodeInfo(
        code=ReasonCode.POLICY_SCHEME_NOT_ALLOWED,
        stage=Stage.POLICY, retryable=False, severity="error",
        description="URL scheme not http/https"
    ),
    ReasonCode.POLICY_REDIRECT_NOT_ALLOWED: ReasonCodeInfo(
        code=ReasonCode.POLICY_REDIRECT_NOT_ALLOWED,
        stage=Stage.POLICY, retryable=False, severity="error",
        description="Redirect to host outside allowlist"
    ),
    ReasonCode.POLICY_EVIDENCE_WRITE_FAIL: ReasonCodeInfo(
        code=ReasonCode.POLICY_EVIDENCE_WRITE_FAIL,
        stage=Stage.POLICY, retryable=False, severity="error",
        description="Failed to write evidence atomically (SSoT not established)"
    ),

    # FETCH
    ReasonCode.DNS_FAIL: ReasonCodeInfo(
        code=ReasonCode.DNS_FAIL,
        stage=Stage.FETCH, retryable=True, severity="error",
        description="DNS resolution failed"
    ),
    ReasonCode.CONNECT_TIMEOUT: ReasonCodeInfo(
        code=ReasonCode.CONNECT_TIMEOUT,
        stage=Stage.FETCH, retryable=True, severity="error",
        description="Connection timeout"
    ),
    ReasonCode.TLS_HANDSHAKE_FAIL: ReasonCodeInfo(
        code=ReasonCode.TLS_HANDSHAKE_FAIL,
        stage=Stage.FETCH, retryable=False, severity="error",
        description="TLS handshake/certificate error"
    ),
    ReasonCode.HTTP_TIMEOUT: ReasonCodeInfo(
        code=ReasonCode.HTTP_TIMEOUT,
        stage=Stage.FETCH, retryable=True, severity="error",
        description="HTTP request timeout"
    ),
    ReasonCode.HTTP_STATUS_4XX: ReasonCodeInfo(
        code=ReasonCode.HTTP_STATUS_4XX,
        stage=Stage.FETCH, retryable=False, severity="error",
        description="HTTP 4xx client error"
    ),
    ReasonCode.HTTP_STATUS_5XX: ReasonCodeInfo(
        code=ReasonCode.HTTP_STATUS_5XX,
        stage=Stage.FETCH, retryable=True, severity="error",
        description="HTTP 5xx server error"
    ),
    ReasonCode.HTTP_TOO_MANY_REDIRECTS: ReasonCodeInfo(
        code=ReasonCode.HTTP_TOO_MANY_REDIRECTS,
        stage=Stage.FETCH, retryable=False, severity="error",
        description="Too many redirects"
    ),
    ReasonCode.HTTP_REDIRECT_LOOP: ReasonCodeInfo(
        code=ReasonCode.HTTP_REDIRECT_LOOP,
        stage=Stage.FETCH, retryable=False, severity="error",
        description="Redirect loop detected"
    ),
    ReasonCode.HTTP_INVALID_RESPONSE: ReasonCodeInfo(
        code=ReasonCode.HTTP_INVALID_RESPONSE,
        stage=Stage.FETCH, retryable=False, severity="error",
        description="Invalid HTTP response"
    ),
    ReasonCode.CONTENT_TOO_LARGE: ReasonCodeInfo(
        code=ReasonCode.CONTENT_TOO_LARGE,
        stage=Stage.FETCH, retryable=False, severity="warning",
        description="Response exceeded max size"
    ),
    ReasonCode.RATE_LIMITED: ReasonCodeInfo(
        code=ReasonCode.RATE_LIMITED,
        stage=Stage.FETCH, retryable=True, severity="warning",
        description="Rate limited (HTTP 429)"
    ),
    ReasonCode.REMOTE_CLOSED: ReasonCodeInfo(
        code=ReasonCode.REMOTE_CLOSED,
        stage=Stage.FETCH, retryable=True, severity="error",
        description="Server closed connection"
    ),

    # PARSE
    ReasonCode.PARSE_INVALID_XML: ReasonCodeInfo(
        code=ReasonCode.PARSE_INVALID_XML,
        stage=Stage.PARSE, retryable=False, severity="error",
        description="Invalid XML"
    ),
    ReasonCode.PARSE_INVALID_JSON: ReasonCodeInfo(
        code=ReasonCode.PARSE_INVALID_JSON,
        stage=Stage.PARSE, retryable=False, severity="error",
        description="Invalid JSON"
    ),
    ReasonCode.PARSE_INVALID_ENCODING: ReasonCodeInfo(
        code=ReasonCode.PARSE_INVALID_ENCODING,
        stage=Stage.PARSE, retryable=False, severity="error",
        description="Invalid encoding"
    ),
    ReasonCode.PARSE_MISSING_REQUIRED_FIELDS: ReasonCodeInfo(
        code=ReasonCode.PARSE_MISSING_REQUIRED_FIELDS,
        stage=Stage.PARSE, retryable=False, severity="error",
        description="Missing required fields"
    ),
    ReasonCode.PARSE_UNSUPPORTED_FORMAT: ReasonCodeInfo(
        code=ReasonCode.PARSE_UNSUPPORTED_FORMAT,
        stage=Stage.PARSE, retryable=False, severity="error",
        description="Unsupported format"
    ),
    ReasonCode.PARSE_HTML_EXTRACT_FAIL: ReasonCodeInfo(
        code=ReasonCode.PARSE_HTML_EXTRACT_FAIL,
        stage=Stage.PARSE, retryable=False, severity="error",
        description="HTML extraction failed"
    ),

    # NORMALIZE / DEDUP / WRITE
    ReasonCode.NORMALIZE_FAIL: ReasonCodeInfo(
        code=ReasonCode.NORMALIZE_FAIL,
        stage=Stage.NORMALIZE, retryable=False, severity="error",
        description="Normalization failed"
    ),
    ReasonCode.DEDUP_STORE_UNAVAILABLE: ReasonCodeInfo(
        code=ReasonCode.DEDUP_STORE_UNAVAILABLE,
        stage=Stage.DEDUP, retryable=True, severity="error",
        description="Dedup store unavailable"
    ),
    ReasonCode.DEDUP_WRITE_FAIL: ReasonCodeInfo(
        code=ReasonCode.DEDUP_WRITE_FAIL,
        stage=Stage.DEDUP, retryable=False, severity="error",
        description="Dedup write failed"
    ),
    ReasonCode.OUTPUT_WRITE_FAIL: ReasonCodeInfo(
        code=ReasonCode.OUTPUT_WRITE_FAIL,
        stage=Stage.WRITE, retryable=False, severity="error",
        description="Output write failed"
    ),
    ReasonCode.HEALTH_WRITE_FAIL: ReasonCodeInfo(
        code=ReasonCode.HEALTH_WRITE_FAIL,
        stage=Stage.WRITE, retryable=False, severity="error",
        description="Health write failed"
    ),
    ReasonCode.STATE_CORRUPTED: ReasonCodeInfo(
        code=ReasonCode.STATE_CORRUPTED,
        stage=Stage.WRITE, retryable=False, severity="error",
        description="State file corrupted"
    ),

    # CLASSIFY / PUBLISH
    ReasonCode.CLASSIFIER_FAIL: ReasonCodeInfo(
        code=ReasonCode.CLASSIFIER_FAIL,
        stage=Stage.CLASSIFY, retryable=False, severity="error",
        description="Classifier error"
    ),
    ReasonCode.PUBLISH_FAIL: ReasonCodeInfo(
        code=ReasonCode.PUBLISH_FAIL,
        stage=Stage.PUBLISH, retryable=True, severity="error",
        description="Publish failed"
    ),
    ReasonCode.QUEUE_UNAVAILABLE: ReasonCodeInfo(
        code=ReasonCode.QUEUE_UNAVAILABLE,
        stage=Stage.PUBLISH, retryable=True, severity="error",
        description="Queue unavailable"
    ),
    ReasonCode.DOWNSTREAM_TIMEOUT: ReasonCodeInfo(
        code=ReasonCode.DOWNSTREAM_TIMEOUT,
        stage=Stage.PUBLISH, retryable=True, severity="error",
        description="Downstream timeout"
    ),

    # UNKNOWN
    ReasonCode.UNKNOWN_ERROR: ReasonCodeInfo(
        code=ReasonCode.UNKNOWN_ERROR,
        stage=Stage.FETCH, retryable=False, severity="error",
        description="Unknown error (forbidden in enforced mode)"
    ),
}


def is_valid_reason_code(code: str) -> bool:
    """Check if code is in the closed dictionary."""
    try:
        ReasonCode(code)
        return True
    except ValueError:
        return False


def get_reason_info(code: ReasonCode) -> ReasonCodeInfo:
    """Get metadata for a reason code."""
    return REASON_CODE_REGISTRY[code]


def map_http_status_to_reason(status: int) -> ReasonCode:
    """Map HTTP status code to reason code."""
    if status == 429:
        return ReasonCode.RATE_LIMITED
    elif 400 <= status < 500:
        return ReasonCode.HTTP_STATUS_4XX
    elif 500 <= status < 600:
        return ReasonCode.HTTP_STATUS_5XX
    else:
        return ReasonCode.HTTP_INVALID_RESPONSE


def map_exception_to_reason(exc: Exception, detail: str = "") -> ReasonCode:
    """
    Map Python exception to reason code.

    Args:
        exc: Exception instance
        detail: Additional detail string

    Returns:
        Appropriate ReasonCode
    """
    import socket
    import ssl
    from urllib.error import URLError

    exc_name = type(exc).__name__.lower()
    detail_lower = detail.lower()

    # Timeout
    if isinstance(exc, socket.timeout) or "timeout" in exc_name or "timeout" in detail_lower:
        return ReasonCode.HTTP_TIMEOUT

    # SSL/TLS
    if isinstance(exc, ssl.SSLError) or "ssl" in exc_name or "certificate" in detail_lower:
        return ReasonCode.TLS_HANDSHAKE_FAIL

    # DNS
    if "getaddrinfo" in detail_lower or "dns" in detail_lower or "name resolution" in detail_lower:
        return ReasonCode.DNS_FAIL

    # Connection
    if "connection" in exc_name or "connect" in detail_lower:
        return ReasonCode.CONNECT_TIMEOUT

    # URL errors
    if isinstance(exc, URLError):
        reason_str = str(getattr(exc, 'reason', ''))
        if "timeout" in reason_str.lower():
            return ReasonCode.HTTP_TIMEOUT
        if "connection" in reason_str.lower():
            return ReasonCode.CONNECT_TIMEOUT
        return ReasonCode.HTTP_INVALID_RESPONSE

    # JSON
    if "json" in exc_name or "json" in detail_lower:
        return ReasonCode.PARSE_INVALID_JSON

    # XML
    if "xml" in exc_name or "xml" in detail_lower:
        return ReasonCode.PARSE_INVALID_XML

    # Encoding
    if "unicode" in exc_name or "decode" in exc_name or "encoding" in detail_lower:
        return ReasonCode.PARSE_INVALID_ENCODING

    # Default to unknown (but log warning)
    return ReasonCode.UNKNOWN_ERROR


@dataclass
class SourceFailure:
    """
    Structured failure record for a source.

    This is the contract for failed[] entries in spider_health.json.
    """
    source_id: str
    reason_code: ReasonCode
    stage: Stage
    retryable: bool
    detail: Optional[str] = None
    http_status: Optional[int] = None
    exception: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        result = {
            "source_id": self.source_id,
            "reason_code": self.reason_code.value,
            "stage": self.stage.value,
            "retryable": self.retryable,
        }
        if self.detail:
            result["detail"] = self.detail[:200]  # Limit detail length
        if self.http_status is not None:
            result["http_status"] = self.http_status
        if self.exception:
            result["exception"] = self.exception[:60]  # Limit exception name
        return result
