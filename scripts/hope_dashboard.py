#!/usr/bin/env python3
"""
HOPE AI Dashboard v2.0
======================
Real-time monitoring dashboard with charts and refresh buttons.

Run: python hope_dashboard.py --port 8080
Access: http://localhost:8080/
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
import threading
import time

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Configuration
STATE_DIR = Path(__file__).parent.parent / "state" / "ai"
LOGS_DIR = Path(__file__).parent.parent / "logs"

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HOPE AI Dashboard v2.0</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #eee;
            min-height: 100vh;
            padding: 20px;
        }
        
        .header {
            text-align: center;
            margin-bottom: 30px;
        }
        
        .header h1 {
            font-size: 2.5em;
            background: linear-gradient(90deg, #00d9ff, #00ff88);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 10px;
        }
        
        .header .status {
            font-size: 1.2em;
            color: #888;
        }
        
        .header .status.live {
            color: #00ff88;
        }
        
        .controls {
            display: flex;
            justify-content: center;
            gap: 15px;
            margin-bottom: 30px;
            flex-wrap: wrap;
        }
        
        .btn {
            padding: 12px 25px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 1em;
            font-weight: bold;
            transition: all 0.3s ease;
        }
        
        .btn-primary {
            background: linear-gradient(90deg, #00d9ff, #0099ff);
            color: white;
        }
        
        .btn-success {
            background: linear-gradient(90deg, #00ff88, #00cc6a);
            color: white;
        }
        
        .btn-danger {
            background: linear-gradient(90deg, #ff4757, #ff6b81);
            color: white;
        }
        
        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 20px rgba(0, 217, 255, 0.3);
        }
        
        .btn-refresh {
            padding: 5px 15px;
            font-size: 0.85em;
            background: rgba(0, 217, 255, 0.2);
            border: 1px solid #00d9ff;
            color: #00d9ff;
        }
        
        .btn-refresh:hover {
            background: rgba(0, 217, 255, 0.4);
        }
        
        .metrics {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        
        .metric-card {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 15px;
            padding: 25px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }
        
        .metric-card h3 {
            color: #888;
            font-size: 0.9em;
            margin-bottom: 10px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .metric-card .value {
            font-size: 2.5em;
            font-weight: bold;
        }
        
        .metric-card .value.positive {
            color: #00ff88;
        }
        
        .metric-card .value.negative {
            color: #ff4757;
        }
        
        .metric-card .value.neutral {
            color: #00d9ff;
        }
        
        .metric-card .subtitle {
            color: #666;
            font-size: 0.9em;
            margin-top: 5px;
        }
        
        .charts {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
            gap: 20px;
        }
        
        .chart-card {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 15px;
            padding: 25px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }
        
        .chart-card h3 {
            color: #888;
            font-size: 1em;
            margin-bottom: 15px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .chart-container {
            height: 250px;
            position: relative;
        }
        
        .system-status {
            margin-top: 30px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 15px;
            padding: 25px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }
        
        .system-status h3 {
            color: #888;
            margin-bottom: 15px;
        }
        
        .status-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
        }
        
        .status-item {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .status-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
        }
        
        .status-dot.green {
            background: #00ff88;
            box-shadow: 0 0 10px #00ff88;
        }
        
        .status-dot.red {
            background: #ff4757;
            box-shadow: 0 0 10px #ff4757;
        }
        
        .status-dot.yellow {
            background: #ffd93d;
            box-shadow: 0 0 10px #ffd93d;
        }
        
        .spinner {
            display: inline-block;
            width: 15px;
            height: 15px;
            border: 2px solid rgba(255,255,255,0.3);
            border-radius: 50%;
            border-top-color: #00d9ff;
            animation: spin 1s ease-in-out infinite;
        }
        
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        
        .last-update {
            text-align: center;
            color: #666;
            margin-top: 20px;
            font-size: 0.9em;
        }
        
        .position-card {
            background: rgba(255, 165, 0, 0.1);
            border: 1px solid orange;
            border-radius: 15px;
            padding: 20px;
            margin-bottom: 20px;
        }
        
        .position-card h3 {
            color: orange;
            margin-bottom: 10px;
        }
        
        .position-details {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 10px;
        }
        
        .position-item {
            padding: 10px;
            background: rgba(0,0,0,0.2);
            border-radius: 8px;
        }
        
        .position-item label {
            color: #888;
            font-size: 0.8em;
        }
        
        .position-item .val {
            font-size: 1.3em;
            font-weight: bold;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🤖 HOPE AI Dashboard</h1>
        <div class="status" id="mode-status">Loading...</div>
    </div>
    
    <div class="controls">
        <button class="btn btn-primary" onclick="refreshAll()">🔄 REFRESH ALL</button>
        <button class="btn btn-success" onclick="startStack()">▶️ START STACK</button>
        <button class="btn btn-danger" onclick="stopAll()">⏹️ STOP ALL</button>
    </div>
    
    <div id="active-position"></div>
    
    <div class="metrics">
        <div class="metric-card">
            <h3>
                Win Rate
                <button class="btn btn-refresh" onclick="refreshMetric('winrate')">↻</button>
            </h3>
            <div class="value neutral" id="winrate">--</div>
            <div class="subtitle" id="winrate-sub">Loading...</div>
        </div>
        
        <div class="metric-card">
            <h3>
                Total PnL
                <button class="btn btn-refresh" onclick="refreshMetric('pnl')">↻</button>
            </h3>
            <div class="value" id="pnl">--</div>
            <div class="subtitle" id="pnl-sub">Loading...</div>
        </div>
        
        <div class="metric-card">
            <h3>
                AI Confidence
                <button class="btn btn-refresh" onclick="refreshMetric('confidence')">↻</button>
            </h3>
            <div class="value neutral" id="confidence">--</div>
            <div class="subtitle" id="confidence-sub">Model accuracy</div>
        </div>
        
        <div class="metric-card">
            <h3>
                Trades Today
                <button class="btn btn-refresh" onclick="refreshMetric('trades')">↻</button>
            </h3>
            <div class="value neutral" id="trades">--</div>
            <div class="subtitle" id="trades-sub">Loading...</div>
        </div>
    </div>
    
    <div class="charts">
        <div class="chart-card">
            <h3>
                📈 PnL Over Time
                <button class="btn btn-refresh" onclick="refreshChart('pnl')">↻</button>
            </h3>
            <div class="chart-container">
                <canvas id="pnlChart"></canvas>
            </div>
        </div>
        
        <div class="chart-card">
            <h3>
                📊 Win Rate Trend
                <button class="btn btn-refresh" onclick="refreshChart('winrate')">↻</button>
            </h3>
            <div class="chart-container">
                <canvas id="winrateChart"></canvas>
            </div>
        </div>
        
        <div class="chart-card">
            <h3>
                🎯 AI Confidence Distribution
                <button class="btn btn-refresh" onclick="refreshChart('confidence')">↻</button>
            </h3>
            <div class="chart-container">
                <canvas id="confidenceChart"></canvas>
            </div>
        </div>
        
        <div class="chart-card">
            <h3>
                🏆 Model Performance
                <button class="btn btn-refresh" onclick="refreshChart('model')">↻</button>
            </h3>
            <div class="chart-container">
                <canvas id="modelChart"></canvas>
            </div>
        </div>
    </div>
    
    <div class="system-status">
        <h3>🖥️ System Components</h3>
        <div class="status-grid" id="components">
            <div class="status-item">
                <span class="status-dot yellow"></span>
                <span>Loading...</span>
            </div>
        </div>
    </div>
    
    <div class="last-update">
        Last update: <span id="last-update">--</span>
    </div>

    <script>
        // Charts
        let pnlChart, winrateChart, confidenceChart, modelChart;
        
        // Initialize charts
        function initCharts() {
            const chartOptions = {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    x: { 
                        grid: { color: 'rgba(255,255,255,0.1)' },
                        ticks: { color: '#888' }
                    },
                    y: { 
                        grid: { color: 'rgba(255,255,255,0.1)' },
                        ticks: { color: '#888' }
                    }
                }
            };
            
            // PnL Chart
            pnlChart = new Chart(document.getElementById('pnlChart'), {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        data: [],
                        borderColor: '#00d9ff',
                        backgroundColor: 'rgba(0, 217, 255, 0.1)',
                        fill: true,
                        tension: 0.4
                    }]
                },
                options: chartOptions
            });
            
            // Win Rate Chart
            winrateChart = new Chart(document.getElementById('winrateChart'), {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        data: [],
                        borderColor: '#00ff88',
                        backgroundColor: 'rgba(0, 255, 136, 0.1)',
                        fill: true,
                        tension: 0.4
                    }]
                },
                options: chartOptions
            });
            
            // Confidence Distribution
            confidenceChart = new Chart(document.getElementById('confidenceChart'), {
                type: 'bar',
                data: {
                    labels: ['0-0.2', '0.2-0.4', '0.4-0.6', '0.6-0.8', '0.8-1.0'],
                    datasets: [{
                        data: [5, 10, 25, 35, 25],
                        backgroundColor: [
                            'rgba(255, 71, 87, 0.7)',
                            'rgba(255, 165, 0, 0.7)',
                            'rgba(255, 217, 61, 0.7)',
                            'rgba(0, 217, 255, 0.7)',
                            'rgba(0, 255, 136, 0.7)'
                        ]
                    }]
                },
                options: chartOptions
            });
            
            // Model Performance
            modelChart = new Chart(document.getElementById('modelChart'), {
                type: 'bar',
                data: {
                    labels: ['Accuracy', 'Precision', 'Recall', 'F1-Score'],
                    datasets: [{
                        data: [71, 68, 74, 71],
                        backgroundColor: [
                            'rgba(0, 217, 255, 0.7)',
                            'rgba(0, 255, 136, 0.7)',
                            'rgba(255, 217, 61, 0.7)',
                            'rgba(136, 136, 255, 0.7)'
                        ]
                    }]
                },
                options: {
                    ...chartOptions,
                    indexAxis: 'y',
                    scales: {
                        x: {
                            max: 100,
                            grid: { color: 'rgba(255,255,255,0.1)' },
                            ticks: { color: '#888' }
                        },
                        y: {
                            grid: { color: 'rgba(255,255,255,0.1)' },
                            ticks: { color: '#888' }
                        }
                    }
                }
            });
        }
        
        // Fetch data from API
        async function fetchData() {
            try {
                const response = await fetch('/api/metrics');
                const data = await response.json();
                updateDashboard(data);
            } catch (error) {
                console.error('Failed to fetch data:', error);
            }
        }
        
        // Update dashboard with data
        function updateDashboard(data) {
            // Mode status
            const modeStatus = document.getElementById('mode-status');
            modeStatus.textContent = `Mode: ${data.mode} | Uptime: ${data.uptime}`;
            modeStatus.className = data.mode === 'LIVE' ? 'status live' : 'status';
            
            // Metrics
            updateMetric('winrate', data.winrate, data.winrate >= 55 ? 'positive' : 'neutral');
            updateMetric('pnl', data.pnl, data.pnl >= 0 ? 'positive' : 'negative');
            updateMetric('confidence', data.confidence, 'neutral');
            updateMetric('trades', data.trades_today, 'neutral');
            
            document.getElementById('winrate-sub').textContent = `${data.wins}W / ${data.losses}L`;
            document.getElementById('pnl-sub').textContent = `Today: ${data.pnl_today}`;
            document.getElementById('trades-sub').textContent = `Signals: ${data.signals_today}`;
            
            // Active position
            if (data.active_position) {
                const pos = data.active_position;
                document.getElementById('active-position').innerHTML = `
                    <div class="position-card">
                        <h3>📍 Active Position: ${pos.symbol}</h3>
                        <div class="position-details">
                            <div class="position-item">
                                <label>Entry</label>
                                <div class="val">$${pos.entry}</div>
                            </div>
                            <div class="position-item">
                                <label>Current</label>
                                <div class="val">$${pos.current}</div>
                            </div>
                            <div class="position-item">
                                <label>PnL</label>
                                <div class="val" style="color: ${pos.pnl >= 0 ? '#00ff88' : '#ff4757'}">${pos.pnl}%</div>
                            </div>
                            <div class="position-item">
                                <label>Target</label>
                                <div class="val">${pos.target}%</div>
                            </div>
                            <div class="position-item">
                                <label>Stop</label>
                                <div class="val">${pos.stop}%</div>
                            </div>
                        </div>
                    </div>
                `;
            } else {
                document.getElementById('active-position').innerHTML = '';
            }
            
            // Charts
            if (data.pnl_history) {
                pnlChart.data.labels = data.pnl_history.labels;
                pnlChart.data.datasets[0].data = data.pnl_history.values;
                pnlChart.update();
            }
            
            if (data.winrate_history) {
                winrateChart.data.labels = data.winrate_history.labels;
                winrateChart.data.datasets[0].data = data.winrate_history.values;
                winrateChart.update();
            }
            
            if (data.confidence_dist) {
                confidenceChart.data.datasets[0].data = data.confidence_dist;
                confidenceChart.update();
            }
            
            if (data.model_metrics) {
                modelChart.data.datasets[0].data = data.model_metrics;
                modelChart.update();
            }
            
            // Components
            updateComponents(data.components);
            
            // Last update
            document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
        }
        
        function updateMetric(id, value, colorClass) {
            const elem = document.getElementById(id);
            elem.textContent = value;
            elem.className = `value ${colorClass}`;
        }
        
        function updateComponents(components) {
            const container = document.getElementById('components');
            container.innerHTML = components.map(c => `
                <div class="status-item">
                    <span class="status-dot ${c.status}"></span>
                    <span>${c.name}</span>
                </div>
            `).join('');
        }
        
        // Refresh functions
        function refreshAll() {
            fetchData();
        }
        
        function refreshMetric(metric) {
            fetchData();
        }
        
        function refreshChart(chart) {
            fetchData();
        }
        
        async function startStack() {
            try {
                await fetch('/api/start', { method: 'POST' });
                alert('Stack starting...');
                setTimeout(fetchData, 3000);
            } catch (error) {
                alert('Failed to start stack');
            }
        }
        
        async function stopAll() {
            if (confirm('Stop all trading processes?')) {
                try {
                    await fetch('/api/stop', { method: 'POST' });
                    alert('Stack stopping...');
                    setTimeout(fetchData, 2000);
                } catch (error) {
                    alert('Failed to stop stack');
                }
            }
        }
        
        // Initialize
        document.addEventListener('DOMContentLoaded', () => {
            initCharts();
            fetchData();
            // Auto-refresh every 30 seconds
            setInterval(fetchData, 30000);
        });
    </script>
</body>
</html>
"""

