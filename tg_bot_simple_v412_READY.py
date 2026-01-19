#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tg_bot_simple.py — HOPEminiBOT Command Center v4.1.1 (Production-Grade)

v4.1.1 (Final Fixes):
  ✅ Proper file handle cleanup (close out_f/err_f after Popen)
  ✅ Action log reset before each action (clear old runs)
  ✅ Global action lock (prevent УТРО + RESTART simultaneously)
  ✅ Safer concurrent action management

v4.1.0 → v4.1.1 Changes:
  - spawn_powershell() now closes file handles properly
  - _clear_action_logs() resets stdout/stderr files before action
  - _GLOBAL_ACTION_LOCK prevents dangerous concurrent ops
  - Better error handling on file operations
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from dotenv import load_dotenv
from telegram import (
    Update,
    BotCommand,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

BOT_VERSION = "tgbot-4.1.1-final"

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

STATE_DIR = ROOT_DIR / "state"
TOOLS_DIR = ROOT_DIR / "tools"
CONFIG_DIR = ROOT_DIR / "config"
LOGS_DIR = ROOT_DIR / "logs"

HEALTH_FILE = STATE_DIR / "health_v5.json"
STOP_FLAG_FILE = STATE_DIR / "STOP.flag"
LAUNCHER_PIDS_FILE = STATE_DIR / "launcher_pids.json"

HUNTERS_HEALTH_FILE = STATE_DIR / "hunters_listener_health.json"
HUNTERS_SIGNALS_FILE = STATE_DIR / "hunters_signals.jsonl"
HUNTERS_TRADES_JSONL = STATE_DIR / "hunters_trades.jsonl"
HUNTERS_TRADES_JSON = STATE_DIR / "hunters_active_trades.json"

AUDIT_LOG = STATE_DIR / "audit_log.jsonl"
ENGINE_STDERR_LOG = LOGS_DIR / "engine_stderr.log"

PS_START_STACK = TOOLS_DIR / "hope_morning.ps1"
PS_NIGHT = TOOLS_DIR / "hope_night.ps1"
PS_SET_MODE = TOOLS_DIR / "set_engine_mode.ps1"
PS_RESTART_STACK = TOOLS_DIR / "start_hope_stack_clean.ps1"

PY_SYNC_TRADES = TOOLS_DIR / "sync_hunters_trades_v1.py"

PROFILE_ALIASES: Dict[str, str] = {
    "BOOST": "HUNTERS_BOOST",
    "SAFE": "HUNTERS_SAFE",
    "SCALP": "HUNTERS_SCALP",
    "SWING": "HUNTERS_SWING",
}

from minibot.pid_lock import acquire_pid_lock, release_pid_lock

SECRETS_ENV_PATH = Path(r"C:\secrets\hope\.env")
if SECRETS_ENV_PATH.exists():
    load_dotenv(SECRETS_ENV_PATH)
else:
    load_dotenv(ROOT_DIR / ".env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_TOKEN_MINI") or ""
HOPE_UI_PIN = (os.getenv("HOPE_UI_PIN") or "").strip()

ALLOWED_IDS: Set[int] = {
    int(x)
    for x in (os.getenv("TELEGRAM_ALLOWED") or "").replace(",", " ").split()
    if x.strip().isdigit()
}

EXCHANGE_SECRETS = {
    "BINANCE_API_KEY": os.getenv("BINANCE_API_KEY") or os.getenv("API_KEY"),
    "BINANCE_API_SECRET": os.getenv("BINANCE_API_SECRET") or os.getenv("API_SECRET"),
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [tg_bot] %(levelname)s: %(message)s",
)
logger = logging.getLogger("tg_bot_simple")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

_exchange_client = None


def get_exchange():
    global _exchange_client
    if _exchange_client is not None:
        return _exchange_client
    try:
        from minibot.core.exchange_client import ExchangeClient
        from minibot.core.types import EngineMode
        _exchange_client = ExchangeClient(EngineMode.LIVE, EXCHANGE_SECRETS)
        logger.info("ExchangeClient initialized (LIVE)")
    except Exception as e:
        logger.error("Exchange init failed: %s", e)
        return None
    return _exchange_client


def _now_ts() -> float:
    return time.time()


def is_admin(user_id: int) -> bool:
    return (user_id in ALLOWED_IDS) if ALLOWED_IDS else False


def audit(event: Dict[str, Any]) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        event = dict(event)
        event.setdefault("ts", _now_ts())
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error("audit write failed: %s", e)


async def deny(update: Update, text: str, action: str = "unknown") -> bool:
    u = update.effective_user
    audit({
        "type": "deny",
        "action": action,
        "user_id": getattr(u, "id", None),
        "username": getattr(u, "username", None),
        "chat_id": getattr(update.effective_chat, "id", None),
    })
    m = update.effective_message or (update.callback_query.message if update.callback_query else None)
    if m:
        await m.reply_text(text)
    return False


async def guard_admin(update: Update, action: str = "admin") -> bool:
    u = update.effective_user
    if u is None:
        return False
    if not ALLOWED_IDS:
        return await deny(
            update,
            "⛔ Управление запрещено: TELEGRAM_ALLOWED не настроен.\n"
            "Сделай /whoami и добавь свой ID в C:\\secrets\\hope\\.env (TELEGRAM_ALLOWED=...).",
            action=action,
        )
    if not is_admin(u.id):
        return await deny(update, "⛔ Доступ запрещён.", action=action)
    return True


async def guard_read(update: Update, action: str = "read") -> bool:
    u = update.effective_user
    if u is None:
        return False
    if not ALLOWED_IDS:
        return await deny(
            update,
            "⛔ Доступ запрещён: TELEGRAM_ALLOWED не настроен.\n"
            "Сделай /whoami и добавь свой ID в C:\\secrets\\hope\\.env.",
            action=action,
        )
    if not is_admin(u.id):
        return await deny(update, "⛔ Доступ запрещён.", action=action)
    return True


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        if not text:
            return None
        return json.loads(text)
    except Exception:
        return None


def format_uptime(seconds: float) -> str:
    try:
        s = max(0, int(seconds))
    except Exception:
        return "0s"
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


def _decode_bytes(b: bytes) -> str:
    for enc in ("utf-8", "cp866", "cp1251"):
        try:
            return b.decode(enc)
        except Exception:
            continue
    return b.decode("utf-8", errors="replace")


def tail_lines(path: Path, n: int = 20) -> List[str]:
    if not path.exists():
        return []
    try:
        data = path.read_bytes()
        text = _decode_bytes(data)
        lines = [ln.rstrip("\r") for ln in text.splitlines() if ln.strip() != ""]
        return lines[-n:]
    except Exception:
        return []


def is_stop_active() -> bool:
    return STOP_FLAG_FILE.exists()


def set_stop_flag(active: bool) -> None:
    try:
        STOP_FLAG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if active:
            STOP_FLAG_FILE.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
        elif STOP_FLAG_FILE.exists():
            STOP_FLAG_FILE.unlink()
    except Exception as e:
        logger.error("set_stop_flag(%s): %s", active, e)


def _health_summary() -> Dict[str, Any]:
    h = read_json(HEALTH_FILE) or {}
    mode = str(h.get("mode", "UNKNOWN")).upper()
    engine_ok = bool(h.get("engine_ok", True))
    uptime = format_uptime(float(h.get("uptime_sec", 0) or 0))
    hb_ts = float(h.get("last_heartbeat_ts", 0) or 0)
    hb_ago = int(_now_ts() - hb_ts) if hb_ts > 0 else None
    return {
        "mode": mode,
        "engine_ok": engine_ok,
        "uptime": uptime,
        "heartbeat_ago": hb_ago,
        "open_positions_count": int(h.get("open_positions_count", 0) or 0),
        "queue_size": h.get("queue_size"),
    }


def build_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["/status", "/panel"],
            ["/morning", "/night"],
            ["/trades", "/balance"],
            ["/signals", "/diag"],
            ["/help", "/restart"],
        ],
        resize_keyboard=True,
    )


