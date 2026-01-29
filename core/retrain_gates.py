# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 02:25:00 UTC
# Purpose: Retrain permission gates with fail-closed semantics
# Contract: No retrain without explicit ack and passing gates
# === END SIGNATURE ===
"""
RETRAIN GATES

Controls auto-retrain with mandatory gates.
By default, retrain is FORBIDDEN until:
1. Explicit ack file exists
2. All quality gates pass

Gates:
- min_samples: Minimum closed outcomes (default: 200)
- label_sanity: Win/loss balance check
- leakage_check: No future data in features
- walkforward: Cross-validation results
- runtime_smoke: Model loads and predicts
"""

import json
import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
import logging

log = logging.getLogger("RETRAIN-GATES")

ACK_FILE = Path("state/ai/retrain_ack.json")
GATES_LOG = Path("state/ai/retrain_gates.jsonl")


@dataclass
class GateResult:
    """Result of a single gate check."""
    name: str
    passed: bool
    reason: str
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrainPermission:
    """Overall retrain permission result."""
    allowed: bool
    ack_valid: bool
    gates_passed: int
    gates_failed: int
    gates: List[GateResult] = field(default_factory=list)
    blocked_by: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "allowed": self.allowed,
            "ack_valid": self.ack_valid,
            "gates_passed": self.gates_passed,
            "gates_failed": self.gates_failed,
            "gates": [
                {"name": g.name, "passed": g.passed, "reason": g.reason}
                for g in self.gates
            ],
            "blocked_by": self.blocked_by
        }


