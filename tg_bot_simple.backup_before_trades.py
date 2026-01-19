#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import os
import sys
import time
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from dotenv import load_dotenv
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand,
    __version__ as TG_VER,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)

# ---------------------------------------------------------------------------
# КОНФИГ
# ---------------------------------------------------------------------------

BOT_VERSION = "tgbot-3.5-super-trades"

# Корень проекта HOPE
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

STATE_DIR = ROOT_DIR / "state"
CONFIG_DIR = ROOT_DIR / "config"

HEALTH_FILE = STATE_DIR / "health_v5.json"

# Risk state — пробуем несколько вариантов имён, выбираем существующий
_RISK_CANDIDATES = [
    STATE_DIR / "risk_state_v1.json",
    STATE_DIR / "risk_state.json",
]
for _p in _RISK_CANDIDATES:
    if _p.exists():
        RISK_STATE_FILE = _p
        break
else:
    RISK_STATE_FILE = _RISK_CANDIDATES[0]

SIGNALS_FILE = STATE_DIR / "signals_v5.jsonl"
STOP_FLAG_FILE = STATE_DIR / "STOP.flag"

# ВАЖНО: файл сделок HUNTERS
HUNTERS_TRADES_FILE = STATE_DIR / "hunters_active_trades.json"

# ---------------------------------------------------------------------------
# ENV / СЕКРЕТЫ
# ---------------------------------------------------------------------------

SECRETS_ENV_PATH = Path(r"C:\secrets\hope\.env")
if SECRETS_ENV_PATH.exists():
    load_dotenv(SECRETS_ENV_PATH)
else:
    load_dotenv(ROOT_DIR / ".env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_TOKEN_MINI")

ALLOWED_IDS: Set[int] = {
    int(x)
    for x in (os.getenv("TELEGRAM_ALLOWED") or "").replace(",", " ").split()
    if x.strip().isdigit()
}

EXCHANGE_SECRETS = {
    "BINANCE_API_KEY": os.getenv("BINANCE_API_KEY"),
    "BINANCE_API_SECRET": os.getenv("BINANCE_API_SECRET"),
}
TESTNET = os.getenv("TESTNET", "False").lower() in {"1", "true", "yes"}

# ---------------------------------------------------------------------------
# ЛОГИРОВАНИЕ
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [tg_bot] %(levelname)s: %(message)s",
)
logger = logging.getLogger("tg_bot_simple")
logging.getLogger("httpx").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# EXCHANGE CLIENT (ленивая инициализация)
# ---------------------------------------------------------------------------

exchange_client = None


def get_exchange():
    global exchange_client
    if exchange_client is not None:
        return exchange_client
    try:
        from minibot.core.exchange_client import ExchangeClient
        from minibot.core.types import EngineMode

        mode = EngineMode.DRY if TESTNET else EngineMode.LIVE
        exchange_client = ExchangeClient(mode, EXCHANGE_SECRETS)
        logger.info("ExchangeClient инициализирован: %s", mode.value)
    except Exception as e:  # noqa: BLE001
        logger.error("Exchange init failed: %s", e)
        return None
    return exchange_client


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def is_allowed(user_id: int) -> bool:
    return not ALLOWED_IDS or user_id in ALLOWED_IDS


async def guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:  # noqa: ARG001
    user = update.effective_user
    if user is None:
        return False
    if not is_allowed(user.id):
        await update.effective_message.reply_text("⛔ Доступ запрещён.")
        return False
    return True


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return None
        return json.loads(text)
    except Exception as e:  # noqa: BLE001
        logger.error("read_json(%s) error: %s", path, e)
        return None


def format_uptime(uptime_sec: float) -> str:
    try:
        s = int(uptime_sec)
    except Exception:
        return "0s"
    if s < 60:
        return f"{s}s"
    m = s // 60
    if m < 60:
        return f"{m}m {s % 60}s"
    h = m // 60
    m = m % 60
    return f"{h}h {m}m"


