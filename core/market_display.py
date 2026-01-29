# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 01:00:00 UTC
# Purpose: Telegram display formatting for HOPE trading bot
# Design: Maximum information density with visual clarity
# === END SIGNATURE ===
"""
Market Display Module — Professional Telegram Formatting

Design Principles:
- Monospace alignment for numbers (easy scanning)
- Visual hierarchy with separators
- Emoji as status indicators, not decoration
- Compact but complete information
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any


# === UNICODE BOX DRAWING ===
LINE_THIN = "─"
LINE_THICK = "━"
CORNER_TL = "┌"
CORNER_TR = "┐"
CORNER_BL = "└"
CORNER_BR = "┘"
T_LEFT = "├"
T_RIGHT = "┤"
VERT = "│"

# === STATUS INDICATORS ===
STATUS_OK = "✅"
STATUS_WARN = "⚠️"
STATUS_FAIL = "❌"
STATUS_RUN = "▶"
STATUS_STOP = "⏹"
STATUS_UP = "📈"
STATUS_DOWN = "📉"
STATUS_FLAT = "➡️"


@dataclass
class MarketTicker:
    """Single asset ticker data."""
    symbol: str
    price: float
    change_pct: float
    volume_usd: float

    @property
    def trend_icon(self) -> str:
        if self.change_pct > 0.5:
            return STATUS_UP
        elif self.change_pct < -0.5:
            return STATUS_DOWN
        return STATUS_FLAT

    @property
    def change_str(self) -> str:
        sign = "+" if self.change_pct >= 0 else ""
        return f"{sign}{self.change_pct:.2f}%"


@dataclass
class GlobalMetrics:
    """Global market metrics."""
    total_mcap_usd: float
    total_volume_24h: float
    btc_dominance: float
    eth_dominance: float
    mcap_change_24h: float


@dataclass
class NewsItem:
    """Single news item."""
    title: str
    source: str
    timestamp: datetime
    impact: str = "neutral"  # bullish, bearish, neutral


def format_usd(value: float, decimals: int = 2) -> str:
    """Format USD value with K/M/B/T suffixes."""
    if value >= 1_000_000_000_000:
        return f"${value/1_000_000_000_000:.2f}T"
    elif value >= 1_000_000_000:
        return f"${value/1_000_000_000:.2f}B"
    elif value >= 1_000_000:
        return f"${value/1_000_000:.2f}M"
    elif value >= 1_000:
        return f"${value/1_000:.1f}K"
    else:
        return f"${value:.{decimals}f}"


def format_price(value: float) -> str:
    """Format price with appropriate decimals."""
    if value >= 10000:
        return f"{value:,.0f}"
    elif value >= 100:
        return f"{value:,.2f}"
    elif value >= 1:
        return f"{value:.4f}"
    else:
        return f"{value:.6f}"


def format_duration(seconds: float) -> str:
    """Format duration compactly."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        m, s = divmod(int(seconds), 60)
        return f"{m}m{s}s" if s else f"{m}m"
    elif seconds < 86400:
        h, rem = divmod(int(seconds), 3600)
        m = rem // 60
        return f"{h}h{m}m" if m else f"{h}h"
    else:
        d, rem = divmod(int(seconds), 86400)
        h = rem // 3600
        return f"{d}d{h}h" if h else f"{d}d"


def build_market_header(tickers: List[MarketTicker]) -> str:
    """
    Build compact market header for panel.

    Example output:
    ┌─────────────────────────────┐
    │ 💹 MARKET                   │
    ├─────────────────────────────┤
    │ BTC   89,111  📉 -0.38%     │
    │ ETH    3,009  📉 -0.46%     │
    │ BNB      904  📈 +0.49%     │
    │ SOL      125  📉 -1.60%     │
    └─────────────────────────────┘
    """
    lines = []
    lines.append("┌" + "─" * 27 + "┐")
    lines.append("│ 💹 MARKET" + " " * 17 + "│")
    lines.append("├" + "─" * 27 + "┤")

    for t in tickers:
        sym = t.symbol.replace("USDT", "")[:4].ljust(4)
        price = format_price(t.price).rjust(7)
        icon = t.trend_icon
        change = t.change_str.rjust(7)
        line = f"│ {sym} {price} {icon}{change}  │"
        lines.append(line)

    lines.append("└" + "─" * 27 + "┘")
    return "\n".join(lines)


