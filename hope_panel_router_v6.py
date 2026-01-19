#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path

try:
    from telegram.ext import CallbackQueryHandler, CommandHandler, ApplicationHandlerStop
except Exception:
    CallbackQueryHandler = None
    CommandHandler = None
    ApplicationHandlerStop = None

_ROOT = Path(r"C:\Users\kirillDev\Desktop\TradingBot")

_DEBUG = True
_last = {}

_CMD_PANEL = None
_BUILD_PANEL_KEYBOARD = None

_KEYS = {
    "evening": "hope_evening",
    "refresh": "refresh_panel",
    "restart": "restart_stack",
}

_PATTERN = r"^(hope_evening|refresh_panel|restart_stack)$"


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
        return ("СѓС‚СЂРѕ" in jl) or ("РІРµС‡РµСЂ" in jl) or ("refresh" in jl) or ("СЂРµС„" in jl) or ("restart" in jl) or ("рџЊ…" in j) or ("рџЊ™" in j) or ("рџ”„" in j) or ("рџ“Љ" in j)
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


async def _run_ps_capture(cmd_list: list[str], timeout_sec: int = 240) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd_list,
        cwd=str(_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        raise
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
        return "(РїСѓСЃС‚Рѕ)"
    return s if len(s) <= n else s[-n:]


async def _start_evening(query) -> None:
    try:
        ps1 = _ROOT / "tools" / "evening_workflow.ps1"
        if not ps1.exists():
            await query.answer("вќЊ evening_workflow.ps1 РЅРµ РЅР°Р№РґРµРЅ", show_alert=True)
            return
        cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1), "-Hours", "8", "-IntervalSec", "300"]
        subprocess.Popen(cmd, cwd=str(_ROOT))
        try:
            await query.answer("рџЊ™ Р’РµС‡РµСЂ Р·Р°РїСѓС‰РµРЅ (8С‡ / 300СЃ). Р›РѕРі: logs\\evening_workflow.log", show_alert=True)
        except Exception:
            pass
        try:
            await query.message.reply_text("рџЊ™ Р’РµС‡РµСЂ Р·Р°РїСѓС‰РµРЅ: 8 С‡Р°СЃРѕРІ, С€Р°Рі 300 СЃРµРє.\nР›РѕРі: C:\\Users\\kirillDev\\Desktop\\TradingBot\\logs\\evening_workflow.log")
        except Exception:
            pass
    except Exception:
        try:
            await query.answer("вќЊ РќРµ СЃРјРѕРі Р·Р°РїСѓСЃС‚РёС‚СЊ РІРµС‡РµСЂ", show_alert=True)
        except Exception:
            pass


async def _restart_stack_report(target_message) -> None:
    ps1 = _ROOT / "tools" / "start_hope_stack_now.ps1"
    if not ps1.exists():
        try:
            await target_message.reply_text(r"вќЊ tools\start_hope_stack_now.ps1 РЅРµ РЅР°Р№РґРµРЅ")
        except Exception:
            pass
        return

    try:
        cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1), "-Mode", "DRY", "-Force", "-NoTgBot"]
        rc, out, err = await _run_ps_capture(cmd, timeout_sec=240)
        tail = "\n".join((out or "").splitlines()[-80:])

        msg = []
        msg.append("рџ§ѕ ACTION REPORT: restart")
        msg.append(f"RESULT: returncode={rc} | status={'OK' if rc == 0 else 'FAIL'}")
        msg.append("")
        msg.append("рџ“њ STDOUT (tail)")
        msg.append(_clip(tail, 2600))

        if (err or "").strip():
            msg.append("")
            msg.append("вљ пёЏ STDERR (tail)")
            msg.append(_clip("\n".join(err.splitlines()[-40:]), 1200))

        text = "\n".join(msg).strip()
        try:
            await target_message.reply_text(text)
        except Exception:
            pass

    except asyncio.TimeoutError:
        try:
            await target_message.reply_text("вќЊ Restart timeout (240s). РџСЂРѕРІРµСЂСЊ: C:\\Users\\kirillDev\\Desktop\\TradingBot\\logs\\* Рё Р·Р°РїСѓСЃРє РІСЂСѓС‡РЅСѓСЋ: .\\tools\\start_hope_stack_now.ps1 -Mode DRY -Force -NoTgBot")
        except Exception:
            pass
    except Exception as e:
        try:
            await target_message.reply_text(f"вќЊ Restart error: {e!r}")
        except Exception:
            pass