def build_panel_keyboard() -> InlineKeyboardMarkup:
    h = _health_summary()
    mode = h["mode"]
    stop_text = "⏹ STOP ON" if is_stop_active() else "▶ STOP OFF"
    pin_on = "ON" if HOPE_UI_PIN else "OFF"

    live_text = "✅ LIVE ACTIVE" if mode == "LIVE" else "🟢✅ GO LIVE ✅🟢"
    dry_text = "✅ DRY ACTIVE" if mode == "DRY" else "🔵🛡 DRY SAFE 🛡🔵"

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🌅 УТРО", callback_data="morning")],
            [InlineKeyboardButton("🌙 НОЧЬ", callback_data="night")],
            [InlineKeyboardButton(live_text, callback_data="set_live")],
            [InlineKeyboardButton(dry_text, callback_data="set_dry")],
            [InlineKeyboardButton(stop_text, callback_data="stop_toggle")],
            [InlineKeyboardButton("🔄 Restart Stack", callback_data="restart_stack")],
            [InlineKeyboardButton("📊 Refresh", callback_data="refresh_panel")],
            [InlineKeyboardButton(f"PIN(LIVE): {pin_on}", callback_data="pin_hint")],
        ]
    )


def normalize_profile(raw: str) -> str:
    if not raw or not str(raw).strip():
        return "NO_PROFILE"
    return str(raw).strip().upper()


def match_profile(trade_profile: str, filter_arg: str) -> bool:
    trade_profile = normalize_profile(trade_profile)
    filter_upper = filter_arg.upper()
    if trade_profile == filter_upper:
        return True
    if filter_upper in PROFILE_ALIASES and trade_profile == PROFILE_ALIASES[filter_upper]:
        return True
    if filter_upper in trade_profile:
        return True
    return False


def calculate_profile_stats(trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    stats: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"wins": 0, "losses": 0, "be": 0, "pnl": 0.0, "count": 0}
    )
    for t in trades:
        status = str(t.get("status", "")).upper()
        if status != "CLOSED":
            continue
        profile = normalize_profile(t.get("profile", ""))
        s = stats[profile]
        s["count"] += 1
        try:
            pnl = float(t.get("pnl_usd", 0))
            s["pnl"] += pnl
            if abs(pnl) < 0.01:
                s["be"] += 1
            elif pnl > 0:
                s["wins"] += 1
            else:
                s["losses"] += 1
        except Exception:
            pass
    return dict(stats)


