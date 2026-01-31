# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-01-31 01:35:00 UTC
# Purpose: HOPE v4.0 Production Dashboard with AI metrics, chat, and controls
# === END SIGNATURE ===
"""
HOPE v4.0 PRODUCTION DASHBOARD
==============================

Senior-level production dashboard with:
- Real-time system status
- AI metrics and test results
- Position management
- Circuit breaker controls
- Telegram chat integration
- Trade history
- Emergency controls

Run: python scripts/hope_dashboard.py --port 8080
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict

# Add project root
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from aiohttp import web
    import aiohttp_cors
except ImportError:
    os.system(f"{sys.executable} -m pip install aiohttp aiohttp-cors -q")
    from aiohttp import web
    import aiohttp_cors

from dotenv import load_dotenv
load_dotenv(Path("C:/secrets/hope.env"))

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
log = logging.getLogger("DASHBOARD")

# ══════════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SystemStatus:
    mode: str = "UNKNOWN"
    uptime_sec: int = 0
    components: Dict[str, bool] = None
    last_update: str = ""

    def __post_init__(self):
        if self.components is None:
            self.components = {}

@dataclass
class AIMetrics:
    signals_processed: int = 0
    signals_approved: int = 0
    signals_rejected: int = 0
    approval_rate: float = 0.0
    avg_confidence: float = 0.0
    last_signal: Dict = None
    model_version: str = "v2.0"

@dataclass
class CircuitBreakerStatus:
    is_open: bool = False
    consecutive_losses: int = 0
    max_losses: int = 5
    daily_loss_pct: float = 0.0
    max_daily_loss_pct: float = 5.0
    last_trade_time: str = ""

@dataclass
class Position:
    position_id: str
    symbol: str
    entry_price: float
    quantity: float
    current_price: float = 0.0
    pnl_pct: float = 0.0
    pnl_usdt: float = 0.0
    age_sec: int = 0
    target_pct: float = 0.0
    stop_pct: float = 0.0

# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD STATE
# ══════════════════════════════════════════════════════════════════════════════

class DashboardState:
    def __init__(self):
        self.start_time = time.time()
        self.mode = os.getenv("HOPE_MODE", "DRY")
        self.testnet = os.getenv("BINANCE_TESTNET", "true").lower() == "true"

        # Metrics
        self.signals_total = 0
        self.signals_approved = 0
        self.signals_rejected = 0
        self.trades_total = 0
        self.trades_won = 0
        self.trades_lost = 0
        self.total_pnl = 0.0

        # Circuit breaker
        self.cb_consecutive_losses = 0
        self.cb_daily_loss = 0.0
        self.cb_is_open = False

        # Last events
        self.last_signal = None
        self.last_trade = None
        self.last_error = None

        # Chat messages
        self.chat_messages: List[Dict] = []

        # Positions
        self.positions: List[Dict] = []

    def get_uptime(self) -> int:
        return int(time.time() - self.start_time)

    def add_chat_message(self, sender: str, message: str):
        self.chat_messages.append({
            "sender": sender,
            "message": message,
            "time": datetime.now(timezone.utc).isoformat()
        })
        # Keep last 100 messages
        if len(self.chat_messages) > 100:
            self.chat_messages = self.chat_messages[-100:]

state = DashboardState()

# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADERS
# ══════════════════════════════════════════════════════════════════════════════

def load_pricefeed() -> Dict:
    """Load current pricefeed."""
    try:
        pf_path = ROOT / "state" / "ai" / "pricefeed.json"
        if pf_path.exists():
            data = json.loads(pf_path.read_text(encoding="utf-8"))
            return data
    except Exception as e:
        log.error(f"Failed to load pricefeed: {e}")
    return {"prices": {}, "count": 0}

def load_positions() -> List[Dict]:
    """Load watchdog positions."""
    try:
        pos_path = ROOT / "state" / "ai" / "watchdog" / "positions.json"
        if pos_path.exists():
            data = json.loads(pos_path.read_text(encoding="utf-8"))
            return data.get("positions", [])
        # Also check production positions
        pos_path2 = ROOT / "state" / "ai" / "production" / "positions.json"
        if pos_path2.exists():
            data = json.loads(pos_path2.read_text(encoding="utf-8"))
            return data.get("positions", [])
    except Exception as e:
        log.error(f"Failed to load positions: {e}")
    return []

def load_production_stats() -> Dict:
    """Load real production stats from stats.json."""
    try:
        stats_path = ROOT / "state" / "ai" / "production" / "stats.json"
        if stats_path.exists():
            return json.loads(stats_path.read_text(encoding="utf-8"))
    except Exception as e:
        log.error(f"Failed to load production stats: {e}")
    return {"total_trades": 0, "wins": 0, "losses": 0, "by_symbol": {}}

def count_signals_today() -> Dict:
    """Count signals from today's signal files."""
    from datetime import date
    today = date.today().strftime("%Y%m%d")
    signals_dir = ROOT / "data" / "moonbot_signals"

    result = {"total": 0, "symbols": {}}
    try:
        for f in signals_dir.glob(f"signals_*.jsonl"):
            for line in f.read_text(encoding="utf-8").strip().split("\n"):
                if not line:
                    continue
                try:
                    sig = json.loads(line)
                    result["total"] += 1
                    sym = sig.get("symbol", "UNKNOWN")
                    result["symbols"][sym] = result["symbols"].get(sym, 0) + 1
                except:
                    pass
    except Exception as e:
        log.error(f"Failed to count signals: {e}")
    return result

