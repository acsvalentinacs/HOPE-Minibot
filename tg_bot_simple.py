# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 22:00:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-28T16:30:00Z
# v2.3.5: Added /mode command for DRY/LIVE switching with confirmation
# v2.3.6: Added systemd watchdog integration (sd_notify)
# === END SIGNATURE ===
r"""
HOPEminiBOT — tg_bot_simple (v2.1.0 — Valuation Policy)

Changes in v2.1.0:
- Valuation policy: fail-closed sanity checks (_is_price_sane)
- Dust filter: exclude positions < 0.50$ (except stablecoins)
- Ticker cache: 60s in-memory TTL to reduce API load
- Degraded mode: if tickers fail, only count stablecoins
- Excluded assets shown in response (excl: AUD, SLF)
- Formatting: no trailing zeros (100.4$ not 100.40$)

Changes in v2.0.0:
- SSoT key contract: BINANCE_MAINNET_API_KEY/SECRET, BINANCE_TESTNET_API_KEY/SECRET
- Env control: HOPE_BINANCE_ENV=mainnet|testnet|auto, HOPE_BINANCE_ACCOUNT=spot|usdm|coinm|auto
- Fail-closed: explicit env requires explicit keys
- Legacy API_KEY/SECRET treated as TESTNET (never mainnet) for safety

Changes in v1.4.0:
- /balance now fetches REAL Binance SPOT balance via ccxt
- Async wrapper for sync ccxt calls
- Fallback chain: Binance API -> health.json -> env var -> snapshot file

Changes in v1.3.0:
- /stack command with direct launcher v2 integration
- Clean status display (monospace table)
- ASCII-safe critical messages
- PID-lock enforcement (exit 42 on duplicate)

Works from BOTH locations:
- C:\Users\kirillDev\Desktop\TradingBot\tg_bot_simple.py
- C:\Users\kirillDev\Desktop\TradingBot\minibot\tg_bot_simple.py

Key points:
- Reads C:\secrets\hope\.env in READ-ONLY mode if process env is empty.
- Registers bot commands (setMyCommands) so Telegram shows /stop /balance etc.
- /balance: uses health fields; if DRY and equity looks zero -> uses HOPE_DRY_EQUITY_USD as paper equity (best-effort, explicit).
- /restart is NON-BLOCKING (spawn) + debounce to avoid restart spam.
- Robust log decoding to avoid mojibake.

Does NOT modify C:\secrets\hope\.env.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

# ccxt for real Binance balance (optional - graceful fallback if missing)
try:
    import ccxt
    CCXT_AVAILABLE = True
except ImportError:
    CCXT_AVAILABLE = False

# Systemd watchdog integration (fail-open: works without systemd)
try:
    from core.runtime.systemd_notify import sd_ready, sd_watchdog, sd_stopping
    SYSTEMD_AVAILABLE = True
except ImportError:
    SYSTEMD_AVAILABLE = False
    def sd_ready() -> bool: return False
    def sd_watchdog() -> bool: return False
    def sd_stopping() -> bool: return False

THIS_FILE = Path(__file__).resolve()
if THIS_FILE.parent.name.lower() == "minibot":
    ROOT = THIS_FILE.parents[1]
else:
    ROOT = THIS_FILE.parent

# CRITICAL: Apply win_subprocess_fix BEFORE any subprocess calls
# This ensures all child Python processes use venv python, preventing Python312 clones
try:
    import minibot.win_subprocess_fix
    minibot.win_subprocess_fix.apply(verbose=False)
except Exception:
    # Continue even if patch fails - better than crashing
    pass

TOOLS_DIR = ROOT / "tools"
LOGS_DIR = ROOT / "logs"
STATE_DIR = ROOT / "state"
PIDS_DIR = STATE_DIR / "pids"

LOGS_DIR.mkdir(parents=True, exist_ok=True)
STATE_DIR.mkdir(parents=True, exist_ok=True)

# === RATE LIMITING (v2.3.0 - GPT TZ) ===
RATE_LIMIT_MAX_COMMANDS = 10  # max commands per window
RATE_LIMIT_WINDOW_SEC = 60    # window in seconds
_rate_limit_store: dict[int, list[float]] = {}  # user_id -> [timestamps]

BOT_AUDIT_LOG = STATE_DIR / "bot_audit.jsonl"
BOT_ERROR_LOG = STATE_DIR / "bot_errors.jsonl"
PIDS_DIR.mkdir(parents=True, exist_ok=True)

SECRETS_ENV_PATH = Path(r"C:\secrets\hope\.env")
FALLBACK_ENV_PATH = ROOT / ".env"

PS_MORNING = TOOLS_DIR / "hope_morning.ps1"
PS_NIGHT = TOOLS_DIR / "hope_night.ps1"
PS_START_CLEAN = TOOLS_DIR / "start_hope_stack_clean.ps1"
PS_START_NOW = TOOLS_DIR / "start_hope_stack_now.ps1"
PS_LAUNCHER_V2 = TOOLS_DIR / "launch_hope_stack_pidtruth_v2.ps1"

HEALTH_JSON = STATE_DIR / "health_v5.json"
# v2.3.3: TGBOT health must be in minibot/state/ for PowerShell detection
MINIBOT_STATE_DIR = ROOT / "minibot" / "state" if not (ROOT / "core").exists() else ROOT / "state"
HEALTH_TGBOT = MINIBOT_STATE_DIR / "health_tgbot.json"
ENGINE_STDERR = LOGS_DIR / "engine_stderr.log"
STOP_FLAG = ROOT / "STOP.flag"

# === TGBOT HEALTH HEARTBEAT (v2.3.5 - NON-BLOCKING) ===
_TGBOT_START_TIME: float = time.time()
_TGBOT_HEALTH_INTERVAL: int = 10  # seconds


def _write_tgbot_health_sync() -> None:
    """
    SYNC file write - runs in thread pool.
    Atomic: temp -> fsync -> replace.
    """
    try:
        from datetime import datetime, timezone
        import tempfile

        HEALTH_TGBOT.parent.mkdir(parents=True, exist_ok=True)
        health = {
            "component": "TGBOT",
            "version": "2.3.5",
            "hb_ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "uptime_sec": int(time.time() - _TGBOT_START_TIME),
            "pid": os.getpid(),
        }
        content = json.dumps(health, indent=2)

        # Atomic write via temp file with fsync
        fd, tmp_path = tempfile.mkstemp(
            dir=str(HEALTH_TGBOT.parent),
            prefix=".hb_",
            suffix=".tmp"
        )
        try:
            os.write(fd, content.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        os.replace(tmp_path, str(HEALTH_TGBOT))
        # Systemd watchdog keepalive
        sd_watchdog()
    except Exception:
        pass  # Fail silently - watchdog handles stale HB


async def _heartbeat_job_callback(context) -> None:
    """
    Job callback with TIMEOUT and THREAD ISOLATION.
    File I/O runs in thread pool to prevent event loop blocking.
    """
    try:
        await asyncio.wait_for(
            asyncio.to_thread(_write_tgbot_health_sync),
            timeout=8.0
        )
    except asyncio.TimeoutError:
        logging.getLogger("tg_bot").warning("Heartbeat TIMEOUT (>8s)")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logging.getLogger("tg_bot").error("Heartbeat error: %s", e)

HUNTERS_SIGNALS_CANDIDATES = [
    STATE_DIR / "hunters_signals_scored.jsonl",
    STATE_DIR / "hunters_signals.jsonl",
    STATE_DIR / "hunters_signals_scored_v4.jsonl",
]
HUNTERS_TRADES_CANDIDATES = [
    STATE_DIR / "hunters_trades.jsonl",
    STATE_DIR / "hunters_trades_scored.jsonl",
    STATE_DIR / "hunters_trades_v1.jsonl",
]

BALANCE_CANDIDATES = [
    STATE_DIR / "balance_snapshot.json",
    STATE_DIR / "balance.json",
    LOGS_DIR / "balance_snapshot.json",
    LOGS_DIR / "balance.json",
]


def _ps_exe() -> str:
    return "powershell.exe"


def _create_no_window_flag() -> int:
    return 0x08000000


def _looks_like_utf16(data: bytes) -> bool:
    if not data or len(data) < 8:
        return False
    sample = data[:4000]
    zeros = sample.count(b"\x00")
    return zeros / max(1, len(sample)) > 0.15


def _decode_bytes(data: bytes) -> str:
    if not data:
        return ""
    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return data.decode("utf-8-sig", errors="replace")
        except Exception:
            pass
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        try:
            return data.decode("utf-16", errors="replace")
        except Exception:
            pass
    if _looks_like_utf16(data):
        for enc in ("utf-16-le", "utf-16"):
            try:
                return data.decode(enc, errors="replace")
            except Exception:
                pass

    def _try(enc: str) -> str:
        try:
            return data.decode(enc, errors="replace")
        except Exception:
            return ""

    t = _try("utf-8")
    if t:
        bad = t.count("\ufffd")
        if bad == 0:
            return t
        if bad / max(1, len(t)) < 0.01:
            return t

    best = ""
    best_bad = 10**9
    for enc in ("cp1251", "cp866", "latin-1"):
        t2 = _try(enc)
        if not t2:
            continue
        b2 = t2.count("\ufffd")
        if b2 < best_bad:
            best = t2
            best_bad = b2

    return best or t


def _read_text(path: Path, max_bytes: int = 512_000) -> str:
    try:
        data = path.read_bytes()
        if len(data) > max_bytes:
            data = data[-max_bytes:]
        return _decode_bytes(data)
    except Exception:
        return ""


def _tail_lines(path: Path, n: int = 40) -> str:
    txt = _read_text(path)
    if not txt:
        return ""
    lines = txt.splitlines()
    return "\n".join(lines[-n:])


def _health() -> dict:
    try:
        return json.loads(_read_text(HEALTH_JSON) or "{}")
    except Exception:
        return {}


def _mode_from_health(h: dict) -> str:
    m = str(h.get("mode") or "").upper().strip()
    return m if m in ("DRY", "LIVE") else "DRY"


def _bool_flag(path: Path) -> bool:
    try:
        return path.exists()
    except Exception:
        return False


def _fmt_duration(sec: float) -> str:
    try:
        sec_i = int(max(0, float(sec)))
    except Exception:
        return "—"
    if sec_i < 60:
        return f"{sec_i}s"
    if sec_i < 3600:
        return f"{sec_i // 60}m {sec_i % 60}s"
    h = sec_i // 3600
    m = (sec_i % 3600) // 60
    return f"{h}h {m}m"


# === RATE LIMITING & AUDIT (v2.3.0 - GPT TZ) ===

def _check_rate_limit(user_id: int) -> tuple[bool, int]:
    """
    Check if user is within rate limit.
    Returns (allowed, remaining_commands).
    """
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW_SEC

    # Get user's command history
    if user_id not in _rate_limit_store:
        _rate_limit_store[user_id] = []

    # Clean old entries
    _rate_limit_store[user_id] = [ts for ts in _rate_limit_store[user_id] if ts > cutoff]

    # Check limit
    count = len(_rate_limit_store[user_id])
    if count >= RATE_LIMIT_MAX_COMMANDS:
        return False, 0

    # Record this command
    _rate_limit_store[user_id].append(now)
    return True, RATE_LIMIT_MAX_COMMANDS - count - 1


def _audit_log(user_id: int, command: str, success: bool, details: str = "") -> None:
    """Log command to audit file."""
    try:
        record = {
            "ts": time.time(),
            "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "user_id": user_id,
            "command": command,
            "success": success,
            "details": details[:200] if details else "",
        }
        with open(BOT_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # Fail silently - audit should not break commands


def _log_error(error_type: str, message: str, context: dict | None = None) -> None:
    """Log error to error file."""
    try:
        record = {
            "ts": time.time(),
            "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "type": error_type,
            "message": message[:500],
            "context": context or {},
        }
        with open(BOT_ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _get_recent_errors(n: int = 10) -> list[dict]:
    """Get last N errors from error log."""
    try:
        if not BOT_ERROR_LOG.exists():
            return []
        lines = BOT_ERROR_LOG.read_text(encoding="utf-8").strip().split("\n")
        errors = []
        for line in lines[-n:]:
            if line.strip():
                try:
                    errors.append(json.loads(line))
                except Exception:
                    pass
        return errors
    except Exception:
        return []


# === FRIEND CHAT HELPERS (v2.2.0) ===

CHAT_BRIDGE_URL = "http://127.0.0.1:18765"
# Scripts are in minibot/scripts/, not ROOT/scripts/
MINIBOT_DIR = THIS_FILE.parent if THIS_FILE.parent.name.lower() == "minibot" else ROOT / "minibot"
CHAT_SCRIPTS = {
    "tunnel": MINIBOT_DIR / "scripts" / "friend_chat_tunnel.cmd",
    "executor": MINIBOT_DIR / "scripts" / "run_claude_executor.cmd",
    "gpt_agent": MINIBOT_DIR / "scripts" / "run_gpt_agent.cmd",
    "claude_agent": MINIBOT_DIR / "scripts" / "run_claude_agent.cmd",
}


def _chat_health() -> dict:
    """
    Check Friend Chat system health.
    Returns dict with status of tunnel, bridge, and agents.
    """
    import socket
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError

    result = {
        "tunnel_ok": False,
        "bridge_ok": False,
        "bridge_version": None,
        "error": None,
    }

    # Check if tunnel port is listening
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        r = sock.connect_ex(("127.0.0.1", 18765))
        sock.close()
        result["tunnel_ok"] = (r == 0)
    except Exception:
        result["tunnel_ok"] = False

    if not result["tunnel_ok"]:
        result["error"] = "Tunnel not connected"
        return result

    # Check bridge health
    try:
        req = Request(f"{CHAT_BRIDGE_URL}/healthz", method="GET")
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            result["bridge_ok"] = data.get("ok", False)
            result["bridge_version"] = data.get("version", "?")
    except HTTPError as e:
        result["error"] = f"Bridge HTTP {e.code}"
    except URLError as e:
        result["error"] = f"Bridge: {e.reason}"
    except Exception as e:
        result["error"] = f"Bridge: {type(e).__name__}"

    return result


def _chat_status_icon() -> str:
    """Return status icon for chat: 🟢 OK, 🟡 partial, 🔴 down."""
    h = _chat_health()
    if h["bridge_ok"]:
        return "🟢"
    if h["tunnel_ok"]:
        return "🟡"
    return "🔴"


def _parse_allowed_ids(s: str) -> List[int]:
    out: List[int] = []
    if not s:
        return out
    for part in re.split(r"[,\s;]+", s.strip()):
        if not part:
            continue
        try:
            out.append(int(part))
        except Exception:
            continue
    return out


def _load_env_file_readonly(path: Path, overwrite: bool = False) -> int:
    if not path.exists():
        return 0
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return 0

    loaded = 0
    for ln in lines:
        s = (ln or "").strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        if not k:
            continue
        v = v.strip()
        if (len(v) >= 2) and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
            v = v[1:-1]
        if not overwrite and os.environ.get(k):
            continue
        os.environ[k] = v
        loaded += 1
    return loaded


def _ensure_env_loaded(logger: logging.Logger) -> None:
    if SECRETS_ENV_PATH.exists():
        n = _load_env_file_readonly(SECRETS_ENV_PATH, overwrite=False)
        logger.info("ENV loaded %s vars from %s", n, str(SECRETS_ENV_PATH))
    else:
        n = _load_env_file_readonly(FALLBACK_ENV_PATH, overwrite=False)
        logger.info("ENV loaded %s vars from %s", n, str(FALLBACK_ENV_PATH))


def _get_token() -> str:
    return (
        os.getenv("TELEGRAM_TOKEN_MINI") or os.getenv("TELEGRAM_TOKEN") or ""
    ).strip()


def _get_allowed_ids() -> List[int]:
    return _parse_allowed_ids(os.getenv("TELEGRAM_ALLOWED", "").strip())


def _get_pin_flag() -> bool:
    return bool((os.getenv("HOPE_UI_PIN") or "").strip())


def _safe_json_load(path: Path) -> Optional[dict]:
    try:
        txt = _read_text(path)
        if not txt:
            return None
        return json.loads(txt)
    except Exception:
        return None


def _parse_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return float(x)
        s = str(x).strip().replace(",", ".")
        if not s:
            return None
        return float(s)
    except Exception:
        return None


# --- Binance SPOT balance fetch (SSoT key contract v2.0) ---
@dataclass
class BinanceBalanceResult:
    """Result of Binance balance fetch."""
    success: bool
    total_usd: Optional[float] = None
    free_usd: Optional[float] = None
    error: Optional[str] = None
    source: str = "binance_api"
    details: str = ""  # Top assets breakdown


@dataclass(frozen=True)
class _BinanceKeySet:
    """Binance API key pair with environment label."""
    env_name: str  # "mainnet" | "testnet"
    api_key: str
    api_secret: str


def _get_env_str(name: str, default: str = "") -> str:
    """Get env var as stripped string."""
    return (os.getenv(name) or default).strip()


def _load_binance_keys() -> dict[str, _BinanceKeySet]:
    """
    Load Binance API keys with strict SSoT contract.

    Explicit keys (recommended):
      BINANCE_MAINNET_API_KEY / BINANCE_MAINNET_API_SECRET
      BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET

    Legacy keys (deprecated, treated as TESTNET by default for safety):
      BINANCE_API_KEY / BINANCE_API_SECRET
      API_KEY / API_SECRET

    This is fail-closed: legacy keys are NEVER treated as mainnet
    to prevent accidental mainnet operations with testnet intent.
    """
    log = logging.getLogger("tg_bot")

    # Explicit mainnet keys (highest priority)
    main_k = _get_env_str("BINANCE_MAINNET_API_KEY")
    main_s = _get_env_str("BINANCE_MAINNET_API_SECRET")

    # Explicit testnet keys
    test_k = _get_env_str("BINANCE_TESTNET_API_KEY")
    test_s = _get_env_str("BINANCE_TESTNET_API_SECRET")

    # Legacy ambiguous keys (treated as testnet for safety)
    legacy_k = _get_env_str("BINANCE_API_KEY") or _get_env_str("API_KEY")
    legacy_s = _get_env_str("BINANCE_API_SECRET") or _get_env_str("API_SECRET")

    out: dict[str, _BinanceKeySet] = {}

    if main_k and main_s:
        out["mainnet"] = _BinanceKeySet("mainnet", main_k, main_s)
        log.info("[KEYS] Loaded MAINNET keys (explicit)")

    if test_k and test_s:
        out["testnet"] = _BinanceKeySet("testnet", test_k, test_s)
        log.info("[KEYS] Loaded TESTNET keys (explicit)")
    elif legacy_k and legacy_s and "testnet" not in out:
        # Fail-closed: legacy keys are TESTNET by default
        out["testnet"] = _BinanceKeySet("testnet", legacy_k, legacy_s)
        log.info("[KEYS] Loaded TESTNET keys (from legacy API_KEY/SECRET)")

    return out


def _choose_env_attempts() -> tuple[str, ...]:
    """
    Determine which environments to try based on HOPE_BINANCE_ENV.

    Values:
      mainnet - only mainnet (fail if keys missing)
      testnet - only testnet (fail if keys missing)
      auto    - try mainnet first, fallback to testnet (default)
    """
    pref = _get_env_str("HOPE_BINANCE_ENV", "auto").lower()
    if pref in ("mainnet", "testnet"):
        return (pref,)
    return ("mainnet", "testnet")


def _choose_account_attempts() -> tuple[str, ...]:
    """
    Determine which account types to try based on HOPE_BINANCE_ACCOUNT.

    Values:
      spot  - only SPOT account (default)
      usdm  - only USDT-M Futures
      coinm - only COIN-M Futures
      auto  - try all (spot first)
    """
    pref = _get_env_str("HOPE_BINANCE_ACCOUNT", "spot").lower()
    if pref in ("spot", "usdm", "coinm"):
        return (pref,)
    return ("spot", "usdm", "coinm")


# === VALUATION POLICY (SSoT v2.1) ===

# Stablecoins (always 1:1 USD, no ticker needed)
_STABLE_ASSETS = frozenset({"USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI"})

# Manual exclusion list (secondary mechanism, use sparingly)
_EXCLUDED_ASSETS = frozenset({
    "AUD", "SLF", "LEND", "NPXS", "MCO", "VEN", "BCN", "CHAT", "ICN",
    "TRIG", "GVT", "HSR", "RPX", "WINGS", "MOD", "QLC", "WPR", "CLOAK",
    "SUB", "STORM", "POA", "BCD", "CDT", "QSP", "OST", "TNT", "FUEL",
    "LDUSDT", "WBETH",  # wrapped/staked variants
})

# Valuation thresholds (fail-closed: if outside -> exclude)
_MIN_USD_PER_ASSET = 0.10  # dust filter (lowered to include BNB ~0.25$)
_MIN_PRICE_USDT = 1e-12    # sanity: price too low
_MAX_PRICE_USDT = 1e6      # sanity: price too high (impossible for legit assets)

# Ticker cache (in-memory, process-local)
_TICKER_CACHE: dict = {}
_TICKER_CACHE_TS: float = 0.0
_TICKER_CACHE_TTL: float = 60.0  # seconds


# --- Formatting helpers (SSoT) ---

def _fmt_usd(x: float) -> str:
    """
    Format USD value without trailing zeros.
    100.40 -> 100.4
    0.22   -> 0.22
    0.00   -> 0
    """
    s = f"{x:.2f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _fmt_qty(x: float) -> str:
    """
    Format quantity without noise.
    Adaptive precision based on magnitude.
    """
    ax = abs(x)
    if ax >= 100:
        return f"{x:.2f}".rstrip("0").rstrip(".")
    if ax >= 1:
        return f"{x:.4f}".rstrip("0").rstrip(".")
    if ax >= 0.01:
        return f"{x:.6f}".rstrip("0").rstrip(".")
    return f"{x:.8f}".rstrip("0").rstrip(".")


def _is_price_sane(price_usdt: Optional[float]) -> bool:
    """
    Sanity check for price. Fail-closed: if doubtful -> False.
    """
    if price_usdt is None:
        return False
    if price_usdt <= 0:
        return False
    if price_usdt < _MIN_PRICE_USDT or price_usdt > _MAX_PRICE_USDT:
        return False
    return True


def _price_usdt(asset: str, tickers: dict) -> Optional[float]:
    """
    Get USD price for an asset using available tickers.

    Pricing chain (fail-closed: returns None if no reliable price):
      1. USDT = 1.0 (base)
      2. Stablecoins (BUSD, USDC, etc.) = 1.0
      3. Direct: ASSET/USDT ticker
      4. Via BTC: ASSET/BTC * BTC/USDT
      5. Via BUSD: ASSET/BUSD * BUSD/USDT

    Does NOT check _EXCLUDED_ASSETS here (done in caller).
    """
    a = asset.upper()

    # Base stablecoin
    if a in _STABLE_ASSETS:
        return 1.0

    # Direct ASSET/USDT
    for sym in (f"{a}/USDT", f"{a}USDT"):
        t = tickers.get(sym)
        if t and t.get("last"):
            try:
                px = float(t["last"])
                if _is_price_sane(px):
                    return px
            except Exception:
                pass

    # Via BTC: ASSET/BTC * BTC/USDT
    btc_price = None
    for sym in ("BTC/USDT", "BTCUSDT"):
        t = tickers.get(sym)
        if t and t.get("last"):
            try:
                btc_price = float(t["last"])
                break
            except Exception:
                pass

    if btc_price and _is_price_sane(btc_price):
        for sym in (f"{a}/BTC", f"{a}BTC"):
            t = tickers.get(sym)
            if t and t.get("last"):
                try:
                    px = float(t["last"]) * btc_price
                    if _is_price_sane(px):
                        return px
                except Exception:
                    pass

    # Via BUSD (deprecated but still available)
    busd_price = None
    for sym in ("BUSD/USDT", "BUSDUSDT"):
        t = tickers.get(sym)
        if t and t.get("last"):
            try:
                busd_price = float(t["last"])
                break
            except Exception:
                pass

    if busd_price and _is_price_sane(busd_price):
        for sym in (f"{a}/BUSD", f"{a}BUSD"):
            t = tickers.get(sym)
            if t and t.get("last"):
                try:
                    px = float(t["last"]) * busd_price
                    if _is_price_sane(px):
                        return px
                except Exception:
                    pass

    return None


def _iter_nonzero_balances(balances: dict) -> list[tuple[str, float, float]]:
    """Extract non-zero balances as (asset, total, free) tuples."""
    total_dict = balances.get("total") or {}
    free_dict = balances.get("free") or {}
    result = []

    for asset, amt in total_dict.items():
        try:
            t = float(amt or 0.0)
            f = float(free_dict.get(asset) or 0.0)
        except Exception:
            continue
        if abs(t) < 1e-12 and abs(f) < 1e-12:
            continue
        result.append((asset, t, f))

    return result


def _fetch_tickers_cached(exchange) -> tuple[dict, bool]:
    """
    Fetch tickers with 60-second in-memory cache.
    Returns (tickers_dict, is_degraded).
    """
    global _TICKER_CACHE, _TICKER_CACHE_TS

    log = logging.getLogger("tg_bot")
    now = time.time()

    # Check cache validity
    if _TICKER_CACHE and (now - _TICKER_CACHE_TS) < _TICKER_CACHE_TTL:
        log.info("[BALANCE] Using cached tickers (age=%.1fs)", now - _TICKER_CACHE_TS)
        return _TICKER_CACHE, False

    # Fetch fresh tickers
    try:
        tickers = exchange.fetch_tickers()
        _TICKER_CACHE = tickers
        _TICKER_CACHE_TS = now
        log.info("[BALANCE] Fetched %d tickers (fresh)", len(tickers))
        return tickers, False
    except Exception as e:
        log.warning("[BALANCE] Tickers fetch failed: %s", str(e)[:50])
        # Return stale cache if available, else empty (degraded mode)
        if _TICKER_CACHE:
            log.info("[BALANCE] Using stale cache (degraded)")
            return _TICKER_CACHE, True
        return {}, True


def _fetch_binance_balance_sync() -> BinanceBalanceResult:
    """
    Sync Binance balance fetch with SSoT key contract v2.1.

    Key contract (fail-closed):
      - HOPE_BINANCE_ENV=mainnet requires BINANCE_MAINNET_API_KEY/SECRET
      - HOPE_BINANCE_ENV=testnet requires BINANCE_TESTNET_API_KEY/SECRET
      - HOPE_BINANCE_ENV=auto tries mainnet first, fallback to testnet
      - Legacy API_KEY/SECRET are ALWAYS treated as testnet

    Valuation policy (fail-closed):
      - Only includes assets with sane price (_is_price_sane)
      - Excludes dust positions (< _MIN_USD_PER_ASSET)
      - Excludes manually blacklisted assets (_EXCLUDED_ASSETS)
      - Reports excluded assets in details

    Returns BinanceBalanceResult with success/error info and details.
    """
    log = logging.getLogger("tg_bot")
    log.info("[BALANCE] Starting fetch, CCXT_AVAILABLE=%s", CCXT_AVAILABLE)

    if not CCXT_AVAILABLE:
        log.warning("[BALANCE] ccxt not installed")
        return BinanceBalanceResult(
            success=False,
            error="ccxt not installed",
            source="none"
        )

    keys = _load_binance_keys()
    env_attempts = _choose_env_attempts()
    acct_attempts = _choose_account_attempts()

    log.info("[BALANCE] Keys available: %s", list(keys.keys()))
    log.info("[BALANCE] Env attempts: %s, Account attempts: %s", env_attempts, acct_attempts)

    # Fail-closed: explicit mode requires explicit keys
    explicit_env = _get_env_str("HOPE_BINANCE_ENV", "auto").lower()
    if explicit_env in ("mainnet", "testnet") and explicit_env not in keys:
        error_msg = f"{explicit_env} keys missing (set BINANCE_{explicit_env.upper()}_API_KEY/SECRET)"
        log.error("[BALANCE] FAIL-CLOSED: %s", error_msg)
        return BinanceBalanceResult(
            success=False,
            error=error_msg,
            source="binance"
        )

    last_err = "no keys available"

    for acct in acct_attempts:
        for env_name in env_attempts:
            ks = keys.get(env_name)
            if not ks:
                log.info("[BALANCE] Skipping %s/%s: no keys", env_name, acct)
                continue

            sandbox = (env_name == "testnet")
            source = f"binance_{env_name}_{acct}"
            log.info("[BALANCE] Trying %s...", source)

            try:
                # Select exchange class based on account type
                if acct == "spot":
                    exchange = ccxt.binance({
                        "apiKey": ks.api_key,
                        "secret": ks.api_secret,
                        "enableRateLimit": True,
                        "options": {"defaultType": "spot"},
                    })
                elif acct == "usdm":
                    exchange = ccxt.binanceusdm({
                        "apiKey": ks.api_key,
                        "secret": ks.api_secret,
                        "enableRateLimit": True,
                    })
                elif acct == "coinm":
                    exchange = ccxt.binancecoinm({
                        "apiKey": ks.api_key,
                        "secret": ks.api_secret,
                        "enableRateLimit": True,
                    })
                else:
                    continue

                if sandbox and hasattr(exchange, "set_sandbox_mode"):
                    exchange.set_sandbox_mode(True)

                # Fetch balances
                balances = exchange.fetch_balance()

                # Fetch tickers with cache
                tickers, valuation_degraded = _fetch_tickers_cached(exchange)

                # Calculate USD value for all assets
                total_usd = 0.0
                free_usd = 0.0
                ranked = []
                excluded = []

                for asset, t_amt, f_amt in _iter_nonzero_balances(balances):
                    a = asset.upper()

                    # 1. Manual exclusion (secondary mechanism)
                    if a in _EXCLUDED_ASSETS:
                        excluded.append(a)
                        log.debug("[BALANCE] %s excluded (manual)", a)
                        continue

                    # 2. Get price
                    if tickers:
                        px = _price_usdt(a, tickers)
                    else:
                        # Degraded: only stablecoins
                        px = 1.0 if a in _STABLE_ASSETS else None

                    # 3. Sanity check (fail-closed)
                    if not _is_price_sane(px):
                        excluded.append(a)
                        log.debug("[BALANCE] %s excluded (no sane price)", a)
                        continue

                    v_total = float(t_amt) * float(px)
                    v_free = float(f_amt) * float(px)

                    # 4. Dust filter (fail-closed: don't include garbage)
                    if abs(v_total) < _MIN_USD_PER_ASSET and a not in _STABLE_ASSETS:
                        excluded.append(a)
                        log.debug("[BALANCE] %s excluded (dust: %.4f$)", a, v_total)
                        continue

                    total_usd += v_total
                    free_usd += v_free
                    ranked.append((abs(v_total), a, v_total, v_free, float(t_amt)))

                # Sort by USD value descending
                ranked.sort(reverse=True)
                top5 = ranked[:5]

                # Build details string
                def _fmt_asset_line(asset: str, usd_val: float, amount: float) -> str:
                    """Format: USDT=100.4$ or BNB=0.17$(0.0003)"""
                    if asset in _STABLE_ASSETS:
                        return f"{asset}={_fmt_usd(usd_val)}$"
                    return f"{asset}={_fmt_usd(usd_val)}$({_fmt_qty(amount)})"

                if top5:
                    top_str = ", ".join([_fmt_asset_line(a, vt, amt) for _, a, vt, _, amt in top5])
                else:
                    top_str = "no valued assets"

                # Add excluded info
                excluded_unique = sorted(set(excluded))[:5]
                excluded_str = ", ".join(excluded_unique) if excluded_unique else ""

                details = top_str
                if excluded_str:
                    details += f"; excl: {excluded_str}"
                if valuation_degraded:
                    details += " [degraded]"

                log.info("[BALANCE] %s SUCCESS: total=%.2f, free=%.2f, excluded=%s",
                        source, total_usd, free_usd, excluded_unique)

                return BinanceBalanceResult(
                    success=True,
                    total_usd=float(total_usd),
                    free_usd=float(free_usd),
                    source=source,
                    details=details
                )

            except ccxt.AuthenticationError as e:
                last_err = f"AuthError ({env_name}/{acct}): {str(e)[:80]}"
                log.warning("[BALANCE] %s", last_err)
                continue
            except ccxt.NetworkError as e:
                last_err = f"NetworkError ({env_name}/{acct}): {str(e)[:80]}"
                log.warning("[BALANCE] %s", last_err)
                continue
            except Exception as e:
                last_err = f"{type(e).__name__} ({env_name}/{acct}): {str(e)[:80]}"
                log.warning("[BALANCE] %s", last_err)
                continue

    # All attempts failed
    log.error("[BALANCE] All attempts failed: %s", last_err)
    return BinanceBalanceResult(
        success=False,
        error=last_err,
        source="binance"
    )


async def _fetch_binance_balance_async() -> BinanceBalanceResult:
    """Async wrapper for sync Binance balance fetch."""
    return await asyncio.to_thread(_fetch_binance_balance_sync)


# --- PID-truth helpers (read state/pids/*.pid files) ---
def _read_pid_file(role: str) -> Optional[int]:
    """Read PID from state/pids/{role}.pid, return None if missing/invalid."""
    pid_file = PIDS_DIR / f"{role}.pid"
    if not pid_file.exists():
        return None
    try:
        txt = pid_file.read_text(encoding="utf-8", errors="replace").strip()
        if txt.isdigit():
            return int(txt)
    except Exception:
        pass
    return None


def _is_pid_alive(pid: int) -> bool:
    """Check if PID is alive via tasklist (Windows)."""
    if not pid or pid <= 0:
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/fi", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            timeout=2,
            creationflags=_create_no_window_flag(),
        )
        return str(pid) in (result.stdout or "")
    except Exception:
        return False


def _get_role_status(role: str) -> str:
    """Return 'OK pid=X' | 'MISSING' | 'STALE'."""
    pid = _read_pid_file(role)
    if pid is None:
        return "MISSING"
    if _is_pid_alive(pid):
        return f"OK pid={pid}"
    return "STALE"


@dataclass
class ActionSpec:
    key: str
    title: str
    script: Path
    args: List[str]
    timeout_sec: int
    stdout_name: str
    stderr_name: str


class HopeMiniBot:
    VERSION = "tgbot-v2.3.0-gpt-tz"

    def __init__(self) -> None:
        self.log = logging.getLogger("tg_bot")
        self.allowed_ids = _get_allowed_ids()
        self.token = _get_token()
        self._action_lock = asyncio.Lock()
        self._restart_lock = asyncio.Lock()
        self._last_restart_ts = 0.0

    async def _reply(
        self, update: Update, text: str, markup: InlineKeyboardMarkup | None = None,
        parse_mode: str | None = None
    ) -> None:
        if update.message:
            await update.message.reply_text(text, reply_markup=markup, parse_mode=parse_mode)
        elif update.callback_query and update.callback_query.message:
            await update.callback_query.message.reply_text(text, reply_markup=markup, parse_mode=parse_mode)

    async def _guard_admin(self, update: Update) -> bool:
        if not self.allowed_ids:
            return True
        uid = update.effective_user.id if update.effective_user else None
        if uid in self.allowed_ids:
            # Rate limiting check (v2.3.0 - GPT TZ)
            allowed, remaining = _check_rate_limit(uid)
            if not allowed:
                await self._reply(
                    update,
                    f"⚠️ Rate limit: слишком много команд.\n"
                    f"Подождите {RATE_LIMIT_WINDOW_SEC} секунд."
                )
                _log_error("rate_limit", f"User {uid} exceeded rate limit")
                return False
            return True
        await self._reply(update, "⛔ Доступ запрещён.")
        return False

    def _panel_keyboard(self) -> InlineKeyboardMarkup:
        # Chat status indicator
        chat_icon = _chat_status_icon()
        buttons = [
            [
                InlineKeyboardButton("🌅 УТРО", callback_data="hope_morning"),
                InlineKeyboardButton("🌙 НОЧЬ", callback_data="hope_night"),
            ],
            [
                InlineKeyboardButton("🔄 RESTART", callback_data="hope_restart"),
                InlineKeyboardButton("🔃 Обновить", callback_data="hope_refresh"),
            ],
            [
                InlineKeyboardButton("⛔ STOP ON", callback_data="hope_stop_on"),
                InlineKeyboardButton("✅ STOP OFF", callback_data="hope_stop_off"),
            ],
            [
                InlineKeyboardButton("💰 Balance", callback_data="hope_balance"),
                InlineKeyboardButton("🔬 Diag", callback_data="hope_diag"),
            ],
            [
                InlineKeyboardButton("📡 Signals", callback_data="hope_signals"),
                InlineKeyboardButton("🧾 Trades", callback_data="hope_trades"),
            ],
            [
                InlineKeyboardButton("🧱 Stack", callback_data="hope_stack"),
                InlineKeyboardButton(f"{chat_icon} Чат", callback_data="hope_chat"),
            ],
            [
                InlineKeyboardButton("ℹ️ Help", callback_data="hope_help"),
            ],
        ]
        return InlineKeyboardMarkup(buttons)

    def _panel_text(self) -> str:
        h = _health()
        mode = _mode_from_health(h)
        engine_ok = bool(h.get("engine_ok", False))
        uptime_s = float(h.get("uptime_sec", 0.0) or 0.0)
        hb_ago = float(h.get("heartbeat_ago_sec", 0.0) or 0.0)
        q = h.get("pending_queue_len", h.get("queue_len", 0))
        try:
            q = int(q)
        except Exception:
            q = 0
        stop = "ON" if _bool_flag(STOP_FLAG) else "OFF"
        pin = "ON" if _get_pin_flag() else "OFF"
        return (
            "📊 Панель HOPE v5\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"Engine: {'✅ OK' if engine_ok else '❌'}\n"
            f"Режим: {mode}\n"
            f"Аптайм: {_fmt_duration(uptime_s)}\n"
            f"HB ago: {_fmt_duration(hb_ago)}\n"
            f"Очередь: {q}\n"
            f"STOP.flag: ▶ {stop}\n"
            f"PIN(LIVE): {pin}"
        )

    async def cmd_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._guard_admin(update):
            return
        await self._reply(update, self._panel_text(), self._panel_keyboard())

    async def cmd_panel(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._guard_admin(update):
            return
        await self._reply(update, self._panel_text(), self._panel_keyboard())

    async def cmd_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._guard_admin(update):
            return
        h = _health()
        mode = _mode_from_health(h)
        engine_ok = bool(h.get("engine_ok", False))
        uptime_s = float(h.get("uptime_sec", 0.0) or 0.0)
        stop = "ON" if _bool_flag(STOP_FLAG) else "OFF"
        txt = (
            "📊 HOPE v5 — статус\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"Engine: {'✅' if engine_ok else '❌'} | {mode} | {_fmt_duration(uptime_s)}\n"
            f"STOP.flag: ▶ {stop}"
        )
        await self._reply(update, txt)

    async def cmd_help(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._guard_admin(update):
            return
        txt = (
            "🛠 Команды HOPEminiBOT v2.3.5:\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "/start — меню\n"
            "/panel — панель + кнопки\n"
            "/status — краткий статус\n"
            "/mode — ⚙️ режим DRY/LIVE\n"
            "/health — 🏥 статус всех систем\n"
            "/logs — 📋 последние ошибки\n"
            "/balance — баланс\n"
            "/stop — статус STOP.flag\n"
            "/stop_on — включить STOP.flag ⚠️\n"
            "/stop_off — выключить STOP.flag\n"
            "/morning — 🌅 УТРО\n"
            "/night — 🌙 НОЧЬ\n"
            "/restart — 🔄 RESTART\n"
            "/stack — 🧱 управление stack\n"
            "/chat — 💬 чат друзей\n"
            "/signals — последние сигналы\n"
            "/trades — последние сделки\n"
            "/diag — диагностика\n"
            "/whoami — твой ID\n"
            "/version — версия\n"
            "/help — помощь"
        )
        await self._reply(update, txt)

    async def cmd_whoami(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        uid = update.effective_user.id if update.effective_user else None
        await self._reply(update, f"🪪 Your ID: {uid}")

    async def cmd_version(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._reply(update, f"🤖 HOPEminiBOT {self.VERSION}")

    # === NEW COMMANDS v2.3.0 (GPT TZ) ===

    async def cmd_health(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Quick system health overview - GPT TZ requirement."""
        if not await self._guard_admin(update):
            return

        uid = update.effective_user.id if update.effective_user else 0
        _audit_log(uid, "health", True)

        # Gather all health info
        h = _health()
        engine_ok = bool(h.get("engine_ok", False))
        mode = _mode_from_health(h)
        uptime = _fmt_duration(float(h.get("uptime_sec", 0) or 0))

        # IPC health (check local folders)
        ipc_ok = False
        ipc_msg = "?"
        try:
            ipc_dir = ROOT / "ipc"
            if ipc_dir.exists():
                claude_inbox = len(list((ipc_dir / "claude_inbox").glob("*.json"))) if (ipc_dir / "claude_inbox").exists() else 0
                gpt_inbox = len(list((ipc_dir / "gpt_inbox").glob("*.json"))) if (ipc_dir / "gpt_inbox").exists() else 0
                deadletter = len(list((ipc_dir / "deadletter").glob("*.json"))) if (ipc_dir / "deadletter").exists() else 0
                ipc_ok = deadletter == 0
                ipc_msg = f"claude={claude_inbox} gpt={gpt_inbox} dead={deadletter}"
            else:
                ipc_msg = "no ipc folder"
        except Exception as e:
            ipc_msg = str(e)[:30]

        # Friend Chat health
        chat_h = _chat_health()
        tunnel_ok = chat_h.get("tunnel_ok", False)
        bridge_ok = chat_h.get("bridge_ok", False)
        bridge_ver = chat_h.get("bridge_version", "?")

        # Bot process
        bot_ok = True  # If we're responding, we're alive

        # Telegram bot
        tg_ok = True

        # Build response
        lines = [
            "🏥 HOPE System Health",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"{'✅' if engine_ok else '❌'} Engine: {mode} ({uptime})",
            f"{'✅' if ipc_ok else '⚠️'} IPC: {ipc_msg}",
            f"{'✅' if tunnel_ok else '❌'} Tunnel: {'OK' if tunnel_ok else 'DOWN'}",
            f"{'✅' if bridge_ok else '❌'} Bridge: {bridge_ver if bridge_ok else 'DOWN'}",
            f"{'✅' if bot_ok else '❌'} Bot: OK",
            f"{'✅' if tg_ok else '❌'} Telegram: OK",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]

        all_ok = engine_ok and ipc_ok and tunnel_ok and bridge_ok
        lines.append(f"Overall: {'✅ ALL OK' if all_ok else '⚠️ ISSUES DETECTED'}")

        await self._reply(update, "\n".join(lines))

    async def cmd_logs(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show recent errors - GPT TZ requirement."""
        if not await self._guard_admin(update):
            return

        uid = update.effective_user.id if update.effective_user else 0
        _audit_log(uid, "logs", True)

        errors = _get_recent_errors(10)

        if not errors:
            await self._reply(update, "📋 Нет ошибок в логе.")
            return

        lines = ["📋 Последние ошибки:", "━━━━━━━━━━━━━━━━━━"]
        for err in errors[-5:]:  # Show last 5
            ts = err.get("ts_iso", "?")[:16]
            etype = err.get("type", "?")
            msg = err.get("message", "?")[:60]
            lines.append(f"• {ts}")
            lines.append(f"  [{etype}] {msg}")

        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append(f"Всего: {len(errors)} ошибок")

        await self._reply(update, "\n".join(lines))

    async def cmd_stop(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._guard_admin(update):
            return
        on = _bool_flag(STOP_FLAG)
        await self._reply(update, f"⛔ STOP.flag: {'ON' if on else 'OFF'}")

    async def cmd_stop_on(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Stop trading - requires confirmation (GPT TZ: confirmations for dangerous actions)."""
        if not await self._guard_admin(update):
            return
        # Show confirmation dialog instead of immediate action
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Да, остановить", callback_data="confirm_stop_on"),
                InlineKeyboardButton("❌ Отмена", callback_data="cancel_action"),
            ]
        ])
        await self._reply(
            update,
            "⚠️ ВНИМАНИЕ: Вы уверены что хотите ОСТАНОВИТЬ торговлю?\n"
            "Это действие включит STOP.flag.",
            keyboard
        )

    async def _do_stop_on(self, update: Update) -> None:
        """Actually execute stop_on after confirmation."""
        try:
            STOP_FLAG.write_text(
                f"STOP set by telegram at {time.strftime('%Y-%m-%d %H:%M:%S')}\n",
                encoding="utf-8",
            )
            uid = update.effective_user.id if update.effective_user else 0
            _audit_log(uid, "stop_on", True, "confirmed")
            await self._reply(update, "⛔ STOP.flag включён (ON).")
        except Exception as e:
            _log_error("stop_on_failed", str(e))
            await self._reply(
                update, f"❌ Не удалось включить STOP.flag: {type(e).__name__}: {e}"
            )

    async def cmd_stop_off(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._guard_admin(update):
            return
        try:
            if STOP_FLAG.exists():
                STOP_FLAG.unlink()
            await self._reply(update, "✅ STOP.flag выключен (OFF).")
        except Exception as e:
            await self._reply(
                update, f"❌ Не удалось выключить STOP.flag: {type(e).__name__}: {e}"
            )

    async def cmd_mode(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """View or change trading mode: /mode [DRY|LIVE]"""
        if not await self._guard_admin(update):
            return

        args = context.args if context.args else []
        h = _health()
        current_mode = _mode_from_health(h)

        # No args - just show current mode
        if not args:
            await self._reply(
                update,
                f"⚙️ Текущий режим: **{current_mode}**\n\n"
                f"Доступные режимы:\n"
                f"• `DRY` — симуляция (без реальных ордеров)\n"
                f"• `LIVE` — реальная торговля\n\n"
                f"Для смены: `/mode DRY` или `/mode LIVE`",
                parse_mode="Markdown",
            )
            return

        new_mode = args[0].upper().strip()
        if new_mode not in ("DRY", "LIVE", "MAINNET", "TESTNET"):
            await self._reply(update, f"❌ Неизвестный режим: {new_mode}\nДопустимо: DRY, LIVE")
            return

        # Normalize MAINNET -> LIVE, TESTNET -> DRY
        if new_mode == "MAINNET":
            new_mode = "LIVE"
        elif new_mode == "TESTNET":
            new_mode = "DRY"

        # Same mode - no change
        if new_mode == current_mode:
            await self._reply(update, f"ℹ️ Режим уже установлен: {current_mode}")
            return

        # Switching to LIVE requires confirmation
        if new_mode == "LIVE":
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Да, включить LIVE", callback_data="confirm_mode_live"),
                    InlineKeyboardButton("❌ Отмена", callback_data="cancel_action"),
                ]
            ])
            await self._reply(
                update,
                "⚠️ **ВНИМАНИЕ: LIVE РЕЖИМ**\n\n"
                "Бот будет совершать РЕАЛЬНЫЕ сделки!\n"
                "Risk Governor: max $15/позиция, SL -3%\n\n"
                "Вы уверены?",
                keyboard,
                parse_mode="Markdown",
            )
            return

        # Switching to DRY - safe, do immediately
        await self._do_mode_change(update, new_mode)

    async def _do_mode_change(self, update: Update, new_mode: str) -> None:
        """Execute mode change."""
        try:
            # Update health_v5.json
            h = _health()
            h["mode"] = new_mode
            HEALTH_JSON.write_text(
                __import__("json").dumps(h, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            # Also write a mode flag file for other components
            mode_flag = STATE_DIR / "mode.flag"
            mode_flag.write_text(new_mode, encoding="utf-8")

            uid = update.effective_user.id if update.effective_user else 0
            _audit_log(uid, "mode_change", True, f"changed to {new_mode}")

            emoji = "🟢" if new_mode == "LIVE" else "⚪"
            await self._reply(
                update,
                f"{emoji} Режим изменён: **{new_mode}**\n\n"
                + (
                    "⚠️ LIVE: реальные сделки активны!\n"
                    "Risk Governor: max $15, SL -3%"
                    if new_mode == "LIVE"
                    else "✅ DRY: симуляция, без реальных ордеров"
                ),
                parse_mode="Markdown",
            )

        except Exception as e:
            self.log.exception("Mode change failed")
            await self._reply(update, f"❌ Ошибка смены режима: {e}")

    def _paper_equity_usd(self) -> Optional[float]:
        v = os.getenv("HOPE_DRY_EQUITY_USD") or os.getenv("HOPE_PAPER_EQUITY_USD") or ""
        f = _parse_float(v)
        if f is None:
            return None
        if f <= 0:
            return None
        return f

    async def cmd_balance(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._guard_admin(update):
            return

        h = _health()
        mode = _mode_from_health(h)

        # Priority 1: ALWAYS try Binance API first (SSoT key contract)
        result = await _fetch_binance_balance_async()
        if result.success and result.total_usd is not None:
            # Format response with full details
            details_line = f"\n  Assets: {result.details}" if result.details else ""
            await self._reply(
                update,
                f"💰 Balance (Binance):\n"
                f"  Total: {result.total_usd:.2f} USD\n"
                f"  Free:  {result.free_usd:.2f} USD{details_line}\n"
                f"Source: {result.source}\n"
                f"Mode={mode}",
            )
            return

        # Binance failed, log and continue to fallbacks
        self.log.warning("Binance balance fetch failed: %s", result.error)
        binance_error = result.error

        # Priority 2: Health.json fields (engine reports this) - but skip if zero
        eq = _parse_float(h.get("equity_usd"))
        bal_f = _parse_float(h.get("balance_usd"))

        # Only use health if we have meaningful non-zero values
        if (eq is not None and eq > 0) or (bal_f is not None and bal_f > 0):
            parts = []
            if eq is not None:
                parts.append(f"equity_usd={eq:.2f}")
            if bal_f is not None:
                parts.append(f"balance_usd={bal_f:.2f}")
            await self._reply(
                update,
                "💰 Balance (from health): " + " | ".join(parts) + f"\nMode={mode}",
            )
            return

        # Priority 3: DRY mode paper equity from env var
        paper = self._paper_equity_usd()
        if paper is not None and paper > 0:
            await self._reply(
                update,
                f"💰 Balance (DRY paper): equity_usd={paper:.2f}\n"
                f"Источник: HOPE_DRY_EQUITY_USD\n"
                f"Mode={mode}",
            )
            return

        # Priority 4: Snapshot files
        for p in BALANCE_CANDIDATES:
            if p.exists():
                j = _safe_json_load(p)
                if isinstance(j, dict):
                    compact = []
                    for cand in ("free_usd", "total_usd", "equity_usd", "balance_usd"):
                        if cand in j:
                            compact.append(f"{cand}={j.get(cand)}")
                    if compact:
                        await self._reply(
                            update,
                            f"💰 Balance (snapshot): "
                            + " | ".join(compact)
                            + f"\nFile={p.name}\nMode={mode}",
                        )
                        return

        # All sources failed - show Binance error
        await self._reply(
            update,
            f"💰 /balance: Binance API недоступен.\n"
            f"Error: {binance_error}\n"
            f"Mode={mode}",
        )

    async def cmd_stack(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show HOPE stack status + control buttons (START/STOP/FIXDUP)."""
        if not await self._guard_admin(update):
            return

        # Build PID-truth status table
        engine_st = _get_role_status("ENGINE")
        tgbot_st = _get_role_status("TGBOT")
        listener_st = _get_role_status("LISTENER")

        # Detect problems
        problems = []
        if "STALE" in engine_st:
            problems.append("ENGINE stale (pid file exists but process dead)")
        if "STALE" in tgbot_st:
            problems.append("TGBOT stale")
        if "STALE" in listener_st:
            problems.append("LISTENER stale")

        # Build table (monospace)
        table = (
            "```\n"
            "ROLE      STATE\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"ENGINE    {engine_st}\n"
            f"TGBOT     {tgbot_st}\n"
            f"LISTENER  {listener_st}\n"
            "```"
        )

        problem_text = ""
        if problems:
            problem_text = "\n⚠️ **Problems:**\n" + "\n".join(f"• {p}" for p in problems)

        # Control buttons
        kb = [
            [InlineKeyboardButton("▶ START", callback_data="stack_start")],
            [InlineKeyboardButton("⏹ STOP", callback_data="stack_stop")],
            [InlineKeyboardButton("🧹 FIXDUP", callback_data="stack_fixdup")],
            [InlineKeyboardButton("🔃 Refresh", callback_data="stack_refresh")],
        ]

        text = (
            "🧱 **HOPE Stack Status**\n\n"
            f"{table}"
            f"{problem_text}\n\n"
            "_Use buttons below to control stack._"
        )

        await self._reply(update, text, InlineKeyboardMarkup(kb))

    async def _run_action_and_report(self, update: Update, spec: ActionSpec) -> None:
        if not spec.script.exists():
            await self._reply(update, f"❌ Не найден скрипт: {spec.script}")
            return

        out_file = LOGS_DIR / spec.stdout_name
        err_file = LOGS_DIR / spec.stderr_name
        cmd = [
            _ps_exe(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(spec.script),
        ] + list(spec.args)

        out_f = None
        err_f = None
        try:
            out_f = open(out_file, "ab", buffering=0)
            err_f = open(err_file, "ab", buffering=0)
            proc = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                stdout=out_f,
                stderr=err_f,
                creationflags=_create_no_window_flag(),
            )
        except Exception as e:
            await self._reply(update, f"❌ spawn error: {type(e).__name__}: {e}")
            try:
                if out_f:
                    out_f.close()
                if err_f:
                    err_f.close()
            except Exception:
                pass
            return

        t0 = time.time()
        try:
            await asyncio.wait_for(
                asyncio.to_thread(proc.wait), timeout=spec.timeout_sec
            )
        except asyncio.TimeoutError:
            await self._reply(
                update,
                f"❌ {spec.title} timeout ({spec.timeout_sec}s). Проверь logs\\{spec.stderr_name}",
            )
            return
        finally:
            try:
                if out_f:
                    out_f.close()
                if err_f:
                    err_f.close()
            except Exception:
                pass

        dt = time.time() - t0
        tail = _tail_lines(out_file, 80) or "(no stdout)"
        await self._reply(
            update,
            f"🧾 ACTION REPORT: {spec.key}\nRESULT: returncode={proc.returncode}\n⏱ {dt:.0f}s\n\n📜 STDOUT (tail)\n{tail}",
        )

    # === FRIEND CHAT MENU (v2.2.0) ===

    def _chat_keyboard(self) -> InlineKeyboardMarkup:
        """Keyboard for Friend Chat submenu."""
        h = _chat_health()
        tunnel_icon = "🟢" if h["tunnel_ok"] else "🔴"
        bridge_icon = "🟢" if h["bridge_ok"] else "🔴"

        buttons = [
            [
                InlineKeyboardButton(f"{tunnel_icon} Туннель", callback_data="chat_tunnel_status"),
                InlineKeyboardButton(f"{bridge_icon} Bridge", callback_data="chat_bridge_status"),
            ],
            [
                InlineKeyboardButton("🔄 Executor", callback_data="chat_restart_executor"),
                InlineKeyboardButton("🔄 All Agents", callback_data="chat_restart_all"),
            ],
            [
                InlineKeyboardButton("🔌 Открыть туннель", callback_data="chat_start_tunnel"),
            ],
            [
                InlineKeyboardButton("📊 Полный статус", callback_data="chat_full_status"),
            ],
            [
                InlineKeyboardButton("◀️ Назад", callback_data="hope_refresh"),
            ],
        ]
        return InlineKeyboardMarkup(buttons)

    def _chat_status_text(self) -> str:
        """Generate chat status text."""
        h = _chat_health()

        tunnel_st = "🟢 Подключен" if h["tunnel_ok"] else "🔴 Отключен"
        bridge_st = "🟢 OK" if h["bridge_ok"] else "🔴 Недоступен"
        version = h.get("bridge_version") or "?"
        error = h.get("error") or "—"

        return (
            "💬 **Чат друзей**\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"SSH Туннель: {tunnel_st}\n"
            f"VPS Bridge: {bridge_st} (v{version})\n"
            f"Ошибка: {error}\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "_Управление сервисами чата_"
        )

    async def cmd_chat(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show Friend Chat control panel."""
        if not await self._guard_admin(update):
            return
        await self._reply(update, self._chat_status_text(), self._chat_keyboard())

    def _kill_agent_processes(self) -> int:
        """Kill all running agent processes. Returns count of killed processes."""
        killed = 0
        patterns = [
            "claude_executor_runner",
            "gpt_orchestrator_runner",
            "ipc_agent",
        ]
        try:
            result = subprocess.run(
                ["wmic", "process", "get", "processid,commandline"],
                capture_output=True, text=True, encoding="utf-8", errors="ignore",
                timeout=10,
            )
            for line in result.stdout.split("\n"):
                line_lower = line.lower()
                if any(p in line_lower for p in patterns):
                    parts = line.strip().split()
                    if parts and parts[-1].isdigit():
                        pid = parts[-1]
                        subprocess.run(
                            ["taskkill", "/F", "/PID", pid],
                            capture_output=True, timeout=5,
                        )
                        killed += 1
        except Exception:
            pass
        return killed

    async def _chat_restart_service(self, update: Update, service: str) -> None:
        """Restart a chat service by launching its CMD script.

        SMART: Executor only starts if tunnel is connected.
        """
        script_map = {
            "executor": CHAT_SCRIPTS.get("executor"),
            "tunnel": CHAT_SCRIPTS.get("tunnel"),
            "gpt_agent": CHAT_SCRIPTS.get("gpt_agent"),
            "claude_agent": CHAT_SCRIPTS.get("claude_agent"),
        }

        script = script_map.get(service)
        if not script or not script.exists():
            await self._reply(update, f"❌ Скрипт не найден: {service}")
            return

        # BLOCK: Don't start executor without tunnel
        if service == "executor":
            h = _chat_health()
            if not h.get("tunnel_ok", False):
                await self._reply(
                    update,
                    "⛔ **Executor заблокирован**\n\n"
                    "SSH туннель не подключен.\n"
                    "Без туннеля executor будет спамить ошибками.\n\n"
                    "💡 Сначала откройте туннель кнопкой **🔌 Открыть туннель**",
                    self._chat_keyboard(),
                )
                return

        try:
            # Kill old agent processes first
            killed = self._kill_agent_processes()
            if killed > 0:
                await self._reply(update, f"🔄 Остановлено {killed} старых процессов...")
                await asyncio.sleep(2)  # Wait for processes to die

            # Start in new window (visible to user)
            subprocess.Popen(
                ["cmd.exe", "/c", "start", str(script.name)],
                cwd=str(script.parent),
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            await self._reply(
                update,
                f"✅ Запущен: {service}\n"
                f"Скрипт: {script.name}\n"
                "_Окно откроется на рабочем столе_",
            )
        except Exception as e:
            await self._reply(update, f"❌ Ошибка запуска {service}: {e}")

    async def _chat_start_tunnel(self, update: Update) -> None:
        """Start SSH tunnel via Task Scheduler."""
        try:
            proc = subprocess.run(
                ["schtasks", "/run", "/tn", "HOPE\\FriendChatTunnel"],
                capture_output=True,
                timeout=10,
            )
            if proc.returncode == 0:
                await self._reply(
                    update,
                    "✅ Туннель запущен через Task Scheduler\n"
                    "_Подождите 5 сек для подключения_",
                )
            else:
                stderr = proc.stderr.decode("utf-8", errors="replace")[:200]
                await self._reply(update, f"❌ Ошибка запуска туннеля:\n{stderr}")
        except Exception as e:
            await self._reply(update, f"❌ Ошибка: {e}")

    def _kill_agent_windows(self) -> int:
        """Kill all agent CMD windows by title. Returns count of killed windows."""
        killed = 0
        # Window titles used by agent scripts
        window_titles = [
            "Claude Executor",
            "GPT Agent",
            "Claude Agent",
            "IPC Agent",
            "Friend Chat",
        ]
        try:
            # Get all cmd.exe processes with window titles
            result = subprocess.run(
                ["tasklist", "/v", "/fi", "imagename eq cmd.exe"],
                capture_output=True, text=True, encoding="utf-8", errors="ignore",
                timeout=10,
            )
            for line in result.stdout.split("\n"):
                line_lower = line.lower()
                for title in window_titles:
                    if title.lower() in line_lower:
                        # Extract PID (second column)
                        parts = line.split()
                        if len(parts) >= 2 and parts[1].isdigit():
                            pid = parts[1]
                            subprocess.run(
                                ["taskkill", "/F", "/PID", pid],
                                capture_output=True, timeout=5,
                            )
                            killed += 1
                            break
        except Exception:
            pass
        return killed

    async def _chat_restart_all_agents(self, update: Update) -> None:
        """Kill all old agent processes/windows and start fresh.

        SMART: Only starts executor if tunnel is connected.
        IPC agents (gpt_agent, claude_agent) always start - they work locally.
        """
        if not await self._guard_admin(update):
            return

        await self._reply(update, "🔄 Закрываю старые окна агентов...")

        # 1. Kill Python agent processes
        killed_procs = self._kill_agent_processes()

        # 2. Kill CMD windows with agent titles
        killed_wins = self._kill_agent_windows()

        total_killed = killed_procs + killed_wins
        if total_killed > 0:
            await self._reply(
                update,
                f"🧹 Закрыто: {killed_procs} процессов, {killed_wins} окон\n"
                "⏳ Жду 3 сек..."
            )
            await asyncio.sleep(3)
        else:
            await self._reply(update, "ℹ️ Старых процессов не найдено")
            await asyncio.sleep(1)

        # 3. Check tunnel status BEFORE starting executor
        h = _chat_health()
        tunnel_ok = h.get("tunnel_ok", False)

        # 4. Start agents (executor ONLY if tunnel is connected)
        started = []
        skipped = []
        failed = []

        # IPC agents - always start (work locally)
        for name in ["gpt_agent", "claude_agent"]:
            script = CHAT_SCRIPTS.get(name)
            if script and script.exists():
                try:
                    subprocess.Popen(
                        ["cmd.exe", "/c", "start", str(script.name)],
                        cwd=str(script.parent),
                        creationflags=subprocess.CREATE_NEW_CONSOLE,
                    )
                    started.append(name)
                    await asyncio.sleep(1)
                except Exception as e:
                    failed.append(f"{name}: {e}")
            else:
                failed.append(f"{name}: скрипт не найден")

        # Executor - ONLY if tunnel is connected
        if tunnel_ok:
            script = CHAT_SCRIPTS.get("executor")
            if script and script.exists():
                try:
                    subprocess.Popen(
                        ["cmd.exe", "/c", "start", str(script.name)],
                        cwd=str(script.parent),
                        creationflags=subprocess.CREATE_NEW_CONSOLE,
                    )
                    started.append("executor")
                except Exception as e:
                    failed.append(f"executor: {e}")
            else:
                failed.append("executor: скрипт не найден")
        else:
            skipped.append("executor (нет туннеля)")

        # 5. Report result
        msg = "🚀 **Перезапуск агентов**\n━━━━━━━━━━━━━━━━━━\n"
        if started:
            msg += f"✅ Запущено: {', '.join(started)}\n"
        if skipped:
            msg += f"⏭️ Пропущено: {', '.join(skipped)}\n"
        if failed:
            msg += f"❌ Ошибки: {'; '.join(failed)}\n"

        if not tunnel_ok:
            msg += "\n💡 _Для executor откройте туннель кнопкой ниже_"

        msg += "\n━━━━━━━━━━━━━━━━━━"

        await self._reply(update, msg, self._chat_keyboard())

    async def cmd_morning(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._guard_admin(update):
            return
        async with self._action_lock:
            await self._reply(
                update, "🌅 УТРО — запуск стека\n⏳ Запускаю... (timeout 180s)"
            )
            spec = ActionSpec(
                "morning",
                "MORNING",
                PS_MORNING,
                [],
                180,
                "morning_stdout.log",
                "morning_stderr.log",
            )
            await self._run_action_and_report(update, spec)

    async def cmd_night(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._guard_admin(update):
            return
        async with self._action_lock:
            await self._reply(
                update, "🌙 НОЧЬ — stop + report\n⏳ Запускаю... (timeout 180s)"
            )
            spec = ActionSpec(
                "night",
                "NIGHT",
                PS_NIGHT,
                [],
                180,
                "night_stdout.log",
                "night_stderr.log",
            )
            await self._run_action_and_report(update, spec)

    async def cmd_restart(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._guard_admin(update):
            return

        now = time.time()
        if (now - self._last_restart_ts) < 45:
            await self._reply(
                update,
                f"🟠 /restart уже запускался {int(now - self._last_restart_ts)}s назад. Подожди 45s.",
            )
            return

        async with self._restart_lock:
            self._last_restart_ts = time.time()
            h = _health()
            mode = _mode_from_health(h)

            script = PS_START_CLEAN if PS_START_CLEAN.exists() else PS_START_NOW
            if not script.exists():
                await self._reply(update, f"❌ Не найден скрипт рестарта: {script}")
                return

            out_file = LOGS_DIR / "restart_spawn_stdout.log"
            err_file = LOGS_DIR / "restart_spawn_stderr.log"
            cmd = [
                _ps_exe(),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Force",
                "-Mode",
                mode,
                "-NoTgBot",
                "-StartTimeoutSec",
                "30",
            ]

            out_f = None
            err_f = None
            try:
                out_f = open(out_file, "ab", buffering=0)
                err_f = open(err_file, "ab", buffering=0)
                subprocess.Popen(
                    cmd,
                    cwd=str(ROOT),
                    stdout=out_f,
                    stderr=err_f,
                    creationflags=_create_no_window_flag(),
                )
            except Exception as e:
                await self._reply(
                    update, f"❌ restart spawn error: {type(e).__name__}: {e}"
                )
                try:
                    if out_f:
                        out_f.close()
                    if err_f:
                        err_f.close()
                except Exception:
                    pass
                return
            finally:
                try:
                    if out_f:
                        out_f.close()
                    if err_f:
                        err_f.close()
                except Exception:
                    pass

            await self._reply(
                update,
                f"🔄 Restart Stack: queued (бот не блокирую)\nMode={mode}\nЛоги: logs\\restart_spawn_*.log",
            )

    async def cmd_diag(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._guard_admin(update):
            return
        h = _health()
        hb_ago = float(h.get("heartbeat_ago_sec", 0.0) or 0.0)
        engine_ok = bool(h.get("engine_ok", False))
        last_err = str(h.get("last_error", "—"))
        tail = _tail_lines(ENGINE_STDERR, 20)
        txt = (
            "🔬 Diag HOPE\n"
            f"health_v5.json: {'OK' if HEALTH_JSON.exists() else 'MISSING'}\n"
            f"engine_ok: {engine_ok}\n"
            f"last_error: {last_err}\n"
            f"heartbeat_ago: {_fmt_duration(hb_ago)}\n"
        )
        if tail:
            txt += "\nengine_stderr (tail):\n" + tail
        await self._reply(update, txt)

    def _pick_existing(self, candidates: List[Path]) -> Optional[Path]:
        for p in candidates:
            if p.exists():
                return p
        return None

    async def cmd_signals(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._guard_admin(update):
            return
        p = self._pick_existing(HUNTERS_SIGNALS_CANDIDATES)
        if not p:
            await self._reply(update, "❌ signals file not found in state/")
            return
        lines = _read_text(p).splitlines()
        tail = lines[-5:] if len(lines) >= 5 else lines
        out: List[str] = []
        for ln in tail:
            try:
                j = json.loads(ln)
                ts = j.get("ts") or j.get("time") or j.get("timestamp") or "—"
                sym = j.get("symbol") or j.get("coin") or "—"
                verdict = j.get("verdict") or j.get("status") or "—"
                score = j.get("final_score") or j.get("score") or "—"
                out.append(f"• {ts} | {sym} | {verdict} ({score})")
            except Exception:
                out.append(f"• {ln[:120]}")
        await self._reply(
            update, "📡 LAST SIGNALS:\n" + ("\n".join(out) if out else "—")
        )

    async def cmd_trades(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._guard_admin(update):
            return
        p = self._pick_existing(HUNTERS_TRADES_CANDIDATES)
        if not p:
            await self._reply(update, "❌ trades file not found in state/")
            return
        lines = _read_text(p).splitlines()
        tail = lines[-5:] if len(lines) >= 5 else lines
        out: List[str] = []
        for ln in tail:
            try:
                j = json.loads(ln)
                sym = j.get("symbol") or j.get("coin") or "—"
                side = j.get("side") or j.get("dir") or "—"
                pnl = j.get("pnl_usd") or j.get("pnl") or "—"
                status = j.get("status") or ("CLOSED" if j.get("closed") else "—")
                # Extended HUNTERS fields
                verdict = j.get("hunters_verdict") or j.get("verdict") or "—"
                score = j.get("hunters_final_score") or j.get("score") or "—"
                risk = j.get("risk_usd") or "—"
                profile = j.get("profile") or "—"
                entry = j.get("entry_price") or "—"
                exit_p = j.get("exit_price") or "—"
                # Format: symbol SIDE verdict(score) PnL risk profile
                line = f"• {sym} {side} {verdict}({score}) PnL={pnl}"
                if risk != "—":
                    line += f" R={risk}"
                if profile != "—":
                    line += f" [{profile}]"
                if entry != "—" and exit_p != "—":
                    line += f" ({entry}→{exit_p})"
                out.append(line)
            except Exception:
                out.append(f"• {ln[:120]}")
        await self._reply(
            update, "🧾 LAST TRADES:\n" + ("\n".join(out) if out else "—")
        )

    async def on_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        q = update.callback_query
        if not q:
            return
        try:
            await q.answer()
        except Exception:
            pass
        data = (q.data or "").strip()

        # Main panel callbacks
        if data == "hope_refresh":
            await self.cmd_panel(update, context)
            return
        if data == "hope_morning":
            await self.cmd_morning(update, context)
            return
        if data == "hope_night":
            await self.cmd_night(update, context)
            return
        if data == "hope_restart":
            await self.cmd_restart(update, context)
            return
        if data == "hope_stop_on":
            await self.cmd_stop_on(update, context)
            return
        if data == "hope_stop_off":
            await self.cmd_stop_off(update, context)
            return
        if data == "hope_balance":
            await self.cmd_balance(update, context)
            return
        if data == "hope_diag":
            await self.cmd_diag(update, context)
            return
        if data == "hope_signals":
            await self.cmd_signals(update, context)
            return
        if data == "hope_trades":
            await self.cmd_trades(update, context)
            return
        if data == "hope_stack":
            await self.cmd_stack(update, context)
            return
        if data == "hope_chat":
            await self.cmd_chat(update, context)
            return
        if data == "hope_help":
            await self.cmd_help(update, context)
            return

        # Friend Chat callbacks (v2.2.0)
        if data == "chat_tunnel_status" or data == "chat_bridge_status" or data == "chat_full_status":
            await self.cmd_chat(update, context)
            return
        if data == "chat_restart_executor":
            await self._chat_restart_service(update, "executor")
            return
        if data == "chat_restart_all":
            await self._chat_restart_all_agents(update)
            return
        if data == "chat_start_tunnel":
            await self._chat_start_tunnel(update)
            return

        # Confirmation callbacks (v2.3.0 - GPT TZ)
        if data == "confirm_stop_on":
            await self._do_stop_on(update)
            return
        if data == "confirm_mode_live":
            await self._do_mode_change(update, "LIVE")
            return
        if data == "cancel_action":
            await self._reply(update, "❌ Действие отменено.")
            return

        # Stack control callbacks
        if data == "stack_refresh":
            await self.cmd_stack(update, context)
            return
        if data == "stack_start":
            await self._handle_stack_action(update, "START")
            return
        if data == "stack_stop":
            await self._handle_stack_action(update, "STOP")
            return
        if data == "stack_fixdup":
            await self._handle_stack_action(update, "FIXDUP")
            return

        await self._reply(update, "⚠️ Unknown button")

    async def _handle_stack_action(self, update: Update, action: str) -> None:
        """Handle stack control actions (START/STOP/FIXDUP) via launcher v2."""
        if not PS_LAUNCHER_V2.exists():
            await self._reply(
                update,
                f"❌ Launcher v2 not found: {PS_LAUNCHER_V2}\n"
                "Install it first from tools/launch_hope_stack_pidtruth_v2.ps1",
            )
            return

        h = _health()
        mode = _mode_from_health(h)

        # Build command based on action
        if action == "START":
            args = ["-Mode", mode]
            msg = f"▶ Starting HOPE stack (mode={mode})..."
        elif action == "STOP":
            # TODO: implement STOP in launcher v2 (kill all roles gracefully)
            await self._reply(update, "⚠️ STOP not yet implemented in launcher v2")
            return
        elif action == "FIXDUP":
            # FIXDUP is implicit in launcher v2 (always runs FixDup before StartMissing)
            args = ["-Mode", mode]
            msg = "🧹 Fixing duplicates + starting missing..."
        else:
            await self._reply(update, f"❌ Unknown action: {action}")
            return

        await self._reply(update, msg)

        # Run launcher v2 async
        out_file = LOGS_DIR / f"stack_{action.lower()}_stdout.log"
        err_file = LOGS_DIR / f"stack_{action.lower()}_stderr.log"

        cmd = [
            _ps_exe(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PS_LAUNCHER_V2),
        ] + args

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(ROOT),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

            # Write logs
            try:
                out_file.write_bytes(stdout or b"")
                err_file.write_bytes(stderr or b"")
            except Exception:
                pass

            if proc.returncode == 0:
                await self._reply(
                    update,
                    f"✅ {action} completed successfully.\n"
                    f"Logs: logs/stack_{action.lower()}_*.log",
                )
                # Refresh stack status
                await self.cmd_stack(update, None)
            elif proc.returncode == 2:
                await self._reply(
                    update,
                    f"⚠️ {action} partial success (some services failed).\n"
                    f"Check logs: logs/stack_{action.lower()}_stderr.log",
                )
            else:
                tail = _decode_bytes(stderr[-2000:] if stderr else b"")
                await self._reply(
                    update,
                    f"❌ {action} failed (exit {proc.returncode}).\n\n"
                    f"stderr tail:\n{tail[:500]}",
                )
        except asyncio.TimeoutError:
            await self._reply(
                update, f"❌ {action} timeout (30s). Check logs in logs/"
            )
        except Exception as e:
            await self._reply(update, f"❌ {action} error: {type(e).__name__}: {e}")

    async def _post_init(self, app: Application) -> None:
        cmds = [
            BotCommand("panel", "панель + кнопки"),
            BotCommand("status", "краткий статус"),
            BotCommand("mode", "⚙️ режим DRY/LIVE"),
            BotCommand("balance", "баланс"),
            BotCommand("stop", "статус STOP.flag"),
            BotCommand("stop_on", "включить STOP.flag ⚠️"),
            BotCommand("stop_off", "выключить STOP.flag"),
            BotCommand("morning", "🌅 запуск стека"),
            BotCommand("night", "🌙 stop + report"),
            BotCommand("restart", "🔄 перезапуск стека"),
            BotCommand("stack", "🧱 управление stack"),
            BotCommand("chat", "💬 чат друзей"),
            BotCommand("signals", "📡 последние сигналы"),
            BotCommand("trades", "📊 последние сделки"),
            BotCommand("diag", "диагностика"),
            BotCommand("health", "🏥 статус систем"),
            BotCommand("logs", "📋 последние ошибки"),
            BotCommand("whoami", "твой ID"),
            BotCommand("version", "версия"),
            BotCommand("help", "помощь"),
        ]
        try:
            await app.bot.set_my_commands(cmds)
            self.log.info("Bot commands registered (setMyCommands): %s", len(cmds))
        except Exception as e:
            self.log.warning("setMyCommands failed: %s: %s", type(e).__name__, e)

        # v2.3.4: Schedule heartbeat job properly within Application's event loop
        if app.job_queue:
            app.job_queue.run_repeating(
                _heartbeat_job_callback,
                interval=_TGBOT_HEALTH_INTERVAL,
                first=0,
                name="tgbot_heartbeat",
            )
            self.log.info("Heartbeat job scheduled (interval=%ds)", _TGBOT_HEALTH_INTERVAL)

    def build_app(self) -> Application:
        if not self.token:
            raise RuntimeError(
                "No TELEGRAM_TOKEN_MINI/TELEGRAM_TOKEN loaded (check C:\\secrets\\hope\\.env)"
            )
        # v2.3.5: Add network timeouts to prevent hanging on slow connections
        app = (
            Application.builder()
            .token(self.token)
            .read_timeout(10)
            .write_timeout(10)
            .connect_timeout(5)
            .pool_timeout(3)
            .post_init(self._post_init)
            .build()
        )

        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("panel", self.cmd_panel))
        app.add_handler(CommandHandler("status", self.cmd_status))
        app.add_handler(CommandHandler("balance", self.cmd_balance))
        app.add_handler(CommandHandler("stop", self.cmd_stop))
        app.add_handler(CommandHandler("stop_on", self.cmd_stop_on))
        app.add_handler(CommandHandler("stop_off", self.cmd_stop_off))
        app.add_handler(CommandHandler("morning", self.cmd_morning))
        app.add_handler(CommandHandler("night", self.cmd_night))
        app.add_handler(CommandHandler("restart", self.cmd_restart))
        app.add_handler(CommandHandler("stack", self.cmd_stack))
        app.add_handler(CommandHandler("chat", self.cmd_chat))
        app.add_handler(CommandHandler("signals", self.cmd_signals))
        app.add_handler(CommandHandler("trades", self.cmd_trades))
        app.add_handler(CommandHandler("diag", self.cmd_diag))
        app.add_handler(CommandHandler("health", self.cmd_health))
        app.add_handler(CommandHandler("logs", self.cmd_logs))
        app.add_handler(CommandHandler("whoami", self.cmd_whoami))
        app.add_handler(CommandHandler("version", self.cmd_version))
        app.add_handler(CommandHandler("help", self.cmd_help))
        app.add_handler(CommandHandler("mode", self.cmd_mode))

        app.add_handler(CallbackQueryHandler(self.on_callback))
        return app


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [tg_bot] %(levelname)s: %(message)s"
    )


def main() -> None:
    _setup_logging()
    logger = logging.getLogger("tg_bot")
    _ensure_env_loaded(logger)

    bot = HopeMiniBot()
    logger.info("Starting polling... (%s)", HopeMiniBot.VERSION)
    logger.info(
        "Bot config: allowed_ids=%s | token_present=%s",
        bot.allowed_ids,
        bool(bot.token),
    )

    app = bot.build_app()

    # HOPE_SINGLETON_RUNPOLLING_PATCH
    root = _hope_project_root()
    _hope_venv_guard(root)
    _hope_acquire_pid_lock(root / "state" / "pids" / "tg_bot_simple.lock")
    _hope_install_log_redaction()

    # v2.3.5: Write initial health, job scheduled via post_init
    _write_tgbot_health_sync()
    logger.info("health_tgbot.json created: %s", HEALTH_TGBOT)

    # Notify systemd we're ready
    if sd_ready():
        logger.info("Systemd READY notification sent")

    app.run_polling(close_loop=False)


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
        try:
            sys.executable = venv_py_str
        except (AttributeError, TypeError):
            pass

    # Force multiprocessing to use venv python for children (Windows).
    try:
        import multiprocessing as _mp

        try:
            _mp.set_executable(str(venv_py))
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
    try:
        import subprocess as _sp

        _original_popen_class = _sp.Popen
        venv_py_str = str(venv_py)

        class _HopePatchedPopen(_original_popen_class):
            def __init__(self, *args, **kwargs):
                if "executable" not in kwargs:
                    if args and len(args) > 0:
                        cmd = args[0]
                        if isinstance(cmd, (list, tuple)) and len(cmd) > 0:
                            first_arg = str(cmd[0])
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
                    exec_path = str(kwargs.get("executable", ""))
                    if "Python312" in exec_path or (
                        "python" in exec_path.lower() and ".venv" not in exec_path
                    ):
                        kwargs["executable"] = venv_py_str

                super().__init__(*args, **kwargs)

        _sp.Popen = _HopePatchedPopen
    except Exception:
        pass


def _hope_acquire_pid_lock(lock_path):
    # Windows-friendly lock via msvcrt (kept open for process lifetime).
    import os, sys
    import msvcrt
    from pathlib import Path

    global _LOCK_HANDLE
    if _LOCK_HANDLE is not None:
        return _LOCK_HANDLE

    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+b")
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        sys.stderr.write(
            f"[HOPE] TGBOT already running (lock held). Lock: {lock_path}\n"
        )
        sys.stderr.flush()
        try:
            fh.close()
        except Exception:
            pass
        raise SystemExit(42)

    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()).encode("ascii", "ignore"))
    fh.flush()
    _LOCK_HANDLE = fh
    return fh


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
_hope_early_guard("tg_bot_simple.lock", install_redaction=True)


if __name__ == "__main__":
    main()
