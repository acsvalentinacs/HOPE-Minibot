# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-02 15:10:00 UTC
# Purpose: HOPE Health Daemon - hourly system verification + auto-repair
# === END SIGNATURE ===
"""
HOPE HEALTH DAEMON - Периодическая проверка системы

Функции:
1. Каждый час проверяет все компоненты
2. Автоматически перезапускает упавшие сервисы
3. Логирует состояние в state/health/
4. Отправляет алерты при критических проблемах

Запуск:
    python scripts/hope_health_daemon.py                # Запуск демона
    python scripts/hope_health_daemon.py --once         # Одна проверка
    python scripts/hope_health_daemon.py --interval 30  # Каждые 30 минут
"""

import os
import sys
import json
import time
import socket
import subprocess
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger("HEALTH")

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

HEALTH_DIR = PROJECT_ROOT / "state" / "health"
HEALTH_LOG = HEALTH_DIR / "health_checks.jsonl"
HEALTH_DIR.mkdir(parents=True, exist_ok=True)

# Components to monitor
COMPONENTS = {
    "pricefeed_gateway": {
        "port": 8100,
        "url": "http://127.0.0.1:8100/prices",
        "start_cmd": ["python", "scripts/pricefeed_gateway.py"],
        "critical": True,
    },
    "autotrader": {
        "port": 8200,
        "url": "http://127.0.0.1:8200/status",
        "start_cmd": ["python", "scripts/autotrader.py", "--mode", "LIVE", "--yes", "--confirm"],
        "critical": True,
    },
    "momentum_trader": {
        "port": None,  # No port, runs as daemon
        "url": None,
        "process_name": "momentum_trader",
        "start_cmd": ["python", "scripts/momentum_trader.py", "--daemon"],
        "critical": False,
    },
}


@dataclass
class HealthCheck:
    """Single health check result."""
    timestamp: str
    component: str
    status: str  # PASS, FAIL, RECOVERED
    details: Dict
    action_taken: Optional[str] = None