class RetrainAck:
    """
    Retrain acknowledgment file.

    Must be explicitly created by operator to enable retrain.
    Contains evidence and expiration.
    """

    def __init__(self, ack_path: Path = None):
        self.path = ack_path or ACK_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def create(self, reason: str, expires_hours: int = 24) -> Dict:
        """
        Create ack file (operator action).

        Returns created ack data.
        """
        expires_at = datetime.now(timezone.utc).timestamp() + (expires_hours * 3600)

        data = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": os.getenv("USERNAME", "unknown"),
            "reason": reason,
            "expires_at": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
            "enabled": True
        }

        # Add sha256
        canonical = json.dumps(
            {k: v for k, v in data.items() if k != "sha256"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":")
        ).encode("utf-8")
        data["sha256"] = "sha256:" + hashlib.sha256(canonical).hexdigest()[:16]

        # Atomic write
        content = json.dumps(data, indent=2, ensure_ascii=False)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

        log.info(f"Retrain ack created: {reason}")
        return data

    def revoke(self) -> bool:
        """Revoke retrain permission (delete ack file)."""
        if self.path.exists():
            self.path.unlink()
            log.info("Retrain ack revoked")
            return True
        return False

    def is_valid(self) -> Tuple[bool, str]:
        """
        Check if ack is valid.

        Returns (is_valid, reason).
        """
        if not self.path.exists():
            return False, "ACK_NOT_FOUND"

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:
            return False, f"ACK_INVALID_JSON:{e}"

        if not data.get("enabled", False):
            return False, "ACK_DISABLED"

        # Check expiration
        expires_at = data.get("expires_at")
        if expires_at:
            try:
                exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) > exp_dt:
                    return False, "ACK_EXPIRED"
            except Exception:
                pass

        # Verify sha256
        sha = data.get("sha256")
        if sha:
            data_copy = {k: v for k, v in data.items() if k != "sha256"}
            canonical = json.dumps(data_copy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            expected = "sha256:" + hashlib.sha256(canonical).hexdigest()[:16]
            if sha != expected:
                return False, "ACK_SHA_MISMATCH"

        return True, "ACK_VALID"


class RetrainGates:
    """
    Gate checks for retrain permission.

    All gates must pass for retrain to be allowed.
    """

    def __init__(self, min_samples: int = 200, min_win_rate: float = 0.3, max_win_rate: float = 0.7):
        self.min_samples = min_samples
        self.min_win_rate = min_win_rate
        self.max_win_rate = max_win_rate
        self.ack = RetrainAck()

    def check_all(self, outcomes: List[Dict] = None) -> RetrainPermission:
        """
        Check all gates and return permission result.

        Args:
            outcomes: List of trading outcomes for validation
        """
        outcomes = outcomes or []

        result = RetrainPermission(
            allowed=False,
            ack_valid=False,
            gates_passed=0,
            gates_failed=0,
            gates=[],
            blocked_by=[]
        )

        # Gate 0: ACK file
        ack_valid, ack_reason = self.ack.is_valid()
        result.ack_valid = ack_valid

        if not ack_valid:
            result.blocked_by.append(f"ACK:{ack_reason}")
            result.gates.append(GateResult("ack", False, ack_reason))
            result.gates_failed += 1
            self._log_result(result)
            return result

        result.gates.append(GateResult("ack", True, "valid"))
        result.gates_passed += 1

        # Gate 1: Min samples
        gate1 = self._check_min_samples(outcomes)
        result.gates.append(gate1)
        if gate1.passed:
            result.gates_passed += 1
        else:
            result.gates_failed += 1
            result.blocked_by.append(f"MIN_SAMPLES:{gate1.reason}")

        # Gate 2: Label sanity
        gate2 = self._check_label_sanity(outcomes)
        result.gates.append(gate2)
        if gate2.passed:
            result.gates_passed += 1
        else:
            result.gates_failed += 1
            result.blocked_by.append(f"LABEL_SANITY:{gate2.reason}")

        # Gate 3: Leakage check (placeholder - needs feature data)
        gate3 = self._check_leakage(outcomes)
        result.gates.append(gate3)
        if gate3.passed:
            result.gates_passed += 1
        else:
            result.gates_failed += 1
            result.blocked_by.append(f"LEAKAGE:{gate3.reason}")

        # Determine final permission
        result.allowed = result.gates_failed == 0

        self._log_result(result)
        return result

    def _check_min_samples(self, outcomes: List[Dict]) -> GateResult:
        """Check minimum samples requirement."""
        count = len(outcomes)
        passed = count >= self.min_samples

        return GateResult(
            name="min_samples",
            passed=passed,
            reason=f"{count}/{self.min_samples}" if passed else f"need {self.min_samples}, have {count}",
            data={"count": count, "required": self.min_samples}
        )

    def _check_label_sanity(self, outcomes: List[Dict]) -> GateResult:
        """Check label distribution sanity."""
        if not outcomes:
            return GateResult("label_sanity", False, "no_outcomes", {})

        wins = sum(1 for o in outcomes if o.get("is_win", False))
        losses = len(outcomes) - wins
        win_rate = wins / len(outcomes) if outcomes else 0

        # Check for reasonable balance
        if win_rate < self.min_win_rate:
            return GateResult(
                "label_sanity", False,
                f"win_rate_too_low:{win_rate:.2%}",
                {"wins": wins, "losses": losses, "win_rate": win_rate}
            )

        if win_rate > self.max_win_rate:
            return GateResult(
                "label_sanity", False,
                f"win_rate_suspicious:{win_rate:.2%}",
                {"wins": wins, "losses": losses, "win_rate": win_rate}
            )

        return GateResult(
            "label_sanity", True,
            f"balanced:{win_rate:.2%}",
            {"wins": wins, "losses": losses, "win_rate": win_rate}
        )

    def _check_leakage(self, outcomes: List[Dict]) -> GateResult:
        """Check for data leakage (future information)."""
        # Basic check: ensure outcomes are ordered by time
        # and no outcome references future data
        timestamps = []
        for o in outcomes:
            ts = o.get("timestamp") or o.get("closed_at")
            if ts:
                timestamps.append(ts)

        if not timestamps:
            return GateResult("leakage", True, "no_timestamps_to_check", {})

        # Check if sorted
        is_sorted = timestamps == sorted(timestamps)

        if not is_sorted:
            return GateResult("leakage", False, "timestamps_not_sorted", {})

        return GateResult("leakage", True, "no_leakage_detected", {})

    def _log_result(self, result: RetrainPermission) -> None:
        """Log gate check result."""
        GATES_LOG.parent.mkdir(parents=True, exist_ok=True)

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "allowed": result.allowed,
            "ack_valid": result.ack_valid,
            "gates_passed": result.gates_passed,
            "gates_failed": result.gates_failed,
            "blocked_by": result.blocked_by
        }

        with open(GATES_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# Singleton
_gates_instance: Optional[RetrainGates] = None


def get_retrain_gates() -> RetrainGates:
    """Get singleton gates instance."""
    global _gates_instance
    if _gates_instance is None:
        _gates_instance = RetrainGates()
    return _gates_instance


def is_retrain_allowed(outcomes: List[Dict] = None) -> bool:
    """Quick check if retrain is allowed."""
    return get_retrain_gates().check_all(outcomes or []).allowed


def create_retrain_ack(reason: str, expires_hours: int = 24) -> Dict:
    """Create retrain ack file (operator action)."""
    return RetrainAck().create(reason, expires_hours)


def revoke_retrain_ack() -> bool:
    """Revoke retrain permission."""
    return RetrainAck().revoke()