def build_global_metrics(metrics: GlobalMetrics) -> str:
    """
    Build global metrics block.

    Example:
    ┌─────────────────────────────┐
    │ 🌍 GLOBAL                   │
    ├─────────────────────────────┤
    │ MCap     $3.10T   -0.30%    │
    │ Vol24h   $116B              │
    │ BTC.D    57.32%             │
    │ ETH.D    11.69%             │
    └─────────────────────────────┘
    """
    lines = []
    lines.append("┌" + "─" * 27 + "┐")
    lines.append("│ 🌍 GLOBAL" + " " * 17 + "│")
    lines.append("├" + "─" * 27 + "┤")

    mcap = format_usd(metrics.total_mcap_usd).rjust(8)
    mcap_ch = f"{metrics.mcap_change_24h:+.2f}%".rjust(8)
    lines.append(f"│ MCap   {mcap} {mcap_ch}  │")

    vol = format_usd(metrics.total_volume_24h).rjust(8)
    lines.append(f"│ Vol24h {vol}" + " " * 10 + "│")

    btc_d = f"{metrics.btc_dominance:.2f}%".rjust(8)
    lines.append(f"│ BTC.D  {btc_d}" + " " * 10 + "│")

    eth_d = f"{metrics.eth_dominance:.2f}%".rjust(8)
    lines.append(f"│ ETH.D  {eth_d}" + " " * 10 + "│")

    lines.append("└" + "─" * 27 + "┘")
    return "\n".join(lines)


def build_engine_status(
    engine_ok: bool,
    mode: str,
    uptime_sec: float,
    hb_ago_sec: float,
    queue_len: int,
    stop_flag: bool,
    pin_live: bool,
) -> str:
    """
    Build engine status block.

    Example:
    ┌─────────────────────────────┐
    │ ⚙️ ENGINE                   │
    ├─────────────────────────────┤
    │ Status   ✅ RUNNING         │
    │ Mode     DRY                │
    │ Uptime   2h15m              │
    │ HB ago   3s                 │
    │ Queue    0                  │
    │ STOP     ▶ OFF              │
    │ PIN      OFF                │
    └─────────────────────────────┘
    """
    lines = []
    lines.append("┌" + "─" * 27 + "┐")
    lines.append("│ ⚙️ ENGINE" + " " * 17 + "│")
    lines.append("├" + "─" * 27 + "┤")

    status = f"{STATUS_OK} RUNNING" if engine_ok else f"{STATUS_FAIL} STOPPED"
    lines.append(f"│ Status  {status.ljust(18)}│")

    lines.append(f"│ Mode    {mode.ljust(18)}│")

    up = format_duration(uptime_sec).ljust(18)
    lines.append(f"│ Uptime  {up}│")

    hb = format_duration(hb_ago_sec).ljust(18)
    lines.append(f"│ HB ago  {hb}│")

    q = str(queue_len).ljust(18)
    lines.append(f"│ Queue   {q}│")

    stop = f"{STATUS_STOP} ON " if stop_flag else f"{STATUS_RUN} OFF"
    lines.append(f"│ STOP    {stop.ljust(18)}│")

    pin = "ON" if pin_live else "OFF"
    lines.append(f"│ PIN     {pin.ljust(18)}│")

    lines.append("└" + "─" * 27 + "┘")
    return "\n".join(lines)


