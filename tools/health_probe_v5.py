# === AI SIGNATURE ===
# Created by: Claude
# Created at: 2026-01-22 10:30:00 UTC
# === END SIGNATURE ===
"""
Health Probe v5 for HOPE Trading Bot.

Strict validation of health_v5.json for readiness checks.
FAIL-CLOSED: Any missing/invalid field = exit 1.

Usage:
    python tools/health_probe_v5.py <health_path> <max_age_sec>

Exit codes:
    0 - PASS (healthy)
    1 - FAIL (unhealthy or invalid)
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


class ProbeFail(RuntimeError):
    """Fail-closed probe error."""
    pass


def _parse_iso8601_z(s: str) -> datetime:
    """Parse ISO8601 timestamp with Z suffix."""
    if not isinstance(s, str) or not s.endswith("Z"):
        raise ProbeFail("hb_ts must be ISO8601 with Z suffix")
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception as e:
        raise ProbeFail(f"hb_ts parse failed: {e!r}") from e


def _read_json(path: Path) -> Dict[str, Any]:
    """Read and parse JSON file (fail-closed)."""
    try:
        raw = path.read_text("utf-8")
    except FileNotFoundError as e:
        raise ProbeFail(f"missing health file: {path.as_posix()}") from e
    except Exception as e:
        raise ProbeFail(f"cannot read health file: {e}") from e

    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ProbeFail(f"health is corrupted JSON: {e}") from e

    if not isinstance(obj, dict):
        raise ProbeFail("health must be a JSON object")
    return obj


def _require(obj: Dict[str, Any], key: str, typ: type) -> Any:
    """Require field with type validation (fail-closed)."""
    if key not in obj:
        raise ProbeFail(f"missing required field: {key}")
    val = obj[key]

    if typ is bool:
        if not isinstance(val, bool):
            raise ProbeFail(f"field {key} must be bool, got {type(val).__name__}")
        return val
    if typ is int:
        if not isinstance(val, int) or isinstance(val, bool):
            raise ProbeFail(f"field {key} must be int, got {type(val).__name__}")
        return val
    if typ is float:
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            raise ProbeFail(f"field {key} must be number, got {type(val).__name__}")
        return float(val)
    if typ is str:
        if not isinstance(val, str):
            raise ProbeFail(f"field {key} must be string, got {type(val).__name__}")
        if not val.strip():
            raise ProbeFail(f"field {key} must be non-empty string")
        return val

    raise ProbeFail(f"internal: unsupported type for {key}")


@dataclass(frozen=True)
class ProbeConfig:
    """Probe configuration."""
    health_path: Path
    max_age_sec: int


def probe(cfg: ProbeConfig) -> Dict[str, Any]:
    """
    Execute health probe.

    Validates all required fields and constraints.
    Returns summary dict on PASS, raises ProbeFail on any issue.
    """
    if cfg.max_age_sec <= 0:
        raise ProbeFail("max_age_sec must be > 0")

    h = _read_json(cfg.health_path)

    # Required fields
    engine_version = _require(h, "engine_version", str)
    mode = _require(h, "mode", str)
    hb_ts = _require(h, "hb_ts", str)
    uptime_sec = _require(h, "uptime_sec", int)
    open_positions = _require(h, "open_positions", int)
    queue_size = _require(h, "queue_size", int)
    daily_pnl_usd = _require(h, "daily_pnl_usd", float)
    daily_stop_hit = _require(h, "daily_stop_hit", bool)

    # Validate mode
    if mode not in {"DRY", "TESTNET", "LIVE"}:
        raise ProbeFail(f"mode invalid: {mode}, expected DRY|TESTNET|LIVE")

    # Validate non-negative counters
    if uptime_sec < 0:
        raise ProbeFail(f"uptime_sec cannot be negative: {uptime_sec}")
    if open_positions < 0:
        raise ProbeFail(f"open_positions cannot be negative: {open_positions}")
    if queue_size < 0:
        raise ProbeFail(f"queue_size cannot be negative: {queue_size}")

    # Validate last_error (must exist; null or empty string is OK)
    if "last_error" not in h:
        raise ProbeFail("missing required field: last_error")
    last_error = h["last_error"]
    if last_error is None:
        pass  # OK
    elif isinstance(last_error, str):
        if last_error.strip():
            raise ProbeFail(f"last_error present: {last_error.strip()}")
    else:
        raise ProbeFail("field last_error must be null or string")

    # Validate daily_stop_hit
    if daily_stop_hit:
        raise ProbeFail("daily_stop_hit=true blocks start")

    # Validate heartbeat age
    hb_dt = _parse_iso8601_z(hb_ts)
    age_sec = int((datetime.now(timezone.utc) - hb_dt).total_seconds())
    if age_sec > cfg.max_age_sec:
        raise ProbeFail(f"heartbeat too old: age_sec={age_sec} > max_age_sec={cfg.max_age_sec}")
    if age_sec < 0:
        raise ProbeFail(f"heartbeat in future: age_sec={age_sec}")

    return {
        "engine_version": engine_version,
        "mode": mode,
        "age_sec": age_sec,
        "uptime_sec": uptime_sec,
        "open_positions": open_positions,
        "queue_size": queue_size,
        "daily_pnl_usd": daily_pnl_usd,
    }


def main() -> None:
    """Main entry point."""
    if len(sys.argv) < 3:
        print("FAIL: usage: health_probe_v5.py <health_path> <max_age_sec>")
        raise SystemExit(1)

    health_path = Path(sys.argv[1])
    try:
        max_age_sec = int(sys.argv[2])
    except ValueError:
        print("FAIL: max_age_sec must be int")
        raise SystemExit(1)

    try:
        summary = probe(ProbeConfig(health_path=health_path, max_age_sec=max_age_sec))
        print("PASS")
        print(json.dumps(summary, indent=2, sort_keys=True))
        raise SystemExit(0)
    except ProbeFail as e:
        print(f"FAIL: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