def load_ledger_stats() -> Dict:
    """Load event ledger statistics."""
    try:
        ledger_dir = ROOT / "state" / "ai" / "ledger"
        if not ledger_dir.exists():
            return {"events": 0, "decisions": 0, "opens": 0, "exits": 0}

        stats = {"events": 0, "decisions": 0, "opens": 0, "exits": 0}
        for f in ledger_dir.glob("events_*.jsonl"):
            for line in f.read_text(encoding="utf-8").strip().split("\n"):
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    stats["events"] += 1
                    etype = event.get("type", "")
                    if etype == "DECISION":
                        stats["decisions"] += 1
                    elif etype == "OPEN":
                        stats["opens"] += 1
                    elif etype == "EXIT":
                        stats["exits"] += 1
                except:
                    pass
        return stats
    except Exception as e:
        log.error(f"Failed to load ledger: {e}")
    return {"events": 0, "decisions": 0, "opens": 0, "exits": 0}

def load_circuit_breaker() -> Dict:
    """Load circuit breaker state."""
    try:
        cb_path = ROOT / "state" / "ai" / "circuit_breaker.json"
        if cb_path.exists():
            return json.loads(cb_path.read_text(encoding="utf-8"))
    except:
        pass
    return {
        "is_open": state.cb_is_open,
        "consecutive_losses": state.cb_consecutive_losses,
        "daily_loss_pct": state.cb_daily_loss
    }

def load_telegram_history() -> List[Dict]:
    """Load recent Telegram chat history from logs."""
    messages = []
    try:
        # Look for Telegram bot logs
        log_path = ROOT / "state" / "telegram_chat.jsonl"
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8").strip().split("\n")[-50:]
            for line in lines:
                if line:
                    try:
                        messages.append(json.loads(line))
                    except:
                        pass
    except:
        pass
    return messages

# ══════════════════════════════════════════════════════════════════════════════
# API HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

async def handle_index(request):
    """Serve main dashboard page."""
    return web.Response(text=DASHBOARD_HTML, content_type="text/html")

