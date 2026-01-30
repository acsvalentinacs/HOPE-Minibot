# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 12:10:00 UTC
# Purpose: Enhanced Live Trading Dashboard with Process, AllowList, and Chat panels
# === END SIGNATURE ===
"""
Live Trading Dashboard V2 — Enhanced real-time web visualization.

Features:
- Live price charts with signal markers
- AI prediction confidence bars
- P&L tracking curve
- Process Status Panel (NEW)
- AllowList Visualization (NEW)
- Friend Chat Widget (NEW)
- Signal feed

Usage:
    python scripts/live_dashboard_v2.py --port 8080
    # Open http://localhost:8080 in browser
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [DASH] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Check dependencies
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse, JSONResponse
    import uvicorn
    WEB_AVAILABLE = True
except ImportError:
    WEB_AVAILABLE = False
    logger.warning("Install: pip install fastapi uvicorn")

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


# === Dashboard HTML V2 ===

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HOPE AI Trading Dashboard V2</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #eee;
            min-height: 100vh;
        }
        .header {
            background: rgba(0,0,0,0.3);
            padding: 12px 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid #333;
        }
        .header h1 {
            font-size: 20px;
            color: #00ff88;
        }
        .status {
            display: flex;
            gap: 15px;
        }
        .status-item {
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 12px;
        }
        .status-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            animation: pulse 2s infinite;
        }
        .status-dot.green { background: #00ff88; }
        .status-dot.yellow { background: #ffcc00; }
        .status-dot.red { background: #ff4444; }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .container {
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            grid-template-rows: auto auto auto;
            gap: 15px;
            padding: 15px;
            max-width: 1920px;
            margin: 0 auto;
        }
        .panel {
            background: rgba(255,255,255,0.05);
            border-radius: 10px;
            padding: 15px;
            border: 1px solid rgba(255,255,255,0.1);
        }
        .panel-title {
            font-size: 12px;
            color: #888;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 12px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .panel-title .refresh-btn {
            background: rgba(255,255,255,0.1);
            border: none;
            color: #888;
            padding: 4px 8px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 10px;
        }
        .panel-title .refresh-btn:hover { background: rgba(255,255,255,0.2); }
        .chart-container { height: 200px; }
        .wide { grid-column: span 2; }
        .tall { grid-row: span 2; }

        /* Process Status */
        .process-list { max-height: 250px; overflow-y: auto; }
        .process-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 10px;
            margin-bottom: 6px;
            background: rgba(0,0,0,0.2);
            border-radius: 6px;
            font-size: 12px;
        }
        .process-item .name { font-weight: bold; }
        .process-item .status-badge {
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 10px;
        }
        .status-badge.running { background: rgba(0,255,136,0.2); color: #00ff88; }
        .status-badge.stopped { background: rgba(255,255,255,0.1); color: #888; }
        .status-badge.failed { background: rgba(255,68,68,0.2); color: #ff4444; }
        .process-item .uptime { color: #888; font-size: 11px; }
        .process-item .actions button {
            background: rgba(255,255,255,0.1);
            border: none;
            color: #888;
            padding: 4px 8px;
            border-radius: 4px;
            cursor: pointer;
            margin-left: 4px;
            font-size: 10px;
        }
        .process-item .actions button:hover { background: rgba(255,255,255,0.2); }

        /* AllowList */
        .allowlist-layers { display: flex; flex-direction: column; gap: 10px; }
        .allowlist-layer {
            padding: 10px;
            border-radius: 6px;
        }
        .allowlist-layer.core { background: rgba(0,100,255,0.15); border-left: 3px solid #0066ff; }
        .allowlist-layer.dynamic { background: rgba(0,255,136,0.15); border-left: 3px solid #00ff88; }
        .allowlist-layer.hot { background: rgba(255,68,68,0.15); border-left: 3px solid #ff4444; }
        .layer-header {
            display: flex;
            justify-content: space-between;
            font-size: 12px;
            margin-bottom: 8px;
        }
        .layer-symbols {
            display: flex;
            flex-wrap: wrap;
            gap: 4px;
        }
        .symbol-tag {
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 10px;
            background: rgba(255,255,255,0.1);
        }

        /* Chat Widget */
        .chat-messages {
            height: 200px;
            overflow-y: auto;
            margin-bottom: 10px;
            padding: 8px;
            background: rgba(0,0,0,0.2);
            border-radius: 6px;
        }
        .chat-message {
            margin-bottom: 8px;
            padding: 6px 10px;
            border-radius: 6px;
            font-size: 12px;
        }
        .chat-message.claude { background: rgba(138,43,226,0.2); border-left: 3px solid #8a2be2; }
        .chat-message.gpt { background: rgba(0,200,83,0.2); border-left: 3px solid #00c853; }
        .chat-message .sender { font-weight: bold; font-size: 10px; color: #888; }
        .chat-message .text { margin-top: 4px; }
        .chat-input {
            display: flex;
            gap: 8px;
        }
        .chat-input input {
            flex: 1;
            background: rgba(0,0,0,0.3);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 6px;
            padding: 8px 12px;
            color: #fff;
            font-size: 12px;
        }
        .chat-input button {
            background: #00ff88;
            color: #000;
            border: none;
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-weight: bold;
            font-size: 12px;
        }
        .chat-input button:hover { background: #00cc66; }

        /* Signals Feed */
        .signals-feed { max-height: 300px; overflow-y: auto; }
        .signal-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px;
            margin-bottom: 6px;
            background: rgba(0,0,0,0.2);
            border-radius: 6px;
            border-left: 3px solid;
        }
        .signal-item.buy { border-color: #00ff88; }
        .signal-item.skip { border-color: #888; }
        .signal-item.watch { border-color: #ffcc00; }
        .signal-symbol { font-weight: bold; font-size: 14px; }
        .signal-price { color: #888; font-size: 11px; }
        .signal-delta { font-size: 16px; font-weight: bold; }
        .signal-delta.positive { color: #00ff88; }
        .signal-delta.negative { color: #ff4444; }

        /* AI Confidence */
        .confidence-bar {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 10px;
        }
        .confidence-label { width: 70px; font-size: 11px; }
        .confidence-track {
            flex: 1;
            height: 6px;
            background: rgba(255,255,255,0.1);
            border-radius: 3px;
            overflow: hidden;
        }
        .confidence-fill {
            height: 100%;
            border-radius: 3px;
            transition: width 0.3s ease;
        }
        .confidence-fill.high { background: linear-gradient(90deg, #00ff88, #00cc66); }
        .confidence-fill.medium { background: linear-gradient(90deg, #ffcc00, #ff9900); }
        .confidence-fill.low { background: linear-gradient(90deg, #ff4444, #cc0000); }
        .confidence-value { width: 35px; font-size: 11px; text-align: right; }

        /* Metrics */
        .metrics {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 10px;
        }
        .metric {
            text-align: center;
            padding: 12px;
            background: rgba(0,0,0,0.2);
            border-radius: 6px;
        }
        .metric-value {
            font-size: 22px;
            font-weight: bold;
            color: #00ff88;
        }
        .metric-value.negative { color: #ff4444; }
        .metric-label {
            font-size: 10px;
            color: #888;
            margin-top: 4px;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>HOPE AI Trading Dashboard V2</h1>
        <div class="status">
            <div class="status-item">
                <div class="status-dot green" id="ws-status"></div>
                <span>WebSocket</span>
            </div>
            <div class="status-item">
                <div class="status-dot" id="bridge-status"></div>
                <span>Friend Bridge</span>
            </div>
            <div class="status-item">
                <div class="status-dot" id="binance-status"></div>
                <span>Binance</span>
            </div>
        </div>
    </div>

    <div class="container">
        <!-- Price Chart -->
        <div class="panel wide">
            <div class="panel-title">BTC/USDT Price + Signals</div>
            <div class="chart-container">
                <canvas id="priceChart"></canvas>
            </div>
        </div>

        <!-- Process Status -->
        <div class="panel">
            <div class="panel-title">
                Process Status
                <button class="refresh-btn" onclick="loadProcesses()">Refresh</button>
            </div>
            <div class="process-list" id="process-list">
                <div class="process-item">
                    <span class="name">Loading...</span>
                </div>
            </div>
        </div>

        <!-- AllowList Status -->
        <div class="panel">
            <div class="panel-title">
                AllowList (3-Layer)
                <button class="refresh-btn" onclick="loadAllowList()">Refresh</button>
            </div>
            <div class="allowlist-layers" id="allowlist">
                <div class="allowlist-layer core">
                    <div class="layer-header">
                        <span>CORE</span>
                        <span id="core-count">0</span>
                    </div>
                    <div class="layer-symbols" id="core-symbols"></div>
                </div>
                <div class="allowlist-layer dynamic">
                    <div class="layer-header">
                        <span>DYNAMIC</span>
                        <span id="dynamic-count">0</span>
                    </div>
                    <div class="layer-symbols" id="dynamic-symbols"></div>
                </div>
                <div class="allowlist-layer hot">
                    <div class="layer-header">
                        <span>HOT</span>
                        <span id="hot-count">0</span>
                    </div>
                    <div class="layer-symbols" id="hot-symbols"></div>
                </div>
            </div>
        </div>

        <!-- AI Confidence -->
        <div class="panel">
            <div class="panel-title">AI Confidence</div>
            <div class="ai-confidence" id="confidence-bars">
                <div class="confidence-bar">
                    <span class="confidence-label">Regime</span>
                    <div class="confidence-track">
                        <div class="confidence-fill high" id="conf-regime" style="width: 85%"></div>
                    </div>
                    <span class="confidence-value" id="conf-regime-val">85%</span>
                </div>
                <div class="confidence-bar">
                    <span class="confidence-label">Anomaly</span>
                    <div class="confidence-track">
                        <div class="confidence-fill medium" id="conf-anomaly" style="width: 62%"></div>
                    </div>
                    <span class="confidence-value" id="conf-anomaly-val">62%</span>
                </div>
                <div class="confidence-bar">
                    <span class="confidence-label">Pump Prob</span>
                    <div class="confidence-track">
                        <div class="confidence-fill high" id="conf-pump" style="width: 78%"></div>
                    </div>
                    <span class="confidence-value" id="conf-pump-val">78%</span>
                </div>
                <div class="confidence-bar">
                    <span class="confidence-label">Risk Score</span>
                    <div class="confidence-track">
                        <div class="confidence-fill low" id="conf-risk" style="width: 25%"></div>
                    </div>
                    <span class="confidence-value" id="conf-risk-val">25%</span>
                </div>
            </div>
        </div>

        <!-- Friend Chat -->
        <div class="panel">
            <div class="panel-title">
                Friend Chat (Claude / GPT)
                <button class="refresh-btn" onclick="loadChat()">Refresh</button>
            </div>
            <div class="chat-messages" id="chat-messages">
                <div class="chat-message claude">
                    <div class="sender">Claude</div>
                    <div class="text">Analyzing XVS signal...</div>
                </div>
                <div class="chat-message gpt">
                    <div class="sender">GPT</div>
                    <div class="text">Pattern detected: volume spike</div>
                </div>
            </div>
            <div class="chat-input">
                <input type="text" id="chat-input" placeholder="Type message...">
                <button onclick="sendChat()">Send</button>
            </div>
        </div>

        <!-- Signal Feed -->
        <div class="panel">
            <div class="panel-title">Live Signals</div>
            <div class="signals-feed" id="signals-feed">
                <div class="signal-item buy">
                    <div>
                        <div class="signal-symbol">SYN</div>
                        <div class="signal-price">$0.0676</div>
                    </div>
                    <div class="signal-delta positive">+9.51%</div>
                </div>
            </div>
        </div>

        <!-- Performance Metrics -->
        <div class="panel wide">
            <div class="panel-title">Performance</div>
            <div class="metrics">
                <div class="metric">
                    <div class="metric-value" id="total-pnl">+$0.00</div>
                    <div class="metric-label">Total P&L</div>
                </div>
                <div class="metric">
                    <div class="metric-value" id="win-rate">0%</div>
                    <div class="metric-label">Win Rate</div>
                </div>
                <div class="metric">
                    <div class="metric-value" id="trades-count">0</div>
                    <div class="metric-label">Trades</div>
                </div>
                <div class="metric">
                    <div class="metric-value" id="drawdown">0%</div>
                    <div class="metric-label">Max DD</div>
                </div>
            </div>
            <div class="chart-container" style="height: 100px; margin-top: 15px;">
                <canvas id="pnlChart"></canvas>
            </div>
        </div>
    </div>

    <script>
        // === WebSocket connection ===
        let ws;
        let priceData = [];

        function connectWebSocket() {
            ws = new WebSocket(`ws://${window.location.host}/ws`);
            ws.onopen = () => {
                document.getElementById('ws-status').className = 'status-dot green';
            };
            ws.onclose = () => {
                document.getElementById('ws-status').className = 'status-dot red';
                setTimeout(connectWebSocket, 3000);
            };
            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                handleMessage(data);
            };
        }

        function handleMessage(data) {
            if (data.type === 'price') updatePrice(data);
            else if (data.type === 'signal') addSignal(data);
            else if (data.type === 'metrics') updateMetrics(data);
            else if (data.type === 'processes') updateProcesses(data.data);
            else if (data.type === 'allowlist') updateAllowList(data.data);
            else if (data.type === 'chat') addChatMessage(data);
        }

        // === Process Management ===
        async function loadProcesses() {
            try {
                const resp = await fetch('/api/processes');
                const data = await resp.json();
                updateProcesses(data);
            } catch (e) {
                console.error('Failed to load processes:', e);
            }
        }

        function updateProcesses(data) {
            const list = document.getElementById('process-list');
            list.innerHTML = '';

            const procs = data.processes || {};
            for (const [name, proc] of Object.entries(procs)) {
                const statusClass = proc.running ? 'running' : (proc.status === 'failed' ? 'failed' : 'stopped');
                const uptime = proc.uptime || '--:--:--';

                list.innerHTML += `
                    <div class="process-item">
                        <div>
                            <span class="name">${proc.display_name}</span>
                            <span class="uptime">${uptime}</span>
                        </div>
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span class="status-badge ${statusClass}">${statusClass.toUpperCase()}</span>
                            <div class="actions">
                                ${proc.running
                                    ? `<button onclick="controlProcess('${name}', 'stop')">Stop</button>`
                                    : `<button onclick="controlProcess('${name}', 'start')">Start</button>`}
                            </div>
                        </div>
                    </div>
                `;
            }
        }

        async function controlProcess(name, action) {
            try {
                const resp = await fetch(`/api/processes/${name}/${action}`, { method: 'POST' });
                const data = await resp.json();
                setTimeout(loadProcesses, 1000);
            } catch (e) {
                console.error(`Failed to ${action} ${name}:`, e);
            }
        }

        // === AllowList ===
        async function loadAllowList() {
            try {
                const resp = await fetch('/api/allowlist');
                const data = await resp.json();
                updateAllowList(data);
            } catch (e) {
                console.error('Failed to load allowlist:', e);
            }
        }

        function updateAllowList(data) {
            // Core
            document.getElementById('core-count').textContent = (data.core || []).length;
            document.getElementById('core-symbols').innerHTML = (data.core || [])
                .map(s => `<span class="symbol-tag">${s}</span>`).join('');

            // Dynamic
            document.getElementById('dynamic-count').textContent = (data.dynamic || []).length;
            document.getElementById('dynamic-symbols').innerHTML = (data.dynamic || [])
                .map(s => `<span class="symbol-tag">${s}</span>`).join('');

            // Hot
            document.getElementById('hot-count').textContent = (data.hot || []).length;
            document.getElementById('hot-symbols').innerHTML = (data.hot || [])
                .map(s => `<span class="symbol-tag">${s}</span>`).join('');
        }

        // === Chat ===
        async function loadChat() {
            try {
                const resp = await fetch('/api/chat/history');
                const data = await resp.json();
                const msgs = document.getElementById('chat-messages');
                msgs.innerHTML = '';
                for (const msg of (data.messages || [])) {
                    addChatMessage(msg);
                }
            } catch (e) {
                console.error('Failed to load chat:', e);
            }
        }

        function addChatMessage(msg) {
            const msgs = document.getElementById('chat-messages');
            const sender = (msg.from || 'unknown').toLowerCase().includes('claude') ? 'claude' : 'gpt';
            msgs.innerHTML += `
                <div class="chat-message ${sender}">
                    <div class="sender">${msg.from || 'Unknown'}</div>
                    <div class="text">${msg.message || msg.text || ''}</div>
                </div>
            `;
            msgs.scrollTop = msgs.scrollHeight;
        }

        async function sendChat() {
            const input = document.getElementById('chat-input');
            const message = input.value.trim();
            if (!message) return;

            try {
                await fetch('/api/chat/send', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ to: 'gpt', message })
                });
                addChatMessage({ from: 'Me', message });
                input.value = '';
            } catch (e) {
                console.error('Failed to send message:', e);
            }
        }

        // === Signals ===
        function addSignal(data) {
            const feed = document.getElementById('signals-feed');
            const type = data.action || (data.delta > 5 ? 'buy' : data.delta > 2 ? 'watch' : 'skip');
            const item = document.createElement('div');
            item.className = `signal-item ${type}`;
            item.innerHTML = `
                <div>
                    <div class="signal-symbol">${data.symbol}</div>
                    <div class="signal-price">$${(data.price || 0).toFixed(4)}</div>
                </div>
                <div class="signal-delta ${(data.delta || 0) > 0 ? 'positive' : 'negative'}">
                    ${(data.delta || 0) > 0 ? '+' : ''}${(data.delta || 0).toFixed(2)}%
                </div>
            `;
            feed.insertBefore(item, feed.firstChild);
            if (feed.children.length > 20) feed.removeChild(feed.lastChild);
        }

        // === Price Chart ===
        function updatePrice(data) {
            priceData.push({ x: new Date(), y: data.price });
            if (priceData.length > 100) priceData.shift();
            priceChart.update('none');
        }

        function updateMetrics(data) {
            document.getElementById('total-pnl').textContent =
                (data.pnl >= 0 ? '+' : '') + '$' + (data.pnl || 0).toFixed(2);
            document.getElementById('total-pnl').className =
                'metric-value' + ((data.pnl || 0) < 0 ? ' negative' : '');
            document.getElementById('win-rate').textContent = (data.winRate || 0) + '%';
            document.getElementById('trades-count').textContent = data.trades || 0;
            document.getElementById('drawdown').textContent = (data.drawdown || 0) + '%';
        }

        // === Initialize Charts ===
        const priceCtx = document.getElementById('priceChart').getContext('2d');
        const priceChart = new Chart(priceCtx, {
            type: 'line',
            data: {
                datasets: [{
                    label: 'BTC/USDT',
                    data: priceData,
                    borderColor: '#00ff88',
                    backgroundColor: 'rgba(0, 255, 136, 0.1)',
                    fill: true,
                    tension: 0.4,
                    pointRadius: 0,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: { type: 'time', time: { unit: 'minute' }, grid: { color: 'rgba(255,255,255,0.1)' }, ticks: { color: '#888' } },
                    y: { grid: { color: 'rgba(255,255,255,0.1)' }, ticks: { color: '#888' } }
                },
                plugins: { legend: { display: false } }
            }
        });

        const pnlCtx = document.getElementById('pnlChart').getContext('2d');
        const pnlChart = new Chart(pnlCtx, {
            type: 'line',
            data: { labels: [], datasets: [{ label: 'P&L', data: [], borderColor: '#00ff88', backgroundColor: 'rgba(0, 255, 136, 0.2)', fill: true, tension: 0.4 }] },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { display: false }, y: { grid: { color: 'rgba(255,255,255,0.1)' }, ticks: { color: '#888' } } } }
        });

        // === Start ===
        connectWebSocket();
        loadProcesses();
        loadAllowList();
        loadChat();

        // Refresh periodically
        setInterval(loadProcesses, 10000);
        setInterval(loadAllowList, 30000);

        // Demo data
        setInterval(() => {
            const price = 88000 + Math.random() * 1000;
            updatePrice({ price });
        }, 2000);

        // Check Friend Bridge status
        async function checkBridgeStatus() {
            try {
                const resp = await fetch('/api/bridge/health');
                const data = await resp.json();
                document.getElementById('bridge-status').className =
                    'status-dot ' + (data.ok ? 'green' : 'red');
            } catch {
                document.getElementById('bridge-status').className = 'status-dot red';
            }
        }
        setInterval(checkBridgeStatus, 5000);
        checkBridgeStatus();
    </script>
</body>
</html>
"""


