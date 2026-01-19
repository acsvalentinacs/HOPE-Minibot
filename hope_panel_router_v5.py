#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import subprocess
import time
from pathlib import Path

try:
    from telegram.ext import CallbackQueryHandler
except Exception:
    CallbackQueryHandler = None

_ROOT = Path(r"C:\Users\kirillDev\Desktop\TradingBot")
_DEBUG = True
_debug_last = {}

_CMD_PANEL = None
_BUILD_PANEL_KEYBOARD = None


def _throttle(chat_id: int, key: str, cooldown_sec: int = 20) -> bool:
    try:
        now = int(time.time())
        k = f"{chat_id}:{key}"
        last = int(_debug_last.get(k, 0))
        if now - last >= cooldown_sec:
            _debug_last[k] = now
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
        return ("утро" in jl) or ("вечер" in jl) or ("реф" in jl) or ("refresh" in jl) or ("🌅" in j) or ("🌙" in j) or ("🔄" in j) or ("♻" in j) or ("🌃" in j)
    except Exception:
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
    except Exception:
        return ""


async def _start_evening(query):
    try:
        ps1 = _ROOT / "tools" / "evening_workflow.ps1"
        if not ps1.exists():
            await query.answer("❌ evening_workflow.ps1 не найден", show_alert=True)
            return
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1), "-Hours", "8", "-IntervalSec", "300"]
        subprocess.Popen(cmd, cwd=str(_ROOT))
        try:
            await query.answer("🌙 Вечер запущен (8ч / 300с). Лог: logs\\evening_workflow.log", show_alert=True)
        except Exception:
            pass
        try:
            await query.message.reply_text("🌙 Вечер запущен: 8 часов, шаг 300 сек.\nЛог: C:\\Users\\kirillDev\\Desktop\\TradingBot\\logs\\evening_workflow.log")
        except Exception:
            pass
    except Exception:
        try:
            await query.answer("❌ Не смог запустить вечер", show_alert=True)
        except Exception:
            pass


async def _do_refresh(update, query, context):
    try:
        if _CMD_PANEL:
            try:
                await _CMD_PANEL(update, context)
                try:
                    await query.answer("🔄 Обновлено", show_alert=False)
                except Exception:
                    pass
                return
            except Exception:
                pass

        if _BUILD_PANEL_KEYBOARD:
            try:
                await query.edit_message_reply_markup(reply_markup=_BUILD_PANEL_KEYBOARD())
                try:
                    await query.answer("🔄 Обновлено", show_alert=False)
                except Exception:
                    pass
                return
            except Exception:
                pass

        try:
            await query.answer("🔄 Обновлено", show_alert=False)
        except Exception:
            pass
    except Exception:
        try:
            await query.answer("❌ Refresh error", show_alert=True)
        except Exception:
            pass


async def _router(update, context):
    try:
        query = getattr(update, "callback_query", None)
        if not query:
            return

        if not _is_panel_keyboard(query):
            return

        data = (getattr(query, "data", "") or "").strip()
        if not data:
            return

        btn_text = _button_text_by_data(query, data)
        low = (btn_text or "").lower()

        if ("ноч" in low) or ("🌃" in (btn_text or "")):
            return

        if ("вечер" in low) or ("🌙" in (btn_text or "")):
            await _start_evening(query)
            return

        if ("реф" in low) or ("обнов" in low) or ("refresh" in low) or ("🔄" in (btn_text or "")) or ("♻" in (btn_text or "")):
            await _do_refresh(update, query, context)
            return

        if _DEBUG:
            try:
                msg = getattr(query, "message", None)
                chat_id = int(getattr(msg, "chat_id", 0) or 0)
                if not chat_id:
                    ch = getattr(msg, "chat", None)
                    chat_id = int(getattr(ch, "id", 0) or 0)
                if chat_id and _throttle(chat_id, data, cooldown_sec=20):
                    await query.answer(f"DEBUG: text='{btn_text}' | data='{data}'", show_alert=True)
            except Exception:
                pass

    except Exception:
        return


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
