# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T16:20:00Z
# Modified by: Claude (opus-4)
# Modified at (UTC): 2026-01-25T17:30:00Z
# Purpose: Order Audit - append-only JSONL аудит трейдов с MANDATORY locking
# === END SIGNATURE ===
"""
Order Audit - Append-Only Audit Trail.

Единственная подсистема аудита для торговли.
Все события записываются в JSONL с lock + atomic + fsync.

Events:
- ORDER_INTENT: Намерение разместить ордер
- ORDER_REJECT: Ордер отклонён (risk/gate)
- ORDER_SUBMIT: Ордер отправлен на биржу
- ORDER_ACK: Подтверждение от биржи
- ORDER_FILL: Исполнение ордера
- ORDER_ERROR: Ошибка исполнения
- RISK_CHECK: Проверка риска
- KILL_ON: Активация kill-switch
- KILL_OFF: Деактивация kill-switch

FAIL-CLOSED:
- Ошибка записи = STOP (запрет дальнейших действий)

Usage:
    from core.trade.order_audit import OrderAudit, AuditEvent

    audit = OrderAudit()

    # Before placing order:
    if not audit.log_intent(intent, run_id):
        return  # STOP - audit failure

    # After rejection:
    audit.log_reject(intent, reason_code, reason)

    # After submission:
    audit.log_submit(intent, client_order_id)

    # After fill:
    audit.log_fill(order_id, symbol, side, qty, price)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os

# Windows-native locking (MANDATORY - not optional)
try:
    import msvcrt
    _HAS_MSVCRT = True
except ImportError:
    _HAS_MSVCRT = False

try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

import time


def _lock_file(f) -> None:
    """Acquire exclusive lock (Windows: msvcrt, Unix: fcntl). MANDATORY."""
    if _HAS_MSVCRT:
        # Windows: lock 1 byte at current position
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    elif _HAS_FCNTL:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        # FAIL-CLOSED: No locking mechanism = cannot proceed safely
        raise RuntimeError("FATAL: No file locking mechanism available (need msvcrt or fcntl)")


def _unlock_file(f) -> None:
    """Release exclusive lock."""
    if _HAS_MSVCRT:
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except Exception:
            pass
    elif _HAS_FCNTL:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("trade.order_audit")

# SSoT paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATE_DIR = BASE_DIR / "state"
AUDIT_DIR = STATE_DIR / "audit"
ORDERS_AUDIT_PATH = AUDIT_DIR / "orders.jsonl"
RISK_AUDIT_PATH = AUDIT_DIR / "risk_decisions.jsonl"


class AuditEvent(str, Enum):
    """Audit event types."""
    ORDER_INTENT = "ORDER_INTENT"
    ORDER_REJECT = "ORDER_REJECT"
    ORDER_SUBMIT = "ORDER_SUBMIT"
    ORDER_ACK = "ORDER_ACK"
    ORDER_FILL = "ORDER_FILL"
    ORDER_ERROR = "ORDER_ERROR"
    RISK_CHECK = "RISK_CHECK"
    KILL_ON = "KILL_ON"
    KILL_OFF = "KILL_OFF"


@dataclass
class AuditRecord:
    """Single audit record."""
    event: AuditEvent
    ts_utc: str
    schema_version: str
    run_id: str
    cmdline_sha256: str
    nonce: str
    symbol: Optional[str] = None
    side: Optional[str] = None
    mode: Optional[str] = None
    dry_run: bool = True
    decision: Optional[str] = None
    reason_code: Optional[str] = None
    reason: Optional[str] = None
    client_order_id: Optional[str] = None
    order_id: Optional[str] = None
    size_usd: Optional[float] = None
    qty: Optional[float] = None
    price: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON."""
        result = {
            "event": self.event.value,
            "ts_utc": self.ts_utc,
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "cmdline_sha256": self.cmdline_sha256,
            "nonce": self.nonce,
        }

        # Add optional fields only if set
        if self.symbol:
            result["symbol"] = self.symbol
        if self.side:
            result["side"] = self.side
        if self.mode:
            result["mode"] = self.mode

        result["dry_run"] = self.dry_run

        if self.decision:
            result["decision"] = self.decision
        if self.reason_code:
            result["reason_code"] = self.reason_code
        if self.reason:
            result["reason"] = self.reason
        if self.client_order_id:
            result["client_order_id"] = self.client_order_id
        if self.order_id:
            result["order_id"] = self.order_id
        if self.size_usd is not None:
            result["size_usd"] = self.size_usd
        if self.qty is not None:
            result["qty"] = self.qty
        if self.price is not None:
            result["price"] = self.price
        if self.extra:
            result["extra"] = self.extra

        return result


