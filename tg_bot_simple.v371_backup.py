#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tg_bot_simple.py — HOPEminiBOT v3.7

Команды:
  /start, /status, /panel, /trades, /signals, /balance,
  /diag, /risk_reset, /restart, /whoami, /version, /help
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

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

# ===========================================================================
# КОНФИГУРАЦИЯ
# ===========================================================================

BOT_VERSION = "tgbot-3.7.0"

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

STATE_DIR = ROOT_DIR / "state"
CONFIG_DIR = ROOT_DIR / "config"
TOOLS_DIR = ROOT_DIR / "tools"

HEALTH_FILE = STATE_DIR / "health_v5.json"
SIGNALS_FILE = STATE_DIR / "signals_v5.jsonl"
STOP_FLAG_FILE = STATE_DIR / "STOP.flag"
HUNTERS_TRADES_FILE = STATE_DIR / "hunters_active_trades.json"
RESTART_SCRIPT = TOOLS_DIR / "start_hope_stack_now.ps1"

# Risk state — пробуем несколько вариантов
_RISK_CANDIDATES = [
    STATE_DIR / "risk_manager_state.json",
    STATE_DIR / "risk_state_v1.json",
    STATE_DIR / "risk_state.json",
]
RISK_STATE_FILE = next((p for p in _RISK_CANDIDATES if p.exists()), _RISK_CANDIDATES[0])

# ===========================================================================
# СЕКРЕТЫ
# ===========================================================================

SECRETS_ENV_PATH = Path(r"C:\secrets\hope\.env")
if SECRETS_ENV_PATH.exists():
    load_dotenv(SECRETS_ENV_PATH)
else:
    load_dotenv(ROOT_DIR / ".env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_TOKEN_MINI") or ""
ALLOWED_IDS: Set[int] = {
    int(x) for x in (os.getenv("TELEGRAM_ALLOWED") or "").replace(",", " ").split()
    if x.strip().isdigit()
}
EXCHANGE_SECRETS = {
    "BINANCE_API_KEY": os.getenv("BINANCE_API_KEY") or os.getenv("API_KEY"),
    "BINANCE_API_SECRET": os.getenv("BINANCE_API_SECRET") or os.getenv("API_SECRET"),
}

# ===========================================================================
# ЛОГИРОВАНИЕ
# ===========================================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s [tg_bot] %(levelname)s: %(message)s")
logger = logging.getLogger("tg_bot_simple")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

# ===========================================================================
# EXCHANGE CLIENT
# ===========================================================================

_exchange_client = None

def get_exchange():
    global _exchange_client
    if _exchange_client is not None:
        return _exchange_client
    try:
        from minibot.core.exchange_client import ExchangeClient
        from minibot.core.types import EngineMode
        _exchange_client = ExchangeClient(EngineMode.LIVE, EXCHANGE_SECRETS)
        logger.info("ExchangeClient initialized")
    except Exception as e:
        logger.error(f"Exchange init failed: {e}")
        return None
    return _exchange_client

# ===========================================================================
# HELPERS
# ===========================================================================

def is_allowed(user_id: int) -> bool:
    return not ALLOWED_IDS or user_id in ALLOWED_IDS

async def guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if update.effective_user is None:
        return False
    if not is_allowed(update.effective_user.id):
        if update.effective_message:
            await update.effective_message.reply_text("⛔ Доступ запрещён.")
        return False
    return True

def read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
        return json.loads(text) if text.strip() else None
    except Exception as e:
        logger.error(f"read_json({path}): {e}")
        return None

def format_uptime(seconds: float) -> str:
    try:
        s = int(seconds)
    except Exception:
        return "0s"
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"