def build_news_block(news: List[NewsItem], max_items: int = 3) -> str:
    """
    Build news summary block.

    Example:
    ┌─────────────────────────────┐
    │ 📰 NEWS                     │
    ├─────────────────────────────┤
    │ 📈 Fed holds rates steady   │
    │ 📈 Strive buys 334 BTC      │
    │ ➡️ UK bans Coinbase ads     │
    └─────────────────────────────┘
    """
    lines = []
    lines.append("┌" + "─" * 27 + "┐")
    lines.append("│ 📰 NEWS" + " " * 19 + "│")
    lines.append("├" + "─" * 27 + "┤")

    for item in news[:max_items]:
        if item.impact == "bullish":
            icon = STATUS_UP
        elif item.impact == "bearish":
            icon = STATUS_DOWN
        else:
            icon = STATUS_FLAT

        # Truncate title to fit
        title = item.title[:22]
        if len(item.title) > 22:
            title = title[:20] + ".."
        lines.append(f"│ {icon} {title.ljust(23)}│")

    lines.append("└" + "─" * 27 + "┘")
    return "\n".join(lines)


def build_action_report(
    action_name: str,
    return_code: int,
    duration_sec: float,
    stdout_tail: str,
    stderr_tail: Optional[str] = None,
) -> str:
    """
    Build professional action report.

    Example:
    ╔═══════════════════════════════╗
    ║ 📋 ACTION REPORT              ║
    ╠═══════════════════════════════╣
    ║ Action   MORNING              ║
    ║ Result   ✅ SUCCESS (0)       ║
    ║ Duration 2.3s                 ║
    ╠═══════════════════════════════╣
    ║ 📜 OUTPUT                     ║
    ╠═══════════════════════════════╣
    ║ [MORNING] Starting...         ║
    ║ [MORNING] Stack started       ║
    ║ Services: active active       ║
    ╚═══════════════════════════════╝
    """
    lines = []
    w = 31  # content width

    lines.append("╔" + "═" * w + "╗")
    lines.append("║ 📋 ACTION REPORT" + " " * (w - 17) + "║")
    lines.append("╠" + "═" * w + "╣")

    # Action name
    name = action_name.upper()[:20].ljust(20)
    lines.append(f"║ Action   {name} ║")

    # Result
    if return_code == 0:
        result = f"{STATUS_OK} SUCCESS (0)"
    else:
        result = f"{STATUS_FAIL} FAILED ({return_code})"
    lines.append(f"║ Result   {result.ljust(20)}║")

    # Duration
    dur = f"{duration_sec:.1f}s".ljust(20)
    lines.append(f"║ Duration {dur}║")

    # Output section
    lines.append("╠" + "═" * w + "╣")
    lines.append("║ 📜 OUTPUT" + " " * (w - 10) + "║")
    lines.append("╠" + "═" * w + "╣")

    # Parse stdout lines
    for line in stdout_tail.strip().split("\n")[-6:]:
        line = line.strip()[:w - 2]
        lines.append(f"║ {line.ljust(w - 2)}║")

    # Stderr if present
    if stderr_tail and stderr_tail.strip():
        lines.append("╠" + "═" * w + "╣")
        lines.append("║ ⚠️ STDERR" + " " * (w - 10) + "║")
        lines.append("╠" + "═" * w + "╣")
        for line in stderr_tail.strip().split("\n")[-3:]:
            line = line.strip()[:w - 2]
            lines.append(f"║ {line.ljust(w - 2)}║")

    lines.append("╚" + "═" * w + "╝")
    return "\n".join(lines)