def is_stop_active() -> bool:
    return STOP_FLAG_FILE.exists()


def set_stop_flag(active: bool) -> None:
    try:
        if active:
            STOP_FLAG_FILE.write_text(
                datetime.now(timezone.utc).isoformat(),
                encoding="utf-8",
            )
        else:
            if STOP_FLAG_FILE.exists():
                STOP_FLAG_FILE.unlink()
    except Exception as e:  # noqa: BLE001
        logger.error("set_stop_flag(%s) error: %s", active, e)


# ---------------------------------------------------------------------------
# КОМАНДЫ
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    kb = ReplyKeyboardMarkup(
        [
            ["/status", "/panel"],
            ["/balance", "/trades"],
            ["/signals", "/diag"],
            ["/help", "/version"],
        ],
        resize_keyboard=True,
    )
    await update.effective_message.reply_text(
        f"🤖 HOPEminiBOT панель ({BOT_VERSION})",
        reply_markup=kb,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    text = (
        "🛠 Команды HOPEminiBOT:\n"
        "/start — меню\n"
        "/status — краткий статус ядра\n"
        "/panel — подробная панель + STOP\n"
        "/balance — баланс биржи\n"
        "/trades — сделки HUNTERS (open/closed + PnL)\n"
        "/signals — последние сигналы\n"
        "/risk_reset — сброс дневного PnL в риск-менеджере\n"
        "/diag — диагностика health_v5.json\n"
        "/whoami — информация о твоём аккаунте\n"
        "/version — версии бота/ядра\n"
    )
    await update.effective_message.reply_text(text)


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:  # noqa: ARG001
    if not await guard(update, context):
        return
    user = update.effective_user
    if user is None:
        await update.effective_message.reply_text("Не могу определить пользователя.")
        return
    allowed = "✅" if is_allowed(user.id) else "⛔"
    text = (
        "👤 whoami\n"
        f"id: `{user.id}`\n"
        f"username: @{user.username if user.username else '—'}\n"
        f"allowed: {allowed}\n"
    )
    await update.effective_message.reply_text(text, parse_mode="Markdown")


async def cmd_version(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    h = read_json(HEALTH_FILE) or {}
    engine_ver = h.get("engine_version") or h.get("version") or "unknown"
    text = (
        "🧪 Короткая сводка:\n"
        f"Версия бота: {BOT_VERSION}\n"
        f"Версия ядра: {engine_ver}\n"
        f"python-telegram-bot: {TG_VER}\n"
        f"python: {sys.version.split()[0]}\n"
        f"testnet: {TESTNET}\n"
    )
    await update.effective_message.reply_text(text)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    h = read_json(HEALTH_FILE) or {}
    uptime = format_uptime(h.get("uptime_sec", 0.0))
    mode = h.get("mode", "UNKNOWN")
    open_positions = h.get("open_positions_count", 0)
    queue_size = h.get("queue_size", None)
    engine_ok = h.get("engine_ok", True)

    icon = "✅" if engine_ok else "⚠️"
    q_str = "None" if queue_size is None else str(queue_size)

    text = (
        "📊 HOPE v5 — статус\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Engine: {icon} | {mode} | {uptime}\n"
        f"Позиции: {open_positions}\n"
        f"Очередь: {q_str}\n"
        f"STOP.flag: {'⏹ ON' if is_stop_active() else '▶ OFF'}\n"
    )
    await update.effective_message.reply_text(text)


async def cmd_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    h = read_json(HEALTH_FILE) or {}
    r = read_json(RISK_STATE_FILE) or {}

    uptime = format_uptime(h.get("uptime_sec", 0.0))
    mode = h.get("mode", "UNKNOWN")
    engine_ok = h.get("engine_ok", True)
    open_positions = h.get("open_positions_count", 0)
    queue_size = h.get("queue_size", None)
    q_str = "None" if queue_size is None else str(queue_size)

    risk_pnl = r.get("daily_pnl", 0.0)
    risk_limit = r.get("daily_stop_usd", r.get("limit", -50.0))
    risk_locked = r.get("locked", False) or r.get("is_locked", False)

    engine_icon = "✅ OK" if engine_ok else "⚠️ PROBLEM"
    risk_icon = "🔴 LOCKED" if risk_locked else "🟢 OK"
    stop_str = "⏹ ON" if is_stop_active() else "▶ OFF"

    text_lines = [
        "📊 Панель HOPE v5",
        "━━━━━━━━━━━━━━━━━━",
        f"Engine: {engine_icon}",
        f"Режим: {mode}",
        f"Аптайм: {uptime}",
        f"Позиции: {open_positions}",
        f"Очередь: {q_str}",
        "Update: —",
        "🛡 Риск-менеджер",
        f"PnL: {risk_pnl:.2f} / {risk_limit:.2f} USDT",
        f"Статус: {risk_icon}",
        "Reset: 00:00 UTC",
        f"STOP.flag: {stop_str}",
    ]

    # Кнопки: STOP ON/OFF + Restart
    stop_button = InlineKeyboardButton(
        "⏹ STOP ON" if not is_stop_active() else "▶ STOP OFF",
        callback_data="stop_toggle",
    )
    restart_button = InlineKeyboardButton(
        "🔄 Restart stack",
        callback_data="restart_stack",
    )
    kb = InlineKeyboardMarkup(
        [[stop_button], [restart_button]]
    )

    await update.effective_message.reply_text(
        "\n".join(text_lines),
        reply_markup=kb,
    )


async def cmd_diag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    h = read_json(HEALTH_FILE) or {}
    hb_ts = h.get("last_heartbeat_ts")
    if isinstance(hb_ts, (int, float)) and hb_ts > 0:
        age = int(time.time() - hb_ts)
    else:
        age = None
    last_error = h.get("last_error") or "—"
    engine_ok = h.get("engine_ok", True)
    text = (
        "🔬 Diag HOPE\n"
        f"health_v5.json: {'OK' if h else 'нет данных'}\n"
        f"engine_ok: {engine_ok}\n"
        f"last_error: {last_error}\n"
        f"heartbeat_ago: {age if age is not None else 'N/A'}s\n"
    )
    await update.effective_message.reply_text(text)


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    client = get_exchange()
    if client is None:
        await update.effective_message.reply_text("❌ Exchange не инициализирован (проверь API ключи).")
        return
    try:
        bal = client.fetch_balance()
        # Ожидаем объект с полями total_usd / free_usd
        total = getattr(bal, "total_usd", None)
        free = getattr(bal, "free_usd", None)
        if total is None or free is None:
            await update.effective_message.reply_text("ℹ️ Баланс получен, но формат не распознан.")
            return
        text = f"💰 Баланс:\nTotal: {total:.2f} USDT\nFree: {free:.2f} USDT"
        await update.effective_message.reply_text(text)
    except Exception as e:  # noqa: BLE001
        logger.error("balance error: %s", e)
        await update.effective_message.reply_text(f"❌ Ошибка при запросе баланса: {e}")


async def cmd_risk_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    try:
        state = {
            "daily_pnl": 0.0,
            "locked": False,
            "is_locked": False,
        }
        RISK_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        RISK_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        text = "🧪 RISK RESET\nPnL: 0.00 USDT\nСтатус: 🟢 OK"
    except Exception as e:  # noqa: BLE001
        text = f"❌ Ошибка записи risk_state: {e}"
    await update.effective_message.reply_text(text)


async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    if not SIGNALS_FILE.exists():
        await update.effective_message.reply_text("📭 Нет сигналов (signals_v5.jsonl отсутствует).")
        return
    try:
        lines = SIGNALS_FILE.read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            await update.effective_message.reply_text("📭 Файл signals_v5.jsonl пуст.")
            return
        msg_lines: List[str] = ["📡 LAST SIGNALS:"]
        for line in reversed(lines[-10:]):
            try:
                s = json.loads(line)
            except Exception:
                continue
            ts_val = s.get("ts", 0)
            try:
                dt = datetime.fromtimestamp(float(ts_val))
                t_str = dt.strftime("%H:%M")
            except Exception:
                t_str = "??:??"
            side = s.get("side", "UNK")
            icon = "🟢" if side.upper() == "LONG" else "🔴"
            symbol = s.get("symbol", "UNKNOWN")
            src = s.get("source", s.get("src", ""))
            msg_lines.append(f"`{t_str}` {icon} {symbol} ({src})")
        await update.effective_message.reply_text(
            "\n".join(msg_lines),
            parse_mode="Markdown",
        )
    except Exception as e:  # noqa: BLE001
        logger.error("signals error: %s", e)
        await update.effective_message.reply_text(f"❌ Ошибка чтения сигналов: {e}")


# ---------------------------------------------------------------------------
# /trades — СДЕЛКИ HUNTERS
# ---------------------------------------------------------------------------

async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Показ сделок HUNTERS с подсчётом суммарного PnL и кратким списком.
    """
    if not await guard(update, context):
        return

    try:
        if not HUNTERS_TRADES_FILE.exists():
            await update.effective_message.reply_text(
                "📭 Файл сделок (hunters_active_trades.json) не найден."
            )
            return

        raw = HUNTERS_TRADES_FILE.read_text(encoding="utf-8").strip()
        if not raw:
            await update.effective_message.reply_text("📭 Файл сделок пуст.")
            return

        try:
            trades = json.loads(raw)
        except Exception as e:  # noqa: BLE001
            await update.effective_message.reply_text(f"❌ Ошибка JSON: {e}")
            return

        if not isinstance(trades, list) or not trades:
            await update.effective_message.reply_text("📭 Список сделок пуст.")
            return

        open_trades: List[Dict[str, Any]] = []
        closed_trades: List[Dict[str, Any]] = []
        total_realized_pnl = 0.0

        for t in trades:
            status = str(t.get("status", "")).upper()
            if status == "CLOSED":
                closed_trades.append(t)
                try:
                    total_realized_pnl += float(t.get("pnl_usd", 0.0))
                except Exception:
                    pass
            else:
                open_trades.append(t)

        def _closed_key(t: Dict[str, Any]) -> float:
            try:
                return float(t.get("close_ts") or t.get("entry_ts") or 0.0)
            except Exception:
                return 0.0

        closed_trades_sorted = sorted(closed_trades, key=_closed_key, reverse=True)

        pnl_emoji = "🤑" if total_realized_pnl > 0 else "📉"
        lines: List[str] = []
        lines.append("📊 HUNTERS PORTFOLIO")
        lines.append(f"Total PnL: {pnl_emoji} `{total_realized_pnl:+.2f} USDT`")
        lines.append("━━━━━━━━━━━━━━━━━━")

        # Открытые
        if open_trades:
            lines.append(f"🟡 OPEN ({len(open_trades)}):")
            for t in open_trades[:5]:
                symbol = t.get("symbol", "UNKNOWN")
                side = t.get("side", "UNKNOWN")
                size = t.get("size", t.get("qty", "?"))
                entry_price = t.get("entry_price", t.get("avg_price", "?"))
                profile = t.get("profile", t.get("risk_profile", ""))
                suffix = f" [{profile}]" if profile else ""
                lines.append(f"• {symbol} {side} size={size} @ {entry_price}{suffix}")
            if len(open_trades) > 5:
                lines.append(f"... и ещё {len(open_trades) - 5} открытых")
            lines.append("")

        # Закрытые
        if closed_trades_sorted:
            lines.append("🏁 CLOSED (Last 5):")
            for t in closed_trades_sorted[:5]:
                symbol = t.get("symbol", "UNKNOWN")
                side = t.get("side", "UNKNOWN")
                try:
                    pnl_val = float(t.get("pnl_usd", 0.0))
                    pnl_str = f"{pnl_val:+.2f}"
                except Exception:
                    pnl_str = str(t.get("pnl_usd", "?"))
                reason = t.get("close_reason", "EXIT")
                profile = t.get("profile", t.get("risk_profile", ""))
                cts = t.get("close_ts", 0)
                if isinstance(cts, (int, float)) and cts > 0:
                    t_str = datetime.fromtimestamp(cts).strftime("%H:%M")
                else:
                    t_str = "--:--"
                prof_suffix = f", {profile}" if profile else ""
                icon = "✅" if isinstance(pnl_val, float) and pnl_val >= 0 else "🔻"
                lines.append(
                    f"{icon} {t_str} {symbol} {side} `{pnl_str} USDT` ({reason}{prof_suffix})"
                )

        await update.effective_message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown",
        )

    except Exception as e:  # noqa: BLE001
        logger.error("Trades error: %s", e)
        await update.effective_message.reply_text(f"❌ Критическая ошибка /trades: {e}")


# ---------------------------------------------------------------------------
# CALLBACKS (STOP, RESTART)
# ---------------------------------------------------------------------------

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    data = query.data or ""
    if data == "stop_toggle":
        new_state = not is_stop_active()
        set_stop_flag(new_state)
        await query.edit_message_text(
            f"STOP.flag теперь: {'⏹ ON' if new_state else '▶ OFF'}"
        )
        # Дополнительно отправим свежую панель
        await cmd_panel(update, context)
        return

    if data == "restart_stack":
        try:
            script_path = ROOT_DIR / "tools" / "start_hope_stack_now.ps1"
            if not script_path.exists():
                await query.edit_message_text(
                    f"❌ Скрипт не найден: {script_path}"
                )
                return
            subprocess.Popen(
                [
                    "powershell",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script_path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await query.edit_message_text("🔄 Перезапуск HOPE-стека запущен.")
        except Exception as e:  # noqa: BLE001
            await query.edit_message_text(f"❌ Ошибка перезапуска: {e}")
        return


# ---------------------------------------------------------------------------
# МЕНЮ / ИНИЦИАЛИЗАЦИЯ
# ---------------------------------------------------------------------------

BOT_COMMANDS: List[BotCommand] = [
    BotCommand("start", "Меню"),
    BotCommand("status", "Статус"),
    BotCommand("panel", "Панель + STOP/Restart"),
    BotCommand("balance", "Баланс"),
    BotCommand("trades", "Сделки HUNTERS"),
    BotCommand("signals", "Сигналы"),
    BotCommand("risk_reset", "Сброс риска"),
    BotCommand("diag", "Диагностика"),
    BotCommand("whoami", "Кто я"),
    BotCommand("version", "Версии"),
    BotCommand("help", "Помощь"),
]


async def post_init(application: Application) -> None:
    logger.info("Bot started, ALLOWED_IDS=%s", list(ALLOWED_IDS) or "ALL")
    try:
        await application.bot.set_my_commands(BOT_COMMANDS)
    except Exception as e:  # noqa: BLE001
        logger.error("set_my_commands error: %s", e)
    # Стартовый пинг
    if ALLOWED_IDS:
        try:
            chat_id = next(iter(ALLOWED_IDS))
            await application.bot.send_message(
                chat_id=chat_id,
                text="🤖 HOPEminiBOT запущен!",
            )
        except Exception as e:  # noqa: BLE001
            logger.error("Startup ping error: %s", e)


def main() -> None:
    if not TELEGRAM_TOKEN:
        print("❌ TELEGRAM_TOKEN не задан.")
        return

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

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
    app.add_handler(CommandHandler("risk_reset", cmd_risk_reset))
    app.add_handler(CallbackQueryHandler(callback_handler))

    print(f"🤖 HOPEminiBOT Telegram started ({BOT_VERSION})")
    app.run_polling()


if __name__ == "__main__":
    main()
