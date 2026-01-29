# === AI SIGNATURE ===
# Created by: Kirill Dev
# Created at: 2026-01-19 18:24:32 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-29 15:30:00 UTC
# Change: Added api.anthropic.com to ALLOWED_DOMAINS for AI-Gateway
# === END SIGNATURE ===
"""
Snapshot Store - Atomic evidence persistence with sha256 verification.

Every piece of external data MUST be persisted as a snapshot before analysis.
Signals/publications MUST reference snapshot_id for audit trail.

Contract:
    fetch -> validate -> sha256:hash -> persist (atomic) -> return SnapshotMeta
    STALE/FAIL -> log reason -> skip cycle (fail-closed)

Fail-closed rules:
    - Domain not in ALLOWED_DOMAINS -> reject, log, skip
    - HTTP != 200 -> snapshot with parse_ok=false, downstream skip
    - JSON/RSS parse fail -> snapshot saved, downstream skip
    - Snapshot missing/stale (now - timestamp > ttl) -> downstream skip

Usage:
    store = SnapshotStore(BASE_DIR)
    meta, path = store.persist(
        source="binance_ticker",
        source_url="https://api.binance.com/api/v3/ticker/24hr",
        raw=response_bytes,
        ttl_sec=300,
        parsed={"top_gainers": [...]},
    )
    print(meta.snapshot_id)  # sha256:abc123...
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Allowed domains for fetching (per CLAUDE.md - SSoT)
ALLOWED_DOMAINS: FrozenSet[str] = frozenset({
    # Binance API (contractual)
    "api.binance.com",
    "data.binance.vision",
    "testnet.binance.vision",
    "developers.binance.com",  # Changelog/breaking changes
    "www.binance.com",
    # News RSS feeds
    "www.coindesk.com",
    "cointelegraph.com",
    "decrypt.co",
    "www.theblock.co",
    "bitcoinmagazine.com",
    # Crypto data aggregators
    "api.coingecko.com",
    "pro-api.coinmarketcap.com",
    # Infrastructure
    "checkip.amazonaws.com",
    "pypi.org",
    "api.github.com",
    "raw.githubusercontent.com",
    # AI APIs
    "api.anthropic.com",
})


class DomainNotAllowedError(Exception):
    """Raised when URL domain is not in allowlist."""
    pass


def _sha256_hex(data: bytes) -> str:
    """Compute sha256 hex digest."""
    return hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    """Atomic write: temp -> fsync -> replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def validate_domain(url: str) -> str:
    """
    Validate URL domain against allowlist. Fail-closed.

    Args:
        url: URL to validate

    Returns:
        Domain string if valid

    Raises:
        DomainNotAllowedError if domain not in allowlist
    """
    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    if domain not in ALLOWED_DOMAINS:
        logger.warning("Domain not allowed: %s (url: %s)", domain, url)
        raise DomainNotAllowedError(f"Domain not in allowlist: {domain}")

    return domain


