# ---- HOPE import bootstrap (do not remove) ----
# --- HOPE_SINGLETON_HARDENING_BEGIN ---
_LOCK_HANDLE = None


def _hope_project_root():
    # Resolve TradingBot root by searching for .venv upward.
    from pathlib import Path

    p = Path(__file__).resolve()
    for up in range(0, 12):
        try:
            cand = p.parents[up]
        except IndexError:
            break
        if (cand / ".venv" / "Scripts" / "python.exe").exists():
            return cand
    return p.parents[1]


def _hope_venv_guard(root):
    # Enforce running via venv python.exe to avoid mixed environments.
    import os, sys

    venv_py = root / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        venv_py_str = str(venv_py)
        # Use realpath to resolve all symlinks and normalize paths properly on Windows
        a = os.path.normcase(os.path.realpath(sys.executable))
        b = os.path.normcase(os.path.realpath(venv_py_str))
        if a != b:
            sys.stderr.write(
                "[HOPE] Refusing to run outside venv.\n"
                f"  sys.executable={sys.executable}\n"
                f"  realpath(sys.executable)={a}\n"
                f"  expected={venv_py_str}\n"
                f"  realpath(expected)={b}\n"
            )
            sys.stderr.flush()
            raise SystemExit(2)

        # CRITICAL: Override sys.executable to ensure all child processes use venv python
        # This is needed because multiprocessing on Windows uses sys.executable directly
        try:
            sys.executable = venv_py_str
        except (AttributeError, TypeError):
            pass

    # Force multiprocessing to use venv python for children (Windows).
    # Must be called BEFORE any multiprocessing context is created.
    try:
        import multiprocessing as _mp

        try:
            _mp.set_executable(str(venv_py))
            # Also patch get_context() to ensure all contexts use venv python
            _original_get_context = _mp.get_context

            def _patched_get_context(method=None):
                ctx = _original_get_context(method)
                try:
                    ctx.set_executable(str(venv_py))
                except Exception:
                    pass
                return ctx

            _mp.get_context = _patched_get_context
        except Exception:
            pass
    except Exception:
        pass

    # CRITICAL: Patch subprocess.Popen to always use venv python for Python scripts
    # Use a class wrapper instead of function to preserve class inheritance
    try:
        import subprocess as _sp

        _original_popen_class = _sp.Popen
        venv_py_str = str(venv_py)

        class _HopePatchedPopen(_original_popen_class):
            def __init__(self, *args, **kwargs):
                # Always ensure executable points to venv python if not explicitly set
                if "executable" not in kwargs:
                    # Check if args[0] is a Python script or Python command
                    if args and len(args) > 0:
                        cmd = args[0]
                        if isinstance(cmd, (list, tuple)) and len(cmd) > 0:
                            first_arg = str(cmd[0])
                            # If first arg is Python-related, set executable
                            if (
                                first_arg.lower() in ("python", "python.exe", "py")
                                or first_arg.endswith(".py")
                                or "python" in first_arg.lower()
                            ):
                                kwargs["executable"] = venv_py_str
                        elif isinstance(cmd, str):
                            if cmd.endswith(".py") or "python" in cmd.lower():
                                kwargs["executable"] = venv_py_str
                else:
                    # If executable is set but points to system Python, replace it
                    exec_path = str(kwargs.get("executable", ""))
                    if "Python312" in exec_path or (
                        "python" in exec_path.lower() and ".venv" not in exec_path
                    ):
                        kwargs["executable"] = venv_py_str

                super().__init__(*args, **kwargs)

        _sp.Popen = _HopePatchedPopen
    except Exception:
        pass


def _hope_acquire_pid_lock(lock_path: str | None = None) -> int:
    """
    HOPE single-instance lock with stale cleanup (Windows-safe).
    - If lock exists and PID is alive -> exit(0)
    - If lock exists but PID is dead/invalid -> remove and retry
    - Create lock via O_EXCL to avoid races
    """
    import os
    import time
    from pathlib import Path

    def _is_pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name != "nt":
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, 0, pid
            )
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            return False
        except Exception:
            # Fail-safe: if we cannot check, assume alive to avoid DUP.
            return True

    if lock_path is None:
        root = Path(__file__).resolve().parent.parent
        lock_path = str(root / "state" / "pids" / "run_live_v5.lock")

    p = Path(lock_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    my_pid = os.getpid()
    attempts = 30
    sleep_s = 0.20

    for _ in range(attempts):
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, (str(my_pid) + "\n").encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)

            # Write standard PID file for launcher (engine.pid)
            try:
                standard_pid = p.parent / "engine.pid"
                standard_pid.write_text(f"{my_pid}\n", encoding="utf-8")
            except Exception:
                pass  # Non-critical, lock file is the source of truth

            # Write PID-Truth file for launcher (role=ENGINE)
            try:
                pid_dir = p.parent / "pids"
                pid_dir.mkdir(parents=True, exist_ok=True)
                pid_path = pid_dir / "ENGINE.pid"
                pid_path.write_text(f"{my_pid}\n", encoding="utf-8")
            except Exception:
                pass

            return my_pid
        except FileExistsError:
            try:
                raw = p.read_text(encoding="utf-8-sig", errors="ignore").strip()
                existing_pid = int(raw) if raw else 0
            except Exception:
                existing_pid = 0

            if existing_pid > 0 and _is_pid_alive(existing_pid):
                import sys
                sys.stderr.write(f"[HOPE] ENGINE already running (PID {existing_pid})\n")
                sys.stderr.flush()
                raise SystemExit(42)  # Exit code 42 = already running (graceful refuse)

            try:
                p.unlink()
                print(f"stale lock removed: {p} pid={existing_pid}")
            except Exception:
                time.sleep(sleep_s)
        except Exception:
            time.sleep(sleep_s)

    print(f"cannot acquire engine lock: {p}")
    raise SystemExit(2)


def acquire_pid_lock(role: str = "engine") -> bool:
    """Compatibility wrapper expected by main(): returns True if lock acquired."""
    root = Path(__file__).resolve().parent.parent
    lock_name = "run_live_v5.lock" if role == "engine" else f"{role}.lock"
    lock_path = str(root / "state" / "pids" / lock_name)
    _hope_acquire_pid_lock(lock_path)
    return True


def _hope_install_log_redaction():
    # Redact Telegram bot token in any log line containing api.telegram.org/bot<id>:<token>/...
    import logging
    import re as _re

    class _RedactFilter(logging.Filter):
        _pat = _re.compile(r"(https://api\.telegram\.org/bot\d+):([^/\s]+)")

        def filter(self, record: logging.LogRecord) -> bool:
            try:
                msg = record.getMessage()
                if msg and "api.telegram.org/bot" in msg:
                    red = self._pat.sub(r"\1:<REDACTED>", msg)
                    if red != msg:
                        record.msg = red
                        record.args = ()
            except Exception:
                pass
            return True

    f = _RedactFilter()
    root = logging.getLogger()
    try:
        root.addFilter(f)
    except Exception:
        pass

    for name in ("httpx", "telegram", "telegram.request", "telegram.ext"):
        try:
            logging.getLogger(name).addFilter(f)
        except Exception:
            pass


def _hope_early_guard(lock_name: str, install_redaction: bool = False):
    # Run as early as possible (import time) to prevent clones.
    root = _hope_project_root()
    _hope_venv_guard(root)
    _hope_acquire_pid_lock(root / "state" / "pids" / lock_name)
    if install_redaction:
        _hope_install_log_redaction()


# --- HOPE_SINGLETON_HARDENING_END ---

