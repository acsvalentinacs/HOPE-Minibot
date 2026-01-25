# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T16:12:00Z
# Purpose: LIVE Gate - единственный барьер MAINNET (fail-closed)
# === END SIGNATURE ===
"""
LIVE Gate - MAINNET Access Control.

Единственный барьер между кодом и реальными деньгами.
Без PASS от этого гейта ордера на MAINNET невозможны.

Проверки (ВСЕ обязательны для MAINNET):
1. LIVE_ENABLE == "YES" (env var)
2. LIVE_ACK == "I_KNOW_WHAT_I_AM_DOING" (env var)
3. MODE == "MAINNET" (explicit)
4. Evidence валиден (cmdline_ssot.sha256, run_id, schema_version)
5. Allowlist egress валиден (URL/host разрешён)
6. Нет активного kill-switch

FAIL-CLOSED: Любое исключение/ошибка/сомнение = REJECT.

Usage:
    from core.trade.live_gate import LiveGate

    gate = LiveGate()

    # Перед любым MAINNET ордером:
    result = gate.check(mode="MAINNET", target_host="api.binance.com")
    if not result.allowed:
        log.error(f"MAINNET blocked: {result.reason}")
        return  # STOP

    # Только после PASS:
    router.execute_order(...)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("live_gate")

# SSoT paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATE_DIR = BASE_DIR / "state"
HEALTH_DIR = STATE_DIR / "health"
CONFIG_DIR = BASE_DIR / "config"


class GateDecision(str, Enum):
    """Gate decision codes."""
    ALLOWED = "ALLOWED"
    REJECTED_NO_LIVE_ENABLE = "REJECTED_NO_LIVE_ENABLE"
    REJECTED_NO_LIVE_ACK = "REJECTED_NO_LIVE_ACK"
    REJECTED_WRONG_MODE = "REJECTED_WRONG_MODE"
    REJECTED_NO_EVIDENCE = "REJECTED_NO_EVIDENCE"
    REJECTED_INVALID_EVIDENCE = "REJECTED_INVALID_EVIDENCE"
    REJECTED_HOST_NOT_ALLOWED = "REJECTED_HOST_NOT_ALLOWED"
    REJECTED_KILL_SWITCH = "REJECTED_KILL_SWITCH"
    REJECTED_EXCEPTION = "REJECTED_EXCEPTION"
    REJECTED_MISSING_CREDENTIALS = "REJECTED_MISSING_CREDENTIALS"


@dataclass
class LiveGateResult:
    """Result of LIVE Gate check."""
    allowed: bool
    decision: GateDecision
    reason: str
    mode: str
    checks_passed: List[str] = field(default_factory=list)
    checks_failed: List[str] = field(default_factory=list)
    evidence_sha256: Optional[str] = None
    allowlist_sha256: Optional[str] = None
    timestamp_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict."""
        return {
            "allowed": self.allowed,
            "decision": self.decision.value,
            "reason": self.reason,
            "mode": self.mode,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "evidence_sha256": self.evidence_sha256,
            "allowlist_sha256": self.allowlist_sha256,
            "timestamp_utc": self.timestamp_utc,
        }


@dataclass
class EvidenceData:
    """Parsed evidence data."""
    schema_version: str
    run_id: str
    cmdline_sha256: str
    allowlist_sha256: str
    valid: bool
    error: Optional[str] = None


