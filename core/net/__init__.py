# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T12:30:00Z
# Purpose: Egress policy enforcement module
# === END SIGNATURE ===
"""
HOPE Egress Policy Module

This module provides fail-closed HTTP egress control:
- AllowList.txt enforcement (host-only, no wildcards)
- Audit logging (JSONL, append-only, locked)
- Single HTTP wrapper for all external requests

SSoT: AllowList.txt in repo root
Audit: staging/history/egress_audit.jsonl
"""

from core.net.net_policy import (
    AllowList,
    load_allowlist,
    validate_host,
    FatalPolicyError,
    PolicyValidationError,
)
from core.net.http_client import http_get, EgressDeniedError
from core.net.audit_log import append_audit_record, AuditAction

__all__ = [
    "AllowList",
    "load_allowlist",
    "validate_host",
    "FatalPolicyError",
    "PolicyValidationError",
    "http_get",
    "EgressDeniedError",
    "append_audit_record",
    "AuditAction",
]
