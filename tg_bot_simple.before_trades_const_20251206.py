#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

"""
tg_bot_simple.py — HOPEminiBOT v5 (handlers + risk + restart button)

Команды:
  /start       — приветствие + клавиатура
  /help        — список команд
  /version     — версии бота/ядра
  /whoami      — информация об аккаунте
  /balance     — баланс Binance (total)
  /status      — краткий статус ядра v5
  /panel       — подробная панель + кнопка Restart
  /diag        — расширенная диагностика
  /risk_reset  — сброс дневного PnL (только не-LIVE)
  /restart     — перезапуск стека HOPE
"""

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple, List

import yaml
from dotenv import load_dotenv
from telegram import (
    Update,
    BotCommand,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
    # noinspection PyUnresolvedReferences
from telegram.error import InvalidToken
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from minibot.core.exchange_client import ExchangeClient
from minibot.core.types import EngineMode

BOT_VERSION = "tgbot-3.0.0-restart-button"

ROOT_DIR = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT_DIR / "state"
CONFIG_DIR = ROOT_DIR / "config"
TOOLS_DIR = ROOT_DIR / "tools"

HEALTH_FILE = STATE_DIR / "health_v5.json"
RISK_STATE_FILE = STATE_DIR / "risk_manager_state.json"
RISK_CFG_FILE = CONFIG_DIR / "risk_v5.yaml"
STOP_FLAG_FILE = STATE_DIR / "STOP.flag"
RESTART_SCRIPT = TOOLS_DIR / "start_hope_stack_now.ps1"

SECRETS_ENV_PATH = Path(r"C:\secrets\hope\.env")
if SECRETS_ENV_PATH.exists():
    load_dotenv(SECRETS_ENV_PATH)
else:
    load_dotenv(ROOT_DIR / ".env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_TOKEN_MINI") or ""

_allowed_raw = (os.getenv("TELEGRAM_ALLOWED") or "").replace(",", " ").split()
ALLOWED_IDS: Set[int] = {int(x) for x in _allowed_raw if x.strip().isdigit()}

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY") or os.getenv("API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET") or os.getenv("API_SECRET")

EXCHANGE_SECRETS: Dict[str, Optional[str]] = {
    "BINANCE_API_KEY": BINANCE_API_KEY,
    "BINANCE_API_SECRET": BINANCE_API_SECRET,
}


class TelegramTokenFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = str(record.getMessage())
        token = TELEGRAM_TOKEN or ""
        if token and token in msg:
            msg = msg.replace(token, "***MASKED***")
        msg = re.sub(r"(https://api\.telegram\.org/bot\d+):[A-Za-z0-9_-]+", r"\1:***MASKED***", msg)
        record.msg = msg
        record.args = ()
        return True


logging.basicConfig(level=logging.INFO, format="%(asctime)s [tg_bot] %(levelname)s: %(message)s")
logger = logging.getLogger("tg_bot_simple")

for h in logging.getLogger().handlers:
    h.addFilter(TelegramTokenFilter())

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

exchange_client: Optional[ExchangeClient] = None


def init_exchange_client() -> Optional[ExchangeClient]:
    global exchange_client
    if exchange_client is not None:
        return exchange_client
    try:
        exchange_client = ExchangeClient(EngineMode.LIVE, EXCHANGE_SECRETS)
        logger.info("ExchangeClient initialized")
    except Exception as e:
        logger.error(f"Failed to init ExchangeClient: {e}")
        exchange_client = None
    return exchange_client


def is_allowed(user_id: int) -> bool:
    return not ALLOWED_IDS or user_id in ALLOWED_IDS


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
        return json.loads(text) if text.strip() else None
    except Exception as e:
        logger.error(f"Failed to read {path}: {e}")
        return None


def read_yaml(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Failed to read {path}: {e}")
        return None


def format_uptime(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def build_main_keyboard() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton("/status"), KeyboardButton("/panel")],
        [KeyboardButton("/balance"), KeyboardButton("/restart")],
        [KeyboardButton("/help"), KeyboardButton("/diag")],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def build_panel_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Restart Stack", callback_data="restart_stack")],
        [InlineKeyboardButton("📊 Refresh", callback_data="refresh_panel")],
    ])


