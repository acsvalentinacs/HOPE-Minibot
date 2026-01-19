#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path

try:
    from telegram.ext import CallbackQueryHandler
except Exception:
    CallbackQueryHandler = None

_ROOT = Path(r"C:\Users\kirillDev\Desktop\TradingBot")
_DEBUG = True
_last = {}
_log_file = _ROOT / "logs" / "router_v7_debug.log"

_CMD_PANEL = None
_BUILD_PANEL_KEYBOARD = None


def _log(msg: str):
    try:
        with open(_log_file, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def _throttle(chat_id: int, key: str, cooldown_sec: int = 20) -> bool:
    try:
        now = int(time.time())
        k = f"{chat_id}:{key}"
        last = int(_last.get(k, 0))
        if now - last >= cooldown_sec:
            _last[k] = now
            return True
        return False
    except Exception:
        return False


def _is_panel_keyboard(query) -> bool:
    try:
        msg = getattr(query, "message", None)
        rm = getattr(msg, "reply_markup", None)
        kb = getattr(rm, "inline_keyboard", None)
        if not kb:
            return False
        texts = []
        for row in kb:
            for b in row:
                texts.append((getattr(b, "text", "") or ""))
        j = " | ".join(texts)
        jl = j.lower()
        result = ("утро" in jl) or ("вечер" in jl) or ("refresh" in jl) or ("реф" in jl) or ("restart" in jl) or ("🌅" in j) or ("🌙" in j) or ("🔄" in j) or ("📊" in j)
        _log(f"_is_panel_keyboard: {result} (texts: {j})")
        return result
    except Exception as e:
        _log(f"_is_panel_keyboard ERROR: {e}")
        return False


def _button_text_by_data(query, data: str) -> str:
    try:
        msg = getattr(query, "message", None)
        rm = getattr(msg, "reply_markup", None)
        kb = getattr(rm, "inline_keyboard", None)
        if not kb:
            return ""
        for row in kb:
            for b in row:
                cb = getattr(b, "callback_data", None)
                if cb == data:
                    return (getattr(b, "text", None) or "")
        return ""
    except Exception as e:
        _log(f"_button_text_by_data ERROR: {e}")
        return ""


async def _run_ps_capture(cmd_list: list[str], timeout_sec: int = 180) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd_list,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        raise
    rc = int(proc.returncode or 0)
    out = (out_b or b"").decode("utf-8", errors="replace")
    err = (err_b or b"").decode("utf-8", errors="replace")
    return rc, out, err


def _clip(s: str, n: int = 3200) -> str:
    s = (s or "").strip()
    if not s:
        return "(пусто)"
    return s if len(s) <= n else s[-n:]


async def _start_evening(query) -> None:
    _log("_start_evening: executing")
    try:
        ps1 = _ROOT / "tools" / "evening_workflow.ps1"
        if not ps1.exists():
            await query.answer("evening_workflow.ps1 not found", show_alert=True)
            return
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1), "-Hours", "8", "-IntervalSec", "300"]
        subprocess.Popen(cmd, cwd=str(_ROOT))
        try:
            await query.answer("Evening started (8h / 300s). Log: logs\\evening_workflow.log", show_alert=True)
        except Exception:
            pass
    except Exception as e:
        _log(f"_start_evening ERROR: {e}")


async def _do_refresh(update, query, context) -> None:
    _log("_do_refresh: executing")
    try:
        if _CMD_PANEL:
            try:
                await _CMD_PANEL(update, context)
                try:
                    await query.answer("Refreshed", show_alert=False)
                except Exception:
                    pass
                return
            except Exception:
                pass

        try:
            await query.answer("Refreshed", show_alert=False)
        except Exception:
            pass
    except Exception as e:
        _log(f"_do_refresh ERROR: {e}")


async def _router(update, context) -> None:
    try:
        _log("CALLBACK RECEIVED")
        query = getattr(update, "callback_query", None)
        if not query:
            _log("  query is None, skip")
            return

        data = (getattr(query, "data", "") or "").strip()
        _log(f"  data: {data}")

        btn_text = _button_text_by_data(query, data)
        _log(f"  btn_text: {btn_text}")

        low_text = (btn_text or "").lower()
        _log(f"  low_text: {low_text}")

        is_panel = _is_panel_keyboard(query)
        _log(f"  is_panel: {is_panel}")
        if not is_panel:
            _log("  NOT panel keyboard, skip")
            return

        _log("  Checking conditions...")

        if data == "hope_evening":
            _log("    MATCH: hope_evening (exact data)")
            await _start_evening(query)
            return

        if "вечер" in low_text or "evening" in low_text:
            _log(f"    MATCH: evening (keyword in text)")
            await _start_evening(query)
            return

        if data == "refresh_panel":
            _log("    MATCH: refresh_panel (exact data)")
            await _do_refresh(update, query, context)
            return

        if "refresh" in low_text or "реф" in low_text:
            _log(f"    MATCH: refresh (keyword in text)")
            await _do_refresh(update, query, context)
            return

        if data == "restart_stack":
            _log("    MATCH: restart_stack (exact data)")
            return

        _log("    NO MATCH")

    except Exception as e:
        _log(f"ROUTER ERROR: {e}")


def install(app_obj, cmd_panel=None, build_panel_keyboard=None) -> bool:
    global _CMD_PANEL, _BUILD_PANEL_KEYBOARD
    _CMD_PANEL = cmd_panel
    _BUILD_PANEL_KEYBOARD = build_panel_keyboard

    if CallbackQueryHandler is None:
        return False

    try:
        app_obj.add_handler(CallbackQueryHandler(_router, pattern=r"^.*$"), group=0)
        return True
    except TypeError:
        try:
            app_obj.add_handler(CallbackQueryHandler(_router, pattern=r"^.*$"))
            return True
        except Exception:
            return False
    except Exception:
        return False