def format_ts(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%H:%M") if ts > 0 else "--:--"
    except Exception:
        return "--:--"

def is_stop_active() -> bool:
    return STOP_FLAG_FILE.exists()

def set_stop_flag(active: bool) -> None:
    try:
        if active:
            STOP_FLAG_FILE.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
        elif STOP_FLAG_FILE.exists():
            STOP_FLAG_FILE.unlink()
    except Exception as e:
        logger.error(f"set_stop_flag({active}): {e}")

def build_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        ["/status", "/panel"],
        ["/trades", "/balance"],
        ["/signals", "/diag"],
        ["/help", "/restart"],
    ], resize_keyboard=True)

def build_panel_keyboard() -> InlineKeyboardMarkup:
    stop_text = "▶ STOP OFF" if is_stop_active() else "⏹ STOP ON"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(stop_text, callback_data="stop_toggle")],
        [InlineKeyboardButton("🔄 Restart Stack", callback_data="restart_stack")],
        [InlineKeyboardButton("📊 Refresh", callback_data="refresh_panel")],
    ])

# ===========================================================================
# КОМАНДЫ
# ===========================================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    await update.effective_message.reply_text(
        f"🤖 HOPEminiBOT ({BOT_VERSION})\n\nИспользуй кнопки или /help",
        reply_markup=build_main_keyboard(),
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    text = (
        "🛠 Команды HOPEminiBOT:\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "/start — меню\n"
        "/status — краткий статус\n"
        "/panel — панель + кнопки\n"
        "/trades — сделки HUNTERS\n"
        "/signals — последние сигналы\n"
        "/balance — баланс биржи\n"
        "/diag — диагностика\n"
        "/restart — перезапуск стека\n"
        "/risk_reset — сброс PnL\n"
        "/whoami — твой ID\n"
        "/version — версии"
    )
    await update.effective_message.reply_text(text)

async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    user = update.effective_user
    if not user:
        return
    allowed = "✅" if is_allowed(user.id) else "⛔"
    await update.effective_message.reply_text(
        f"👤 whoami\nID: {user.id}\nUsername: @{user.username or '—'}\nДоступ: {allowed}"
    )