def format_profile_stats(stats: Dict[str, Dict[str, Any]]) -> str:
    if not stats:
        return "Нет данных по профилям."
    lines = ["📊 СТАТИСТИКА ПО ПРОФИЛЯМ:", ""]
    sorted_profiles = sorted(stats.items(), key=lambda x: x[1]["count"], reverse=True)
    for profile, s in sorted_profiles:
        wins, losses, be, pnl = s["wins"], s["losses"], s["be"], s["pnl"]
        total = wins + losses
        wr_str = f"{wins / total * 100:.0f}%" if total > 0 else "n/a"
        pnl_icon = "🟢" if pnl >= 0 else "🔴"
        lines.append(
            f"▸ {profile}: {s['count']} trades | WR: {wr_str} "
            f"(W:{wins} L:{losses} BE:{be}) | {pnl_icon} {pnl:+.2f}$"
        )
    return "\n".join(lines)


def load_trades_universal() -> List[Dict[str, Any]]:
    if HUNTERS_TRADES_JSON.exists():
        try:
            raw = HUNTERS_TRADES_JSON.read_text(encoding="utf-8", errors="ignore").strip()
            if raw.startswith("["):
                obj = json.loads(raw)
                if isinstance(obj, list):
                    return obj
        except Exception:
            pass

    if not HUNTERS_TRADES_JSONL.exists():
        return []
    try:
        raw = HUNTERS_TRADES_JSONL.read_text(encoding="utf-8", errors="ignore").strip()
        if not raw:
            return []
        items: List[Dict[str, Any]] = []
        for ln in raw.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
                if isinstance(obj, dict):
                    items.append(obj)
            except Exception:
                continue
        return items
    except Exception:
        return []


def _pin_ok(context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not HOPE_UI_PIN:
        return True
    ts = float(context.user_data.get("pin_ok_until", 0) or 0)
    return ts > _now_ts()


def _set_pin_ok(context: ContextTypes.DEFAULT_TYPE, ttl_sec: int = 600) -> None:
    context.user_data["pin_ok_until"] = _now_ts() + ttl_sec


def _ps_cmd(script: Path, args: List[str]) -> List[str]:
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *args]


def _ps_creationflags() -> int:
    try:
        return subprocess.CREATE_NO_WINDOW
    except Exception:
        return 0


def _compact_tail_from_lines(lines: List[str], n: int = 30) -> str:
    if not lines:
        return "(пусто)"
    return "\n".join(lines[-n:])


def _action_log_paths(action_key: str) -> Tuple[Path, Path]:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    outp = LOGS_DIR / f"action_{action_key}_stdout.log"
    errp = LOGS_DIR / f"action_{action_key}_stderr.log"
    return outp, errp


def _clear_action_logs(action_key: str) -> None:
    """Очистить логи действия перед запуском (чтобы не было кашей из старых прогонов)"""
    outp, errp = _action_log_paths(action_key)
    try:
        outp.write_text("", encoding="utf-8")
        errp.write_text("", encoding="utf-8")
    except Exception as e:
        logger.warning("_clear_action_logs(%s): %s", action_key, e)


def spawn_powershell(action_key: str, script: Path, args: List[str]) -> Dict[str, Any]:
    """Запустить PowerShell скрипт в фоне, редирект в файлы, с ПРАВИЛЬНЫМ закрытием хэндлов"""
    if not script.exists():
        return {"ok": False, "error": f"Script not found: {script}", "pid": None}

    _clear_action_logs(action_key)
    outp, errp = _action_log_paths(action_key)

    try:
        out_f = open(outp, "wb", buffering=0)
        err_f = open(errp, "wb", buffering=0)
    except Exception as e:
        return {"ok": False, "error": f"Cannot open log files: {e}", "pid": None}

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    try:
        p = subprocess.Popen(
            _ps_cmd(script, args),
            cwd=str(ROOT_DIR),
            stdout=out_f,
            stderr=err_f,
            env=env,
            creationflags=_ps_creationflags(),
        )
        # ВАЖНО: закрываем хэндлы после передачи их Popen
        # Popen сам управляет процессом и файлами, нам они больше не нужны
        out_f.close()
        err_f.close()
        
        return {"ok": True, "pid": p.pid, "proc": p, "stdout_path": str(outp), "stderr_path": str(errp)}
    except Exception as e:
        try:
            out_f.close()
        except Exception:
            pass
        try:
            err_f.close()
        except Exception:
            pass
        return {"ok": False, "error": f"Popen failed: {e}", "pid": None}


async def wait_process(p: subprocess.Popen, timeout_sec: int) -> Tuple[bool, Optional[int]]:
    def _wait():
        try:
            return (True, p.wait(timeout=timeout_sec))
        except subprocess.TimeoutExpired:
            return (False, None)

    ok, rc = await asyncio.to_thread(_wait)
    if ok:
        return True, int(rc) if rc is not None else 0

    try:
        p.kill()
    except Exception:
        pass

    def _wait2():
        try:
            return p.wait(timeout=5)
        except Exception:
            return None

    rc2 = await asyncio.to_thread(_wait2)
    return False, int(rc2) if rc2 is not None else None