def build_full_panel(
    tickers: Optional[List[MarketTicker]] = None,
    metrics: Optional[GlobalMetrics] = None,
    engine_ok: bool = False,
    mode: str = "DRY",
    uptime_sec: float = 0,
    hb_ago_sec: float = 0,
    queue_len: int = 0,
    stop_flag: bool = False,
    pin_live: bool = False,
    news: Optional[List[NewsItem]] = None,
    timestamp: Optional[datetime] = None,
) -> str:
    """
    Build complete trading panel.

    Combines: Market + Global + Engine + News
    """
    blocks = []

    # Header with timestamp
    ts = timestamp or datetime.now(timezone.utc)
    header = f"📊 HOPE TRADING PANEL\n⏰ {ts.strftime('%Y-%m-%d %H:%M')} UTC"
    blocks.append(header)
    blocks.append("")

    # Market tickers (if available)
    if tickers:
        blocks.append(build_market_header(tickers))
        blocks.append("")

    # Global metrics (if available)
    if metrics:
        blocks.append(build_global_metrics(metrics))
        blocks.append("")

    # Engine status (always shown)
    blocks.append(build_engine_status(
        engine_ok=engine_ok,
        mode=mode,
        uptime_sec=uptime_sec,
        hb_ago_sec=hb_ago_sec,
        queue_len=queue_len,
        stop_flag=stop_flag,
        pin_live=pin_live,
    ))

    # News (if available)
    if news:
        blocks.append("")
        blocks.append(build_news_block(news))

    return "\n".join(blocks)


def build_balance_report(
    total_usd: float,
    free_usd: float,
    assets: List[Dict[str, Any]],
    source: str,
    mode: str,
    excluded: Optional[List[str]] = None,
) -> str:
    """
    Build professional balance report.

    Example:
    ╔═══════════════════════════════╗
    ║ 💰 BALANCE REPORT             ║
    ╠═══════════════════════════════╣
    ║ Total    $399,638.11          ║
    ║ Free     $399,638.11          ║
    ║ Source   binance_testnet      ║
    ║ Mode     DRY                  ║
    ╠═══════════════════════════════╣
    ║ 📊 TOP HOLDINGS               ║
    ╠═══════════════════════════════╣
    ║ 1. BTC      $89,128 (1.00)    ║
    ║ 2. WBTC     $88,887 (1.00)    ║
    ║ 3. USDC     $10,000           ║
    ║ 4. TUSD     $10,000           ║
    ║ 5. FDUSD    $10,000           ║
    ╚═══════════════════════════════╝
    """
    lines = []
    w = 31

    lines.append("╔" + "═" * w + "╗")
    lines.append("║ 💰 BALANCE REPORT" + " " * (w - 18) + "║")
    lines.append("╠" + "═" * w + "╣")

    total_str = format_usd(total_usd).ljust(20)
    lines.append(f"║ Total    {total_str}║")

    free_str = format_usd(free_usd).ljust(20)
    lines.append(f"║ Free     {free_str}║")

    src = source[:20].ljust(20)
    lines.append(f"║ Source   {src}║")

    mode_str = mode[:20].ljust(20)
    lines.append(f"║ Mode     {mode_str}║")

    if assets:
        lines.append("╠" + "═" * w + "╣")
        lines.append("║ 📊 TOP HOLDINGS" + " " * (w - 16) + "║")
        lines.append("╠" + "═" * w + "╣")

        for i, asset in enumerate(assets[:5], 1):
            sym = asset.get("symbol", "???")[:6].ljust(6)
            val = format_usd(asset.get("value_usd", 0))
            qty = asset.get("quantity")
            if qty and qty != 1:
                entry = f"{i}. {sym} {val} ({qty:.2f})"
            else:
                entry = f"{i}. {sym} {val}"
            lines.append(f"║ {entry[:w-2].ljust(w-2)}║")

    if excluded:
        excl_str = ", ".join(excluded[:5])
        if len(excluded) > 5:
            excl_str += f"... +{len(excluded)-5}"
        lines.append("╠" + "═" * w + "╣")
        lines.append(f"║ Excl: {excl_str[:w-7].ljust(w-7)}║")

    lines.append("╚" + "═" * w + "╝")
    return "\n".join(lines)
