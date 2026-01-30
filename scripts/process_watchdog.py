# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-01-30 18:30:00 UTC
# Modified by: Claude (opus-4.5)
# Modified at: 2026-01-30 19:35:00 UTC
# Purpose: Process Watchdog with auto-restart, HTTP API and Telegram control
# Changes: FIX 3 - Enabled autotrader (TESTNET mode)
# Version: 1.1
# === END SIGNATURE ===
"""
HOPE PROCESS WATCHDOG v1.0

Автоматический мониторинг и перезапуск процессов.

═══════════════════════════════════════════════════════════════════════════════
ФУНКЦИИ:
═══════════════════════════════════════════════════════════════════════════════

1. AUTO-RESTART: Если процесс упал → автоматически перезапускаем
2. HTTP API: http://localhost:8080/ для управления
3. TELEGRAM: Команды /start_*, /stop_*, /restart_*, /status

═══════════════════════════════════════════════════════════════════════════════
HTTP ENDPOINTS:
═══════════════════════════════════════════════════════════════════════════════

GET  /                  - Dashboard HTML
GET  /api/status        - JSON статус всех процессов
POST /api/start/{name}  - Запустить процесс
POST /api/stop/{name}   - Остановить процесс
POST /api/restart/{name}- Перезапустить процесс
GET  /api/logs/{name}   - Последние 100 строк лога

═══════════════════════════════════════════════════════════════════════════════
TELEGRAM COMMANDS:
═══════════════════════════════════════════════════════════════════════════════

/status          - Статус всех процессов
/start_pump      - Запустить pump_detector
/stop_pump       - Остановить pump_detector
/restart_pump    - Перезапустить pump_detector
/start_all       - Запустить все
/stop_all        - Остановить все (кроме watchdog)

═══════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any
from enum import Enum

# HTTP server
try:
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    
# Telegram
try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)-12s | %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger("WATCHDOG")


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

# Working directory
WORK_DIR = Path(os.environ.get("HOPE_DIR", "."))

# HTTP API port
HTTP_PORT = 8080

# Telegram config
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Check interval
CHECK_INTERVAL = 5  # seconds

# Max restart attempts before giving up
MAX_RESTART_ATTEMPTS = 5
RESTART_COOLDOWN = 60  # seconds between restart attempts


# ══════════════════════════════════════════════════════════════════════════════
# PROCESS DEFINITIONS
# ══════════════════════════════════════════════════════════════════════════════

class ProcessState(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    STARTING = "starting"
    CRASHED = "crashed"
    DISABLED = "disabled"


@dataclass
class ProcessConfig:
    """Configuration for a managed process."""
    name: str
    command: List[str]
    auto_restart: bool = True
    enabled: bool = True
    log_file: str = ""
    health_check_url: str = ""
    health_check_port: int = 0
    
    
@dataclass
class ProcessStatus:
    """Runtime status of a process."""
    name: str
    state: ProcessState
    pid: Optional[int] = None
    started_at: Optional[float] = None
    uptime_seconds: float = 0
    restart_count: int = 0
    last_restart: Optional[float] = None
    last_error: str = ""
    

# Process definitions
# NOTE: Only enable processes that actually exist!
PROCESSES: Dict[str, ProcessConfig] = {
    "pump_detector": ProcessConfig(
        name="pump_detector",
        command=["python", "scripts/pump_detector.py", "--top", "10"],
        auto_restart=True,
        enabled=True,
        log_file="logs/pump_detector.log",
        health_check_port=0,
    ),
    "autotrader": ProcessConfig(
        name="autotrader",
        command=["python", "scripts/autotrader.py", "--mode", "TESTNET"],  # TESTNET mode for safety
        auto_restart=True,
        enabled=True,  # ENABLED - FIX 2026-01-30
        log_file="logs/autotrader.log",
        health_check_port=8200,  # AutoTrader API port
    ),
    "tv_allowlist": ProcessConfig(
        name="tv_allowlist",
        command=["python", "scripts/tradingview_allowlist.py", "--daemon"],
        auto_restart=True,
        enabled=True,
        log_file="logs/tradingview_allowlist.log",
    ),
    "tg_bot": ProcessConfig(
        name="tg_bot",
        command=["python", "tg_bot_simple.py"],  # In root, not scripts/
        auto_restart=True,
        enabled=False,  # Disabled - already running on server
        log_file="logs/tg_bot.log",
    ),
    # DISABLED - files don't exist yet:
    # "ai_gateway": disabled
    # "friend_bridge": disabled
}


# ══════════════════════════════════════════════════════════════════════════════
# PROCESS MANAGER
# ══════════════════════════════════════════════════════════════════════════════

class ProcessManager:
    """
    Manages all HOPE processes with auto-restart capability.
    """
    
    def __init__(self, work_dir: Path):
        self.work_dir = work_dir
        self.processes: Dict[str, subprocess.Popen] = {}
        self.status: Dict[str, ProcessStatus] = {}
        self.running = False
        
        # Initialize status for all processes
        for name in PROCESSES:
            self.status[name] = ProcessStatus(
                name=name,
                state=ProcessState.STOPPED,
            )
            
    def start_process(self, name: str) -> bool:
        """Start a process."""
        if name not in PROCESSES:
            log.error(f"Unknown process: {name}")
            return False
            
        config = PROCESSES[name]
        
        if not config.enabled:
            log.warning(f"Process {name} is disabled")
            return False
            
        # Check if already running
        if name in self.processes and self.processes[name].poll() is None:
            log.warning(f"Process {name} already running (PID {self.processes[name].pid})")
            return True
            
        try:
            # Prepare log file
            log_path = self.work_dir / config.log_file if config.log_file else None
            log_file = open(log_path, 'a') if log_path else subprocess.DEVNULL
            
            # Start process
            self.status[name].state = ProcessState.STARTING
            
            proc = subprocess.Popen(
                config.command,
                cwd=str(self.work_dir),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
            )
            
            self.processes[name] = proc
            self.status[name].pid = proc.pid
            self.status[name].started_at = time.time()
            self.status[name].state = ProcessState.RUNNING
            self.status[name].last_error = ""
            
            log.info(f"✅ Started {name} (PID {proc.pid})")
            return True
            
        except Exception as e:
            log.error(f"❌ Failed to start {name}: {e}")
            self.status[name].state = ProcessState.CRASHED
            self.status[name].last_error = str(e)
            return False
            
    def stop_process(self, name: str, force: bool = False) -> bool:
        """Stop a process."""
        if name not in self.processes:
            log.warning(f"Process {name} not running")
            return True
            
        proc = self.processes[name]
        
        if proc.poll() is not None:
            # Already terminated
            del self.processes[name]
            self.status[name].state = ProcessState.STOPPED
            self.status[name].pid = None
            return True
            
        try:
            if force:
                proc.kill()
            else:
                proc.terminate()
                
            proc.wait(timeout=10)
            
            del self.processes[name]
            self.status[name].state = ProcessState.STOPPED
            self.status[name].pid = None
            
            log.info(f"🛑 Stopped {name}")
            return True
            
        except subprocess.TimeoutExpired:
            proc.kill()
            del self.processes[name]
            self.status[name].state = ProcessState.STOPPED
            return True
            
        except Exception as e:
            log.error(f"Error stopping {name}: {e}")
            return False
            
    def restart_process(self, name: str) -> bool:
        """Restart a process."""
        log.info(f"🔄 Restarting {name}...")
        
        self.stop_process(name, force=True)
        time.sleep(1)  # Brief pause
        
        self.status[name].restart_count += 1
        self.status[name].last_restart = time.time()
        
        return self.start_process(name)
        
    def check_processes(self):
        """Check all processes and restart if needed."""
        for name, config in PROCESSES.items():
            if not config.enabled:
                continue
                
            status = self.status[name]
            
            # Check if process is running
            if name in self.processes:
                proc = self.processes[name]
                
                if proc.poll() is not None:
                    # Process has terminated
                    exit_code = proc.returncode
                    log.warning(f"⚠️ Process {name} terminated with code {exit_code}")
                    
                    del self.processes[name]
                    status.state = ProcessState.CRASHED
                    status.pid = None
                    status.last_error = f"Exit code: {exit_code}"
                    
                    # Auto-restart if enabled
                    if config.auto_restart:
                        # Check cooldown
                        now = time.time()
                        if status.last_restart and now - status.last_restart < RESTART_COOLDOWN:
                            log.warning(f"Cooldown active for {name}, waiting...")
                            continue
                            
                        # Check max attempts
                        if status.restart_count >= MAX_RESTART_ATTEMPTS:
                            log.error(f"❌ Max restart attempts reached for {name}")
                            status.state = ProcessState.DISABLED
                            continue
                            
                        log.info(f"🔄 Auto-restarting {name}...")
                        self.restart_process(name)
                        
                else:
                    # Process is running
                    status.state = ProcessState.RUNNING
                    if status.started_at:
                        status.uptime_seconds = time.time() - status.started_at
                        
    def get_status(self) -> Dict[str, Dict]:
        """Get status of all processes."""
        self.check_processes()
        
        result = {}
        for name, status in self.status.items():
            result[name] = {
                "name": name,
                "state": status.state.value,
                "pid": status.pid,
                "uptime": self._format_uptime(status.uptime_seconds),
                "uptime_seconds": int(status.uptime_seconds),
                "restart_count": status.restart_count,
                "last_error": status.last_error,
                "auto_restart": PROCESSES[name].auto_restart,
                "enabled": PROCESSES[name].enabled,
            }
        return result
        
    def _format_uptime(self, seconds: float) -> str:
        """Format uptime as HH:MM:SS."""
        if seconds <= 0:
            return "--:--:--"
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        
    def start_all(self):
        """Start all enabled processes."""
        for name, config in PROCESSES.items():
            if config.enabled:
                self.start_process(name)
                
    def stop_all(self):
        """Stop all processes."""
        for name in list(self.processes.keys()):
            self.stop_process(name)


# ══════════════════════════════════════════════════════════════════════════════
# HTTP API SERVER
# ══════════════════════════════════════════════════════════════════════════════

async def create_http_server(manager: ProcessManager, port: int = HTTP_PORT):
    """Create HTTP API server."""
    
    if not AIOHTTP_AVAILABLE:
        log.warning("aiohttp not available, HTTP API disabled")
        return None
        
    app = web.Application()
    
    # Dashboard HTML
    async def handle_dashboard(request):
        html = generate_dashboard_html(manager)
        return web.Response(text=html, content_type='text/html')
        
    # API: Get status
    async def handle_status(request):
        status = manager.get_status()
        return web.json_response(status)
        
    # API: Start process
    async def handle_start(request):
        name = request.match_info.get('name')
        success = manager.start_process(name)
        return web.json_response({"success": success, "action": "start", "process": name})
        
    # API: Stop process
    async def handle_stop(request):
        name = request.match_info.get('name')
        success = manager.stop_process(name)
        return web.json_response({"success": success, "action": "stop", "process": name})
        
    # API: Restart process
    async def handle_restart(request):
        name = request.match_info.get('name')
        success = manager.restart_process(name)
        return web.json_response({"success": success, "action": "restart", "process": name})
        
    # API: Get logs
    async def handle_logs(request):
        name = request.match_info.get('name')
        lines = int(request.query.get('lines', 100))
        
        if name not in PROCESSES:
            return web.json_response({"error": "Unknown process"}, status=404)
            
        config = PROCESSES[name]
        if not config.log_file:
            return web.json_response({"error": "No log file configured"}, status=404)
            
        log_path = manager.work_dir / config.log_file
        if not log_path.exists():
            return web.json_response({"error": "Log file not found"}, status=404)
            
        try:
            with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                all_lines = f.readlines()
                last_lines = all_lines[-lines:]
                return web.json_response({"logs": last_lines})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
            
    # Routes
    app.router.add_get('/', handle_dashboard)
    app.router.add_get('/api/status', handle_status)
    app.router.add_post('/api/start/{name}', handle_start)
    app.router.add_post('/api/stop/{name}', handle_stop)
    app.router.add_post('/api/restart/{name}', handle_restart)
    app.router.add_get('/api/logs/{name}', handle_logs)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    log.info(f"🌐 HTTP API started on http://localhost:{port}/")
    return runner


def generate_dashboard_html(manager: ProcessManager) -> str:
    """Generate simple dashboard HTML."""
    status = manager.get_status()
    
    rows = ""
    for name, data in status.items():
        state = data['state']
        color = "#00ff00" if state == "running" else "#ff4444" if state in ["crashed", "disabled"] else "#ffaa00"
        
        rows += f"""
        <tr>
            <td>{name}</td>
            <td style="color: {color}; font-weight: bold;">{state.upper()}</td>
            <td>{data['pid'] or '-'}</td>
            <td>{data['uptime']}</td>
            <td>{data['restart_count']}</td>
            <td>
                <button onclick="api('start', '{name}')" {'disabled' if state == 'running' else ''}>▶ Start</button>
                <button onclick="api('stop', '{name}')" {'disabled' if state != 'running' else ''}>⏹ Stop</button>
                <button onclick="api('restart', '{name}')">🔄 Restart</button>
            </td>
        </tr>
        """
        
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>HOPE Process Watchdog</title>
        <meta charset="utf-8">
        <style>
            body {{ 
                background: #1a1a2e; 
                color: #eee; 
                font-family: 'Segoe UI', monospace;
                padding: 20px;
            }}
            h1 {{ color: #00d4ff; }}
            table {{ 
                width: 100%; 
                border-collapse: collapse; 
                margin-top: 20px;
            }}
            th, td {{ 
                padding: 12px; 
                text-align: left; 
                border-bottom: 1px solid #333;
            }}
            th {{ background: #16213e; color: #00d4ff; }}
            tr:hover {{ background: #16213e; }}
            button {{
                background: #0f3460;
                color: #fff;
                border: 1px solid #00d4ff;
                padding: 8px 16px;
                margin: 2px;
                cursor: pointer;
                border-radius: 4px;
            }}
            button:hover {{ background: #00d4ff; color: #000; }}
            button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
            .refresh {{ margin: 20px 0; }}
        </style>
        <script>
            function api(action, name) {{
                fetch('/api/' + action + '/' + name, {{method: 'POST'}})
                    .then(r => r.json())
                    .then(d => {{
                        console.log(d);
                        setTimeout(() => location.reload(), 500);
                    }});
            }}
            function refresh() {{ location.reload(); }}
            setInterval(refresh, 10000); // Auto-refresh every 10s
        </script>
    </head>
    <body>
        <h1>🔧 HOPE Process Watchdog</h1>
        <p>Auto-restart: ✅ Enabled | Check interval: {CHECK_INTERVAL}s | Updated: {datetime.now().strftime('%H:%M:%S')}</p>
        
        <div class="refresh">
            <button onclick="refresh()">🔄 Refresh</button>
            <button onclick="fetch('/api/status').then(r=>r.json()).then(d=>alert(JSON.stringify(d,null,2)))">📊 JSON Status</button>
        </div>
        
        <table>
            <tr>
                <th>Process</th>
                <th>State</th>
                <th>PID</th>
                <th>Uptime</th>
                <th>Restarts</th>
                <th>Actions</th>
            </tr>
            {rows}
        </table>
        
        <h2>Quick Actions</h2>
        <button onclick="['pump_detector','autotrader','ai_gateway'].forEach(p=>api('start',p))">▶ Start Core</button>
        <button onclick="['pump_detector','autotrader','ai_gateway'].forEach(p=>api('stop',p))">⏹ Stop Core</button>
        <button onclick="['pump_detector','autotrader','ai_gateway'].forEach(p=>api('restart',p))">🔄 Restart Core</button>
        
        <h2>Logs</h2>
        <p>
            <a href="/api/logs/pump_detector?lines=50" target="_blank">pump_detector</a> |
            <a href="/api/logs/autotrader?lines=50" target="_blank">autotrader</a> |
            <a href="/api/logs/ai_gateway?lines=50" target="_blank">ai_gateway</a>
        </p>
    </body>
    </html>
    """
    return html


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