async def cmd_version(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    h = read_json(HEALTH_FILE) or {}
    engine_ver = h.get("engine_version", h.get("version", "unknown"))
    await update.effective_message.reply_text(
        f"🧪 Версии\nБот: {BOT_VERSION}\nЯдро: {engine_ver}\nРежим: {h.get('mode', 'UNKNOWN')}"
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    h = read_json(HEALTH_FILE) or {}
    r = read_json(RISK_STATE_FILE) or {}

    engine_ok = h.get("engine_ok", True)
    mode = h.get("mode", "UNKNOWN")
    uptime = format_uptime(h.get("uptime_sec", 0))
    positions = h.get("open_positions_count", 0)
    pnl = float(r.get("daily_pnl", 0))
    locked = r.get("is_locked", False) or r.get("emergency_stop_active", False)

    icon = "✅" if engine_ok else "⚠️"
    risk_icon = "🔴 LOCKED" if locked else "🟢 OK"
    stop_icon = "⏹ ON" if is_stop_active() else "▶ OFF"

    await update.effective_message.reply_text(
        f"📊 HOPE v5 — статус\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Engine: {icon} | {mode} | {uptime}\n"
        f"Позиции: {positions}\n"
        f"PnL: {pnl:.2f} USDT | {risk_icon}\n"
        f"STOP.flag: {stop_icon}",
        reply_markup=build_main_keyboard(),
    )

async def cmd_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    h = read_json(HEALTH_FILE) or {}
    r = read_json(RISK_STATE_FILE) or {}

    engine_ok = h.get("engine_ok", True)
    mode = h.get("mode", "UNKNOWN")
    uptime = format_uptime(h.get("uptime_sec", 0))
    positions = h.get("open_positions_count", 0)
    queue = h.get("queue_size")
    q_str = "None" if queue is None else str(queue)

    pnl = float(r.get("daily_pnl", 0))
    max_loss = float(r.get("max_daily_loss_usd", 50))
    locked = r.get("is_locked", False) or r.get("emergency_stop_active", False)

    engine_icon = "✅ OK" if engine_ok else "⚠️ PROBLEM"
    risk_icon = "🔴 LOCKED" if locked else "🟢 OK"
    stop_icon = "⏹ ON" if is_stop_active() else "▶ OFF"

    text = (
        f"📊 Панель HOPE v5\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Engine: {engine_icon}\n"
        f"Режим: {mode}\n"
        f"Аптайм: {uptime}\n"
        f"Позиции: {positions}\n"
        f"Очередь: {q_str}\n"
        f"\n🛡 Риск-менеджер\n"
        f"PnL: {pnl:.2f} / -{max_loss:.2f} USDT\n"
        f"Статус: {risk_icon}\n"
        f"STOP.flag: {stop_icon}"
    )
    await update.effective_message.reply_text(text, reply_markup=build_panel_keyboard())

async def cmd_diag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    h = read_json(HEALTH_FILE) or {}
    hb_ts = h.get("last_heartbeat_ts", 0)
    age = int(time.time() - hb_ts) if hb_ts > 0 else None
    last_error = h.get("last_error") or "—"
    age_str = f"{age}s" if age is not None else "N/A"

    await update.effective_message.reply_text(
        f"🔬 Diag HOPE\n"
        f"health_v5.json: {'OK' if h else 'нет данных'}\n"
        f"engine_ok: {h.get('engine_ok', 'N/A')}\n"
        f"last_error: {last_error}\n"
        f"heartbeat_ago: {age_str}"
    )

async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    client = get_exchange()
    if client is None:
        await update.effective_message.reply_text("❌ Exchange не инициализирован.")
        return
    try:
        bal = client.fetch_balance()
        total = getattr(bal, "total_usd", None)
        free = getattr(bal, "free_usd", None)
        if total is not None and free is not None:
            await update.effective_message.reply_text(f"💰 Баланс:\nTotal: {total:.2f} USDT\nFree: {free:.2f} USDT")
        else:
            await update.effective_message.reply_text("💰 Баланс получен, но формат не распознан.")
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Ошибка: {e}")

async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    if not SIGNALS_FILE.exists():
        await update.effective_message.reply_text("📭 Нет сигналов.")
        return
    try:
        lines = SIGNALS_FILE.read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            await update.effective_message.reply_text("📭 Файл сигналов пуст.")
            return
        msg_lines = ["📡 LAST SIGNALS:"]
        for line in reversed(lines[-10:]):
            try:
                s = json.loads(line)
                t_str = format_ts(s.get("ts", 0))
                side = s.get("side", "UNK")
                icon = "🟢" if side.upper() == "LONG" else "🔴"
                symbol = s.get("symbol", "UNKNOWN")
                src = s.get("source", "")
                msg_lines.append(f"{t_str} {icon} {symbol} ({src})")
            except Exception:
                continue
        await update.effective_message.reply_text("\n".join(msg_lines))
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Ошибка: {e}")

async def cmd_risk_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    h = read_json(HEALTH_FILE) or {}
    mode = str(h.get("mode", "")).upper()
    if mode in ("LIVE", "REAL", "PROD"):
        await update.effective_message.reply_text("⛔ /risk_reset заблокирован в LIVE.")
        return
    try:
        state = read_json(RISK_STATE_FILE) or {}
        state.update({"daily_pnl": 0.0, "is_locked": False, "emergency_stop_active": False})
        RISK_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        RISK_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        await update.effective_message.reply_text("🧪 RISK RESET\nPnL: 0.00 USDT")
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Ошибка: {e}")

async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /restart — перезапуск стека."""
    if not await guard(update, context):
        return
    if not RESTART_SCRIPT.exists():
        await update.effective_message.reply_text(f"❌ Скрипт не найден: {RESTART_SCRIPT}")
        return
    await update.effective_message.reply_text("🔄 Перезапускаю стек HOPE...\n(бот отключится на ~5 сек)")
    try:
        subprocess.Popen(
            ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(RESTART_SCRIPT)],
            cwd=str(ROOT_DIR),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Ошибка: {e}")

# ===========================================================================
# /trades — СДЕЛКИ HUNTERS
# ===========================================================================

async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показ сделок HUNTERS с PnL, R-multiple, WinRate и BE."""
    if not await guard(update, context):
        return

    try:
        if not HUNTERS_TRADES_FILE.exists():
            await update.effective_message.reply_text("📭 Файл сделок не найден.")
            return

        raw = HUNTERS_TRADES_FILE.read_text(encoding="utf-8").strip()
        if not raw:
            await update.effective_message.reply_text("📭 Файл сделок пуст.")
            return

        try:
            trades = json.loads(raw)
        except Exception as e:
            await update.effective_message.reply_text(f"❌ Ошибка JSON: {e}")
            return

        if not isinstance(trades, list) or not trades:
            await update.effective_message.reply_text("📭 Список сделок пуст.")
            return

        open_trades: List[Dict[str, Any]] = []
        closed_trades: List[Dict[str, Any]] = []
        total_pnl = 0.0
        wins, losses, be_count = 0, 0, 0

        for t in trades:
            status = str(t.get("status", "")).upper()
            if status == "CLOSED":
                closed_trades.append(t)
                try:
                    pnl = float(t.get("pnl_usd", 0))
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

        closed_trades.sort(key=lambda x: float(x.get("close_ts", 0) or 0), reverse=True)

        lines: List[str] = []
        lines.append("📊 HUNTERS TRADES")
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append(f"Всего: {len(trades)} | Open: {len(open_trades)} | Closed: {len(closed_trades)}")

        if closed_trades:
            total = wins + losses + be_count
            wr = (wins / total * 100) if total > 0 else 0
            lines.append(f"WinRate: {wr:.0f}% (W:{wins} L:{losses} BE:{be_count})")

        pnl_icon = "🤑" if total_pnl >= 0 else "📉"
        lines.append(f"Total PnL: {pnl_icon} {total_pnl:+.2f} USDT")
        lines.append("")

        if open_trades:
            lines.append("🟡 OPEN:")
            for t in open_trades[:5]:
                sym = t.get("symbol", "???")
                side = t.get("side", "???")
                size = t.get("size", t.get("qty", "?"))
                price = t.get("entry_price", t.get("avg_price", "?"))
                t_str = format_ts(t.get("entry_ts", 0))
                profile = t.get("profile", "")
                prof_str = f" [{profile}]" if profile else ""
                lines.append(f"  {t_str} {sym} {side} x{size} @{price}{prof_str}")
            if len(open_trades) > 5:
                lines.append(f"  ... +{len(open_trades) - 5} more")
            lines.append("")

        if closed_trades:
            lines.append("🏁 CLOSED (last 5):")
            for t in closed_trades[:5]:
                sym = t.get("symbol", "???")
                side = t.get("side", "???")
                try:
                    pnl = float(t.get("pnl_usd", 0))
                    pnl_str = f"{pnl:+.2f}"
                except Exception:
                    pnl = 0.0
                    pnl_str = "?"

                r_mult = t.get("r_multiple")
                r_str = f" ({float(r_mult):+.1f}R)" if r_mult is not None else ""

                reason = t.get("close_reason", "EXIT")
                open_str = format_ts(t.get("entry_ts", 0))
                close_str = format_ts(t.get("close_ts", 0))

                icon = "✅" if pnl >= 0 else "🔻"
                lines.append(f"  {icon} {open_str}→{close_str} {sym} {side} {pnl_str}${r_str} ({reason})")

        await update.effective_message.reply_text("\n".join(lines))

    except Exception as e:
        logger.error(f"cmd_trades error: {e}")
        await update.effective_message.reply_text(f"❌ Ошибка /trades: {e}")

# ===========================================================================
# CALLBACKS
# ===========================================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""

    if data == "stop_toggle":
        new_state = not is_stop_active()
        set_stop_flag(new_state)
        status = "⏹ ON" if new_state else "▶ OFF"
        try:
            await query.message.reply_text(f"STOP.flag: {status}", reply_markup=build_panel_keyboard())
        except Exception:
            pass
        return

    if data == "restart_stack":
        if not RESTART_SCRIPT.exists():
            await query.edit_message_text(f"❌ Скрипт не найден: {RESTART_SCRIPT}")
            return
        await query.edit_message_text("🔄 Перезапускаю стек HOPE...")
        try:
            subprocess.Popen(
                ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(RESTART_SCRIPT)],
                cwd=str(ROOT_DIR),
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
        except Exception as e:
            logger.error(f"restart error: {e}")
        return

    if data == "refresh_panel":
        # Отправляем новое сообщение с панелью
        h = read_json(HEALTH_FILE) or {}
        r = read_json(RISK_STATE_FILE) or {}
        engine_ok = h.get("engine_ok", True)
        mode = h.get("mode", "UNKNOWN")
        uptime = format_uptime(h.get("uptime_sec", 0))
        positions = h.get("open_positions_count", 0)
        queue = h.get("queue_size")
        q_str = "None" if queue is None else str(queue)
        pnl = float(r.get("daily_pnl", 0))
        max_loss = float(r.get("max_daily_loss_usd", 50))
        locked = r.get("is_locked", False) or r.get("emergency_stop_active", False)
        engine_icon = "✅ OK" if engine_ok else "⚠️ PROBLEM"
        risk_icon = "🔴 LOCKED" if locked else "🟢 OK"
        stop_icon = "⏹ ON" if is_stop_active() else "▶ OFF"

        text = (
            f"📊 Панель HOPE v5\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Engine: {engine_icon}\n"
            f"Режим: {mode}\n"
            f"Аптайм: {uptime}\n"
            f"Позиции: {positions}\n"
            f"Очередь: {q_str}\n"
            f"\n🛡 Риск-менеджер\n"
            f"PnL: {pnl:.2f} / -{max_loss:.2f} USDT\n"
            f"Статус: {risk_icon}\n"
            f"STOP.flag: {stop_icon}"
        )
        try:
            await query.edit_message_text(text, reply_markup=build_panel_keyboard())
        except Exception:
            await query.message.reply_text(text, reply_markup=build_panel_keyboard())
        return

# ===========================================================================
# МЕНЮ / ИНИЦИАЛИЗАЦИЯ
# ===========================================================================

BOT_COMMANDS: List[BotCommand] = [
    BotCommand("start", "Меню"),
    BotCommand("status", "Статус"),
    BotCommand("panel", "Панель + кнопки"),
    BotCommand("trades", "Сделки HUNTERS"),
    BotCommand("signals", "Сигналы"),
    BotCommand("balance", "Баланс"),
    BotCommand("restart", "Перезапуск стека"),
    BotCommand("diag", "Диагностика"),
    BotCommand("risk_reset", "Сброс PnL"),
    BotCommand("whoami", "Мой ID"),
    BotCommand("version", "Версии"),
    BotCommand("help", "Помощь"),
]

async def post_init(application: Application) -> None:
    logger.info(f"Bot started, ALLOWED_IDS={list(ALLOWED_IDS) or 'ALL'}")
    try:
        await application.bot.set_my_commands(BOT_COMMANDS)
    except Exception as e:
        logger.error(f"set_my_commands error: {e}")
    if ALLOWED_IDS:
        try:
            await application.bot.send_message(chat_id=next(iter(ALLOWED_IDS)), text="🤖 HOPEminiBOT запущен!")
        except Exception as e:
            logger.error(f"Startup ping error: {e}")

def main() -> None:
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN not found")
        raise SystemExit(1)

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
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("restart", cmd_restart))
    app.add_handler(CommandHandler("risk_reset", cmd_risk_reset))
    app.add_handler(CallbackQueryHandler(callback_handler))

    logger.info(f"Starting polling... ({BOT_VERSION})")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