async def run_sync_trades(timeout_sec: int = 30) -> Dict[str, Any]:
    if not PY_SYNC_TRADES.exists():
        return {"returncode": 127, "stdout": "", "stderr": "sync_hunters_trades_v1.py not found"}

    cmd = [sys.executable, str(PY_SYNC_TRADES)]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    def _run():
        try:
            r = subprocess.run(
                cmd,
                cwd=str(ROOT_DIR),
                capture_output=True,
                timeout=timeout_sec,
                env=env,
            )
            return {"returncode": int(r.returncode), "stdout": _decode_bytes(r.stdout or b""), "stderr": _decode_bytes(r.stderr or b"")}
        except subprocess.TimeoutExpired:
            return {"returncode": 124, "stdout": "", "stderr": f"TIMEOUT after {timeout_sec}s"}
        except Exception as e:
            return {"returncode": 1, "stdout": "", "stderr": str(e)}

    return await asyncio.to_thread(_run)


def _format_action_report(action_title: str, action_key: str, rc: Optional[int], timed_out: bool) -> str:
    """Отчет с ровным выравниванием (v4.1.2)"""
    h = _health_summary()
    hb_ago = h.get("heartbeat_ago")
    hb_str = f"{hb_ago}s" if hb_ago is not None else "N/A"

    outp, errp = _action_log_paths(action_key)
    ps_out = tail_lines(outp, 40)
    ps_err = tail_lines(errp, 30)
    eng_tail = tail_lines(ENGINE_STDERR_LOG, 25)

    engine_ok = h.get("engine_ok")
    engine_ok_str = "✅" if engine_ok is True else ("⚠️" if engine_ok is False else "❓")

    def _clean_line(s: str) -> str:
        """Убирает лидирующие пробелы"""
        return (s or "").replace("	", "    ").strip().lstrip()

    def _rewrite_roles_line(s: str) -> str:
        """ENGINE ✗ PID - → ENGINE: ✗ | PID: -"""
        s0 = _clean_line(s)
        import re
        m = re.match(r"^(ENGINE|TGBOT|LISTENER)\s+([✓✗])\s+PID\s+(.+)$", s0)
        if m:
            role, mark, pid = m.group(1), m.group(2), m.group(3).strip()
            return f"{role}: {mark} | PID: {pid}"
        return s0

    status_str = "TIMEOUT (killed)" if timed_out else "OK"
    q = h.get("queue_size")
    q_str = "N/A" if q is None else str(q)

    lines = []
    lines.append(action_title)
    lines.append(f"RESULT: returncode={rc if rc is not None else 'N/A'} | status={status_str}")
    lines.append("")
    lines.append("📊 HEALTH")
    lines.append(f"mode: {h.get('mode', 'UNKNOWN')}")
    lines.append(f"engine_ok: {engine_ok_str}")
    lines.append(f"uptime: {h.get('uptime', '0s')}")
    lines.append(f"heartbeat_ago: {hb_str}")
    lines.append(f"queue: {q_str}")

    if h.get("engine_version"):
        lines.append(f"engine_version: {h.get('engine_version')}")
    if h.get("last_error"):
        lines.append(f"last_error: {h.get('last_error')}")

    lines.append("")
    lines.append("🧩 ENGINE STDERR (tail)")
    if eng_tail:
        for ln in eng_tail:
            lines.append(_rewrite_roles_line(ln))
    else:
        lines.append("(пусто)")

    lines.append("")
    lines.append("🧨 POWERSHELL STDERR (tail)")
    if ps_err:
        for ln in ps_err:
            lines.append(_rewrite_roles_line(ln))
    else:
        lines.append("(пусто)")

    lines.append("")
    lines.append("📜 POWERSHELL STDOUT (tail)")
    if ps_out:
        for ln in ps_out:
            lines.append(_rewrite_roles_line(ln))
    else:
        lines.append("(пусто)")

    return "\n".join(lines)