async def guard_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:  # noqa: ARG001
    if update.effective_user is None:
        return False
    if not is_allowed(update.effective_user.id):
        if update.effective_message:
            await update.effective_message.reply_text("⛔ Доступ запрещён.")
        return False
    return True


def _load_health() -> Dict[str, Any]:
    return read_json(HEALTH_FILE) or {}


def _load_risk_state() -> Dict[str, Any]:
    return read_json(RISK_STATE_FILE) or {}


def _load_risk_cfg() -> Dict[str, Any]:
    return read_yaml(RISK_CFG_FILE) or {}


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_access(update, context):
        return
    text = (
        "Привет! Я HOPEminiBOT — пульт для ядра v5.\n\n"
        "Команды:\n"
        "/status — краткий статус\n"
        "/panel — панель + кнопка Restart\n"
        "/balance — баланс Binance\n"
        "/restart — перезапуск стека"
    )
    await update.effective_message.reply_text(text, reply_markup=build_main_keyboard())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_access(update, context):
        return
    text = (
        "🧾 Команды HOPEminiBOT:\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "/start — приветствие\n"
        "/status — краткий статус\n"
        "/panel — панель + Restart\n"
        "/diag — диагностика\n"
        "/balance — баланс\n"
        "/restart — перезапуск стека\n"
        "/risk_reset — сброс PnL (DEBUG)\n"
        "/version — версии\n"
        "/whoami — ваш ID"
    )
    await update.effective_message.reply_text(text)


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:  # noqa: ARG001
    u = update.effective_user
    if not u:
        return
    allowed = "✅" if is_allowed(u.id) else "⛔"
    text = f"🧑 ID: <code>{u.id}</code>\nUsername: @{u.username or '—'}\nДоступ: {allowed}"
    await update.effective_message.reply_html(text)