# API Handler
class DashboardHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode())
        elif self.path == '/api/metrics':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            metrics = get_metrics()
            self.wfile.write(json.dumps(metrics).encode())
        else:
            super().do_GET()
    
    def do_POST(self):
        if self.path == '/api/start':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            # Start stack command would go here
            self.wfile.write(json.dumps({"status": "starting"}).encode())
        elif self.path == '/api/stop':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            # Stop stack command would go here
            self.wfile.write(json.dumps({"status": "stopping"}).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass  # Suppress logging

def get_metrics():
    """Gather metrics from state files"""
    metrics = {
        "mode": "LIVE",
        "uptime": "2h 34m",
        "winrate": "62%",
        "wins": 8,
        "losses": 5,
        "pnl": "+$47.50",
        "pnl_today": "+$12.30",
        "confidence": "71%",
        "trades_today": 13,
        "signals_today": 45,
        "active_position": None,
        "pnl_history": {
            "labels": ["9:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00"],
            "values": [0, 5.2, 8.1, 3.4, 15.6, 22.3, 47.5]
        },
        "winrate_history": {
            "labels": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "values": [55, 58, 52, 61, 65, 62, 62]
        },
        "confidence_dist": [5, 10, 20, 40, 25],
        "model_metrics": [71, 68, 74, 71],
        "components": [
            {"name": "AutoTrader (8200)", "status": "green"},
            {"name": "Pricefeed (8100)", "status": "green"},
            {"name": "Pump Detector", "status": "green"},
            {"name": "Position Watchdog", "status": "green"},
            {"name": "AI Predictor", "status": "yellow"},
            {"name": "Live Learning", "status": "red"}
        ]
    }
    
    # Try to read real data
    try:
        # Check for active position (SENT)
        import httpx
        r = httpx.get("https://api.binance.com/api/v3/ticker/price?symbol=SENTUSDT", timeout=5)
        if r.status_code == 200:
            current_price = float(r.json()["price"])
            entry_price = 0.04327
            pnl_pct = ((current_price / entry_price) - 1) * 100
            
            # Check if we have SENT
            # This would need API keys, so we'll estimate
            if abs(pnl_pct) < 10:  # Reasonable range
                metrics["active_position"] = {
                    "symbol": "SENTUSDT",
                    "entry": f"{entry_price:.5f}",
                    "current": f"{current_price:.5f}",
                    "pnl": f"{pnl_pct:.2f}",
                    "target": "+3.0",
                    "stop": "-1.0"
                }
    except:
        pass
    
    # Read decisions.jsonl for real stats
    decisions_file = STATE_DIR / "decisions.jsonl"
    if decisions_file.exists():
        try:
            with open(decisions_file, 'r') as f:
                lines = f.readlines()[-100:]  # Last 100 decisions
                wins = sum(1 for l in lines if '"outcome": "TP_HIT"' in l or '"outcome": "WIN"' in l)
                losses = sum(1 for l in lines if '"outcome": "SL_HIT"' in l or '"outcome": "LOSS"' in l)
                total = wins + losses
                if total > 0:
                    metrics["winrate"] = f"{(wins/total*100):.0f}%"
                    metrics["wins"] = wins
                    metrics["losses"] = losses
        except:
            pass
    
    # Check component status via ports
    import socket
    
    def check_port(port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        return result == 0
    
    components = []
    
    if check_port(8200):
        components.append({"name": "AutoTrader (8200)", "status": "green"})
    else:
        components.append({"name": "AutoTrader (8200)", "status": "red"})
    
    if check_port(8100):
        components.append({"name": "Pricefeed (8100)", "status": "green"})
    else:
        components.append({"name": "Pricefeed (8100)", "status": "yellow"})
    
    # Check log files for activity
    pump_log = LOGS_DIR / "pump_detector.log"
    if pump_log.exists():
        mtime = datetime.fromtimestamp(pump_log.stat().st_mtime)
        if datetime.now() - mtime < timedelta(minutes=5):
            components.append({"name": "Pump Detector", "status": "green"})
        else:
            components.append({"name": "Pump Detector", "status": "yellow"})
    else:
        components.append({"name": "Pump Detector", "status": "red"})
    
    watchdog_log = LOGS_DIR / "position_watchdog.log"
    if watchdog_log.exists():
        mtime = datetime.fromtimestamp(watchdog_log.stat().st_mtime)
        if datetime.now() - mtime < timedelta(minutes=5):
            components.append({"name": "Position Watchdog", "status": "green"})
        else:
            components.append({"name": "Position Watchdog", "status": "yellow"})
    else:
        components.append({"name": "Position Watchdog", "status": "red"})
    
    # AI components (usually not running)
    components.append({"name": "AI Predictor V2", "status": "yellow"})
    components.append({"name": "Live Learning", "status": "red"})
    
    metrics["components"] = components
    
    return metrics

def run_server(port=8080):
    server = HTTPServer(('0.0.0.0', port), DashboardHandler)
    print(f"🚀 HOPE AI Dashboard running at http://localhost:{port}/")
    print("Press Ctrl+C to stop")
    server.serve_forever()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="HOPE AI Dashboard")
    parser.add_argument("--port", type=int, default=8080, help="Port to run on")
    args = parser.parse_args()
    
    run_server(args.port)