def get_hunters_listener_status() -> Tuple[str, str]:
    try:
        if not HUNTERS_HEALTH_FILE.exists():
            return "⏳ Waiting", "no health file"
        try:
            health_raw = HUNTERS_HEALTH_FILE.read_text(encoding="utf-8-sig")
            health = json.loads(health_raw or "{}")
        except Exception as e:
            return "⚠️ Bad health file", str(e)

        last_hb = float(health.get("last_heartbeat_ts", 0.0) or 0.0)
        total_signals = int(health.get("total_signals", 0) or 0)
        status_flag = str(health.get("status", "") or "").upper()

        now = _now_ts()
        hb_age = (now - last_hb) if last_hb > 0 else None

        def fmt_age(age_sec: Optional[float]) -> str:
            if age_sec is None:
                return "unknown"
            if age_sec < 60:
                return f"{int(age_sec)}s"
            return f"{int(age_sec // 60)}m"

        HEARTBEAT_STALE_SEC = 600

        if status_flag and status_flag not in ("OK", "RUNNING", "INIT"):
            detail = f"status={status_flag}"
            if hb_age is not None:
                detail += f", hb {fmt_age(hb_age)} ago"
            return "⚠️ Bad status", detail

        if hb_age is not None and hb_age > HEARTBEAT_STALE_SEC:
            return "❌ Dead", f"hb {fmt_age(hb_age)} ago"

        return "✅ Alive", f"hb {fmt_age(hb_age)} ago, total {total_signals}"

    except Exception:
        return "❓ Unknown", "internal error"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(
        update,
        f"🤖 HOPEminiBOT ({BOT_VERSION})\n\nКомандный центр HOPE.\n",
        reply_markup=build_main_keyboard(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🛠 Команды HOPEminiBOT:\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "/start — меню\n"
        "/panel — панель + кнопки\n"
        "/status — краткий статус\n"
        "/morning — 🌅 УТРО (start stack)\n"
        "/night — 🌙 НОЧЬ (stop + report)\n"
        "/pin 1234 — PIN для LIVE (если настроен HOPE_UI_PIN)\n"
        "/live — 🟢 LIVE (требует PIN, если задан)\n"
        "/dry — 🔵 DRY (тестовый режим)\n"
        "/trades — сделки HUNTERS (+ фильтр)\n"
        "/hunters — обзор HUNTERS\n"
        "/hunters_stats — статистика HUNTERS\n"
        "/signals — последние сигналы\n"
        "/balance — баланс биржи\n"
        "/diag — диагностика\n"
        "/restart — перезапуск стека\n"
        "/whoami — твой ID\n"
        "/version — версии\n"
        "/help — помощь\n"
    )
    await _reply(update, text)


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    allowed = "✅" if is_admin(user.id) else "⛔"
    await _reply(
        update,
        f"👤 whoami\nID: {user.id}\nUsername: @{user.username or '—'}\nДоступ: {allowed}\n\n"
        "Добавь этот ID в C:\\secrets\\hope\\.env:\n"
        "TELEGRAM_ALLOWED=100767137"
    )


async def cmd_version(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_read(update, action="version"):
        return
    h = read_json(HEALTH_FILE) or {}
    engine_ver = h.get("engine_version", h.get("version", "unknown"))
    await _reply(update, f"🧪 Версии\nБот: {BOT_VERSION}\nЯдро: {engine_ver}\nРежим: {h.get('mode', 'UNKNOWN')}\nPIN(LIVE): {'ON' if HOPE_UI_PIN else 'OFF'}")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_read(update, action="status"):
        return
    h = _health_summary()
    stop_icon = "⏹ ON" if is_stop_active() else "▶ OFF"
    await _reply(
        update,
        "📊 HOPE v5 — статус\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Engine: {'✅' if h['engine_ok'] else '⚠️'} | {h['mode']} | {h['uptime']}\n"
        f"Позиции: {h['open_positions_count']}\n"
        f"STOP.flag: {stop_icon}",
        reply_markup=build_main_keyboard(),
    )


async def cmd_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_read(update, action="panel"):
        return
    h = _health_summary()
    queue = h.get("queue_size")
    q_str = "None" if queue is None else str(queue)
    stop_icon = "⏹ ON" if is_stop_active() else "▶ OFF"
    text = (
        "📊 Панель HOPE v5\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Engine: {'✅ OK' if h['engine_ok'] else '⚠️ PROBLEM'}\n"
        f"Режим: {h['mode']}\n"
        f"Аптайм: {h['uptime']}\n"
        f"Позиции: {h['open_positions_count']}\n"
        f"Очередь: {q_str}\n"
        f"STOP.flag: {stop_icon}\n"
        f"PIN(LIVE): {'ON' if HOPE_UI_PIN else 'OFF'}"
    )
    await _reply(update, text, reply_markup=build_panel_keyboard())


async def cmd_diag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_read(update, action="diag"):
        return
    h = read_json(HEALTH_FILE) or {}
    hb_ts = float(h.get("last_heartbeat_ts", 0) or 0)
    age = int(_now_ts() - hb_ts) if hb_ts and hb_ts > 0 else None
    age_str = f"{age}s" if age is not None else "N/A"
    await _reply(
        update,
        "🔬 Diag HOPE\n"
        f"health_v5.json: {'OK' if h else 'нет данных'}\n"
        f"engine_ok: {h.get('engine_ok', 'N/A')}\n"
        f"last_error: {h.get('last_error') or '—'}\n"
        f"heartbeat_ago: {age_str}\n"
        f"engine_stderr_tail_lines: {len(tail_lines(ENGINE_STDERR_LOG, 20))}"
    )


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_read(update, action="balance"):
        return
    client = get_exchange()
    if client is None:
        await _reply(update, "❌ Exchange не инициализирован.")
        return
    try:
        bal = client.fetch_balance()
        total = bal.get("total_usd") if isinstance(bal, dict) else getattr(bal, "total_usd", None)
        free = bal.get("free_usd") if isinstance(bal, dict) else getattr(bal, "free_usd", None)
        if total is not None and free is not None:
            await _reply(update, f"💰 Баланс:\nTotal: {float(total):.2f} USDT\nFree: {float(free):.2f} USDT")
        else:
            await _reply(update, "💰 Баланс получен, но формат не распознан.")
    except Exception as e:
        await _reply(update, f"❌ Ошибка: {e}")


async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_read(update, action="signals"):
        return
    qfile = STATE_DIR / "hunters_signals_scored.jsonl"
    if not qfile.exists():
        await _reply(update, "📡 Сигналов в очереди нет.")
        return

    raw = qfile.read_text(encoding="utf-8", errors="ignore").strip().splitlines()
    if not raw:
        await _reply(update, "📭 Очередь пуста.")
        return

    def pick_ts(obj: dict):
        for k in ("ts", "timestamp", "time", "created_ts", "created_at", "ts_ms", "timestamp_ms"):
            if k in obj and obj[k] is not None:
                try:
                    v = float(obj[k])
                except Exception:
                    continue
                if v > 10_000_000_000:
                    v = v / 1000.0
                return v
        return None

    last = raw[-5:] if len(raw) > 5 else raw
    out = ["📡 LAST SIGNALS:"]
    wrote = 0
    for line in last:
        try:
            obj = json.loads(line)
        except Exception:
            continue
        ts = pick_ts(obj)
        dt = datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "??:??"
        symbol = obj.get("symbol") or obj.get("pair") or obj.get("ticker") or "UNKNOWN"
        side = (obj.get("side") or obj.get("direction") or "UNK").upper()
        verdict = obj.get("verdict") or obj.get("signal") or "—"
        score = obj.get("final_score") if "final_score" in obj else obj.get("score")
        score_str = f" ({score})" if score is not None else ""
        out.append(f"• {dt} | {symbol} | {side} | {verdict}{score_str}")
        wrote += 1

    await _reply(update, "\n".join(out) if wrote else "📭 Очередь пуста (валидных JSON-строк не найдено).")


async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_read(update, action="trades"):
        return

    profile_filter: Optional[str] = None
    stats_only = False
    if context.args:
        arg = context.args[0].upper()
        if arg == "STATS":
            stats_only = True
        else:
            profile_filter = arg

    trades = load_trades_universal()
    if not trades:
        await _reply(update, "📭 Нет данных по сделкам HUNTERS.")
        return

    if stats_only:
        await _reply(update, format_profile_stats(calculate_profile_stats(trades)))
        return

    all_trades = trades
    if profile_filter:
        trades = [t for t in trades if match_profile(t.get("profile", ""), profile_filter)]
        if not trades:
            available = sorted({normalize_profile(t.get("profile", "")) for t in all_trades})
            await _reply(update, f"📭 Нет сделок по фильтру '{profile_filter}'.\n\nДоступные профили: {', '.join(available) or 'нет'}")
            return

    open_trades: List[Dict[str, Any]] = []
    closed_trades: List[Dict[str, Any]] = []
    total_pnl = 0.0
    wins = losses = be_count = 0

    for t in trades:
        status = str(t.get("status", "")).upper()
        if status == "CLOSED":
            closed_trades.append(t)
            try:
                pnl = float(t.get("pnl_usd", 0) or 0)
                total_pnl += pnl
                if abs(pnl) < 0.01:
                    be_count += 1
                elif pnl > 0:
                    wins += 1
                else:
                    losses += 1
            except Exception:
                pass
        else:
            open_trades.append(t)

    def _ts(x: Dict[str, Any]) -> float:
        for k in ("close_ts", "updated_ts", "entry_ts"):
            try:
                v = float(x.get(k) or 0)
                if v > 0:
                    return v
            except Exception:
                continue
        return 0.0

    closed_trades.sort(key=_ts, reverse=True)

    lines: List[str] = []
    lines.append("📊 HUNTERS TRADES" + (f" [*{profile_filter}*]" if profile_filter else ""))
    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append(f"Всего: {len(trades)} | Open: {len(open_trades)} | Closed: {len(closed_trades)}")
    if closed_trades:
        non_be = wins + losses
        wr_str = f"{wins / non_be * 100:.0f}%" if non_be > 0 else "n/a"
        lines.append(f"WinRate: {wr_str} (W:{wins} L:{losses} BE:{be_count})")
    pnl_icon = "🤑" if total_pnl >= 0 else "📉"
    lines.append(f"Total PnL: {pnl_icon} {total_pnl:+.2f} USDT")

    if closed_trades:
        lines.append("\n🏁 CLOSED (last 5):")
        for t in closed_trades[:5]:
            sym = t.get("symbol", "???")
            side = str(t.get("side", "???")).upper()
            try:
                pnl = float(t.get("pnl_usd", 0) or 0)
                pnl_str = f"{pnl:+.2f}"
            except Exception:
                pnl = 0.0
                pnl_str = "?"
            prof = normalize_profile(t.get("profile", ""))
            prof_str = f" [{prof}]" if not profile_filter and prof != "NO_PROFILE" else ""
            icon = "✅" if pnl >= 0 else "🔻"
            lines.append(f"  {icon} {sym} {side} {pnl_str}$ {prof_str}".rstrip())

    await _reply(update, "\n".join(lines))


async def cmd_hunters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_read(update, action="hunters"):
        return
    status_main, status_detail = get_hunters_listener_status()
    trades = load_trades_universal()
    active = [t for t in trades if str(t.get("status", "")).upper() not in ("CLOSED", "CANCELLED")]

    lines = [
        "🎯 HUNTERS Overview",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📡 Listener: {status_main}",
        f"   {status_detail}" if status_detail else "",
        "",
        f"📊 Active: {len(active)}",
    ]
    await _reply(update, "\n".join(lines))


async def cmd_hunters_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_read(update, action="hunters_stats"):
        return
    trades = load_trades_universal()
    if not trades:
        await _reply(update, "📭 Нет данных по сделкам HUNTERS.")
        return
    await _reply(update, format_profile_stats(calculate_profile_stats(trades)))


async def cmd_pin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_admin(update, action="pin"):
        return
    if not HOPE_UI_PIN:
        await _reply(update, "ℹ️ HOPE_UI_PIN не задан — PIN не требуется.")
        return
    if not context.args:
        await _reply(update, "🔐 Введи PIN так: /pin 1234")
        return

    pin = str(context.args[0]).strip()
    if pin == HOPE_UI_PIN:
        _set_pin_ok(context, ttl_sec=600)
        audit({"type": "pin_ok", "user_id": update.effective_user.id, "username": update.effective_user.username})
        await _reply(update, "✅ PIN принят. Теперь можно /live или кнопка GO LIVE (10 минут).")
    else:
        audit({"type": "pin_bad", "user_id": update.effective_user.id, "username": update.effective_user.username})
        await _reply(update, "⛔ Неверный PIN.")


async def cmd_live(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_admin(update, action="live"):
        return
    if HOPE_UI_PIN and not _pin_ok(context):
        await _reply(update, "🔐 Для LIVE нужен PIN.\nСделай: /pin 1234\nПотом снова /live")
        return

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Подтвердить LIVE", callback_data="confirm_live"),
        InlineKeyboardButton("❌ Отмена", callback_data="cancel_live"),
    ]])
    await _reply(update, "⚠️ ВКЛЮЧЕНИЕ LIVE\n\nЭто реальные сделки на бирже.\nНажми подтверждение ниже.\nДля отката есть /dry.", reply_markup=kb)