async def cmd_version(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_access(update, context):
        return
    health = _load_health()
    text = (
        f"🧪 Версии\n"
        f"Бот: <b>{BOT_VERSION}</b>\n"
        f"Ядро: <b>{health.get('engine_version', 'v5.x')}</b>\n"
        f"Режим: <b>{health.get('mode', 'UNKNOWN')}</b>"
    )
    await update.effective_message.reply_html(text)


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_access(update, context):
        return
    client = init_exchange_client()
    if client is None:
        await update.effective_message.reply_text("❌ ExchangeClient не инициализирован.")
        return
    try:
        bal = client.fetch_balance()
        if hasattr(bal, "total") and isinstance(bal.total, dict):
            assets = {k: v for k, v in bal.total.items() if v > 0.01}
        elif isinstance(bal, dict) and "total" in bal:
            assets = {k: float(v) for k, v in bal["total"].items() if float(v) > 0.01}
        else:
            assets = {}

        if assets:
            lines = [f"{a}: {v:g}" for a, v in sorted(assets.items(), key=lambda x: -x[1])]
            text = "💰 Баланс:\n━━━━━━━━━━\n" + "\n".join(lines[:15])
        else:
            text = "💰 Баланс: пусто или не распарсился"
    except Exception as e:  # noqa: BLE001
        text = f"❌ Ошибка: {e}"
    await update.effective_message.reply_text(text)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_access(update, context):
        return

    health = _load_health()
    risk = _load_risk_state()
    risk_cfg = _load_risk_cfg()

    engine_ok = health.get("engine_ok", True)
    mode = health.get("mode", "UNKNOWN")
    uptime = format_uptime(health.get("uptime_sec", 0))

    positions = 0
    if isinstance(health.get("open_positions"), list):
        positions = len(health["open_positions"])
    elif isinstance(health.get("open_positions_count"), int):
        positions = health["open_positions_count"]

    pnl = float(risk.get("daily_pnl", 0.0) or 0.0)
    max_loss = float(risk_cfg.get("max_daily_loss_usd", 50.0) or 50.0)
    locked = bool(risk.get("is_locked", False) or risk.get("daily_loss_limit_reached", False))

    stop_flag = bool(health.get("stop_flag", False) or STOP_FLAG_FILE.exists())

    text = (
        "📊 HOPE v5 — статус\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Engine: {'✅' if engine_ok else '⚠️'} | {mode} | {uptime}\n"
        f"Позиции: {positions}\n\n"
        f"🛡 Риск: {pnl:.2f} / -{max_loss:.2f} USDT\n"
        f"Статус: {'🔴 LOCKED' if locked else '🟢 OK'}\n"
        f"STOP.flag: {'⏹ ON' if stop_flag else '▶ OFF'}"
    )
    await update.effective_message.reply_text(text, reply_markup=build_main_keyboard())


async def cmd_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_access(update, context):
        return

    health = _load_health()
    risk = _load_risk_state()
    risk_cfg = _load_risk_cfg()

    engine_ok = health.get("engine_ok", True)
    mode = health.get("mode", "UNKNOWN")
    uptime = format_uptime(health.get("uptime_sec", 0))

    positions = 0
    if isinstance(health.get("open_positions"), list):
        positions = len(health["open_positions"])
    elif isinstance(health.get("open_positions_count"), int):
        positions = health["open_positions_count"]

    queue = health.get("queue_size", "—")
    last_utc = health.get("updated_at_utc", "—")

    pnl = float(risk.get("daily_pnl", 0.0) or 0.0)
    max_loss = float(risk_cfg.get("max_daily_loss_usd", 50.0) or 50.0)
    locked = bool(risk.get("is_locked", False) or risk.get("daily_loss_limit_reached", False))
    reset_hour = int(risk_cfg.get("reset_daily_pnl_utc_hour", 0) or 0)

    stop_flag = bool(health.get("stop_flag", False) or STOP_FLAG_FILE.exists())

    text = (
        "📊 Панель HOPE v5\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Engine: {'✅ OK' if engine_ok else '⚠️ PROBLEM'}\n"
        f"Режим: {mode}\n"
        f"Аптайм: {uptime}\n"
        f"Позиции: {positions}\n"
        f"Очередь: {queue}\n"
        f"Update: {last_utc}\n\n"
        "🛡 Риск-менеджер\n"
        f"PnL: {pnl:.2f} / -{max_loss:.2f} USDT\n"
        f"Статус: {'🔴 LOCKED' if locked else '🟢 OK'}\n"
        f"Reset: {reset_hour:02d}:00 UTC\n\n"
        f"STOP.flag: {'⏹ ON' if stop_flag else '▶ OFF'}"
    )
    await update.effective_message.reply_text(text, reply_markup=build_panel_inline_keyboard())


async def cmd_diag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_access(update, context):
        return

    health = _load_health()
    engine_ok = health.get("engine_ok", True)
    mode = health.get("mode", "UNKNOWN")
    uptime = format_uptime(health.get("uptime_sec", 0))

    positions = 0
    if isinstance(health.get("open_positions"), list):
        positions = len(health["open_positions"])
    elif isinstance(health.get("open_positions_count"), int):
        positions = health["open_positions_count"]

    daily_pnl = float(health.get("daily_pnl_usd", 0.0) or 0.0)
    queue = health.get("queue_size", "None")
    last_ago = int(health.get("last_update_ago_sec", 0) or 0)
    last_utc = health.get("updated_at_utc", "—")
    stop_flag = bool(health.get("stop_flag", False) or STOP_FLAG_FILE.exists())

    text = (
        "🔬 Диагностика v5\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Engine: {'YES' if engine_ok else 'NO'}\n"
        f"Mode: {mode}\n"
        f"Uptime: {uptime}\n"
        f"Positions: {positions}\n"
        f"Daily PnL: {daily_pnl:.2f} USDT\n"
        f"Queue: {queue}\n"
        f"Last update: {last_ago}s ago\n"
        f"UTC: {last_utc}\n\n"
        f"STOP.flag: {'ON' if stop_flag else 'OFF'}\n"
        f"Source: {HEALTH_FILE.name}"
    )
    await update.effective_message.reply_text(text)


async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /restart — перезапуск стека."""
    if not await guard_access(update, context):
        return
    await do_restart_stack(update, is_callback=False)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик inline-кнопок."""
    if not await guard_access(update, context):
        return

    query = update.callback_query
    if not query:
        return

    await query.answer()

    if query.data == "restart_stack":
        await do_restart_stack(update, is_callback=True)
    elif query.data == "refresh_panel":
        await cmd_panel(update, context)


async def do_restart_stack(update: Update, is_callback: bool = False) -> None:
    """Выполняет перезапуск стека HOPE."""
    message = update.callback_query.message if is_callback and update.callback_query else update.effective_message

    if not RESTART_SCRIPT.exists():
        await message.reply_text(f"❌ Скрипт не найден: {RESTART_SCRIPT}")
        return

    await message.reply_text("🔄 Перезапускаю стек HOPE...\n(бот отключится на ~5 сек)")

    try:
        subprocess.Popen(
            ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(RESTART_SCRIPT)],
            cwd=str(ROOT_DIR),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
        logger.info("Restart script launched")
    except Exception as e:  # noqa: BLE001
        await message.reply_text(f"❌ Ошибка запуска: {e}")


async def cmd_risk_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_access(update, context):
        return

    health = _load_health()
    mode = str(health.get("mode", "UNKNOWN")).upper()

    if mode in ("LIVE", "REAL", "PROD"):
        await update.effective_message.reply_text("⛔ /risk_reset заблокирован в LIVE-режиме.")
        return

    now_utc = datetime.now(timezone.utc)
    risk_state = _load_risk_state()
    risk_state.update({
        "daily_pnl": 0.0,
        "daily_trades": 0,
        "daily_wins": 0,
        "daily_losses": 0,
        "daily_loss_limit_reached": False,
        "hunters_daily_pnl": 0.0,
        "last_reset_day": now_utc.day,
        "timestamp": now_utc.isoformat(),
        "emergency_stop_active": False,
    })

    try:
        RISK_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        RISK_STATE_FILE.write_text(json.dumps(risk_state, indent=2), encoding="utf-8")
        text = f"🧪 RISK RESET\nPnL: 0.00 USDT\nРежим: {mode}"
    except Exception as e:  # noqa: BLE001
        text = f"❌ Ошибка: {e}"

    await update.effective_message.reply_text(text)


async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:  # noqa: ARG001
    """
    Показ последних HUNTERS-сделок.

    Берём state/hunters_active_trades.json:
    - открытые сделки
    - последние закрытые сделки
    """
    try:
        if not HUNTERS_TRADES_FILE.exists():
            await update.effective_message.reply_text(
                "Пока нет данных по сделкам HUNTERS (файл hunters_active_trades.json не найден)."
            )
            return

        raw = HUNTERS_TRADES_FILE.read_text(encoding="utf-8").strip()
        if not raw:
            await update.effective_message.reply_text("Файл hunters_active_trades.json пуст.")
            return

        try:
            trades = json.loads(raw)
        except Exception as e:  # noqa: BLE001
            await update.effective_message.reply_text(f"❌ Ошибка парсинга hunters_active_trades.json: {e}")
            return

        if not isinstance(trades, list) or not trades:
            await update.effective_message.reply_text("В hunters_active_trades.json нет сделок.")
            return

        open_trades: list[dict] = []
        closed_trades: list[dict] = []
        for t in trades:
            status = str(t.get("status", "")).upper()
            if status == "CLOSED":
                closed_trades.append(t)
            else:
                open_trades.append(t)

        def _closed_key(t: dict) -> float:
            try:
                return float(t.get("close_ts") or t.get("entry_ts") or 0.0)
            except Exception:
                return 0.0

        closed_trades_sorted = sorted(closed_trades, key=_closed_key, reverse=True)

        lines: list[str] = []
        lines.append("📊 HUNTERS — сделки")
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append(
            f"Всего записей: {len(trades)} | "
            f"Открытых: {len(open_trades)} | Закрытых: {len(closed_trades)}"
        )

        if open_trades:
            lines.append("")
            lines.append("🟡 Открытые позиции:")
            for t in open_trades[:5]:
                symbol = t.get("symbol", "UNKNOWN")
                side = t.get("side", "UNKNOWN")
                size = t.get("size", t.get("qty", '?'))
                entry_price = t.get("entry_price", t.get("avg_price", '?'))
                profile = t.get("profile", t.get("risk_profile", ""))
                suffix = f" [{profile}]" if profile else ""
                lines.append(f"• {symbol} {side} size={size} @ {entry_price}{suffix}")

        if closed_trades_sorted:
            lines.append("")
            lines.append("✅ Последние закрытые (до 5):")
            for t in closed_trades_sorted[:5]:
                symbol = t.get("symbol", "UNKNOWN")
                side = t.get("side", "UNKNOWN")
                pnl = t.get("pnl_usd", 0.0)
                try:
                    pnl_val = float(pnl)
                    pnl_str = f"{pnl_val:+.2f}"
                except Exception:
                    pnl_str = str(pnl)

                reason = t.get("close_reason", "UNKNOWN")
                profile = t.get("profile", t.get("risk_profile", ""))
                profile_suffix = f", {profile}" if profile else ""
                lines.append(
                    f"• {symbol} {side} {pnl_str} USDT ({reason}{profile_suffix})"
                )

        text = "\n".join(lines)
        await update.effective_message.reply_text(text)

    except Exception as e:  # noqa: BLE001
        await update.effective_message.reply_text(f"❌ Ошибка обработки /trades: {e}")
BOT_COMMANDS: List[BotCommand] = [
    BotCommand("start", "Запуск"),
    BotCommand("status", "Статус"),
    BotCommand("panel", "Панель + Restart"),
    BotCommand("diag", "Диагностика"),
    BotCommand("balance", "Баланс"),
    BotCommand("trades", "Сделки HUNTERS"),
    BotCommand("restart", "Перезапуск стека"),
    BotCommand("risk_reset", "Сброс PnL"),
    BotCommand("help", "Помощь"),
    BotCommand("version", "Версии"),
]


async def post_init(application: Application) -> None:
    logger.info(f"Bot started, ALLOWED_IDS={list(ALLOWED_IDS) or 'ALL'}")
    try:
        await application.bot.set_my_commands(BOT_COMMANDS)
    except Exception as e:  # noqa: BLE001
        logger.error(f"set_my_commands error: {e}")

    if ALLOWED_IDS:
        try:
            await application.bot.send_message(
                chat_id=next(iter(ALLOWED_IDS)),
                text="🤖 HOPEminiBOT запущен!",
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"Startup ping error: {e}")


def main() -> None:
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN not found")
        raise SystemExit(1)

    try:
        app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    except InvalidToken:
        logger.error("Invalid Telegram token")
        raise SystemExit(1)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("version", cmd_version))
    app.add_handler(CommandHandler("whoami", cmd_whoami))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("panel", cmd_panel))
    app.add_handler(CommandHandler("diag", cmd_diag))
    app.add_handler(CommandHandler("restart", cmd_restart))
    app.add_handler(CommandHandler("risk_reset", cmd_risk_reset))
    app.add_handler(CommandHandler("trades", cmd_trades))
    app.add_handler(CallbackQueryHandler(callback_handler))

    logger.info("Starting polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()