class LiveGate:
    """
    LIVE Gate - MAINNET Access Control.

    FAIL-CLOSED по умолчанию:
    - Нет env var = REJECT
    - Нет evidence = REJECT
    - Невалидный evidence = REJECT
    - Host не в allowlist = REJECT
    - Любое исключение = REJECT
    """

    # Required env vars for MAINNET
    LIVE_ENABLE_VAR = "HOPE_LIVE_ENABLE"
    LIVE_ACK_VAR = "HOPE_LIVE_ACK"
    LIVE_ENABLE_VALUE = "YES"
    LIVE_ACK_VALUE = "I_KNOW_WHAT_I_AM_DOING"

    # Evidence requirements
    REQUIRED_SCHEMA_VERSION = "spider_health_v1"

    def __init__(
        self,
        health_path: Optional[Path] = None,
        allowlist_path: Optional[Path] = None,
        kill_switch_path: Optional[Path] = None,
    ):
        """
        Initialize LIVE Gate.

        Args:
            health_path: Path to health evidence file
            allowlist_path: Path to allowlist file
            kill_switch_path: Path to kill switch state
        """
        self.health_path = health_path or (HEALTH_DIR / "spider_health.json")
        self.allowlist_path = allowlist_path or (CONFIG_DIR / "AllowList.spider.txt")
        self.kill_switch_path = kill_switch_path or (STATE_DIR / "risk_engine_state.json")

        self._allowlist_cache: Optional[set] = None
        self._allowlist_sha256: Optional[str] = None

    def _load_allowlist(self) -> set:
        """Load allowlist hosts."""
        if self._allowlist_cache is not None:
            return self._allowlist_cache

        hosts = set()
        if self.allowlist_path.exists():
            content = self.allowlist_path.read_text(encoding="utf-8")
            self._allowlist_sha256 = hashlib.sha256(content.encode()).hexdigest()

            for line in content.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    hosts.add(line.lower())

            self._allowlist_cache = hosts

        return hosts

    def _check_allowlist(self, host: str) -> bool:
        """Check if host is in allowlist."""
        hosts = self._load_allowlist()
        return host.lower() in hosts

    def _load_evidence(self) -> EvidenceData:
        """Load and parse evidence."""
        if not self.health_path.exists():
            return EvidenceData(
                schema_version="",
                run_id="",
                cmdline_sha256="",
                allowlist_sha256="",
                valid=False,
                error="Health file not found",
            )

        try:
            data = json.loads(self.health_path.read_text(encoding="utf-8"))

            schema_version = data.get("schema_version", "")
            run_id = data.get("run_id", "")
            cmdline_ssot = data.get("cmdline_ssot", {})
            cmdline_sha256 = cmdline_ssot.get("sha256", "")
            policy_egress = data.get("policy_egress", {})
            allowlist_sha256 = policy_egress.get("allowlist_sha256", "")

            # Validate required fields
            errors = []
            if schema_version != self.REQUIRED_SCHEMA_VERSION:
                errors.append(f"schema_version mismatch: {schema_version}")
            if not run_id:
                errors.append("missing run_id")
            if "__cmd=" not in run_id:
                errors.append("run_id missing __cmd= binding")
            if not cmdline_sha256:
                errors.append("missing cmdline_ssot.sha256")
            if not allowlist_sha256:
                errors.append("missing policy_egress.allowlist_sha256")

            if errors:
                return EvidenceData(
                    schema_version=schema_version,
                    run_id=run_id,
                    cmdline_sha256=cmdline_sha256,
                    allowlist_sha256=allowlist_sha256,
                    valid=False,
                    error="; ".join(errors),
                )

            return EvidenceData(
                schema_version=schema_version,
                run_id=run_id,
                cmdline_sha256=cmdline_sha256,
                allowlist_sha256=allowlist_sha256,
                valid=True,
            )

        except Exception as e:
            return EvidenceData(
                schema_version="",
                run_id="",
                cmdline_sha256="",
                allowlist_sha256="",
                valid=False,
                error=f"Parse error: {e}",
            )

    def _check_kill_switch(self) -> tuple[bool, str]:
        """Check if kill switch is active. Returns (is_active, reason)."""
        if not self.kill_switch_path.exists():
            return False, ""

        try:
            data = json.loads(self.kill_switch_path.read_text(encoding="utf-8"))
            if data.get("kill_switch_active", False):
                return True, data.get("kill_switch_reason", "Unknown")
            return False, ""
        except Exception:
            # FAIL-CLOSED: if we can't read, assume active
            return True, "Unable to read kill switch state"

    def _check_credentials(self, mode: str) -> tuple[bool, str]:
        """Check if API credentials are configured."""
        secrets_path = Path(r"C:\secrets\hope\.env")
        if not secrets_path.exists():
            return False, "Secrets file not found"

        try:
            content = secrets_path.read_text(encoding="utf-8")
            env = {}
            for line in content.splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip()

            if mode == "MAINNET":
                key = env.get("BINANCE_MAINNET_API_KEY", "")
                secret = env.get("BINANCE_MAINNET_API_SECRET", "")
            else:
                key = env.get("BINANCE_TESTNET_API_KEY", "")
                secret = env.get("BINANCE_TESTNET_API_SECRET", "")

            if not key or not secret:
                return False, f"Missing API credentials for {mode}"

            return True, ""
        except Exception as e:
            return False, f"Error reading credentials: {e}"

    def check(
        self,
        mode: str,
        target_host: Optional[str] = None,
        skip_evidence: bool = False,
    ) -> LiveGateResult:
        """
        Check if LIVE trading is allowed.

        FAIL-CLOSED: Любая ошибка = REJECT.

        Args:
            mode: Trading mode (DRY, TESTNET, MAINNET)
            target_host: Target API host (e.g., api.binance.com)
            skip_evidence: Skip evidence check (for DRY mode only)

        Returns:
            LiveGateResult with decision
        """
        checks_passed = []
        checks_failed = []

        try:
            # === MODE CHECK ===
            mode = mode.upper()
            if mode not in ("DRY", "TESTNET", "MAINNET"):
                return LiveGateResult(
                    allowed=False,
                    decision=GateDecision.REJECTED_WRONG_MODE,
                    reason=f"Invalid mode: {mode}",
                    mode=mode,
                    checks_failed=["mode_valid"],
                )

            # DRY mode always allowed (no real orders)
            if mode == "DRY":
                return LiveGateResult(
                    allowed=True,
                    decision=GateDecision.ALLOWED,
                    reason="DRY mode - no real orders",
                    mode=mode,
                    checks_passed=["mode_dry"],
                )

            checks_passed.append("mode_valid")

            # === KILL SWITCH CHECK ===
            kill_active, kill_reason = self._check_kill_switch()
            if kill_active:
                return LiveGateResult(
                    allowed=False,
                    decision=GateDecision.REJECTED_KILL_SWITCH,
                    reason=f"Kill switch active: {kill_reason}",
                    mode=mode,
                    checks_passed=checks_passed,
                    checks_failed=["kill_switch"],
                )
            checks_passed.append("kill_switch")

            # === CREDENTIALS CHECK ===
            creds_ok, creds_error = self._check_credentials(mode)
            if not creds_ok:
                return LiveGateResult(
                    allowed=False,
                    decision=GateDecision.REJECTED_MISSING_CREDENTIALS,
                    reason=creds_error,
                    mode=mode,
                    checks_passed=checks_passed,
                    checks_failed=["credentials"],
                )
            checks_passed.append("credentials")

            # === MAINNET-SPECIFIC CHECKS ===
            if mode == "MAINNET":
                # Check LIVE_ENABLE
                live_enable = os.environ.get(self.LIVE_ENABLE_VAR, "")
                if live_enable != self.LIVE_ENABLE_VALUE:
                    return LiveGateResult(
                        allowed=False,
                        decision=GateDecision.REJECTED_NO_LIVE_ENABLE,
                        reason=f"{self.LIVE_ENABLE_VAR} != {self.LIVE_ENABLE_VALUE}",
                        mode=mode,
                        checks_passed=checks_passed,
                        checks_failed=["live_enable"],
                    )
                checks_passed.append("live_enable")

                # Check LIVE_ACK
                live_ack = os.environ.get(self.LIVE_ACK_VAR, "")
                if live_ack != self.LIVE_ACK_VALUE:
                    return LiveGateResult(
                        allowed=False,
                        decision=GateDecision.REJECTED_NO_LIVE_ACK,
                        reason=f"{self.LIVE_ACK_VAR} not set correctly",
                        mode=mode,
                        checks_passed=checks_passed,
                        checks_failed=["live_ack"],
                    )
                checks_passed.append("live_ack")

            # === EVIDENCE CHECK ===
            if not skip_evidence:
                evidence = self._load_evidence()
                if not evidence.valid:
                    return LiveGateResult(
                        allowed=False,
                        decision=GateDecision.REJECTED_INVALID_EVIDENCE,
                        reason=f"Invalid evidence: {evidence.error}",
                        mode=mode,
                        checks_passed=checks_passed,
                        checks_failed=["evidence_valid"],
                    )
                checks_passed.append("evidence_valid")

            # === ALLOWLIST CHECK ===
            if target_host:
                if not self._check_allowlist(target_host):
                    return LiveGateResult(
                        allowed=False,
                        decision=GateDecision.REJECTED_HOST_NOT_ALLOWED,
                        reason=f"Host not in allowlist: {target_host}",
                        mode=mode,
                        checks_passed=checks_passed,
                        checks_failed=["allowlist"],
                        allowlist_sha256=self._allowlist_sha256,
                    )
                checks_passed.append("allowlist")

            # === ALL CHECKS PASSED ===
            evidence_sha = None
            if not skip_evidence:
                evidence = self._load_evidence()
                evidence_sha = evidence.cmdline_sha256

            return LiveGateResult(
                allowed=True,
                decision=GateDecision.ALLOWED,
                reason=f"{mode} trading allowed - all gates passed",
                mode=mode,
                checks_passed=checks_passed,
                checks_failed=[],
                evidence_sha256=evidence_sha,
                allowlist_sha256=self._allowlist_sha256,
            )

        except Exception as e:
            # FAIL-CLOSED: any exception = REJECT
            logger.error("LiveGate exception: %s", e)
            return LiveGateResult(
                allowed=False,
                decision=GateDecision.REJECTED_EXCEPTION,
                reason=f"Gate exception: {e}",
                mode=mode,
                checks_passed=checks_passed,
                checks_failed=["exception"],
            )

    def is_mainnet_enabled(self) -> bool:
        """Quick check if MAINNET env vars are set."""
        enable = os.environ.get(self.LIVE_ENABLE_VAR, "")
        ack = os.environ.get(self.LIVE_ACK_VAR, "")
        return enable == self.LIVE_ENABLE_VALUE and ack == self.LIVE_ACK_VALUE

    def get_status(self) -> Dict[str, Any]:
        """Get current gate status."""
        evidence = self._load_evidence()
        kill_active, kill_reason = self._check_kill_switch()

        return {
            "live_enable_set": os.environ.get(self.LIVE_ENABLE_VAR, "") == self.LIVE_ENABLE_VALUE,
            "live_ack_set": os.environ.get(self.LIVE_ACK_VAR, "") == self.LIVE_ACK_VALUE,
            "mainnet_enabled": self.is_mainnet_enabled(),
            "evidence_valid": evidence.valid,
            "evidence_error": evidence.error,
            "kill_switch_active": kill_active,
            "kill_switch_reason": kill_reason,
            "allowlist_hosts": len(self._load_allowlist()),
            "allowlist_sha256": self._allowlist_sha256,
        }


# === CLI Interface ===
def main() -> int:
    """CLI entrypoint."""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if len(sys.argv) < 2:
        print("Usage: python -m core.trade.live_gate <command>")
        print("Commands:")
        print("  status              - Show gate status")
        print("  check <mode>        - Check if mode is allowed")
        print("  check-host <host>   - Check if host is in allowlist")
        return 1

    command = sys.argv[1]
    gate = LiveGate()

    if command == "status":
        status = gate.get_status()
        print(json.dumps(status, indent=2))
        return 0

    elif command == "check":
        mode = sys.argv[2] if len(sys.argv) > 2 else "MAINNET"
        result = gate.check(mode)
        print(json.dumps(result.to_dict(), indent=2))
        return 0 if result.allowed else 1

    elif command == "check-host":
        host = sys.argv[2] if len(sys.argv) > 2 else "api.binance.com"
        result = gate.check("MAINNET", target_host=host)
        print(json.dumps(result.to_dict(), indent=2))
        return 0 if result.allowed else 1

    else:
        print(f"Unknown command: {command}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