async def _do_refresh(update, query, context) -> None:
    try:
        if _CMD_PANEL:
            try:
                await _CMD_PANEL(update, context)
                try:
                    await query.answer("рџ“Љ Refresh: OK", show_alert=False)
                except Exception:
                    pass
                return
            except Exception:
                pass

        if _BUILD_PANEL_KEYBOARD:
            try:
                await query.edit_message_reply_markup(reply_markup=_BUILD_PANEL_KEYBOARD())
                try:
                    await query.answer("рџ“Љ Refresh: OK", show_alert=False)
                except Exception:
                    pass
                return
            except Exception:
                pass

        try:
            await query.answer("рџ“Љ Refresh: OK", show_alert=False)
        except Exception:
            pass

    except Exception:
        try:
            await query.answer("вќЊ Refresh error", show_alert=True)
        except Exception:
            pass


def _stop() -> None:
    if ApplicationHandlerStop is not None:
        raise ApplicationHandlerStop


async def _router(update, context) -> None:
    try:
        query = getattr(update, "callback_query", None)
        if not query:
            return

        data = (getattr(query, "data", "") or "").strip()
        if data not in _KEYS.values():
            return

        if not _is_panel_keyboard(query):
            if _DEBUG:
                try:
                    await query.answer(f"DEBUG: not panel kb | data='{data}'", show_alert=True)
                except Exception:
                    pass
            return

        btn_text = _button_text_by_data(query, data)
        low_text = (btn_text or "").lower()

        if data == _KEYS["evening"] or ("РІРµС‡РµСЂ" in low_text) or ("evening" in low_text):
            await _start_evening(query)
            _stop()
            return

        if data == _KEYS["refresh"] or ("refresh" in low_text) or ("СЂРµС„" in low_text):
            await _do_refresh(update, query, context)
            _stop()
            return

        if data == _KEYS["restart"] or ("restart" in low_text) or ("/restart" in low_text):
            try:
                await query.answer("рџ”„ /restart", show_alert=False)
            except Exception:
                pass
            try:
                await query.message.reply_text("рџ”„ Restart Stack\nвЏі Р—Р°РїСѓСЃРєР°СЋ... (timeout 240s)")
            except Exception:
                pass
            await _restart_stack_report(query.message)
            _stop()
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


async def _cmd_restart(update, context) -> None:
    msg = getattr(update, "effective_message", None)
    if not msg:
        return
    try:
        await msg.reply_text("рџ”„ Restart Stack\nвЏі Р—Р°РїСѓСЃРєР°СЋ... (timeout 240s)")
    except Exception:
        pass
    await _restart_stack_report(msg)
    _stop()


def install(app_obj, cmd_panel=None, build_panel_keyboard=None) -> bool:
    global _CMD_PANEL, _BUILD_PANEL_KEYBOARD
    _CMD_PANEL = cmd_panel
    _BUILD_PANEL_KEYBOARD = build_panel_keyboard

    ok = True

    if CommandHandler is not None:
        try:
            app_obj.add_handler(CommandHandler("restart", _cmd_restart), group=-10)
        except Exception:
            ok = False

    if CallbackQueryHandler is None:
        return False

    try:
        app_obj.add_handler(CallbackQueryHandler(_router, pattern=_PATTERN), group=-10)
        return ok
    except TypeError:
        try:
            app_obj.add_handler(CallbackQueryHandler(_router, pattern=_PATTERN))
            return ok
        except Exception:
            return False
    except Exception:
        return False