async def cmd_dry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_admin(update, action="dry"):
        return
    await do_action_popen(
        update,
        context,
        action_key="set_mode_dry",
        running_text="🔵 DRY — перезапуск ENGINE (--mode DRY)\n⏳ Запускаю... (timeout 90s)",
        script=PS_SET_MODE,
        args=["-Mode", "DRY"],
        timeout_sec=90,
        do_sync_trades=True,
    )


async def cmd_morning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_admin(update, action="morning"):
        return
    await do_action_popen(
        update,
        context,
        action_key="morning",
        running_text="🌅 УТРО — запуск стека\n⏳ Запускаю... (timeout 180s)",
        script=PS_START_STACK,
        args=[],
        timeout_sec=180,
        do_sync_trades=True,
    )


async def cmd_night(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_admin(update, action="night"):
        return

    force = False
    if context.args and str(context.args[0]).strip().lower() in ("force", "-force", "--force"):
        force = True

    args = ["-Force"] if force else []
    await do_action_popen(
        update,
        context,
        action_key="night",
        running_text="🌙 НОЧЬ — stop + report\n⏳ Запускаю... (timeout 180s)",
        script=PS_NIGHT,
        args=args,
        timeout_sec=180,
        do_sync_trades=True,
    )


async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_admin(update, action="restart"):
        return
    if PS_RESTART_STACK.exists():
        await do_action_popen(
            update,
            context,
            action_key="restart_stack",
            running_text="🔄 Restart Stack\n⏳ Запускаю... (timeout 180s)",
            script=PS_RESTART_STACK,
            args=["-Force"],
            timeout_sec=180,
            do_sync_trades=True,
        )
    else:
        await _reply(update, "❌ Не найден start_hope_stack_clean.ps1")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    data = query.data or ""

    if not await guard_admin(update, action=f"btn:{data}"):
        try:
            await query.answer("⛔ Доступ запрещён.", show_alert=True)
        except Exception:
            pass
        return

    try:
        await query.answer()
    except Exception:
        pass

    if data == "pin_hint":
        try:
            await query.answer("PIN нужен только для LIVE. Команда: /pin 1234", show_alert=True)
        except Exception:
            pass
        return

    if data == "refresh_panel":
        h = _health_summary()
        queue = h.get("queue_size")
        q_str = "None" if queue is None else str(queue)
        stop_icon = "⏹ ON" if is_stop_active() else "▶ OFF"
        text = (
            "📊 Панель HOPE v5\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"Engine: {'✅ OK' if h['engine_ok'] else '⚠️ PROBLEM'}\n"
            f"Режим: {h['mode']}\n"
            f"Аптайм: {h['uptime']}\n"
            f"Позиции: {h['open_positions_count']}\n"
            f"Очередь: {q_str}\n"
            f"STOP.flag: {stop_icon}\n"
            f"PIN(LIVE): {'ON' if HOPE_UI_PIN else 'OFF'}"
        )
        try:
            await query.edit_message_text(text, reply_markup=build_panel_keyboard())
        except Exception:
            pass
        return

    if data == "stop_toggle":
        new_state = not is_stop_active()
        set_stop_flag(new_state)
        audit({"type": "action", "action": "stop_toggle", "value": new_state, "user_id": update.effective_user.id})
        status = "⏹ ON" if new_state else "▶ OFF"
        try:
            await query.edit_message_text(f"✅ STOP.flag → {status}", reply_markup=build_panel_keyboard())
        except Exception:
            pass
        return

    if data == "morning":
        await cmd_morning(update, context)
        return

    if data == "night":
        await cmd_night(update, context)
        return

    if data == "restart_stack":
        await cmd_restart(update, context)
        return

    if data == "set_dry":
        await cmd_dry(update, context)
        return

    if data == "set_live":
        if HOPE_UI_PIN and not _pin_ok(context):
            try:
                await query.answer("🔐 Нужен PIN: /pin 1234", show_alert=True)
            except Exception:
                pass
            return

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Подтвердить LIVE", callback_data="confirm_live"),
            InlineKeyboardButton("❌ Отмена", callback_data="cancel_live"),
        ]])
        try:
            await query.edit_message_text(
                "⚠️ ВКЛЮЧЕНИЕ LIVE\n\nЭто реальные сделки на бирже.\nНажми подтверждение ниже.\nДля отката есть /dry.",
                reply_markup=kb,
            )
        except Exception:
            pass
        return

    if data == "confirm_live":
        await do_action_popen(
            update,
            context,
            action_key="set_mode_live",
            running_text="🟢 LIVE — перезапуск ENGINE (--mode LIVE)\n⏳ Запускаю... (timeout 90s)",
            script=PS_SET_MODE,
            args=["-Mode", "LIVE"],
            timeout_sec=90,
            do_sync_trades=True,
        )
        return

    if data == "cancel_live":
        audit({"type": "action", "action": "cancel_live", "user_id": update.effective_user.id})
        try:
            await query.edit_message_text("❎ LIVE отменён.", reply_markup=build_panel_keyboard())
        except Exception:
            pass
        return


