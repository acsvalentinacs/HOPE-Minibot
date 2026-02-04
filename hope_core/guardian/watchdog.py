# === AI SIGNATURE ===
# Module: hope_core/guardian/watchdog.py
# Created by: Claude (opus-4.5)
# Created at: 2026-02-04 10:20:00 UTC
# Purpose: Guardian watchdog with health monitoring and auto-recovery
# === END SIGNATURE ===
"""
HOPE Core - Guardian Watchdog

Independent process that monitors HOPE Core health.
Automatically restarts on failure, validates state, sends alerts.

Features:
- Heartbeat monitoring
- State validation
- Auto-recovery with backoff
- Telegram alerts (optional)
- Health dashboard
"""

from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
import threading
import asyncio
import time
import json
import os
import signal
import subprocess
import psutil


# =============================================================================
# HEALTH STATUS
# =============================================================================

class HealthStatus(Enum):
    """Overall health status."""
    HEALTHY = "HEALTHY"           # All checks pass
    DEGRADED = "DEGRADED"         # Some checks failing
    UNHEALTHY = "UNHEALTHY"       # Critical checks failing
    UNKNOWN = "UNKNOWN"           # Cannot determine health


class CheckResult(Enum):
    """Individual check result."""
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass
class HealthCheck:
    """Result of a health check."""
    name: str
    result: CheckResult
    message: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "result": self.result.value,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
        }


@dataclass
class HealthReport:
    """Complete health report."""
    status: HealthStatus
    checks: List[HealthCheck]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    uptime_seconds: float = 0.0
    memory_mb: float = 0.0
    cpu_percent: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "checks": [c.to_dict() for c in self.checks],
            "timestamp": self.timestamp.isoformat(),
            "uptime_seconds": self.uptime_seconds,
            "memory_mb": self.memory_mb,
            "cpu_percent": self.cpu_percent,
        }


# =============================================================================
# RECOVERY ACTION
# =============================================================================

class RecoveryAction(Enum):
    """Actions Guardian can take."""
    NONE = "NONE"
    RESTART_CORE = "RESTART_CORE"
    RESTART_COMPONENT = "RESTART_COMPONENT"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    ALERT = "ALERT"
    ROLLBACK = "ROLLBACK"


@dataclass
class RecoveryEvent:
    """Record of recovery action."""
    action: RecoveryAction
    reason: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    success: bool = False
    details: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# GUARDIAN CONFIG
# =============================================================================

@dataclass
class GuardianConfig:
    """Guardian configuration."""
    
    # Heartbeat settings
    heartbeat_interval_sec: float = 10.0
    heartbeat_timeout_sec: float = 30.0
    
    # Health check settings
    health_check_interval_sec: float = 30.0
    max_consecutive_failures: int = 3
    
    # Recovery settings
    max_restarts_per_hour: int = 5
    restart_delay_sec: float = 5.0
    restart_backoff_multiplier: float = 2.0
    max_restart_delay_sec: float = 300.0
    
    # Process settings
    core_command: List[str] = field(default_factory=lambda: [
        "python", "-m", "hope_core.main", "--mode", "LIVE"
    ])
    core_working_dir: str = "/opt/hope/minibot"
    
    # Alerting
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    
    # State file
    state_file: str = "state/guardian_state.json"
    
    # Memory limit (MB)
    memory_limit_mb: float = 500.0
    
    # API endpoint for health check
    health_endpoint: str = "http://127.0.0.1:8200/api/health"


# =============================================================================
# GUARDIAN
# =============================================================================

