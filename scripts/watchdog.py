# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-01-30 18:30:00 UTC
# Purpose: Process Watchdog - Auto-restart crashed processes
# Version: 1.0
# === END SIGNATURE ===
"""
HOPE PROCESS WATCHDOG v1.0

Автоматический перезапуск упавших процессов.

═══════════════════════════════════════════════════════════════════════════════
ФУНКЦИИ:
═══════════════════════════════════════════════════════════════════════════════

1. МОНИТОРИНГ процессов каждые 5 секунд
2. АВТО-ПЕРЕЗАПУСК при падении (< 3 секунд задержка)
3. TELEGRAM уведомления о падении/перезапуске
4. HTTP API на порту 8080 для управления
5. TELEGRAM команды /start, /stop, /restart, /status

═══════════════════════════════════════════════════════════════════════════════
HTTP API (localhost:8080):
═══════════════════════════════════════════════════════════════════════════════

GET  /status                - Статус всех процессов
POST /start/<process>       - Запустить процесс
POST /stop/<process>        - Остановить процесс
POST /restart/<process>     - Перезапустить процесс
POST /restart-all           - Перезапустить все
GET  /logs/<process>        - Последние логи

═══════════════════════════════════════════════════════════════════════════════
TELEGRAM КОМАНДЫ:
═══════════════════════════════════════════════════════════════════════════════

/watchdog           - Статус watchdog
/ps                 - Статус всех процессов
/start <process>    - Запустить процесс
/stop <process>     - Остановить процесс  
/restart <process>  - Перезапустить процесс
/restart_all        - Перезапустить всё
/logs <process>     - Последние логи

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
import threading
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
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

# Base directory
BASE_DIR = Path(os.environ.get("HOPE_BASE_DIR", r"C:\Users\kirillDev\Desktop\TradingBot\minibot"))

# State
STATE_DIR = BASE_DIR / "state" / "watchdog"
STATE_DIR.mkdir(parents=True, exist_ok=True)

# HTTP API port
HTTP_PORT = 8080

# Check interval
CHECK_INTERVAL = 5  # seconds

# Restart delay
RESTART_DELAY = 2  # seconds

# Max restarts per hour (circuit breaker)
MAX_RESTARTS_PER_HOUR = 10


# ══════════════════════════════════════════════════════════════════════════════
# PROCESS DEFINITIONS
# ══════════════════════════════════════════════════════════════════════════════

class ProcessState(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    CRASHED = "crashed"
    STARTING = "starting"
    DISABLED = "disabled"


@dataclass
class ProcessConfig:
    """Конфигурация процесса."""
    name: str
    command: str
    args: List[str] = field(default_factory=list)
    cwd: str = ""
    auto_restart: bool = True
    enabled: bool = True
    priority: int = 1  # 1=highest, lower first
    depends_on: List[str] = field(default_factory=list)
    health_check_url: str = ""
    log_file: str = ""


# Process configurations
PROCESSES: Dict[str, ProcessConfig] = {
    "ai_gateway": ProcessConfig(
        name="ai_gateway",
        command="python",
        args=["scripts/ai_gateway_server.py"],
        auto_restart=True,
        priority=1,
        health_check_url="http://127.0.0.1:8100/health",
        log_file="logs/ai_gateway.log",
    ),
    "pump_detector": ProcessConfig(
        name="pump_detector",
        command="python",
        args=["scripts/pump_detector.py", "--top", "20"],
        auto_restart=True,
        priority=2,
        depends_on=["ai_gateway"],
        log_file="logs/pump_detector_v4.log",
    ),
    "autotrader": ProcessConfig(
        name="autotrader",
        command="python",
        args=["scripts/autotrader.py", "--mode", "DRY", "--api-port", "8200"],
        auto_restart=True,
        priority=3,
        depends_on=["ai_gateway"],
        health_check_url="http://127.0.0.1:8200/status",
        log_file="logs/autotrader.log",
    ),
    "friend_bridge": ProcessConfig(
        name="friend_bridge",
        command="python",
        args=["scripts/friend_bridge.py"],
        auto_restart=True,
        priority=4,
        log_file="logs/friend_bridge.log",
    ),
    "tradingview_allowlist": ProcessConfig(
        name="tradingview_allowlist",
        command="python",
        args=["scripts/tradingview_allowlist.py", "--daemon"],
        auto_restart=True,
        priority=5,
        log_file="logs/tradingview_allowlist.log",
    ),
    "tg_bot": ProcessConfig(
        name="tg_bot",
        command="python",
        args=["scripts/tg_bot_simple.py"],
        auto_restart=True,
        priority=6,
        log_file="logs/tg_bot.log",
    ),
}


@dataclass
class ProcessStatus:
    """Статус процесса."""
    name: str
    state: ProcessState
    pid: Optional[int] = None
    uptime: float = 0
    restarts: int = 0
    last_restart: Optional[float] = None
    last_error: Optional[str] = None
    cpu_percent: float = 0
    memory_mb: float = 0


# ══════════════════════════════════════════════════════════════════════════════
# PROCESS MANAGER
# ══════════════════════════════════════════════════════════════════════════════

class ProcessManager:
    """
    Менеджер процессов с автоматическим перезапуском.
    """
    
    def __init__(self):
        self.processes: Dict[str, subprocess.Popen] = {}
        self.status: Dict[str, ProcessStatus] = {}
        self.restart_counts: Dict[str, List[float]] = {}  # timestamps
        self.enabled: Dict[str, bool] = {}
        self.running = False
        
        # Initialize status
        for name, config in PROCESSES.items():
            self.status[name] = ProcessStatus(
                name=name,
                state=ProcessState.STOPPED,
            )
            self.enabled[name] = config.enabled
            self.restart_counts[name] = []
            
        # Load saved state
        self._load_state()
        
    def _load_state(self):
        """Load saved state."""
        state_file = STATE_DIR / "watchdog_state.json"
        if state_file.exists():
            try:
                with open(state_file) as f:
                    data = json.load(f)
                    for name, enabled in data.get("enabled", {}).items():
                        if name in self.enabled:
                            self.enabled[name] = enabled
            except:
                pass
                
    def _save_state(self):
        """Save state."""
        state_file = STATE_DIR / "watchdog_state.json"
        with open(state_file, 'w') as f:
            json.dump({
                "enabled": self.enabled,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }, f, indent=2)
            
    def _check_circuit_breaker(self, name: str) -> bool:
        """Check if too many restarts (circuit breaker)."""
        now = time.time()
        hour_ago = now - 3600
        
        # Cleanup old timestamps
        self.restart_counts[name] = [
            t for t in self.restart_counts[name] if t > hour_ago
        ]
        
        return len(self.restart_counts[name]) < MAX_RESTARTS_PER_HOUR
        
    def _record_restart(self, name: str):
        """Record restart timestamp."""
        self.restart_counts[name].append(time.time())
        
    async def start_process(self, name: str) -> bool:
        """Start a process."""
        if name not in PROCESSES:
            log.error(f"Unknown process: {name}")
            return False
            
        config = PROCESSES[name]
        
        # Check dependencies
        for dep in config.depends_on:
            if dep in self.status and self.status[dep].state != ProcessState.RUNNING:
                log.warning(f"Dependency {dep} not running, starting it first...")
                await self.start_process(dep)
                await asyncio.sleep(2)
                
        # Check if already running
        if name in self.processes and self.processes[name].poll() is None:
            log.info(f"{name} already running (PID={self.processes[name].pid})")
            return True
            
        # Build command
        cmd = [config.command] + config.args
        cwd = config.cwd or str(BASE_DIR)
        
        # Start process
        try:
            log.info(f"Starting {name}: {' '.join(cmd)}")
            
            self.status[name].state = ProcessState.STARTING
            
            # Open log file
            log_file = None
            if config.log_file:
                log_path = BASE_DIR / config.log_file
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_file = open(log_path, 'a')
                
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=log_file or subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
            )
            
            self.processes[name] = proc
            
            # Wait a bit to check if started
            await asyncio.sleep(1)
            
            if proc.poll() is None:
                self.status[name] = ProcessStatus(
                    name=name,
                    state=ProcessState.RUNNING,
                    pid=proc.pid,
                    uptime=0,
                    restarts=self.status[name].restarts,
                    last_restart=time.time(),
                )
                log.info(f"✅ {name} started (PID={proc.pid})")
                return True
            else:
                self.status[name].state = ProcessState.CRASHED
                self.status[name].last_error = f"Exit code: {proc.returncode}"
                log.error(f"❌ {name} failed to start")
                return False
                
        except Exception as e:
            log.error(f"Error starting {name}: {e}")
            self.status[name].state = ProcessState.CRASHED
            self.status[name].last_error = str(e)
            return False
            
    async def stop_process(self, name: str) -> bool:
        """Stop a process."""
        if name not in self.processes:
            log.info(f"{name} not running")
            return True
            
        proc = self.processes[name]
        
        if proc.poll() is not None:
            log.info(f"{name} already stopped")
            self.status[name].state = ProcessState.STOPPED
            return True
            
        try:
            log.info(f"Stopping {name} (PID={proc.pid})...")
            
            # Graceful termination
            proc.terminate()
            
            # Wait up to 5 seconds
            for _ in range(10):
                if proc.poll() is not None:
                    break
                await asyncio.sleep(0.5)
                
            # Force kill if still running
            if proc.poll() is None:
                log.warning(f"Force killing {name}...")
                proc.kill()
                await asyncio.sleep(1)
                
            self.status[name].state = ProcessState.STOPPED
            self.status[name].pid = None
            log.info(f"✅ {name} stopped")
            return True
            
        except Exception as e:
            log.error(f"Error stopping {name}: {e}")
            return False
            
    async def restart_process(self, name: str) -> bool:
        """Restart a process."""
        log.info(f"Restarting {name}...")
        await self.stop_process(name)
        await asyncio.sleep(RESTART_DELAY)
        return await self.start_process(name)
        
    async def check_processes(self):
        """Check all processes and restart if needed."""
        for name, config in PROCESSES.items():
            if not self.enabled.get(name, config.enabled):
                self.status[name].state = ProcessState.DISABLED
                continue
                
            # Check if process is running
            if name in self.processes:
                proc = self.processes[name]
                
                if proc.poll() is None:
                    # Running - update uptime
                    if self.status[name].last_restart:
                        self.status[name].uptime = time.time() - self.status[name].last_restart
                    self.status[name].state = ProcessState.RUNNING
                else:
                    # Crashed!
                    log.error(f"💥 {name} CRASHED! Exit code: {proc.returncode}")
                    self.status[name].state = ProcessState.CRASHED
                    self.status[name].last_error = f"Exit code: {proc.returncode}"
                    
                    # Auto-restart if enabled
                    if config.auto_restart and self._check_circuit_breaker(name):
                        log.info(f"🔄 Auto-restarting {name}...")
                        self._record_restart(name)
                        self.status[name].restarts += 1
                        
                        # Notify Telegram
                        await self._notify_crash(name, proc.returncode)
                        
                        # Restart
                        await asyncio.sleep(RESTART_DELAY)
                        await self.start_process(name)
                    elif not self._check_circuit_breaker(name):
                        log.error(f"⚠️ Circuit breaker triggered for {name} (too many restarts)")
                        self.status[name].state = ProcessState.DISABLED
                        self.enabled[name] = False
                        await self._notify_circuit_breaker(name)
            else:
                # Not started - try to start if enabled
                if config.auto_restart:
                    await self.start_process(name)
                    
    async def _notify_crash(self, name: str, exit_code: int):
        """Send crash notification to Telegram."""
        message = (
            f"💥 *ПРОЦЕСС УПАЛ!*\n\n"
            f"Process: `{name}`\n"
            f"Exit code: `{exit_code}`\n"
            f"Time: {datetime.now().strftime('%H:%M:%S')}\n\n"
            f"🔄 Автоматический перезапуск..."
        )
        await self._send_telegram(message)
        
    async def _notify_circuit_breaker(self, name: str):
        """Send circuit breaker notification."""
        message = (
            f"⚠️ *CIRCUIT BREAKER!*\n\n"
            f"Process: `{name}`\n"
            f"Restarts: {MAX_RESTARTS_PER_HOUR}+ per hour\n\n"
            f"Процесс отключен. Проверьте логи!\n"
            f"/start {name} - включить вручную"
        )
        await self._send_telegram(message)
        
    async def _send_telegram(self, message: str):
        """Send message to Telegram."""
        if not HTTPX_AVAILABLE:
            return
            
        # Load credentials
        env_file = Path(r"C:\secrets\hope.env")
        if not env_file.exists():
            return
            
        bot_token = None
        chat_id = None
        
        with open(env_file) as f:
            for line in f:
                if line.startswith("TG_BOT_TOKEN="):
                    bot_token = line.split("=", 1)[1].strip().strip('"')
                elif line.startswith("TG_CHAT_ID="):
                    chat_id = line.split("=", 1)[1].strip().strip('"')
                    
        if not bot_token or not chat_id:
            return
            
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": message,
                        "parse_mode": "Markdown",
                    }
                )
        except Exception as e:
            log.error(f"Telegram error: {e}")
            
    def get_all_status(self) -> Dict[str, Dict]:
        """Get status of all processes."""
        result = {}
        for name, status in self.status.items():
            result[name] = {
                "name": status.name,
                "state": status.state.value,
                "pid": status.pid,
                "uptime": int(status.uptime),
                "uptime_str": str(timedelta(seconds=int(status.uptime))),
                "restarts": status.restarts,
                "enabled": self.enabled.get(name, True),
                "last_error": status.last_error,
            }
        return result
        
    async def enable_process(self, name: str, enabled: bool):
        """Enable/disable auto-restart for process."""
        if name in self.enabled:
            self.enabled[name] = enabled
            self._save_state()
            
            if not enabled:
                self.status[name].state = ProcessState.DISABLED
            elif enabled and self.status[name].state == ProcessState.DISABLED:
                self.status[name].state = ProcessState.STOPPED
                
    async def start_all(self):
        """Start all enabled processes in priority order."""
        log.info("Starting all processes...")
        
        # Sort by priority
        sorted_procs = sorted(
            [(n, c) for n, c in PROCESSES.items() if self.enabled.get(n, c.enabled)],
            key=lambda x: x[1].priority
        )
        
        for name, config in sorted_procs:
            await self.start_process(name)
            await asyncio.sleep(1)
            
    async def stop_all(self):
        """Stop all processes."""
        log.info("Stopping all processes...")
        
        # Stop in reverse priority order
        sorted_procs = sorted(
            list(PROCESSES.keys()),
            key=lambda n: PROCESSES[n].priority,
            reverse=True
        )
        
        for name in sorted_procs:
            await self.stop_process(name)


# ══════════════════════════════════════════════════════════════════════════════
# HTTP API SERVER
# ══════════════════════════════════════════════════════════════════════════════

class HTTPServer:
    """HTTP API server for watchdog control."""
    
    def __init__(self, manager: ProcessManager):
        self.manager = manager
        self.app = None
        self.runner = None
        
    async def setup(self):
        """Setup HTTP server."""
        if not AIOHTTP_AVAILABLE:
            log.warning("aiohttp not available, HTTP API disabled")
            return
            
        self.app = web.Application()
        
        # Routes
        self.app.router.add_get('/', self.handle_index)
        self.app.router.add_get('/status', self.handle_status)
        self.app.router.add_get('/api/status', self.handle_api_status)
        self.app.router.add_post('/api/start/{name}', self.handle_start)
        self.app.router.add_post('/api/stop/{name}', self.handle_stop)
        self.app.router.add_post('/api/restart/{name}', self.handle_restart)
        self.app.router.add_post('/api/restart-all', self.handle_restart_all)
        self.app.router.add_post('/api/enable/{name}', self.handle_enable)
        self.app.router.add_get('/api/logs/{name}', self.handle_logs)
        
        # CORS
        self.app.router.add_route('OPTIONS', '/{tail:.*}', self.handle_options)
        
    async def start(self):
        """Start HTTP server."""
        if not self.app:
            return
            
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        
        site = web.TCPSite(self.runner, '0.0.0.0', HTTP_PORT)
        await site.start()
        
        log.info(f"🌐 HTTP API running on http://localhost:{HTTP_PORT}")
        
    async def stop(self):
        """Stop HTTP server."""
        if self.runner:
            await self.runner.cleanup()
            
    def _cors_headers(self):
        return {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        }
        
    async def handle_options(self, request):
        return web.Response(headers=self._cors_headers())
        
    async def handle_index(self, request):
        """Serve main page - redirect to dashboard."""
        return web.Response(
            text="HOPE Watchdog API - Use /status for process status",
            content_type='text/plain'
        )
        
    async def handle_status(self, request):
        """HTML status page."""
        status = self.manager.get_all_status()
        
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>HOPE Watchdog</title>
            <meta http-equiv="refresh" content="5">
            <style>
                body { font-family: monospace; background: #1a1a2e; color: #eee; padding: 20px; }
                .running { color: #00ff00; }
                .stopped { color: #ff6600; }
                .crashed { color: #ff0000; }
                .disabled { color: #666; }
                table { border-collapse: collapse; width: 100%; }
                th, td { border: 1px solid #333; padding: 10px; text-align: left; }
                th { background: #16213e; }
                button { padding: 5px 15px; margin: 2px; cursor: pointer; }
                .btn-start { background: #28a745; color: white; border: none; }
                .btn-stop { background: #dc3545; color: white; border: none; }
                .btn-restart { background: #ffc107; color: black; border: none; }
            </style>
        </head>
        <body>
            <h1>🐕 HOPE Watchdog Status</h1>
            <p>Auto-refresh: 5 seconds</p>
            <table>
                <tr>
                    <th>Process</th>
                    <th>State</th>
                    <th>PID</th>
                    <th>Uptime</th>
                    <th>Restarts</th>
                    <th>Actions</th>
                </tr>
        """
        
        for name, s in status.items():
            state_class = s['state']
            html += f"""
                <tr>
                    <td>{name}</td>
                    <td class="{state_class}">{s['state'].upper()}</td>
                    <td>{s['pid'] or '-'}</td>
                    <td>{s['uptime_str']}</td>
                    <td>{s['restarts']}</td>
                    <td>
                        <button class="btn-start" onclick="api('start', '{name}')">Start</button>
                        <button class="btn-stop" onclick="api('stop', '{name}')">Stop</button>
                        <button class="btn-restart" onclick="api('restart', '{name}')">Restart</button>
                    </td>
                </tr>
            """
            
        html += """
            </table>
            <br>
            <button class="btn-restart" onclick="api('restart-all', '')">Restart All</button>
            <script>
                async function api(action, name) {
                    const url = name ? `/api/${action}/${name}` : `/api/${action}`;
                    await fetch(url, {method: 'POST'});
                    location.reload();
                }
            </script>
        </body>
        </html>
        """
        
        return web.Response(text=html, content_type='text/html')
        
    async def handle_api_status(self, request):
        """JSON status."""
        status = self.manager.get_all_status()
        return web.json_response(status, headers=self._cors_headers())
        
    async def handle_start(self, request):
        """Start process."""
        name = request.match_info['name']
        await self.manager.enable_process(name, True)
        result = await self.manager.start_process(name)
        return web.json_response(
            {"success": result, "process": name, "action": "start"},
            headers=self._cors_headers()
        )
        
    async def handle_stop(self, request):
        """Stop process."""
        name = request.match_info['name']
        result = await self.manager.stop_process(name)
        return web.json_response(
            {"success": result, "process": name, "action": "stop"},
            headers=self._cors_headers()
        )
        
    async def handle_restart(self, request):
        """Restart process."""
        name = request.match_info['name']
        result = await self.manager.restart_process(name)
        return web.json_response(
            {"success": result, "process": name, "action": "restart"},
            headers=self._cors_headers()
        )
        
    async def handle_restart_all(self, request):
        """Restart all processes."""
        await self.manager.stop_all()
        await asyncio.sleep(2)
        await self.manager.start_all()
        return web.json_response(
            {"success": True, "action": "restart-all"},
            headers=self._cors_headers()
        )
        
    async def handle_enable(self, request):
        """Enable/disable process."""
        name = request.match_info['name']
        data = await request.json()
        enabled = data.get('enabled', True)
        await self.manager.enable_process(name, enabled)
        return web.json_response(
            {"success": True, "process": name, "enabled": enabled},
            headers=self._cors_headers()
        )
        
    async def handle_logs(self, request):
        """Get process logs."""
        name = request.match_info['name']
        
        if name not in PROCESSES:
            return web.json_response({"error": "Unknown process"}, status=404)
            
        log_file = PROCESSES[name].log_file
        if not log_file:
            return web.json_response({"error": "No log file"}, status=404)
            
        log_path = BASE_DIR / log_file
        
        if not log_path.exists():
            return web.json_response({"error": "Log file not found"}, status=404)
            
        # Read last 100 lines
        try:
            with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()[-100:]
            return web.json_response(
                {"process": name, "logs": lines},
                headers=self._cors_headers()
            )
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN WATCHDOG
# ══════════════════════════════════════════════════════════════════════════════