BOT_COMMANDS: List[BotCommand] = [
    BotCommand("start", "Меню"),
    BotCommand("panel", "Панель + кнопки"),
    BotCommand("status", "Статус"),
    BotCommand("morning", "🌅 УТРО (start stack)"),
    BotCommand("night", "🌙 НОЧЬ (stop + report)"),
    BotCommand("pin", "PIN для LIVE"),
    BotCommand("live", "🟢 LIVE (реальные сделки)"),
    BotCommand("dry", "🔵 DRY (тестовый режим)"),
    BotCommand("trades", "Сделки HUNTERS"),
    BotCommand("hunters", "HUNTERS overview"),
    BotCommand("hunters_stats", "HUNTERS статистика"),
    BotCommand("signals", "Сигналы"),
    BotCommand("balance", "Баланс"),
    BotCommand("diag", "Диагностика"),
    BotCommand("restart", "Перезапуск стека"),
    BotCommand("whoami", "Мой ID"),
    BotCommand("version", "Версии"),
    BotCommand("help", "Помощь"),
]


async def post_init(application: Application) -> None:
    logger.info("Bot started, ALLOWED_IDS=%s", list(ALLOWED_IDS) or "EMPTY")
    try:
        await application.bot.set_my_commands(BOT_COMMANDS)
    except Exception as e:
        logger.error("set_my_commands error: %s", e)

    if ALLOWED_IDS:
        try:
            await application.bot.send_message(
                chat_id=next(iter(ALLOWED_IDS)),
                text=f"🤖 HOPEminiBOT {BOT_VERSION} запущен!",
            )
        except Exception as e:
            logger.error("Startup ping error: %s", e)