class Guardian:
    """
    Guardian Watchdog for HOPE Core.
    
    Monitors health, performs recovery, sends alerts.
    Should run as independent process/service.
    """
    
    def __init__(self, config: Optional[GuardianConfig] = None):
        """
        Initialize Guardian.
        
        Args:
            config: Guardian configuration
        """
        self._config = config or GuardianConfig()
        self._running = False
        self._lock = threading.Lock()
        
        # Tracking
        self._start_time: Optional[datetime] = None
        self._last_heartbeat: Optional[datetime] = None
        self._consecutive_failures = 0
        self._restart_count = 0
        self._restart_times: List[datetime] = []
        self._current_restart_delay = self._config.restart_delay_sec
        
        # Core process
        self._core_process: Optional[subprocess.Popen] = None
        
        # Health history
        self._health_history: List[HealthReport] = []
        self._recovery_history: List[RecoveryEvent] = []
        
        # Callbacks
        self._on_alert: Optional[Callable[[str, str], None]] = None
        self._on_recovery: Optional[Callable[[RecoveryEvent], None]] = None
    
    @property
    def uptime(self) -> float:
        """Get uptime in seconds."""
        if self._start_time:
            return (datetime.now(timezone.utc) - self._start_time).total_seconds()
        return 0.0
    
    @property
    def is_core_running(self) -> bool:
        """Check if core process is running."""
        if self._core_process is None:
            return False
        return self._core_process.poll() is None
    
    def set_alert_callback(self, callback: Callable[[str, str], None]):
        """Set callback for alerts. Args: (title, message)."""
        self._on_alert = callback
    
    def set_recovery_callback(self, callback: Callable[[RecoveryEvent], None]):
        """Set callback for recovery events."""
        self._on_recovery = callback
    
    async def start(self):
        """Start Guardian watchdog loop."""
        self._running = True
        self._start_time = datetime.now(timezone.utc)
        
        print(f"[GUARDIAN] Starting at {self._start_time.isoformat()}")
        print(f"[GUARDIAN] Heartbeat interval: {self._config.heartbeat_interval_sec}s")
        print(f"[GUARDIAN] Health check interval: {self._config.health_check_interval_sec}s")
        
        # Start core process
        await self._start_core()
        
        # Main loop
        last_health_check = time.monotonic()
        
        while self._running:
            try:
                # Check heartbeat
                await self._check_heartbeat()
                
                # Periodic health check
                now = time.monotonic()
                if now - last_health_check >= self._config.health_check_interval_sec:
                    await self._run_health_check()
                    last_health_check = now
                
                # Check if core is running
                if not self.is_core_running:
                    print("[GUARDIAN] Core process died!")
                    await self._handle_core_crash()
                
                await asyncio.sleep(self._config.heartbeat_interval_sec)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[GUARDIAN] Error in main loop: {e}")
                await asyncio.sleep(1)
        
        print("[GUARDIAN] Stopped")
    
    def stop(self):
        """Stop Guardian."""
        self._running = False
        if self._core_process:
            self._core_process.terminate()
    
    async def _start_core(self):
        """Start HOPE Core process."""
        print(f"[GUARDIAN] Starting core: {' '.join(self._config.core_command)}")
        
        try:
            self._core_process = subprocess.Popen(
                self._config.core_command,
                cwd=self._config.core_working_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            
            # Give it time to start
            await asyncio.sleep(2)
            
            if self._core_process.poll() is None:
                print(f"[GUARDIAN] Core started with PID {self._core_process.pid}")
                self._record_restart(True)
            else:
                print(f"[GUARDIAN] Core failed to start")
                self._record_restart(False)
                
        except Exception as e:
            print(f"[GUARDIAN] Failed to start core: {e}")
            self._record_restart(False)
    
    def _record_restart(self, success: bool):
        """Record restart attempt."""
        now = datetime.now(timezone.utc)
        self._restart_times.append(now)
        
        # Clean old restart times
        cutoff = now - timedelta(hours=1)
        self._restart_times = [t for t in self._restart_times if t > cutoff]
        
        self._restart_count = len(self._restart_times)
        
        event = RecoveryEvent(
            action=RecoveryAction.RESTART_CORE,
            reason="Core process recovery",
            success=success,
            details={"restart_count": self._restart_count},
        )
        self._recovery_history.append(event)
        
        if self._on_recovery:
            self._on_recovery(event)
    
    async def _check_heartbeat(self):
        """Check heartbeat from core."""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self._config.health_endpoint,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self._last_heartbeat = datetime.now(timezone.utc)
                        self._consecutive_failures = 0
                        return True
                    else:
                        self._consecutive_failures += 1
                        return False
        except ImportError:
            # aiohttp not available, use subprocess curl
            try:
                result = subprocess.run(
                    ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                     self._config.health_endpoint],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.stdout.strip() == "200":
                    self._last_heartbeat = datetime.now(timezone.utc)
                    self._consecutive_failures = 0
                    return True
                else:
                    self._consecutive_failures += 1
                    return False
            except Exception:
                self._consecutive_failures += 1
                return False
        except Exception:
            self._consecutive_failures += 1
            return False
    
    async def _run_health_check(self) -> HealthReport:
        """Run all health checks."""
        start = time.monotonic()
        checks: List[HealthCheck] = []
        
        # 1. Process check
        checks.append(await self._check_process())
        
        # 2. Memory check
        checks.append(await self._check_memory())
        
        # 3. API check
        checks.append(await self._check_api())
        
        # 4. Heartbeat freshness
        checks.append(self._check_heartbeat_freshness())
        
        # Determine overall status
        fail_count = sum(1 for c in checks if c.result == CheckResult.FAIL)
        warn_count = sum(1 for c in checks if c.result == CheckResult.WARN)
        
        if fail_count > 0:
            status = HealthStatus.UNHEALTHY
        elif warn_count > 0:
            status = HealthStatus.DEGRADED
        else:
            status = HealthStatus.HEALTHY
        
        # Get resource usage
        memory_mb = 0.0
        cpu_percent = 0.0
        if self._core_process and self._core_process.poll() is None:
            try:
                proc = psutil.Process(self._core_process.pid)
                memory_mb = proc.memory_info().rss / (1024 * 1024)
                cpu_percent = proc.cpu_percent(interval=0.1)
            except Exception:
                pass
        
        report = HealthReport(
            status=status,
            checks=checks,
            uptime_seconds=self.uptime,
            memory_mb=memory_mb,
            cpu_percent=cpu_percent,
        )
        
        self._health_history.append(report)
        if len(self._health_history) > 100:
            self._health_history = self._health_history[-100:]
        
        # Handle unhealthy status
        if status == HealthStatus.UNHEALTHY:
            await self._handle_unhealthy(report)
        
        return report
    
    async def _check_process(self) -> HealthCheck:
        """Check if core process is running."""
        start = time.monotonic()
        
        if self._core_process is None:
            return HealthCheck(
                name="process",
                result=CheckResult.FAIL,
                message="Core process not started",
                duration_ms=(time.monotonic() - start) * 1000,
            )
        
        if self._core_process.poll() is not None:
            return HealthCheck(
                name="process",
                result=CheckResult.FAIL,
                message=f"Core process exited with code {self._core_process.returncode}",
                duration_ms=(time.monotonic() - start) * 1000,
            )
        
        return HealthCheck(
            name="process",
            result=CheckResult.PASS,
            message=f"Core running (PID {self._core_process.pid})",
            duration_ms=(time.monotonic() - start) * 1000,
            metadata={"pid": self._core_process.pid},
        )
    
    async def _check_memory(self) -> HealthCheck:
        """Check memory usage."""
        start = time.monotonic()
        
        if not self._core_process or self._core_process.poll() is not None:
            return HealthCheck(
                name="memory",
                result=CheckResult.SKIP,
                message="Core not running",
                duration_ms=(time.monotonic() - start) * 1000,
            )
        
        try:
            proc = psutil.Process(self._core_process.pid)
            memory_mb = proc.memory_info().rss / (1024 * 1024)
            
            result = CheckResult.PASS
            message = f"Memory: {memory_mb:.1f}MB"
            
            if memory_mb > self._config.memory_limit_mb:
                result = CheckResult.FAIL
                message = f"Memory exceeded: {memory_mb:.1f}MB > {self._config.memory_limit_mb}MB"
            elif memory_mb > self._config.memory_limit_mb * 0.8:
                result = CheckResult.WARN
                message = f"Memory high: {memory_mb:.1f}MB"
            
            return HealthCheck(
                name="memory",
                result=result,
                message=message,
                duration_ms=(time.monotonic() - start) * 1000,
                metadata={"memory_mb": memory_mb},
            )
            
        except Exception as e:
            return HealthCheck(
                name="memory",
                result=CheckResult.WARN,
                message=f"Cannot check memory: {e}",
                duration_ms=(time.monotonic() - start) * 1000,
            )
    
    async def _check_api(self) -> HealthCheck:
        """Check API health endpoint."""
        start = time.monotonic()
        
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self._config.health_endpoint,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    duration = (time.monotonic() - start) * 1000
                    
                    if resp.status == 200:
                        data = await resp.json()
                        status = data.get("status", "unknown")
                        
                        if status == "healthy":
                            return HealthCheck(
                                name="api",
                                result=CheckResult.PASS,
                                message=f"API healthy ({duration:.0f}ms)",
                                duration_ms=duration,
                                metadata=data,
                            )
                        else:
                            return HealthCheck(
                                name="api",
                                result=CheckResult.WARN,
                                message=f"API status: {status}",
                                duration_ms=duration,
                                metadata=data,
                            )
                    else:
                        return HealthCheck(
                            name="api",
                            result=CheckResult.FAIL,
                            message=f"API returned {resp.status}",
                            duration_ms=duration,
                        )
        except ImportError:
            return HealthCheck(
                name="api",
                result=CheckResult.SKIP,
                message="aiohttp not available",
                duration_ms=(time.monotonic() - start) * 1000,
            )
        except Exception as e:
            return HealthCheck(
                name="api",
                result=CheckResult.FAIL,
                message=f"API error: {e}",
                duration_ms=(time.monotonic() - start) * 1000,
            )
    
    def _check_heartbeat_freshness(self) -> HealthCheck:
        """Check if heartbeat is fresh."""
        if self._last_heartbeat is None:
            return HealthCheck(
                name="heartbeat",
                result=CheckResult.WARN,
                message="No heartbeat received yet",
            )
        
        age = (datetime.now(timezone.utc) - self._last_heartbeat).total_seconds()
        
        if age > self._config.heartbeat_timeout_sec:
            return HealthCheck(
                name="heartbeat",
                result=CheckResult.FAIL,
                message=f"Heartbeat stale: {age:.0f}s ago",
                metadata={"age_seconds": age},
            )
        elif age > self._config.heartbeat_timeout_sec * 0.5:
            return HealthCheck(
                name="heartbeat",
                result=CheckResult.WARN,
                message=f"Heartbeat aging: {age:.0f}s ago",
                metadata={"age_seconds": age},
            )
        else:
            return HealthCheck(
                name="heartbeat",
                result=CheckResult.PASS,
                message=f"Heartbeat fresh: {age:.0f}s ago",
                metadata={"age_seconds": age},
            )
    
    async def _handle_unhealthy(self, report: HealthReport):
        """Handle unhealthy status."""
        print(f"[GUARDIAN] UNHEALTHY detected!")
        
        # Check restart limit
        if self._restart_count >= self._config.max_restarts_per_hour:
            print(f"[GUARDIAN] Max restarts reached ({self._restart_count}/{self._config.max_restarts_per_hour})")
            await self._send_alert(
                "HOPE Guardian: Emergency Stop",
                f"Max restarts reached ({self._restart_count}/hour). Manual intervention required."
            )
            return
        
        # Attempt recovery
        await self._handle_core_crash()
    
    async def _handle_core_crash(self):
        """Handle core process crash."""
        print(f"[GUARDIAN] Handling core crash (attempt {self._restart_count + 1})")
        
        # Wait with backoff
        print(f"[GUARDIAN] Waiting {self._current_restart_delay:.1f}s before restart")
        await asyncio.sleep(self._current_restart_delay)
        
        # Increase delay for next time
        self._current_restart_delay = min(
            self._current_restart_delay * self._config.restart_backoff_multiplier,
            self._config.max_restart_delay_sec,
        )
        
        # Kill if still running
        if self._core_process and self._core_process.poll() is None:
            self._core_process.terminate()
            await asyncio.sleep(1)
            if self._core_process.poll() is None:
                self._core_process.kill()
        
        # Restart
        await self._start_core()
        
        # Reset delay on successful restart
        if self.is_core_running:
            self._current_restart_delay = self._config.restart_delay_sec
            await self._send_alert(
                "HOPE Guardian: Core Restarted",
                f"Core process restarted successfully. Restart count: {self._restart_count}/hour"
            )
    
    async def _send_alert(self, title: str, message: str):
        """Send alert via configured channels."""
        print(f"[GUARDIAN] ALERT: {title} - {message}")
        
        if self._on_alert:
            self._on_alert(title, message)
        
        if self._config.telegram_enabled and self._config.telegram_bot_token:
            try:
                # Use subprocess curl for simplicity
                text = f"🚨 *{title}*\n\n{message}"
                subprocess.run([
                    "curl", "-s", "-X", "POST",
                    f"https://api.telegram.org/bot{self._config.telegram_bot_token}/sendMessage",
                    "-d", f"chat_id={self._config.telegram_chat_id}",
                    "-d", f"text={text}",
                    "-d", "parse_mode=Markdown",
                ], capture_output=True, timeout=10)
            except Exception as e:
                print(f"[GUARDIAN] Telegram alert failed: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """Get Guardian status."""
        return {
            "running": self._running,
            "uptime_seconds": self.uptime,
            "core_running": self.is_core_running,
            "core_pid": self._core_process.pid if self._core_process else None,
            "last_heartbeat": self._last_heartbeat.isoformat() if self._last_heartbeat else None,
            "consecutive_failures": self._consecutive_failures,
            "restart_count_last_hour": self._restart_count,
            "current_restart_delay": self._current_restart_delay,
            "last_health_report": self._health_history[-1].to_dict() if self._health_history else None,
        }


# =============================================================================
# STANDALONE GUARDIAN
# =============================================================================

async def run_guardian(config_path: Optional[str] = None):
    """Run Guardian as standalone process."""
    # Load config
    config = GuardianConfig()
    if config_path and Path(config_path).exists():
        with open(config_path) as f:
            data = json.load(f)
            for key, value in data.items():
                if hasattr(config, key):
                    setattr(config, key, value)
    
    # Create and run guardian
    guardian = Guardian(config)
    
    # Handle signals
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, guardian.stop)
    
    await guardian.start()


# =============================================================================
# TESTING
# =============================================================================

if __name__ == "__main__":
    print("=== Guardian Tests ===\n")
    
    # Create config for testing (no actual process start)
    config = GuardianConfig(
        heartbeat_interval_sec=2,
        health_check_interval_sec=5,
        core_command=["echo", "test"],  # Dummy command
    )
    
    guardian = Guardian(config)
    
    # Test health check functions
    print("Test: Heartbeat freshness check")
    check = guardian._check_heartbeat_freshness()
    print(f"  Result: {check.result.value}")
    print(f"  Message: {check.message}")
    print()
    
    print("Test: Get status")
    status = guardian.get_status()
    print(f"  Status: {json.dumps(status, indent=2, default=str)}")
    print()
    
    print("=== Tests Completed ===")
    print("\nTo run Guardian as standalone:")
    print("  python -m hope_core.guardian.watchdog")
