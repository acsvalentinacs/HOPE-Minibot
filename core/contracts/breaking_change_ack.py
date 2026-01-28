# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T21:30:00Z
# Purpose: Breaking Change Acknowledgment Contract
# Security: Fail-closed, checksum validation, atomic operations
# === END SIGNATURE ===
"""
Breaking Change Acknowledgment Contract.

Принципы:
- SSoT: один файл state/breaking_changes_ack.json для всех acks
- Fail-closed: отсутствие ack или невалидный checksum = BLOCK
- Explicit: каждый ack требует verification evidence
- Atomic: temp -> fsync -> replace при записи

Usage:
    from core.contracts.breaking_change_ack import (
        BreakingChangeAck,
        load_acknowledged_changes,
        is_change_acknowledged,
    )

    # Check if a change is acknowledged
    acks = load_acknowledged_changes()
    if is_change_acknowledged("SIGNATURE_CHANGE_2026-01-15", acks):
        print("Change acknowledged, proceeding...")
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# SSoT paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATE_DIR = BASE_DIR / "state"
ACK_FILE = STATE_DIR / "breaking_changes_ack.json"


@dataclass
class BreakingChangeAck:
    """
    Acknowledgment record for a breaking change.

    All fields except checksum are used to compute the checksum.
    If any field is modified, checksum validation will fail.
    """
    change_id: str                    # Unique ID: "SIGNATURE_CHANGE_2026-01-15"
    summary: str                      # Human-readable description
    acknowledged_at: str              # ISO8601 UTC (e.g., "2026-01-28T21:00:00Z")
    acknowledged_by: str              # Who acked (e.g., "Valentin")
    verification_method: str          # How verified: "TESTNET_ORDER", "MAINNET_ORDER", "MANUAL_TEST"
    verification_evidence: str        # Proof (e.g., "Order #123 succeeded after change date")
    expires_at: Optional[str] = None  # Optional expiry for time-limited acks
    checksum: Optional[str] = None    # sha256:... of all fields except checksum

    def compute_checksum(self) -> str:
        """
        Compute sha256 checksum of all fields except checksum itself.

        Format: sha256:<first 16 chars of hex digest>
        """
        data = (
            f"{self.change_id}|"
            f"{self.summary}|"
            f"{self.acknowledged_at}|"
            f"{self.acknowledged_by}|"
            f"{self.verification_method}|"
            f"{self.verification_evidence}|"
            f"{self.expires_at or ''}"
        )
        digest = hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]
        return f"sha256:{digest}"

    def is_checksum_valid(self) -> bool:
        """
        Validate that checksum matches computed value.

        Returns:
            True if checksum is present and matches.
            False otherwise (fail-closed).
        """
        if not self.checksum:
            logger.warning("Ack missing checksum: %s", self.change_id)
            return False

        expected = self.compute_checksum()
        if self.checksum != expected:
            logger.warning(
                "Ack checksum mismatch for %s: got %s, expected %s",
                self.change_id, self.checksum, expected
            )
            return False

        return True

    def is_expired(self) -> bool:
        """
        Check if ack has expired.

        Returns:
            True if expires_at is set and in the past.
            False if no expiry or not yet expired.
        """
        if not self.expires_at:
            return False

        try:
            expiry = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            return expiry < now
        except ValueError as e:
            logger.warning("Invalid expires_at format for %s: %s", self.change_id, e)
            return True  # Fail-closed: treat invalid date as expired

    def is_valid(self) -> bool:
        """
        Full validation: checksum valid AND not expired.

        Returns:
            True if ack is valid and can be used.
            False otherwise.
        """
        return self.is_checksum_valid() and not self.is_expired()

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "BreakingChangeAck":
        """Create from dictionary (e.g., from JSON)."""
        return cls(
            change_id=data.get("change_id", ""),
            summary=data.get("summary", ""),
            acknowledged_at=data.get("acknowledged_at", ""),
            acknowledged_by=data.get("acknowledged_by", ""),
            verification_method=data.get("verification_method", ""),
            verification_evidence=data.get("verification_evidence", ""),
            expires_at=data.get("expires_at"),
            checksum=data.get("checksum"),
        )

    @classmethod
    def create_new(
        cls,
        change_id: str,
        summary: str,
        acknowledged_by: str,
        verification_method: str,
        verification_evidence: str,
        expires_at: Optional[str] = None,
    ) -> "BreakingChangeAck":
        """
        Create a new ack with auto-generated timestamp and checksum.

        Use this method to create valid acks programmatically.
        """
        ack = cls(
            change_id=change_id,
            summary=summary,
            acknowledged_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            acknowledged_by=acknowledged_by,
            verification_method=verification_method,
            verification_evidence=verification_evidence,
            expires_at=expires_at,
        )
        ack = cls(
            **{**asdict(ack), "checksum": ack.compute_checksum()}
        )
        return ack


def load_acknowledged_changes(ack_file: Optional[Path] = None) -> List[BreakingChangeAck]:
    """
    Load acknowledged changes from JSON file.

    Args:
        ack_file: Path to ack file. Defaults to state/breaking_changes_ack.json

    Returns:
        List of valid BreakingChangeAck objects.
        Returns empty list on any error (fail-closed).
    """
    path = ack_file or ACK_FILE

    if not path.exists():
        logger.debug("Ack file not found: %s", path)
        return []

    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)

        if not isinstance(data, dict):
            logger.error("Ack file is not a JSON object: %s", path)
            return []

        acks = []
        for ack_data in data.get("acknowledged", []):
            try:
                ack = BreakingChangeAck.from_dict(ack_data)
                if ack.is_valid():
                    acks.append(ack)
                    logger.debug("Loaded valid ack: %s", ack.change_id)
                else:
                    logger.warning("Skipping invalid ack: %s", ack.change_id)
            except Exception as e:
                logger.warning("Failed to parse ack: %s", e)

        return acks

    except json.JSONDecodeError as e:
        logger.error("Failed to parse ack JSON: %s", e)
        return []
    except Exception as e:
        logger.error("Failed to load acks: %s", e)
        return []


def get_acknowledged_ids(ack_file: Optional[Path] = None) -> Set[str]:
    """
    Get set of acknowledged change IDs.

    Args:
        ack_file: Path to ack file.

    Returns:
        Set of change_id strings for valid acks.
    """
    acks = load_acknowledged_changes(ack_file)
    return {ack.change_id for ack in acks}


def is_change_acknowledged(change_id: str, acks: Optional[List[BreakingChangeAck]] = None) -> bool:
    """
    Check if a specific change is acknowledged.

    Args:
        change_id: The change ID to check.
        acks: Optional list of acks (loads from file if not provided).

    Returns:
        True if change is acknowledged with valid checksum and not expired.
        False otherwise.
    """
    if acks is None:
        acks = load_acknowledged_changes()

    for ack in acks:
        if ack.change_id == change_id and ack.is_valid():
            return True

    return False


def save_acknowledged_changes(
    acks: List[BreakingChangeAck],
    ack_file: Optional[Path] = None
) -> bool:
    """
    Save acknowledged changes to JSON file (atomic write).

    Args:
        acks: List of acks to save.
        ack_file: Path to ack file.

    Returns:
        True if saved successfully, False otherwise.
    """
    path = ack_file or ACK_FILE

    try:
        # Ensure directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        # Build data structure
        data = {
            "schema_version": "1.0",
            "acknowledged": [ack.to_dict() for ack in acks],
            "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        content = json.dumps(data, indent=2, ensure_ascii=False)

        # Atomic write: temp -> fsync -> replace
        tmp_path = path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, path)
        logger.info("Saved %d acks to %s", len(acks), path)
        return True

    except Exception as e:
        logger.error("Failed to save acks: %s", e)
        return False


# Convenience function for generating ack file content
def generate_ack_entry(
    change_id: str,
    summary: str,
    acknowledged_by: str,
    verification_method: str,
    verification_evidence: str,
) -> Dict:
    """
    Generate a complete ack entry with checksum for manual insertion.

    Prints the JSON that can be added to breaking_changes_ack.json.
    """
    ack = BreakingChangeAck.create_new(
        change_id=change_id,
        summary=summary,
        acknowledged_by=acknowledged_by,
        verification_method=verification_method,
        verification_evidence=verification_evidence,
    )
    return ack.to_dict()