# HOPE_SINGLETON_EARLY_GUARD_PATCH
_hope_early_guard("run_live_v5.lock", install_redaction=False)
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# ---------------------------------------------
# -*- coding: utf-8 -*-
"""
run_live_v5.py - HOPE Engine v5.14

Changes in v5.14:
  - Atomic JSONL queue: Prevents signal loss via atomic rename pattern (claim -> process -> finalize)
  - Decision log completeness: All exit branches now log decisions (QTY_ZERO, BUY_ERROR, etc)
  - Deduplication fix: Load _seen_ids from decisions on startup, mark only after final decision
  - Health file race fix: Write hunters data to separate file to avoid race with HealthMonitor
  - Version alignment: Unified version numbering

Changes in v5.13:
  - HUNTERS Risk Profiles: Dynamic risk calculation via compute_hunters_risk() based on
    profile + verdict + equity.
    Writes HUNTERS-specific risk to state/hunters_trades.jsonl (via profile + verdict + equity).
  - Price fetch improvements: Uses ExchangeClient.fetch_last_price()
    Fallback chain in _safe_get_price() with fallback to alternative price sources.
  - Validation improvements: Validates risk_usd <= 0 and price <= 0
    Writes trade records to state/hunters_trades.jsonl (via log_trade() or Engine).

Changes in v5.12:
  - HUNTERS Trades Logging: Writes HUNTERS-specific trades to state/hunters_trades.jsonl
    tools.hunters_trade_logger_v1.log_trade(...) writes trades to
    state/hunters_trades.jsonl.

Changes in v5.11:
  - HUNTERS Daily PnL: Tracks daily PnL for trades with src == "HUNTERS".
  - HUNTERS Daily Stop: Stops trading when daily loss limit is hit via hunters.daily_loss_limit_usd.
  - HUNTERS Lock: Locks HUNTERS trading when daily loss limit is exceeded.

Changes in v5.10:
  - HUNTERS Core Integration: Reads scored HUNTERS signals from hunters_signals_scored.jsonl and queues them to pending queue.
  - pending_ttl_sec and pending_soft_limit configurable via hunters section in risk_v5.yaml.

Common features (not version-specific, HUNTERS):
  - Supports verdict: STRONG, WEAK, ENTER, BUY, LONG, BORDERLINE.
  - STRONG -> uses profile HUNTERS_BOOST.
  - WEAK   -> uses profile HUNTERS_SAFE.
  - Others -> uses profile HUNTERS_SCALP.
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import argparse
import json
import logging
import os
import time
from collections import Counter
from typing import Any, Dict

from dotenv import load_dotenv

try:
    from minibot.notifications_schema_v5 import (
        build_open_notification,
        build_close_notification,
        append_notification,
        build_info_notification,
    )
    from minibot.core.types import (
        EngineMode,
        EngineStatus,
        TradeSide,
        PositionInfo,
        PositionState,
    )
    from minibot.core.storage_v5 import PositionStorageV5
    from minibot.core.monitoring import HealthMonitor
    from minibot.core.exchange_client import ExchangeClient
    from minibot.core.risk_manager_v1 import RiskManagerV1
    from minibot.hope_liquidity_guard import LiquidityGuard
    from tools.hunters_trade_logger_v1 import log_trade
    from minibot.pid_lock import acquire_pid_lock, release_pid_lock
except ImportError:
    print("CRITICAL: Minibot modules not found. Run from project root.")
    raise SystemExit(1)

ROOT_DIR = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT_DIR / "state"
SIGNALS_FILE = STATE_DIR / "signals_v5.jsonl"
HUNTERS_SCORED_SIGNALS = STATE_DIR / "hunters_signals_scored.jsonl"
HEALTH_FILE = STATE_DIR / "health_v5.json"
STOP_FLAG_FILE = STATE_DIR / "STOP.flag"
EXEC_POS_FILE = STATE_DIR / "exec_positions_v5.json"
ENV_FILE = ROOT_DIR / ".env"

# Encoding: read with utf-8-sig (handles BOM), write with utf-8 (no BOM)
JSON_ENCODING_READ = "utf-8-sig"
JSON_ENCODING_WRITE = "utf-8"

DEFAULT_PENDING_SOFT_LIMIT = 10
DEFAULT_PENDING_TTL_SEC = 900

ENGINE_VERSION = "5.14"

logger = logging.getLogger("run_live_v5")


class HOPEEngineV5:
    def __init__(
        self,
        mode: EngineMode,
        storage: PositionStorageV5,
        health: HealthMonitor,
        exchange: ExchangeClient,
        risk: RiskManagerV1,
        start_ts: float,
    ) -> None:
        self.mode = mode
        self.storage = storage
        self.health = health
        self.exchange = exchange
        self.risk = risk
        self.start_ts = start_ts

        self._positions = self.storage.load_positions()
        self._last_queue_size = 0
        self._trading_paused = False
        self._seen_ids: set[str] = set()
        self._load_seen_ids_from_decisions()  # Load persisted decisions to prevent duplicates after restart

        self._pending_signals: list[dict] = []

        self._daily_stop_alerted = False
        self._pending_overload_alerted = False
        self._hunters_daily_stop_alerted = False

        cfg: Dict[str, Any] = getattr(self.risk, "cfg", {}) or {}
        hunters_cfg: Dict[str, Any] = cfg.get("hunters", {}) or {}

        try:
            self.pending_queue_soft_limit = max(
                1,
                int(hunters_cfg.get("pending_soft_limit", DEFAULT_PENDING_SOFT_LIMIT)),
            )
        except Exception:
            self.pending_queue_soft_limit = DEFAULT_PENDING_SOFT_LIMIT

        try:
            self.pending_ttl_sec = max(
                60, int(hunters_cfg.get("pending_ttl_sec", DEFAULT_PENDING_TTL_SEC))
            )
        except Exception:
            self.pending_ttl_sec = DEFAULT_PENDING_TTL_SEC

        self.hunters_base_risk_usd: float = float(
            hunters_cfg.get("base_risk_usd", 0.0) or 0.0
        )
        self.hunters_daily_loss_limit_usd: float = float(
            hunters_cfg.get("daily_loss_limit_usd", 0.0) or 0.0
        )

        self.hunters_daily_pnl: float = 0.0
        self.hunters_trades: int = 0
        self.hunters_blocked_signals: int = 0
        self.hunters_locked: bool = False

        # Equity cache (prevents DRY hangs on API calls like fetch_balance)
        # In DRY mode we DON'T call exchange.fetch_balance at all; we use a cached/default equity.
        try:
            self._equity_usd_cache: float = float(
                hunters_cfg.get("default_equity_usd", 1000.0) or 1000.0
            )
        except Exception:
            self._equity_usd_cache = 1000.0
        self._equity_usd_ts: float = 0.0
        try:
            self._equity_refresh_sec: float = float(
                hunters_cfg.get("equity_refresh_sec", 30.0) or 30.0
            )
        except Exception:
            self._equity_refresh_sec = 30.0

        # Loop heartbeat
        self._last_heartbeat_ts: float = time.time()
        self._loop_counter: int = 0

        # --- P3 Liquidity Guard (v5.14) ---
        self.liquidity_guard = LiquidityGuard(cfg)
        logger.info("LiquidityGuard initialized from risk config")

        # === HUNTERS Percentile Thresholds ===
        self.hunters_score_thr_strong = 14.25
        self.hunters_score_thr_ok = 12.0
        self.hunters_score_thr_weak = 9.1

        try:
            thr_path = STATE_DIR / "hunters_thresholds.json"
            if thr_path.is_file():
                import json as _json_thr

                cfg = _json_thr.loads(thr_path.read_text(encoding=JSON_ENCODING_READ))
                t = cfg.get("thresholds") or {}
                self.hunters_score_thr_strong = float(t.get("strong", 14.25) or 14.25)
                self.hunters_score_thr_ok = float(t.get("ok", 12.0) or 12.0)
                self.hunters_score_thr_weak = float(t.get("weak", 9.1) or 9.1)
        except Exception as e:
            logger.warning("HUNTERS thresholds load failed: %s (using defaults)", e)

        logger.info(
            "HUNTERS percentile thresholds: strong>=%.4f ok>=%.4f weak>=%.4f",
            self.hunters_score_thr_strong,
            self.hunters_score_thr_ok,
            self.hunters_score_thr_weak,
        )

        logger.info(
            "Pending config (hunters): soft_limit=%d, ttl=%ds",
            self.pending_queue_soft_limit,
            self.pending_ttl_sec,
        )
        logger.info(
            "HUNTERS risk: base_risk_usd=%.2f, daily_loss_limit_usd=%.2f",
            self.hunters_base_risk_usd,
            self.hunters_daily_loss_limit_usd,
        )

        # P2: Load audit configuration
        try:
            from minibot import hope_audit
            audit_cfg = hope_audit.AuditConfig.load_from_dict(cfg)
            hope_audit.set_audit_config(audit_cfg)
            if audit_cfg.enabled:
                logger.info("P2 Audit enabled: signals=%s queue=%s dedup=%s risk=%s",
                           audit_cfg.log_signals, audit_cfg.log_queue,
                           audit_cfg.log_dedup, audit_cfg.log_risk)
        except Exception as e:
            logger.warning("P2 Audit init failed: %s (disabled)", e)

    def _load_seen_ids_from_decisions(self) -> None:
        """Load signal_ids from recent decisions to prevent duplicates after restart."""
        try:
            decisions_path = STATE_DIR / "hunters_decisions.jsonl"
            if not decisions_path.exists():
                return

            # Read last 1000 lines (enough for deduplication)
            try:
                txt = decisions_path.read_text(encoding=JSON_ENCODING_READ)
                lines = txt.splitlines()
                tail = lines[-1000:] if len(lines) > 1000 else lines

                for line in tail:
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                        sig_id = obj.get("signal_id")
                        if sig_id:
                            self._seen_ids.add(str(sig_id))
                    except Exception:
                        continue

                logger.info("Loaded %d seen_ids from decisions", len(self._seen_ids))
            except Exception as e:
                logger.warning("Failed to load seen_ids from decisions: %s", e)
        except Exception as e:
            logger.warning("Error loading seen_ids: %s", e)

    # === HUNTERS Risk Profiles ===

    def _safe_get_price(self, symbol: str) -> float:
        """
        Price fetch improvements: Uses ExchangeClient.fetch_last_price()
        Fallback chain in _safe_get_price() with fallback to alternative price sources.
        """
        # In DRY mode we must not block on network/API.
        if getattr(self, "mode", None) == EngineMode.DRY:
            return 0.0

        try:
            if hasattr(self.exchange, "get_price"):
                price = self.exchange.get_price(symbol)
                if price is not None and float(price) > 0:
                    return float(price)

            if hasattr(self.exchange, "fetch_last_price"):
                price = self.exchange.fetch_last_price(symbol)
                if price is not None and float(price) > 0:
                    return float(price)

            if hasattr(self.exchange, "fetch_ticker"):
                ticker = self.exchange.fetch_ticker(symbol)
                if isinstance(ticker, dict):
                    for key in ("last", "close", "price"):
                        v = ticker.get(key)
                        if v and float(v) > 0:
                            return float(v)
                else:
                    for attr in ("last", "close", "price"):
                        v = getattr(ticker, attr, None)
                        if v and float(v) > 0:
                            return float(v)

        except Exception as e:
            logger.error("Price fetch error for %s: %s", symbol, e)

        return 0.0

    def _get_equity_usd(self) -> float:
        """Return account equity in USD.

        Key rule: In DRY mode we must not block on any network/API call.
        In LIVE/TESTNET we refresh equity at most once per _equity_refresh_sec seconds.
        """
        try:
            # DRY: never touch the network here.
            if self.mode == EngineMode.DRY:
                return float(self._equity_usd_cache or 1000.0)

            now = time.time()
            if self._equity_usd_ts > 0 and (now - self._equity_usd_ts) < float(
                self._equity_refresh_sec or 30.0
            ):
                return float(self._equity_usd_cache or 1000.0)

            t0 = time.time()
            bal = self.exchange.fetch_balance()  # may be slow; avoid calling too often
            t1 = time.time()
            if (t1 - t0) > 5.0:
                logger.warning("Balance fetch slow: %.2fs", (t1 - t0))

            equity = float(getattr(bal, "total_usd", 0.0) or 0.0)
            if equity > 0:
                self._equity_usd_cache = equity
                self._equity_usd_ts = now
            return float(self._equity_usd_cache or 1000.0)
        except Exception as e:
            # Keep last known equity; don't crash the engine.
            try:
                logger.warning("Balance fetch failed (using cached): %s", e)
            except Exception:
                pass
            return float(self._equity_usd_cache or 1000.0)

    def _compute_risk_usd(
        self, profile: str, raw: dict, is_hunters: bool, equity_usd: float
    ) -> float:
        """
        Risk calculation in USD:
          - Fixed risk per trade via RiskManagerV1.get_risk_for_profile(profile)
          - HUNTERS-specific risk via RiskManagerV1.compute_hunters_risk(profile, verdict, equity)
        """
        if not is_hunters:
            return float(self.risk.get_risk_for_profile(profile))

        verdict = str(raw.get("hunters_verdict") or raw.get("verdict") or "").upper()

        func = getattr(self.risk, "compute_hunters_risk", None)
        if callable(func):
            try:
                risk_val = func(profile, verdict, equity_usd)
                if isinstance(risk_val, (int, float)) and risk_val >= 0:
                    logger.info(
                        "HUNTERS risk: profile=%s, verdict=%s, equity=%.2f -> risk=%.3f",
                        profile,
                        verdict,
                        equity_usd,
                        risk_val,
                    )

                    # P2: Log risk calculation details
                    try:
                        from minibot import hope_audit
                        sig_id = raw.get("signal_id") or "UNKNOWN"
                        # Extract multipliers from risk manager
                        profile_mult = self.risk.get_hunters_profile_multiplier(profile)
                        verdict_mult = self.risk.get_verdict_multiplier(verdict)
                        base_risk_usd = self.hunters_base_risk_usd
                        # Calculate equity cap
                        raw_risk = base_risk_usd * profile_mult * verdict_mult
                        equity_cap = risk_val / raw_risk if raw_risk > 0 else 1.0
                        hope_audit.safe_log_risk_calc(
                            sig_id, profile, verdict, base_risk_usd,
                            profile_mult, verdict_mult, equity_cap, risk_val
                        )
                    except Exception:
                        pass

                    return float(risk_val)
            except Exception as e:
                logger.error("compute_hunters_risk error: %s", e)

        return float(self.risk.get_risk_for_profile(profile))

    # --- HUNTERS Signal Reading Logic ---
    def _read_new_hunter_signals(self) -> list[dict]:
        """
        Read scored HUNTERS signals from hunters_signals_scored.jsonl.
        """
        if self.hunters_locked:
            logger.info(
                "HUNTERS locked by daily loss limit (pnl=%.2f, limit=%.2f)",
                self.hunters_daily_pnl,
                self.hunters_daily_loss_limit_usd,
            )
            return []

        new_raw_signals: list[dict] = []

        try:
            if not HUNTERS_SCORED_SIGNALS.exists():
                return []

            txt = HUNTERS_SCORED_SIGNALS.read_text(encoding=JSON_ENCODING_READ).strip()
            if not txt:
                return []

            lines = txt.splitlines()
            tail = lines[-100:]

            now = time.time()

            for line in tail:
                if not line.strip():
                    continue

                try:
                    sig = json.loads(line)
                except Exception:
                    continue

                symbol = str(sig.get("symbol") or "").upper()
                if not symbol:
                    continue

                ts = float(sig.get("timestamp") or sig.get("ts") or 0.0) or 0.0
                signal_id = sig.get("signal_id") or f"HUNTERS:{symbol}:{int(ts)}"

                if signal_id in self._seen_ids:
                    # P2: Log deduplication rejection
                    try:
                        from minibot import hope_audit
                        hope_audit.safe_log_dedup_reject(signal_id, ts, now)
                    except Exception:
                        pass
                    continue

                if ts <= 0 or now - ts > self.pending_ttl_sec:
                    continue

                old_verdict = str(sig.get("verdict") or "").upper()
                final_score = (
                    float(
                        sig.get("final_score") or sig.get("hunters_final_score") or 0.0
                    )
                    or 0.0
                )
                if final_score <= 0.0:
                    continue

                # Percentile-based classification (v5.14)
                thr_strong = float(
                    getattr(self, "hunters_score_thr_strong", 14.25) or 14.25
                )
                thr_ok = float(getattr(self, "hunters_score_thr_ok", 12.0) or 12.0)
                thr_weak = float(getattr(self, "hunters_score_thr_weak", 9.1) or 9.1)

                if final_score >= thr_strong:
                    verdict_raw = "STRONG"
                elif final_score >= thr_ok:
                    verdict_raw = "ENTER"
                elif final_score >= thr_weak:
                    verdict_raw = "WEAK"
                else:
                    continue

                if verdict_raw == "STRONG":
                    profile = "HUNTERS_BOOST"
                elif verdict_raw == "WEAK":
                    profile = "HUNTERS_SAFE"
                else:
                    profile = str(sig.get("profile") or "HUNTERS_SCALP").upper()
                # DO NOT add to _seen_ids here - only after successful queue append

                raw = {
                    "signal_id": signal_id,
                    "ts": ts,
                    "symbol": symbol,
                    "side": "LONG",
                    "price": float(
                        (
                            sig.get("entry_price")
                            or sig.get("price")
                            or sig.get("mark_price")
                            or sig.get("entry")
                            or 0.0
                        )
                        or (
                            (sig.get("hunters_raw") or {}).get("entry_price")
                            or (sig.get("hunters_raw") or {}).get("price")
                            or 0.0
                        )
                        or 0.0
                    ),
                    "source": "HUNTERS",
                    "profile": profile,
                    "hunters_final_score": final_score,
                    "hunters_verdict": verdict_raw,
                    "hunters_raw": sig,
                    "_queued_ts": now,
                }

                new_raw_signals.append(raw)

                # P2: Log signal ingestion
                try:
                    from minibot import hope_audit
                    ttl_remaining = self.pending_ttl_sec - (now - ts)
                    hope_audit.safe_log_signal_read(
                        signal_id, symbol, ts, final_score, verdict_raw, profile, ttl_remaining
                    )
                except Exception:
                    pass

        except Exception as e:
            logger.error("Error reading HUNTERS signals: %s", e)

        return new_raw_signals

    def _send_alert(self, title: str, message: str) -> None:
        try:
            notif = build_info_notification(
                title=title,
                message=message,
                mode=self.mode.value,
            )
            append_notification(notif)
            logger.warning("HUNTERS ALERT: %s - %s", title, message)
        except Exception as e:
            logger.error("Alert send error: %s", e)

    def _check_proactive_alerts(self) -> None:
        if self.risk.is_locked and not self._daily_stop_alerted:
            self._send_alert(
                "DAILY STOP HIT",
                f"DAILY STOP HIT! PnL: {self.risk.daily_pnl:.2f} USD.",
            )
            self._daily_stop_alerted = True

        if not self.risk.is_locked:
            self._daily_stop_alerted = False

        pq_len = len(self._pending_signals)
        critical_threshold = self.pending_queue_soft_limit * 2

        if pq_len > critical_threshold and not self._pending_overload_alerted:
            self._send_alert(
                "PENDING OVERLOAD",
                f"Queue full: {pq_len} signals waiting (limit {self.pending_queue_soft_limit}).",
            )
            self._pending_overload_alerted = True

        if pq_len <= self.pending_queue_soft_limit:
            self._pending_overload_alerted = False

        if self.hunters_locked and not self._hunters_daily_stop_alerted:
            self._send_alert(
                "HUNTERS STOP HIT",
                f"HUNTERS PnL: {self.hunters_daily_pnl:.2f}, limit: {self.hunters_daily_loss_limit_usd:.2f}",
            )
            self._hunters_daily_stop_alerted = True

        if not self.hunters_locked:
            self._hunters_daily_stop_alerted = False

    def _classify_queue_state(self, pending_len: int, soft_limit: int) -> str:
        if soft_limit <= 0:
            return "UNKNOWN"
        if pending_len <= 0:
            return "IDLE"

        ratio = pending_len / float(soft_limit)
        if ratio < 0.8:
            return "OK"
        if ratio < 1.0:
            return "WARM"
        if ratio <= 1.2:
            return "WARNING"
        return "BLOCK"

    def update_health(self, now_ts: float) -> None:
        try:
            open_pos = [p for p in self._positions if p.state == PositionState.OPEN]

            prof_counter: Counter[str] = Counter()
            for p in open_pos:
                prof = (p.tags or {}).get("profile", "UNKNOWN")
                prof = str(prof).upper().replace("HUNTERS_", "")
                prof_counter[prof] += 1

            q_size = len(self._pending_signals)
            self._last_queue_size = q_size

            st = EngineStatus(
                mode=self.mode,
                open_positions=open_pos,
                last_heartbeat_ts=now_ts,
            )

            st.daily_pnl_usd = self.risk.daily_pnl
            st.daily_stop_hit = self.risk.is_locked
            st.risk_blocked_count = self.risk.blocked_count
            st.profile_stats = dict(prof_counter)
            st.pending_queue_len = len(self._pending_signals)
            st.pending_queue_soft_limit = self.pending_queue_soft_limit

            self.health.update(st, now_ts - self.start_ts, q_size, now_ts)

            try:
                data: Dict[str, Any] = {}
                if HEALTH_FILE.exists():
                    raw = HEALTH_FILE.read_text(encoding=JSON_ENCODING_READ) or "{}"
                    obj = json.loads(raw)
                    data = obj if isinstance(obj, dict) else {}

                data["pending_queue_len"] = len(self._pending_signals)
                data["pending_queue_soft_limit"] = self.pending_queue_soft_limit

                hunters_block: Dict[str, Any] = data.get("hunters") or {}
                hunters_block["pending_queue_len"] = len(self._pending_signals)
                hunters_block["pending_queue_soft_limit"] = (
                    self.pending_queue_soft_limit
                )
                hunters_block["queue_state"] = self._classify_queue_state(
                    len(self._pending_signals), self.pending_queue_soft_limit
                )
                hunters_block["daily_pnl_usd"] = self.hunters_daily_pnl
                hunters_block["daily_loss_limit_usd"] = (
                    self.hunters_daily_loss_limit_usd
                )
                hunters_block["locked"] = self.hunters_locked
                hunters_block["blocked_signals"] = self.hunters_blocked_signals

                data["hunters"] = hunters_block

                # Write hunters data to separate file to avoid race with HealthMonitor
                # HealthMonitor writes health_v5.json, we write hunters data separately
                hunters_health_file = STATE_DIR / "health_hunters.json"
                hunters_health_file.write_text(
                    json.dumps(hunters_block, indent=2), encoding=JSON_ENCODING_WRITE
                )
            except Exception as e:
                logger.error("Post health update error: %s", e)

            self._check_proactive_alerts()

        except Exception as e:
            logger.error("Health update error: %s", e)

    def process_signals(self, raw_signals: list[dict]) -> None:
        if STOP_FLAG_FILE.exists() and not self._trading_paused:
            self._trading_paused = True
            logger.warning("TRADING PAUSED (STOP.flag ON)")
        elif not STOP_FLAG_FILE.exists() and self._trading_paused:
            self._trading_paused = False
            logger.info("TRADING RESUMED (STOP.flag OFF)")

        for raw in raw_signals:
            try:
                sig_id = raw.get("signal_id") or f"{raw.get('ts')}_{raw.get('symbol')}"
                if sig_id in self._seen_ids:
                    continue

                side = str(raw.get("side", "")).upper()

                if self._trading_paused and side != "CLOSE":
                    continue

                # Process signal first, then mark as seen only if successfully processed
                processed = False
                if side == "LONG":
                    processed = self._do_long(raw, from_pending=False)
                elif side == "CLOSE":
                    self._do_close(raw)
                    processed = True  # CLOSE is always processed
                else:
                    logger.info("Unknown side: %r", raw)
                    processed = False

                # Mark as seen only after successful processing (prevents losing retries)
                # This ensures we don't reprocess signals that were already handled
                if processed:
                    self._seen_ids.add(sig_id)
                    # Also log decision for non-HUNTERS signals if needed
                    if not self._is_hunters_signal(raw):
                        try:
                            self._append_decision(
                                sig_id,
                                str(raw.get("symbol", "?")),
                                "",
                                0.0,
                                "TRADE" if processed else "SKIP",
                                "PROCESSED",
                                0.0,
                            )
                        except Exception:
                            pass
            except Exception as e:
                logger.error("Signal Error: %s", e)

    def _process_pending(self) -> None:
        if not self._pending_signals:
            return

        now = time.time()
        still_pending: list[dict] = []

        for raw in self._pending_signals:
            try:
                queued_ts = float(raw.get("_queued_ts") or 0.0) or now
                symbol = str(raw.get("symbol") or "?")
                sig_id = raw.get("signal_id") or f"{raw.get('ts')}_{symbol}"

                # 1) EXPIRE
                if now - queued_ts > self.pending_ttl_sec:
                    logger.info("Expired: %s", symbol)
                    try:
                        verdict = str(raw.get("hunters_verdict", "?") or "?").upper()
                        score = float(raw.get("hunters_final_score", 0.0) or 0.0)
                        self._append_decision(
                            sig_id,
                            symbol,
                            verdict,
                            score,
                            "EXPIRE",
                            "TTL_EXCEEDED",
                            queued_ts,
                        )
                        # Mark as seen after EXPIRE decision is logged
                        self._seen_ids.add(sig_id)

                        # P2: Log queue expiry
                        try:
                            from minibot import hope_audit
                            ttl_age = now - queued_ts
                            hope_audit.safe_log_queue_expire(sig_id, len(still_pending), ttl_age)
                        except Exception:
                            pass
                    except Exception:
                        pass
                    continue

                if self._trading_paused:
                    still_pending.append(raw)
                    continue

                ok = self._do_long(raw, from_pending=True)
                if ok:
                    try:
                        if self._is_hunters_signal(raw):
                            verdict = str(
                                raw.get("hunters_verdict") or raw.get("verdict") or ""
                            ).upper()
                            score = float(raw.get("hunters_final_score", 0.0) or 0.0)
                            self._append_decision(
                                sig_id,
                                symbol,
                                verdict,
                                score,
                                "TRADE",
                                "OPENED_FROM_PENDING",
                                queued_ts,
                            )
                    except Exception:
                        pass
                    # Mark as seen after successful trade
                    self._seen_ids.add(sig_id)
                    logger.info("From Pending: %s", symbol)

                    # P2: Log queue removal (processed)
                    try:
                        from minibot import hope_audit
                        hope_audit.safe_log_queue_remove(sig_id, len(still_pending), "PROCESSED")
                    except Exception:
                        pass
                else:
                    last_reason = str(raw.get("_last_reason") or "")
                    last_decision = str(raw.get("_last_decision") or "SKIP").upper()

                    if last_reason == "MAX_OPEN_POSITIONS":
                        still_pending.append(raw)
                    else:
                        try:
                            if self._is_hunters_signal(raw):
                                verdict = str(
                                    raw.get("hunters_verdict")
                                    or raw.get("verdict")
                                    or ""
                                ).upper()
                                score = float(
                                    raw.get("hunters_final_score", 0.0) or 0.0
                                )
                                self._append_decision(
                                    sig_id,
                                    symbol,
                                    verdict,
                                    score,
                                    last_decision,
                                    last_reason or "DO_LONG_FALSE",
                                    queued_ts,
                                )
                                # Mark as seen after decision is logged (non-retryable failure)
                                self._seen_ids.add(sig_id)
                        except Exception:
                            pass

            except Exception as e:
                logger.error("Pending error: %s", e)
                still_pending.append(raw)

        self._pending_signals = still_pending

    def _append_decision(
        self,
        signal_id: str,
        symbol: str,
        verdict: str,
        score: float,
        decision: str,
        reason: str,
        queued_ts: float = 0.0,
    ) -> None:
        """Append a single decision row to state\\hunters_decisions.jsonl (UTF-8, no BOM)."""
        import json
        import time

        try:
            now_ts = time.time()
            obj = {
                "signal_id": (signal_id or "?")[:80],
                "symbol": symbol or "?",
                "hunters_verdict": (verdict or "?").upper(),
                "hunters_final_score": float(score or 0.0),
                "decision": (decision or "?").upper(),
                "reason": reason or "NO_REASON",
                "queued_ts": float(queued_ts or 0.0),
                "decision_ts": float(now_ts),
                "engine_version": ENGINE_VERSION,
                "engine_mode": getattr(
                    self.mode, "value", str(getattr(self, "mode", "?"))
                ),
            }
            path = STATE_DIR / "hunters_decisions.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(obj, ensure_ascii=False) + "\n"
            # Atomic write with retry (handles file locks gracefully)
            try:
                from tools.atomic_jsonl_writer import atomic_append_jsonl

                if not atomic_append_jsonl(path, obj):
                    logger.warning("Decision log write failed (retries exhausted)")
            except ImportError:
                # Fallback to direct write if atomic writer not available
                with path.open("a", encoding=JSON_ENCODING_WRITE, newline="\n") as f:
                    f.write(line)
        except Exception as e:
            try:
                logger.warning("Decision log failed: %s", e)
            except Exception:
                pass

    def _is_hunters_signal(self, raw: dict) -> bool:
        src = str(raw.get("source") or "").upper()
        profile = str(raw.get("profile") or "").upper()
        return src.startswith("HUNTERS") or profile.startswith("HUNTERS_")

    def _do_long(self, raw: dict, from_pending: bool = False) -> bool:
        symbol = raw.get("symbol")
        profile = str(raw.get("profile", "SCALP"))
        if not symbol:
            return False

        is_hunters = self._is_hunters_signal(raw)

        queued_ts = float(raw.get("_queued_ts") or 0.0)
        sig_id = raw.get("signal_id") or f"{raw.get('ts')}_{symbol}"
        hv = str(raw.get("hunters_verdict") or raw.get("verdict") or "").upper()
        try:
            hs = float(raw.get("hunters_final_score", 0.0) or 0.0)
        except Exception:
            hs = 0.0

        def _set_last(decision: str, reason: str) -> None:
            raw["_last_decision"] = str(decision or "SKIP").upper()
            raw["_last_reason"] = str(reason or "NO_REASON")

        def _log_if_immediate(decision: str, reason: str) -> None:
            # Only log here for non-pending path; pending path is logged in _process_pending.
            try:
                if is_hunters and (not from_pending):
                    self._append_decision(
                        sig_id, symbol, hv, hs, decision, reason, queued_ts
                    )
            except Exception:
                pass

        if is_hunters and self.hunters_locked:
            self.hunters_blocked_signals += 1
            logger.warning(
                "HUNTERS BLOCKED %s [%s]: daily limit (pnl=%.2f, limit=%.2f)",
                symbol,
                profile,
                self.hunters_daily_pnl,
                self.hunters_daily_loss_limit_usd,
            )
            _set_last("BLOCK", "HUNTERS_DAILY_LIMIT")
            _log_if_immediate("BLOCK", "HUNTERS_DAILY_LIMIT")
            return False

        curr_pos = len([p for p in self._positions if p.state == PositionState.OPEN])
        equity = self._get_equity_usd()

        allowed, reason = self.risk.can_open_position(curr_pos, equity)

        if not allowed:
            _set_last("SKIP", reason)
            if (
                not from_pending
                and reason == "MAX_OPEN_POSITIONS"
                and not self.risk.is_locked
            ):
                raw_copy = dict(raw)
                raw_copy["_queued_ts"] = time.time()
                self._pending_signals.append(raw_copy)
                logger.info("HUNTERS Queued: %s [%s]", symbol, profile)
                _log_if_immediate("SKIP", "QUEUED_MAX_OPEN_POSITIONS")

                # P2: Log queue add
                try:
                    from minibot import hope_audit
                    sig_id = raw.get("signal_id") or f"HUNTERS:{symbol}:{int(time.time())}"
                    hope_audit.safe_log_queue_add(
                        sig_id,
                        len(self._pending_signals) - 1,
                        len(self._pending_signals),
                        self.pending_queue_soft_limit,
                        "MAX_OPEN_POSITIONS"
                    )
                except Exception:
                    pass
            elif not from_pending:
                logger.warning("HUNTERS Block %s: %s", symbol, reason)
                _set_last("BLOCK", reason)
                _log_if_immediate("BLOCK", reason)
            return False

        # === HUNTERS Risk Profiles: compute_hunters_risk() ===
        risk_usd = self._compute_risk_usd(profile, raw, is_hunters, equity)
        if risk_usd <= 0:
            logger.warning(
                "Skip %s [%s]: risk_usd=%.4f <= 0",
                symbol,
                profile,
                risk_usd,
            )
            _set_last("SKIP", "RISK_USD_LE_0")
            _log_if_immediate("SKIP", "RISK_USD_LE_0")
            return False

        price = float(raw.get("price", 0.0) or 0.0)
        if price <= 0:
            price = self._safe_get_price(symbol)

        if price <= 0:
            logger.error("Cannot get price for %s - skip.", symbol)
            _set_last("SKIP", "NO_PRICE")
            _log_if_immediate("SKIP", "NO_PRICE")
            return False

        qty = risk_usd / price
        qty = round(qty, 5) if "BTC" in symbol else round(qty, 1)

        # GUARD: block zero/negative qty after rounding
        if qty <= 0:
            logger.warning(
                "SAFETY: qty<=0 -> SKIP %s risk_usd=%.4f price=%.8f qty=%s",
                symbol,
                float(risk_usd),
                float(price),
                str(qty),
            )
            _set_last("SKIP", "QTY_ZERO")
            _log_if_immediate("SKIP", "QTY_ZERO")
            return False

        # === P3 Liquidity Guard (v5.14) ===
        orderbook = self.exchange.fetch_order_book(symbol, limit=5)
        liq_allowed, liq_reason = self.liquidity_guard.check_liquidity(
            symbol=symbol,
            side="BUY",
            risk_usd=risk_usd,
            orderbook=orderbook,
        )
        if not liq_allowed:
            logger.warning(
                "LIQUIDITY_BLOCK %s [%s]: %s (risk=$%.2f)",
                symbol,
                profile,
                liq_reason,
                risk_usd,
            )
            _set_last("BLOCK", liq_reason)
            _log_if_immediate("BLOCK", liq_reason)
            return False

        try:
            logger.info(
                "HUNTERS BUY %s [%s] $%.2f qty=%s", symbol, profile, risk_usd, qty
            )
            fill_price, fill_qty = price, qty

            if self.mode == EngineMode.LIVE:
                # PRE-FLIGHT CHECKS (Phase 1: Balance, Fat-Finger, Max Position USD)
                import os

                # 1. Balance check
                try:
                    balance = self.exchange.fetch_balance()
                    required_usd = qty * price * 1.005  # +0.5% fee buffer
                    if balance.free_usd < required_usd:
                        logger.error(
                            "SAFETY BLOCK: insufficient balance (need=%.2f, have=%.2f) %s",
                            required_usd, balance.free_usd, symbol
                        )
                        _set_last("SKIP", "INSUFFICIENT_BALANCE")
                        _log_if_immediate("SKIP", "INSUFFICIENT_BALANCE")
                        return False
                except Exception as e:
                    logger.error("SAFETY BLOCK: balance check FAILED (fail-closed): %s", e)
                    _set_last("SKIP", "BALANCE_CHECK_FAILED")
                    _log_if_immediate("SKIP", "BALANCE_CHECK_FAILED")
                    return False

                # 2. Fat-finger protection (5% price deviation)
                try:
                    fresh_price = self._safe_get_price(symbol)
                    if fresh_price > 0:
                        deviation = abs(fresh_price - price) / price
                        max_dev = float(os.environ.get("HOPE_MAX_PRICE_DEVIATION_PCT", "5.0")) / 100.0
                        if deviation > max_dev:
                            logger.error(
                                "SAFETY BLOCK: price moved %.1f%% (signal=%.8f, market=%.8f, max=%.1f%%) %s",
                                deviation * 100, price, fresh_price, max_dev * 100, symbol
                            )
                            _set_last("SKIP", "PRICE_DEVIATION_TOO_HIGH")
                            _log_if_immediate("SKIP", "PRICE_DEVIATION_TOO_HIGH")
                            return False
                except Exception as e:
                    logger.error("SAFETY BLOCK: fat-finger check FAILED (fail-closed): %s", e)
                    _set_last("SKIP", "FATFINGER_CHECK_FAILED")
                    _log_if_immediate("SKIP", "FATFINGER_CHECK_FAILED")
                    return False

                # 3. Max position USD enforcement
                try:
                    position_usd = qty * price
                    max_pos_usd = float(os.environ.get("HOPE_MAX_POSITION_USD", "100.0"))
                    if position_usd > max_pos_usd:
                        logger.error(
                            "SAFETY BLOCK: position too large (size=%.2f, max=%.2f) %s",
                            position_usd, max_pos_usd, symbol
                        )
                        _set_last("SKIP", "POSITION_TOO_LARGE")
                        _log_if_immediate("SKIP", "POSITION_TOO_LARGE")
                        return False
                except Exception as e:
                    logger.error("SAFETY BLOCK: max position check FAILED (fail-closed): %s", e)
                    _set_last("SKIP", "MAXPOS_CHECK_FAILED")
                    _log_if_immediate("SKIP", "MAXPOS_CHECK_FAILED")
                    return False

                # PRE-FLIGHT PASSED: place order
                order = self.exchange.create_market_order(symbol, "BUY", qty)
                fill_price = float(getattr(order, "price", price))
                fill_qty = float(getattr(order, "qty", qty))

            tags: Dict[str, Any] = {
                "src": raw.get("source"),
                "profile": profile,
                "risk_usd": risk_usd,
                "entry_price": fill_price,
            }
            if is_hunters:
                tags["hunters_verdict"] = str(
                    raw.get("hunters_verdict") or raw.get("verdict") or ""
                ).upper()
                try:
                    tags["hunters_final_score"] = float(
                        raw.get("hunters_final_score", 0.0) or 0.0
                    )
                except Exception:
                    tags["hunters_final_score"] = 0.0

            new_pos = PositionInfo(
                symbol=symbol,
                side=TradeSide.LONG,
                qty=fill_qty,
                avg_price=fill_price,
                size_usd=fill_qty * fill_price,
                tags=tags,
                state=PositionState.OPEN,
                created_at=time.time(),
                updated_at=time.time(),
            )
            self._positions.append(new_pos)
            self.storage.save_positions(self._positions)

            notif = build_open_notification(
                symbol=symbol,
                side="LONG",
                price=fill_price,
                qty=fill_qty,
                mode=self.mode.value,
                reason="OPEN",
            )
            append_notification(notif)
            logger.info("HOPE OPEN %s", symbol)
            _log_if_immediate("TRADE", "OPENED")
            return True
        except Exception as e:
            logger.error("Buy Error: %s", e)
            _set_last("SKIP", "BUY_ERROR")
            _log_if_immediate("SKIP", "BUY_ERROR")
            return False

    def _do_close(self, raw: dict) -> None:
        symbol = raw.get("symbol")
        if not symbol:
            return

        target = next(
            (
                p
                for p in self._positions
                if p.symbol == symbol and p.state == PositionState.OPEN
            ),
            None,
        )
        if not target:
            return

        try:
            logger.info("HUNTERS SELL %s", symbol)
            exit_price = float(raw.get("price", 0.0) or 0.0)
            if exit_price <= 0:
                exit_price = self._safe_get_price(symbol)

            # CRITICAL FIX: In DRY mode, if exit_price is still 0, use entry_price to avoid fake losses
            if exit_price <= 0 and self.mode == EngineMode.DRY:
                tags = target.tags or {}
                entry_price = float(
                    tags.get("entry_price", 0.0) or target.avg_price or 0.0
                )
                if entry_price > 0:
                    exit_price = entry_price
                    logger.info(
                        "DRY close: using entry_price=%.8f (no network price available)",
                        entry_price,
                    )
                else:
                    exit_price = target.avg_price
                    logger.warning("DRY close: fallback to avg_price=%.8f", exit_price)

            # === P3 Liquidity Guard (v5.14) - SELL side ===
            # NOTE: We don't hard-block on SELL (must close position eventually),
            # but we log warnings for poor liquidity that may cause slippage
            risk_usd = float(target.qty) * exit_price
            orderbook = self.exchange.fetch_order_book(symbol, limit=5)
            liq_allowed, liq_reason = self.liquidity_guard.check_liquidity(
                symbol=symbol,
                side="SELL",
                risk_usd=risk_usd,
                orderbook=orderbook,
            )
            if not liq_allowed:
                logger.warning(
                    "LIQUIDITY_POOR_SELL %s: %s (executing anyway - must close position)",
                    symbol,
                    liq_reason,
                )

            if self.mode == EngineMode.LIVE:
                # PRE-FLIGHT CHECK: Fat-finger protection for SELL
                import os
                try:
                    fresh_price = self._safe_get_price(symbol)
                    if fresh_price > 0 and exit_price > 0:
                        deviation = abs(fresh_price - exit_price) / exit_price
                        max_dev = float(os.environ.get("HOPE_MAX_PRICE_DEVIATION_PCT", "5.0")) / 100.0
                        if deviation > max_dev:
                            logger.error(
                                "SAFETY BLOCK SELL: price moved %.1f%% (expected=%.8f, market=%.8f, max=%.1f%%) %s",
                                deviation * 100, exit_price, fresh_price, max_dev * 100, symbol
                            )
                            return False  # Skip close, position remains open
                except Exception as e:
                    logger.error("SAFETY BLOCK SELL: fat-finger check FAILED (fail-closed): %s", e)
                    return False

                order = self.exchange.create_market_order(
                    symbol, "SELL", float(target.qty)
                )
                exit_price = float(getattr(order, "price", exit_price))

            pnl = (exit_price - target.avg_price) * float(target.qty)
            self.risk.update_pnl(pnl)

            tags = target.tags or {}
            src = str(tags.get("src") or "").upper()
            if src.startswith("HUNTERS"):
                self.hunters_daily_pnl += pnl
                self.hunters_trades += 1

                try:
                    profile = str(tags.get("profile") or "HUNTERS_SCALP")
                    risk_usd = float(tags.get("risk_usd", 0.0) or 0.0)

                    trade_id = getattr(target, "id", None) or getattr(
                        target, "position_id", None
                    )

                    opened_ts = float(
                        getattr(target, "created_at", 0.0)
                        or getattr(target, "open_ts", 0.0)
                    )

                    side_attr = getattr(target, "side", None)
                    if isinstance(side_attr, TradeSide):
                        side_str = side_attr.value
                    else:
                        side_str = str(side_attr or "LONG")

                    verdict = str(tags.get("hunters_verdict") or "").upper()
                    try:
                        final_score = float(tags.get("hunters_final_score", 0.0) or 0.0)
                    except Exception:
                        final_score = 0.0

                    closed_ts = time.time()

                    entry_price = float(
                        tags.get("entry_price", 0.0) or target.avg_price or 0.0
                    )

                    trade_payload: Dict[str, Any] = {
                        "symbol": target.symbol,
                        "side": side_str,
                        "status": "CLOSED",
                        "pnl_usd": float(pnl),
                        "risk_usd": risk_usd,
                        "profile": profile,
                        "source": src,
                        "trade_id": trade_id,
                        "opened_ts": opened_ts,
                        "closed_ts": closed_ts,
                        "updated_ts": closed_ts,
                        "hunters_verdict": verdict,
                        "hunters_final_score": final_score,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                    }

                    log_trade(trade_payload)
                except Exception as e:
                    logger.error("HUNTERS trade log error: %s", e)

                if (
                    self.hunters_daily_loss_limit_usd > 0
                    and not self.hunters_locked
                    and self.hunters_daily_pnl <= -self.hunters_daily_loss_limit_usd
                ):
                    self.hunters_locked = True
                    logger.warning(
                        "HUNTERS DAILY STOP: pnl=%.2f, limit=%.2f",
                        self.hunters_daily_pnl,
                        self.hunters_daily_loss_limit_usd,
                    )

            self.storage.append_trade_record(
                {
                    "ts": time.time(),
                    "symbol": symbol,
                    "side": "CLOSE",
                    "pnl": pnl,
                    "profile": (target.tags or {}).get("profile"),
                }
            )

            target.state = PositionState.CLOSED
            self._positions = [
                p for p in self._positions if p.state == PositionState.OPEN
            ]
            self.storage.save_positions(self._positions)

            notif = build_close_notification(
                symbol,
                "LONG",
                exit_price,
                float(target.qty),
                pnl,
                self.mode.value,
                "CLOSE",
            )
            append_notification(notif)
            logger.info("HUNTERS CLOSE %s PnL: %.2f", symbol, pnl)
        except Exception as e:
            logger.error("Close Error: %s", e)

    def loop_forever(self) -> None:
        logger.info(
            "=== HOPE V%s ENGINE (HUNTERS Risk Profiles) MODE: %s ===",
            ENGINE_VERSION,
            self.mode.value,
        )
        try:
            while True:
                now = time.time()
                self._loop_counter += 1
                if (now - self._last_heartbeat_ts) >= 30.0:
                    try:
                        open_n = len(
                            [
                                p
                                for p in self._positions
                                if p.state == PositionState.OPEN
                            ]
                        )
                    except Exception:
                        open_n = 0
                    logger.info(
                        "HEARTBEAT loop=%d pending=%d open=%d",
                        self._loop_counter,
                        len(self._pending_signals),
                        open_n,
                    )
                    self._last_heartbeat_ts = now
                self.update_health(now)

                hunter_raw_signals = self._read_new_hunter_signals()
                for raw in hunter_raw_signals:
                    sig_id = (
                        raw.get("signal_id")
                        or f"HUNTERS:{raw.get('symbol')}:{int(float(raw.get('ts') or 0.0))}"
                    )

                    if sig_id in self._seen_ids:
                        continue

                    if len(self._pending_signals) < self.pending_queue_soft_limit:
                        self._pending_signals.append(raw)
                        # DO NOT add to _seen_ids here - only after final decision (TRADE/EXPIRE/BLOCK)
                        # This allows retry if signal expires or fails
                        logger.info(
                            "HUNTERS queued: %s [%s] verdict=%s score=%.3f",
                            raw.get("symbol"),
                            raw.get("profile"),
                            raw.get("hunters_verdict"),
                            float(raw.get("hunters_final_score", 0.0) or 0.0),
                        )
                    else:
                        # Reduce spam: log only every 10th loop iteration
                        if (self._loop_counter % 10) == 0:
                            logger.warning(
                                "HUNTERS OVERLOAD: queue %d/%d, waiting...",
                                len(self._pending_signals),
                                self.pending_queue_soft_limit,
                            )
                        self._pending_overload_alerted = True
                # SAFETY: NEVER truncate hunters_signals_scored.jsonl from the engine.
                # scored is an input/audit stream; rotation/cleanup must be done only by dedicated tools with guards.
                if HUNTERS_SCORED_SIGNALS.exists() and len(hunter_raw_signals) > 0:
                    logger.warning(
                        "SAFETY: refused to truncate hunters_signals_scored.jsonl (engine is read-only for scored)."
                    )

                # ATOMIC QUEUE: Claim batch atomically to prevent signal loss
                try:
                    from tools.atomic_jsonl_queue import (
                        consume_jsonl_queue_batch,
                        finalize_processing_file,
                    )

                    sigs, processing_path = consume_jsonl_queue_batch(
                        SIGNALS_FILE,
                        encoding_read=JSON_ENCODING_READ,
                        archive_dir=None,
                    )

                    if processing_path and sigs:
                        ok = False
                        try:
                            self.process_signals(sigs)
                            ok = True
                        except Exception as e:
                            logger.error("Signal processing error: %s", e)
                        finally:
                            # Finalize only if processing succeeded
                            if ok:
                                finalize_processing_file(
                                    processing_path, archive_dir=None, keep=False
                                )
                            else:
                                # Keep failed batch for inspection/retry
                                failed_dir = STATE_DIR / "failed_batches"
                                finalize_processing_file(
                                    processing_path, archive_dir=failed_dir, keep=False
                                )
                                logger.warning(
                                    "Failed batch archived to: %s",
                                    failed_dir / processing_path.name,
                                )
                except ImportError:
                    # Fallback to old method if atomic queue not available
                    logger.warning(
                        "atomic_jsonl_queue not available, using fallback method"
                    )
                    if SIGNALS_FILE.exists():
                        try:
                            content = SIGNALS_FILE.read_text(
                                encoding=JSON_ENCODING_READ
                            ).strip()
                            lines = content.splitlines()
                            if lines:
                                sigs = []
                                for line in lines:
                                    if line.strip():
                                        try:
                                            sigs.append(json.loads(line))
                                        except Exception:
                                            pass
                                if sigs:
                                    self.process_signals(sigs)
                                SIGNALS_FILE.write_text(
                                    "", encoding=JSON_ENCODING_WRITE
                                )
                        except Exception as e:
                            logger.error("Signals Read: %s", e)
                except Exception as e:
                    logger.error("Atomic queue error: %s", e)

                self._process_pending()
                time.sleep(1.0)
        except KeyboardInterrupt:
            logger.info("Stopping...")


# HOPE: compatibility alias for older code paths


def main() -> None:
    # CRITICAL: Final venv check in main() to catch any subprocess/multiprocessing spawns
    venv_py = Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        a = os.path.normcase(os.path.realpath(sys.executable))
        b = os.path.normcase(os.path.realpath(str(venv_py)))
        if a != b:
            sys.stderr.write(
                "[HOPE] FATAL: main() called from non-venv Python. Exiting.\n"
                f"  sys.executable={sys.executable}\n"
                f"  realpath(sys.executable)={a}\n"
                f"  expected venv={venv_py}\n"
                f"  realpath(venv)={b}\n"
            )
            sys.stderr.flush()
            raise SystemExit(3)

    if not acquire_pid_lock("engine"):
        print("HOPE Engine stopped! Exiting...")
        raise SystemExit(1)

    print(f"[HOPE] ENGINE started pid={os.getpid()}", file=sys.stderr, flush=True)

    # CRITICAL: Start clone killer to prevent Python312 duplicates
    try:
        import minibot.hope_clone_killer
        minibot.hope_clone_killer.start_clone_killer(__file__, interval=10, verbose=False)
    except Exception:
        pass  # Non-critical, continue even if clone killer fails

    # P1: Start heartbeat for health monitoring
    try:
        import minibot.hope_heartbeat
        minibot.hope_heartbeat.start_heartbeat("engine", interval=30, verbose=False)
    except Exception:
        pass  # Non-critical, continue even if heartbeat fails

    try:
        log_dir = ROOT_DIR / "logs"
        log_dir.mkdir(exist_ok=True)

        log_file = log_dir / "run_live_v5.log"

        formatter = logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
        )

        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)

        if root_logger.handlers:
            root_logger.handlers.clear()

        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root_logger.addHandler(console)

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

        parser = argparse.ArgumentParser()
        parser.add_argument("--mode", default="DRY")
        opts = parser.parse_args()

        if Path(r"C:\secrets\hope\.env").exists():
            load_dotenv(r"C:\secrets\hope\.env")
        else:
            load_dotenv(ENV_FILE)

        secrets = {
            "BINANCE_API_KEY": os.getenv("BINANCE_API_KEY"),
            "BINANCE_API_SECRET": os.getenv("BINANCE_API_SECRET"),
        }

        mode = EngineMode(opts.mode)

        risk = RiskManagerV1()
        storage = PositionStorageV5(path_exec_positions=str(EXEC_POS_FILE))
        health = HealthMonitor(str(HEALTH_FILE))
        exch = ExchangeClient(mode, secrets)

        engine = HOPEEngineV5(mode, storage, health, exch, risk, time.time())
        engine.loop_forever()
    finally:
        release_pid_lock("engine")


if __name__ == "__main__":
    main()

# Backward compatibility alias
EngineV5 = HOPEEngineV5