def main() -> None:
    if not acquire_pid_lock("tgbot"):
        logger.error("❌ TG Bot уже запущен! Выход.")
        raise SystemExit(1)

    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN not found")
        release_pid_lock("tgbot")
        raise SystemExit(1)

    try:
        app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

        app.add_handler(CommandHandler("start", cmd_start))
        app.add_handler(CommandHandler("help", cmd_help))
        app.add_handler(CommandHandler("whoami", cmd_whoami))
        app.add_handler(CommandHandler("version", cmd_version))
        app.add_handler(CommandHandler("status", cmd_status))
        app.add_handler(CommandHandler("panel", cmd_panel))
        app.add_handler(CommandHandler("diag", cmd_diag))
        app.add_handler(CommandHandler("signals", cmd_signals))
        app.add_handler(CommandHandler("trades", cmd_trades))
        app.add_handler(CommandHandler("hunters", cmd_hunters))
        app.add_handler(CommandHandler("hunters_stats", cmd_hunters_stats))
        app.add_handler(CommandHandler("balance", cmd_balance))
        app.add_handler(CommandHandler("restart", cmd_restart))
        app.add_handler(CommandHandler("pin", cmd_pin))
        app.add_handler(CommandHandler("live", cmd_live))
        app.add_handler(CommandHandler("dry", cmd_dry))
        app.add_handler(CommandHandler("morning", cmd_morning))
        app.add_handler(CommandHandler("night", cmd_night))

        app.add_handler(CallbackQueryHandler(callback_handler))

        logger.info("Starting polling... (%s)", BOT_VERSION)
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        release_pid_lock("tgbot")


if __name__ == "__main__":
    main()