class OrderAudit:
    """
    Order Audit - fail-closed append-only logging.

    Любая ошибка записи = STOP.
    """

    SCHEMA_VERSION = "order_audit_v1"

    def __init__(
        self,
        orders_path: Optional[Path] = None,
        risk_path: Optional[Path] = None,
    ):
        """
        Initialize Order Audit.

        Args:
            orders_path: Path for orders audit log
            risk_path: Path for risk decisions log
        """
        self.orders_path = orders_path or ORDERS_AUDIT_PATH
        self.risk_path = risk_path or RISK_AUDIT_PATH

        # Ensure directories exist
        self.orders_path.parent.mkdir(parents=True, exist_ok=True)
        self.risk_path.parent.mkdir(parents=True, exist_ok=True)

        # Context (set by caller)
        self._run_id = ""
        self._cmdline_sha256 = ""
        self._mode = "DRY"
        self._dry_run = True

        logger.debug("OrderAudit initialized: orders=%s", self.orders_path)

    def set_context(
        self,
        run_id: str,
        cmdline_sha256: str,
        mode: str = "DRY",
        dry_run: bool = True,
    ) -> None:
        """
        Set audit context.

        Must be called before any logging.

        Args:
            run_id: Current run ID
            cmdline_sha256: SHA256 of cmdline (SSoT)
            mode: Trading mode (DRY, TESTNET, MAINNET)
            dry_run: Whether in dry-run mode
        """
        self._run_id = run_id
        self._cmdline_sha256 = cmdline_sha256
        self._mode = mode
        self._dry_run = dry_run

    def _generate_nonce(self) -> str:
        """Generate unique nonce for record."""
        ts = time.time_ns()
        data = f"{ts}{self._run_id}{os.getpid()}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    def _write_record(self, path: Path, record: AuditRecord) -> bool:
        """
        Write record to JSONL with MANDATORY lock + fsync.

        FAIL-CLOSED: Returns False on any error.
        Lock is MANDATORY - no fallback to unlocked writes.
        """
        retries = 3
        last_error = None

        for attempt in range(retries):
            try:
                line = json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"

                with open(path, "a", encoding="utf-8", newline="\n") as f:
                    try:
                        _lock_file(f)  # MANDATORY - will raise if no lock available
                        f.write(line)
                        f.flush()
                        os.fsync(f.fileno())
                    finally:
                        _unlock_file(f)

                return True

            except (IOError, OSError, BlockingIOError) as e:
                last_error = e
                time.sleep(0.01 * (attempt + 1))  # Brief retry delay

            except Exception as e:
                logger.error("AUDIT WRITE FAILED: %s - %s", path, e)
                return False

        logger.error("AUDIT WRITE FAILED after %d retries: %s - %s", retries, path, last_error)
        return False

    def _create_record(
        self,
        event: AuditEvent,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        **kwargs: Any,
    ) -> AuditRecord:
        """Create audit record with context."""
        return AuditRecord(
            event=event,
            ts_utc=datetime.now(timezone.utc).isoformat(),
            schema_version=self.SCHEMA_VERSION,
            run_id=self._run_id,
            cmdline_sha256=self._cmdline_sha256,
            nonce=self._generate_nonce(),
            symbol=symbol,
            side=side,
            mode=self._mode,
            dry_run=self._dry_run,
            **kwargs,
        )

    def log_intent(
        self,
        symbol: str,
        side: str,
        size_usd: float,
        signal_id: Optional[str] = None,
    ) -> bool:
        """
        Log ORDER_INTENT event.

        Returns False on write error (STOP condition).
        """
        record = self._create_record(
            AuditEvent.ORDER_INTENT,
            symbol=symbol,
            side=side,
            size_usd=size_usd,
            extra={"signal_id": signal_id} if signal_id else {},
        )
        success = self._write_record(self.orders_path, record)

        if not success:
            logger.critical("AUDIT FAILED - STOP TRADING")

        return success

    def log_reject(
        self,
        symbol: str,
        side: str,
        reason_code: str,
        reason: str,
        size_usd: Optional[float] = None,
    ) -> bool:
        """Log ORDER_REJECT event."""
        record = self._create_record(
            AuditEvent.ORDER_REJECT,
            symbol=symbol,
            side=side,
            decision="REJECTED",
            reason_code=reason_code,
            reason=reason,
            size_usd=size_usd,
        )
        return self._write_record(self.orders_path, record)

    def log_submit(
        self,
        symbol: str,
        side: str,
        client_order_id: str,
        size_usd: float,
    ) -> bool:
        """Log ORDER_SUBMIT event."""
        record = self._create_record(
            AuditEvent.ORDER_SUBMIT,
            symbol=symbol,
            side=side,
            client_order_id=client_order_id,
            size_usd=size_usd,
        )
        return self._write_record(self.orders_path, record)

    def log_ack(
        self,
        symbol: str,
        side: str,
        client_order_id: str,
        order_id: str,
        status: str,
    ) -> bool:
        """Log ORDER_ACK event."""
        record = self._create_record(
            AuditEvent.ORDER_ACK,
            symbol=symbol,
            side=side,
            client_order_id=client_order_id,
            order_id=order_id,
            extra={"status": status},
        )
        return self._write_record(self.orders_path, record)

    def log_fill(
        self,
        symbol: str,
        side: str,
        order_id: str,
        qty: float,
        price: float,
        commission: Optional[float] = None,
    ) -> bool:
        """Log ORDER_FILL event."""
        extra = {}
        if commission is not None:
            extra["commission"] = commission

        record = self._create_record(
            AuditEvent.ORDER_FILL,
            symbol=symbol,
            side=side,
            order_id=order_id,
            qty=qty,
            price=price,
            extra=extra,
        )
        return self._write_record(self.orders_path, record)

    def log_error(
        self,
        symbol: str,
        side: str,
        error: str,
        client_order_id: Optional[str] = None,
    ) -> bool:
        """Log ORDER_ERROR event."""
        record = self._create_record(
            AuditEvent.ORDER_ERROR,
            symbol=symbol,
            side=side,
            client_order_id=client_order_id,
            reason=error,
        )
        return self._write_record(self.orders_path, record)

    def log_risk_check(
        self,
        symbol: str,
        side: str,
        decision: str,
        reason_code: str,
        allowed_size: Optional[float] = None,
    ) -> bool:
        """Log RISK_CHECK event."""
        record = self._create_record(
            AuditEvent.RISK_CHECK,
            symbol=symbol,
            side=side,
            decision=decision,
            reason_code=reason_code,
            size_usd=allowed_size,
        )
        return self._write_record(self.risk_path, record)

    def log_kill_switch(self, active: bool, reason: str, actor: str) -> bool:
        """Log KILL_ON or KILL_OFF event."""
        event = AuditEvent.KILL_ON if active else AuditEvent.KILL_OFF
        record = self._create_record(
            event,
            reason=reason,
            extra={"actor": actor},
        )
        return self._write_record(self.risk_path, record)

    def get_recent_events(self, count: int = 20) -> list:
        """Get recent audit events (for debugging)."""
        if not self.orders_path.exists():
            return []

        try:
            lines = self.orders_path.read_text(encoding="utf-8").strip().split("\n")
            recent = lines[-count:] if len(lines) > count else lines
            return [json.loads(line) for line in recent if line]
        except Exception as e:
            logger.error("Failed to read audit: %s", e)
            return []


# === CLI Interface ===
def main() -> int:
    """CLI entrypoint."""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if len(sys.argv) < 2:
        print("Usage: python -m core.trade.order_audit <command>")
        print("Commands:")
        print("  recent [N]   - Show last N events")
        print("  test         - Write test event")
        return 1

    command = sys.argv[1]
    audit = OrderAudit()

    if command == "recent":
        count = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        events = audit.get_recent_events(count)
        for e in events:
            print(json.dumps(e))
        return 0

    elif command == "test":
        audit.set_context(
            run_id="test_run_001",
            cmdline_sha256="test_sha256",
            mode="DRY",
            dry_run=True,
        )

        success = audit.log_intent(
            symbol="BTCUSDT",
            side="BUY",
            size_usd=100.0,
            signal_id="test_signal",
        )
        print(f"Write test: {'PASS' if success else 'FAIL'}")
        return 0 if success else 1

    else:
        print(f"Unknown command: {command}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