# === FastAPI App ===

if WEB_AVAILABLE:
    app = FastAPI(title="HOPE AI Dashboard V2")
    clients: List[WebSocket] = []

    @app.get("/", response_class=HTMLResponse)
    async def get_dashboard():
        return DASHBOARD_HTML

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        clients.append(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            clients.remove(websocket)

    # === API Endpoints ===

    @app.get("/api/processes")
    async def get_processes():
        """Get process status from Process Manager."""
        try:
            from scripts.hope_process_manager import ProcessManager
            manager = ProcessManager()
            return manager.get_status()
        except Exception as e:
            return {"error": str(e), "processes": {}}

    @app.post("/api/processes/{name}/{action}")
    async def control_process(name: str, action: str):
        """Control a process (start/stop/restart)."""
        try:
            from scripts.hope_process_manager import ProcessManager
            manager = ProcessManager()

            if action == "start":
                success, msg = manager.start_process(name)
            elif action == "stop":
                success, msg = manager.stop_process(name)
            elif action == "restart":
                success, msg = manager.restart_process(name)
            else:
                return {"ok": False, "error": f"Unknown action: {action}"}

            return {"ok": success, "message": msg}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.get("/api/allowlist")
    async def get_allowlist():
        """Get AllowList status."""
        try:
            from core.unified_allowlist import get_unified_allowlist
            al = get_unified_allowlist()

            return {
                "core": list(al.core_list.keys()),
                "dynamic": list(al.dynamic_list.keys()),
                "hot": list(al.hot_list.keys()),
                "total": len(al.get_symbols_set()),
            }
        except Exception as e:
            return {"error": str(e), "core": [], "dynamic": [], "hot": []}

    @app.get("/api/bridge/health")
    async def get_bridge_health():
        """Check Friend Bridge health."""
        try:
            if HTTPX_AVAILABLE:
                async with httpx.AsyncClient() as client:
                    resp = await client.get("http://localhost:8765/healthz", timeout=5.0)
                    return resp.json()
            return {"ok": False, "error": "httpx not available"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.get("/api/chat/history")
    async def get_chat_history():
        """Get chat history from Friend Bridge."""
        try:
            if HTTPX_AVAILABLE:
                async with httpx.AsyncClient() as client:
                    resp = await client.get("http://localhost:8765/inbox/claude?limit=20", timeout=5.0)
                    data = resp.json()
                    messages = []
                    for msg in data.get("messages", []):
                        messages.append({
                            "from": msg.get("from", "Unknown"),
                            "message": msg.get("payload", {}).get("message", "") if isinstance(msg.get("payload"), dict) else str(msg.get("payload", "")),
                        })
                    return {"messages": messages}
            return {"messages": []}
        except Exception as e:
            return {"messages": [], "error": str(e)}

    @app.post("/api/chat/send")
    async def send_chat(data: dict):
        """Send chat message via Friend Bridge."""
        try:
            if HTTPX_AVAILABLE:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        "http://localhost:8765/send",
                        json={"to": data.get("to", "gpt"), "message": data.get("message", "")},
                        timeout=5.0
                    )
                    return resp.json()
            return {"ok": False, "error": "httpx not available"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def broadcast(message: dict):
        """Broadcast message to all connected clients."""
        for client in clients:
            try:
                await client.send_json(message)
            except:
                pass


async def main():
    parser = argparse.ArgumentParser(description="Live Trading Dashboard V2")
    parser.add_argument("--port", type=int, default=8080, help="Port to run on")
    args = parser.parse_args()

    if not WEB_AVAILABLE:
        logger.error("FastAPI not installed! Run: pip install fastapi uvicorn")
        return

    logger.info(f"Starting Dashboard V2 on http://localhost:{args.port}")

    config = uvicorn.Config(app, host="0.0.0.0", port=args.port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