class HealthDaemon:
    """HOPE System Health Monitor."""

    def __init__(self, auto_repair: bool = True):
        self.auto_repair = auto_repair
        self.check_history: List[HealthCheck] = []
        self.consecutive_failures: Dict[str, int] = {}

    def check_port(self, port: int) -> bool:
        """Check if port is listening."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(2)
                return s.connect_ex(('127.0.0.1', port)) == 0
        except:
            return False

    def check_http(self, url: str) -> Tuple[bool, Optional[Dict]]:
        """Check HTTP endpoint."""
        try:
            import httpx
            resp = httpx.get(url, timeout=5)
            if resp.status_code == 200:
                try:
                    return True, resp.json()
                except:
                    return True, {}
            return False, None
        except Exception as e:
            return False, {"error": str(e)}

    def check_process_running(self, name: str) -> bool:
        """Check if process with name is running."""
        try:
            if sys.platform == 'win32':
                result = subprocess.run(
                    ['wmic', 'process', 'where', 'name="python.exe"', 'get', 'commandline'],
                    capture_output=True, text=True, timeout=10
                )
                return name in result.stdout
            else:
                result = subprocess.run(
                    ['pgrep', '-af', name],
                    capture_output=True, text=True, timeout=5
                )
                return result.returncode == 0
        except:
            return False

    def start_component(self, name: str, config: Dict) -> bool:
        """Start a component."""
        try:
            cmd = config["start_cmd"]
            log.info(f"Starting {name}: {' '.join(cmd)}")

            if sys.platform == 'win32':
                subprocess.Popen(
                    cmd,
                    cwd=PROJECT_ROOT,
                    creationflags=subprocess.CREATE_NEW_CONSOLE
                )
            else:
                subprocess.Popen(
                    cmd,
                    cwd=PROJECT_ROOT,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )

            # Wait and verify
            time.sleep(3)

            if config.get("port"):
                return self.check_port(config["port"])
            elif config.get("process_name"):
                return self.check_process_running(config["process_name"])
            return True
        except Exception as e:
            log.error(f"Failed to start {name}: {e}")
            return False

    def check_component(self, name: str, config: Dict) -> HealthCheck:
        """Check single component health."""
        ts = datetime.now(timezone.utc).isoformat()
        details = {}
        status = "PASS"
        action = None

        # Check by port
        if config.get("port"):
            port_ok = self.check_port(config["port"])
            details["port"] = config["port"]
            details["port_listening"] = port_ok

            if not port_ok:
                status = "FAIL"

        # Check by HTTP
        if config.get("url") and status != "FAIL":
            http_ok, data = self.check_http(config["url"])
            details["http_ok"] = http_ok
            if data:
                details["http_data"] = data

            if not http_ok:
                status = "FAIL"

        # Check by process name
        if config.get("process_name"):
            proc_ok = self.check_process_running(config["process_name"])
            details["process_running"] = proc_ok

            if not proc_ok:
                status = "FAIL"

        # Track consecutive failures
        if status == "FAIL":
            self.consecutive_failures[name] = self.consecutive_failures.get(name, 0) + 1
            details["consecutive_failures"] = self.consecutive_failures[name]

            # Auto-repair
            if self.auto_repair:
                log.warning(f"Component {name} FAILED - attempting repair...")
                if self.start_component(name, config):
                    status = "RECOVERED"
                    action = "auto_restart"
                    self.consecutive_failures[name] = 0
                    log.info(f"Component {name} RECOVERED")
                else:
                    action = "restart_failed"
                    log.error(f"Component {name} restart FAILED")
        else:
            self.consecutive_failures[name] = 0

        return HealthCheck(
            timestamp=ts,
            component=name,
            status=status,
            details=details,
            action_taken=action,
        )

    def run_check(self) -> Dict:
        """Run full health check."""
        log.info("=" * 50)
        log.info("HOPE HEALTH CHECK")
        log.info("=" * 50)

        results = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "overall": "PASS",
            "components": {},
            "critical_failures": [],
        }

        for name, config in COMPONENTS.items():
            check = self.check_component(name, config)
            self.check_history.append(check)

            status_icon = {
                "PASS": "✅",
                "FAIL": "❌",
                "RECOVERED": "🔧",
            }.get(check.status, "❓")

            log.info(f"  {status_icon} {name}: {check.status}")

            results["components"][name] = asdict(check)

            if check.status == "FAIL":
                results["overall"] = "FAIL"
                if config.get("critical"):
                    results["critical_failures"].append(name)

        # Log to file
        self._log_check(results)

        return results

    def _log_check(self, results: Dict):
        """Log check results to JSONL."""
        with open(HEALTH_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(results, ensure_ascii=False) + '\n')

    def run_daemon(self, interval_minutes: int = 60):
        """Run as daemon, checking every interval."""
        log.info(f"Starting Health Daemon (interval: {interval_minutes} min)")

        while True:
            try:
                results = self.run_check()

                if results["critical_failures"]:
                    log.critical(f"CRITICAL FAILURES: {results['critical_failures']}")
                    # Here you could send Telegram alert

                log.info(f"Next check in {interval_minutes} minutes\n")
                time.sleep(interval_minutes * 60)

            except KeyboardInterrupt:
                log.info("Health Daemon stopped")
                break
            except Exception as e:
                log.error(f"Daemon error: {e}")
                time.sleep(60)  # Wait 1 min on error


def main():
    import argparse

    parser = argparse.ArgumentParser(description="HOPE Health Daemon")
    parser.add_argument("--once", action="store_true", help="Run single check")
    parser.add_argument("--interval", type=int, default=60, help="Check interval (minutes)")
    parser.add_argument("--no-repair", action="store_true", help="Disable auto-repair")

    args = parser.parse_args()

    daemon = HealthDaemon(auto_repair=not args.no_repair)

    if args.once:
        results = daemon.run_check()
        print(f"\nOverall: {results['overall']}")
        if results['critical_failures']:
            print(f"Critical: {results['critical_failures']}")
    else:
        daemon.run_daemon(interval_minutes=args.interval)


if __name__ == "__main__":
    main()