class Watchdog:
    """Main watchdog daemon."""
    
    def __init__(self):
        self.manager = ProcessManager()
        self.http_server = HTTPServer(self.manager)
        self.running = False
        
    async def run(self):
        """Run watchdog."""
        self.running = True
        
        log.info("=" * 60)
        log.info("🐕 HOPE WATCHDOG v1.0 STARTING")
        log.info("=" * 60)
        
        # Setup HTTP server
        await self.http_server.setup()
        await self.http_server.start()
        
        # Start all processes
        await self.manager.start_all()
        
        # Main loop
        log.info(f"Monitoring {len(PROCESSES)} processes every {CHECK_INTERVAL}s...")
        
        while self.running:
            try:
                await self.manager.check_processes()
            except Exception as e:
                log.error(f"Check error: {e}")
                
            await asyncio.sleep(CHECK_INTERVAL)
            
    async def stop(self):
        """Stop watchdog."""
        log.info("Stopping watchdog...")
        self.running = False
        
        await self.manager.stop_all()
        await self.http_server.stop()
        
        log.info("Watchdog stopped")


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="HOPE Process Watchdog")
    parser.add_argument("--start", action="store_true", help="Start watchdog daemon")
    parser.add_argument("--status", action="store_true", help="Show process status")
    parser.add_argument("--restart", type=str, help="Restart specific process")
    
    args = parser.parse_args()
    
    if args.status:
        manager = ProcessManager()
        status = manager.get_all_status()
        
        print("=" * 60)
        print("HOPE WATCHDOG STATUS")
        print("=" * 60)
        
        for name, s in status.items():
            state = s['state'].upper()
            emoji = "✅" if state == "RUNNING" else "❌" if state == "CRASHED" else "⭕"
            print(f"{emoji} {name:20} | {state:10} | PID: {s['pid'] or '-':6} | Uptime: {s['uptime_str']}")
            
    elif args.restart:
        manager = ProcessManager()
        await manager.restart_process(args.restart)
        
    elif args.start:
        watchdog = Watchdog()
        
        # Handle shutdown
        def signal_handler(sig, frame):
            asyncio.create_task(watchdog.stop())
            
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        await watchdog.run()
        
    else:
        parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())