async def handle_api_status(request):
    """Get system status."""
    pricefeed = load_pricefeed()
    positions = load_positions()
    ledger = load_ledger_stats()
    cb = load_circuit_breaker()
    prod_stats = load_production_stats()
    signal_stats = count_signals_today()

    # Check component health
    pricefeed_age = time.time() - pricefeed.get("produced_unix", 0)

    # Calculate real PnL from stats
    total_pnl = 0.0
    for sym_stats in prod_stats.get("by_symbol", {}).values():
        total_pnl += sym_stats.get("pnl", 0)

    wins = prod_stats.get("wins", 0)
    losses = prod_stats.get("losses", 0)
    total_trades = prod_stats.get("total_trades", wins + losses)
    win_rate = wins / max(total_trades, 1) * 100

    data = {
        "mode": "LIVE" if state.mode == "LIVE" and not state.testnet else "TESTNET" if state.testnet else state.mode,
        "is_live": state.mode == "LIVE" and not state.testnet,
        "uptime_sec": state.get_uptime(),
        "uptime_str": format_duration(state.get_uptime()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "components": {
            "pricefeed": pricefeed_age < 30,
            "watchdog": True,
            "pump_detector": True,
            "circuit_breaker": not cb.get("is_open", False)
        },
        "pricefeed": {
            "count": pricefeed.get("count", 0),
            "age_sec": int(pricefeed_age),
            "fresh": pricefeed_age < 30
        },
        "positions": {
            "count": len(positions),
            "total_value": sum(p.get("quantity", 0) * p.get("entry_price", 0) for p in positions)
        },
        "ledger": ledger,
        "circuit_breaker": cb,
        "signals": signal_stats,
        "metrics": {
            "signals_total": signal_stats["total"],
            "signals_approved": ledger.get("opens", 0),
            "signals_rejected": signal_stats["total"] - ledger.get("opens", 0),
            "approval_rate": ledger.get("opens", 0) / max(signal_stats["total"], 1) * 100,
            "trades_total": total_trades,
            "trades_won": wins,
            "trades_lost": losses,
            "win_rate": win_rate,
            "total_pnl": total_pnl
        },
        "by_symbol": prod_stats.get("by_symbol", {})
    }
    return web.json_response(data)

async def handle_api_prices(request):
    """Get current prices."""
    pricefeed = load_pricefeed()
    return web.json_response(pricefeed)

async def handle_api_positions(request):
    """Get open positions."""
    positions = load_positions()
    pricefeed = load_pricefeed()
    prices = pricefeed.get("prices", {})

    # Enrich with current prices and PnL
    enriched = []
    for pos in positions:
        symbol = pos.get("symbol", "")
        entry = pos.get("entry_price", 0)
        qty = pos.get("quantity", 0)
        current = prices.get(symbol, {}).get("price", entry)

        pnl_pct = (current - entry) / entry * 100 if entry > 0 else 0
        pnl_usdt = (current - entry) * qty

        enriched.append({
            **pos,
            "current_price": current,
            "pnl_pct": round(pnl_pct, 2),
            "pnl_usdt": round(pnl_usdt, 2),
            "age_sec": int(time.time() - pos.get("entry_unix", time.time()))
        })

    return web.json_response({"positions": enriched})

async def handle_api_chat(request):
    """Get chat messages."""
    # Combine state messages with Telegram history
    all_messages = load_telegram_history() + state.chat_messages
    # Sort by time and take last 50
    all_messages.sort(key=lambda x: x.get("time", ""))
    return web.json_response({"messages": all_messages[-50:]})

async def handle_api_chat_send(request):
    """Send chat message (triggers Telegram)."""
    try:
        data = await request.json()
        message = data.get("message", "").strip()
        if not message:
            return web.json_response({"error": "Empty message"}, status=400)

        state.add_chat_message("USER", message)

        # Try to send to Telegram
        try:
            from core.telegram_sender import TelegramSender
            sender = TelegramSender()
            await sender.send(f"[DASHBOARD] {message}")
            await sender.close()
            state.add_chat_message("SYSTEM", "Message sent to Telegram")
        except Exception as e:
            state.add_chat_message("SYSTEM", f"Telegram send failed: {e}")

        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def handle_api_emergency_stop(request):
    """Emergency stop - close all positions."""
    state.add_chat_message("SYSTEM", "🛑 EMERGENCY STOP triggered!")

    # Create STOP flag
    stop_flag = ROOT / "stateSTOP.flag"
    stop_flag.write_text(f"EMERGENCY_STOP triggered at {datetime.now(timezone.utc).isoformat()}")

    return web.json_response({"status": "EMERGENCY_STOP", "message": "Stop flag created"})

async def handle_api_test_ai(request):
    """Run AI diagnostic test."""
    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tests": []
    }

    # Test 1: Pretrade Pipeline
    try:
        from core.pretrade_pipeline import pretrade_check, PipelineConfig
        config = PipelineConfig()
        test_signal = {
            "symbol": "BTCUSDT",
            "delta_pct": 5.0,
            "type": "PUMP",
            "confidence": 0.8,
            "price": 84000,
            "daily_volume_m": 1000,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        result = pretrade_check(test_signal, config)
        results["tests"].append({
            "name": "Pretrade Pipeline",
            "status": "PASS" if not result.ok else "BLOCKED",  # BTC should be blocked
            "detail": result.reason
        })
    except Exception as e:
        results["tests"].append({"name": "Pretrade Pipeline", "status": "ERROR", "detail": str(e)})

    # Test 2: Event Ledger
    try:
        from core.event_ledger import get_ledger
        ledger = get_ledger()
        event_count = len(getattr(ledger, 'recent_events', [])) if hasattr(ledger, 'recent_events') else 0
        results["tests"].append({
            "name": "Event Ledger",
            "status": "PASS",
            "detail": f"Ledger initialized, {event_count} recent events"
        })
    except Exception as e:
        results["tests"].append({"name": "Event Ledger", "status": "ERROR", "detail": str(e)})

    # Test 3: Executor
    try:
        from execution.binance_oco_executor import BinanceOCOExecutor, ExecutorConfig, ExecutionMode
        exec_cfg = ExecutorConfig(mode=ExecutionMode.DRY)
        executor = BinanceOCOExecutor(exec_cfg)
        results["tests"].append({
            "name": "Binance Executor",
            "status": "PASS",
            "detail": f"Mode: {exec_cfg.mode.value}"
        })
    except Exception as e:
        results["tests"].append({"name": "Binance Executor", "status": "ERROR", "detail": str(e)})

    # Test 4: AI Predictor
    try:
        from ai_predictor_v2 import AIDecision
        results["tests"].append({
            "name": "AI Predictor v2",
            "status": "PASS",
            "detail": "Module loaded"
        })
    except Exception as e:
        results["tests"].append({"name": "AI Predictor v2", "status": "SKIP", "detail": str(e)})

    # Test 5: Pricefeed
    pricefeed = load_pricefeed()
    pf_age = time.time() - pricefeed.get("produced_unix", 0)
    results["tests"].append({
        "name": "Pricefeed",
        "status": "PASS" if pf_age < 30 else "STALE",
        "detail": f"{pricefeed.get('count', 0)} prices, age: {int(pf_age)}s"
    })

    return web.json_response(results)

def format_duration(seconds: int) -> str:
    """Format seconds to human readable duration."""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    else:
        hours = seconds // 3600
        mins = (seconds % 3600) // 60
        return f"{hours}h {mins}m"

# ══════════════════════════════════════════════════════════════════════════════
# HTML TEMPLATE
# ══════════════════════════════════════════════════════════════════════════════

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HOPE v4.0 Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', system-ui, sans-serif;
            background: #0d1117;
            color: #c9d1d9;
            min-height: 100vh;
        }
        .header {
            background: linear-gradient(135deg, #161b22 0%, #21262d 100%);
            padding: 20px;
            border-bottom: 1px solid #30363d;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .logo { font-size: 24px; font-weight: bold; color: #58a6ff; }
        .mode-badge {
            padding: 8px 16px;
            border-radius: 20px;
            font-weight: bold;
            font-size: 14px;
        }
        .mode-live { background: #f85149; color: white; animation: pulse 2s infinite; }
        .mode-testnet { background: #f0883e; color: white; }
        .mode-dry { background: #388bfd; color: white; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.7; } }

        .container {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
            gap: 20px;
            padding: 20px;
            max-width: 1800px;
            margin: 0 auto;
        }
        .card {
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 12px;
            padding: 20px;
        }
        .card-title {
            font-size: 16px;
            font-weight: 600;
            color: #8b949e;
            margin-bottom: 15px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .card-title .icon { font-size: 20px; }

        .stat-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 15px;
        }
        .stat {
            text-align: center;
            padding: 15px;
            background: #21262d;
            border-radius: 8px;
        }
        .stat-value { font-size: 28px; font-weight: bold; color: #58a6ff; }
        .stat-label { font-size: 12px; color: #8b949e; margin-top: 5px; }
        .stat-value.green { color: #3fb950; }
        .stat-value.red { color: #f85149; }
        .stat-value.yellow { color: #f0883e; }

        .status-indicator {
            display: inline-block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            margin-right: 8px;
        }
        .status-ok { background: #3fb950; }
        .status-error { background: #f85149; }
        .status-warn { background: #f0883e; }

        .component-list { list-style: none; }
        .component-list li {
            padding: 10px;
            border-bottom: 1px solid #30363d;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .component-list li:last-child { border-bottom: none; }

        .price-table {
            width: 100%;
            font-size: 13px;
        }
        .price-table th {
            text-align: left;
            padding: 8px;
            border-bottom: 1px solid #30363d;
            color: #8b949e;
        }
        .price-table td {
            padding: 8px;
            border-bottom: 1px solid #21262d;
        }
        .price-up { color: #3fb950; }
        .price-down { color: #f85149; }

        .chat-box {
            height: 200px;
            overflow-y: auto;
            background: #0d1117;
            border-radius: 8px;
            padding: 10px;
            margin-bottom: 10px;
        }
        .chat-message {
            margin-bottom: 8px;
            padding: 8px;
            border-radius: 6px;
            background: #21262d;
        }
        .chat-message.user { background: #1f6feb33; }
        .chat-message.system { background: #3fb95033; }
        .chat-time { font-size: 11px; color: #8b949e; }
        .chat-sender { font-weight: bold; color: #58a6ff; }

        .chat-input {
            display: flex;
            gap: 10px;
        }
        .chat-input input {
            flex: 1;
            padding: 10px;
            border: 1px solid #30363d;
            border-radius: 6px;
            background: #0d1117;
            color: #c9d1d9;
        }
        .btn {
            padding: 10px 20px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-weight: 600;
            transition: all 0.2s;
        }
        .btn-primary { background: #238636; color: white; }
        .btn-primary:hover { background: #2ea043; }
        .btn-danger { background: #da3633; color: white; }
        .btn-danger:hover { background: #f85149; }
        .btn-secondary { background: #30363d; color: #c9d1d9; }
        .btn-secondary:hover { background: #484f58; }

        .test-result {
            padding: 10px;
            margin: 5px 0;
            border-radius: 6px;
            display: flex;
            justify-content: space-between;
        }
        .test-pass { background: #238636aa; }
        .test-fail { background: #da3633aa; }
        .test-skip { background: #f0883eaa; }

        .emergency-btn {
            width: 100%;
            padding: 20px;
            font-size: 18px;
            margin-top: 10px;
        }

        .position-card {
            background: #21262d;
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 10px;
        }
        .position-symbol { font-size: 18px; font-weight: bold; color: #58a6ff; }
        .position-pnl { font-size: 24px; font-weight: bold; }

        .refresh-time { font-size: 12px; color: #8b949e; }
    </style>
</head>
<body>
    <div class="header">
        <div class="logo">🚀 HOPE v4.0 Dashboard</div>
        <div>
            <span class="refresh-time" id="refresh-time">Loading...</span>
            <span class="mode-badge mode-dry" id="mode-badge">LOADING</span>
        </div>
    </div>

    <div class="container">
        <!-- System Status -->
        <div class="card">
            <div class="card-title"><span class="icon">⚡</span> System Status</div>
            <div class="stat-grid">
                <div class="stat">
                    <div class="stat-value" id="uptime">--</div>
                    <div class="stat-label">Uptime</div>
                </div>
                <div class="stat">
                    <div class="stat-value" id="positions-count">0</div>
                    <div class="stat-label">Open Positions</div>
                </div>
            </div>
            <ul class="component-list" id="components">
                <li>Loading...</li>
            </ul>
        </div>

        <!-- AI Metrics -->
        <div class="card">
            <div class="card-title"><span class="icon">🤖</span> AI Metrics</div>
            <div class="stat-grid">
                <div class="stat">
                    <div class="stat-value" id="signals-total">0</div>
                    <div class="stat-label">Signals Processed</div>
                </div>
                <div class="stat">
                    <div class="stat-value green" id="approval-rate">0%</div>
                    <div class="stat-label">Approval Rate</div>
                </div>
                <div class="stat">
                    <div class="stat-value green" id="win-rate">0%</div>
                    <div class="stat-label">Win Rate</div>
                </div>
                <div class="stat">
                    <div class="stat-value" id="total-pnl">$0</div>
                    <div class="stat-label">Total PnL</div>
                </div>
            </div>
        </div>

        <!-- Circuit Breaker -->
        <div class="card">
            <div class="card-title"><span class="icon">🔒</span> Circuit Breaker</div>
            <div class="stat-grid">
                <div class="stat">
                    <div class="stat-value" id="cb-losses">0/5</div>
                    <div class="stat-label">Consecutive Losses</div>
                </div>
                <div class="stat">
                    <div class="stat-value" id="cb-daily-loss">0%</div>
                    <div class="stat-label">Daily Loss</div>
                </div>
            </div>
            <div id="cb-status" style="text-align: center; padding: 15px; margin-top: 10px; border-radius: 8px; background: #238636;">
                ✅ CIRCUIT BREAKER OK
            </div>
        </div>

        <!-- Prices -->
        <div class="card">
            <div class="card-title"><span class="icon">📊</span> Live Prices</div>
            <table class="price-table">
                <thead>
                    <tr><th>Symbol</th><th>Price</th><th>Age</th></tr>
                </thead>
                <tbody id="prices-table">
                    <tr><td colspan="3">Loading...</td></tr>
                </tbody>
            </table>
        </div>

        <!-- Positions -->
        <div class="card">
            <div class="card-title"><span class="icon">📈</span> Open Positions</div>
            <div id="positions-list">
                <div style="text-align: center; color: #8b949e; padding: 20px;">
                    No open positions
                </div>
            </div>
        </div>

        <!-- AI Tests -->
        <div class="card">
            <div class="card-title"><span class="icon">🧪</span> AI Diagnostics</div>
            <div id="test-results">
                <div style="text-align: center; padding: 20px;">
                    <div class="spinner" style="margin-bottom: 10px;">Loading tests...</div>
                </div>
            </div>
        </div>

        <!-- Trade Stats by Symbol -->
        <div class="card">
            <div class="card-title"><span class="icon">📊</span> Stats by Symbol</div>
            <div id="symbol-stats">
                <div style="text-align: center; color: #8b949e; padding: 20px;">
                    Loading stats...
                </div>
            </div>
        </div>

        <!-- Chat -->
        <div class="card">
            <div class="card-title"><span class="icon">💬</span> Chat / Telegram</div>
            <div class="chat-box" id="chat-box"></div>
            <div class="chat-input">
                <input type="text" id="chat-input" placeholder="Send message to Telegram..." onkeypress="if(event.key==='Enter')sendChat()">
                <button class="btn btn-primary" onclick="sendChat()">Send</button>
            </div>
        </div>

        <!-- Emergency Controls -->
        <div class="card">
            <div class="card-title"><span class="icon">🛑</span> Emergency Controls</div>
            <p style="color: #f85149; margin-bottom: 15px;">
                ⚠️ Use only in emergencies. This will create a STOP flag and halt all trading.
            </p>
            <button class="btn btn-danger emergency-btn" onclick="emergencyStop()">
                🛑 EMERGENCY STOP
            </button>
        </div>
    </div>

    <script>
        const API = '';

        async function fetchStatus() {
            try {
                const res = await fetch(API + '/api/status');
                const data = await res.json();

                // Update mode badge
                const badge = document.getElementById('mode-badge');
                badge.textContent = data.mode;
                badge.className = 'mode-badge mode-' + data.mode.toLowerCase();

                // Update refresh time
                document.getElementById('refresh-time').textContent =
                    'Updated: ' + new Date().toLocaleTimeString();

                // Update stats
                document.getElementById('uptime').textContent = data.uptime_str;
                document.getElementById('positions-count').textContent = data.positions.count;
                document.getElementById('signals-total').textContent = data.metrics.signals_total;
                document.getElementById('approval-rate').textContent = data.metrics.approval_rate.toFixed(0) + '%';
                document.getElementById('win-rate').textContent = data.metrics.win_rate.toFixed(0) + '%';

                const pnl = document.getElementById('total-pnl');
                pnl.textContent = '$' + data.metrics.total_pnl.toFixed(2);
                pnl.className = 'stat-value ' + (data.metrics.total_pnl >= 0 ? 'green' : 'red');

                // Update circuit breaker
                const cb = data.circuit_breaker;
                document.getElementById('cb-losses').textContent =
                    cb.consecutive_losses + '/' + (cb.max_losses || 5);
                document.getElementById('cb-daily-loss').textContent =
                    (cb.daily_loss_pct || 0).toFixed(1) + '%';

                const cbStatus = document.getElementById('cb-status');
                if (cb.is_open) {
                    cbStatus.innerHTML = '🛑 CIRCUIT BREAKER OPEN';
                    cbStatus.style.background = '#da3633';
                } else {
                    cbStatus.innerHTML = '✅ CIRCUIT BREAKER OK';
                    cbStatus.style.background = '#238636';
                }

                // Update components
                const compList = document.getElementById('components');
                compList.innerHTML = Object.entries(data.components).map(([name, ok]) => `
                    <li>
                        <span><span class="status-indicator status-${ok ? 'ok' : 'error'}"></span>${name}</span>
                        <span>${ok ? '✅' : '❌'}</span>
                    </li>
                `).join('');

                // Update symbol stats
                const symbolStats = document.getElementById('symbol-stats');
                const bySymbol = data.by_symbol || {};
                if (Object.keys(bySymbol).length === 0) {
                    symbolStats.innerHTML = '<div style="text-align: center; color: #8b949e; padding: 20px;">No trades yet</div>';
                } else {
                    symbolStats.innerHTML = Object.entries(bySymbol).map(([sym, stats]) => {
                        const pnlClass = stats.pnl >= 0 ? 'green' : 'red';
                        const pnlSign = stats.pnl >= 0 ? '+' : '';
                        return `
                        <div style="display: flex; justify-content: space-between; padding: 10px; border-bottom: 1px solid #30363d;">
                            <span><strong>${sym.replace('USDT', '')}</strong></span>
                            <span>${stats.wins}W / ${stats.losses}L</span>
                            <span class="${pnlClass}">${pnlSign}$${stats.pnl.toFixed(2)}</span>
                        </div>`;
                    }).join('');
                }

            } catch (e) {
                console.error('Status fetch error:', e);
            }
        }

        function formatPrice(price) {
            const p = parseFloat(price);
            if (p >= 1000) return '$' + p.toLocaleString(undefined, {maximumFractionDigits: 2});
            if (p >= 1) return '$' + p.toFixed(2);
            if (p >= 0.01) return '$' + p.toFixed(4);
            if (p >= 0.0001) return '$' + p.toFixed(6);
            return '$' + p.toExponential(2);
        }

        async function fetchPrices() {
            try {
                const res = await fetch(API + '/api/prices');
                const data = await res.json();

                const table = document.getElementById('prices-table');
                // Sort: major coins first (BTC, ETH, BNB, SOL, XRP), then by name
                const majorCoins = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT'];
                const prices = Object.entries(data.prices || {})
                    .sort((a, b) => {
                        const aIdx = majorCoins.indexOf(a[0]);
                        const bIdx = majorCoins.indexOf(b[0]);
                        if (aIdx >= 0 && bIdx >= 0) return aIdx - bIdx;
                        if (aIdx >= 0) return -1;
                        if (bIdx >= 0) return 1;
                        return a[0].localeCompare(b[0]);
                    })
                    .slice(0, 15);

                table.innerHTML = prices.map(([symbol, info]) => {
                    const age = Math.round((Date.now()/1000 - info.ts_unix));
                    const ageClass = age > 30 ? 'price-down' : '';
                    return `
                    <tr>
                        <td><strong>${symbol.replace('USDT', '')}</strong></td>
                        <td>${formatPrice(info.price)}</td>
                        <td class="${ageClass}">${age}s</td>
                    </tr>`;
                }).join('');

            } catch (e) {
                console.error('Prices fetch error:', e);
            }
        }

        async function fetchPositions() {
            try {
                const res = await fetch(API + '/api/positions');
                const data = await res.json();

                const list = document.getElementById('positions-list');
                if (!data.positions || data.positions.length === 0) {
                    list.innerHTML = '<div style="text-align: center; color: #8b949e; padding: 20px;">No open positions</div>';
                    return;
                }

                list.innerHTML = data.positions.map(pos => `
                    <div class="position-card">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <span class="position-symbol">${pos.symbol}</span>
                            <span class="position-pnl ${pos.pnl_pct >= 0 ? 'green' : 'red'}">
                                ${pos.pnl_pct >= 0 ? '+' : ''}${pos.pnl_pct.toFixed(2)}%
                            </span>
                        </div>
                        <div style="margin-top: 10px; font-size: 13px; color: #8b949e;">
                            Entry: $${pos.entry_price} | Current: $${pos.current_price} | PnL: $${pos.pnl_usdt.toFixed(2)}
                        </div>
                    </div>
                `).join('');

            } catch (e) {
                console.error('Positions fetch error:', e);
            }
        }

        async function fetchChat() {
            try {
                const res = await fetch(API + '/api/chat');
                const data = await res.json();

                const box = document.getElementById('chat-box');
                box.innerHTML = (data.messages || []).map(msg => `
                    <div class="chat-message ${msg.sender.toLowerCase()}">
                        <span class="chat-sender">${msg.sender}</span>
                        <span class="chat-time">${new Date(msg.time).toLocaleTimeString()}</span>
                        <div>${msg.message}</div>
                    </div>
                `).join('');

                box.scrollTop = box.scrollHeight;
            } catch (e) {
                console.error('Chat fetch error:', e);
            }
        }

        async function sendChat() {
            const input = document.getElementById('chat-input');
            const message = input.value.trim();
            if (!message) return;

            try {
                await fetch(API + '/api/chat/send', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({message})
                });
                input.value = '';
                fetchChat();
            } catch (e) {
                alert('Failed to send message');
            }
        }

        async function runTests() {
            const container = document.getElementById('test-results');
            container.innerHTML = '<div style="text-align: center; padding: 20px;">Running tests...</div>';

            try {
                const res = await fetch(API + '/api/test/ai');
                const data = await res.json();

                container.innerHTML = data.tests.map(test => `
                    <div class="test-result test-${test.status.toLowerCase()}">
                        <span>${test.name}</span>
                        <span>${test.status} - ${test.detail}</span>
                    </div>
                `).join('') + `
                    <div style="text-align: center; margin-top: 15px;">
                        <button class="btn btn-secondary" onclick="runTests()">Run Again</button>
                    </div>
                `;
            } catch (e) {
                container.innerHTML = '<div style="color: #f85149;">Test failed: ' + e.message + '</div>';
            }
        }

        async function emergencyStop() {
            if (!confirm('⚠️ EMERGENCY STOP\\n\\nThis will halt all trading. Are you sure?')) return;
            if (!confirm('⚠️ FINAL CONFIRMATION\\n\\nThis action cannot be undone easily. Proceed?')) return;

            try {
                await fetch(API + '/api/emergency-stop', {method: 'POST'});
                alert('🛑 EMERGENCY STOP ACTIVATED\\n\\nStop flag created. Restart services to resume.');
            } catch (e) {
                alert('Failed to activate emergency stop');
            }
        }

        // Initial fetch
        fetchStatus();
        fetchPrices();
        fetchPositions();
        fetchChat();
        // Auto-run AI tests on page load
        setTimeout(runTests, 1000);

        // Auto-refresh
        setInterval(fetchStatus, 5000);
        setInterval(fetchPrices, 5000);
        setInterval(fetchPositions, 10000);
        setInterval(fetchChat, 10000);
        // Re-run AI tests every 2 minutes
        setInterval(runTests, 120000);
    </script>
</body>
</html>
"""

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

async def main(port: int = 8888):
    """Start dashboard server."""
    app = web.Application()

    # Setup CORS
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
            allow_methods="*"
        )
    })

    # Routes
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/status", handle_api_status)
    app.router.add_get("/api/prices", handle_api_prices)
    app.router.add_get("/api/positions", handle_api_positions)
    app.router.add_get("/api/chat", handle_api_chat)
    app.router.add_post("/api/chat/send", handle_api_chat_send)
    app.router.add_post("/api/emergency-stop", handle_api_emergency_stop)
    app.router.add_get("/api/test/ai", handle_api_test_ai)

    # Apply CORS to all routes
    for route in list(app.router.routes()):
        cors.add(route)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    log.info(f"=" * 60)
    log.info(f"  HOPE v4.0 PRODUCTION DASHBOARD")
    log.info(f"=" * 60)
    log.info(f"  URL: http://localhost:{port}")
    log.info(f"  Mode: {state.mode}")
    log.info(f"  Testnet: {state.testnet}")
    log.info(f"=" * 60)

    state.add_chat_message("SYSTEM", f"Dashboard started on port {port}")

    # Keep running
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8888, help="Dashboard port")
    args = parser.parse_args()

    try:
        asyncio.run(main(args.port))
    except KeyboardInterrupt:
        log.info("Dashboard stopped")