@dataclass(frozen=True)
class SnapshotMeta:
    """Immutable snapshot metadata."""
    snapshot_id: str          # sha256:<64-hex>
    timestamp_unix: float     # UTC epoch
    source: str               # e.g. "binance_ticker"
    source_url: str           # original URL
    content_sha256: str       # <64-hex> of raw bytes
    ttl_sec: int              # time-to-live in seconds
    bytes_len: int            # raw payload size
    http_status: int = 200    # HTTP response status
    parse_ok: bool = True     # Whether parsing succeeded
    error: str = ""           # Error message if any

    def is_stale(self, now: Optional[float] = None) -> bool:
        """Check if snapshot has exceeded TTL."""
        now = now or time.time()
        return (now - self.timestamp_unix) > self.ttl_sec

    def is_valid(self) -> bool:
        """Check if snapshot is valid for downstream use."""
        return self.http_status == 200 and self.parse_ok and not self.error

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SnapshotStore:
    """
    Atomic snapshot storage with integrity verification.

    Directory structure:
        data/snapshots/{source}/{timestamp}_{hash16}.json
    """

    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir / "data" / "snapshots"
        self._base.mkdir(parents=True, exist_ok=True)

    def persist(
        self,
        *,
        source: str,
        source_url: str,
        raw: bytes,
        ttl_sec: int,
        http_status: int = 200,
        parse_ok: bool = True,
        error: str = "",
        parsed: Optional[Dict[str, Any]] = None,
        skip_domain_check: bool = False,
    ) -> Tuple[SnapshotMeta, Path]:
        """
        Persist raw data as atomic snapshot with sha256 verification.

        Fail-closed: Domain validation required unless skip_domain_check=True.

        Args:
            source: Data source identifier (e.g. "binance_ticker")
            source_url: Original URL fetched
            raw: Raw response bytes
            ttl_sec: Time-to-live in seconds
            http_status: HTTP response status code
            parse_ok: Whether parsing succeeded
            error: Error message if any
            parsed: Optional pre-parsed data to include
            skip_domain_check: Skip domain validation (for testing only)

        Returns:
            Tuple of (SnapshotMeta, Path to snapshot file)

        Raises:
            DomainNotAllowedError: If domain not in allowlist
        """
        # Fail-closed: validate domain
        if not skip_domain_check:
            validate_domain(source_url)

        ts = time.time()
        raw_hash = _sha256_hex(raw)
        snapshot_id = f"sha256:{raw_hash}"

        meta = SnapshotMeta(
            snapshot_id=snapshot_id,
            timestamp_unix=ts,
            source=source,
            source_url=source_url,
            content_sha256=raw_hash,
            ttl_sec=ttl_sec,
            bytes_len=len(raw),
            http_status=http_status,
            parse_ok=parse_ok,
            error=error,
        )

        payload: Dict[str, Any] = {
            "snapshot_id": snapshot_id,
            "timestamp_unix": ts,
            "timestamp_iso": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "source": source,
            "source_url": source_url,
            "content_sha256": raw_hash,
            "ttl_sec": ttl_sec,
            "bytes_len": len(raw),
            "http_status": http_status,
            "parse_ok": parse_ok,
            "error": error,
        }

        if parsed is not None:
            payload["parsed"] = parsed

        # Deterministic filename: timestamp_hash16.json
        fname = f"{int(ts)}_{raw_hash[:16]}.json"
        out_path = self._base / source / fname

        _atomic_write(out_path, json.dumps(payload, ensure_ascii=False, indent=2))

        logger.debug("Persisted snapshot: %s (%d bytes, valid=%s)", snapshot_id[:24], len(raw), meta.is_valid())

        return meta, out_path

    def get_latest(self, source: str, max_age_sec: Optional[float] = None) -> Optional[Tuple[SnapshotMeta, Dict[str, Any]]]:
        """
        Get latest snapshot for source, optionally filtering by age.

        Args:
            source: Data source identifier
            max_age_sec: Maximum age in seconds (None = no filter)

        Returns:
            Tuple of (SnapshotMeta, parsed payload) or None if not found/stale
        """
        source_dir = self._base / source
        if not source_dir.exists():
            return None

        files = sorted(source_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            return None

        try:
            data = json.loads(files[0].read_text(encoding="utf-8"))
            meta = SnapshotMeta(
                snapshot_id=data["snapshot_id"],
                timestamp_unix=data["timestamp_unix"],
                source=data["source"],
                source_url=data["source_url"],
                content_sha256=data["content_sha256"],
                ttl_sec=data["ttl_sec"],
                bytes_len=data["bytes_len"],
                http_status=data.get("http_status", 200),
                parse_ok=data.get("parse_ok", True),
                error=data.get("error", ""),
            )

            if max_age_sec is not None:
                if (time.time() - meta.timestamp_unix) > max_age_sec:
                    return None

            return meta, data

        except (json.JSONDecodeError, KeyError):
            return None

    def list_snapshots(self, source: str, limit: int = 10) -> List[SnapshotMeta]:
        """List recent snapshots for source."""
        source_dir = self._base / source
        if not source_dir.exists():
            return []

        results = []
        files = sorted(source_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

        for f in files[:limit]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                results.append(SnapshotMeta(
                    snapshot_id=data["snapshot_id"],
                    timestamp_unix=data["timestamp_unix"],
                    source=data["source"],
                    source_url=data["source_url"],
                    content_sha256=data["content_sha256"],
                    ttl_sec=data["ttl_sec"],
                    bytes_len=data["bytes_len"],
                    http_status=data.get("http_status", 200),
                    parse_ok=data.get("parse_ok", True),
                    error=data.get("error", ""),
                ))
            except (json.JSONDecodeError, KeyError):
                continue

        return results

    def cleanup_stale(self, source: str, keep_count: int = 100) -> int:
        """Remove old snapshots, keeping most recent keep_count."""
        source_dir = self._base / source
        if not source_dir.exists():
            return 0

        files = sorted(source_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        removed = 0

        for f in files[keep_count:]:
            try:
                f.unlink()
                removed += 1
            except OSError:
                continue

        return removed

    def require_fresh(
        self,
        source: str,
        max_age_sec: Optional[float] = None,
    ) -> Tuple[SnapshotMeta, Dict[str, Any]]:
        """
        Get latest valid snapshot or raise. Fail-closed.

        Args:
            source: Data source identifier
            max_age_sec: Maximum age (defaults to snapshot's TTL)

        Returns:
            Tuple of (SnapshotMeta, parsed payload)

        Raises:
            ValueError: If no valid fresh snapshot exists
        """
        result = self.get_latest(source, max_age_sec)

        if result is None:
            raise ValueError(f"No fresh snapshot for source: {source}")

        meta, data = result

        if not meta.is_valid():
            raise ValueError(
                f"Snapshot invalid for {source}: "
                f"http={meta.http_status}, parse_ok={meta.parse_ok}, error={meta.error}"
            )

        if meta.is_stale():
            raise ValueError(
                f"Snapshot stale for {source}: "
                f"age={time.time() - meta.timestamp_unix:.0f}s > ttl={meta.ttl_sec}s"
            )

        return meta, data