async def send_telegram(message: str):
    """Send message to Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
        
    if not HTTPX_AVAILABLE:
        return
        
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": message,
                    "parse_mode": "Markdown",
                }
            )
    except Exception as e:
        log.error(f"Telegram send error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN WATCHDOG LOOP
# ══════════════════════════════════════════════════════════════════════════════

async def watchdog_loop(manager: ProcessManager):
    """Main watchdog loop."""
    log.info("🐕 Watchdog loop started")
    
    while manager.running:
        try:
            manager.check_processes()
        except Exception as e:
            log.error(f"Watchdog error: {e}")
            
        await asyncio.sleep(CHECK_INTERVAL)


async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="HOPE Process Watchdog")
    parser.add_argument("--port", type=int, default=HTTP_PORT, help="HTTP API port")
    parser.add_argument("--no-autostart", action="store_true", help="Don't auto-start processes")
    parser.add_argument("--work-dir", type=str, default=".", help="Working directory")
    
    args = parser.parse_args()
    
    work_dir = Path(args.work_dir).resolve()
    log.info(f"Working directory: {work_dir}")
    
    # Create manager
    manager = ProcessManager(work_dir)
    manager.running = True
    
    # Start HTTP server
    http_runner = await create_http_server(manager, args.port)
    
    # Auto-start processes
    if not args.no_autostart:
        log.info("Auto-starting processes...")
        manager.start_all()
        
    # Notify via Telegram
    await send_telegram("🐕 *HOPE Watchdog started*\nAuto-restart: enabled")
    
    # Run watchdog loop
    try:
        await watchdog_loop(manager)
    except KeyboardInterrupt:
        log.info("Shutting down...")
        manager.running = False
        manager.stop_all()
        
        if http_runner:
            await http_runner.cleanup()
            

if __name__ == "__main__":
    asyncio.run(main())
